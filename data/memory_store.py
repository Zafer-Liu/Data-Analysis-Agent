"""Scoped Markdown long-term-memory storage."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from infrastructure.paths import data_path

log = logging.getLogger(__name__)

MEMORY_TYPES = {"user", "feedback", "project", "reference"}
USER_TYPES = {"user", "feedback"}
WORKSPACE_TYPES = MEMORY_TYPES - USER_TYPES
MAX_TITLE_CHARS = 180
MAX_BODY_CHARS = 2_000
MAX_RECORDS = 200
MAX_INDEX_CHARS = 25_000
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SECRET_RE = re.compile(
    r"(?:api[_ -]?key|secret|password|passwd|token|authorization|bearer)\s*[:=]\s*\S+|"
    r"(?:mysql|postgres(?:ql)?|mongodb(?:\+srv)?|redis)://\S+|"
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----",
    re.IGNORECASE,
)
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def normalize_user_id(user_id: str | None) -> str:
    value = str(user_id or "").strip()[:200]
    return value or "local-default"


def _scope_root(scope: str, *, user_id: str = "", workspace_id: str = "") -> Path:
    if scope == "user":
        owner = normalize_user_id(user_id)
        root = data_path("memory")
        if owner != "local-default":
            root = root / "users" / hashlib.sha256(owner.encode("utf-8")).hexdigest()[:24]
        return root.resolve(strict=False)
    if scope == "workspace":
        if not workspace_id:
            raise ValueError("当前未挂载工作区，无法保存工作区记忆")
        from data.workspace import workspace_manager

        workspace = workspace_manager.root_for_workspace(str(workspace_id))
        if not workspace:
            raise ValueError("工作区不可用")
        return (Path(workspace).resolve(strict=False) / ".zhixi" / "memory").resolve(strict=False)
    raise ValueError("记忆作用域无效")


def _scope_for_type(memory_type: str) -> str:
    if memory_type not in MEMORY_TYPES:
        raise ValueError("记忆类型无效")
    return "user" if memory_type in USER_TYPES else "workspace"


def _lock(root: Path) -> threading.RLock:
    key = str(root)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _validate_text(value: Any, *, label: str, limit: int, required: bool = True, automatic: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{label}不能为空")
    if len(text) > limit:
        raise ValueError(f"{label}不能超过 {limit} 个字符")
    if automatic and _SECRET_RE.search(text):
        raise ValueError("自动记忆不能包含疑似凭据或连接信息")
    return text


def _validate_payload(payload: dict[str, Any], *, automatic: bool = False, existing: dict[str, Any] | None = None) -> dict[str, str]:
    source = dict(existing or {})
    source.update({key: value for key, value in payload.items() if value is not None})
    memory_type = str(source.get("type") or "").strip()
    scope = _scope_for_type(memory_type)
    name = str(source.get("name") or "").strip()
    if not _NAME_RE.fullmatch(name):
        raise ValueError("记忆 ID 必须是 kebab-case")
    title = _validate_text(source.get("title"), label="标题", limit=MAX_TITLE_CHARS, automatic=automatic)
    body = _validate_text(source.get("body"), label="正文", limit=MAX_BODY_CHARS, automatic=automatic)
    why = _validate_text(source.get("why"), label="原因", limit=500, required=False, automatic=automatic)
    how_to_apply = _validate_text(source.get("how_to_apply"), label="应用方式", limit=500, required=False, automatic=automatic)
    return {
        "name": name,
        "type": memory_type,
        "scope": scope,
        "title": title,
        "body": body,
        "why": why,
        "how_to_apply": how_to_apply,
    }


def _record_path(root: Path, name: str) -> Path:
    folder = root / "memories"
    path = (folder / f"{name}.md").resolve(strict=False)
    if not _within(path, folder):
        raise ValueError("记忆路径无效")
    return path


def _parse(path: Path, root: Path, scope: str) -> dict[str, Any] | None:
    if not _within(path, root) or path.suffix != ".md" or not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not raw.startswith("---\n"):
        return None
    try:
        _, frontmatter, body = raw.split("---\n", 2)
        metadata = yaml.safe_load(frontmatter) or {}
    except (ValueError, yaml.YAMLError):
        return None
    if not isinstance(metadata, dict):
        return None
    try:
        payload = _validate_payload({**metadata, "body": body.strip()}, existing=None)
    except ValueError:
        return None
    if _scope_for_type(payload["type"]) != scope or path.stem != payload["name"]:
        return None
    return {
        **payload,
        "created_at": str(metadata.get("created_at") or ""),
        "updated_at": str(metadata.get("updated_at") or ""),
        "last_seen_at": str(metadata.get("last_seen_at") or ""),
        "source_session": str(metadata.get("source_session") or ""),
    }


def _records(root: Path, scope: str) -> list[dict[str, Any]]:
    folder = root / "memories"
    if not folder.exists():
        return []
    records = [record for path in folder.glob("*.md") if (record := _parse(path, root, scope))]
    return sorted(records, key=lambda record: record["updated_at"], reverse=True)


def _render_index(records: list[dict[str, Any]]) -> str:
    labels = {"user": "用户偏好", "feedback": "纠正反馈", "project": "工作区知识", "reference": "参考资料"}
    lines = [f"- [{labels[record['type']]}] {record['title']} ({record['name']})" for record in records]
    kept: list[str] = []
    for line in lines[:MAX_RECORDS]:
        candidate = "\n".join(kept + [line])
        if len(candidate) > MAX_INDEX_CHARS:
            break
        kept.append(line)
    if len(kept) < len(lines):
        kept.append(f"<!-- memory index truncated, {len(lines) - len(kept)} more -->")
    return "\n".join(kept) + ("\n" if kept else "")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _rebuild_index(root: Path, scope: str) -> None:
    _write_atomic(root / "MEMORY.md", _render_index(_records(root, scope)))


def _audit(root: Path, operation: str, record: dict[str, Any], actor: str) -> None:
    path = root / "memory_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    item = {"op": operation, "actor": actor, "memory": record["name"], "type": record["type"], "ts": _now()}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def record_extraction_activity(*, user_id: str, session_id: str, status: str, message: str, records: list[dict[str, str]] | None = None) -> None:
    """Persist a compact, user-visible audit entry for an extraction attempt."""
    root = _scope_root("user", user_id=user_id)
    item = {
        "op": "extraction", "actor": "extraction", "session_id": str(session_id or "")[:200],
        "status": str(status or "unknown")[:30], "message": str(message or "")[:500],
        "records": [
            {"name": str(record.get("name") or "")[:120], "title": str(record.get("title") or "")[:180],
             "scope": str(record.get("scope") or "")[:20]}
            for record in (records or []) if isinstance(record, dict) and record.get("name")
        ][:5],
        "ts": _now(),
    }
    with _lock(root):
        path = root / "memory_events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def list_extraction_activity(*, user_id: str = "", session_id: str = "", limit: int = 12) -> list[dict[str, Any]]:
    root = _scope_root("user", user_id=user_id)
    path = root / "memory_events.jsonl"
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict) or item.get("op") != "extraction":
            continue
        if session_id and item.get("session_id") != session_id:
            continue
        entries.append(item)
        if len(entries) >= max(1, min(int(limit or 12), 50)):
            break
    return entries


def list_records(*, user_id: str = "", workspace_id: str = "") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for scope in ("user", "workspace"):
        try:
            root = _scope_root(scope, user_id=user_id, workspace_id=workspace_id)
        except ValueError:
            continue
        with _lock(root):
            records = _records(root, scope)
            _rebuild_index(root, scope)
        result.extend(records)
    return result


def get_record(name: str, *, user_id: str = "", workspace_id: str = "") -> dict[str, Any] | None:
    if not _NAME_RE.fullmatch(str(name or "")):
        return None
    for record in list_records(user_id=user_id, workspace_id=workspace_id):
        if record["name"] == name:
            return record
    return None


def create_record(payload: dict[str, Any], *, user_id: str = "", workspace_id: str = "", actor: str = "user", automatic: bool = False, source_session: str = "") -> dict[str, Any]:
    record = _validate_payload(payload, automatic=automatic)
    root = _scope_root(record["scope"], user_id=user_id, workspace_id=workspace_id)
    with _lock(root):
        target = _record_path(root, record["name"])
        if target.exists():
            raise ValueError("同名记忆已存在")
        timestamp = _now()
        saved = {**record, "created_at": timestamp, "updated_at": timestamp, "last_seen_at": timestamp[:10], "source_session": source_session[:200]}
        _write_record(root, saved)
        _rebuild_index(root, record["scope"])
        _audit(root, "create", saved, actor)
    return saved


def update_record(name: str, payload: dict[str, Any], *, user_id: str = "", workspace_id: str = "", actor: str = "user", automatic: bool = False) -> dict[str, Any] | None:
    current = get_record(name, user_id=user_id, workspace_id=workspace_id)
    if not current:
        return None
    # Drop None values so a partial update (e.g. body-only) does not null out
    # fields the caller did not intend to change (type, title, why, ...).
    filtered = {key: value for key, value in payload.items() if value is not None}
    merged = _validate_payload({**current, **filtered, "name": name}, automatic=automatic)
    if merged["scope"] != current["scope"]:
        raise ValueError("不能跨作用域修改记忆类型")
    root = _scope_root(current["scope"], user_id=user_id, workspace_id=workspace_id)
    with _lock(root):
        saved = {**current, **merged, "updated_at": _now(), "last_seen_at": current.get("last_seen_at") or ""}
        _write_record(root, saved)
        _rebuild_index(root, current["scope"])
        _audit(root, "update", saved, actor)
    return saved


def _memory_trash_root() -> Path:
    return data_path("outputs", ".trash", "memories")


def archive_record(name: str, *, user_id: str = "", workspace_id: str = "", actor: str = "user") -> bool:
    """Move a memory into the recoverable lifecycle trash (not a permanent delete).

    The `.md` file is moved under `outputs/.trash/memories/<id>/` with a
    manifest that records its original scope and source root, so it can be
    restored from the Settings → Storage screen later.
    """
    current = get_record(name, user_id=user_id, workspace_id=workspace_id)
    if not current:
        return False
    root = _scope_root(current["scope"], user_id=user_id, workspace_id=workspace_id)
    with _lock(root):
        source = _record_path(root, name)
        if not source.is_file() or not _within(source, root):
            return False
        trash_id = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex}"
        trash = _memory_trash_root() / trash_id
        trash.mkdir(parents=True, exist_ok=False)
        destination = trash / f"{name}.md"
        size = source.stat().st_size
        os.replace(source, destination)
        manifest = {
            "kind": "memory_record",
            "deleted_at": _now(),
            "name": name,
            "scope": current["scope"],
            "type": current["type"],
            "title": current.get("title", ""),
            "source_root": str(root),
            "filename": f"{name}.md",
            "size_bytes": size,
        }
        (trash / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _rebuild_index(root, current["scope"])
        _audit(root, "archive", current, actor)
    return True


def _read_memory_trash_manifest(group: Path, root: Path) -> dict[str, Any] | None:
    manifest_path = group / "manifest.json"
    if not group.is_dir() or not _within(group, root) or not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("[memory] skip invalid trash group: %s", group.name)
        return None
    if not isinstance(manifest, dict) or manifest.get("kind") != "memory_record":
        return None
    return manifest


def list_memory_trash() -> list[dict[str, Any]]:
    """List recoverable archived memories without exposing filesystem paths."""
    root = _memory_trash_root()
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for group in root.iterdir():
        manifest = _read_memory_trash_manifest(group, root)
        if not manifest:
            continue
        items.append({
            "id": group.name,
            "deleted_at": str(manifest.get("deleted_at") or ""),
            "name": str(manifest.get("name") or ""),
            "scope": str(manifest.get("scope") or ""),
            "type": str(manifest.get("type") or ""),
            "title": str(manifest.get("title") or ""),
            "filename": str(manifest.get("title") or manifest.get("name") or ""),
            "bytes": int(manifest.get("size_bytes") or 0),
        })
    return sorted(items, key=lambda item: item["deleted_at"], reverse=True)


def restore_memory_trash(trash_id: str) -> dict[str, Any]:
    """Restore an archived memory back to its original memories/ directory."""
    if not trash_id or Path(trash_id).name != trash_id:
        raise FileNotFoundError(trash_id)
    root = _memory_trash_root()
    group = (root / trash_id).resolve(strict=False)
    if not group.is_dir() or not _within(group, root):
        raise FileNotFoundError(trash_id)
    manifest = _read_memory_trash_manifest(group, root)
    if not manifest:
        raise ValueError("记忆回收站项目已损坏，无法恢复")
    name = str(manifest.get("name") or "")
    scope = str(manifest.get("scope") or "")
    if scope not in {"user", "workspace"} or not _NAME_RE.fullmatch(name):
        raise ValueError("记忆回收站项目无效")
    source_root = Path(str(manifest.get("source_root") or "")).resolve(strict=False)
    # Memory roots always end in a directory literally named "memory"
    # (data_path("memory")… / <workspace>/.zhixi/memory); this keeps a
    # tampered manifest from redirecting the restore anywhere else.
    if not source_root.is_dir() or source_root.name != "memory":
        raise ValueError("记忆回收站项目源目录无效")
    filename = f"{name}.md"
    source = (group / filename).resolve(strict=False)
    memories_dir = (source_root / "memories").resolve(strict=False)
    destination = (memories_dir / filename).resolve(strict=False)
    if not source.is_file() or not _within(source, group) or not _within(destination, source_root):
        raise ValueError("记忆回收站项目包含无效文件")
    if destination.exists():
        raise ValueError(f"无法恢复：{filename} 已存在")
    with _lock(source_root):
        memories_dir.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        (group / "manifest.json").unlink(missing_ok=True)
        group.rmdir()
        _rebuild_index(source_root, scope)
        _audit(source_root, "restore", {"name": name, "type": str(manifest.get("type") or "")}, "ui")
    summary = {"restored": [filename], "trash_id": trash_id, "scope": scope, "name": name}
    return summary


def reclaim_expired_memory_trash(*, retention_days: int = 30, now: datetime | None = None) -> dict[str, int]:
    """Permanently reclaim expired, manifest-backed memory trash groups only."""
    if retention_days < 0:
        raise ValueError("retention_days must be non-negative")
    # memory deleted_at timestamps are UTC-aware; compare with an aware now
    current = now or datetime.now(timezone.utc)
    root = _memory_trash_root()
    removed_groups = 0
    removed_files = 0
    freed_bytes = 0
    if not root.exists():
        return {"groups": 0, "files": 0, "bytes": 0}
    with _lock(root):
        for group in root.iterdir():
            manifest = _read_memory_trash_manifest(group, root)
            if not manifest:
                continue
            try:
                deleted_at = datetime.fromisoformat(str(manifest["deleted_at"]))
            except (ValueError, KeyError):
                continue
            if (current - deleted_at).total_seconds() < retention_days * 86400:
                continue
            name = str(manifest.get("name") or "")
            scope = str(manifest.get("scope") or "")
            source_root = Path(str(manifest.get("source_root") or "")).resolve(strict=False)
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
                log.warning("[memory] cannot reclaim trash group %s: %s", group.name, exc)
                continue
            removed_groups += 1
            removed_files += len(files)
            freed_bytes += group_bytes
            if scope in {"user", "workspace"} and source_root.name == "memory" and source_root.is_dir():
                _audit(source_root, "reclaim", {"name": name, "type": str(manifest.get("type") or "")}, "system")
    summary = {"groups": removed_groups, "files": removed_files, "bytes": freed_bytes}
    if removed_groups:
        log.info("[memory] reclaimed %d memory trash group(s), %d file(s), %d bytes",
                 removed_groups, removed_files, freed_bytes)
    return summary


def render_memory_section(*, user_id: str = "", workspace_id: str = "") -> str:
    records = list_records(user_id=user_id, workspace_id=workspace_id)
    if not records:
        return ""
    content = _render_index(records)
    return "\n\n[LONG-TERM MEMORY]\n" + content + "Use memory_read(name) only when the full record is needed.\n[END LONG-TERM MEMORY]"


def _write_record(root: Path, record: dict[str, Any]) -> None:
    metadata = {
        "name": record["name"], "type": record["type"], "title": record["title"],
        "created_at": record.get("created_at", ""), "updated_at": record.get("updated_at", ""),
        "last_seen_at": record.get("last_seen_at", ""), "source_session": record.get("source_session", ""),
    }
    optional = {"why": record.get("why", ""), "how_to_apply": record.get("how_to_apply", "")}
    metadata.update({key: value for key, value in optional.items() if value})
    document = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip() + "\n---\n" + record["body"].strip() + "\n"
    _write_atomic(_record_path(root, record["name"]), document)
