"""Blueprint: start, inspect, replay, and cancel Workflow Runs."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent.workflows.models import WorkflowContractError, WorkflowErrorCode
from agent.workflows.runtime import workflow_runtime_manager
from .state import require_session_ownership


bp = Blueprint("workflow_runs", __name__)


def _error(error: WorkflowContractError):
    status = 404 if error.code is WorkflowErrorCode.RESOURCE_NOT_FOUND else 400
    if error.code is WorkflowErrorCode.PERMISSION_DENIED:
        status = 403
    if error.code in {
        WorkflowErrorCode.APPROVAL_ALREADY_DECIDED,
        WorkflowErrorCode.VERSION_CONFLICT,
    }:
        status = 409
    return jsonify({
        "ok": False,
        "code": error.code.value,
        "error": str(error),
    }), status


@bp.post("/api/session/<sid>/workflow-runs")
@require_session_ownership
def start_workflow_run(sid: str):
    body = request.get_json(silent=True) or {}
    try:
        runtime = workflow_runtime_manager.get(sid)
        detail = runtime.scheduler.start(
            workflow_version_id=str(body.get("workflow_version_id") or ""),
            session_id=sid,
            inputs=body.get("inputs") if isinstance(body.get("inputs"), dict) else {},
            started_by=str(body.get("started_by") or ""),
        )
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, **detail}), 202


@bp.get("/api/session/<sid>/workflow-runs")
@require_session_ownership
def list_workflow_runs(sid: str):
    try:
        runtime = workflow_runtime_manager.get(sid)
        runs = runtime.run_store.list_runs(session_id=sid)
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, "runs": runs})


@bp.get("/api/session/<sid>/workflow-metrics")
@require_session_ownership
def get_workflow_metrics(sid: str):
    from agent.workflows.metrics import (
        workflow_metrics,
        workflow_optimization_suggestions,
    )

    try:
        metrics = workflow_metrics(
            workflow_runtime_manager.get(sid),
            workflow_version_id=str(
                request.args.get("workflow_version_id") or ""
            ),
        )
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({
        "ok": True,
        "metrics": metrics,
        "suggestions": workflow_optimization_suggestions(metrics),
    })


@bp.post(
    "/api/session/<sid>/workflow-optimization-suggestions/<suggestion_id>/draft"
)
def create_workflow_optimization_draft(sid: str, suggestion_id: str):
    from agent.workflows.metrics import create_suggestion_draft

    body = request.get_json(silent=True) or {}
    try:
        workflow = create_suggestion_draft(
            workflow_runtime_manager.get(sid),
            suggestion_id,
            created_by=str(body.get("created_by") or ""),
        )
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, "workflow": workflow}), 201


@bp.delete("/api/session/<sid>/workflow-runs/<run_id>")
@require_session_ownership
def delete_workflow_run(sid: str, run_id: str):
    try:
        result = workflow_runtime_manager.get(sid).delete_run(run_id)
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, **result})


@bp.get("/api/session/<sid>/workflow-runs/<run_id>")
@require_session_ownership
def get_workflow_run(sid: str, run_id: str):
    try:
        detail = workflow_runtime_manager.get(sid).scheduler.advance(run_id)
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, **detail})


@bp.get("/api/session/<sid>/workflow-runs/<run_id>/events")
@require_session_ownership
def get_workflow_run_events(sid: str, run_id: str):
    after = request.args.get("after_sequence", "0")
    try:
        after_sequence = max(0, int(after))
    except ValueError:
        after_sequence = 0
    try:
        runtime = workflow_runtime_manager.get(sid)
        if runtime.run_store.get_run(run_id) is None:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                f"workflow run not found: {run_id}",
            )
        events = runtime.run_store.list_events(run_id, after_sequence)
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, "events": events})


@bp.get("/api/session/<sid>/workflow-runs/<run_id>/artifacts/<artifact_id>")
@require_session_ownership
def get_workflow_artifact_content(sid: str, run_id: str, artifact_id: str):
    """Read the complete immutable Artifact body for progressive disclosure."""
    try:
        runtime = workflow_runtime_manager.get(sid)
        if runtime.run_store.get_run(run_id) is None:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                f"workflow run not found: {run_id}",
            )
        found = any(
            str(item.get("artifact_id") or "") == artifact_id
            for manifest in runtime.run_store.list_manifests(run_id)
            for item in manifest.get("items", [])
        )
        if not found:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                f"workflow artifact not found: {artifact_id}",
            )
        content = runtime.run_store.get_artifact_content(artifact_id)
        if content is None:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                "complete artifact content is unavailable for this legacy run",
            )
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, "artifact_id": artifact_id, "content": content})


@bp.get("/api/session/<sid>/workflow-runs/<run_id>/approvals")
@require_session_ownership
def list_workflow_run_approvals(sid: str, run_id: str):
    try:
        runtime = workflow_runtime_manager.get(sid)
        if runtime.run_store.get_run(run_id) is None:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                f"workflow run not found: {run_id}",
            )
        approvals = runtime.run_store.list_approvals(run_id)
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, "approvals": approvals})


@bp.post("/api/session/<sid>/workflow-runs/<run_id>/approvals/<approval_id>/decide")
@require_session_ownership
def decide_workflow_run_approval(sid: str, run_id: str, approval_id: str):
    body = request.get_json(silent=True) or {}
    try:
        detail = workflow_runtime_manager.get(sid).scheduler.decide_approval(
            run_id,
            approval_id,
            decision=str(body.get("decision") or ""),
            decided_by=str(body.get("decided_by") or ""),
            comment=str(body.get("comment") or ""),
            comments=(
                body.get("comments")
                if isinstance(body.get("comments"), dict) else None
            ),
            revised_outputs=(
                body.get("revised_outputs")
                if isinstance(body.get("revised_outputs"), dict) else None
            ),
            revised_summary=str(body.get("revised_summary") or ""),
        )
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, **detail})


@bp.post("/api/session/<sid>/workflow-runs/<run_id>/cancel")
@require_session_ownership
def cancel_workflow_run(sid: str, run_id: str):
    try:
        detail = workflow_runtime_manager.get(sid).scheduler.cancel(run_id)
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, **detail})
@bp.post("/api/session/<sid>/workflow-runs/<run_id>/resume")
@require_session_ownership
def resume_workflow_run(sid: str, run_id: str):
    try:
        detail = workflow_runtime_manager.get(sid).scheduler.resume(run_id)
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, **detail})


@bp.post("/api/session/<sid>/workflow-runs/<run_id>/nodes/<node_run_id>/retry")
@require_session_ownership
def retry_workflow_node(sid: str, run_id: str, node_run_id: str):
    try:
        detail = workflow_runtime_manager.get(sid).scheduler.retry_node(
            run_id, node_run_id
        )
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, **detail})


@bp.post("/api/session/<sid>/workflow-runs/<run_id>/fork")
@require_session_ownership
def fork_workflow_run_from_checkpoint(sid: str, run_id: str):
    body = request.get_json(silent=True) or {}
    try:
        detail = workflow_runtime_manager.get(sid).scheduler.fork_from_checkpoint(
            run_id,
            str(body.get("checkpoint_node_run_id") or ""),
            session_id=sid,
            started_by=str(body.get("started_by") or ""),
        )
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, **detail}), 202

@bp.get("/api/session/<sid>/workflow-templates")
@require_session_ownership
def list_workflow_templates(sid: str):
    try:
        templates = workflow_runtime_manager.get(sid).run_store.list_run_templates()
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, "templates": templates})


@bp.post("/api/session/<sid>/workflow-runs/<run_id>/template")
@require_session_ownership
def create_workflow_run_template(sid: str, run_id: str):
    from agent.workflows.knowledge import mark_run_template

    body = request.get_json(silent=True) or {}
    try:
        template = mark_run_template(
            workflow_runtime_manager.get(sid),
            run_id,
            name=str(body.get("name") or ""),
            description=str(body.get("description") or ""),
            created_by=str(body.get("created_by") or ""),
        )
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, "template": template}), 201


@bp.get("/api/session/<sid>/workflow-knowledge-candidates")
@require_session_ownership
def list_workflow_knowledge_candidates(sid: str):
    try:
        candidates = workflow_runtime_manager.get(
            sid
        ).run_store.list_knowledge_candidates(
            run_id=str(request.args.get("run_id") or ""),
            status=str(request.args.get("status") or ""),
        )
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, "candidates": candidates})


@bp.post("/api/session/<sid>/workflow-runs/<run_id>/knowledge-candidates")
@require_session_ownership
def create_workflow_knowledge_candidates(sid: str, run_id: str):
    from agent.workflows.knowledge import generate_knowledge_candidates

    try:
        candidates = generate_knowledge_candidates(
            workflow_runtime_manager.get(sid), run_id
        )
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, "candidates": candidates}), 201


@bp.post("/api/session/<sid>/workflow-knowledge-candidates/<candidate_id>/decide")
@require_session_ownership
def decide_workflow_knowledge_candidate(sid: str, candidate_id: str):
    from agent.workflows.knowledge import decide_knowledge_candidate

    body = request.get_json(silent=True) or {}
    user_id = str(
        request.headers.get("X-BAA-User-ID")
        or body.get("user_id")
        or "local-default"
    ).strip()[:200]
    raw_category = body.get("category_id")
    try:
        category_id = int(raw_category) if raw_category is not None else None
    except (TypeError, ValueError):
        category_id = None
    try:
        candidate = decide_knowledge_candidate(
            workflow_runtime_manager.get(sid),
            candidate_id,
            decision=str(body.get("decision") or ""),
            user_id=user_id,
            category_id=category_id,
            decided_by=str(body.get("decided_by") or ""),
            comment=str(body.get("comment") or ""),
        )
    except WorkflowContractError as exc:
        return _error(exc)
    return jsonify({"ok": True, "candidate": candidate})
