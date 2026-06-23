import json
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from data.workspace import WorkspaceManager
from data.workspace_metadata import (
    CorruptWorkspaceMetadata,
    FutureWorkspaceMetadata,
    WorkspaceMetadataStore,
    is_workspace_uuid,
)


class TestWorkspaceMetadata(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.index = self.root / "global" / "index.json"
        self.store = WorkspaceMetadataStore(self.index)

    def tearDown(self):
        self.tmp.cleanup()

    def _workdir(self, name="workspace") -> Path:
        path = self.root / name
        path.mkdir()
        return path

    def test_same_path_keeps_id_across_sessions_and_store_restart(self):
        workdir = self._workdir()
        first = self.store.open_or_create(workdir, "read_only")
        second = self.store.open_or_create(workdir, "read_write")
        restarted = WorkspaceMetadataStore(self.index).open_or_create(workdir)
        self.assertTrue(is_workspace_uuid(first.workspace_id))
        self.assertEqual(first.workspace_id, second.workspace_id)
        self.assertEqual(first.workspace_id, restarted.workspace_id)
        self.assertGreater(restarted.metadata_revision, first.metadata_revision)

        manager = WorkspaceManager(WorkspaceMetadataStore(self.index))
        ok1, message1, runtime1 = manager.mount("session-a", str(workdir))
        ok2, message2, runtime2 = manager.mount("session-b", str(workdir))
        self.assertTrue(ok1, message1)
        self.assertTrue(ok2, message2)
        self.assertEqual(runtime1.workspace_id, runtime2.workspace_id)
        self.assertNotEqual(runtime1.workspace_id, "session-a")

    def test_manager_shares_runtime_and_releases_it_after_last_session(self):
        workdir = self._workdir()
        manager = WorkspaceManager(self.store)
        _, _, first = manager.mount("session-a", str(workdir), "read_write")
        _, _, second = manager.mount("session-b", str(workdir), "read_write")

        self.assertIs(first, second)
        self.assertIs(first.db_lock, second.db_lock)
        self.assertEqual(first.ref_count, 2)
        self.assertIs(manager.get_by_workspace(first.workspace_id), first)
        self.assertEqual(manager.status("session-a")["ref_count"], 2)

        self.assertTrue(manager.unmount("session-a"))
        self.assertEqual(second.ref_count, 1)
        self.assertIs(manager.get("session-b"), second)
        self.assertTrue(manager.unmount("session-b"))
        self.assertEqual(second.state, "closed")
        self.assertIsNone(manager.get_by_workspace(second.workspace_id))

    def test_remount_same_session_does_not_increment_reference(self):
        workdir = self._workdir()
        manager = WorkspaceManager(self.store)
        _, _, first = manager.mount("session-a", str(workdir))
        _, _, second = manager.mount("session-a", str(workdir))
        self.assertIs(first, second)
        self.assertEqual(first.ref_count, 1)

    def test_shared_permission_uses_strictest_active_session(self):
        workdir = self._workdir()
        manager = WorkspaceManager(self.store)
        _, _, runtime = manager.mount("writer", str(workdir), "read_write")
        manager.mount("reader", str(workdir), "read_only")
        self.assertEqual(runtime.permission, "read_only")
        self.assertEqual(
            manager.status("writer")["requested_permission"], "read_write"
        )

        ok, message, updated = manager.update_permission("reader", "read_write")
        self.assertTrue(ok, message)
        self.assertIs(updated, runtime)
        self.assertEqual(runtime.permission, "read_write")
        manager.update_permission("writer", "read_only")
        self.assertEqual(runtime.permission, "read_only")
        manager.unmount("writer")
        self.assertEqual(runtime.permission, "read_write")

    def test_switching_session_releases_previous_workspace(self):
        first_root = self._workdir("first")
        second_root = self._workdir("second")
        manager = WorkspaceManager(self.store)
        _, _, first = manager.mount("session-a", str(first_root))
        _, _, second = manager.mount("session-a", str(second_root))
        self.assertIsNone(manager.get_by_workspace(first.workspace_id))
        self.assertEqual(first.state, "closed")
        self.assertIs(manager.get("session-a"), second)
        self.assertEqual(second.ref_count, 1)

    def test_known_workspace_list_reports_current_runtime_and_missing_path(self):
        workdir = self._workdir("known")
        manager = WorkspaceManager(self.store)
        ok, message, runtime = manager.mount("session-a", str(workdir), "read_write")
        self.assertTrue(ok, message)
        manager.acquire_job(runtime.workspace_id)

        known = manager.list_known("session-a")

        self.assertEqual(len(known), 1)
        self.assertEqual(known[0]["workspace_id"], runtime.workspace_id)
        self.assertTrue(known[0]["current"])
        self.assertTrue(known[0]["available"])
        self.assertEqual(known[0]["permission"], "read_write")
        self.assertEqual(known[0]["active_lease_count"], 1)
        self.assertEqual(known[0]["active_job_count"], 0)
        manager.release_job(runtime.workspace_id)
        manager.unmount("session-a")
        workdir.rename(self.root / "known-moved-away")

        missing = manager.list_known("session-a")[0]
        self.assertFalse(missing["current"])
        self.assertFalse(missing["available"])
        self.assertEqual(missing["issue"], "path_missing")

    def test_rename_changes_display_metadata_without_moving_directory(self):
        workdir = self._workdir("physical-folder")
        manager = WorkspaceManager(self.store)
        _, _, runtime = manager.mount("session-a", str(workdir), "read_only")
        old_revision = runtime.metadata_revision
        old_authorization = manager.path_authorization(runtime.workspace_id)

        renamed = manager.rename(runtime.workspace_id, "贝恩分析项目")

        self.assertEqual(renamed.name, "贝恩分析项目")
        self.assertEqual(runtime.name, "贝恩分析项目")
        self.assertGreater(runtime.metadata_revision, old_revision)
        self.assertTrue(workdir.is_dir())
        self.assertFalse((self.root / "贝恩分析项目").exists())
        self.assertEqual(self.store.find(runtime.workspace_id).name, "贝恩分析项目")
        self.assertEqual(manager.list_known("session-a")[0]["name"], "贝恩分析项目")
        self.assertIsNot(
            manager.path_authorization(runtime.workspace_id), old_authorization
        )

    def test_rename_rejects_control_characters_without_mutation(self):
        workdir = self._workdir("unchanged")
        metadata = self.store.open_or_create(workdir, name="原名称")

        with self.assertRaises(RuntimeError):
            self.store.rename(metadata.workspace_id, "坏\n名称")

        self.assertEqual(self.store.find(metadata.workspace_id).name, "原名称")

    def test_forget_hides_discovery_but_preserves_identity_and_files(self):
        workdir = self._workdir("forgettable")
        metadata = self.store.open_or_create(workdir)
        metadata_file = self.store.metadata_path(workdir)
        before = metadata_file.read_bytes()

        forgotten = self.store.forget(metadata.workspace_id)

        self.assertIsNotNone(forgotten)
        self.assertEqual(self.store.list_known(), [])
        self.assertEqual(self.store.find(metadata.workspace_id).workspace_id, metadata.workspace_id)
        self.assertTrue(workdir.is_dir())
        self.assertEqual(metadata_file.read_bytes(), before)
        raw_index = json.loads(self.index.read_text(encoding="utf-8"))
        self.assertTrue(raw_index["workspaces"][metadata.workspace_id]["hidden"])

        reopened = self.store.open_or_create(workdir)
        self.assertEqual(reopened.workspace_id, metadata.workspace_id)
        self.assertEqual(len(self.store.list_known()), 1)

    def test_manager_forget_rejects_connected_and_leased_workspace(self):
        workdir = self._workdir("busy")
        manager = WorkspaceManager(self.store)
        _, _, runtime = manager.mount("session-a", str(workdir))

        ok, message, _ = manager.forget(runtime.workspace_id)
        self.assertFalse(ok)
        self.assertIn("会话", message)

        manager.acquire_job(runtime.workspace_id)
        manager.unmount("session-a")
        ok, message, _ = manager.forget(runtime.workspace_id)
        self.assertFalse(ok)
        self.assertIn("任务", message)

        manager.release_job(runtime.workspace_id)
        ok, message, _ = manager.forget(runtime.workspace_id)
        self.assertTrue(ok, message)
        self.assertEqual(manager.list_known(), [])

    def test_path_authorization_cache_is_keyed_by_workspace_identity(self):
        first_root = self._workdir("auth-first")
        second_root = self._workdir("auth-second")
        manager = WorkspaceManager(self.store)
        _, _, first = manager.mount("session-a", str(first_root))
        first_auth = manager.path_authorization(first.workspace_id)
        self.assertIs(first_auth, manager.path_authorization(first.workspace_id))
        self.assertEqual(first_auth.workspace_id, first.workspace_id)

        manager.acquire_job(first.workspace_id)
        _, _, second = manager.mount("session-a", str(second_root))
        second_auth = manager.path_authorization(second.workspace_id)
        self.assertNotEqual(first_auth.workspace_id, second_auth.workspace_id)
        self.assertNotEqual(first_auth.allowed_roots[0], second_auth.allowed_roots[0])
        self.assertIs(first_auth, manager.path_authorization(first.workspace_id))

        manager.release_job(first.workspace_id)
        self.assertIsNone(manager.path_authorization(first.workspace_id))

    def test_legacy_database_and_registry_are_not_modified(self):
        workdir = self._workdir()
        meta = workdir / ".zhixi"
        meta.mkdir()
        database = meta / "workspace.duckdb"
        registry = meta / "registry.json"
        database.write_bytes(b"legacy-duckdb-content")
        registry.write_bytes(b'{"legacy": true}')
        before = (database.read_bytes(), registry.read_bytes())

        metadata = self.store.open_or_create(workdir)

        self.assertEqual(before, (database.read_bytes(), registry.read_bytes()))
        self.assertTrue(metadata.legacy["upgraded_from_session_mount"])

    def test_move_preserves_identity_when_old_root_disappears(self):
        original = self._workdir("original")
        workspace_id = self.store.open_or_create(original).workspace_id
        moved = self.root / "moved"
        original.rename(moved)

        reopened = self.store.open_or_create(moved)

        self.assertEqual(reopened.workspace_id, workspace_id)
        self.assertEqual(Path(reopened.root_path), moved.resolve())

    def test_copy_gets_new_identity_when_original_still_exists(self):
        original = self._workdir("original")
        original_metadata = self.store.open_or_create(original)
        copied = self.root / "copied"
        shutil.copytree(original, copied)

        copied_metadata = self.store.open_or_create(copied)

        self.assertNotEqual(copied_metadata.workspace_id, original_metadata.workspace_id)
        self.assertEqual(
            copied_metadata.cloned_from_workspace_id,
            original_metadata.workspace_id,
        )
        self.assertEqual(
            self.store.open_or_create(original).workspace_id,
            original_metadata.workspace_id,
        )

    def test_corrupt_and_future_metadata_are_rejected_without_overwrite(self):
        corrupt = self._workdir("corrupt")
        corrupt_file = self.store.metadata_path(corrupt)
        corrupt_file.parent.mkdir()
        corrupt_file.write_bytes(b"{definitely-not-json")
        before = corrupt_file.read_bytes()
        with self.assertRaises(CorruptWorkspaceMetadata):
            self.store.open_or_create(corrupt)
        self.assertEqual(corrupt_file.read_bytes(), before)

        future = self._workdir("future")
        future_file = self.store.metadata_path(future)
        future_file.parent.mkdir()
        payload = {"schema_version": 999, "workspace_id": "future"}
        future_file.write_text(json.dumps(payload), encoding="utf-8")
        before_future = future_file.read_text(encoding="utf-8")
        with self.assertRaises(FutureWorkspaceMetadata):
            self.store.open_or_create(future)
        self.assertEqual(future_file.read_text(encoding="utf-8"), before_future)

    def test_concurrent_first_mount_mints_only_one_identity(self):
        workdir = self._workdir()

        def open_workspace(_number):
            return WorkspaceMetadataStore(self.index).open_or_create(workdir).workspace_id

        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(open_workspace, range(24)))

        self.assertEqual(len(set(ids)), 1)
        on_disk = json.loads(
            self.store.metadata_path(workdir).read_text(encoding="utf-8")
        )
        self.assertEqual(on_disk["workspace_id"], ids[0])

    def test_concurrent_sessions_share_one_runtime(self):
        workdir = self._workdir()
        manager = WorkspaceManager(self.store)

        def mount_session(number):
            ok, message, runtime = manager.mount(
                f"session-{number}", str(workdir), "read_write"
            )
            self.assertTrue(ok, message)
            return runtime

        with ThreadPoolExecutor(max_workers=8) as pool:
            runtimes = list(pool.map(mount_session, range(24)))

        self.assertEqual(len({id(runtime) for runtime in runtimes}), 1)
        self.assertEqual(runtimes[0].ref_count, 24)

    def test_broken_global_index_is_rebuildable(self):
        workdir = self._workdir()
        self.index.parent.mkdir(parents=True)
        self.index.write_text("not-json", encoding="utf-8")

        metadata = self.store.open_or_create(workdir)

        rebuilt = json.loads(self.index.read_text(encoding="utf-8"))
        self.assertIn(metadata.workspace_id, rebuilt["workspaces"])
        self.assertTrue(list(self.index.parent.glob("index.json.corrupt-*")))


if __name__ == "__main__":
    unittest.main()
