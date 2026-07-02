"""Hook dataclasses and context expansion helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Action:
    type: str
    command: str = ""
    message: str = ""
    url: str = ""
    method: str = "POST"
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout: int = 10


@dataclass
class ActionResult:
    output: str = ""
    success: bool = True


@dataclass
class Hook:
    id: str
    event: str
    action: Action
    enabled: bool = True
    condition: str = ""
    reject: bool = False
    once: bool = False
    async_exec: bool = False
    executed: bool = False

    def should_run(self, ctx: "HookContext") -> bool:
        if not self.enabled or (self.once and self.executed):
            return False
        if not self.condition:
            return True
        from .conditions import evaluate_condition

        return evaluate_condition(self.condition, ctx)

    def mark_executed(self) -> None:
        self.executed = True


@dataclass
class HookContext:
    event_name: str
    session_id: str = ""
    turn_id: str = ""
    workspace_id: str = ""
    workspace_name: str = ""
    workspace_path: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_ok: bool | None = None
    tool_error: str = ""
    message: str = ""
    final_answer: str = ""
    error: str = ""
    model_provider: str = ""
    model: str = ""
    elapsed_seconds: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def child(self, **updates: Any) -> "HookContext":
        data = {
            "event_name": self.event_name,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "workspace_path": self.workspace_path,
            "tool_name": self.tool_name,
            "tool_args": dict(self.tool_args or {}),
            "tool_ok": self.tool_ok,
            "tool_error": self.tool_error,
            "message": self.message,
            "final_answer": self.final_answer,
            "error": self.error,
            "model_provider": self.model_provider,
            "model": self.model,
            "elapsed_seconds": self.elapsed_seconds,
            "extra": dict(self.extra or {}),
        }
        data.update(updates)
        return HookContext(**data)

    def get_field(self, name: str) -> Any:
        key = str(name or "").strip()
        if not key:
            return ""
        aliases = {
            "event": "event_name",
            "tool": "tool_name",
            "args": "tool_args",
            "ok": "tool_ok",
        }
        key = aliases.get(key, key)
        if key.startswith("args."):
            return _nested_get(self.tool_args, key[5:])
        if key.startswith("extra."):
            return _nested_get(self.extra, key[6:])
        return getattr(self, key, "")

    def expand(self, template: Any) -> Any:
        if isinstance(template, dict):
            return {k: self.expand(v) for k, v in template.items()}
        if isinstance(template, list):
            return [self.expand(v) for v in template]
        if not isinstance(template, str):
            return template

        def repl(match: re.Match[str]) -> str:
            name = match.group(1)
            value = self.get_field(_variable_to_field(name))
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False)
            if value is None:
                return ""
            return str(value)

        return re.sub(r"\$([A-Z][A-Z0-9_]*(?:\.[A-Za-z0-9_]+)*)", repl, template)


@dataclass
class HookSettings:
    enabled: bool = True
    allow_command_hooks: bool = False
    hooks: list[Hook] = field(default_factory=list)


class ToolRejectedError(Exception):
    def __init__(self, tool: str, reason: str, hook_id: str):
        super().__init__(reason)
        self.tool = tool
        self.reason = reason
        self.hook_id = hook_id


def _nested_get(source: Any, path: str) -> Any:
    current = source
    for part in str(path or "").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part, "")
        else:
            return ""
    return current


def _variable_to_field(name: str) -> str:
    base = name.lower()
    mapping = {
        "event": "event_name",
        "session_id": "session_id",
        "turn_id": "turn_id",
        "workspace_id": "workspace_id",
        "workspace_name": "workspace_name",
        "workspace_path": "workspace_path",
        "tool_name": "tool_name",
        "tool": "tool_name",
        "message": "message",
        "final_answer": "final_answer",
        "error": "error",
        "tool_error": "tool_error",
        "model": "model",
        "model_provider": "model_provider",
        "elapsed_seconds": "elapsed_seconds",
    }
    if base.startswith("tool_args."):
        return "args." + base[len("tool_args."):]
    return mapping.get(base, base)
