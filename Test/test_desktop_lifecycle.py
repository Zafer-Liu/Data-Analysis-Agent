"""Desktop browser leases stop the frozen server after the final page closes."""

from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask

from api.desktop import bp as desktop_bp
from infrastructure.desktop_lifecycle import DesktopClientRegistry, desktop_clients

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import desktop_launcher  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class DesktopClientRegistryTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.registry = DesktopClientRegistry(self.clock)

    def should_shutdown(self, **overrides):
        options = {
            "startup_timeout": 120.0,
            "idle_timeout": 5.0,
            "heartbeat_timeout": 10.0,
        }
        options.update(overrides)
        return self.registry.should_shutdown(**options)

    def test_last_explicit_disconnect_starts_short_idle_grace(self):
        self.registry.heartbeat("page_one")
        self.clock.now = 3.0
        self.registry.disconnect("page_one")
        self.clock.now = 7.9
        self.assertFalse(self.should_shutdown())
        self.clock.now = 8.0
        self.assertTrue(self.should_shutdown())

    def test_multiple_pages_keep_server_alive_until_all_disconnect(self):
        self.registry.heartbeat("page_one")
        self.registry.heartbeat("page_two")
        self.registry.disconnect("page_one")
        self.clock.now = 100.0
        self.assertFalse(self.should_shutdown())
        self.registry.disconnect("page_two")
        self.clock.now = 105.0
        self.assertTrue(self.should_shutdown())

    def test_missing_unload_event_expires_heartbeat_then_waits_idle_grace(self):
        self.registry.heartbeat("page_one")
        self.clock.now = 10.0
        self.assertFalse(self.should_shutdown())
        self.clock.now = 15.0
        self.assertTrue(self.should_shutdown())

    def test_browser_never_connecting_uses_startup_timeout(self):
        self.clock.now = 119.9
        self.assertFalse(self.should_shutdown())
        self.clock.now = 120.0
        self.assertTrue(self.should_shutdown())


class DesktopLifecycleApiTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(desktop_bp)
        desktop_clients.reset()

    @patch.dict(os.environ, {"BAA_DESKTOP_LIFECYCLE": "1"}, clear=False)
    def test_loopback_page_can_heartbeat_and_disconnect(self):
        client = self.app.test_client()
        headers = {"Origin": "http://localhost"}
        response = client.post(
            "/api/desktop/clients/page_client_123/heartbeat", headers=headers
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(desktop_clients.active_count(), 1)
        response = client.post(
            "/api/desktop/clients/page_client_123/disconnect", headers=headers
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(desktop_clients.active_count(), 0)

    def test_endpoint_is_hidden_outside_desktop_mode(self):
        with patch.dict(os.environ, {}, clear=True):
            response = self.app.test_client().post(
                "/api/desktop/clients/page_client_123/heartbeat"
            )
        self.assertEqual(response.status_code, 404)

    @patch.dict(os.environ, {"BAA_DESKTOP_LIFECYCLE": "1"}, clear=False)
    def test_cross_site_request_is_rejected(self):
        response = self.app.test_client().post(
            "/api/desktop/clients/page_client_123/heartbeat",
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(response.status_code, 404)


class DesktopMonitorTests(unittest.TestCase):
    def test_monitor_closes_server_when_registry_expires(self):
        registry = Mock()
        registry.should_shutdown.return_value = True
        event = Mock(spec=threading.Event)
        event.wait.return_value = False
        close_server = Mock()
        desktop_launcher._monitor_desktop_clients(registry, event, close_server)
        close_server.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
