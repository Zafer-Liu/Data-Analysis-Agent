"""Persistence for user hook settings."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from agent.hooks.loader import default_settings, load_settings, serialize_settings
from infrastructure.paths import data_path


def hooks_config_path() -> Path:
    return data_path("config", "hooks.json")


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
        fire_and_forget_side_effects=True,
    )
