#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blueprint: workspace management — 挂载/卸载工作目录。

用户侧模型（与上传并行，非替代）：
  - 上传：保留现有 /api/session/<sid>/upload 流程
  - 工作目录：挂载本地项目文件夹后，**自动把目录内数据文件注册为 DataSource**，
    和上传走同一套逻辑。Agent 可直接 get_schema / query_data，无需特殊流程。

A4 修复（2026-06-18）：
  之前挂载只挂了路径，没创建 DataSource，导致 create_analysis_table /
  query_data 返回 "No data source connected"。现在挂载时自动遍历目录内
  数据文件（csv/xlsx/parquet/json），每个文件创建一个 DataSource 注册到
  session，行为和上传完全一致。
"""
import logging
import os
import traceback
from pathlib import Path
from flask import Blueprint, request, jsonify

from .state import session_manager, workspace_manager
from data.workspace import validate_workdir
from data.workspace_storage import (
    build_workspace_storage_plan,
    execute_workspace_storage_cleanup,
    target_from_metadata,
    target_from_runtime,
)

log = logging.getLogger(__name__)

bp = Blueprint("workspace", __name__)

# 可注册为 DataSource 的文件后缀
_REGISTERABLE_SUFFIXES = {".csv", ".xlsx", ".xls"}


def _active_workspace_jobs(sess, workspace_id: str) -> list[dict]:
    if not workspace_id:
        return []
    runner = getattr(sess, "_job_runner", None)
    if runner is None:
        return []
    return [
        job for job in runner.list_jobs(active_only=True)
        if job.get("workspace_id") == workspace_id
    ]


def _active_workspace_parent_jobs(sess, workspace_id: str) -> list[dict]:
    if not workspace_id:
        return []
    runner = getattr(sess, "_job_runner", None)
    if runner is None:
        return []
    return [
        job for job in runner.list_jobs(active_only=True, top_level_only=True)
        if job.get("workspace_id") == workspace_id
    ]


def _remove_workspace_sources(sid: str, runtime) -> int:
    """Close/remove only this session's sources for *runtime*."""
    sess = session_manager.get_or_create(sid)
    workdir_resolved = runtime.workdir.resolve()
    db_path_resolved = str(runtime.db_path.resolve())
    to_remove = []
    for entry in list(sess._sources):
        src = entry.get("source")
        db_p = getattr(src, "_db_path", None)
        if db_p is not None:
            try:
                if str(Path(db_p).resolve()) == db_path_resolved:
                    to_remove.append(entry["id"])
                    continue
            except (OSError, RuntimeError):
                pass
        fp = getattr(src, "file_path", None)
        if fp:
            try:
                Path(fp).resolve().relative_to(workdir_resolved)
                to_remove.append(entry["id"])
            except (OSError, RuntimeError, ValueError):
                pass
    for source_id in to_remove:
        sess.remove_source(source_id)
    return len(to_remove)


def _register_workdir_files(sid: str, runtime) -> dict:
    """把工作目录内的数据文件注册为**持久化** DataSource（A5+）。

    A5+ 核心改进：
      - 使用 WorkspacePersistentSource（持久化 DuckDB 连接到 .zhixi/workspace.duckdb）
      - 文件 sha256 记录在 registry.json，下次挂载时未变则跳过解析（大 Excel 秒开）
      - 关闭软件后 .duckdb 文件保留，下次挂载表已就绪
      - 新增/变更文件增量注册，已注册且未变的表直接复用

    返回 {"added": [...], "errors": [...], "skipped": int, "reused": int}。
    """
    from data.sources.workspace_persistent import WorkspacePersistentSource

    sess = session_manager.get_or_create(sid)
    added = []
    pending_jobs = []
    errors = []
    reused = 0

    # ── 1. 创建/打开持久化 DataSource ──────────────────────────────────────
    source_name = f"📁 {runtime.workdir.name}" if hasattr(runtime, 'workdir') else "工作目录"
    try:
        source = WorkspacePersistentSource(
            str(runtime.db_path), source_name, db_lock=runtime.db_lock,
        )
    except Exception as exc:
        log.error("[workspace] failed to open persistent source: %s", exc)
        return {"added": [], "errors": [f"持久化数据库打开失败：{exc}"], "skipped": 0, "reused": 0}

    # ── 2. 读取注册快照，检测文件变化 ──────────────────────────────────────
    registry = runtime.load_registry()
    files = runtime.list_data_files(max_files=50)

    for f in files:
        name = f["name"]
        suffix = f["suffix"]

        if suffix not in _REGISTERABLE_SUFFIXES:
            continue

        file_path = str(runtime.workdir / name)
        file_key = name  # registry 用文件名作 key（工作目录内唯一）

        # 算 sha256 检测变化
        current_hash = runtime.compute_file_hash(Path(file_path))
        if not current_hash:
            errors.append(f"{name}: 无法读取文件")
            continue

        old_entry = registry.get(file_key)
        if old_entry and old_entry.get("sha256") == current_hash:
            # 文件未变化，表已在 .duckdb 里，跳过解析
            reused += 1
            log.debug("[workspace] reuse cached: %s (sha256 match)", name)
            continue

        # 新文件或内容变化，注册到持久化连接
        base_table = _safe_table_name(name)
        try:
            if suffix in {".xlsx", ".xls"} and f.get("size", 0) > 5_000_000:
                from data.sources.workspace_persistent import parse_workspace_excel_job
                job_id = sess.job_runner.create(
                    lambda ctx, fp=file_path, table=base_table, key=file_key,
                           digest=current_hash, previous=(old_entry or {}).get("tables", []):
                        parse_workspace_excel_job(
                            ctx, ctx.runtime or runtime, fp, table, key, digest, previous
                        ),
                    job_type="excel_parse",
                    label=name,
                )
                pending_jobs.append({
                    "id": job_id,
                    "type": "excel_parse",
                    "source_name": name,
                    "status": "queued",
                })
                continue
            if suffix == ".csv":
                with runtime.db_lock:
                    ok = source._register_csv(file_path, base_table)
                if ok:
                    tables = [base_table]
                else:
                    errors.append(f"{name}: CSV 注册失败")
                    continue
            else:
                with runtime.db_lock:
                    tables = source._register_excel(file_path, base_table)
                if not tables:
                    errors.append(f"{name}: Excel 无可注册的 sheet")
                    continue

            # 更新 registry
            entry = {
                "sha256": current_hash,
                "tables": tables,
                "source_type": suffix.lstrip("."),
                "file_path": file_path,
            }
            # Merge and persist under the same writer lock.  A large Excel job
            # may complete while this request is still walking later files;
            # writing the original snapshot at function exit would lose it.
            with runtime.db_lock:
                registry = runtime.load_registry()
                registry[file_key] = entry
                runtime.save_registry(registry)
            added.append({
                "source_name": name,
                "tables": tables,
            })
            log.info("[workspace] registered %s → tables=%s", name, tables)
        except Exception as exc:
            log.error("[workspace] FAILED to register %s: %s\n%s",
                      name, exc, traceback.format_exc())
            errors.append(f"{name}: {exc}")

    # ── 3. 把持久化 source 注册到 session ───────────────────────────────────
    # 检查是否已注册过这个持久化 source（按 db_path 去重）
    already_registered = any(
        getattr(entry.get("source"), "_db_path", None) == runtime.db_path
        for entry in sess._sources
    )
    if not already_registered:
        source_id = sess.add_source(source)
        log.info("[workspace] persistent source registered  sid=%s  source_id=%s  tables=%s",
                 sid, source_id, source.list_tables())

    # 获取 schema 给前端
    try:
        schema_preview = source.get_schema()[:500]
    except Exception:
        schema_preview = ""

    return {
        "added": added,
        "errors": errors,
        "skipped": len(files) - len(added) - len(errors) - reused,
        "reused": reused,
        "schema_preview": schema_preview,
        "source_name": source_name,
        "pending_jobs": pending_jobs,
    }


def _safe_table_name(filename: str) -> str:
    """从文件名生成合法的 DuckDB 表名。"""
    import re
    # 去扩展名
    stem = re.sub(r'\.(csv|xlsx|xls)$', '', filename, flags=re.IGNORECASE)
    # 清理为合法标识符
    cleaned = re.sub(r'[^\w]', '_', stem)
    cleaned = re.sub(r'_+', '_', cleaned).strip('_')
    if cleaned and cleaned[0].isdigit():
        cleaned = '_' + cleaned
    return cleaned or 'data'


@bp.post("/api/session/<sid>/workspace/mount")
def mount_workspace(sid: str):
    """挂载工作目录并自动注册目录内数据文件为 DataSource。

    Body: {"path": "C:/Users/xxx/projects/财务分析"}
    返回: {
        "ok": true,
        "workspace": {...},
        "added": [{source_id, source_name, schema_preview}, ...],
        "errors": [...],
        "sources": sess.list_sources()
    }
    或 {"ok": false, "error": "..."}
    """
    session = session_manager.get(sid)
    if not session:
        return jsonify({"ok": False, "error": "会话不存在。"}), 404

    body = request.get_json(silent=True) or {}
    path = (body.get("path") or "").strip()
    permission = (body.get("permission") or "read_only").strip()
    expected_workspace_id = str(body.get("expected_workspace_id") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "缺少 path 参数。"}), 400

    if permission not in {"read_only", "read_write"}:
        return jsonify({"ok": False, "error": "permission 必须是 read_only 或 read_write。"}), 400

    if expected_workspace_id:
        expected = workspace_manager.metadata_store.find(expected_workspace_id)
        if expected is None:
            return jsonify({
                "ok": False,
                "error": "目标工作目录已失效，请刷新列表后重试。",
                "code": "workspace_identity_unavailable",
            }), 409
        try:
            expected_root = Path(expected.root_path).resolve(strict=True)
            requested_root = Path(path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            return jsonify({
                "ok": False,
                "error": "目标工作目录路径已不可用，请刷新列表后重试。",
                "code": "workspace_path_unavailable",
            }), 409
        if expected_root != requested_root:
            return jsonify({
                "ok": False,
                "error": "目标工作目录身份与路径不匹配，请刷新列表后重试。",
                "code": "workspace_identity_mismatch",
            }), 409

    previous_runtime = workspace_manager.get(sid)
    active_jobs = []
    if previous_runtime is not None:
        try:
            target_changed = Path(path).expanduser().resolve(strict=False) != previous_runtime.workdir
        except (OSError, RuntimeError):
            target_changed = True
        current_requested = workspace_manager.status(sid).get(
            "requested_permission", previous_runtime.permission,
        )
        permission_changed = permission != current_requested
        active_jobs = _active_workspace_jobs(session, previous_runtime.workspace_id)
        if active_jobs and permission_changed and not target_changed:
            return jsonify({
                "ok": False,
                "error": "当前工作区仍有任务执行中，请等待完成或取消任务后再修改权限。",
                "active_job_ids": [job["id"] for job in active_jobs],
                "workspace_id": previous_runtime.workspace_id,
            }), 409

    # Explicit API mounts are always remembered. Internal production mounts use
    # the manager default (also remembered); tests opt out with remember=False.
    ok, msg, runtime = workspace_manager.mount(
        sid, path, permission=permission, remember=True,
    )
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400

    if previous_runtime is not None and previous_runtime is not runtime:
        removed = _remove_workspace_sources(sid, previous_runtime)
        log.info("[api] workspace switched sid=%s old_sources_removed=%d", sid, removed)

    log.info("[api] workspace mounted  sid=%s  path=%s", sid, runtime.workdir)

    # 自动注册目录内数据文件为持久化 DataSource
    reg = _register_workdir_files(sid, runtime)

    sess = session_manager.get_or_create(sid)
    sess.workspace_id = runtime.workspace_id
    continued_workspace = None
    if previous_runtime is not None and previous_runtime is not runtime and active_jobs:
        continued_workspace = {
            "workspace_id": previous_runtime.workspace_id,
            "workdir": str(previous_runtime.workdir),
            "active_job_ids": [job["id"] for job in active_jobs],
            "active_job_count": len(active_jobs),
        }
    return jsonify({
        "ok": True,
        "workspace": runtime.to_dict(),
        "added": reg["added"],
        "errors": reg["errors"],
        "reused": reg.get("reused", 0),
        "schema_preview": reg.get("schema_preview", ""),
        "source_name": reg.get("source_name", ""),
        "pending_jobs": reg.get("pending_jobs", []),
        "sources": sess.list_sources(),
        "continued_workspace": continued_workspace,
    })


@bp.post("/api/session/<sid>/workspace/jobs/<jid>/finalize")
def finalize_workspace_job(sid: str, jid: str):
    """Validate a completed workspace Excel job and expose its committed tables."""
    sess = session_manager.get_or_create(sid)
    job = sess.job_runner.get_status(jid)
    if job is None or job.get("type") != "excel_parse":
        return jsonify({"error": "工作目录 Excel 任务不存在"}), 404
    if job.get("status") != "succeeded":
        return jsonify({"error": "任务尚未完成", "status": job.get("status")}), 409
    runtime = workspace_manager.get(sid)
    result = job.get("result") or {}
    if runtime is None or result.get("workspace_id") != runtime.workspace_id \
            or result.get("workdir") != str(runtime.workdir):
        return jsonify({"error": "任务所属工作目录已变化，结果未挂载"}), 409

    source = next(
        (entry.get("source") for entry in sess._sources
         if getattr(entry.get("source"), "_db_path", None) == runtime.db_path),
        None,
    )
    if source is None:
        return jsonify({"error": "工作目录数据源已卸载"}), 409
    schema = source.get_schema()
    return jsonify({
        "ok": True,
        "added": [{"source_name": result.get("source_name"), "tables": result.get("tables", [])}],
        "sources": sess.list_sources(),
        "source_name": source.name,
        "schema_preview": schema,
    })


@bp.post("/api/session/<sid>/workspace/unmount")
def unmount_workspace(sid: str):
    """卸载工作目录，并移除由工作目录注册的 DataSource。"""
    session = session_manager.get(sid)
    if not session:
        return jsonify({"ok": False, "error": "会话不存在。"}), 404

    runtime = workspace_manager.get(sid)
    active_jobs = (
        _active_workspace_jobs(session, runtime.workspace_id) if runtime else []
    )
    removed = workspace_manager.unmount(sid)
    if not removed:
        return jsonify({"ok": False, "error": "未挂载工作目录。"}), 400

    if runtime:
        removed_count = _remove_workspace_sources(sid, runtime)
        log.info("[api] workspace unmounted sid=%s sources_removed=%d", sid, removed_count)
    session.workspace_id = ""

    continued_workspace = None
    if runtime is not None and active_jobs:
        continued_workspace = {
            "workspace_id": runtime.workspace_id,
            "workdir": str(runtime.workdir),
            "active_job_ids": [job["id"] for job in active_jobs],
            "active_job_count": len(active_jobs),
        }
    return jsonify({
        "ok": True,
        "sources": session_manager.get_or_create(sid).list_sources(),
        "continued_workspace": continued_workspace,
    })


@bp.get("/api/session/<sid>/workspace")
def get_workspace(sid: str):
    """查询当前工作目录挂载状态。"""
    session = session_manager.get(sid)
    if not session:
        return jsonify({"ok": False, "error": "会话不存在。"}), 404

    return jsonify({"ok": True, "workspace": workspace_manager.status(sid)})


@bp.get("/api/session/<sid>/workspaces")
def list_workspaces(sid: str):
    """List known Workspace identities for the C4 management panel."""
    session = session_manager.get(sid)
    if not session:
        return jsonify({"ok": False, "error": "会话不存在。"}), 404
    records = workspace_manager.list_known(sid)
    active_counts: dict[str, int] = {}
    runner = getattr(session, "_job_runner", None)
    if runner is not None:
        for job in runner.list_jobs(active_only=True, top_level_only=True):
            workspace_id = str(job.get("workspace_id") or "")
            if workspace_id:
                active_counts[workspace_id] = active_counts.get(workspace_id, 0) + 1
    for record in records:
        record["active_job_count"] = active_counts.get(record["workspace_id"], 0)
    return jsonify({
        "ok": True,
        "workspaces": records,
    })


def _bounded_directory_stats(path: Path, limit: int = 10_000) -> dict:
    """Return a bounded, symlink-safe summary for removal preflight."""
    files = 0
    total_bytes = 0
    scanned = 0
    truncated = False
    if not path.is_dir():
        return {"file_count": 0, "total_bytes": 0, "truncated": False}
    try:
        for current, dirs, names in os.walk(path, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
            for name in names:
                scanned += 1
                if scanned > limit:
                    truncated = True
                    break
                candidate = Path(current) / name
                try:
                    if not candidate.is_symlink() and candidate.is_file():
                        files += 1
                        total_bytes += candidate.stat().st_size
                except OSError:
                    continue
            if truncated:
                break
    except OSError:
        pass
    return {"file_count": files, "total_bytes": total_bytes, "truncated": truncated}


def _removal_preview(sid: str, workspace_id: str) -> tuple[dict | None, int]:
    records = workspace_manager.list_known(sid)
    record = next(
        (item for item in records if item.get("workspace_id") == workspace_id),
        None,
    )
    if record is None:
        return {"ok": False, "error": "工作目录不在发现列表中。",
                "code": "workspace_not_found"}, 404
    blockers = []
    if record.get("connected_session_count"):
        blockers.append({
            "code": "workspace_connected",
            "message": "工作目录仍有会话连接，请先卸载。",
        })
    if record.get("active_lease_count"):
        blockers.append({
            "code": "workspace_leased",
            "message": "工作目录仍有任务执行，请等待完成或取消任务。",
        })
    root = Path(str(record.get("root_path") or ""))
    return {
        "ok": True,
        "workspace": record,
        "can_remove": not blockers,
        "blockers": blockers,
        "action": "hide_discovery_record",
        "preserved": {
            "root_path": str(root),
            "physical_directory": True,
            "authoritative_metadata": True,
            "stable_identity_lookup": True,
            "artifacts": _bounded_directory_stats(root / "artifacts"),
            "cache": _bounded_directory_stats(root / ".baa_cache"),
        },
    }, 200


def _storage_target_and_leases(sid: str, workspace_id: str):
    # Active runtimes are authoritative even when the mount is intentionally
    # absent from user-facing recent-workspace discovery (internal/job mounts).
    runtime = workspace_manager.get_by_workspace(workspace_id)
    if runtime is not None:
        return (
            target_from_runtime(runtime),
            int(runtime.job_ref_count),
            None,
            200,
        )
    records = workspace_manager.list_known(sid)
    record = next(
        (item for item in records if item.get("workspace_id") == workspace_id),
        None,
    )
    if record is None:
        return None, 0, {
            "ok": False,
            "error": "工作目录不在发现列表中。",
            "code": "workspace_not_found",
        }, 404
    metadata = workspace_manager.metadata_store.find(workspace_id)
    if metadata is None:
        return None, 0, {
            "ok": False,
            "error": "工作目录身份已失效，请刷新列表后重试。",
            "code": "workspace_identity_unavailable",
        }, 409
    target = target_from_metadata(metadata)
    active_lease_count = int(record.get("active_lease_count") or 0)
    return target, active_lease_count, None, 200


@bp.get("/api/session/<sid>/workspaces/<workspace_id>/remove-preview")
def preview_workspace_removal(sid: str, workspace_id: str):
    """Preview a discovery-only removal; never modify the Workspace."""
    if not session_manager.get(sid):
        return jsonify({"ok": False, "error": "会话不存在。"}), 404
    payload, status = _removal_preview(sid, workspace_id)
    return jsonify(payload), status


@bp.delete("/api/session/<sid>/workspaces/<workspace_id>")
def remove_workspace_record(sid: str, workspace_id: str):
    """Hide a safe Workspace discovery record without deleting physical data."""
    if not session_manager.get(sid):
        return jsonify({"ok": False, "error": "会话不存在。"}), 404
    body = request.get_json(silent=True) or {}
    if body.get("confirmed") is not True:
        return jsonify({
            "ok": False,
            "error": "移除前必须明确确认。",
            "code": "confirmation_required",
        }), 400
    preview, status = _removal_preview(sid, workspace_id)
    if status != 200:
        return jsonify(preview), status
    if not preview["can_remove"]:
        return jsonify(preview), 409
    ok, message, _entry = workspace_manager.forget(workspace_id)
    if not ok:
        return jsonify({
            "ok": False,
            "error": message,
            "code": "workspace_remove_blocked",
        }), 409
    return jsonify({
        "ok": True,
        "removed_workspace_id": workspace_id,
        "files_deleted": 0,
        "directory_deleted": False,
        "metadata_deleted": False,
        "stable_identity_lookup_preserved": True,
    })


@bp.get("/api/session/<sid>/workspaces/<workspace_id>/storage-cleanup-preview")
def preview_workspace_storage_cleanup(sid: str, workspace_id: str):
    """Read-only D4 storage inspection and cleanup dry-run."""
    if not session_manager.get(sid):
        return jsonify({"ok": False, "error": "会话不存在。"}), 404
    target, active_lease_count, error, status = _storage_target_and_leases(
        sid, workspace_id,
    )
    if error is not None:
        return jsonify(error), status
    payload = build_workspace_storage_plan(
        target, active_lease_count=active_lease_count,
    )
    return jsonify(payload)


@bp.post("/api/session/<sid>/workspaces/<workspace_id>/storage-cleanup")
def run_workspace_storage_cleanup(sid: str, workspace_id: str):
    """Execute confirmed D4 cleanup candidates with a manifest."""
    if not session_manager.get(sid):
        return jsonify({"ok": False, "error": "会话不存在。"}), 404
    body = request.get_json(silent=True) or {}
    if body.get("confirmed") is not True:
        return jsonify({
            "ok": False,
            "error": "清理前必须明确确认。",
            "code": "confirmation_required",
        }), 400
    candidate_ids = body.get("candidate_ids")
    if candidate_ids is not None and not isinstance(candidate_ids, list):
        return jsonify({
            "ok": False,
            "error": "candidate_ids 必须是数组。",
            "code": "invalid_candidate_ids",
        }), 400
    target, active_lease_count, error, status = _storage_target_and_leases(
        sid, workspace_id,
    )
    if error is not None:
        return jsonify(error), status
    try:
        payload = execute_workspace_storage_cleanup(
            target,
            candidate_ids=[str(value) for value in candidate_ids] if candidate_ids else None,
            active_lease_count=active_lease_count,
        )
    except Exception as exc:
        log.exception("[api] workspace storage cleanup failed workspace=%s", workspace_id)
        return jsonify({
            "ok": False,
            "error": str(exc),
            "code": "workspace_storage_cleanup_failed",
        }), 500
    if not payload.get("ok") and payload.get("error") == "workspace_cleanup_blocked":
        return jsonify(payload), 409
    return jsonify(payload)


@bp.get("/api/session/<sid>/workspaces/<workspace_id>/switch-preview")
def preview_workspace_switch(sid: str, workspace_id: str):
    """Preflight a stable-identity Workspace switch without changing state."""
    session = session_manager.get(sid)
    if not session:
        return jsonify({"ok": False, "error": "会话不存在。"}), 404
    metadata = workspace_manager.metadata_store.find(workspace_id)
    if metadata is None:
        return jsonify({
            "ok": False,
            "error": "目标工作目录已失效，请刷新列表后重试。",
            "code": "workspace_identity_unavailable",
        }), 409
    ok, message, root = validate_workdir(metadata.root_path)
    if not ok or root is None:
        return jsonify({
            "ok": False,
            "error": message or "目标工作目录不可用。",
            "code": "workspace_path_unavailable",
        }), 409
    current = workspace_manager.get(sid)
    target_runtime = workspace_manager.get_by_workspace(metadata.workspace_id)
    current_id = current.workspace_id if current else ""
    active_jobs = _active_workspace_parent_jobs(session, current_id)
    return jsonify({
        "ok": True,
        "target": {
            "workspace_id": metadata.workspace_id,
            "name": metadata.name,
            "root_path": str(root),
            "permission": metadata.permission,
            "effective_permission": (
                target_runtime.permission if target_runtime else metadata.permission
            ),
        },
        "current": ({
            "workspace_id": current.workspace_id,
            "name": current.workdir.name,
            "root_path": str(current.workdir),
        } if current else None),
        "already_current": current_id == metadata.workspace_id,
        "requires_confirmation": bool(current and current_id != metadata.workspace_id),
        "continuing_job_ids": [job["id"] for job in active_jobs],
        "continuing_job_count": len(active_jobs),
    })


@bp.patch("/api/session/<sid>/workspaces/<workspace_id>")
def rename_workspace(sid: str, workspace_id: str):
    """Rename a Workspace display label without moving its directory."""
    if not session_manager.get(sid):
        return jsonify({"ok": False, "error": "会话不存在。"}), 404
    body = request.get_json(silent=True) or {}
    raw_name = str(body.get("name") or "")
    name = " ".join(raw_name.split())
    if not name or len(name) > 80 or any(ord(char) < 32 for char in raw_name):
        return jsonify({
            "ok": False,
            "error": "工作目录显示名称长度必须为 1-80 个字符，且不能包含控制字符。",
            "code": "invalid_workspace_name",
        }), 400
    try:
        metadata = workspace_manager.rename(workspace_id, name)
    except (OSError, RuntimeError) as exc:
        return jsonify({
            "ok": False,
            "error": str(exc),
            "code": "workspace_rename_failed",
        }), 409
    item = next(
        (record for record in workspace_manager.list_known(sid)
         if record.get("workspace_id") == workspace_id),
        None,
    )
    return jsonify({
        "ok": True,
        "workspace": item or metadata.to_dict(),
    })


@bp.get("/api/session/<sid>/workspace/checkpoints")
def list_workspace_checkpoints(sid: str):
    runtime = workspace_manager.get(sid)
    if runtime is None:
        return jsonify({"ok": False, "error": "请先连接工作目录。"}), 409
    from filehistory import FileHistory
    return jsonify({
        "ok": True,
        "workspace": runtime.to_dict(),
        "snapshots": FileHistory(runtime, sid).list_snapshots(),
    })


@bp.post("/api/session/<sid>/workspace/checkpoints/<snapshot_id>/restore")
def restore_workspace_checkpoint(sid: str, snapshot_id: str):
    sess = session_manager.get_or_create(sid)
    runtime = workspace_manager.get(sid)
    if runtime is None:
        return jsonify({"ok": False, "error": "请先连接工作目录。"}), 409
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:
        return jsonify({"ok": False, "error": "恢复前必须明确确认。"}), 400
    mode = str(body.get("mode") or "code_and_conversation")
    if mode not in {"code_and_conversation", "conversation_only", "code_only"}:
        return jsonify({"ok": False, "error": "不支持的回退模式。"}), 400
    if mode != "conversation_only" and runtime.permission != "read_write":
        return jsonify({"ok": False, "error": "恢复文件需要“可读和编辑”权限。"}), 403
    from filehistory import FileHistory
    snapshots = FileHistory(runtime, sid).list_snapshots()
    target = next((item for item in snapshots if item.get("id") == snapshot_id), None)
    if target is None:
        return jsonify({"ok": False, "error": "快照不存在。"}), 404
    active_jobs = _active_workspace_jobs(sess, runtime.workspace_id)
    if active_jobs:
        return jsonify({
            "ok": False,
            "error": "当前工作目录仍有任务执行中，请等待完成或取消后再回退。",
            "active_job_ids": [job["id"] for job in active_jobs],
        }), 409

    def worker(ctx):
        from filehistory import FileHistory
        ctx.set_progress(10, "正在回退到历史快照")
        result = FileHistory(runtime, sid).rewind(snapshot_id, mode, sess)
        if mode != "code_only":
            from api.saved_sessions import sync_autosave_after_rewind
            sync_autosave_after_rewind(sess)
        if mode != "conversation_only":
            ctx.set_progress(75, "正在刷新工作目录数据")
            sess._combined_schema_cache = None
            result["refresh"] = _register_workdir_files(sid, runtime)
        ctx.set_progress(100, "时光回退完成")
        return result

    try:
        job_id = sess.job_runner.create(
            worker, "filehistory_rewind", label=f"回退：{target.get('user_text', '')[:48]}",
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "job_id": job_id}), 202


@bp.post("/api/session/<sid>/workspace/permission")
def update_workspace_permission(sid: str):
    """Update the mounted workspace's interactive file permission."""
    session = session_manager.get(sid)
    if not session:
        return jsonify({"ok": False, "error": "会话不存在。"}), 404
    runtime = workspace_manager.get(sid)
    if runtime is None:
        return jsonify({"ok": False, "error": "未挂载工作目录。"}), 400
    body = request.get_json(silent=True) or {}
    permission = (body.get("permission") or "").strip()
    if permission not in {"read_only", "read_write"}:
        return jsonify({"ok": False, "error": "permission 必须是 read_only 或 read_write。"}), 400
    active_jobs = _active_workspace_jobs(session, runtime.workspace_id)
    if active_jobs:
        return jsonify({
            "ok": False,
            "error": "当前工作区仍有任务执行中，请等待完成或取消任务后再修改权限。",
            "active_job_ids": [job["id"] for job in active_jobs],
        }), 409
    ok, message, runtime = workspace_manager.update_permission(sid, permission)
    if not ok or runtime is None:
        return jsonify({"ok": False, "error": message}), 400
    log.info("[api] workspace permission updated sid=%s permission=%s", sid, permission)
    return jsonify({"ok": True, "workspace": runtime.to_dict()})
