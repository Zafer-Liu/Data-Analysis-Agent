"""Agent-facing helper for validated Hooks configuration."""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

from agent.hooks.loader import load_settings, serialize_settings
from data.hooks_store import load_raw_settings, save_raw_settings


def configure_hooks_from_agent(
    settings: Any,
    *,
    merge: bool = True,
    reason: str = "",
    confirm_command_hooks: bool = False,
) -> str:
    """Validate and save hook settings proposed by the model."""
    if not isinstance(settings, dict):
        raise ValueError("settings must be a JSON object")
    if _contains_command_hooks(settings):
        if not confirm_command_hooks:
            raise ValueError("command hooks require confirm_command_hooks=true")
        _validate_command_hooks(settings)
        settings = dict(settings)
        settings["allow_command_hooks"] = True
    proposed = serialize_settings(load_settings(settings))
    current = load_raw_settings()
    final = _merge_settings(current, proposed) if merge else proposed
    saved = save_raw_settings(final)
    hook_ids = [str(item.get("id") or "") for item in saved.get("hooks") or []]
    return json.dumps({
        "ok": True,
        "message": "Hooks configuration saved.",
        "merge": bool(merge),
        "reason": str(reason or "")[:500],
        "command_hooks_enabled": bool(saved.get("allow_command_hooks", False)),
        "hook_count": len(hook_ids),
        "hook_ids": hook_ids,
    }, ensure_ascii=False, indent=2)


def _merge_settings(current: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    existing_hooks = [
        item for item in (current.get("hooks") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    merged_by_id = {str(item.get("id")): item for item in existing_hooks}
    for item in proposed.get("hooks") or []:
        merged_by_id[str(item.get("id"))] = item
    return {
        "enabled": bool(proposed.get("enabled", current.get("enabled", True))),
        "allow_command_hooks": bool(
            current.get("allow_command_hooks", False)
            or proposed.get("allow_command_hooks", False)
        ),
        "hooks": list(merged_by_id.values()),
    }


def _contains_command_hooks(settings: dict[str, Any]) -> bool:
    if bool(settings.get("allow_command_hooks", False)):
        return True
    for hook in settings.get("hooks") or []:
        if not isinstance(hook, dict):
            continue
        action = hook.get("action") if isinstance(hook.get("action"), dict) else {}
        if str(action.get("type") or "").strip() == "command":
            return True
    return False


def _validate_command_hooks(settings: dict[str, Any]) -> None:
    for hook in settings.get("hooks") or []:
        if not isinstance(hook, dict):
            continue
        action = hook.get("action") if isinstance(hook.get("action"), dict) else {}
        if str(action.get("type") or "").strip() != "command":
            continue
        command = str(action.get("command") or "").strip()
        _validate_python_command(command)


def _validate_python_command(command: str) -> None:
    if not command:
        raise ValueError("command hook requires action.command")
    if re.search(r"[&|;<>()`]", command) or "$(" in command:
        raise ValueError("command hook must be a simple Python script invocation without shell operators")
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        raise ValueError(f"invalid command hook syntax: {exc}") from exc
    parts = [_strip_quotes(part) for part in parts if _strip_quotes(part)]
    if len(parts) < 2:
        raise ValueError("command hook must call a Python script, e.g. python path/to/script.py")
    exe = Path(parts[0]).name.lower()
    if exe not in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
        raise ValueError("command hook auto-configuration only supports Python executables")
    script = _find_script_arg(parts[1:])
    if not script:
        raise ValueError("command hook must include a .py script path")
    if "$" in script:
        raise ValueError("the Python script path cannot be a hook variable")
    if not script.lower().endswith(".py"):
        raise ValueError("command hook script must end with .py")


def _find_script_arg(args: list[str]) -> str:
    for arg in args:
        lower = arg.lower()
        if lower in {"-u", "-b", "-bb", "-s", "-e", "-i", "-q", "-v"}:
            continue
        if re.fullmatch(r"-\d(?:\.\d+)?", lower):
            continue
        if lower.startswith("-"):
            continue
        return arg
    return ""


def _strip_quotes(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text
