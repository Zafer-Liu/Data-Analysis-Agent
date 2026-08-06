"""API endpoints for local artifact lifecycle visibility and cleanup."""
from flask import Blueprint, jsonify, request

from data.workspace import workspace_manager

from infrastructure.artifact_lifecycle import (
    artifact_cleanup_preview,
    lifecycle_audit,
    lifecycle_report,
    load_lifecycle_settings,
    list_artifact_trash,
    list_session_trash,
    list_upload_trash,
    reclaim_expired_artifact_trash,
    reclaim_expired_session_trash,
    reclaim_expired_upload_trash,
    recycle_registered_artifact,
    recycle_unregistered_artifact,
    registered_artifact_reference_preview,
    restore_artifact_trash,
    save_lifecycle_settings,
    uploads_storage_preview,
    recycle_upload_file,
    restore_upload_trash,
    workspace_storage_preview,
    restore_session_trash,
)

bp = Blueprint("lifecycle", __name__)


@bp.before_request
def _check_auth():
    _require_cloud_auth()


def _require_cloud_auth():
    """Require an authenticated user in cloud-managed deployments.

    In desktop mode (no cloud env) this is a no-op so local users can still
    manage their own storage without a login step.
    """
    from .auth import is_cloud_managed, current_user
    if not is_cloud_managed():
        return  # desktop — no auth required
    user = current_user()
    if not user:
        from flask import abort
        abort(401)


def _retention_days() -> int:
    raw = (request.json or {}).get("retention_days", 30)
    try:
        days = int(raw)
    except (TypeError, ValueError):
        raise ValueError("保留天数必须是非负整数")
    if not 0 <= days <= 3650:
        raise ValueError("保留天数必须在 0 到 3650 之间")
    return days


@bp.get("/api/lifecycle/audit")
def get_lifecycle_audit():
    raw_limit = request.args.get("limit", "50")
    try:
        limit = int(raw_limit)
    except ValueError:
        return jsonify({"ok": False, "error": "limit 必须是整数"}), 400
    return jsonify({"ok": True, "items": lifecycle_audit(limit)})

@bp.get("/api/lifecycle/report")
def get_report():
    return jsonify({"ok": True, "report": lifecycle_report()})


@bp.get("/api/lifecycle/settings")
def get_lifecycle_settings():
    return jsonify({"ok": True, "settings": load_lifecycle_settings()})


@bp.put("/api/lifecycle/settings")
def update_lifecycle_settings():
    try:
        settings = save_lifecycle_settings(request.json or {})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "settings": settings})


@bp.get("/api/lifecycle/workspaces/preview")
def get_workspace_storage_preview():
    return jsonify({"ok": True, "preview": workspace_storage_preview(workspace_manager.list_known())})

@bp.get("/api/lifecycle/artifacts/preview")
def get_artifact_cleanup_preview():
    return jsonify({"ok": True, "preview": artifact_cleanup_preview()})


@bp.get("/api/lifecycle/uploads/preview")
def get_uploads_storage_preview():
    return jsonify({"ok": True, "preview": uploads_storage_preview()})


@bp.post("/api/lifecycle/uploads/recycle")
def recycle_upload():
    payload = request.json or {}
    try:
        summary = recycle_upload_file(
            str(payload.get("category") or ""),
            str(payload.get("relative_path") or ""),
        )
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "上传文件不存在或已被处理"}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": summary})


@bp.get("/api/lifecycle/upload-trash")
def get_upload_trash():
    return jsonify({"ok": True, "items": list_upload_trash()})


@bp.post("/api/lifecycle/upload-trash/reclaim")
def reclaim_upload_trash():
    try:
        days = _retention_days()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": reclaim_expired_upload_trash(retention_days=days)})


@bp.post("/api/lifecycle/upload-trash/<trash_id>/restore")
def restore_upload(trash_id: str):
    try:
        summary = restore_upload_trash(trash_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "上传回收站项目不存在"}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": summary})


@bp.get("/api/lifecycle/artifacts/references/preview")
def get_artifact_reference_preview():
    return jsonify({"ok": True, "preview": registered_artifact_reference_preview()})


@bp.post("/api/lifecycle/artifacts/unregistered/recycle")
def recycle_legacy_artifact():
    payload = request.json or {}
    try:
        summary = recycle_unregistered_artifact(
            str(payload.get("type") or ""),
            str(payload.get("relative_path") or ""),
        )
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "历史产物不存在或已被处理"}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": summary})


@bp.post("/api/lifecycle/artifacts/registered/recycle")
def recycle_registered_artifact_endpoint():
    payload = request.json or {}
    try:
        summary = recycle_registered_artifact(str(payload.get("artifact_id") or ""))
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "已登记产物不存在或已被处理"}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": summary})


@bp.get("/api/lifecycle/artifact-trash")
def get_artifact_trash():
    return jsonify({"ok": True, "items": list_artifact_trash()})


@bp.post("/api/lifecycle/artifact-trash/reclaim")
def reclaim_artifact_trash():
    try:
        days = _retention_days()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": reclaim_expired_artifact_trash(retention_days=days)})


@bp.post("/api/lifecycle/artifact-trash/<trash_id>/restore")
def restore_artifact(trash_id: str):
    try:
        summary = restore_artifact_trash(trash_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "产物回收站项目不存在"}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": summary})


@bp.get("/api/lifecycle/session-trash")
def get_session_trash():
    return jsonify({"ok": True, "items": list_session_trash()})


@bp.post("/api/lifecycle/session-trash/reclaim")
def reclaim_session_trash():
    try:
        days = _retention_days()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": reclaim_expired_session_trash(retention_days=days)})


@bp.post("/api/lifecycle/session-trash/<trash_id>/restore")
def restore_session(trash_id: str):
    try:
        summary = restore_session_trash(trash_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "回收站项目不存在"}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": summary})
