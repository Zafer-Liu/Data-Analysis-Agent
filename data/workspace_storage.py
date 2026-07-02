#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Workspace DuckDB storage inspection and conservative cleanup.

D4 deliberately starts with the narrowest safe surface: inspect the persistent
Workspace database and registry, then clean only DB tables that are provably
stale because their registered source file is missing or changed. User files,
artifacts, saved sessions and dashboards are never deleted here.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from infrastructure.paths import data_path


@dataclass(frozen=True)
class WorkspaceStorageTarget:
    workspace_id: str
    root_path: Path
    db_path: Path
    registry_path: Path
    metadata_revision: int = 0
    lock: threading.RLock | None = None


def target_from_runtime(runtime: Any) -> WorkspaceStorageTarget:
    return WorkspaceStorageTarget(
        workspace_id=str(runtime.workspace_id),
        root_path=Path(runtime.workdir),
        db_path=Path(runtime.db_path),
        registry_path=Path(runtime.registry_path),
        metadata_revision=int(getattr(runtime, "metadata_revision", 0) or 0),
        lock=getattr(runtime, "db_lock", None),
    )


def target_from_metadata(metadata: Any) -> WorkspaceStorageTarget:
    root = Path(metadata.root_path)
    meta_dir = root / ".zhixi"
    return WorkspaceStorageTarget(
        workspace_id=str(metadata.workspace_id),
        root_path=root,
        db_path=meta_dir / "workspace.duckdb",
        registry_path=meta_dir / "registry.json",
        metadata_revision=int(getattr(metadata, "metadata_revision", 0) or 0),
    )


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.exists() and path.is_file() else 0
    except OSError:
        return 0


def _read_registry(path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if not path.exists():
        return {}, []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw, []
        return {}, [{"code": "registry_invalid", "message": "registry.json is not an object"}]
    except (json.JSONDecodeError, OSError) as exc:
        return {}, [{"code": "registry_unreadable", "message": str(exc)}]


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _hash_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _connect_readonly(db_path: Path):
    return duckdb.connect(str(db_path), read_only=True)


def _list_db_tables(db_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if not db_path.exists():
        return [], []
    conn = None
    try:
        conn = _connect_readonly(db_path)
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        ).fetchall()
        tables: list[dict[str, Any]] = []
        for (name,) in rows:
            columns = []
            row_count = None
            try:
                columns = [
                    {"name": col[0], "type": col[1]}
                    for col in conn.execute(f'DESCRIBE "{name}"').fetchall()
                ]
            except Exception:
                columns = []
            try:
                row_count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except Exception:
                row_count = None
            tables.append({
                "name": name,
                "row_count": row_count,
                "column_count": len(columns),
                "columns": columns[:20],
            })
        return tables, []
    except Exception as exc:
        return [], [{"code": "duckdb_unreadable", "message": str(exc)}]
    finally:
        if conn is not None:
            conn.close()


def _iter_json_files(root: Path, *, limit: int = 1000):
    if not root.is_dir():
        return
    count = 0
    try:
        for path in sorted(root.glob("*.json")):
            count += 1
            if count > limit:
                break
            yield path
    except OSError:
        return


def _text_mentions_table(text: str, table: str) -> bool:
    if not text or not table:
        return False
    pattern = re.compile(rf'(?<![\w])"?{re.escape(table)}"?(?![\w])', re.IGNORECASE)
    return bool(pattern.search(text))


def _json_mentions_table(value: Any, table: str) -> bool:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    return _text_mentions_table(text, table)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def _scan_cleanup_manifests(root_path: Path) -> list[dict[str, Any]]:
    cleanup_dir = root_path / ".zhixi" / "cleanup"
    manifests: list[dict[str, Any]] = []
    for path in _iter_json_files(cleanup_dir, limit=200) or []:
        data = _load_json(path)
        if not data:
            continue
        status = str(data.get("status") or "")
        if status == "succeeded":
            continue
        manifests.append({
            "cleanup_id": str(data.get("cleanup_id") or path.stem),
            "status": status or "unknown",
            "started_at": str(data.get("started_at") or ""),
            "finished_at": str(data.get("finished_at") or ""),
            "error": str(data.get("error") or ""),
            "path": str(path),
        })
    return manifests


def _workspace_relevant_saved_session(data: dict[str, Any], workspace_id: str) -> bool:
    workspace = data.get("workspace") or {}
    if isinstance(workspace, dict) and workspace.get("workspace_id") == workspace_id:
        return True
    recovery = data.get("recovery_state") or {}
    if isinstance(recovery, dict):
        for artifact in recovery.get("recent_artifacts") or []:
            if isinstance(artifact, dict) and artifact.get("workspace_id") == workspace_id:
                return True
    return False


def _collect_json_reference_index(
    *,
    workspace_id: str,
    table_names: set[str],
) -> tuple[dict[str, list[dict[str, str]]], list[dict[str, str]]]:
    references: dict[str, list[dict[str, str]]] = {name: [] for name in table_names}
    diagnostics: list[dict[str, str]] = []

    saved_dir = data_path("outputs", "Session")
    for path in _iter_json_files(saved_dir) or []:
        data = _load_json(path)
        if not data or not _workspace_relevant_saved_session(data, workspace_id):
            continue
        for table in table_names:
            if _json_mentions_table(data, table):
                references[table].append({
                    "kind": "saved_session",
                    "name": data.get("name") or path.stem,
                    "path": str(path),
                })

    dashboard_dir = data_path("outputs", "Dashboard")
    for path in _iter_json_files(dashboard_dir) or []:
        data = _load_json(path)
        if not data or str(data.get("workspace_id") or "") != workspace_id:
            continue
        for table in table_names:
            if _json_mentions_table(data, table):
                references[table].append({
                    "kind": "dashboard",
                    "name": data.get("name") or path.stem,
                    "path": str(path),
                })

    jobs_db = data_path("outputs", "jobs", "jobs.db")
    if jobs_db.is_file():
        conn = None
        try:
            conn = sqlite3.connect(str(jobs_db))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, type, label, result FROM jobs WHERE workspace_id = ? "
                "ORDER BY created_at DESC LIMIT 500",
                (workspace_id,),
            ).fetchall()
            for row in rows:
                payload = {
                    "id": row["id"],
                    "type": row["type"],
                    "label": row["label"],
                    "result": row["result"],
                }
                for table in table_names:
                    if _json_mentions_table(payload, table):
                        references[table].append({
                            "kind": "job_history",
                            "name": row["label"] or row["type"] or row["id"],
                            "path": str(jobs_db),
                        })
        except sqlite3.Error as exc:
            diagnostics.append({"code": "jobs_reference_scan_failed", "message": str(exc)})
        finally:
            if conn is not None:
                conn.close()

    return {key: value for key, value in references.items() if value}, diagnostics


def build_workspace_storage_plan(
    target: WorkspaceStorageTarget,
    *,
    active_lease_count: int = 0,
) -> dict[str, Any]:
    """Return a read-only storage report and conservative cleanup candidates."""
    registry, diagnostics = _read_registry(target.registry_path)
    db_tables, db_diags = _list_db_tables(target.db_path)
    diagnostics.extend(db_diags)

    table_names = {item["name"] for item in db_tables}
    references_by_table, reference_diags = _collect_json_reference_index(
        workspace_id=target.workspace_id,
        table_names=table_names,
    )
    diagnostics.extend(reference_diags)
    registered_tables: dict[str, str] = {}
    registry_entries = []
    candidates = []
    protected = []
    incomplete_manifests = _scan_cleanup_manifests(target.root_path)

    for key, entry in sorted(registry.items(), key=lambda pair: pair[0].lower()):
        if not isinstance(entry, dict):
            diagnostics.append({
                "code": "registry_entry_invalid",
                "key": key,
                "message": "registry entry is not an object",
            })
            continue
        tables = [str(value) for value in entry.get("tables") or [] if value]
        for table in tables:
            registered_tables[table] = key
        source_path = Path(str(entry.get("file_path") or target.root_path / key))
        if not source_path.is_absolute():
            source_path = target.root_path / source_path
        source_exists = source_path.exists()
        expected_hash = str(entry.get("sha256") or "")
        current_hash = _hash_file(source_path) if source_exists and expected_hash else ""
        hash_matches = bool(expected_hash and current_hash and expected_hash == current_hash)
        missing_tables = [table for table in tables if table not in table_names]
        stale = source_exists and expected_hash and current_hash and expected_hash != current_hash
        if not source_exists:
            status = "missing_source"
        elif stale:
            status = "stale_source"
        elif missing_tables:
            status = "registry_missing_table"
        else:
            status = "registered_ok"
        cleanup_allowed = status in {"missing_source", "stale_source"}
        table_references = {
            table: references_by_table.get(table, [])
            for table in tables if references_by_table.get(table)
        }
        if table_references:
            cleanup_allowed = False
        entry_payload = {
            "key": key,
            "status": status,
            "source_path": str(source_path),
            "source_exists": source_exists,
            "sha256": expected_hash,
            "current_sha256": current_hash,
            "hash_matches": hash_matches,
            "tables": tables,
            "missing_tables": missing_tables,
            "references": table_references,
            "cleanup_candidate": cleanup_allowed,
        }
        registry_entries.append(entry_payload)
        if cleanup_allowed:
            candidate_id = f"registry:{key}"
            candidates.append({
                "id": candidate_id,
                "kind": status,
                "registry_key": key,
                "drop_tables": [table for table in tables if table in table_names],
                "remove_registry_key": True,
                "estimated_reclaim_bytes": 0,
                "reason": (
                    "registered source file is missing"
                    if status == "missing_source"
                    else "registered source file changed since last parse"
                ),
            })
        elif status in {"missing_source", "stale_source"} and table_references:
            protected.append({
                "id": f"registry:{key}",
                "kind": "protected_reference",
                "reason": "registered stale tables are referenced by saved history",
                "references": table_references,
            })

    table_reports = []
    for table in db_tables:
        name = table["name"]
        registry_key = registered_tables.get(name, "")
        if registry_key:
            status = "registered_table"
        else:
            status = "derived_or_unregistered_table"
            protected.append({
                "id": f"table:{name}",
                "kind": status,
                "reason": (
                    "unregistered table is referenced by saved history"
                    if references_by_table.get(name)
                    else "unregistered tables may be user-created analysis tables"
                ),
                "references": references_by_table.get(name, []),
            })
        table_reports.append({
            **table,
            "status": status,
            "registry_key": registry_key,
            "references": references_by_table.get(name, []),
            "cleanup_candidate": False,
        })

    blockers = []
    if active_lease_count:
        blockers.append({
            "code": "workspace_leased",
            "message": "Workspace has active sessions or jobs; cleanup must wait.",
            "active_lease_count": active_lease_count,
        })

    return {
        "ok": True,
        "workspace_id": target.workspace_id,
        "root_path": str(target.root_path),
        "metadata_revision": target.metadata_revision,
        "generated_at": _now_iso(),
        "database": {
            "path": str(target.db_path),
            "exists": target.db_path.exists(),
            "size_bytes": _file_size(target.db_path),
            "table_count": len(db_tables),
        },
        "registry": {
            "path": str(target.registry_path),
            "exists": target.registry_path.exists(),
            "entry_count": len(registry_entries),
        },
        "registry_entries": registry_entries,
        "tables": table_reports,
        "cleanup_candidates": candidates,
        "protected": protected,
        "references": references_by_table,
        "incomplete_manifests": incomplete_manifests,
        "blockers": blockers,
        "can_execute": bool(candidates) and not blockers and target.db_path.exists(),
        "diagnostics": diagnostics,
        "action": "duckdb_drop_registered_stale_tables",
    }


def execute_workspace_storage_cleanup(
    target: WorkspaceStorageTarget,
    *,
    candidate_ids: list[str] | None = None,
    active_lease_count: int = 0,
) -> dict[str, Any]:
    """Execute selected safe cleanup candidates and write a manifest."""
    plan = build_workspace_storage_plan(target, active_lease_count=active_lease_count)
    if plan["blockers"]:
        return {**plan, "ok": False, "error": "workspace_cleanup_blocked"}
    candidates = plan["cleanup_candidates"]
    wanted = set(candidate_ids or [item["id"] for item in candidates])
    selected = [item for item in candidates if item["id"] in wanted]
    if not selected:
        return {
            "ok": True,
            "workspace_id": target.workspace_id,
            "cleanup_id": "",
            "executed": False,
            "message": "No cleanup candidates selected.",
            "plan": plan,
        }

    cleanup_id = f"cleanup_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    cleanup_dir = target.root_path / ".zhixi" / "cleanup"
    manifest_path = cleanup_dir / f"{cleanup_id}.json"
    manifest = {
        "cleanup_id": cleanup_id,
        "workspace_id": target.workspace_id,
        "metadata_revision": target.metadata_revision,
        "started_at": _now_iso(),
        "status": "started",
        "db_path": str(target.db_path),
        "registry_path": str(target.registry_path),
        "selected_candidates": selected,
        "operations": [],
    }
    _atomic_write_json(manifest_path, manifest)

    lock = target.lock or threading.RLock()
    conn = None
    try:
        with lock:
            registry, _diagnostics = _read_registry(target.registry_path)
            conn = duckdb.connect(str(target.db_path))
            conn.execute("BEGIN TRANSACTION")
            dropped_tables: list[str] = []
            removed_registry_keys: list[str] = []
            for candidate in selected:
                for table in candidate.get("drop_tables") or []:
                    conn.execute(f'DROP TABLE IF EXISTS "{table}"')
                    dropped_tables.append(table)
                    manifest["operations"].append({
                        "action": "drop_table",
                        "table": table,
                        "status": "done",
                    })
                key = str(candidate.get("registry_key") or "")
                if candidate.get("remove_registry_key") and key in registry:
                    registry.pop(key, None)
                    removed_registry_keys.append(key)
                    manifest["operations"].append({
                        "action": "remove_registry_key",
                        "registry_key": key,
                        "status": "done",
                    })
            conn.execute("COMMIT")
            conn.close()
            conn = None
            _atomic_write_json(target.registry_path, registry)
            manifest.update({
                "status": "succeeded",
                "finished_at": _now_iso(),
                "dropped_tables": dropped_tables,
                "removed_registry_keys": removed_registry_keys,
            })
            _atomic_write_json(manifest_path, manifest)
    except Exception as exc:
        if conn is not None:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
        manifest.update({
            "status": "failed",
            "finished_at": _now_iso(),
            "error": str(exc),
        })
        _atomic_write_json(manifest_path, manifest)
        raise

    after = build_workspace_storage_plan(target, active_lease_count=0)
    return {
        "ok": True,
        "workspace_id": target.workspace_id,
        "cleanup_id": cleanup_id,
        "manifest_path": str(manifest_path),
        "executed": True,
        "dropped_tables": manifest.get("dropped_tables", []),
        "removed_registry_keys": manifest.get("removed_registry_keys", []),
        "plan_after": after,
    }
