"""Persistence for user hook settings."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.hooks.loader import default_settings, load_settings, serialize_settings
from infrastructure.paths import data_path

_HISTORY_LOCK = threading.Lock()
_HISTORY_LIMIT = 500
_LEGACY_INTERNAL_ENDPOINT_IDS: set[str] = set()
_BUILTIN_HOOK_NAMES = {
    "promlight-turn-end": "PromLight 信号灯",
    "safe-sql-drop": "破坏性 SQL 防护（DROP）",
    "safe-sql-delete": "破坏性 SQL 防护（DELETE）",
    "safe-sql-update": "破坏性 SQL 防护（UPDATE）",
    "query-result-review": "查询结果复核",
    "tool-error-recovery": "工具失败恢复",
    "answer-quality-check": "结论质量检查",
}


def hooks_config_path() -> Path:
    return data_path("config", "hooks.json")


def hooks_history_path() -> Path:
    return data_path("config", "hooks-history.json")


def list_hook_history(limit: int = 100) -> list[dict[str, Any]]:
    try:
        limit = max(1, min(int(limit), _HISTORY_LIMIT))
    except (TypeError, ValueError):
        limit = 100
    with _HISTORY_LOCK:
        try:
            items = json.loads(hooks_history_path().read_text(encoding="utf-8"))
        except Exception:
            return []
    if not isinstance(items, list):
        return []
    configured_names = _configured_hook_names()
    visible = []
    for item in items:
        if not isinstance(item, dict):
            continue
        hook_id = str(item.get("hook_id") or "")
        if hook_id in _LEGACY_INTERNAL_ENDPOINT_IDS or hook_id.startswith("codex_"):
            continue
        # Historical records created before source tagging may be test residue.
        # New runtime records remain visible even if their Hook is deleted.
        configured = hook_id in configured_names
        if not configured and item.get("source") != "runtime":
            continue
        item = dict(item)
        configured_name = configured_names.get(hook_id, "")
        stored_name = str(item.get("hook_name") or "")
        # Earlier versions persisted the ID as the name. Do not present that as a
        # human-readable Hook name when the rule never had one configured.
        if stored_name == hook_id and not configured_name:
            stored_name = ""
        item["hook_name"] = configured_name or stored_name or _BUILTIN_HOOK_NAMES.get(hook_id) or "未命名 Hook"
        item["configured"] = configured
        visible.append(item)
    return list(reversed(visible[-limit:]))


def record_hook_trigger(hook: Any, notification: Any, ctx: Any, *, action_type: str) -> None:
    item = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hook_id": str(getattr(notification, "hook_id", "")),
        "hook_name": str(getattr(hook, "name", "")),
        "event": str(getattr(notification, "event", "")),
        "action_type": str(action_type or ""),
        "ok": bool(getattr(notification, "success", False)),
        # Command/HTTP output can contain credentials or response payloads.  The
        # audit log retains status and timing but only stores prompt text.
        "output": str(getattr(notification, "output", ""))[:500] if action_type == "prompt" else "",
        "source": "runtime",
        "session_id": str(getattr(ctx, "session_id", ""))[:200],
        "turn_id": str(getattr(ctx, "turn_id", ""))[:200],
    }
    with _HISTORY_LOCK:
        path = hooks_history_path()
        try:
            items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            items = items if isinstance(items, list) else []
            items.append(item)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(items[-_HISTORY_LIMIT:], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            return


def clear_hook_history() -> int:
    with _HISTORY_LOCK:
        path = hooks_history_path()
        try:
            items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            count = len(items) if isinstance(items, list) else 0
            path.unlink(missing_ok=True)
            return count
        except Exception:
            return 0


def _configured_hook_names() -> dict[str, str]:
    """Read explicit display names without requiring every legacy rule to have one."""
    raw = load_raw_settings()
    hooks = raw.get("hooks") if isinstance(raw, dict) else []
    if not isinstance(hooks, list):
        return {}
    return {
        str(hook.get("id") or ""): str(hook.get("name") or hook.get("title") or "").strip()
        for hook in hooks
        if isinstance(hook, dict) and str(hook.get("id") or "")
    }


def load_raw_settings() -> dict[str, Any]:
    path = hooks_config_path()
    if not path.exists():
        return default_settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_settings()
    return data if isinstance(data, dict) else default_settings()


def load_hook_settings():
    return load_settings(load_raw_settings())


def save_raw_settings(raw: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings(raw)
    normalized = serialize_settings(settings)
    path = hooks_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="hooks-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(normalized, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return normalized


def load_engine():
    settings = load_hook_settings()
    from agent.hooks.engine import HookEngine

    return HookEngine(
        settings.hooks,
        enabled=settings.enabled,
        allow_command_hooks=settings.allow_command_hooks,
        # HTTP/command hooks are synchronous unless their own async flag is
        # set.  This keeps production behaviour consistent with /api/hooks/test.
        fire_and_forget_side_effects=False,
    )
