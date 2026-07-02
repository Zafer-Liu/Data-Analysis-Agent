"""Blueprint: save / load / delete persistent sessions."""
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from flask import Blueprint, request, jsonify

from .state import session_manager, config_manager
from data.connector import ExcelDataSource, CSVDataSource
from agent.reasoning import split_reasoning_tags
from infrastructure.paths import data_path

log = logging.getLogger(__name__)

bp = Blueprint("saved_sessions", __name__)

SAVE_DIR = data_path("outputs", "Session")

SAVE_DIR.mkdir(parents=True, exist_ok=True)


def _visible_msg_count(history: list) -> int:
    """Count only user + assistant-with-text messages (exclude tool calls/results).

    sess.history now contains tool call chains (role=assistant with tool_calls,
    role=tool), but the user-facing "条数" should reflect actual conversation
    exchanges, not internal tool round-trips.
    """
    return sum(
        1 for m in history
        if m.get("role") in ("user", "assistant")
        and m.get("content")                       # has visible text content
        and not m.get("tool_calls")                # not an intermediate tool-call entry
    )


def _collect_chart_ids(history: list) -> list[str]:
    """Gather every chart_id referenced across the conversation history.

    Chart HTML itself is already written through to disk by _ChartStore
    (outputs/charts/<cid>.html), so it survives restarts on its own — we only
    need the id list (e.g. to keep sess.chart_ids in sync for export).
    """
    ids: list[str] = []
    for msg in history:
        for cid in (msg.get("chart_ids") or []):
            if cid not in ids:
                ids.append(cid)
    return ids


def _normalize_reasoning_history(history: list) -> list:
    """Migrate legacy assistant messages that stored ``<think>`` in content."""
    for msg in history:
        if msg.get("role") != "assistant" or not msg.get("content"):
            continue
        visible, embedded = split_reasoning_tags(msg["content"])
        if embedded:
            msg["content"] = visible
            prior = (msg.get("reasoning") or "").strip()
            msg["reasoning"] = "\n\n".join(x for x in (prior, embedded) if x)
    return history


# ── helpers ────────────────────────────────────────────────────────────────

def _safe_stem(name: str) -> str:
    """Turn an arbitrary name into a filesystem-safe stem (keep CJK)."""
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return name or "session"


def _ds_info(sess) -> dict | None:
    """Serialize data source metadata for JSON storage."""
    ds = sess.data_source
    if ds is None:
        return None
    info: dict = {"display_name": ds.name, "ds_type": type(ds).__name__}
    if isinstance(ds, (ExcelDataSource, CSVDataSource)):
        info["file_path"] = ds.file_path
    return info


def _workspace_info(sid: str) -> dict | None:
    from data.workspace import workspace_manager
    runtime = workspace_manager.get(sid)
    if runtime is None:
        return None
    return {
        "workdir": str(runtime.workdir),
        "permission": runtime.permission,
        "workspace_id": runtime.workspace_id,
    }


def _recovery_state(sess) -> dict:
    return {
        "recent_sql": list(getattr(sess, "recent_sql", []))[-5:],
        "recent_artifacts": list(getattr(sess, "recent_artifacts", []))[-20:],
        "active_sources": [
            item for item in sess.list_sources() if item.get("active")
        ],
        "turn_activations": list(getattr(sess, "turn_activations", []))[-100:],
        "discovered_tools": list(getattr(sess, "discovered_tools", []))[-100:],
        "discovered_mcp_tools": list(
            getattr(sess, "discovered_mcp_tools", [])
        )[-10:],
        "mcp_tool_last_used": dict(
            getattr(sess, "mcp_tool_last_used", {}) or {}
        ),
        "mcp_catalog_version": str(
            getattr(sess, "mcp_catalog_version", "") or ""
        ),
        "compaction_state": dict(getattr(sess, "compaction_state", {}) or {}),
        "usage_breakdowns": list(getattr(sess, "usage_breakdowns", []))[-100:],
        "total_cached_input_tokens": int(
            getattr(sess, "total_cached_input_tokens", 0) or 0
        ),
        "total_cache_write_tokens": int(
            getattr(sess, "total_cache_write_tokens", 0) or 0
        ),
    }


def sync_autosave_after_rewind(sess) -> None:
    """Replace stale future autosave content after conversation time travel."""
    path = SAVE_DIR / f"autosave_{sess.session_id}.json"
    if not sess.history:
        path.unlink(missing_ok=True)
        return
    payload = {
        "name": f"自动保存_{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "autosave": True,
        "session_id": sess.session_id,
        "model_provider": sess.model_provider,
        "history": sess.history,
        "total_input_tokens": sess.total_input_tokens,
        "total_output_tokens": sess.total_output_tokens,
        "data_source": _ds_info(sess),
        "workspace": _workspace_info(sess.session_id),
        "recovery_state": _recovery_state(sess),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _restore_ds(info: dict):
    """Re-instantiate a data source from saved metadata.

    Returns None when the source cannot be genuinely restored — the original
    file is missing, construction fails, or the rebuilt source has no usable
    table. The caller maps None → ds_lost so the frontend never shows a
    misleading "connected" state for a source that is not actually usable.
    """
    if not info:
        return None
    fp = info.get("file_path", "")
    if not fp or not Path(fp).exists():
        return None
    display = info.get("display_name", Path(fp).name)
    ext = Path(fp).suffix.lower()
    try:
        if info.get("ds_type") == "CSVDataSource" or ext == ".csv":
            ds = CSVDataSource(fp, display)
        else:
            ds = ExcelDataSource(fp, display)
    except Exception:
        return None
    # Verify the rebuilt source actually has at least one queryable table —
    # a file can exist on disk yet be empty/corrupt.
    try:
        if not ds.list_tables():
            return None
    except Exception:
        return None
    return ds


def _list_files() -> list[dict]:
    # Include both manual saves and autosave files; autosaves are flagged is_autosave=True
    files = sorted(SAVE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    result = []
    for f in files:
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
            is_autosave = bool(meta.get("autosave")) or f.stem.startswith("autosave_")
            result.append({
                "filename":    f.name,
                "name":        meta.get("name", f.stem),
                "saved_at":    meta.get("saved_at", ""),
                "msg_count":   _visible_msg_count(meta.get("history", [])),
                "ds_name":     (meta.get("data_source") or {}).get("display_name", ""),
                "is_autosave": is_autosave,
                "session_id":  meta.get("session_id", ""),
            })
        except Exception:
            continue
    return result


def _session_file(filename: str) -> Path:
    """Resolve a saved-session filename inside SAVE_DIR."""
    return SAVE_DIR / Path(filename).name


# ── API endpoints ──────────────────────────────────────────────────────────

@bp.get("/api/saved-sessions")
def list_sessions():
    return jsonify(_list_files())


@bp.post("/api/session/<sid>/autosave")
def autosave_session(sid: str):
    """Silent auto-save — overwrites a single per-session autosave file.

    Unlike /save, this never returns an error for empty history (just skips)
    and uses a fixed filename so old autosaves are replaced rather than
    accumulated.
    """
    sess = session_manager.get(sid)
    if not sess or not sess.history:
        return jsonify({"ok": False, "reason": "empty"})

    body = request.json or {}
    req_name     = body.get("name", "").strip()
    target_file  = body.get("target_file", "").strip()   # filename to overwrite when loaded from a save

    ts_label  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    auto_name = req_name if req_name else f"自动保存_{ts_label}"

    payload = {
        "name":               auto_name,
        "saved_at":           datetime.now().isoformat(timespec="seconds"),
        "autosave":           True,
        "session_id":         sid,
        "model_provider":     sess.model_provider,
        "history":            sess.history,
        "total_input_tokens": sess.total_input_tokens,
        "total_output_tokens":sess.total_output_tokens,
        "data_source":        _ds_info(sess),
        "workspace":          _workspace_info(sid),
        "recovery_state":     _recovery_state(sess),
    }

    # If the user loaded from an existing file, overwrite that file directly
    # so no new entry appears in the list.
    # Otherwise fall back to the per-session autosave file.
    if target_file:
        safe = Path(target_file).name          # strip any path traversal
        path = SAVE_DIR / safe
        if not path.exists():                  # guard: don't create arbitrary files
            path = SAVE_DIR / f"autosave_{sid}.json"
    else:
        path = SAVE_DIR / f"autosave_{sid}.json"

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.debug("[session] autosave  sid=%s  file=%s  msg_count=%d",
              sid, path.name, _visible_msg_count(sess.history))
    return jsonify({"ok": True, "saved_at": payload["saved_at"], "filename": path.name})


@bp.get("/api/session/<sid>/autosave")
def get_autosave(sid: str):
    """Check whether an autosave exists for this session."""
    path = SAVE_DIR / f"autosave_{sid}.json"
    if not path.exists():
        return jsonify({"exists": False})
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
        return jsonify({
            "exists":    True,
            "saved_at":  meta.get("saved_at", ""),
            "msg_count": _visible_msg_count(meta.get("history", [])),
            "filename":  path.name,
        })
    except Exception:
        return jsonify({"exists": False})


@bp.post("/api/session/<sid>/save")
def save_session(sid: str):
    sess = session_manager.get(sid)
    if not sess:
        log.warning("[session] save  sid=%s  error=session not found", sid)
        return jsonify({"error": "会话不存在"}), 404
    if not sess.history:
        return jsonify({"error": "对话为空，无需保存"}), 400

    name = (request.json or {}).get("name", "").strip()
    if not name:
        name = datetime.now().strftime("对话_%Y%m%d_%H%M%S")

    payload = {
        "name":               name,
        "saved_at":           datetime.now().isoformat(timespec="seconds"),
        "session_id":         sid,
        "model_provider":     sess.model_provider,
        "history":            sess.history,
        "total_input_tokens": sess.total_input_tokens,
        "total_output_tokens":sess.total_output_tokens,
        "data_source":        _ds_info(sess),
        "workspace":          _workspace_info(sid),
        "recovery_state":     _recovery_state(sess),
    }

    stem = _safe_stem(name)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SAVE_DIR / f"{stem}_{ts}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("[session] saved  sid=%s  name=%r  file=%s  msg_count=%d",
             sid, name, path.name, _visible_msg_count(sess.history))
    return jsonify({"ok": True, "filename": path.name, "name": name})


@bp.post("/api/session/<sid>/load")
def load_session(sid: str):
    filename = (request.json or {}).get("filename", "").strip()
    if not filename:
        return jsonify({"error": "未指定文件名"}), 400

    path = _session_file(filename)
    if not path.exists() or path.suffix != ".json":
        return jsonify({"error": "文件不存在"}), 404

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return jsonify({"error": f"读取失败: {exc}"}), 500

    sess = session_manager.get_or_create(sid)
    sess.history              = _normalize_reasoning_history(data.get("history", []))
    keep_provider = (request.json or {}).get("keep_provider", False)
    if not keep_provider:
        sess.model_provider = data.get("model_provider", "")
    sess.total_input_tokens   = data.get("total_input_tokens", 0)
    sess.total_output_tokens  = data.get("total_output_tokens", 0)
    sess.last_prompt_tokens   = 0
    sess.last_reasoning = next(
        (m.get("reasoning", "") for m in reversed(sess.history)
         if m.get("role") == "assistant" and m.get("reasoning")),
        "",
    )

    sess.chart_ids = _collect_chart_ids(sess.history)

    recovery = data.get("recovery_state") or {}
    sess.usage_breakdowns = [
        item for item in recovery.get("usage_breakdowns", []) if isinstance(item, dict)
    ][-100:]
    sess.total_cached_input_tokens = int(
        recovery.get("total_cached_input_tokens") or 0
    )
    sess.total_cache_write_tokens = int(
        recovery.get("total_cache_write_tokens") or 0
    )
    sess.recent_sql = [str(item)[:4000] for item in recovery.get("recent_sql", [])][-5:]
    sess.recent_artifacts = []
    for artifact in recovery.get("recent_artifacts", [])[-20:]:
        if not isinstance(artifact, dict):
            continue
        item = dict(artifact)
        if item.get("artifact_id"):
            item["url"] = f"/api/session/{sid}/tool-results/{item['artifact_id']}"
        sess.recent_artifacts.append(item)
    sess.turn_activations = [
        dict(item) for item in recovery.get("turn_activations", [])[-100:]
        if isinstance(item, dict)
    ]
    sess.discovered_tools = [
        str(item) for item in recovery.get("discovered_tools", [])[-100:]
        if str(item or "").strip()
    ]
    sess.discovered_mcp_tools = [
        str(item) for item in recovery.get("discovered_mcp_tools", [])
        if str(item).startswith("mcp__")
    ][-10:]
    sess.mcp_tool_last_used = {
        str(name): float(value or 0)
        for name, value in dict(recovery.get("mcp_tool_last_used") or {}).items()
        if str(name) in sess.discovered_mcp_tools
    }
    sess.mcp_catalog_version = str(recovery.get("mcp_catalog_version") or "")
    sess.compaction_state = dict(recovery.get("compaction_state") or {
        "consecutive_failures": 0,
        "last_failure_type": "",
        "circuit_open": False,
        "last_attempt_at": 0.0,
        "last_success_at": 0.0,
    })

    # Restore the workspace first. Its persistent DuckDB and cache contain the
    # active tables and B6 result artifacts referenced by the saved session.
    from data.workspace import workspace_manager
    workspace_info = data.get("workspace") or {}
    workspace_restored = False
    workspace_lost = False
    workspace_identity_mismatch = False
    sess.data_source = None
    sess.workspace_id = ""
    if workspace_info.get("workdir"):
        ok, _message, runtime = workspace_manager.mount(
            sid,
            str(workspace_info.get("workdir")),
            permission=str(workspace_info.get("permission") or "read_only"),
            remember=True,
        )
        if ok and runtime is not None:
            saved_workspace_id = str(workspace_info.get("workspace_id") or "")
            saved_session_id = str(data.get("session_id") or "")
            # Before C0 workspace_id was simply session_id (also a UUID), so
            # that exact legacy value is a compatibility hint, not identity.
            is_legacy_identity = bool(
                saved_workspace_id and saved_workspace_id == saved_session_id
            )
            if (
                saved_workspace_id
                and not is_legacy_identity
                and saved_workspace_id != runtime.workspace_id
            ):
                workspace_manager.unmount(sid)
                workspace_lost = True
                workspace_identity_mismatch = True
            else:
                sess.workspace_id = runtime.workspace_id
                from api.workspace import _register_workdir_files
                reg = _register_workdir_files(sid, runtime)
                workspace_restored = not bool(reg.get("errors")) or bool(reg.get("reused"))
        else:
            workspace_lost = True

    ds_info = data.get("data_source")
    ds = sess.data_source if workspace_restored else _restore_ds(ds_info)
    if not workspace_restored:
        sess.data_source = ds

    ds_status = "connected" if ds else ("lost" if ds_info or workspace_lost else "none")
    log.info("[session] loaded  sid=%s  file=%s  name=%r  msg_count=%d  ds=%s  "
             "in_tokens=%d  out_tokens=%d",
             sid, filename, data.get("name", ""), _visible_msg_count(sess.history),
             ds_status, sess.total_input_tokens, sess.total_output_tokens)

    return jsonify({
        "ok":              True,
        "name":            data.get("name", ""),
        "history":         sess.history,
        "model_provider":  sess.model_provider,   # 实际生效的模型（keep_provider 时为原值）
        "saved_provider":  data.get("model_provider", ""),  # 存档中记录的模型（仅供参考）
        "total_input":     sess.total_input_tokens,
        "total_output":    sess.total_output_tokens,
        "ds_connected":    ds is not None,
        "ds_name":         ds.name if ds else (ds_info or {}).get("display_name", ""),
        "ds_lost":         ds is None and ds_info is not None,
        "workspace_restored": workspace_restored,
        "workspace_lost": workspace_lost,
        "workspace_identity_mismatch": workspace_identity_mismatch,
    })


@bp.post("/api/saved-sessions/<filename>/rename")
@bp.patch("/api/saved-sessions/<filename>")
def rename_session(filename: str):
    """Rename a saved conversation by updating its display name in metadata."""
    path = _session_file(filename)
    if not path.exists() or path.suffix != ".json":
        return jsonify({"error": "文件不存在"}), 404

    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"error": "名称不能为空"}), 400

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["name"] = name
        data["renamed_at"] = datetime.now().isoformat(timespec="seconds")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        return jsonify({"error": f"重命名失败: {exc}"}), 500

    log.info("[session] renamed  file=%s  name=%r", path.name, name)
    return jsonify({"ok": True, "filename": path.name, "name": name})


@bp.delete("/api/saved-sessions/<filename>")
def delete_session(filename: str):
    path = _session_file(filename)
    if not path.exists() or path.suffix != ".json":
        return jsonify({"error": "文件不存在"}), 404
    path.unlink()
    # Chart HTML in outputs/charts/ is intentionally NOT deleted — it is shared
    # storage and may still be referenced by other saved conversations.
    return jsonify({"ok": True})
