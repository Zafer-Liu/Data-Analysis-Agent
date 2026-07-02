"""Blueprint: workspace-scoped analyst teams."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent.tools.workspace.teams import WorkspaceTeamError, WorkspaceTeamStore

bp = Blueprint("teams", __name__)


def _error_response(error: Exception, status: int):
    return jsonify({"ok": False, "error": str(error)}), status


@bp.get("/api/session/<sid>/teams")
def list_teams(sid: str):
    try:
        teams = WorkspaceTeamStore(sid).list()
    except WorkspaceTeamError as exc:
        return jsonify({"ok": False, "error": str(exc), "teams": []}), 400
    return jsonify({"ok": True, "teams": teams})


@bp.get("/api/session/<sid>/teams/<team_name>")
def team_status(sid: str, team_name: str):
    try:
        team = WorkspaceTeamStore(sid).status(team_name, mark_lead_read=True)
    except WorkspaceTeamError as exc:
        message = str(exc)
        status = 404 if "not found" in message else 400
        return _error_response(exc, status)
    return jsonify({"ok": True, "team": team})


@bp.delete("/api/session/<sid>/teams/<team_name>")
def delete_team(sid: str, team_name: str):
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:
        return _error_response(ValueError("解散团队前必须明确确认。"), 400)
    try:
        result = WorkspaceTeamStore(sid).delete(
            team_name, require_inactive=True,
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
