"""Canonical read-only resource and writable runtime-data paths.

Source checkouts keep the historical project-root layout. Frozen desktop
builds and explicit ``BAA_DATA_DIR`` runs use an OS user-data directory, so
the application never writes into Program Files or a macOS ``.app`` bundle.
This module never copies or migrates legacy data.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


APP_DIR_NAME = "BusinessAnalyticsAgent"


class PersistentAccountStorageError(RuntimeError):
    """Raised when a managed deployment has no durable account-data store."""


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


def is_cloud_managed_runtime() -> bool:
    return bool(os.environ.get("RAILWAY_PROJECT_ID")) or os.environ.get("VERCEL") == "1"


def account_data_root() -> Path:
    """Return the durable root for identity, audit and quota data.

    Managed deployments must never silently place account data in a container
    filesystem.  Railway supplies ``RAILWAY_VOLUME_MOUNT_PATH`` only when a
    Volume is attached; treating its absence as an error prevents an otherwise
    successful deployment from accepting registrations whose records disappear
    after a redeploy.

    Local and packaged applications retain the established ``data_root``
    behaviour.  This function intentionally does not honour ``BAA_DATA_DIR``
    in managed mode: the account ledger must live on the platform Volume.
    """
    if not is_cloud_managed_runtime():
        return data_root()

    railway_volume = _absolute_override("RAILWAY_VOLUME_MOUNT_PATH")
    if railway_volume is None:
        raise PersistentAccountStorageError(
            "Managed authentication requires RAILWAY_VOLUME_MOUNT_PATH backed by a persistent Volume"
        )
    if railway_volume.exists() and not railway_volume.is_dir():
        raise PersistentAccountStorageError(
            "RAILWAY_VOLUME_MOUNT_PATH must reference a directory"
        )
    return railway_volume


def account_data_path(*parts: str | os.PathLike[str]) -> Path:
    """Build a path for durable account-only data."""
    return account_data_root().joinpath(*parts)


def account_storage_ready() -> bool:
    """Return whether durable account storage is currently usable.

    The check intentionally exposes no filesystem path.  It is safe for the
    unauthenticated health endpoint and catches a Volume that disappears after
    process startup.
    """
    try:
        root = account_data_root()
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir() or not os.access(root, os.R_OK | os.W_OK | os.X_OK):
            return False
        # A metadata-only check can still succeed after a broken mount.  Force
        # a small synced write, then remove the probe immediately.  The PID
        # makes this safe if a process is briefly replaced during deployment.
        probe = root / f".account-storage-probe-{os.getpid()}"
        try:
            with probe.open("wb") as handle:
                handle.write(b"ok")
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                return False
        return True
    except (OSError, PersistentAccountStorageError, ValueError):
        return False


def transient_data_root() -> Path:
    """Non-persistent runtime root for managed-Web user content."""
    override = _absolute_override("BAA_TRANSIENT_DATA_DIR")
    if override is not None:
        return override
    return (Path(tempfile.gettempdir()) / f"{APP_DIR_NAME}-runtime").resolve(strict=False)


def content_data_root() -> Path:
    """Keep Web user content off the persistent cloud account-data volume."""
    if not is_cloud_managed_runtime():
        return data_root()
    transient = transient_data_root()
    railway_volume = _absolute_override("RAILWAY_VOLUME_MOUNT_PATH")
    if railway_volume is not None:
        try:
            transient.relative_to(railway_volume)
        except ValueError:
            pass
        else:
            raise ValueError(
                "BAA_TRANSIENT_DATA_DIR must not be inside RAILWAY_VOLUME_MOUNT_PATH"
            )
    return transient


def content_data_path(*parts: str | os.PathLike[str]) -> Path:
    return content_data_root().joinpath(*parts)


def runtime_config_path(filename: str, source_relative: str) -> Path:
    """Keep source-mode config locations; isolate packaged/override configs."""
    if is_cloud_managed_runtime():
        # Managed deployments receive platform configuration from environment
        # variables.  Any UI/runtime override is process-local and must not
        # turn into persistent user-controlled state on the account volume.
        return content_data_path("config", filename)
    if uses_external_data_root():
        return data_path("config", filename)
    return resource_path(source_relative)
