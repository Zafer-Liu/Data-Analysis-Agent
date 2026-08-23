"""Conservative lifecycle operations for user-owned runtime artifacts."""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from infrastructure.paths import data_path

log = logging.getLogger(__name__)
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _audit_path() -> Path:
    return data_path("outputs", "lifecycle", "audit.jsonl")


def _settings_path() -> Path:
    return data_path("config", "lifecycle.json")


def load_lifecycle_settings() -> dict[str, Any]:
    defaults = {"retention_preset": "forever", "retention_custom_days": 30}
    path = _settings_path()
    if not path.exists():
        return defaults
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    preset = str(raw.get("retention_preset") or defaults["retention_preset"])
    if preset not in {"7", "14", "forever", "custom"}:
        preset = defaults["retention_preset"]
    try:
        custom_days = int(raw.get("retention_custom_days", defaults["retention_custom_days"]))
    except (TypeError, ValueError):
        custom_days = defaults["retention_custom_days"]
    custom_days = max(0, min(custom_days, 3650))
    return {"retention_preset": preset, "retention_custom_days": custom_days}


def save_lifecycle_settings(settings: dict[str, Any]) -> dict[str, Any]:
    preset = str(settings.get("retention_preset") or "forever")
    if preset not in {"7", "14", "forever", "custom"}:
        raise ValueError("保留策略无效")
    try:
        custom_days = int(settings.get("retention_custom_days", 30))
    except (TypeError, ValueError) as exc:
        raise ValueError("自定义保留天数必须是整数") from exc
    if not 0 <= custom_days <= 3650:
        raise ValueError("自定义保留天数必须在 0 到 3650 之间")
    normalized = {"retention_preset": preset, "retention_custom_days": custom_days}
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return normalized


def _record(event: str, payload: dict[str, Any]) -> None:
    entry = {"event": event, "at": _now(), **payload}
    path = _audit_path()
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        log.warning("[lifecycle] audit write failed: %s", exc)


def register_session_file(path: Path, *, session_id: str, autosave: bool) -> None:
    """Record an owned session artifact without exposing its contents."""
    _record("session_registered", {
        "path": str(path),
        "session_id": session_id,
        "autosave": autosave,
        "size_bytes": path.stat().st_size if path.exists() else 0,
    })


def _session_owned_artifacts(session_id: str, exclude_files: set[Path]) -> list[dict[str, Any]]:
    """Active registry artifacts owned by this session that no OTHER saved
    session references (the archived session's own files are excluded from the
    reference corpus so its self-references do not block the archive)."""
    if not session_id:
        return []
    corpus, _ = _artifact_reference_corpus(exclude_files=exclude_files)
    data_root = data_path().resolve(strict=False)
    with _LOCK:
        items = _load_registry()
    owned: list[dict[str, Any]] = []
    for item in _active_registry_items(items):
        if str(item.get("session_id") or "") != session_id:
            continue
        if str(item.get("type") or "") not in {"chart", "export", "report", "upload"}:
            continue
        relative = str(item.get("path") or "")
        if not relative or not (data_root / relative).is_file():
            continue
        tokens = _artifact_reference_tokens(item)
        if any(token and token in corpus for token in tokens):
            continue
        owned.append({
            "registry_id": str(item.get("id") or ""),
            "type": str(item.get("type") or ""),
            "filename": Path(relative).name,
            "relative_path": relative.replace("\\", "/"),
            "size_bytes": int(item.get("size_bytes") or 0),
        })
    return owned


def soft_delete_session_group(session_dir: Path, filename: str, *, archive_artifacts: bool = True) -> dict[str, Any]:
    """Move a saved session, same-session autosaves and the session's own
    artifacts into the managed trash as one recoverable group.

    Files without a readable matching session id are deliberately left in place.
    Artifacts (charts / exports / reports / uploads) owned by this session are
    archived together only when no other active saved session references them.
    The returned summary is safe for an API response and audit log.
    """
    root = session_dir.resolve(strict=False)
    target = (root / Path(filename).name).resolve(strict=False)
    if not _within(target, root) or not target.exists() or target.suffix != ".json":
        raise FileNotFoundError(filename)

    try:
        source = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取会话文件: {exc}") from exc
    session_id = str(source.get("session_id") or "").strip()
    candidates = [target]
    if session_id:
        for path in root.glob("*.json"):
            if path == target:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(data.get("session_id") or "").strip() == session_id and bool(data.get("autosave")):
                candidates.append(path)

    owned_artifacts = _session_owned_artifacts(session_id, set(candidates)) if (archive_artifacts and session_id) else []

    trash = data_path("outputs", ".trash", "sessions", f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex}")
    moved: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    moved_artifacts: list[dict[str, Any]] = []
    with _LOCK:
        trash.mkdir(parents=True, exist_ok=False)
        for path in candidates:
            try:
                destination = trash / path.name
                os.replace(path, destination)
                moved.append({"filename": path.name, "size_bytes": destination.stat().st_size})
            except OSError as exc:
                failed.append({"filename": path.name, "error": str(exc)})

        data_root = data_path().resolve(strict=False)
        for artifact in owned_artifacts:
            relative = artifact["relative_path"]
            source_path = (data_root / relative).resolve(strict=False)
            artifact_filename = f"{artifact['registry_id'].replace(':', '_')}__{artifact['filename']}"
            destination = (trash / "artifacts" / artifact_filename).resolve(strict=False)
            if not source_path.is_file() or not _within(source_path, data_root) or not _within(destination, trash):
                failed.append({"filename": artifact_filename, "error": "产物路径无效"})
                continue
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source_path, destination)
                moved_artifacts.append({**artifact, "moved_filename": artifact_filename})
            except OSError as exc:
                failed.append({"filename": artifact_filename, "error": str(exc)})

        if moved_artifacts:
            items = _load_registry()
            for artifact in moved_artifacts:
                rid = artifact["registry_id"]
                if rid in items:
                    items[rid]["status"] = "archived"
                    items[rid]["trash_id"] = trash.name
            _save_registry(items)

        manifest = {
            "kind": "saved_session",
            "deleted_at": _now(),
            "session_id": session_id,
            "source_filename": target.name,
            "files": moved,
            "artifacts": moved_artifacts,
            "failed": failed,
        }
        (trash / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "deleted": [item["filename"] for item in moved],
        "archived_artifacts": [item["filename"] for item in moved_artifacts],
        "retained": [],
        "pending_reclaim": [item["filename"] for item in moved] + [item["filename"] for item in moved_artifacts],
        "failed": failed,
        "trash_id": trash.name,
    }
    _record("session_soft_deleted", {"session_id": session_id, "archived_artifacts": len(moved_artifacts), **summary})
    return summary


def reclaim_expired_session_trash(*, retention_days: int = 30, now: datetime | None = None) -> dict[str, int]:
    """Permanently reclaim expired, manifest-backed session trash groups only."""
    if retention_days < 0:
        raise ValueError("retention_days must be non-negative")
    current = now or datetime.now()
    root = data_path("outputs", ".trash", "sessions")
    removed_groups = 0
    removed_files = 0
    freed_bytes = 0
    if not root.exists():
        return {"groups": 0, "files": 0, "bytes": 0}

    with _LOCK:
        for group in root.iterdir():
            manifest_path = group / "manifest.json"
            if not group.is_dir() or not _within(group, root) or not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                deleted_at = datetime.fromisoformat(str(manifest["deleted_at"]))
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                log.warning("[lifecycle] skip invalid trash group: %s", group.name)
                continue
            if (current - deleted_at).total_seconds() < retention_days * 86400:
                continue
            files = [path for path in group.rglob("*") if path.is_file()]
            group_bytes = sum(path.stat().st_size for path in files)
            try:
                for path in files:
                    path.unlink()
                for path in sorted(group.rglob("*"), key=lambda item: -len(item.parts)):
                    if path.is_dir():
                        path.rmdir()
                group.rmdir()
            except OSError as exc:
                log.warning("[lifecycle] cannot reclaim trash group %s: %s", group.name, exc)
                continue
            removed_groups += 1
            removed_files += len(files)
            freed_bytes += group_bytes

    summary = {"groups": removed_groups, "files": removed_files, "bytes": freed_bytes}
    if removed_groups:
        _record("session_trash_reclaimed", {"retention_days": retention_days, **summary})
    return summary


def lifecycle_report() -> dict[str, Any]:
    """Return a read-only disk-usage summary for lifecycle-managed locations."""
    locations = {
        "saved_sessions": data_path("outputs", "Session"),
        "session_trash": data_path("outputs", ".trash", "sessions"),
        "artifact_trash": data_path("outputs", ".trash", "artifacts"),
        "upload_trash": data_path("outputs", ".trash", "uploads"),
        "memory_trash": data_path("outputs", ".trash", "memories"),
        "uploads": data_path("uploads"),
        "charts": data_path("outputs", "charts"),
        "exports": data_path("outputs", "exports"),
        "memories": data_path("memory"),
    }
    report: dict[str, Any] = {"locations": {}, "total_files": 0, "total_bytes": 0}
    for name, root in locations.items():
        files = [path for path in root.rglob("*") if path.is_file()] if root.exists() else []
        size = sum(path.stat().st_size for path in files)
        report["locations"][name] = {"files": len(files), "bytes": size}
        report["total_files"] += len(files)
        report["total_bytes"] += size
    return report


def _session_trash_root() -> Path:
    return data_path("outputs", ".trash", "sessions")


def _artifact_trash_root() -> Path:
    return data_path("outputs", ".trash", "artifacts")


def _upload_trash_root() -> Path:
    return data_path("outputs", ".trash", "uploads")


def list_session_trash() -> list[dict[str, Any]]:
    """List recoverable session-trash groups without exposing filesystem paths."""
    root = _session_trash_root()
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for group in root.iterdir():
        manifest_path = group / "manifest.json"
        if not group.is_dir() or not _within(group, root) or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = manifest.get("files") or []
            artifacts = manifest.get("artifacts") or []
            items.append({
                "id": group.name,
                "deleted_at": str(manifest.get("deleted_at") or ""),
                "source_filename": str(manifest.get("source_filename") or ""),
                "session_id": str(manifest.get("session_id") or ""),
                "files": len(files),
                "artifacts": len(artifacts) if isinstance(artifacts, list) else 0,
                "bytes": sum(int(item.get("size_bytes") or 0) for item in files if isinstance(item, dict))
                    + sum(int(item.get("size_bytes") or 0) for item in artifacts if isinstance(item, dict)),
            })
        except (OSError, ValueError, json.JSONDecodeError):
            log.warning("[lifecycle] skip invalid trash group: %s", group.name)
    return sorted(items, key=lambda item: item["deleted_at"], reverse=True)


def restore_session_trash(trash_id: str) -> dict[str, Any]:
    """Restore a soft-deleted session group (session JSON + autosaves + archived
    artifacts) only when no target file conflicts."""
    if not trash_id or Path(trash_id).name != trash_id:
        raise FileNotFoundError(trash_id)
    root = _session_trash_root()
    group = (root / trash_id).resolve(strict=False)
    if not group.is_dir() or not _within(group, root):
        raise FileNotFoundError(trash_id)
    manifest_path = group / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest["files"]
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("回收站项目已损坏，无法恢复") from exc
    if not isinstance(files, list) or not files:
        raise ValueError("回收站项目不包含可恢复文件")
    artifacts = manifest.get("artifacts") or []
    if not isinstance(artifacts, list):
        artifacts = []

    session_dir = data_path("outputs", "Session")
    session_dir.mkdir(parents=True, exist_ok=True)
    sources: list[tuple[Path, Path, str, str]] = []  # (source, destination, kind, registry_id)
    for item in files:
        name = str(item.get("filename") or "") if isinstance(item, dict) else ""
        source = (group / Path(name).name).resolve(strict=False)
        destination = (session_dir / Path(name).name).resolve(strict=False)
        if not name or not source.is_file() or not _within(source, group) or not _within(destination, session_dir):
            raise ValueError("回收站项目包含无效文件")
        if destination.exists():
            raise ValueError(f"无法恢复：{destination.name} 已存在")
        sources.append((source, destination, "json", ""))

    data_root = data_path().resolve(strict=False)
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ValueError("回收站项目包含无效产物")
        registry_id = str(artifact.get("registry_id") or "")
        relative = str(artifact.get("relative_path") or "")
        moved_filename = str(artifact.get("moved_filename") or artifact.get("filename") or "")
        source = (group / "artifacts" / Path(moved_filename).name).resolve(strict=False)
        destination = (data_root / relative).resolve(strict=False)
        if not registry_id or not moved_filename or not relative:
            raise ValueError("回收站项目包含无效产物")
        if not source.is_file() or not _within(source, group) or not _within(destination, data_root):
            raise ValueError("回收站项目包含无效产物")
        if destination.exists():
            raise ValueError(f"无法恢复：{destination.name} 已存在")
        sources.append((source, destination, "artifact", registry_id))

    with _LOCK:
        for source, destination, kind, registry_id in sources:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
            if kind == "artifact" and registry_id:
                items = _load_registry()
                if registry_id in items:
                    items[registry_id]["status"] = "active"
                    items[registry_id].pop("trash_id", None)
                    _save_registry(items)
        manifest_path.unlink(missing_ok=True)
        group.rmdir()
    summary = {"restored": [destination.name for _, destination, _, _ in sources], "trash_id": trash_id}
    _record("session_trash_restored", summary)
    return summary


def _registry_path() -> Path:
    return data_path("outputs", "lifecycle", "artifacts.json")


def _load_registry() -> dict[str, dict[str, Any]]:
    path = _registry_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        log.warning("[lifecycle] artifact registry is unreadable; preserving files")
        return {}


def _save_registry(items: dict[str, dict[str, Any]]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(items, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def prune_registry_for_paths(relative_paths: set[str]) -> int:
    """Drop ACTIVE registry entries whose files were physically removed by the
    cleanup sweeper, so they do not linger as "registered but missing".

    `relative_paths` are paths relative to the managed data root (separator
    normalized to `/`). Only entries whose file is gone AND whose status is
    active are removed; archived/recycled entries are never touched.
    """
    normalized = {str(value).replace("\\", "/") for value in (relative_paths or ()) if str(value or "")}
    if not normalized:
        return 0
    with _LOCK:
        items = _load_registry()
        removed = 0
        for key, item in list(items.items()):
            if str(item.get("status") or "active") != "active":
                continue
            if str(item.get("path") or "").replace("\\", "/") in normalized:
                items.pop(key, None)
                removed += 1
        if removed:
            _save_registry(items)
    if removed:
        _record("registry_pruned", {"removed": removed, "scope": "sweep"})
    return removed


def prune_missing_registered() -> dict[str, int]:
    """Remove ACTIVE registry entries whose files no longer exist on disk.

    Used to reconcile historical "registered but missing" entries (e.g. files
    removed by earlier cleanup sweeps before registry sync existed).
    """
    root = data_path().resolve(strict=False)
    with _LOCK:
        items = _load_registry()
        removed: list[str] = []
        for key, item in list(items.items()):
            if str(item.get("status") or "active") != "active":
                continue
            relative = str(item.get("path") or "")
            if not relative or not (root / relative).is_file():
                removed.append(key)
                items.pop(key, None)
        if removed:
            _save_registry(items)
    if removed:
        _record("registry_pruned", {"removed": len(removed), "scope": "missing_reconcile"})
    return {"removed": len(removed)}


def _active_registry_items(items: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items.values() if str(item.get("status") or "active") == "active"]


def register_artifact(
    path: Path,
    *,
    artifact_type: str,
    session_id: str = "",
    workspace_id: str = "",
    artifact_id: str = "",
) -> str:
    """Register an owned artifact under a lifecycle-managed root.

    Unknown or out-of-root files are never registered, so later scans remain
    conservative and cannot be used to delete arbitrary paths.
    """
    root = data_path().resolve(strict=False)
    resolved = path.resolve(strict=False)
    if not path.is_file() or not _within(resolved, root):
        raise ValueError("产物路径不在受控数据目录内")
    key = artifact_id or uuid.uuid4().hex
    with _LOCK:
        items = _load_registry()
        items[key] = {
            "id": key,
            "type": artifact_type,
            "path": str(resolved.relative_to(root)),
            "session_id": session_id,
            "workspace_id": workspace_id,
            "created_at": _now(),
            "size_bytes": resolved.stat().st_size,
        }
        _save_registry(items)
    _record("artifact_registered", {"artifact_id": key, "type": artifact_type, "session_id": session_id})
    return key


def _managed_artifact_dirs() -> dict[str, Path]:
    return {
        "charts": data_path("outputs", "charts"),
        "exports": data_path("outputs", "exports"),
    }


def _safe_relative_path(value: str) -> Path:
    relative = Path(str(value or ""))
    if not str(value or "").strip() or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("产物路径无效")
    return relative


def artifact_cleanup_preview() -> dict[str, Any]:
    """List registry-backed artifacts that disappeared and unregistered files."""
    root = data_path().resolve(strict=False)
    managed = _managed_artifact_dirs()
    with _LOCK:
        items = _load_registry()
    active_items = _active_registry_items(items)
    registered_paths = {str(item.get("path") or "") for item in active_items}
    missing = [item["id"] for item in active_items if not (root / str(item.get("path") or "")).is_file()]
    unknown: list[dict[str, Any]] = []
    for kind, directory in managed.items():
        if not directory.exists():
            continue
        base = directory.resolve(strict=False)
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            resolved = path.resolve(strict=False)
            relative_to_root = str(resolved.relative_to(root))
            if relative_to_root not in registered_paths:
                unknown.append({
                    "type": kind,
                    "filename": path.name,
                    "relative_path": str(resolved.relative_to(base)).replace("\\", "/"),
                    "size_bytes": path.stat().st_size,
                })
    return {
        "registered": len(active_items),
        "missing_registered_ids": missing,
        "unknown_files": unknown,
        "unknown_bytes": sum(item["size_bytes"] for item in unknown),
        "dry_run": True,
    }


def uploads_storage_preview() -> dict[str, Any]:
    """Classify uploads without deleting or deciding reclaimability."""
    data_root = data_path().resolve(strict=False)
    uploads_root = data_path("uploads").resolve(strict=False)
    categories: dict[str, dict[str, int]] = {
        "registered_uploads": {"files": 0, "bytes": 0},
        "knowledge": {"files": 0, "bytes": 0},
        "parsed_excel_cache": {"files": 0, "bytes": 0},
        "unknown_uploads": {"files": 0, "bytes": 0},
    }
    samples: list[dict[str, Any]] = []
    cache_samples: list[dict[str, Any]] = []
    if not uploads_root.exists():
        return {"categories": categories, "samples": [], "cache_samples": [], "missing_registered_upload_ids": [], "dry_run": True}
    with _LOCK:
        items = _load_registry()
    active_items = _active_registry_items(items)
    registered_upload_paths = {
        str(item.get("path") or "")
        for item in active_items
        if str(item.get("type") or "") == "upload"
    }
    missing = [
        str(item.get("id") or "")
        for item in active_items
        if str(item.get("type") or "") == "upload" and not (data_root / str(item.get("path") or "")).is_file()
    ]
    for path in uploads_root.rglob("*"):
        if not path.is_file():
            continue
        resolved = path.resolve(strict=False)
        try:
            upload_relative = resolved.relative_to(uploads_root)
            data_relative = str(resolved.relative_to(data_root))
        except ValueError:
            continue
        parts = upload_relative.parts
        size = path.stat().st_size
        if data_relative in registered_upload_paths:
            category = "registered_uploads"
        elif parts and parts[0] == "knowledge":
            category = "knowledge"
        elif parts and parts[0] == ".parsed_excel":
            category = "parsed_excel_cache"
        else:
            category = "unknown_uploads"
        categories[category]["files"] += 1
        categories[category]["bytes"] += size
        sample = {
            "filename": path.name,
            "relative_path": str(upload_relative).replace("\\", "/"),
            "size_bytes": size,
            "category": category,
        }
        if category == "unknown_uploads" and len(samples) < 20:
            samples.append(sample)
        elif category == "parsed_excel_cache" and len(cache_samples) < 20:
            cache_samples.append(sample)
    return {
        "categories": categories,
        "samples": samples,
        "cache_samples": cache_samples,
        "missing_registered_upload_ids": [item for item in missing if item],
        "dry_run": True,
    }


def recycle_upload_file(category: str, relative_path: str) -> dict[str, Any]:
    """Move a safe upload-side candidate into managed upload trash."""
    if category not in {"unknown_uploads", "parsed_excel_cache"}:
        raise ValueError("仅支持回收未知上传或 Excel 解析缓存")
    uploads_root = data_path("uploads").resolve(strict=False)
    relative = _safe_relative_path(relative_path)
    target = (uploads_root / relative).resolve(strict=False)
    if not target.is_file() or not _within(target, uploads_root):
        raise FileNotFoundError(relative_path)
    parts = relative.parts
    if parts and parts[0] == "knowledge":
        raise ValueError("知识库文件不能在这里删除")
    actual_category = "parsed_excel_cache" if parts and parts[0] == ".parsed_excel" else "unknown_uploads"
    if actual_category != category:
        raise ValueError("上传文件分类不匹配")
    data_root = data_path().resolve(strict=False)
    registry_path = str(target.relative_to(data_root))
    with _LOCK:
        registered_upload_paths = {
            str(item.get("path") or "")
            for item in _active_registry_items(_load_registry())
            if str(item.get("type") or "") == "upload"
        }
        if registry_path in registered_upload_paths:
            raise ValueError("已登记上传不能按未知上传回收")
        trash_id = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex}"
        trash = _upload_trash_root() / trash_id
        trash.mkdir(parents=True, exist_ok=False)
        destination = trash / target.name
        size = target.stat().st_size
        os.replace(target, destination)
        manifest = {
            "kind": "upload_file",
            "deleted_at": _now(),
            "category": category,
            "relative_path": str(relative).replace("\\", "/"),
            "filename": target.name,
            "size_bytes": size,
        }
        (trash / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"trash_id": trash_id, "category": category, "filename": target.name, "size_bytes": size}
    _record("upload_file_recycled", summary)
    return summary


def _read_upload_trash_manifest(group: Path, root: Path) -> dict[str, Any] | None:
    manifest_path = group / "manifest.json"
    if not group.is_dir() or not _within(group, root) or not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("[lifecycle] skip invalid upload trash group: %s", group.name)
        return None
    if not isinstance(manifest, dict) or manifest.get("kind") != "upload_file":
        return None
    return manifest


def list_upload_trash() -> list[dict[str, Any]]:
    root = _upload_trash_root()
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for group in root.iterdir():
        manifest = _read_upload_trash_manifest(group, root)
        if not manifest:
            continue
        items.append({
            "id": group.name,
            "deleted_at": str(manifest.get("deleted_at") or ""),
            "category": str(manifest.get("category") or ""),
            "filename": str(manifest.get("filename") or ""),
            "relative_path": str(manifest.get("relative_path") or ""),
            "bytes": int(manifest.get("size_bytes") or 0),
        })
    return sorted(items, key=lambda item: item["deleted_at"], reverse=True)


def restore_upload_trash(trash_id: str) -> dict[str, Any]:
    if not trash_id or Path(trash_id).name != trash_id:
        raise FileNotFoundError(trash_id)
    root = _upload_trash_root()
    group = (root / trash_id).resolve(strict=False)
    if not group.is_dir() or not _within(group, root):
        raise FileNotFoundError(trash_id)
    manifest = _read_upload_trash_manifest(group, root)
    if not manifest:
        raise ValueError("上传回收站项目已损坏，无法恢复")
    filename = str(manifest.get("filename") or "")
    relative = _safe_relative_path(str(manifest.get("relative_path") or ""))
    source = (group / Path(filename).name).resolve(strict=False)
    uploads_root = data_path("uploads").resolve(strict=False)
    destination = (uploads_root / relative).resolve(strict=False)
    if not filename or not source.is_file() or not _within(source, group) or not _within(destination, uploads_root):
        raise ValueError("上传回收站项目包含无效文件")
    if destination.exists():
        raise ValueError(f"无法恢复：{destination.name} 已存在")
    with _LOCK:
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        (group / "manifest.json").unlink(missing_ok=True)
        group.rmdir()
    summary = {"restored": [destination.name], "trash_id": trash_id}
    _record("upload_trash_restored", summary)
    return summary


def reclaim_expired_upload_trash(*, retention_days: int = 30, now: datetime | None = None) -> dict[str, int]:
    if retention_days < 0:
        raise ValueError("retention_days must be non-negative")
    current = now or datetime.now()
    root = _upload_trash_root()
    removed_groups = 0
    removed_files = 0
    freed_bytes = 0
    if not root.exists():
        return {"groups": 0, "files": 0, "bytes": 0}
    with _LOCK:
        for group in root.iterdir():
            manifest = _read_upload_trash_manifest(group, root)
            if not manifest:
                continue
            try:
                deleted_at = datetime.fromisoformat(str(manifest["deleted_at"]))
            except (ValueError, KeyError):
                continue
            if (current - deleted_at).total_seconds() < retention_days * 86400:
                continue
            files = [path for path in group.rglob("*") if path.is_file()]
            group_bytes = sum(path.stat().st_size for path in files)
            try:
                for path in files:
                    path.unlink()
                for path in sorted(group.rglob("*"), key=lambda item: -len(item.parts)):
                    if path.is_dir():
                        path.rmdir()
                group.rmdir()
            except OSError as exc:
                log.warning("[lifecycle] cannot reclaim upload trash group %s: %s", group.name, exc)
                continue
            removed_groups += 1
            removed_files += len(files)
            freed_bytes += group_bytes
    summary = {"groups": removed_groups, "files": removed_files, "bytes": freed_bytes}
    if removed_groups:
        _record("upload_trash_reclaimed", {"retention_days": retention_days, **summary})
    return summary


def _artifact_reference_tokens(item: dict[str, Any]) -> set[str]:
    artifact_id = str(item.get("id") or "")
    relative = str(item.get("path") or "")
    filename = Path(relative).name if relative else ""
    tokens = {artifact_id, relative, relative.replace("\\", "/"), filename}
    if ":" in artifact_id:
        suffix = artifact_id.split(":", 1)[1]
        tokens.update({suffix, f"/api/chart/{suffix}", f"{suffix}.html"})
    return {token for token in tokens if len(token) >= 4}


def _artifact_reference_corpus(exclude_files: set[Path] | None = None) -> tuple[str, int]:
    """Build a bounded text corpus from managed metadata likely to hold refs.

    `exclude_files` lets an archiving operation drop the session files that are
    being archived themselves, so their own references do not count as
    "external" references that would block the archive.
    """
    roots = [data_path("outputs", "Session")]
    chunks: list[str] = []
    files = 0
    max_file_bytes = 2_000_000
    exclude = {path.resolve(strict=False) for path in (exclude_files or ())}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.json"):
            if not path.is_file():
                continue
            if path.resolve(strict=False) in exclude:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")[:max_file_bytes]
            except OSError:
                continue
            chunks.append(text)
            files += 1
    return "\n".join(chunks), files


def registered_artifact_reference_preview() -> dict[str, Any]:
    """Conservatively report registered artifact reference state.

    This is a preview only. A missing token in saved-session metadata is not
    proof that an artifact is safe to delete; it is only a candidate signal.
    """
    root = data_path().resolve(strict=False)
    with _LOCK:
        items = _load_registry()
    active_items = _active_registry_items(items)
    corpus, source_count = _artifact_reference_corpus()
    referenced: list[dict[str, Any]] = []
    unreferenced: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for item in active_items:
        artifact_id = str(item.get("id") or "")
        relative = str(item.get("path") or "")
        resolved = root / relative
        base = {
            "id": artifact_id,
            "type": str(item.get("type") or ""),
            "filename": Path(relative).name,
            "size_bytes": int(item.get("size_bytes") or 0),
            "session_id": str(item.get("session_id") or ""),
            "workspace_id": str(item.get("workspace_id") or ""),
        }
        if not resolved.is_file():
            missing.append(base)
            continue
        tokens = _artifact_reference_tokens(item)
        matched = sorted(token for token in tokens if token and token in corpus)
        if matched:
            referenced.append({**base, "matched_tokens": matched[:3]})
        else:
            unreferenced.append(base)
    return {
        "registered": len(active_items),
        "referenced": len(referenced),
        "unreferenced": len(unreferenced),
        "missing": len(missing),
        "reference_sources": source_count,
        "referenced_samples": referenced[:20],
        "unreferenced_samples": unreferenced[:20],
        "unreferenced_ids": [str(item.get("id") or "") for item in unreferenced],
        "missing_samples": missing[:20],
        "dry_run": True,
    }


def recycle_unregistered_artifact(kind: str, relative_path: str) -> dict[str, Any]:
    """Move an unregistered legacy chart/export into managed artifact trash."""
    managed = _managed_artifact_dirs()
    if kind not in managed:
        raise ValueError("仅支持回收 charts / exports 历史产物")
    root = data_path().resolve(strict=False)
    base = managed[kind].resolve(strict=False)
    relative = _safe_relative_path(relative_path)
    target = (base / relative).resolve(strict=False)
    if not target.is_file() or not _within(target, base):
        raise FileNotFoundError(relative_path)

    registry_path = str(target.relative_to(root))
    with _LOCK:
        registered_paths = {str(item.get("path") or "") for item in _active_registry_items(_load_registry())}
        if registry_path in registered_paths:
            raise ValueError("该产物已经登记，不能按历史产物回收")
        trash_id = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex}"
        trash = _artifact_trash_root() / trash_id
        trash.mkdir(parents=True, exist_ok=False)
        destination = trash / target.name
        size = target.stat().st_size
        os.replace(target, destination)
        manifest = {
            "kind": "unregistered_artifact",
            "deleted_at": _now(),
            "type": kind,
            "relative_path": str(relative).replace("\\", "/"),
            "filename": target.name,
            "size_bytes": size,
        }
        (trash / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"trash_id": trash_id, "type": kind, "filename": target.name, "size_bytes": size}
    _record("unregistered_artifact_recycled", summary)
    return summary


def recycle_registered_artifact(artifact_id: str) -> dict[str, Any]:
    """Move an active registered, unreferenced artifact into managed trash."""
    artifact_id = str(artifact_id or "")
    if not artifact_id:
        raise ValueError("artifact_id 不能为空")
    preview = registered_artifact_reference_preview()
    # Use the full unreferenced id set, not the 20-sample preview list, so
    # every unreferenced registered artifact remains recyclable.
    candidate_ids = {str(item) for item in preview.get("unreferenced_ids") or []}
    if artifact_id not in candidate_ids:
        raise ValueError("该产物仍有引用或未进入可回收候选")
    root = data_path().resolve(strict=False)
    with _LOCK:
        items = _load_registry()
        item = items.get(artifact_id)
        if not item or str(item.get("status") or "active") != "active":
            raise FileNotFoundError(artifact_id)
        artifact_type = str(item.get("type") or "")
        if artifact_type not in {"chart", "export", "report"}:
            raise ValueError("仅支持回收 chart/export/report 已登记产物")
        relative = _safe_relative_path(str(item.get("path") or ""))
        target = (root / relative).resolve(strict=False)
        if not target.is_file() or not _within(target, root):
            raise FileNotFoundError(artifact_id)
        trash_id = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex}"
        trash = _artifact_trash_root() / trash_id
        trash.mkdir(parents=True, exist_ok=False)
        destination = trash / target.name
        size = target.stat().st_size
        os.replace(target, destination)
        item["status"] = "recycled"
        item["trash_id"] = trash_id
        item["recycled_at"] = _now()
        _save_registry(items)
        manifest = {
            "kind": "registered_artifact",
            "deleted_at": item["recycled_at"],
            "registry_id": artifact_id,
            "type": artifact_type,
            "relative_path": str(relative).replace("\\", "/"),
            "filename": target.name,
            "size_bytes": size,
        }
        (trash / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"trash_id": trash_id, "artifact_id": artifact_id, "type": artifact_type, "filename": target.name, "size_bytes": size}
    _record("registered_artifact_recycled", summary)
    return summary


def _read_artifact_trash_manifest(group: Path, root: Path) -> dict[str, Any] | None:
    manifest_path = group / "manifest.json"
    if not group.is_dir() or not _within(group, root) or not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("[lifecycle] skip invalid artifact trash group: %s", group.name)
        return None
    if not isinstance(manifest, dict) or manifest.get("kind") not in {"unregistered_artifact", "registered_artifact"}:
        return None
    return manifest


def list_artifact_trash() -> list[dict[str, Any]]:
    """List recoverable artifact trash groups without exposing filesystem paths."""
    root = _artifact_trash_root()
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for group in root.iterdir():
        manifest = _read_artifact_trash_manifest(group, root)
        if not manifest:
            continue
        items.append({
            "id": group.name,
            "deleted_at": str(manifest.get("deleted_at") or ""),
            "kind": str(manifest.get("kind") or ""),
            "registry_id": str(manifest.get("registry_id") or ""),
            "type": str(manifest.get("type") or ""),
            "filename": str(manifest.get("filename") or ""),
            "relative_path": str(manifest.get("relative_path") or ""),
            "bytes": int(manifest.get("size_bytes") or 0),
        })
    return sorted(items, key=lambda item: item["deleted_at"], reverse=True)


def restore_artifact_trash(trash_id: str) -> dict[str, Any]:
    """Restore an artifact trash item when the original target path is free."""
    if not trash_id or Path(trash_id).name != trash_id:
        raise FileNotFoundError(trash_id)
    root = _artifact_trash_root()
    group = (root / trash_id).resolve(strict=False)
    if not group.is_dir() or not _within(group, root):
        raise FileNotFoundError(trash_id)
    manifest = _read_artifact_trash_manifest(group, root)
    if not manifest:
        raise ValueError("产物回收站项目已损坏，无法恢复")
    trash_kind = str(manifest.get("kind") or "")
    kind = str(manifest.get("type") or "")
    filename = str(manifest.get("filename") or "")
    relative = _safe_relative_path(str(manifest.get("relative_path") or ""))
    managed = _managed_artifact_dirs()
    if not filename:
        raise ValueError("产物回收站项目包含无效目标")
    source = (group / Path(filename).name).resolve(strict=False)
    if trash_kind == "unregistered_artifact":
        if kind not in managed:
            raise ValueError("产物回收站项目包含无效目标")
        base = managed[kind].resolve(strict=False)
        destination = (base / relative).resolve(strict=False)
        boundary = base
    else:
        base = data_path().resolve(strict=False)
        destination = (base / relative).resolve(strict=False)
        boundary = base
    if not source.is_file() or not _within(source, group) or not _within(destination, boundary):
        raise ValueError("产物回收站项目包含无效文件")
    if destination.exists():
        raise ValueError(f"无法恢复：{destination.name} 已存在")
    with _LOCK:
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        if trash_kind == "registered_artifact":
            registry_id = str(manifest.get("registry_id") or "")
            items = _load_registry()
            if registry_id in items:
                items[registry_id]["status"] = "active"
                items[registry_id].pop("trash_id", None)
                items[registry_id].pop("recycled_at", None)
                _save_registry(items)
        (group / "manifest.json").unlink(missing_ok=True)
        group.rmdir()
    summary = {"restored": [destination.name], "trash_id": trash_id, "type": kind}
    _record("artifact_trash_restored", summary)
    return summary


def reclaim_expired_artifact_trash(*, retention_days: int = 30, now: datetime | None = None) -> dict[str, int]:
    """Permanently reclaim expired, manifest-backed artifact trash groups only."""
    if retention_days < 0:
        raise ValueError("retention_days must be non-negative")
    current = now or datetime.now()
    root = _artifact_trash_root()
    removed_groups = 0
    removed_files = 0
    freed_bytes = 0
    if not root.exists():
        return {"groups": 0, "files": 0, "bytes": 0}
    with _LOCK:
        for group in root.iterdir():
            manifest = _read_artifact_trash_manifest(group, root)
            if not manifest:
                continue
            try:
                deleted_at = datetime.fromisoformat(str(manifest["deleted_at"]))
            except (ValueError, KeyError):
                log.warning("[lifecycle] skip invalid artifact trash group: %s", group.name)
                continue
            if (current - deleted_at).total_seconds() < retention_days * 86400:
                continue
            files = [path for path in group.rglob("*") if path.is_file()]
            group_bytes = sum(path.stat().st_size for path in files)
            registry_id = str(manifest.get("registry_id") or "") if manifest.get("kind") == "registered_artifact" else ""
            try:
                for path in files:
                    path.unlink()
                for path in sorted(group.rglob("*"), key=lambda item: -len(item.parts)):
                    if path.is_dir():
                        path.rmdir()
                group.rmdir()
                if registry_id:
                    items = _load_registry()
                    if registry_id in items:
                        items.pop(registry_id, None)
                        _save_registry(items)
            except OSError as exc:
                log.warning("[lifecycle] cannot reclaim artifact trash group %s: %s", group.name, exc)
                continue
            removed_groups += 1
            removed_files += len(files)
            freed_bytes += group_bytes
    summary = {"groups": removed_groups, "files": removed_files, "bytes": freed_bytes}
    if removed_groups:
        _record("artifact_trash_reclaimed", {"retention_days": retention_days, **summary})
    return summary


def workspace_storage_preview(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Report known Workspace DuckDB files without deleting external data.

    Workspace roots are user-selected and may be outside the managed data root.
    They are therefore inventory-only here; physical cleanup stays behind the
    existing Workspace removal plan and lease checks.
    """
    items: list[dict[str, Any]] = []
    total_bytes = 0
    for record in records:
        workspace_id = str(record.get("workspace_id") or "")
        root_path = str(record.get("root_path") or "")
        if not workspace_id or not root_path:
            continue
        db_path = Path(root_path) / ".zhixi" / "workspace.duckdb"
        exists = db_path.is_file()
        size = db_path.stat().st_size if exists else 0
        total_bytes += size
        items.append({
            "workspace_id": workspace_id,
            "name": str(record.get("name") or Path(root_path).name),
            "db_exists": exists,
            "db_bytes": size,
            "active_lease_count": int(record.get("active_lease_count") or 0),
            "connected_session_count": int(record.get("connected_session_count") or 0),
            "cleanup": "protected" if exists else "missing",
        })
    return {"workspaces": items, "total_bytes": total_bytes, "dry_run": True}


def lifecycle_audit(limit: int = 50) -> list[dict[str, Any]]:
    """Return recent lifecycle audit metadata without filesystem paths."""
    limit = max(1, min(int(limit), 200))
    path = _audit_path()
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                entry.pop("path", None)
                entry.pop("trash_path", None)
                entries.append(entry)
    except OSError as exc:
        log.warning("[lifecycle] audit read failed: %s", exc)
        return []
    return list(reversed(entries[-limit:]))
