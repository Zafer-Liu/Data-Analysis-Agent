"""Blueprint: workspace-scoped workflow drafts, profiles, and publication."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent.workflows.models import WorkflowContractError, WorkflowErrorCode
from agent.workflows.runtime import workflow_runtime_manager
from agent.workflows.service import WorkflowService
from .state import require_session_ownership


bp = Blueprint("workflows", __name__)


def _error_status(error: WorkflowContractError) -> int:
    if error.code is WorkflowErrorCode.RESOURCE_NOT_FOUND:
        return 404
    if error.code is WorkflowErrorCode.PERMISSION_DENIED:
        return 403
    if error.code in {
        WorkflowErrorCode.VERSION_CONFLICT,
        WorkflowErrorCode.IDEMPOTENCY_CONFLICT,
        WorkflowErrorCode.APPROVAL_ALREADY_DECIDED,
    }:
        return 409
    return 400


def _error_response(error: WorkflowContractError):
    return jsonify({
        "ok": False,
        "error": str(error),
        "code": error.code.value,
    }), _error_status(error)


def _body() -> dict:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


@bp.get("/api/session/<sid>/agent-profiles")
@require_session_ownership
def list_agent_profiles(sid: str):
    try:
        with WorkflowService.for_session(sid) as service:
            profiles = service.list_agent_profiles()
    except WorkflowContractError as exc:
        return _error_response(exc)
    return jsonify({"ok": True, "profiles": profiles})


@bp.post("/api/session/<sid>/agent-profiles")
@require_session_ownership
def create_agent_profile(sid: str):
    body = _body()
    try:
        with WorkflowService.for_session(sid) as service:
            profile = service.create_agent_profile(
                key=body.get("key", ""),
                name=body.get("name", ""),
                role=body.get("role", ""),
                instructions=body.get("instructions", ""),
                allowed_tools=body.get("allowed_tools", []),
                model_policy=body.get("model_policy", "inherit"),
                created_by=body.get("created_by", ""),
            )
    except WorkflowContractError as exc:
        return _error_response(exc)
    return jsonify({"ok": True, "profile": profile}), 201


@bp.get("/api/session/<sid>/workflows")
@require_session_ownership
def list_workflows(sid: str):
    try:
        with WorkflowService.for_session(sid) as service:
            workflows = service.list_workflows()
            for workflow in workflows:
                version_id = str(workflow.get("current_version_id") or "")
                workflow["current_version"] = (
                    service.store.get_version(version_id) if version_id else None
                )
    except WorkflowContractError as exc:
        return _error_response(exc)
    return jsonify({"ok": True, "workflows": workflows})


@bp.post("/api/session/<sid>/workflows")
@require_session_ownership
def create_workflow(sid: str):
    body = _body()
    try:
        with WorkflowService.for_session(sid) as service:
            workflow = service.create_workflow(
                name=body.get("name", ""),
                description=body.get("description", ""),
                graph=body.get("graph", {}),
                input_schema=body.get("input_schema", {}),
                output_schema=body.get("output_schema", {}),
                created_by=body.get("created_by", ""),
            )
    except WorkflowContractError as exc:
        return _error_response(exc)
    return jsonify({"ok": True, "workflow": workflow}), 201


@bp.get("/api/session/<sid>/workflows/<workflow_id>")
@require_session_ownership
def get_workflow(sid: str, workflow_id: str):
    try:
        with WorkflowService.for_session(sid) as service:
            workflow = service.get_workflow(workflow_id)
    except WorkflowContractError as exc:
        return _error_response(exc)
    return jsonify({"ok": True, "workflow": workflow})


@bp.delete("/api/session/<sid>/workflows/<workflow_id>")
@require_session_ownership
def delete_workflow(sid: str, workflow_id: str):
    try:
        result = workflow_runtime_manager.get(sid).delete_workflow(workflow_id)
    except WorkflowContractError as exc:
        return _error_response(exc)
    return jsonify({"ok": True, **result})


@bp.put("/api/session/<sid>/workflows/<workflow_id>/draft")
@require_session_ownership
def update_workflow_draft(sid: str, workflow_id: str):
    body = _body()
    try:
        with WorkflowService.for_session(sid) as service:
            workflow = service.update_draft(
                workflow_id,
                graph=body.get("graph", {}),
                input_schema=body.get("input_schema"),
                output_schema=body.get("output_schema"),
                name=body.get("name"),
                description=body.get("description"),
                expected_revision=body.get("expected_revision"),
            )
    except WorkflowContractError as exc:
        return _error_response(exc)
    return jsonify({"ok": True, "workflow": workflow})


@bp.post("/api/session/<sid>/workflows/<workflow_id>/validate")
@require_session_ownership
def validate_workflow(sid: str, workflow_id: str):
    try:
        with WorkflowService.for_session(sid) as service:
            validation = service.validate_draft(workflow_id)
    except WorkflowContractError as exc:
        return _error_response(exc)
    return jsonify({"ok": True, "validation": validation})


@bp.post("/api/session/<sid>/workflows/<workflow_id>/publish")
@require_session_ownership
def publish_workflow(sid: str, workflow_id: str):
    body = _body()
    try:
        with WorkflowService.for_session(sid) as service:
            result = service.publish(
                workflow_id,
                published_by=body.get("published_by", ""),
            )
    except WorkflowContractError as exc:
        return _error_response(exc)
    return jsonify({"ok": True, **result})
