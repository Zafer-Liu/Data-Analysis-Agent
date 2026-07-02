"""Typed slash-command parsing, prompt rendering, and handler dispatch."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .models import CommandDef, CommandType
from .registry import CommandRegistry

CommandHandler = Callable[[str, Any], Any | Awaitable[Any]]


class CommandDispatchError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    arguments: str
    is_command: bool


@dataclass(frozen=True)
class CommandDispatchResult:
    command: CommandDef
    arguments: str
    prompt: str = ""
    value: Any = None


def parse_slash_command(text: str) -> ParsedCommand:
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return ParsedCommand("", "", False)
    remainder = stripped[1:]
    if not remainder:
        return ParsedCommand("", "", True)
    parts = remainder.split(None, 1)
    return ParsedCommand(parts[0].lower(), parts[1].strip() if len(parts) > 1 else "", True)


def render_command_prompt(command: CommandDef, arguments: str) -> str:
    args = (arguments or "").strip()
    if "$ARGUMENTS" in command.prompt:
        return command.prompt.replace("$ARGUMENTS", args)
    if args:
        return f"{command.prompt}\n\n## User request\n\n{args}"
    return command.prompt


class CommandDispatcher:
    def __init__(
        self, registry: CommandRegistry, handlers: dict[str, CommandHandler] | None = None,
    ) -> None:
        self.registry = registry
        self.handlers = dict(handlers or {})

    def resolve(self, name_or_alias: str) -> CommandDef:
        command = self.registry.get((name_or_alias or "").lower())
        if command is None:
            raise CommandDispatchError(f"unknown slash command: {name_or_alias}")
        return command

    def prepare_agent_turn(
        self, name_or_alias: str, arguments: str = "",
    ) -> CommandDispatchResult:
        """Prepare only a prompt command for the Agent execution loop."""
        command = self.resolve(name_or_alias)
        if command.type is not CommandType.PROMPT:
            raise CommandDispatchError(
                f"{command.type.value} command /{command.name} cannot run in the Agent"
            )
        return CommandDispatchResult(
            command, arguments, prompt=render_command_prompt(command, arguments),
        )

    async def dispatch(
        self, name_or_alias: str, arguments: str = "", context: Any = None,
    ) -> CommandDispatchResult:
        command = self.resolve(name_or_alias)
        if command.type is CommandType.PROMPT:
            return CommandDispatchResult(
                command, arguments, prompt=render_command_prompt(command, arguments),
            )
        handler = self.handlers.get(command.handler_key)
        if handler is None:
            raise CommandDispatchError(
                f"command /{command.name} handler is unavailable: {command.handler_key}"
            )
        value = handler(arguments, context)
        if inspect.isawaitable(value):
            value = await value
        return CommandDispatchResult(command, arguments, value=value)
