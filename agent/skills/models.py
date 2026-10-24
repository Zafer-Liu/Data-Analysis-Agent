"""Data model for reusable analysis Skills."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SKILL_TAG_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
MAX_SKILL_TAGS = 12
ANALYSIS_MODES = frozenset({"business", "ml", "empirical"})
SkillSource = Literal["builtin", "user", "workspace", "workflow"]


def normalize_skill_tags(value: object) -> tuple[str, ...]:
    """Validate and normalize ordered, multi-valued Skill tags."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise ValueError("tags must be a list of tag IDs")
    tags = tuple(item.strip().lower() for item in value)
    if len(tags) > MAX_SKILL_TAGS:
        raise ValueError(f"skill has more than {MAX_SKILL_TAGS} tags")
    if any(not tag or not SKILL_TAG_RE.fullmatch(tag) for tag in tags):
        raise ValueError("tags must use lowercase letters, digits, and hyphens")
    if len(set(tags)) != len(tags):
        raise ValueError("duplicate skill tags are not allowed")
    return tags


@dataclass(frozen=True)
class SkillResource:
    kind: Literal["references", "scripts", "assets"]
    relative_path: str
    path: Path
    size: int


@dataclass(frozen=True)
class SkillDef:
    name: str
    description: str
    prompt: str
    path: Path
    icon: str = "🧩"
    allowed_tools: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    source: SkillSource = "builtin"
    resources: tuple[SkillResource, ...] = ()
    display_name: str = ""

    def __post_init__(self) -> None:
        if self.source not in {"builtin", "user", "workspace", "workflow"}:
            raise ValueError(f"invalid skill source: {self.source!r}")
        if not SKILL_NAME_RE.fullmatch(self.name):
            raise ValueError(f"invalid skill name: {self.name!r}")
        if not self.description.strip():
            raise ValueError("skill description is required")
        if not self.prompt.strip():
            raise ValueError("skill prompt is required")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError(f"duplicate allowed tools for skill {self.name!r}")
        normalize_skill_tags(self.tags)

    def to_public_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "display_name": self.display_name or self.name,
            "description": self.description,
            "icon": self.icon,
            "source": self.source,
            "tags": list(self.tags),
        }

    def supports_analysis_mode(self, mode: str) -> bool:
        """Whether this Skill declares itself available for an analysis mode."""
        normalized = str(mode or "").strip().lower()
        return normalized in ANALYSIS_MODES and f"mode-{normalized}" in self.tags
