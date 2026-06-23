import tempfile
import time
import unittest
import uuid
from pathlib import Path

from api import create_app
from api.saved_sessions import SAVE_DIR
from api.state import session_manager
from data.workspace import workspace_manager
from data.workspace_metadata import WorkspaceMetadataStore
from filehistory import FileHistory


class TestCheckpointAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = create_app().test_client()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.original_metadata_store = workspace_manager.metadata_store
        workspace_manager.metadata_store = WorkspaceMetadataStore(
            self.root / ".test-global" / "index.json"
        )
        self.sid = "filehistory-api-" + uuid.uuid4().hex
        self.session = session_manager.get_or_create(self.sid)
        ok, message, self.runtime = workspace_manager.mount(
            self.sid, str(self.root), permission="read_write",
        )
        self.assertTrue(ok, message)

    def tearDown(self):
        session_manager.remove(self.sid)
        (SAVE_DIR / f"autosave_{self.sid}.json").unlink(missing_ok=True)
        workspace_manager.metadata_store = self.original_metadata_store
        self.tmp.cleanup()

    def _wait(self, job_id):
        for _ in range(200):
            response = self.client.get(f"/api/session/{self.sid}/jobs/{job_id}")
            job = response.get_json()["job"]
            if job["status"] in {"succeeded", "failed", "canceled"}:
                return job
            time.sleep(0.02)
        self.fail("file history rewind job timed out")

    def _snapshot_with_edit(self):
        target = self.root / "notes.md"
        target.write_text("before", encoding="utf-8")
        history = FileHistory(self.runtime, self.sid)
        snapshot = history.begin_snapshot("修改说明文件", self.session.capture_rewind_state())
        history.track_before_write(target)
        target.write_text("after", encoding="utf-8")
        self.session.add_user("修改说明文件")
        self.session.add_assistant("已修改")
        history.finalize_snapshot(snapshot["id"], "succeeded")
        return target, snapshot

    def test_list_and_restore_code_and_conversation(self):
        target, snapshot = self._snapshot_with_edit()
        listed = self.client.get(f"/api/session/{self.sid}/workspace/checkpoints")
        self.assertEqual(listed.status_code, 200)
        item = listed.get_json()["snapshots"][0]
        self.assertEqual(item["id"], snapshot["id"])
        self.assertEqual(item["file_count"], 1)

        restored = self.client.post(
            f"/api/session/{self.sid}/workspace/checkpoints/{snapshot['id']}/restore",
            json={"confirm": True, "mode": "code_and_conversation"},
        )
        self.assertEqual(restored.status_code, 202)
        self.assertEqual(self._wait(restored.get_json()["job_id"])["status"], "succeeded")
        self.assertEqual(target.read_text(encoding="utf-8"), "before")
        self.assertEqual(self.session.history, [])

    def test_conversation_only_allowed_in_read_only_mode(self):
        target, snapshot = self._snapshot_with_edit()
        workspace_manager.update_permission(self.sid, "read_only")
        restored = self.client.post(
            f"/api/session/{self.sid}/workspace/checkpoints/{snapshot['id']}/restore",
            json={"confirm": True, "mode": "conversation_only"},
        )
        self.assertEqual(restored.status_code, 202)
        self.assertEqual(self._wait(restored.get_json()["job_id"])["status"], "succeeded")
        self.assertEqual(target.read_text(encoding="utf-8"), "after")
        self.assertEqual(self.session.history, [])

    def test_code_restore_requires_edit_permission_and_confirmation(self):
        _target, snapshot = self._snapshot_with_edit()
        workspace_manager.update_permission(self.sid, "read_only")
        denied = self.client.post(
            f"/api/session/{self.sid}/workspace/checkpoints/{snapshot['id']}/restore",
            json={"confirm": True, "mode": "code_only"},
        )
        self.assertEqual(denied.status_code, 403)
        missing_confirm = self.client.post(
            f"/api/session/{self.sid}/workspace/checkpoints/{snapshot['id']}/restore",
            json={"mode": "conversation_only"},
        )
        self.assertEqual(missing_confirm.status_code, 400)


if __name__ == "__main__":
    unittest.main()
