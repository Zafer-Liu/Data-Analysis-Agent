"""Strict, non-executable parser for directory-based SKILL.md packages."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .models import SkillDef, SkillResource, SkillSource, normalize_skill_tags

MAX_SKILL_BYTES = 100_000
MAX_PROMPT_CHARS = 50_000
MAX_DESCRIPTION_CHARS = 240
MAX_RESOURCE_FILES = 200
RESOURCE_DIRS = ("references", "scripts", "assets")
_FRONTMATTER_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|\Z)", re.DOTALL)


class SkillError(ValueError):
    """Raised when a Skill package cannot be safely loaded."""


def _discover_resources(skill_dir: Path) -> tuple[SkillResource, ...]:
    resources: list[SkillResource] = []
    for kind in RESOURCE_DIRS:
        root = skill_dir / kind
        if not root.is_dir() or root.is_symlink():
            continue
        for path in sorted(root.rglob("*"), key=lambda item: str(item).lower()):
            if len(resources) >= MAX_RESOURCE_FILES:
                raise SkillError(f"skill has more than {MAX_RESOURCE_FILES} resource files")
            if path.is_symlink() or not path.is_file():
                continue
            try:
                relative = path.relative_to(skill_dir).as_posix()
                size = path.stat().st_size
            except OSError as exc:
                raise SkillError(f"cannot inspect skill resource: {exc}") from exc
            resources.append(SkillResource(kind, relative, path, size))
    return tuple(resources)


def parse_skill_file(path: Path, *, source: SkillSource = "builtin") -> SkillDef:
    try:
        if path.is_symlink():
            raise SkillError("SKILL.md symlinks are not allowed")
        if path.stat().st_size > MAX_SKILL_BYTES:
            raise SkillError(f"skill file exceeds {MAX_SKILL_BYTES} bytes")
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillError(f"cannot read skill file: {exc}") from exc

    clean = raw.lstrip("\ufeff")
    match = _FRONTMATTER_RE.match(clean)
    if not match:
        raise SkillError("missing YAML frontmatter")
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillError("frontmatter must be a mapping")

    name = str(meta.get("name", "")).strip()
    description = str(meta.get("description", "")).strip()
    icon = str(meta.get("icon", "🧩")).strip() or "🧩"
    prompt = clean[match.end():].strip()
    allowed_tools = meta.get("allowedTools", [])
    tags = meta.get("tags", [])
    if not isinstance(allowed_tools, list) or not all(isinstance(item, str) for item in allowed_tools):
        raise SkillError("allowedTools must be a list of tool names")
    try:
        tags = normalize_skill_tags(tags)
    except ValueError as exc:
        raise SkillError(str(exc)) from exc
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillError(f"description exceeds {MAX_DESCRIPTION_CHARS} characters")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise SkillError(f"skill prompt exceeds {MAX_PROMPT_CHARS} characters")
    if len(icon) > 8:
        raise SkillError("icon is too long")

    skill_dir = path.parent if path.name.upper() == "SKILL.MD" else None
    resources = _discover_resources(skill_dir) if skill_dir else ()
    try:
        return SkillDef(
            name=name, description=description, prompt=prompt, path=path,
            icon=icon, allowed_tools=tuple(allowed_tools), tags=tags, source=source,
            resources=resources,
        )
    except ValueError as exc:
        raise SkillError(str(exc)) from exc

