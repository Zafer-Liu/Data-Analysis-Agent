"""Persistence for the optional Feishu application-bot connection.

The App Secret is a bearer credential. This module keeps only an opaque
reference in the configuration file and delegates the secret itself to the OS
credential store.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from infrastructure.credential_store import delete_secret, store_secret
from infrastructure.paths import data_path


_LOCK = threading.RLock()


def config_path() -> Path:
    return data_path("config", "feishu-bot.json")


def _default_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "app_id": "",
        "app_secret_ref": "",
        "event_verification_token_ref": "",
        "inbound_transport": "long_connection",
        "receive_id_type": "chat_id",
        "receive_id": "",
        "updated_at": "",
    }


def load_config() -> dict[str, Any]:
    """Return a normalized configuration that never contains the App Secret."""
    path = config_path()
    with _LOCK:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    raw = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "app_id": str(raw.get("app_id") or ""),
        "app_secret_ref": str(raw.get("app_secret_ref") or ""),
        "event_verification_token_ref": str(raw.get("event_verification_token_ref") or ""),
        "inbound_transport": (
            "webhook" if raw.get("inbound_transport") == "webhook" else "long_connection"
        ),
        "receive_id_type": str(raw.get("receive_id_type") or "chat_id"),
        "receive_id": str(raw.get("receive_id") or ""),
        "updated_at": str(raw.get("updated_at") or ""),
    }


def _write_config(config: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix="feishu-bot-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def save_config(
    *,
    enabled: bool,
    app_id: str,
    receive_id_type: str,
    receive_id: str,
    app_secret: str | None = None,
    event_verification_token: str | None = None,
    inbound_transport: str = "long_connection",
) -> dict[str, Any]:
    """Save settings and optionally replace the protected application secret.

    ``app_secret=None`` deliberately means "keep the current secret" so the UI
    does not need to read a secret back from the server to change the toggle.
    """
    with _LOCK:
        current = load_config()
        old_reference = current["app_secret_ref"]
        old_event_reference = current["event_verification_token_ref"]
        new_reference = old_reference
        new_event_reference = old_event_reference
        stored_new_reference = ""
        stored_new_event_reference = ""
        if app_secret is not None:
            stored_new_reference = store_secret(app_secret, label="feishu-app-secret")
            new_reference = stored_new_reference
        if event_verification_token is not None:
            stored_new_event_reference = store_secret(
                event_verification_token, label="feishu-event-verification-token",
            )
            new_event_reference = stored_new_event_reference

        next_config = {
            "enabled": bool(enabled),
            "app_id": app_id,
            "app_secret_ref": new_reference,
            "event_verification_token_ref": new_event_reference,
            "inbound_transport": (
                "webhook" if inbound_transport == "webhook" else "long_connection"
            ),
            "receive_id_type": receive_id_type,
            "receive_id": receive_id,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            _write_config(next_config)
        except Exception:
            for reference in (stored_new_reference, stored_new_event_reference):
                if reference:
                    try:
                        delete_secret(reference)
                    except Exception:
                        pass
            raise

        if stored_new_reference and old_reference and old_reference != stored_new_reference:
            try:
                delete_secret(old_reference)
            except Exception:
                # The new connection is usable even if a stale OS-vault item
                # cannot be removed.  Do not turn a successful save into loss.
                pass
        if (
            stored_new_event_reference
            and old_event_reference
            and old_event_reference != stored_new_event_reference
        ):
            try:
                delete_secret(old_event_reference)
            except Exception:
                pass
        return next_config
