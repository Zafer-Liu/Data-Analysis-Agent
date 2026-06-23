#!/usr/bin/env python3
"""B6 saved-session recovery for SQL and persisted tool-result references."""
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.tools import results as result_module
from agent.tools.results import make_tool_result
from api import create_app
from api.state import session_manager
import api.saved_sessions as saved_sessions
from api.workspace import _register_workdir_files
from data.workspace import workspace_manager
from data.workspace_metadata import WorkspaceMetadataStore


class TestResultRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = create_app().test_client()
        cls.metadata_tmp = tempfile.TemporaryDirectory()
        cls.original_metadata_store = workspace_manager.metadata_store
        workspace_manager.metadata_store = WorkspaceMetadataStore(
            Path(cls.metadata_tmp.name) / "index.json"
        )

    @classmethod
    def tearDownClass(cls):
        workspace_manager.metadata_store = cls.original_metadata_store
        cls.metadata_tmp.cleanup()

    def test_save_load_keeps_artifact_readable_and_recovery_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_dir = root / "sessions"
            result_dir = root / "results"
            save_dir.mkdir()
            sid = f"b6-source-{time.time_ns()}"
            loaded_sid = f"b6-loaded-{time.time_ns()}"
            sess = session_manager.get_or_create(sid)
            sess.add_user("分析全部明细")
            sess.add_assistant("已完成")
            with patch.object(saved_sessions, "SAVE_DIR", save_dir), patch.object(
                result_module, "_GLOBAL_RESULT_ROOT", result_dir,
            ):
                # Re-create the artifact under the patched durable root.
                env = make_tool_result(
                    "query_data", "header\n" + "detail\n" * 8000,
                    session_id=sid, result_char_budget=100,
                )
                sess.recent_artifacts = []
                sess.record_tool_audit({
                    "recovery": {"sql": "SELECT * FROM orders"},
                    "artifacts": env.artifacts,
                })
                saved = self.client.post(
                    f"/api/session/{sid}/save", json={"name": "B6 recovery"},
                )
                self.assertEqual(saved.status_code, 200)
                filename = saved.get_json()["filename"]
                loaded = self.client.post(
                    f"/api/session/{loaded_sid}/load", json={"filename": filename},
                )
                self.assertEqual(loaded.status_code, 200)
                restored = session_manager.get(loaded_sid)
                self.assertIn("SELECT * FROM orders", restored.recent_sql)
                context = restored.build_recovery_context()
                self.assertIn("Recent SQL", context)
                self.assertIn("artifact://tool-result/", context)
                artifact_id = restored.recent_artifacts[0]["artifact_id"]
                response = self.client.get(
                    f"/api/session/{loaded_sid}/tool-results/{artifact_id}",
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("detail", response.get_data(as_text=True))
                metadata = self.client.get(
                    f"/api/session/{loaded_sid}/tool-results/{artifact_id}?format=json",
                )
                self.assertIn("detail", metadata.get_json()["data"])

    def test_saved_workspace_is_remounted_with_tables_and_cached_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workdir = root / "workspace"
            save_dir = root / "sessions"
            workdir.mkdir()
            save_dir.mkdir()
            (workdir / "orders.csv").write_text(
                "city,revenue\nBeijing,100\nShanghai,80\n", encoding="utf-8",
            )
            sid = f"b6-ws-source-{time.time_ns()}"
            loaded_sid = f"b6-ws-loaded-{time.time_ns()}"
            sess = session_manager.get_or_create(sid)
            sess.add_user("分析工作目录")
            sess.add_assistant("完成")
            ok, message, runtime = workspace_manager.mount(
                sid, str(workdir), permission="read_write",
            )
            self.assertTrue(ok, message)
            registration = _register_workdir_files(sid, runtime)
            self.assertFalse(registration["errors"])
            self.assertIn("orders", sess.data_source.list_tables())

            with patch.object(saved_sessions, "SAVE_DIR", save_dir):
                env = make_tool_result(
                    "query_data", "full-row\n" * 5000,
                    session_id=sid, runtime=runtime, result_char_budget=100,
                )
                sess.record_tool_audit({"recovery": {}, "artifacts": env.artifacts})
                saved = self.client.post(
                    f"/api/session/{sid}/save", json={"name": "workspace recovery"},
                )
                filename = saved.get_json()["filename"]
                loaded = self.client.post(
                    f"/api/session/{loaded_sid}/load", json={"filename": filename},
                )
                self.assertEqual(loaded.status_code, 200)
                self.assertTrue(loaded.get_json()["workspace_restored"])
                restored_runtime = workspace_manager.get(loaded_sid)
                self.assertEqual(restored_runtime.workdir, workdir.resolve())
                self.assertEqual(restored_runtime.workspace_id, runtime.workspace_id)
                restored = session_manager.get(loaded_sid)
                self.assertIn("orders", restored.data_source.list_tables())
                artifact_id = restored.recent_artifacts[0]["artifact_id"]
                full = self.client.get(
                    f"/api/session/{loaded_sid}/tool-results/{artifact_id}",
                )
                self.assertEqual(full.status_code, 200)
                self.assertIn("full-row", full.get_data(as_text=True))

            workspace_manager.unmount(sid)
            workspace_manager.unmount(loaded_sid)

    def test_saved_stable_identity_rejects_a_copied_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "original"
            copied = root / "copied"
            save_dir = root / "sessions"
            original.mkdir()
            save_dir.mkdir()
            source_sid = f"c0-source-{time.time_ns()}"
            loaded_sid = f"c0-loaded-{time.time_ns()}"
            ok, message, runtime = workspace_manager.mount(source_sid, str(original))
            self.assertTrue(ok, message)
            shutil.copytree(original, copied)
            payload = {
                "name": "copied workspace",
                "session_id": source_sid,
                "history": [],
                "workspace": {
                    "workdir": str(copied),
                    "permission": "read_only",
                    "workspace_id": runtime.workspace_id,
                },
            }
            session_file = save_dir / "copied.json"
            session_file.write_text(json.dumps(payload), encoding="utf-8")

            with patch.object(saved_sessions, "SAVE_DIR", save_dir):
                response = self.client.post(
                    f"/api/session/{loaded_sid}/load",
                    json={"filename": session_file.name},
                )

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["workspace_lost"])
            self.assertTrue(response.get_json()["workspace_identity_mismatch"])
            self.assertIsNone(workspace_manager.get(loaded_sid))
            workspace_manager.unmount(source_sid)

    def test_two_sessions_share_runtime_lock_and_release_on_session_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "shared"
            workdir.mkdir()
            (workdir / "orders.csv").write_text(
                "city,revenue\nBeijing,100\n", encoding="utf-8"
            )
            first_sid = f"c1-first-{time.time_ns()}"
            second_sid = f"c1-second-{time.time_ns()}"
            session_manager.get_or_create(first_sid)
            session_manager.get_or_create(second_sid)
            _, _, first_runtime = workspace_manager.mount(
                first_sid, str(workdir), "read_write"
            )
            _, _, second_runtime = workspace_manager.mount(
                second_sid, str(workdir), "read_write"
            )
            self.assertIs(first_runtime, second_runtime)
            self.assertEqual(first_runtime.ref_count, 2)

            _register_workdir_files(first_sid, first_runtime)
            _register_workdir_files(second_sid, second_runtime)
            first_source = session_manager.get(first_sid).data_source
            second_source = session_manager.get(second_sid).data_source
            self.assertIs(first_source._db_lock, first_runtime.db_lock)
            self.assertIs(second_source._db_lock, first_runtime.db_lock)

            session_manager.remove(first_sid)
            self.assertEqual(second_runtime.ref_count, 1)
            self.assertIs(workspace_manager.get(second_sid), second_runtime)
            session_manager.remove(second_sid)
            self.assertIsNone(
                workspace_manager.get_by_workspace(second_runtime.workspace_id)
            )

    def test_workspace_switch_keeps_active_task_data_source_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            first.mkdir()
            second.mkdir()
            (first / "first.csv").write_text("value\n1\n", encoding="utf-8")
            (second / "second.csv").write_text("value\n2\n", encoding="utf-8")
            sid = f"c3-switch-{time.time_ns()}"
            sess = session_manager.get_or_create(sid)
            mounted = self.client.post(
                f"/api/session/{sid}/workspace/mount",
                json={"path": str(first), "permission": "read_write"},
            )
            self.assertEqual(mounted.status_code, 200)
            parent_id = sess.job_runner.begin_tracked(
                "conversation_analysis", "active conversation"
            )
            workspace_id = sess.job_runner.get_status(parent_id)["workspace_id"]
            snapshot = sess.acquire_data_source_snapshot()
            old_source = snapshot.primary
            self.assertIsNotNone(old_source)
            self.assertTrue(old_source.list_tables())

            switched = self.client.post(
                f"/api/session/{sid}/workspace/mount",
                json={"path": str(second), "permission": "read_write"},
            )
            self.assertEqual(switched.status_code, 200)
            continued = switched.get_json()["continued_workspace"]
            self.assertEqual(continued["workspace_id"], workspace_id)
            self.assertEqual(continued["active_job_count"], 1)
            self.assertNotEqual(
                switched.get_json()["workspace"]["workspace_id"], workspace_id
            )
            self.assertTrue(old_source.list_tables())
            self.assertIsNotNone(workspace_manager.get_by_workspace(workspace_id))
            history = self.client.get(f"/api/session/{sid}/jobs").get_json()["jobs"]
            parent = next(job for job in history if job["id"] == parent_id)
            self.assertEqual(parent["workspace_id"], workspace_id)
            self.assertEqual(parent["workspace"]["name"], first.name)
            self.assertEqual(Path(parent["workspace"]["path"]), first.resolve())

            snapshot.release()
            sess.job_runner.succeed_tracked(parent_id, {"ok": True})
            self.assertIsNone(workspace_manager.get_by_workspace(workspace_id))
            session_manager.remove(sid)

    def test_workspace_unmount_reports_jobs_continuing_on_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "workspace"
            workdir.mkdir()
            sid = f"c4-unmount-{time.time_ns()}"
            sess = session_manager.get_or_create(sid)
            mounted = self.client.post(
                f"/api/session/{sid}/workspace/mount",
                json={"path": str(workdir), "permission": "read_only"},
            )
            self.assertEqual(mounted.status_code, 200)
            parent_id = sess.job_runner.begin_tracked(
                "conversation_analysis", "active conversation"
            )
            workspace_id = sess.job_runner.get_status(parent_id)["workspace_id"]

            unmounted = self.client.post(f"/api/session/{sid}/workspace/unmount")

            self.assertEqual(unmounted.status_code, 200)
            continued = unmounted.get_json()["continued_workspace"]
            self.assertEqual(continued["workspace_id"], workspace_id)
            self.assertEqual(continued["active_job_ids"], [parent_id])
            self.assertIsNotNone(workspace_manager.get_by_workspace(workspace_id))
            sess.job_runner.succeed_tracked(parent_id, {"ok": True})
            self.assertIsNone(workspace_manager.get_by_workspace(workspace_id))
            session_manager.remove(sid)

    def test_known_workspace_api_marks_current_and_active_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "known-workspace"
            workdir.mkdir()
            sid = f"c4-list-{time.time_ns()}"
            sess = session_manager.get_or_create(sid)
            mounted = self.client.post(
                f"/api/session/{sid}/workspace/mount",
                json={"path": str(workdir), "permission": "read_only"},
            )
            self.assertEqual(mounted.status_code, 200)
            job_id = sess.job_runner.begin_tracked("conversation_analysis", "active")

            response = self.client.get(f"/api/session/{sid}/workspaces")

            self.assertEqual(response.status_code, 200)
            workspace_id = mounted.get_json()["workspace"]["workspace_id"]
            item = next(
                entry for entry in response.get_json()["workspaces"]
                if entry["workspace_id"] == workspace_id
            )
            self.assertTrue(item["current"])
            self.assertTrue(item["available"])
            self.assertEqual(item["active_job_count"], 1)
            sess.job_runner.succeed_tracked(job_id, {"ok": True})
            session_manager.remove(sid)

    def test_workspace_switch_preview_uses_stable_identity_and_parent_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            first.mkdir()
            second.mkdir()
            sid = f"c42-preview-{time.time_ns()}"
            sess = session_manager.get_or_create(sid)
            mounted_first = self.client.post(
                f"/api/session/{sid}/workspace/mount",
                json={"path": str(first), "permission": "read_only"},
            )
            self.assertEqual(mounted_first.status_code, 200)
            first_id = mounted_first.get_json()["workspace"]["workspace_id"]
            second_meta = workspace_manager.metadata_store.open_or_create(
                second, "read_write"
            )
            job_id = sess.job_runner.begin_tracked("conversation_analysis", "active")

            preview = self.client.get(
                f"/api/session/{sid}/workspaces/{second_meta.workspace_id}/switch-preview"
            )

            self.assertEqual(preview.status_code, 200)
            payload = preview.get_json()
            self.assertTrue(payload["requires_confirmation"])
            self.assertFalse(payload["already_current"])
            self.assertEqual(payload["current"]["workspace_id"], first_id)
            self.assertEqual(payload["target"]["workspace_id"], second_meta.workspace_id)
            self.assertEqual(payload["target"]["permission"], "read_write")
            self.assertEqual(payload["continuing_job_ids"], [job_id])
            sess.job_runner.succeed_tracked(job_id, {"ok": True})
            session_manager.remove(sid)

    def test_expected_workspace_identity_rejects_a_different_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            first.mkdir()
            second.mkdir()
            sid = f"c42-identity-{time.time_ns()}"
            session_manager.get_or_create(sid)
            first_meta = workspace_manager.metadata_store.open_or_create(first)

            response = self.client.post(
                f"/api/session/{sid}/workspace/mount",
                json={
                    "path": str(second),
                    "permission": "read_only",
                    "expected_workspace_id": first_meta.workspace_id,
                },
            )

            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.get_json()["code"], "workspace_identity_mismatch")
            self.assertIsNone(workspace_manager.get(sid))
            session_manager.remove(sid)

    def test_workspace_rename_api_updates_current_runtime_not_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "physical-name"
            workdir.mkdir()
            sid = f"c43-rename-{time.time_ns()}"
            session_manager.get_or_create(sid)
            mounted = self.client.post(
                f"/api/session/{sid}/workspace/mount",
                json={"path": str(workdir), "permission": "read_only"},
            )
            workspace_id = mounted.get_json()["workspace"]["workspace_id"]

            renamed = self.client.patch(
                f"/api/session/{sid}/workspaces/{workspace_id}",
                json={"name": "经营分析空间"},
            )

            self.assertEqual(renamed.status_code, 200)
            self.assertEqual(renamed.get_json()["workspace"]["name"], "经营分析空间")
            status = self.client.get(f"/api/session/{sid}/workspace").get_json()
            self.assertEqual(status["workspace"]["name"], "经营分析空间")
            self.assertEqual(workspace_manager.get(sid).workdir, workdir.resolve())
            self.assertTrue(workdir.exists())
            invalid = self.client.patch(
                f"/api/session/{sid}/workspaces/{workspace_id}",
                json={"name": "错误\n名称"},
            )
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(workspace_manager.get(sid).name, "经营分析空间")
            session_manager.remove(sid)

    def test_workspace_removal_is_preflighted_and_never_deletes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "safe-remove"
            artifacts = workdir / "artifacts"
            cache = workdir / ".baa_cache"
            artifacts.mkdir(parents=True)
            cache.mkdir()
            artifact_file = artifacts / "report.txt"
            cache_file = cache / "schema.json"
            artifact_file.write_text("report", encoding="utf-8")
            cache_file.write_text("{}", encoding="utf-8")
            sid = f"c44-remove-{time.time_ns()}"
            session_manager.get_or_create(sid)
            mounted = self.client.post(
                f"/api/session/{sid}/workspace/mount",
                json={"path": str(workdir), "permission": "read_only"},
            )
            workspace_id = mounted.get_json()["workspace"]["workspace_id"]

            blocked = self.client.get(
                f"/api/session/{sid}/workspaces/{workspace_id}/remove-preview"
            ).get_json()
            self.assertFalse(blocked["can_remove"])
            self.assertEqual(blocked["blockers"][0]["code"], "workspace_connected")

            self.client.post(f"/api/session/{sid}/workspace/unmount")
            preview = self.client.get(
                f"/api/session/{sid}/workspaces/{workspace_id}/remove-preview"
            ).get_json()
            self.assertTrue(preview["can_remove"])
            self.assertEqual(preview["preserved"]["artifacts"]["file_count"], 1)
            self.assertEqual(preview["preserved"]["cache"]["file_count"], 1)
            denied = self.client.delete(
                f"/api/session/{sid}/workspaces/{workspace_id}", json={}
            )
            self.assertEqual(denied.status_code, 400)

            removed = self.client.delete(
                f"/api/session/{sid}/workspaces/{workspace_id}",
                json={"confirmed": True},
            )
            self.assertEqual(removed.status_code, 200)
            self.assertEqual(removed.get_json()["files_deleted"], 0)
            self.assertTrue(artifact_file.exists())
            self.assertTrue(cache_file.exists())
            self.assertTrue((workdir / ".zhixi" / "workspace.json").exists())
            known = self.client.get(f"/api/session/{sid}/workspaces").get_json()
            self.assertNotIn(workspace_id, [item["workspace_id"] for item in known["workspaces"]])
            self.assertEqual(
                workspace_manager.root_for_workspace(workspace_id), workdir.resolve()
            )
            session_manager.remove(sid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
