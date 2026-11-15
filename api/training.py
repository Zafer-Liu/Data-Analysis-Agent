"""User approval facade for RemoteTrainingService (registered by the main API)."""
from __future__ import annotations

import threading

from flask import Blueprint, current_app, jsonify, request, send_file

from data.remote_execution_store import RemoteExecutionError
from infrastructure.paths import is_cloud_managed_runtime
from infrastructure.remote_training_client import RemoteTrainingService, public_record
from remote_runner.protocol import RunnerError

bp = Blueprint("training", __name__)
_LOCK = threading.Lock()


def _session_scope(sid):
    if is_cloud_managed_runtime():
        raise RemoteExecutionError("permission_denied", "Hosted training is disabled until tenant SSH isolation is configured")
    # Lazy imports avoid api/__init__ / DatasetCatalog / training import cycles.
    from .state import check_session_ownership, session_manager
    allowed, owner = check_session_ownership(sid)
    if not allowed:
        raise RemoteExecutionError("permission_denied", "Session belongs to another user")
    session = session_manager.get_or_create(sid)
    return session, {"owner_id": owner or "local-default", "session_id": sid,
                     "workspace_id": str(getattr(session, "workspace_id", "") or "")}


def _service():
    # Tests inject a real service with a controlled transport in this extension;
    # request JSON cannot choose a transport, credential resolver or catalog root.
    with _LOCK:
        if "remote_training_service" not in current_app.extensions:
            current_app.extensions["remote_training_service"] = RemoteTrainingService()
        return current_app.extensions["remote_training_service"]


def _body(allowed):
    value = request.get_json(silent=True)
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise RemoteExecutionError("invalid_request", "Unexpected training request fields")
    return value


def _error(exc):
    code = getattr(exc, "code", "invalid_request")
    status = 403 if code == "permission_denied" else 404 if code == "not_found" else 409 if code in {
        "idempotency_conflict", "plan_invalidated", "grant_expired", "grant_consumed", "runner_incompatible", "plan_expired"} else 400
    return jsonify({"ok": False, "code": code, "error": str(exc)[:1000]}), status


_ERRORS = (RemoteExecutionError, RunnerError, ValueError, TypeError, KeyError, OSError)


@bp.post("/api/session/<sid>/training/plans")
def create_plan(sid):
    try:
        _session, scope = _session_scope(sid)
        payload = _body({"family", "params", "budget", "dataset_id", "snapshot_id", "connection_id", "retention_seconds"})
        plan = _service().plan(payload, **scope)
        return jsonify({"ok": True, "plan": plan, "plan_id": plan["plan_id"]}), 201
    except _ERRORS as exc:
        return _error(exc)


@bp.post("/api/session/<sid>/training/plans/<plan_id>/approve")
def approve_plan(sid, plan_id):
    try:
        _session, scope = _session_scope(sid)
        payload = _body({"expected_plan_hash"})
        grant = _service().approve(plan_id, expected_plan_hash=payload.get("expected_plan_hash", ""), **scope)
        return jsonify({"ok": True, "grant": grant, "grant_id": grant["grant_id"]}), 201
    except _ERRORS as exc:
        return _error(exc)


@bp.post("/api/session/<sid>/training/runs")
def create_run(sid):
    try:
        session, scope = _session_scope(sid)
        payload = _body({"plan_id", "grant_id", "idempotency_key"})
        run = _service().submit(payload.get("plan_id", ""), payload.get("grant_id", ""),
                                idempotency_key=payload.get("idempotency_key", ""), job_runner=session.job_runner, **scope)
        return jsonify({"ok": True, "run": public_record(run), "run_id": run["run_id"], "job_id": run["job_id"]}), 202
    except _ERRORS as exc:
        return _error(exc)


@bp.get("/api/session/<sid>/training/runs/<run_id>")
def get_run(sid, run_id):
    try:
        session, scope = _session_scope(sid)
        service = _service()
        service.store.get("run", run_id, **scope)
        service.attach_job_runner(run_id, session.job_runner)
        return jsonify({"ok": True, "run": service.status(run_id, **scope)})
    except _ERRORS as exc:
        return _error(exc)


@bp.get("/api/session/<sid>/training/runs/<run_id>/artifacts/<artifact_id>")
def get_artifact(sid, run_id, artifact_id):
    try:
        _session, scope = _session_scope(sid)
        path, entry = _service().artifact(run_id, artifact_id, **scope)
        response = send_file(path, as_attachment=True, download_name=artifact_id)
        response.headers["X-Content-SHA256"] = entry["sha256"]
        return response
    except _ERRORS as exc:
        return _error(exc)


@bp.post("/api/session/<sid>/training/runs/<run_id>/cancel")
def cancel_run(sid, run_id):
    try:
        session, scope = _session_scope(sid)
        if request.content_length:
            _body(set())
        service = _service()
        service.store.get("run", run_id, **scope)
        service.attach_job_runner(run_id, session.job_runner)
        return jsonify({"ok": True, "run": service.cancel(run_id, **scope)}), 202
    except _ERRORS as exc:
        return _error(exc)
