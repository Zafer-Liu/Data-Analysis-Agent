"""Frozen native desktop entry point for Windows and macOS.

Starts the local Waitress server and hosts the existing web application in a
native pywebview window (WebView2 on Windows, WKWebView on macOS). A browser
mode remains available as an explicit fallback for diagnostics. The launcher
never installs Python dependencies or touches source-checkout uploads/outputs.
"""

from __future__ import annotations

import atexit
import json
import logging
import multiprocessing
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin, urlsplit


log = logging.getLogger("desktop_launcher")
SERVICE_ID = "business-analytics-agent"
APP_TITLE = "Business Analytics Agent"
DEFAULT_CLOUD_AUTH_URL = "https://app.sage-agent.online"

_DESKTOP_BRIDGE_SCRIPT = r"""
(function () {
  "use strict";
  if (window.__baaDesktopShellInstalled) return;
  window.__baaDesktopShellInstalled = true;

  function openNativeChild(rawUrl) {
    try {
      const resolved = new URL(rawUrl, window.location.href);
      if (resolved.origin !== window.location.origin) return false;
      window.pywebview.api.open_window(resolved.href).catch(function () {});
      return true;
    } catch (_) {
      return false;
    }
  }

  const originalOpen = window.open.bind(window);
  window.open = function (url, target, features) {
    if (target === "_blank" && url && openNativeChild(url)) return null;
    return originalOpen(url, target, features);
  };

  document.addEventListener("click", function (event) {
    const link = event.target && event.target.closest
      ? event.target.closest("a[href]")
      : null;
    if (!link || link.target !== "_blank") return;
    if (openNativeChild(link.href)) event.preventDefault();
  }, true);
}());
"""


def _normalize_windows_environment() -> None:
    """Supply a standard Windows variable omitted by some CI launch shells."""
    if os.name == "nt" and not os.environ.get("WINDIR"):
        os.environ["WINDIR"] = os.environ.get("SystemRoot", r"C:\Windows")


def _configure_cloud_auth_url() -> None:
    """Set the packaged client's cloud auth endpoint unless explicitly overridden."""
    configured = (os.environ.get("BAA_CLOUD_AUTH_URL") or "").strip().rstrip("/")
    os.environ["BAA_CLOUD_AUTH_URL"] = configured or DEFAULT_CLOUD_AUTH_URL


def _health_url(host: str, port: int) -> str:
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{browser_host}:{port}/api/health"


def _app_url(host: str, port: int) -> str:
    return _health_url(host, port).removesuffix("/api/health")


def probe_health(url: str, timeout: float = 1.0) -> bool:
    """Return True only for this application's intentionally minimal probe."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read(4096).decode("utf-8"))
        return bool(
            response.status == 200
            and payload.get("ok") is True
            and payload.get("status") == "healthy"
            and payload.get("service") == SERVICE_ID
        )
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return False


def wait_for_health(
    url: str,
    *,
    timeout: float = 30.0,
    interval: float = 0.2,
    probe: Callable[[str, float], bool] = probe_health,
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if probe(url, min(1.0, max(interval, 0.05))):
            return True
        time.sleep(max(interval, 0.01))
    return False


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _env_enabled(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _same_origin(left: str, right: str) -> bool:
    try:
        left_url = urlsplit(left)
        right_url = urlsplit(right)
        return (
            left_url.scheme.lower() in {"http", "https"}
            and left_url.scheme.lower() == right_url.scheme.lower()
            and left_url.hostname == right_url.hostname
            and left_url.port == right_url.port
        )
    except ValueError:
        return False


def _webview_storage_path() -> Path:
    from infrastructure.paths import data_path

    path = data_path("webview")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _webview_backend() -> str | None:
    override = str(os.environ.get("BAA_DESKTOP_GUI") or "").strip().lower()
    if override:
        return override
    if sys.platform == "win32":
        return "edgechromium"
    if sys.platform == "darwin":
        return "cocoa"
    return None


class DesktopApi:
    """Minimal JS bridge used only to create same-origin native child windows."""

    def __init__(self, webview_module, app_url: str):
        self._webview = webview_module
        self._app_url = app_url

    def _attach_bridge(self, window) -> None:
        # pywebview injects the window only when the parameter is literally
        # named ``window`` (see webview.event.Event.set).
        def install_bridge(window) -> None:
            window.run_js(_DESKTOP_BRIDGE_SCRIPT)

        window.events.loaded += install_bridge

    def open_window(self, raw_url: str) -> bool:
        candidate = urljoin(f"{self._app_url}/", str(raw_url or ""))
        if not _same_origin(candidate, self._app_url):
            return False
        child = self._webview.create_window(
            APP_TITLE,
            candidate,
            js_api=self,
            width=_positive_int_env("BAA_DESKTOP_CHILD_WIDTH", 1100),
            height=_positive_int_env("BAA_DESKTOP_CHILD_HEIGHT", 760),
            min_size=(720, 480),
            background_color="#F7F9FC",
            text_select=True,
            zoomable=True,
        )
        self._attach_bridge(child)
        return True


def _run_native_window(app_url: str) -> None:
    """Run the platform GUI loop on the main thread until all windows close."""
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = True

    desktop_api = DesktopApi(webview, app_url)
    window = webview.create_window(
        APP_TITLE,
        app_url,
        js_api=desktop_api,
        width=_positive_int_env("BAA_DESKTOP_WIDTH", 1360),
        height=_positive_int_env("BAA_DESKTOP_HEIGHT", 860),
        min_size=(960, 640),
        maximized=_env_enabled("BAA_DESKTOP_MAXIMIZED"),
        background_color="#F7F9FC",
        text_select=True,
        zoomable=True,
    )
    desktop_api._attach_bridge(window)
    webview.start(
        gui=_webview_backend(),
        debug=_env_enabled("BAA_DESKTOP_DEBUG"),
        private_mode=False,
        storage_path=str(_webview_storage_path()),
    )


def _open_browser(app_url: str) -> None:
    webbrowser.open(app_url, new=1, autoraise=True)


def _monitor_desktop_clients(registry, shutdown_event, close_server) -> None:
    startup_timeout = _positive_float_env("BAA_DESKTOP_STARTUP_TIMEOUT", 120.0)
    idle_timeout = _positive_float_env("BAA_DESKTOP_IDLE_TIMEOUT", 5.0)
    # Browsers throttle background-tab timers to ~once per minute, so the
    # heartbeat can legitimately stall well past the client's send interval.
    # Keep a generous grace period so a backgrounded page never makes the
    # server shut down under the user (which surfaces as "Failed to fetch").
    # Explicit page close still exits promptly via disconnect + idle_timeout.
    heartbeat_timeout = _positive_float_env("BAA_DESKTOP_HEARTBEAT_TIMEOUT", 90.0)
    while not shutdown_event.wait(0.5):
        if registry.should_shutdown(
            startup_timeout=startup_timeout,
            idle_timeout=idle_timeout,
            heartbeat_timeout=heartbeat_timeout,
        ):
            log.info("all desktop pages closed; stopping local server")
            close_server()
            return


def main() -> int:
    multiprocessing.freeze_support()
    _normalize_windows_environment()
    _configure_cloud_auth_url()
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    # Frozen builds run with console=False: basicConfig writes to a lost stderr.
    # Mirror everything to a log file so native-window failures are diagnosable
    # on end-user machines (otherwise the browser fallback is silent).
    try:
        from infrastructure.paths import data_path

        launcher_log = data_path("logs")
        launcher_log.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            launcher_log / "desktop-launcher.log", encoding="utf-8"
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        logging.getLogger().addHandler(file_handler)
    except Exception:
        log.exception("failed to attach launcher file log")

    if os.environ.get("BAA_ONEDIR_SELF_TEST") == "1":
        from frozen_smoke import run_frozen_smoke

        return run_frozen_smoke()

    host = os.environ.get("BAA_DESKTOP_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("BAA_DESKTOP_PORT") or os.environ.get("AGENT_PORT") or 0)
    except ValueError:
        log.error("BAA_DESKTOP_PORT/AGENT_PORT must be an integer")
        return 2
    if not 0 <= port <= 65535:
        log.error("desktop port must be between 0 and 65535")
        return 2

    health_url = _health_url(host, port)
    app_url = _app_url(host, port)
    if port and probe_health(health_url):
        if os.environ.get("BAA_NO_BROWSER") == "1":
            return 0
        if _env_enabled("BAA_DESKTOP_BROWSER"):
            _open_browser(app_url)
            return 0
        try:
            _run_native_window(app_url)
        except Exception as exc:
            log.exception("native desktop window failed; opening browser fallback: %s", exc)
            _open_browser(app_url)
        return 0

    previous_lifecycle = os.environ.get("BAA_DESKTOP_LIFECYCLE")
    os.environ["BAA_DESKTOP_LIFECYCLE"] = "1"
    try:
        from waitress import create_server
        from app import app
        from infrastructure.desktop_lifecycle import desktop_clients

        desktop_clients.reset()

        server = create_server(
            app,
            host=host,
            port=port,
            send_bytes=1,
            inbuf_overflow=1024 * 1024,
            connection_limit=100,
            channel_timeout=300,
        )
    except Exception as exc:
        if previous_lifecycle is None:
            os.environ.pop("BAA_DESKTOP_LIFECYCLE", None)
        else:
            os.environ["BAA_DESKTOP_LIFECYCLE"] = previous_lifecycle
        log.exception("failed to initialize desktop server: %s", exc)
        return 1

    effective_port = int(server.effective_port)
    health_url = _health_url(host, effective_port)
    app_url = _app_url(host, effective_port)
    closed = False
    shutdown_event = threading.Event()
    server_errors: list[Exception] = []

    def restore_lifecycle_environment() -> None:
        if previous_lifecycle is None:
            os.environ.pop("BAA_DESKTOP_LIFECYCLE", None)
        else:
            os.environ["BAA_DESKTOP_LIFECYCLE"] = previous_lifecycle

    def close_server(*_args) -> None:
        nonlocal closed
        if not closed:
            closed = True
            shutdown_event.set()
            server.close()

    atexit.register(close_server)
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, close_server)
        except (OSError, ValueError):
            pass

    def run_server() -> None:
        try:
            server.run()
        except Exception as exc:
            server_errors.append(exc)
            log.exception("desktop server stopped unexpectedly: %s", exc)
        finally:
            shutdown_event.set()

    server_thread = threading.Thread(
        target=run_server,
        daemon=True,
        name="desktop-waitress-server",
    )
    server_thread.start()
    log.info("Business Analytics Agent desktop -> %s", app_url)

    if not wait_for_health(health_url):
        log.error("desktop server did not become healthy: %s", health_url)
        close_server()
        server_thread.join(timeout=5.0)
        restore_lifecycle_environment()
        return 1

    def run_browser_mode() -> None:
        threading.Thread(
            target=_monitor_desktop_clients,
            args=(desktop_clients, shutdown_event, close_server),
            daemon=True,
            name="desktop-client-monitor",
        ).start()
        _open_browser(app_url)
        server_thread.join()

    try:
        if os.environ.get("BAA_NO_BROWSER") == "1":
            server_thread.join()
        elif _env_enabled("BAA_DESKTOP_BROWSER"):
            run_browser_mode()
        else:
            try:
                _run_native_window(app_url)
            except Exception as exc:
                log.exception("native desktop window failed; opening browser fallback: %s", exc)
                run_browser_mode()
    except KeyboardInterrupt:
        pass
    finally:
        close_server()
        server_thread.join(timeout=5.0)
        restore_lifecycle_environment()
    return 1 if server_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
