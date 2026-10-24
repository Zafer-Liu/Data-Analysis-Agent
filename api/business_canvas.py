"""Business canvas drawer API.

Canvas projects are owner-scoped: in cloud (multi-user) deployments every
project is tagged with the authenticated user's uid and all access is
filtered by it; in local single-user deployments the owner uid is "" and
everything is shared (unchanged behaviour).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent.tools.business.xml_utils import validate_and_fix_xml
from data.business_canvas_store import BusinessCanvasError, get_business_canvas_store
from data.workspace import workspace_manager
from .state import require_session_ownership

bp = Blueprint("business_canvas", __name__)


def _json_error(message: str, status: int = 400, code: str = "business_canvas_error"):
    return jsonify({"ok": False, "error": message, "code": code}), status


def _payload() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _workspace_id_for_session(session_id: str) -> str:
    runtime = workspace_manager.get(session_id)
    return runtime.workspace_id if runtime else ""


def _owner_uid() -> str:
    """Owner uid for scoping canvas projects ("" in local single-user mode)."""
    try:
        from api.auth import current_user

        user = current_user()
        return str(user["id"]) if user else ""
    except Exception:
        return ""


@bp.get("/api/session/<sid>/business-canvas/templates")
@require_session_ownership
def list_templates(sid: str):
    store = get_business_canvas_store()
    return jsonify({"ok": True, "templates": store.list_templates()})


@bp.delete("/api/session/<sid>/business-canvas/projects/<project_id>")
@require_session_ownership
def delete_project(sid: str, project_id: str):
    store = get_business_canvas_store()
    if not store.get_project(project_id, owner_uid=_owner_uid()):
        return _json_error("canvas project not found", 404, "not_found")
    try:
        store.delete_project(project_id, owner_uid=_owner_uid())
    except BusinessCanvasError as exc:
        return _json_error(str(exc), 400, "business_canvas_error")
    return jsonify({"ok": True})


@bp.get("/api/session/<sid>/business-canvas/projects")
@require_session_ownership
def list_projects(sid: str):
    store = get_business_canvas_store()
    try:
        limit = max(1, min(500, int(request.args.get("limit", "50"))))
    except ValueError:
        limit = 50
    return jsonify({
        "ok": True,
        "projects": store.list_projects(owner_uid=_owner_uid(), limit=limit),
    })


@bp.post("/api/session/<sid>/business-canvas/projects")
@require_session_ownership
def create_project(sid: str):
    body = _payload()
    store = get_business_canvas_store()
    try:
        project = store.create_project(
            session_id=sid,
            template_id=str(body.get("template_id") or ""),
            title=str(body.get("title") or ""),
            workspace_id=_workspace_id_for_session(sid),
            owner_uid=_owner_uid(),
        )
    except ValueError as exc:
        return _json_error(str(exc), 400, "invalid_request")
    except BusinessCanvasError as exc:
        return _json_error(str(exc), 400, "business_canvas_error")
    return jsonify({"ok": True, "project": project})


@bp.get("/api/session/<sid>/business-canvas/projects/<project_id>")
@require_session_ownership
def get_project(sid: str, project_id: str):
    store = get_business_canvas_store()
    project = store.get_project(project_id, owner_uid=_owner_uid())
    if not project:
        return _json_error("canvas project not found", 404, "not_found")
    return jsonify({"ok": True, "project": project})


@bp.patch("/api/session/<sid>/business-canvas/projects/<project_id>")
@require_session_ownership
def update_project(sid: str, project_id: str):
    body = _payload()
    store = get_business_canvas_store()
    if not store.get_project(project_id, owner_uid=_owner_uid()):
        return _json_error("canvas project not found", 404, "not_found")
    title = str(body.get("title") or "").strip()
    if not title:
        return _json_error("title is required", 400, "invalid_request")
    try:
        project = store.update_project_title(
            project_id=project_id, title=title, owner_uid=_owner_uid()
        )
    except BusinessCanvasError as exc:
        return _json_error(str(exc), 400, "business_canvas_error")
    return jsonify({"ok": True, "project": project})


@bp.get("/api/session/<sid>/business-canvas/projects/<project_id>/revisions")
@require_session_ownership
def list_revisions(sid: str, project_id: str):
    store = get_business_canvas_store()
    try:
        revisions = store.list_diagram_revisions(project_id, owner_uid=_owner_uid())
    except BusinessCanvasError as exc:
        text = str(exc)
        status = 404 if "not found" in text else 400
        return _json_error(text, status, "business_canvas_error")
    return jsonify({"ok": True, "revisions": revisions})


@bp.get("/api/session/<sid>/business-canvas/projects/<project_id>/diagram")
@require_session_ownership
def get_diagram_xml(sid: str, project_id: str):
    store = get_business_canvas_store()
    project = store.get_project(project_id, owner_uid=_owner_uid())
    if not project:
        return _json_error("canvas project not found", 404, "not_found")
    return jsonify({"ok": True, "diagram_xml": project.get("diagram_xml", "")})


@bp.patch("/api/session/<sid>/business-canvas/projects/<project_id>/diagram")
@require_session_ownership
def update_diagram_xml(sid: str, project_id: str):
    body = _payload()
    store = get_business_canvas_store()
    diagram_xml = str(body.get("diagram_xml") or "")
    # Validate XML before persisting — the same gate the agent tool path uses.
    validation = validate_and_fix_xml(diagram_xml) if diagram_xml.strip() else {"valid": True, "fixed": ""}
    if not validation["valid"]:
        return _json_error(
            f"Invalid diagram XML: {validation['error']}", 400, "invalid_xml"
        )
    diagram_xml = validation.get("fixed") or diagram_xml
    try:
        project = store.update_project_diagram_xml(
            project_id=project_id,
            diagram_xml=diagram_xml,
            actor_type=str(body.get("actor_type") or "user"),
            owner_uid=_owner_uid(),
        )
    except BusinessCanvasError as exc:
        text = str(exc)
        status = 404 if "not found" in text else 400
        return _json_error(text, status, "business_canvas_error")
    return jsonify({"ok": True, "project": project})
