"""Load and validate persisted hook settings."""

from __future__ import annotations

from typing import Any

from .events import SUPPORTED_EVENTS, normalize_event_name
from .models import Action, Hook, HookSettings


ACTION_TYPES = {"prompt", "http", "command"}


class HookConfigError(ValueError):
    pass


def load_settings(raw: dict[str, Any] | None) -> HookSettings:
    raw = raw or {}
    hooks_raw = raw.get("hooks") or []
    if not isinstance(hooks_raw, list):
        raise HookConfigError("hooks must be a list")
    hooks = [_load_hook(item, index) for index, item in enumerate(hooks_raw)]
    return HookSettings(
        enabled=bool(raw.get("enabled", True)),
        allow_command_hooks=bool(raw.get("allow_command_hooks", False)),
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
    reject = bool(raw.get("reject", False))
    async_exec = bool(raw.get("async", raw.get("async_exec", False)))
    if reject and event != "pre_tool_use":
        raise HookConfigError(f"hook[{hook_id}].reject is only allowed for pre_tool_use")
    if event == "pre_tool_use" and async_exec:
        raise HookConfigError(f"hook[{hook_id}].pre_tool_use hooks cannot be async")
    return Hook(
        id=hook_id,
        event=event,
        action=action,
        enabled=bool(raw.get("enabled", True)),
        condition=str(raw.get("if") or raw.get("condition") or "").strip(),
        reject=reject,
        once=bool(raw.get("once", False)),
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
    action = Action(
        type=action_type,
        command=str(raw.get("command") or ""),
        message=message,
        url=str(raw.get("url") or ""),
        method=str(raw.get("method") or "POST").upper(),
        body=raw.get("body"),
        headers={str(k): str(v) for k, v in (raw.get("headers") or {}).items()},
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
        "enabled": hook.enabled,
        "event": hook.event,
        "if": hook.condition,
        "reject": hook.reject,
        "once": hook.once,
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
