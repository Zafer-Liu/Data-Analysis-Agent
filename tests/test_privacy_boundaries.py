import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask


class PrivacyPathTests(unittest.TestCase):
    def test_managed_cloud_content_root_is_not_the_volume(self):
        from infrastructure.paths import content_data_root, data_root

        with tempfile.TemporaryDirectory() as volume, tempfile.TemporaryDirectory() as transient:
            env = {
                "RAILWAY_PROJECT_ID": "project-test",
                "RAILWAY_VOLUME_MOUNT_PATH": volume,
                "BAA_TRANSIENT_DATA_DIR": transient,
                "BAA_DATA_DIR": "",
            }
            with patch.dict(os.environ, env, clear=False):
                self.assertEqual(Path(volume).resolve(), data_root())
                self.assertEqual(Path(transient).resolve(), content_data_root())
                self.assertNotEqual(data_root(), content_data_root())

    def test_docker_uses_single_worker_for_memory_only_sessions(self):
        dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
        self.assertIn("ENV BAA_WORKERS=1", dockerfile.read_text(encoding="utf-8"))

    def test_transient_root_cannot_point_inside_railway_volume(self):
        from infrastructure.paths import content_data_root

        with tempfile.TemporaryDirectory() as volume:
            env = {
                "RAILWAY_PROJECT_ID": "project-test",
                "RAILWAY_VOLUME_MOUNT_PATH": volume,
                "BAA_TRANSIENT_DATA_DIR": str(Path(volume) / "runtime"),
            }
            with patch.dict(os.environ, env, clear=False):
                with self.assertRaisesRegex(ValueError, "must not be inside"):
                    content_data_root()


class BusinessCanvasPrivacyTests(unittest.TestCase):
    def test_legacy_unowned_canvas_is_not_shared_with_cloud_owner(self):
        from data.business_canvas_store import BusinessCanvasStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = BusinessCanvasStore(Path(temp_dir) / "canvas.sqlite")
            try:
                legacy = store.create_project(
                    session_id="legacy-session",
                    template_id="swot_analysis",
                    title="legacy",
                    owner_uid="",
                )
                owned = store.create_project(
                    session_id="owned-session",
                    template_id="swot_analysis",
                    title="owned",
                    owner_uid="user-a",
                )
                visible = store.list_projects(owner_uid="user-a")
                self.assertEqual([owned["id"]], [item["id"] for item in visible])
                self.assertIsNone(store.get_project(legacy["id"], owner_uid="user-a"))
            finally:
                store.close()


class DashboardPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_cloud_dashboard_requires_exact_owner(self):
        from api import dashboard

        with tempfile.TemporaryDirectory() as temp_dir:
            payload = {"id": "dash", "owner_uid": "user-a", "widgets": []}
            Path(temp_dir, "dash.json").write_text(json.dumps(payload), encoding="utf-8")
            with (
                patch.object(dashboard, "_DASHBOARD_DIR", temp_dir),
                patch.object(dashboard, "is_cloud_managed_runtime", return_value=True),
                patch("api.auth.current_user", return_value={"id": "user-b"}),
                self.app.test_request_context("/api/dashboard/dash"),
            ):
                with self.assertRaises(Exception) as caught:
                    dashboard._load_dashboard("dash")
                self.assertEqual(404, getattr(caught.exception, "code", None))

            with (
                patch.object(dashboard, "_DASHBOARD_DIR", temp_dir),
                patch.object(dashboard, "is_cloud_managed_runtime", return_value=True),
                patch("api.auth.current_user", return_value={"id": "user-a"}),
                self.app.test_request_context("/api/dashboard/dash"),
            ):
                self.assertEqual("dash", dashboard._load_dashboard("dash")["id"])


if __name__ == "__main__":
    unittest.main()
