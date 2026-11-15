"""Choose training routes before touching local Torch; external transfer needs a grant."""
from __future__ import annotations

from infrastructure.training_device import select_training_device


def select_training_target(remote_runners=(), *, min_free_memory_mb=2048,
                           execution_target="auto", approved_plan_id="", grant_id=""):
    target = execution_target
    connection_id = ""
    if isinstance(target, dict):
        connection_id = str(target.get("connection_id") or "")
        target = target.get("kind", "auto")
    if isinstance(target, str) and target.startswith("remote(") and target.endswith(")"):
        connection_id, target = target[7:-1], "remote"
    if target == "remote":
        if not approved_plan_id or not grant_id:
            raise ValueError("Explicit remote training requires a server-approved plan and grant")
        return {"kind": "remote", "connection_id": connection_id, "reason": "Approved remote execution"}
    if target not in {"auto", "cpu", "local_cuda"}:
        raise ValueError("execution_target must be auto, cpu, local_cuda or remote(connection_id)")
    if target == "cpu":
        return {"kind": "cpu", "device": "cpu", "reason": "Explicit CPU execution"}
    local = select_training_device(min_free_memory_mb)
    if target == "local_cuda" and not local["device"].startswith("cuda"):
        raise ValueError("Explicit local_cuda target is unavailable")
    return {"kind": "local" if local["device"].startswith("cuda") else "cpu", **local}


def submit_approved_analysis(family, params, *, service=None, approved_plan_id="", grant_id="",
                             idempotency_key="", owner_id="local-default", session_id="", workspace_id="",
                             connection_id="", job_runner=None):
    from infrastructure.remote_training_client import RemoteTrainingService
    owned = service is None
    service = service or RemoteTrainingService()
    try:
        plan = service.store.get("plan", approved_plan_id, owner_id=owner_id, session_id=session_id, workspace_id=workspace_id)
        from remote_runner.protocol import validate_params
        if (plan["family"] != family or plan["params"] != validate_params(family, params)
                or (connection_id and plan["connection_id"] != connection_id)):
            raise ValueError("Analysis parameters or target changed after approval; prepare a new plan")
        return service.submit(approved_plan_id, grant_id, idempotency_key=idempotency_key, owner_id=owner_id,
                              session_id=session_id, workspace_id=workspace_id, job_runner=job_runner)
    finally:
        if owned:
            service.close()
