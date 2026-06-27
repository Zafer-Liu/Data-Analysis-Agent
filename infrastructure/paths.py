"""Canonical read-only resource and writable runtime-data paths.

Source checkouts keep the historical project-root layout. Frozen desktop
builds and explicit ``BAA_DATA_DIR`` runs use an OS user-data directory, so
the application never writes into Program Files or a macOS ``.app`` bundle.
This module never copies or migrates legacy data.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DIR_NAME = "BusinessAnalyticsAgent"


def source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _absolute_override(name: str) -> Path | None:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return path.resolve(strict=False)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    override = _absolute_override("BAA_RESOURCE_DIR")
    if override is not None:
        return override
    bundle_root = getattr(sys, "_MEIPASS", None)
    return Path(bundle_root).resolve(strict=False) if bundle_root else source_root()


def uses_external_data_root() -> bool:
    return bool(
        os.environ.get("BAA_DATA_DIR")
        or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
        or os.environ.get("VERCEL")
        or is_frozen()
    )


def data_root() -> Path:
    override = _absolute_override("BAA_DATA_DIR")
    if override is not None:
        return override
    railway_volume = _absolute_override("RAILWAY_VOLUME_MOUNT_PATH")
    if railway_volume is not None:
        return railway_volume
    if os.environ.get("VERCEL"):
        return Path("/tmp")
    if not is_frozen():
        return source_root()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return (base / APP_DIR_NAME).resolve(strict=False)


def resource_path(*parts: str | os.PathLike[str]) -> Path:
    return resource_root().joinpath(*parts)


def data_path(*parts: str | os.PathLike[str]) -> Path:
    return data_root().joinpath(*parts)


def runtime_config_path(filename: str, source_relative: str) -> Path:
    """Keep source-mode config locations; isolate packaged/override configs."""
    if uses_external_data_root():
        return data_path("config", filename)
    return resource_path(source_relative)
