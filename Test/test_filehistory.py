import tempfile
import unittest
import uuid
from pathlib import Path

from agent.tools.workspace.files import WorkspaceToolService
from data.session import ChatSession
from data.workspace import workspace_manager
from data.workspace_metadata import WorkspaceMetadataStore
from filehistory import FileHistory, Snapshot


class TestFileHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.original_metadata_store = workspace_manager.metadata_store
        workspace_manager.metadata_store = WorkspaceMetadataStore(
            self.root / ".test-global" / "index.json"
        )
        self.sid = "filehistory-" + uuid.uuid4().hex
        ok, message, self.runtime = workspace_manager.mount(
            self.sid, str(self.root), permission="read_write",
        )
        self.assertTrue(ok, message)
        self.session = ChatSession(session_id=self.sid)
        self.history = FileHistory(self.runtime, self.sid)

    def tearDown(self):
        workspace_manager.unmount(self.sid)
        workspace_manager.metadata_store = self.original_metadata_store
        self.tmp.cleanup()

    def test_snapshots_are_persistent_and_export_dataclass(self):
        snapshot = self.history.begin_snapshot("第一轮", self.session.capture_rewind_state())
        self.history.finalize_snapshot(snapshot["id"], "succeeded")
        loaded = FileHistory(self.runtime, self.sid).get_snapshots()
        self.assertEqual(len(loaded), 1)
        self.assertIsInstance(loaded[0], Snapshot)
        self.assertEqual(loaded[0].user_text, "第一轮")
        self.assertGreater(loaded[0].created_at, 0)

    def test_rewind_multiple_turns_restores_files_and_conversation(self):
        original = self.root / "notes.md"
        created = self.root / "created.txt"
        original.write_text("v0", encoding="utf-8")

        first = self.history.begin_snapshot("第一轮修改", self.session.capture_rewind_state())
        self.history.track_before_write(original)
        original.write_text("v1", encoding="utf-8")
        self.history.track_before_write(created)
        created.write_text("new", encoding="utf-8")
        self.session.add_user("第一轮修改")
        self.session.add_assistant("完成第一轮")
        self.history.finalize_snapshot(first["id"], "succeeded")

        second = self.history.begin_snapshot("第二轮修改", self.session.capture_rewind_state())
        self.history.track_before_write(original)
        original.write_text("v2", encoding="utf-8")
        self.history.track_before_write(created)
        created.unlink()
        self.session.add_user("第二轮修改")
        self.session.add_assistant("完成第二轮")
        self.history.finalize_snapshot(second["id"], "succeeded")

        result = self.history.rewind(first["id"], "code_and_conversation", self.session)
        self.assertEqual(original.read_text(encoding="utf-8"), "v0")
        self.assertFalse(created.exists())
        self.assertEqual(self.session.history, [])
        self.assertEqual(result["changed_file_count"], 2)
        self.assertEqual(len(self.history.get_snapshots()), 1)

    def test_conversation_only_does_not_change_files(self):
        target = self.root / "notes.md"
        target.write_text("before", encoding="utf-8")
        snapshot = self.history.begin_snapshot("修改", self.session.capture_rewind_state())
        self.history.track_before_write(target)
        target.write_text("after", encoding="utf-8")
        self.session.add_user("修改")
        self.history.finalize_snapshot(snapshot["id"], "succeeded")
        self.history.rewind(snapshot["id"], "conversation_only", self.session)
        self.assertEqual(target.read_text(encoding="utf-8"), "after")
        self.assertEqual(self.session.history, [])

    def test_code_only_does_not_change_conversation(self):
        target = self.root / "notes.md"
        target.write_text("before", encoding="utf-8")
        snapshot = self.history.begin_snapshot("修改", self.session.capture_rewind_state())
        self.history.track_before_write(target)
        target.write_text("after", encoding="utf-8")
        self.session.add_user("保留这条消息")
        self.history.finalize_snapshot(snapshot["id"], "succeeded")
        self.history.rewind(snapshot["id"], "code_only", self.session)
        self.assertEqual(target.read_text(encoding="utf-8"), "before")
        self.assertEqual(self.session.history[0]["content"], "保留这条消息")

    def test_workspace_write_tools_track_versions_automatically(self):
        target = self.root / "notes.md"
        target.write_text("before", encoding="utf-8")
        snapshot = self.history.begin_snapshot("自动联动", self.session.capture_rewind_state())
        service = WorkspaceToolService(self.sid)
        service.read_file("notes.md")
        service.edit_file("notes.md", "before", "after")
        self.history.finalize_snapshot(snapshot["id"], "succeeded")
        self.assertEqual(self.history.get_snapshots()[0].file_count, 1)
        self.history.rewind(snapshot["id"], "code_only", self.session)
        self.assertEqual(target.read_text(encoding="utf-8"), "before")


if __name__ == "__main__":
    unittest.main()
