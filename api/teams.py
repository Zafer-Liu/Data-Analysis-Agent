"""Blueprint: workspace-scoped analyst teams."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent.tools.workspace.teams import WorkspaceTeamError, WorkspaceTeamStore
from .state import require_session_ownership

bp = Blueprint("teams", __name__)


def _error_response(error: Exception, status: int):
    return jsonify({"ok": False, "error": str(error)}), status


@bp.get("/api/session/<sid>/teams")
@require_session_ownership
def list_teams(sid: str):
    try:
        teams = WorkspaceTeamStore(sid).list()
    except WorkspaceTeamError as exc:
        return jsonify({"ok": False, "error": str(exc), "teams": []}), 400
    return jsonify({"ok": True, "teams": teams})


@bp.get("/api/session/<sid>/teams/<team_name>")
@require_session_ownership
def team_status(sid: str, team_name: str):
    try:
        team = WorkspaceTeamStore(sid).status(team_name, mark_lead_read=True)
    except WorkspaceTeamError as exc:
        message = str(exc)
        status = 404 if "not found" in message else 400
        return _error_response(exc, status)
    return jsonify({"ok": True, "team": team})


@bp.delete("/api/session/<sid>/teams/<team_name>")
@require_session_ownership
def delete_team(sid: str, team_name: str):
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:
        return _error_response(ValueError("解散团队前必须明确确认。"), 400)
    try:
        result = WorkspaceTeamStore(sid).delete(
            team_name,
            require_inactive=True,
            force=body.get("force") is True,
        )
    except WorkspaceTeamError as exc:
        message = str(exc)
        if "not found" in message:
            status = 404
        elif "暂不能解散" in message:
            status = 409
        else:
            status = 400
        return _error_response(exc, status)
    return jsonify({"ok": True, **result})


@bp.delete("/api/session/<sid>/teams/<team_name>/messages")
@require_session_ownership
def clear_team_messages(sid: str, team_name: str):
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:
        return _error_response(ValueError("清空前必须明确确认。"), 400)
    try:
        result = WorkspaceTeamStore(sid).clear_messages(team_name)
    except WorkspaceTeamError as exc:
        message = str(exc)
        if "not found" in message:
            status = 404
        elif "暂不能清空" in message:
            status = 409
        else:
            status = 400
        return _error_response(exc, status)
    return jsonify({"ok": True, **result})

@bp.get("/api/session/<sid>/team-plans")
@require_session_ownership
def list_team_plans(sid: str):
    from agent.teams.dynamic_plans import DynamicTeamPlanStore
    try:
        plans = DynamicTeamPlanStore(sid).list(
            team_name=str(request.args.get("team_name") or "")
        )
    except WorkspaceTeamError as exc:
        return _error_response(exc, 400)
    return jsonify({"ok": True, "plans": plans})


@bp.get("/api/session/<sid>/team-plans/<plan_id>")
@require_session_ownership
def get_team_plan(sid: str, plan_id: str):
    from agent.teams.dynamic_plans import DynamicTeamPlanStore
    try:
        plan = DynamicTeamPlanStore(sid).get(plan_id)
    except WorkspaceTeamError as exc:
        return _error_response(exc, 404 if "not found" in str(exc) else 400)
    return jsonify({"ok": True, "plan": plan})


@bp.post("/api/session/<sid>/team-plans/<plan_id>/actions/<action>")
@require_session_ownership
def control_team_plan(sid: str, plan_id: str, action: str):
    from agent.teams.dynamic_plans import DynamicTeamPlanStore
    try:
        plan = DynamicTeamPlanStore(sid).control(plan_id, action)
    except WorkspaceTeamError as exc:
        return _error_response(exc, 409 if "cannot" in str(exc) else 400)
    canceled_jobs = 0
    if action == "cancel":
        from api.state import session_manager

        session = session_manager.get(sid)
        runner = getattr(session, "job_runner", None) if session else None
        if runner is not None:
            for task in plan.get("tasks") or []:
                job_id = str(task.get("job_id") or "")
                if job_id:
                    runner.cancel_tracked(job_id)
                    canceled_jobs += 1
    return jsonify({"ok": True, "plan": plan, "canceled_jobs": canceled_jobs})


@bp.post("/api/session/<sid>/team-plans/<plan_id>/workflow-draft")
@require_session_ownership
def create_team_plan_workflow_draft(sid: str, plan_id: str):
    from agent.teams.dynamic_plans import DynamicTeamPlanStore
    body = request.get_json(silent=True) or {}
    try:
        workflow = DynamicTeamPlanStore(sid).create_workflow_draft(
            plan_id, created_by=str(body.get("created_by") or "teams_panel"),
        )
    except WorkspaceTeamError as exc:
        return _error_response(exc, 409 if "completed" in str(exc) else 400)
    return jsonify({"ok": True, "workflow": workflow}), 201
