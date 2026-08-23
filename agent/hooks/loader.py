"""Load and validate persisted hook settings."""

from __future__ import annotations

from typing import Any

from .events import SUPPORTED_EVENTS, normalize_event_name
from .models import Action, Hook, HookSettings


ACTION_TYPES = {"prompt", "http", "command"}
ONCE_SCOPES = {"turn", "session", "global"}


class HookConfigError(ValueError):
    pass


def load_settings(raw: dict[str, Any] | None) -> HookSettings:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise HookConfigError("hook settings must be an object")
    hooks_raw = raw.get("hooks") or []
    if not isinstance(hooks_raw, list):
        raise HookConfigError("hooks must be a list")
    hooks = [_load_hook(item, index) for index, item in enumerate(hooks_raw)]
    hook_ids = [hook.id for hook in hooks]
    if len(set(hook_ids)) != len(hook_ids):
        raise HookConfigError("hook ids must be unique")
    return HookSettings(
        enabled=_require_bool(raw.get("enabled", True), "enabled"),
        allow_command_hooks=_require_bool(
            raw.get("allow_command_hooks", False), "allow_command_hooks"
        ),
        hooks=hooks,
    )


def serialize_settings(settings: HookSettings) -> dict[str, Any]:
    return {
        "enabled": settings.enabled,
        "allow_command_hooks": settings.allow_command_hooks,
        "hooks": [_serialize_hook(hook) for hook in settings.hooks],
    }


def default_settings() -> dict[str, Any]:
    return {"enabled": True, "allow_command_hooks": False, "hooks": []}


def _load_hook(raw: Any, index: int) -> Hook:
    if not isinstance(raw, dict):
        raise HookConfigError(f"hook[{index}] must be an object")
    hook_id = str(raw.get("id") or "").strip()
    if not hook_id:
        raise HookConfigError(f"hook[{index}].id is required")
    event = normalize_event_name(str(raw.get("event") or "").strip())
    if event not in SUPPORTED_EVENTS:
        raise HookConfigError(f"hook[{hook_id}].event is unsupported: {event}")
    action = _load_action(raw.get("action"), hook_id)
    name = str(raw.get("name") or raw.get("title") or "").strip()
    if not name and hook_id == "promlight-turn-end":
        name = "PromLight 信号灯"
    internal_endpoint = _require_bool(
        raw.get("internal_endpoint", _is_legacy_internal_endpoint(hook_id)),
        f"hook[{hook_id}].internal_endpoint",
    )
    reject = _require_bool(raw.get("reject", False), f"hook[{hook_id}].reject")
    async_exec = _require_bool(
        raw.get("async", raw.get("async_exec", False)), f"hook[{hook_id}].async"
    )
    once = _require_bool(raw.get("once", False), f"hook[{hook_id}].once")
    once_scope = str(raw.get("once_scope", "session") or "session").strip().lower()
    if once_scope not in ONCE_SCOPES:
        raise HookConfigError(
            f"hook[{hook_id}].once_scope must be one of: {', '.join(sorted(ONCE_SCOPES))}"
        )
    if reject and event != "pre_tool_use":
        raise HookConfigError(f"hook[{hook_id}].reject is only allowed for pre_tool_use")
    if reject and action.type != "prompt":
        raise HookConfigError(f"hook[{hook_id}].reject hooks must use a prompt action")
    if async_exec and action.type == "prompt":
        raise HookConfigError(f"hook[{hook_id}].prompt hooks cannot be async")
    if event == "pre_tool_use" and async_exec:
        raise HookConfigError(f"hook[{hook_id}].pre_tool_use hooks cannot be async")
    return Hook(
        id=hook_id,
        event=event,
        action=action,
        name=name,
        enabled=_require_bool(raw.get("enabled", True), f"hook[{hook_id}].enabled"),
        internal_endpoint=internal_endpoint,
        condition=str(raw.get("if") or raw.get("condition") or "").strip(),
        reject=reject,
        once=once,
        once_scope=once_scope,
        async_exec=async_exec,
    )


def _load_action(raw: Any, hook_id: str) -> Action:
    if not isinstance(raw, dict):
        raise HookConfigError(f"hook[{hook_id}].action must be an object")
    action_type = str(raw.get("type") or "").strip()
    if action_type not in ACTION_TYPES:
        raise HookConfigError(f"hook[{hook_id}].action.type is unsupported: {action_type}")
    timeout = raw.get("timeout", 10)
    try:
        timeout_int = int(timeout)
    except (TypeError, ValueError):
        raise HookConfigError(f"hook[{hook_id}].action.timeout must be an integer") from None
    if timeout_int <= 0 or timeout_int > 120:
        raise HookConfigError(f"hook[{hook_id}].action.timeout must be between 1 and 120")
    message = str(raw.get("message") or raw.get("prompt") or "")
    raw_headers = raw.get("headers") or {}
    if not isinstance(raw_headers, dict):
        raise HookConfigError(f"hook[{hook_id}].action.headers must be an object")
    action = Action(
        type=action_type,
        command=str(raw.get("command") or ""),
        message=message,
        url=str(raw.get("url") or ""),
        method=str(raw.get("method") or "POST").upper(),
        body=raw.get("body"),
        headers={str(k): str(v) for k, v in raw_headers.items()},
        timeout=timeout_int,
    )
    if action.type == "prompt" and not action.message.strip():
        raise HookConfigError(f"hook[{hook_id}].action.message is required")
    if action.type == "http" and not action.url.strip():
        raise HookConfigError(f"hook[{hook_id}].action.url is required")
    if action.type == "command" and not action.command.strip():
        raise HookConfigError(f"hook[{hook_id}].action.command is required")
    return action


def _serialize_hook(hook: Hook) -> dict[str, Any]:
    return {
        "id": hook.id,
        "name": hook.name,
        "enabled": hook.enabled,
        "internal_endpoint": hook.internal_endpoint,
        "event": hook.event,
        "if": hook.condition,
        "reject": hook.reject,
        "once": hook.once,
        "once_scope": hook.once_scope,
        "async": hook.async_exec,
        "action": {
            "type": hook.action.type,
            "command": hook.action.command,
            "message": hook.action.message,
            "url": hook.action.url,
            "method": hook.action.method,
            "body": hook.action.body,
            "headers": hook.action.headers,
            "timeout": hook.action.timeout,
        },
    }


def _is_legacy_internal_endpoint(hook_id: str) -> bool:
    """Keep legacy Codex event bridges out of user Hook records."""
    return hook_id.startswith("codex_")


def _require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise HookConfigError(f"{field} must be a boolean")
    return value
