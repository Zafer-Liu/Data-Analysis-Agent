"""Persistent identity metadata for mounted workspaces.

The per-directory ``.zhixi/workspace.json`` file is authoritative.  The
global index is only a rebuildable discovery aid used to distinguish a moved
workspace from a copied one.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from infrastructure.paths import data_path


WORKSPACE_SCHEMA_VERSION = 1
INDEX_SCHEMA_VERSION = 1


class WorkspaceMetadataError(RuntimeError):
    """Base class for workspace identity failures."""


class CorruptWorkspaceMetadata(WorkspaceMetadataError):
    """The authoritative metadata file cannot be safely interpreted."""


class FutureWorkspaceMetadata(WorkspaceMetadataError):
    """The metadata was written by a newer, unsupported version."""


_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def _path_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve(strict=False)))
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normal_path(path: Path | str) -> str:
    return os.path.normcase(str(Path(path).resolve(strict=False)))


def _is_missing_ephemeral_temp_root(path: Path) -> bool:
    """Hide stale temporary mounts from the user-facing discovery list."""
    if path.is_dir():
        return False
    try:
        path.resolve(strict=False).relative_to(
            Path(tempfile.gettempdir()).resolve(strict=False)
        )
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def is_workspace_uuid(value: object) -> bool:
    """Return whether *value* is a canonical UUID v4 workspace id."""
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value.lower()


def _new_id() -> str:
    return str(uuid.uuid4())


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    """Durably replace a JSON file without exposing a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


@dataclass
class WorkspaceMetadata:
    workspace_id: str
    name: str
    root_path: str
    permission: str
    created_at: str
    last_opened_at: str
    metadata_revision: int = 1
    schema_version: int = WORKSPACE_SCHEMA_VERSION
    legacy: dict[str, Any] = field(default_factory=dict)
    cloned_from_workspace_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "root_path": self.root_path,
            "permission": self.permission,
            "created_at": self.created_at,
            "last_opened_at": self.last_opened_at,
            "metadata_revision": self.metadata_revision,
        }
        if self.legacy:
            result["legacy"] = self.legacy
        if self.cloned_from_workspace_id:
            result["cloned_from_workspace_id"] = self.cloned_from_workspace_id
        return result

    @classmethod
    def from_dict(cls, raw: object) -> "WorkspaceMetadata":
        if not isinstance(raw, dict):
            raise CorruptWorkspaceMetadata("workspace.json 根节点必须是对象。")
        version = raw.get("schema_version")
        if not isinstance(version, int):
            raise CorruptWorkspaceMetadata("workspace.json 缺少有效 schema_version。")
        if version > WORKSPACE_SCHEMA_VERSION:
            raise FutureWorkspaceMetadata(
                f"工作区元数据版本 {version} 高于当前支持版本 {WORKSPACE_SCHEMA_VERSION}。"
            )
        if version < 1:
            raise CorruptWorkspaceMetadata(f"不支持的工作区元数据版本：{version}")
        workspace_id = raw.get("workspace_id")
        if not is_workspace_uuid(workspace_id):
            raise CorruptWorkspaceMetadata("workspace.json 的 workspace_id 不是有效 UUID v4。")
        required_strings = ("name", "root_path", "permission", "created_at", "last_opened_at")
        if any(not isinstance(raw.get(key), str) or not raw[key] for key in required_strings):
            raise CorruptWorkspaceMetadata("workspace.json 缺少必需字段。")
        if raw["permission"] not in {"read_only", "read_write"}:
            raise CorruptWorkspaceMetadata("workspace.json 的 permission 无效。")
        revision = raw.get("metadata_revision")
        if not isinstance(revision, int) or revision < 1:
            raise CorruptWorkspaceMetadata("workspace.json 的 metadata_revision 无效。")
        legacy = raw.get("legacy") or {}
        if not isinstance(legacy, dict):
            raise CorruptWorkspaceMetadata("workspace.json 的 legacy 无效。")
        cloned_from = raw.get("cloned_from_workspace_id")
        if cloned_from is not None and not is_workspace_uuid(cloned_from):
            raise CorruptWorkspaceMetadata("workspace.json 的 cloned_from_workspace_id 无效。")
        return cls(
            workspace_id=workspace_id,
            name=raw["name"],
            root_path=raw["root_path"],
            permission=raw["permission"],
            created_at=raw["created_at"],
            last_opened_at=raw["last_opened_at"],
            metadata_revision=revision,
            schema_version=version,
            legacy=legacy,
            cloned_from_workspace_id=cloned_from,
        )


class WorkspaceMetadataStore:
    """Create/load stable workspace identities and maintain a global index."""

    def __init__(self, index_path: Path | str | None = None):
        self.index_path = Path(index_path or data_path("outputs", "workspaces", "index.json"))

    @staticmethod
    def metadata_path(root: Path | str) -> Path:
        return Path(root) / ".zhixi" / "workspace.json"

    def _read_metadata(self, path: Path) -> WorkspaceMetadata:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CorruptWorkspaceMetadata(f"无法读取工作区元数据：{exc}") from exc
        return WorkspaceMetadata.from_dict(raw)

    def _read_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"schema_version": INDEX_SCHEMA_VERSION, "workspaces": {}}
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            if (
                not isinstance(raw, dict)
                or raw.get("schema_version") != INDEX_SCHEMA_VERSION
                or not isinstance(raw.get("workspaces"), dict)
            ):
                raise ValueError("invalid index schema")
            return raw
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            # The index is explicitly non-authoritative. Preserve evidence and
            # rebuild it from metadata encountered during future mounts.
            try:
                backup = self.index_path.with_name(
                    f"{self.index_path.name}.corrupt-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
                )
                shutil.copy2(self.index_path, backup)
            except OSError:
                pass
            return {"schema_version": INDEX_SCHEMA_VERSION, "workspaces": {}}

    def find(self, workspace_id: str) -> WorkspaceMetadata | None:
        """Resolve a known stable ID without mounting or mutating its metadata."""
        if not is_workspace_uuid(workspace_id):
            return None
        with _path_lock(self.index_path):
            entry = self._read_index().get("workspaces", {}).get(workspace_id)
        if not isinstance(entry, dict) or not entry.get("root_path"):
            return None
        metadata_path = self.metadata_path(Path(entry["root_path"]))
        try:
            metadata = self._read_metadata(metadata_path)
        except (WorkspaceMetadataError, OSError):
            return None
        return metadata if metadata.workspace_id == workspace_id else None

    def list_known(self) -> list[dict[str, Any]]:
        """List discovery-index entries without mounting or rewriting them."""
        with _path_lock(self.index_path):
            entries = dict(self._read_index().get("workspaces", {}))
        known: list[dict[str, Any]] = []
        for workspace_id, entry in entries.items():
            if not is_workspace_uuid(workspace_id) or not isinstance(entry, dict):
                continue
            # A forgotten entry remains as a private stable-ID locator so old
            # artifact links keep working.  It is only hidden from discovery.
            if entry.get("hidden") is True:
                continue
            root_text = str(entry.get("root_path") or "").strip()
            if not root_text:
                continue
            root = Path(root_text)
            available = root.is_dir()
            if (
                not available
                and entry.get("discovery_kind") != "user"
                and _is_missing_ephemeral_temp_root(root)
            ):
                # Internal jobs and tests may briefly mount temporary roots.
                # Once deleted, they are not meaningful user workspace history.
                continue
            metadata = None
            issue = ""
            if available:
                try:
                    candidate = self._read_metadata(self.metadata_path(root))
                    if candidate.workspace_id == workspace_id:
                        metadata = candidate
                    else:
                        issue = "identity_mismatch"
                except (WorkspaceMetadataError, OSError):
                    issue = "metadata_unavailable"
            else:
                issue = "path_missing"
            known.append({
                "workspace_id": workspace_id,
                "name": metadata.name if metadata else str(entry.get("name") or root.name),
                "root_path": metadata.root_path if metadata else root_text,
                "permission": metadata.permission if metadata else "read_only",
                "created_at": metadata.created_at if metadata else entry.get("created_at", ""),
                "last_opened_at": (
                    metadata.last_opened_at if metadata else entry.get("last_opened_at", "")
                ),
                "available": bool(available and metadata is not None),
                "issue": issue,
            })
        return sorted(
            known,
            key=lambda item: str(item.get("last_opened_at") or ""),
            reverse=True,
        )

    def forget(self, workspace_id: str) -> dict[str, Any] | None:
        """Hide a Workspace from discovery without deleting files or identity lookup."""
        if not is_workspace_uuid(workspace_id):
            return None
        with _path_lock(self.index_path):
            index = self._read_index()
            entry = index.get("workspaces", {}).get(workspace_id)
            if not isinstance(entry, dict):
                return None
            entry = dict(entry)
            entry["hidden"] = True
            entry["hidden_at"] = _now()
            index["workspaces"][workspace_id] = entry
            _atomic_json_write(self.index_path, index)
            return entry

    def rename(self, workspace_id: str, name: str) -> WorkspaceMetadata:
        """Rename display metadata only; never rename or move the root directory."""
        raw_name = str(name or "")
        if any(ord(char) < 32 for char in raw_name):
            raise WorkspaceMetadataError("工作目录显示名称不能包含控制字符。")
        clean_name = " ".join(raw_name.split())
        if not clean_name or len(clean_name) > 80:
            raise WorkspaceMetadataError("工作目录显示名称长度必须为 1-80 个字符。")
        if not is_workspace_uuid(workspace_id):
            raise WorkspaceMetadataError("工作目录身份无效。")

        with _path_lock(self.index_path):
            index = self._read_index()
            entry = index.get("workspaces", {}).get(workspace_id)
        if not isinstance(entry, dict) or not entry.get("root_path"):
            raise WorkspaceMetadataError("工作目录不在发现索引中。")
        metadata_path = self.metadata_path(Path(entry["root_path"]))
        lock_paths = sorted((metadata_path, self.index_path), key=lambda p: _normal_path(p))
        first, second = (_path_lock(path) for path in lock_paths)
        with first, second:
            index = self._read_index()
            entry = index.get("workspaces", {}).get(workspace_id)
            if not isinstance(entry, dict) or not entry.get("root_path"):
                raise WorkspaceMetadataError("工作目录不在发现索引中。")
            metadata_path = self.metadata_path(Path(entry["root_path"]))
            metadata = self._read_metadata(metadata_path)
            if metadata.workspace_id != workspace_id:
                raise WorkspaceMetadataError("工作目录身份与发现索引不一致。")
            metadata.name = clean_name
            metadata.metadata_revision += 1
            _atomic_json_write(metadata_path, metadata.to_dict())
            entry["name"] = clean_name
            index["workspaces"][workspace_id] = entry
            _atomic_json_write(self.index_path, index)
            return metadata

    def open_or_create(
        self,
        root: Path | str,
        permission: str = "read_only",
        *,
        name: str | None = None,
        remember: bool = True,
    ) -> WorkspaceMetadata:
        root_path = Path(root).resolve(strict=True)
        if permission not in {"read_only", "read_write"}:
            raise WorkspaceMetadataError("工作目录权限无效。")
        metadata_path = self.metadata_path(root_path)

        # Lock both files in a stable order. The in-process locks prevent two
        # simultaneous mounts from assigning different ids to the same root.
        lock_paths = sorted((metadata_path, self.index_path), key=lambda p: _normal_path(p))
        first, second = (_path_lock(path) for path in lock_paths)
        with first, second:
            index = self._read_index()
            entries: dict[str, Any] = index["workspaces"]
            now = _now()
            current_root = str(root_path)

            if metadata_path.exists():
                metadata = self._read_metadata(metadata_path)
                previous_root = Path(metadata.root_path)
                indexed = entries.get(metadata.workspace_id)
                indexed_root = indexed.get("root_path") if isinstance(indexed, dict) else None
                origin_text = indexed_root or metadata.root_path
                origin = Path(origin_text)
                root_changed = _normal_path(origin) != _normal_path(root_path)

                if root_changed and origin.exists():
                    # Both roots exist: this is a copy/clone. The copied
                    # metadata must receive a fresh identity.
                    original_id = metadata.workspace_id
                    metadata = WorkspaceMetadata(
                        workspace_id=_new_id(),
                        name=name or root_path.name,
                        root_path=current_root,
                        permission=permission,
                        created_at=now,
                        last_opened_at=now,
                        cloned_from_workspace_id=original_id,
                    )
                else:
                    # Same path, or old root disappeared: normal reopen/move.
                    metadata.root_path = current_root
                    metadata.permission = permission
                    metadata.last_opened_at = now
                    metadata.metadata_revision += 1
                    if name:
                        metadata.name = name
            else:
                # Recover from a rebuildable index if only workspace.json was
                # lost, otherwise mint a new authoritative identity.
                path_match = next(
                    (
                        (wid, entry) for wid, entry in entries.items()
                        if is_workspace_uuid(wid)
                        and isinstance(entry, dict)
                        and _normal_path(entry.get("root_path", "")) == _normal_path(root_path)
                    ),
                    None,
                )
                workspace_id = path_match[0] if path_match else _new_id()
                created_at = (
                    str(path_match[1].get("created_at") or now) if path_match else now
                )
                legacy_files = (
                    root_path / ".zhixi" / "workspace.duckdb",
                    root_path / ".zhixi" / "registry.json",
                )
                metadata = WorkspaceMetadata(
                    workspace_id=workspace_id,
                    name=name or root_path.name,
                    root_path=current_root,
                    permission=permission,
                    created_at=created_at,
                    last_opened_at=now,
                    legacy={"upgraded_from_session_mount": True}
                    if any(path.exists() for path in legacy_files)
                    else {},
                )

            _atomic_json_write(metadata_path, metadata.to_dict())
            if remember:
                entries[metadata.workspace_id] = {
                    "name": metadata.name,
                    "root_path": metadata.root_path,
                    "created_at": metadata.created_at,
                    "last_opened_at": metadata.last_opened_at,
                    "discovery_kind": "user",
                }
                _atomic_json_write(self.index_path, index)
            return metadata


workspace_metadata_store = WorkspaceMetadataStore()
