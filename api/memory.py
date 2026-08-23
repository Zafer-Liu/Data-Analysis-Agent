# -*- coding: utf-8 -*-
"""Blueprint: scoped long-term memory CRUD (user + workspace records)."""
import logging
from typing import Any

from flask import Blueprint, request, jsonify

from .state import session_manager
from data import memory_store

log = logging.getLogger(__name__)
bp = Blueprint("memory", __name__)


def _scope_context() -> tuple[str, str, dict[str, Any]]:
    """Resolve trusted user ID, workspace ID, and the validated JSON body."""
    body: dict[str, Any] = request.get_json(silent=True) if request.is_json else {}
    sid = str(
        request.args.get("session_id")
        or request.form.get("session_id")
        or body.get("session_id")
        or ""
    )
    user_id = str(
        request.headers.get("X-BAA-User-ID")
        or request.args.get("user_id")
        or body.get("user_id")
        or "local-default"
    ).strip()[:200]
    from data.workspace import workspace_manager
    workspace_id = str(workspace_manager.workspace_id_for_session(sid) or "")
    return user_id, workspace_id, body


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    """Strip internals that the UI has no reason to see."""
    return {
        "name": record.get("name", ""),
        "type": record.get("type", ""),
        "scope": record.get("scope", ""),
        "title": record.get("title", ""),
        "body": record.get("body", ""),
        "why": record.get("why", ""),
        "how_to_apply": record.get("how_to_apply", ""),
        "created_at": record.get("created_at", ""),
        "updated_at": record.get("updated_at", ""),
    }


@bp.get("/api/memory")
def list_memory():
    user_id, workspace_id, _body = _scope_context()
    records = memory_store.list_records(
        user_id=user_id, workspace_id=workspace_id,
    )
    return jsonify({
        "records": [_public_record(r) for r in records],
        "workspace_mounted": bool(workspace_id),
    })


@bp.get("/api/memory-notices")
def memory_notices():
    """Drain pending 'remembered' toasts produced by background extraction."""
    from agent.memory import pop_notices

    sid = str(request.args.get("session_id") or "").strip()[:200]
    if not sid:
        return jsonify({"notices": []})
    return jsonify({"notices": pop_notices(sid)})


@bp.get("/api/memory-activity")
def memory_activity():
    user_id, _workspace_id, _body = _scope_context()
    sid = str(request.args.get("session_id") or "").strip()[:200]
    return jsonify({"activity": memory_store.list_extraction_activity(user_id=user_id, session_id=sid)})


@bp.get("/api/memory/<name>")
def get_memory(name: str):
    user_id, workspace_id, _body = _scope_context()
    record = memory_store.get_record(
        name, user_id=user_id, workspace_id=workspace_id,
    )
    if not record:
        return jsonify({"error": "记忆不存在或不属于当前作用域"}), 404
    return jsonify({"record": _public_record(record)})


@bp.post("/api/memory")
def create_memory():
    user_id, workspace_id, body = _scope_context()
    payload = {key: body.get(key) for key in ("name", "type", "title", "body", "why", "how_to_apply")}
    try:
        saved = memory_store.create_record(
            payload, user_id=user_id, workspace_id=workspace_id,
            actor="ui", automatic=False,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        log.exception("[memory] create failed")
        return jsonify({"error": f"保存失败：{exc}"}), 500
    return jsonify({"record": _public_record(saved)}), 201


@bp.put("/api/memory/<name>")
def update_memory(name: str):
    user_id, workspace_id, body = _scope_context()
    payload = {key: body.get(key) for key in ("type", "title", "body", "why", "how_to_apply")}
    try:
        saved = memory_store.update_record(
            name, payload, user_id=user_id, workspace_id=workspace_id,
            actor="ui", automatic=False,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        log.exception("[memory] update failed")
        return jsonify({"error": f"保存失败：{exc}"}), 500
    if not saved:
        return jsonify({"error": "记忆不存在或不属于当前作用域"}), 404
    return jsonify({"record": _public_record(saved)})


@bp.delete("/api/memory/<name>")
def archive_memory(name: str):
    user_id, workspace_id, body = _scope_context()
    confirm = bool(body.get("confirm"))
    if not confirm:
        return jsonify({"error": "归档需要确认"}), 400
    try:
        ok = memory_store.archive_record(
            name, user_id=user_id, workspace_id=workspace_id, actor="ui",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        log.exception("[memory] archive failed")
        return jsonify({"error": f"归档失败：{exc}"}), 500
    if not ok:
        return jsonify({"error": "记忆不存在或不属于当前作用域"}), 404
    return jsonify({"ok": True})
