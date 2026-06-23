import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

from agent.tools.exposure import filter_tools_for_turn
from agent.tools.schemas import AGENT_TOOLS
from agent.tools.workspace.files import (
    MAX_READ_BYTES,
    MAX_WRITE_BYTES,
    WorkspaceToolError,
    WorkspaceToolService,
)
from agent.tools.workspace.bash import WorkspaceBashService
from agent.tools.workspace.tasks import WorkspaceTaskStore
from agent.tools.workspace.teams import WorkspaceTeamStore, WorkspaceTeamError
from data.workspace import workspace_manager
from data.workspace_metadata import WorkspaceMetadataStore
from data.system_workspace import MAX_LIST_LIMIT, SystemWorkspace


def _names(tools):
    return {(item.get("function") or {}).get("name") for item in tools}


class TestWorkspaceTools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_metadata_store = workspace_manager.metadata_store
        workspace_manager.metadata_store = WorkspaceMetadataStore(
            Path(self.tmp.name) / ".test-global" / "index.json"
        )
        self.sid = "test-" + uuid.uuid4().hex
        ok, message, _runtime = workspace_manager.mount(
            self.sid, self.tmp.name, permission="read_write"
        )
        self.assertTrue(ok, message)
        self.root = Path(self.tmp.name)
        self.service = WorkspaceToolService(self.sid)
        self.bash = WorkspaceBashService(self.sid)

    def tearDown(self):
        workspace_manager.unmount(self.sid)
        workspace_manager.metadata_store = self.original_metadata_store
        self.tmp.cleanup()

    def test_system_file_tools_are_always_exposed(self):
        system_only = _names(filter_tools_for_turn(
            AGENT_TOOLS, has_data_source=False, has_workspace=False, include_mcp=False
        ))
        visible = _names(filter_tools_for_turn(
            AGENT_TOOLS, has_data_source=False, has_workspace=True, include_mcp=False
        ))
        self.assertIn("workspace_read_file", system_only)
        self.assertNotIn("task_create", system_only)
        self.assertIn("workspace_read_file", visible)
        self.assertIn("workspace_write_file", visible)
        self.assertIn("workspace_delete_file", visible)
        self.assertIn("workspace_move_file", visible)
        self.assertNotIn("workspace_bash", system_only)
        self.assertIn("workspace_bash", visible)

    def test_glob_grep_and_read(self):
        (self.root / "notes.md").write_text("alpha\nbeta alpha\n", encoding="utf-8")
        matches = self.service.glob("**/*.md")
        self.assertEqual(matches["count"], 1)
        found = self.service.grep("alpha", include="*.md")
        self.assertEqual(found["count"], 2)
        read = self.service.read_file("notes.md")
        self.assertIn("1: alpha", read["content"])

    def test_read_and_write_limits_are_twenty_mib(self):
        self.assertEqual(MAX_READ_BYTES, 20 * 1024 * 1024)
        self.assertEqual(MAX_WRITE_BYTES, 20 * 1024 * 1024)

        # Prove the former 512 KB read and 2 MB write ceilings are gone.
        (self.root / "large.txt").write_text("x" * 600_000, encoding="utf-8")
        read = self.service.read_file("large.txt")
        self.assertTrue(read["truncated"])
        payload = "中" * 750_000  # 2.25 MB in UTF-8
        written = self.service.write_file("large-write.txt", payload)
        self.assertEqual(written["bytes"], len(payload.encode("utf-8")))

    def test_read_docx_extracts_paragraph_and_table_text(self):
        document = self.root / "2026贝恩杯赛题.docx"
        xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p><w:r><w:t>贝恩杯赛题说明</w:t></w:r></w:p>
            <w:tbl><w:tr>
              <w:tc><w:p><w:r><w:t>指标</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>即时零售利润</w:t></w:r></w:p></w:tc>
            </w:tr></w:tbl>
          </w:body>
        </w:document>"""
        with zipfile.ZipFile(document, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", xml)

        result = self.service.read_file(document.name)
        self.assertIn("贝恩杯赛题说明", result["content"])
        self.assertIn("即时零售利润", result["content"])
        self.assertIn("wordprocessingml.document", result["content_type"])

    def test_service_keeps_frozen_workspace_after_session_rebind(self):
        """C5: an in-flight tool service must never follow session A -> B."""
        original_runtime = workspace_manager.get(self.sid)
        self.assertIsNotNone(original_runtime)
        original_id = original_runtime.workspace_id
        (self.root / "identity.txt").write_text("workspace-a", encoding="utf-8")
        workspace_manager.acquire_job(original_id)
        other = tempfile.TemporaryDirectory()
        try:
            other_root = Path(other.name)
            (other_root / "identity.txt").write_text("workspace-b", encoding="utf-8")
            ok, message, rebound = workspace_manager.mount(
                self.sid, other.name, permission="read_write",
            )
            self.assertTrue(ok, message)
            self.assertNotEqual(rebound.workspace_id, original_id)

            # Created before the rebind: remains pinned to A.
            self.assertIn("workspace-a", self.service.read_file("identity.txt")["content"])
            # Created after the rebind: snapshots and reads B.
            current = WorkspaceToolService(self.sid)
            self.assertIn("workspace-b", current.read_file("identity.txt")["content"])
            self.assertIsNot(self.service._file_state(), current._file_state())
        finally:
            workspace_manager.release_job(original_id)
            other.cleanup()

    def test_existing_file_requires_read_before_write_and_detects_race(self):
        path = self.root / "report.md"
        path.write_text("old", encoding="utf-8")
        with self.assertRaises(WorkspaceToolError):
            self.service.write_file("report.md", "new")
        self.service.read_file("report.md")
        self.service.write_file("report.md", "new")
        self.assertEqual(path.read_text(encoding="utf-8"), "new")
        self.service.read_file("report.md")
        path.write_text("user changed it", encoding="utf-8")
        with self.assertRaises(WorkspaceToolError):
            self.service.edit_file("report.md", "user", "agent")

    def test_new_file_and_unique_edit(self):
        result = self.service.write_file("drafts/result.md", "revenue: 10")
        self.assertEqual(result["path"], "user/drafts/result.md")
        self.service.read_file("drafts/result.md")
        self.service.edit_file("drafts/result.md", "10", "12")
        self.assertIn("12", (self.root / "drafts" / "result.md").read_text(encoding="utf-8"))

    def test_delete_file_requires_confirmation_and_supports_binary_files(self):
        path = self.root / "聚类数据.xlsx"
        path.write_bytes(b"PK\x03\x04binary")
        with self.assertRaises(WorkspaceToolError):
            self.service.delete_file("聚类数据.xlsx")
        deleted = self.service.delete_file("聚类数据.xlsx", confirm=True)
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["path"], "user/聚类数据.xlsx")
        self.assertFalse(path.exists())
        (self.root / "folder").mkdir()
        with self.assertRaises(WorkspaceToolError):
            self.service.delete_file("folder", confirm=True)

    def test_glob_user_result_round_trips_to_delete_and_user_uri_alias(self):
        path = self.root / "聚类数据.xlsx"
        path.write_bytes(b"PK\x03\x04binary")
        found = self.service.glob("**/聚类数据.xlsx")
        self.assertEqual(found["count"], 1)
        self.assertEqual(found["matches"][0]["path"], "user/聚类数据.xlsx")
        self.service.delete_file(found["matches"][0]["path"], confirm=True)
        self.assertFalse(path.exists())

        path.write_bytes(b"PK\x03\x04binary-again")
        alias = self.service.glob("*.xlsx", path="workspace://user")
        self.assertEqual(alias["matches"][0]["path"], "user/聚类数据.xlsx")
        self.service.delete_file("workspace://user/聚类数据.xlsx", confirm=True)
        self.assertFalse(path.exists())

    def test_move_file_renames_and_requires_confirmation_to_overwrite(self):
        source = self.root / "source.xlsx"
        destination = self.root / "archive" / "renamed.xlsx"
        source.write_bytes(b"source")
        moved = self.service.move_file("source.xlsx", "archive/renamed.xlsx")
        self.assertTrue(moved["moved"])
        self.assertFalse(moved["overwritten"])
        self.assertEqual(destination.read_bytes(), b"source")

        replacement = self.root / "replacement.xlsx"
        replacement.write_bytes(b"replacement")
        with self.assertRaises(WorkspaceToolError):
            self.service.move_file("replacement.xlsx", "archive/renamed.xlsx")
        overwritten = self.service.move_file(
            "replacement.xlsx", "archive/renamed.xlsx", confirm_overwrite=True,
        )
        self.assertTrue(overwritten["overwritten"])
        self.assertEqual(destination.read_bytes(), b"replacement")

    def test_read_only_workspace_blocks_write_and_edit(self):
        workspace_manager.unmount(self.sid)
        ok, message, runtime = workspace_manager.mount(
            self.sid, self.tmp.name, permission="read_only"
        )
        self.assertTrue(ok, message)
        self.assertEqual(runtime.to_dict()["permission"], "read_only")
        (self.root / "readable.txt").write_text("original", encoding="utf-8")
        self.assertIn("original", self.service.read_file("readable.txt")["content"])
        with self.assertRaises(WorkspaceToolError):
            self.service.write_file("new.txt", "blocked")
        with self.assertRaises(WorkspaceToolError):
            self.service.edit_file("readable.txt", "original", "changed")
        with self.assertRaises(WorkspaceToolError):
            self.service.delete_file("readable.txt", confirm=True)
        with self.assertRaises(WorkspaceToolError):
            self.service.move_file("readable.txt", "moved.txt")
        runtime.permission = "read_write"
        self.service.write_file("new.txt", "allowed")
        self.assertEqual((self.root / "new.txt").read_text(encoding="utf-8"), "allowed")

    def test_invalid_workspace_permission_is_rejected(self):
        workspace_manager.unmount(self.sid)
        ok, message, runtime = workspace_manager.mount(
            self.sid, self.tmp.name, permission="admin"
        )
        self.assertFalse(ok)
        self.assertIn("权限", message)
        self.assertIsNone(runtime)

    def test_traversal_sensitive_and_internal_paths_are_blocked(self):
        for path in ("../outside.txt", ".env", ".git/config", ".zhixi/registry.json"):
            with self.subTest(path=path), self.assertRaises(WorkspaceToolError):
                self.service.read_file(path)

    def test_shell_free_operations(self):
        (self.root / "data.json").write_text('{"ok": true}', encoding="utf-8")
        valid = self.service.command("json_validate", "data.json")
        self.assertTrue(valid["valid"])
        checksum = self.service.command("checksum", "data.json")
        self.assertEqual(len(checksum["sha256"]), 64)
        with self.assertRaises(WorkspaceToolError):
            self.service.command("powershell", ".")

    def test_restricted_workspace_bash_read_commands(self):
        (self.root / "notes.txt").write_text("alpha\nbeta alpha\n", encoding="utf-8")
        self.assertEqual(self.bash.execute("pwd")["output"]["path"], "workspace://user")
        listed = self.bash.execute("ls")["output"]
        self.assertIn("user/notes.txt", [item["path"] for item in listed["matches"]])
        read = self.bash.execute("cat user/notes.txt")["output"]
        self.assertIn("1: alpha", read["content"])
        searched = self.bash.execute('rg "alpha" user')["output"]
        self.assertEqual(searched["count"], 2)
        digest = self.bash.execute("sha256sum user/notes.txt")["output"]
        self.assertEqual(len(digest["sha256"]), 64)

    def test_restricted_workspace_bash_mutations_use_existing_guards(self):
        source = self.root / "source.txt"
        source.write_text("payload", encoding="utf-8")
        moved = self.bash.execute("mv user/source.txt user/moved.txt")["output"]
        self.assertTrue(moved["moved"])
        with self.assertRaises(WorkspaceToolError):
            self.bash.execute("rm user/moved.txt")
        deleted = self.bash.execute("rm user/moved.txt", confirm=True)["output"]
        self.assertTrue(deleted["deleted"])

    def test_restricted_workspace_bash_rejects_shell_escape_and_unknown_commands(self):
        rejected = (
            "pwd && whoami",
            "cat notes.txt | more",
            "dir > listing.txt",
            "powershell -Command Get-ChildItem",
            "cmd /c dir",
            "python -c print(1)",
            "rm -rf .",
        )
        for command in rejected:
            with self.subTest(command=command), self.assertRaises(WorkspaceToolError):
                self.bash.execute(command, confirm=True)

    def test_system_virtual_roots_and_read_character_cap(self):
        relative = f"workspace-test-{self.sid}.txt"
        virtual = f"outputs/{relative}"
        physical = workspace_manager.system_workspace.policy("outputs").path / relative
        try:
            written = self.service.write_file(virtual, "x" * 20_000)
            self.assertEqual(written["path"], virtual)
            read = self.service.read_file(virtual)
            self.assertLessEqual(len(read["content"]), 12_000)
            self.assertTrue(read["truncated"])
            with self.assertRaises(WorkspaceToolError):
                self.service.write_file("uploads/blocked.txt", "no")
        finally:
            physical.unlink(missing_ok=True)
            workspace_manager.system_workspace.invalidate("outputs")

    def test_persistent_workspace_task_board(self):
        store = WorkspaceTaskStore(self.sid)
        first = store.create("Profile sales", assignee="analyst")
        second = store.create("Build report", blocked_by=[first["id"]])
        self.assertEqual(len(store.list()), 2)
        self.assertEqual(store.get(second["id"])["blocked_by"], [first["id"]])
        updated = store.update(first["id"], status="completed", add_blocks=[second["id"]])
        self.assertEqual(updated["status"], "completed")
        self.assertEqual(store.list(status="completed")[0]["id"], first["id"])

    def test_workspace_team_and_mailbox(self):
        store = WorkspaceTeamStore(self.sid)
        team = store.create("growth", "Growth review", [
            {"name": "researcher", "role": "research", "instructions": "Check assumptions."},
            {"name": "reviewer", "role": "review"},
        ])
        self.assertEqual(len(team["members"]), 2)
        message = store.send_message("growth", "reviewer", "Review the funnel")
        self.assertEqual(message["recipient"], "reviewer")
        self.assertEqual(store.member("growth", "researcher")["role"], "research")
        with self.assertRaises(WorkspaceTeamError):
            store.send_message("growth", "missing", "hello")
        self.assertEqual(store.delete("growth")["deleted"], "growth")

class TestSystemWorkspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("uploads", "outputs", "MCP"):
            (self.root / name).mkdir()
        self.workspace = SystemWorkspace(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_virtual_roots_permissions_and_traversal(self):
        self.assertEqual(
            self.workspace.resolve("uploads", "sales.csv"),
            (self.root / "uploads" / "sales.csv").resolve(),
        )
        with self.assertRaises(ValueError):
            self.workspace.resolve("uploads", "new.txt", write=True)
        self.assertEqual(
            self.workspace.resolve("outputs", "report.md", write=True),
            (self.root / "outputs" / "report.md").resolve(),
        )
        with self.assertRaises(ValueError):
            self.workspace.resolve("outputs", "../secret.txt")

    def test_index_summary_pagination_and_mcp_exclusions(self):
        for number in range(7):
            (self.root / "uploads" / f"data-{number}.csv").write_text("x\n1\n", encoding="utf-8")
        (self.root / "MCP" / "src").mkdir()
        (self.root / "MCP" / "src" / "server.py").write_text("print('ok')", encoding="utf-8")
        (self.root / "MCP" / "node_modules").mkdir()
        (self.root / "MCP" / "node_modules" / "ignored.js").write_text("ignored", encoding="utf-8")

        first = self.workspace.list_files("uploads", limit=3)
        second = self.workspace.list_files("uploads", limit=3, cursor=first["next_cursor"])
        self.assertEqual(first["count"], 3)
        self.assertEqual(second["cursor"], 3)
        self.assertLessEqual(first["count"], MAX_LIST_LIMIT)
        mcp_entries = self.workspace.entries("mcp", force=True)
        self.assertIn("src/server.py", mcp_entries)
        self.assertNotIn("node_modules/ignored.js", mcp_entries)
        summary = self.workspace.summary()
        uploads = next(item for item in summary["roots"] if item["name"] == "uploads")
        self.assertEqual(uploads["file_count"], 7)
        self.assertLessEqual(len(uploads["recent"]), 5)


if __name__ == "__main__":
    unittest.main()
