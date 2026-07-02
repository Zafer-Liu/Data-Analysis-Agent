"""Parser for safe Markdown slash-command definitions."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .models import CommandDef, CommandSource, CommandType

MAX_COMMAND_BYTES = 100_000
MAX_PROMPT_CHARS = 50_000
_FRONTMATTER_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|\Z)", re.DOTALL)
_TRUSTED_ONLY_FIELDS = frozenset({
    "handler-key", "uses-model", "confirmation",
})


class CommandError(ValueError):
    """A command definition is invalid or unsafe."""


def command_name_from_path(base_dir: Path, path: Path) -> str:
    try:
        relative = path.relative_to(base_dir)
    except ValueError as exc:
        raise CommandError("command file is outside its source directory") from exc
    parts = list(relative.with_suffix("").parts)
    return ":".join(part.lower().replace(" ", "-") for part in parts)


def parse_command_file(
    base_dir: Path, path: Path, *, source: CommandSource = "user",
    allow_trusted_types: bool = False,
) -> CommandDef:
    try:
        if path.is_symlink():
            raise CommandError("command symlinks are not allowed")
        if path.stat().st_size > MAX_COMMAND_BYTES:
            raise CommandError(f"command file exceeds {MAX_COMMAND_BYTES} bytes")
        raw = path.read_text(encoding="utf-8").lstrip("\ufeff")
    except OSError as exc:
        raise CommandError(f"cannot read command file: {exc}") from exc

    match = _FRONTMATTER_RE.match(raw)
    if not match:
        raise CommandError("missing YAML frontmatter")
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise CommandError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise CommandError("frontmatter must be a mapping")

    name = command_name_from_path(base_dir, path)
    body = raw[match.end():].strip()
    if len(body) > MAX_PROMPT_CHARS:
        raise CommandError(f"command prompt exceeds {MAX_PROMPT_CHARS} characters")
    description = str(meta.get("description", "")).strip()
    aliases = meta.get("aliases", [])
    if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
        raise CommandError("aliases must be a list of command names")
    command_type = str(meta.get("type", "prompt")).strip().lower()
    if not allow_trusted_types and command_type != CommandType.PROMPT.value:
        raise CommandError("custom Markdown commands may only use type: prompt")
    if not allow_trusted_types:
        forbidden = sorted(field for field in _TRUSTED_ONLY_FIELDS if field in meta)
        if forbidden:
            raise CommandError(
                "custom Markdown commands cannot declare trusted fields: "
                + ", ".join(forbidden)
            )
    try:
        resolved_type = CommandType(command_type)
    except ValueError as exc:
        raise CommandError(f"invalid command type: {command_type}") from exc
    hidden = meta.get("hidden", False)
    uses_model = meta.get("uses-model", False)
    if not isinstance(hidden, bool):
        raise CommandError("hidden must be a boolean")
    if not isinstance(uses_model, bool):
        raise CommandError("uses-model must be a boolean")
    default_arguments = (
        "optional" if resolved_type is CommandType.PROMPT else "none"
    )

    try:
        return CommandDef(
            name=name,
            description=description or f"Custom command: {name}",
            type=resolved_type,
            aliases=tuple(item.strip().lower() for item in aliases),
            usage=str(meta.get("usage", "")).strip(),
            argument_hint=str(meta.get("argument-hint", "")).strip(),
            arguments=str(meta.get("arguments", default_arguments)).strip().lower(),
            icon=str(meta.get("icon", "⌘")).strip() or "⌘",
            category=str(meta.get("category", "custom")).strip() or "custom",
            prompt=body,
            handler_key=(
                str(meta.get("handler-key", "")).strip()
                if allow_trusted_types else ""
            ),
            hidden=hidden,
            protected=allow_trusted_types,
            uses_model=uses_model if allow_trusted_types else False,
            confirmation=(
                str(meta.get("confirmation", "none")).strip().lower()
                if allow_trusted_types else "none"
            ),
            source=source,
            path=path,
        )
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
