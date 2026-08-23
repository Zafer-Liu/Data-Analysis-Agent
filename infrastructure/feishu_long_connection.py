"""Local-first Feishu event transport using the official long connection SDK.

The client makes an outbound WSS connection to Feishu, so a desktop/local
deployment needs no public URL or inbound firewall rule.  Webhook remains an
independent deployment fallback in ``api.feishu_bot``.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass

from data.feishu_bot_service import FeishuBotError, _configured_credentials
from data.feishu_bot_store import load_config


log = logging.getLogger(__name__)


@dataclass
class _ConnectionState:
    fingerprint: tuple[str, str] = ("", "")
    thread: threading.Thread | None = None
    status: str = "idle"
    error: str = ""


_LOCK = threading.RLock()
_STATE = _ConnectionState()


def status() -> dict[str, str]:
    with _LOCK:
        return {"status": _STATE.status, "error": _STATE.error}


def _event_dict(data, lark) -> tuple[dict, str]:
    """Normalize SDK event objects without retaining event content in logs."""
    raw = json.loads(lark.JSON.marshal(data))
    if not isinstance(raw, dict):
        return {}, ""
    header = raw.get("header") if isinstance(raw.get("header"), dict) else {}
    event = raw.get("event") if isinstance(raw.get("event"), dict) else raw
    return event, str(header.get("event_id") or "")


def _run(app, app_id: str, app_secret: str) -> None:
    try:
        import lark_oapi as lark

        def on_message(data):
            event, event_id = _event_dict(data, lark)
            if not event:
                return {}
            from api.feishu_bot import dispatch_inbound_event

            dispatch_inbound_event(app, event, event_id)
            return {}

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )
        with _LOCK:
            _STATE.status = "connected"
            _STATE.error = ""
        lark.ws.Client(
            app_id,
            app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
        ).start()
    except Exception as exc:
        with _LOCK:
            _STATE.status = "error"
            _STATE.error = type(exc).__name__
        log.warning("[feishu] long connection stopped: %s", type(exc).__name__)


def start_long_connection(app) -> bool:
    """Start exactly one local event listener for the active application config."""
    config = load_config()
    if not config["enabled"] or config["inbound_transport"] != "long_connection":
        return False
    try:
        app_id, app_secret = _configured_credentials(config)
    except FeishuBotError:
        return False
    fingerprint = (app_id, config["app_secret_ref"])
    with _LOCK:
        if _STATE.thread and _STATE.thread.is_alive():
            return _STATE.fingerprint == fingerprint
        _STATE.fingerprint = fingerprint
        _STATE.status = "starting"
        _STATE.error = ""
        _STATE.thread = threading.Thread(
            target=_run,
            args=(app, app_id, app_secret),
            daemon=True,
            name="feishu-long-connection",
        )
        _STATE.thread.start()
    return True
