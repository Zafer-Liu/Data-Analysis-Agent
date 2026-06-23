"""P2 desktop launcher, health probe, and MCP-optional runtime contracts."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

import api
from api.mcp import bp as mcp_bp
from api.system import bp as system_bp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import desktop_launcher  # noqa: E402


class DesktopLauncherTests(unittest.TestCase):
    @patch("desktop_launcher.os.name", "nt")
    def test_windows_environment_uses_system_root_when_windir_is_missing(self):
        with patch.dict(os.environ, {"SystemRoot": r"C:\WINDOWS"}, clear=True):
            desktop_launcher._normalize_windows_environment()
            self.assertEqual(os.environ["WINDIR"], r"C:\WINDOWS")

    def test_urls_are_loopback_when_server_binds_all_interfaces(self):
        self.assertEqual(
            desktop_launcher._health_url("0.0.0.0", 5001),
            "http://127.0.0.1:5001/api/health",
        )
        self.assertEqual(
            desktop_launcher._app_url("::", 5001),
            "http://127.0.0.1:5001",
        )

    @patch("desktop_launcher.time.sleep", return_value=None)
    @patch("desktop_launcher.time.monotonic", side_effect=[0.0, 0.0, 0.1, 0.2])
    def test_wait_for_health_retries_until_ready(self, _clock, _sleep):
        probe = Mock(side_effect=[False, True])
        self.assertTrue(desktop_launcher.wait_for_health(
            "http://127.0.0.1/health", timeout=1, probe=probe
        ))
        self.assertEqual(probe.call_count, 2)

    @patch("desktop_launcher.webbrowser.open")
    @patch("desktop_launcher.probe_health", return_value=True)
    @patch.dict(os.environ, {"BAA_DESKTOP_PORT": "5001"}, clear=False)
    def test_existing_healthy_instance_is_reused(self, _probe, browser):
        self.assertEqual(desktop_launcher.main(), 0)
        browser.assert_called_once_with("http://127.0.0.1:5001", new=1, autoraise=True)

    @patch("desktop_launcher.webbrowser.open")
    @patch("desktop_launcher.probe_health", return_value=True)
    @patch.dict(
        os.environ,
        {"BAA_DESKTOP_PORT": "5001", "BAA_NO_BROWSER": "1"},
        clear=False,
    )
    def test_ci_mode_reuses_instance_without_opening_browser(self, _probe, browser):
        self.assertEqual(desktop_launcher.main(), 0)
        browser.assert_not_called()

    @patch.dict(os.environ, {"BAA_DESKTOP_PORT": "not-a-port"}, clear=False)
    def test_invalid_port_fails_before_app_import(self):
        self.assertEqual(desktop_launcher.main(), 2)

    @patch("desktop_launcher.threading.Thread")
    @patch("desktop_launcher.probe_health", return_value=False)
    @patch("desktop_launcher.multiprocessing.freeze_support")
    @patch("desktop_launcher.atexit.register")
    @patch("desktop_launcher.signal.signal")
    @patch.dict(os.environ, {"BAA_DESKTOP_PORT": "0"}, clear=False)
    def test_fresh_server_runs_and_closes_cleanly(
        self, _signal, _atexit, freeze_support, _probe, thread_cls
    ):
        server = Mock(effective_port=51234)
        fake_waitress = SimpleNamespace(create_server=Mock(return_value=server))
        fake_app = SimpleNamespace(app=object())
        with patch.dict(sys.modules, {"waitress": fake_waitress, "app": fake_app}):
            self.assertEqual(desktop_launcher.main(), 0)

        freeze_support.assert_called_once_with()
        server.run.assert_called_once_with()
        server.close.assert_called_once_with()
        self.assertEqual(thread_cls.return_value.start.call_count, 2)


class OptionalMcpRuntimeTests(unittest.TestCase):
    def test_background_services_skip_cleanly_when_mcp_directory_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "api.resource_path", return_value=Path(tmp) / "MCP"
        ):
            api._start_background_services()

    def test_mcp_catalog_reports_not_bundled_but_remains_configurable(self):
        class ConfigManager:
            @staticmethod
            def list_servers():
                return {}

        class RuntimeManager:
            @staticmethod
            def get_all_status():
                return []

        app = Flask(__name__)
        app.register_blueprint(mcp_bp)
        with tempfile.TemporaryDirectory() as tmp, patch(
            "api.mcp._get_managers", return_value=(ConfigManager(), RuntimeManager())
        ), patch("api.mcp.resource_path", return_value=Path(tmp) / "MCP"):
            response = app.test_client().get("/api/mcp/servers")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["servers"], [])
        self.assertFalse(payload["bundled_resources_available"])
        self.assertEqual(payload["bundled_message"], "内置 MCP 未随安装包提供")


class FrozenUpdateBoundaryTests(unittest.TestCase):
    @patch("api.system.is_frozen", return_value=True)
    @patch("api.system._download_zip")
    def test_frozen_app_refuses_source_overwrite_before_download(self, download, _frozen):
        app = Flask(__name__)
        app.register_blueprint(system_bp)
        response = app.test_client().post("/api/system/update")

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.get_json()["ok"])
        self.assertIn("下载并安装新版本", response.get_json()["error"])
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
