"""Data model for explicit slash commands."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal


COMMAND_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*(?::[a-z][a-z0-9-]*)*$")
COMMAND_ALIAS_RE = re.compile(r"^(?:[a-z][a-z0-9-]*(?::[a-z][a-z0-9-]*)*|\?)$")
MAX_COMMAND_NAME_CHARS = 80
MAX_COMMAND_ALIASES = 12
MAX_DESCRIPTION_CHARS = 500
MAX_USAGE_CHARS = 300
MAX_ARGUMENT_HINT_CHARS = 300
MAX_ICON_CHARS = 16
MAX_CATEGORY_CHARS = 64
CommandSource = Literal["builtin", "user", "workspace"]
CommandArguments = Literal["none", "optional", "required"]
CommandConfirmation = Literal["none", "confirm"]


class CommandType(str, Enum):
    LOCAL = "local"
    LOCAL_UI = "local-ui"
    BACKEND = "backend"
    PROMPT = "prompt"


@dataclass(frozen=True)
class CommandDef:
    name: str
    description: str
    type: CommandType
    aliases: tuple[str, ...] = ()
    usage: str = ""
    argument_hint: str = ""
    arguments: CommandArguments = "none"
    icon: str = "⌘"
    category: str = "tools"
    prompt: str = ""
    handler_key: str = ""
    hidden: bool = False
    protected: bool = False
    uses_model: bool = False
    confirmation: CommandConfirmation = "none"
    source: CommandSource = "builtin"
    path: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.type, CommandType):
            raise ValueError(f"invalid command type: {self.type!r}")
        if not COMMAND_NAME_RE.fullmatch(self.name):
            raise ValueError(f"invalid command name: {self.name!r}")
        if len(self.name) > MAX_COMMAND_NAME_CHARS:
            raise ValueError(f"command name exceeds {MAX_COMMAND_NAME_CHARS} characters")
        if not self.description.strip():
            raise ValueError("command description is required")
        if len(self.description) > MAX_DESCRIPTION_CHARS:
            raise ValueError(
                f"command description exceeds {MAX_DESCRIPTION_CHARS} characters"
            )
        if len(self.aliases) > MAX_COMMAND_ALIASES:
            raise ValueError(f"command has more than {MAX_COMMAND_ALIASES} aliases")
        if len(set(self.aliases)) != len(self.aliases):
            raise ValueError(f"duplicate aliases for command {self.name!r}")
        for alias in self.aliases:
            if not COMMAND_ALIAS_RE.fullmatch(alias):
                raise ValueError(f"invalid command alias: {alias!r}")
            if alias == self.name:
                raise ValueError(f"command alias duplicates its name: {alias!r}")
        if self.arguments not in {"none", "optional", "required"}:
            raise ValueError(f"invalid command arguments mode: {self.arguments!r}")
        if self.confirmation not in {"none", "confirm"}:
            raise ValueError(f"invalid command confirmation mode: {self.confirmation!r}")
        for label, value, limit in (
            ("usage", self.usage, MAX_USAGE_CHARS),
            ("argument hint", self.argument_hint, MAX_ARGUMENT_HINT_CHARS),
            ("icon", self.icon, MAX_ICON_CHARS),
            ("category", self.category, MAX_CATEGORY_CHARS),
        ):
            if len(value) > limit:
                raise ValueError(f"command {label} exceeds {limit} characters")
        if self.type is CommandType.PROMPT and not self.prompt.strip():
            raise ValueError(f"prompt command {self.name!r} requires prompt text")
        if (
            self.type in {CommandType.LOCAL, CommandType.LOCAL_UI, CommandType.BACKEND}
            and not self.handler_key.strip()
        ):
            raise ValueError(f"{self.type.value} command {self.name!r} requires handler_key")
        if self.uses_model and not self.protected:
            raise ValueError("only protected commands may declare uses_model")
        if self.confirmation != "none" and not self.protected:
            raise ValueError("only protected commands may declare confirmation")

    def to_public_dict(
        self,
        *,
        availability: object | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "description": self.description,
            "type": self.type.value,
            "aliases": list(self.aliases),
            "usage": self.usage,
            "argument_hint": self.argument_hint,
            "arguments": self.arguments,
            "icon": self.icon,
            "category": self.category,
            "source": self.source,
            "available": True,
            "uses_model": self.uses_model,
            "confirmation": self.confirmation,
        }
        if availability is not None:
            to_public_dict = getattr(availability, "to_public_dict", None)
            if callable(to_public_dict):
                payload.update(to_public_dict())
        # Expose only audited client actions. Backend handler keys remain an
        # implementation detail and are never sent to the browser.
        if (
            self.type in {CommandType.LOCAL, CommandType.LOCAL_UI}
            and self.protected
            and self.handler_key.startswith("client:")
        ):
            payload["client_action"] = self.handler_key.removeprefix("client:")
        return payload
