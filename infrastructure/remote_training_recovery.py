"""Periodic reconciliation of approved local remote runs after application restart."""
from __future__ import annotations

import atexit
import logging
import os
import threading

log = logging.getLogger(__name__)


def reconcile_once(service, session_manager):
    reconciled = []
    for scope in service.store.pending_scopes(owner_id="local-default"):
        session = session_manager.get(scope["session_id"])
        temporary = None
        runner = None
        if session is not None and str(getattr(session, "workspace_id", "") or "") == scope["workspace_id"]:
            runner = session.job_runner
        try:
            if runner is None:
                # Sessions are restored lazily. Their durable Jobs and accepted
                # Stop intent must converge even before the first /jobs request.
                from agent.jobs import JobRunner
                from data.session import get_global_jobs_store
                temporary = runner = JobRunner(scope["session_id"], get_global_jobs_store(),
                                                acquire_workspace_leases=False)
            reconciled.extend(service.reconcile_pending(**scope, job_runner=runner))
        except Exception as exc:
            # The durable run keeps unknown/cancel intent; a network failure
            # must not become another submit or a claim that the process exited.
            log.warning("[training-recovery] reconciliation deferred: %s", type(exc).__name__)
        finally:
            if temporary is not None:
                service.detach_job_runner(temporary)
                temporary.shutdown()
    return reconciled


def start_remote_reconciliation(app):
    from infrastructure.paths import is_cloud_managed_runtime
    if is_cloud_managed_runtime() or os.environ.get("BAA_REMOTE_RECONCILE_ENABLED", "1") != "1":
        return None
    if "remote_training_recovery" in app.extensions:
        return app.extensions["remote_training_recovery"]
    from .remote_training_client import RemoteTrainingService
    from api.state import session_manager

    service = app.extensions.get("remote_training_service")
    if service is None:
        service = RemoteTrainingService()
        app.extensions["remote_training_service"] = service
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            try:
                reconcile_once(service, session_manager)
            except Exception as exc:
                log.warning("[training-recovery] local recovery unavailable: %s", type(exc).__name__)
            stop.wait(30)

    thread = threading.Thread(target=poll, name="baa-training-recovery", daemon=True)
    app.extensions["remote_training_recovery"] = {"stop": stop, "thread": thread}
    atexit.register(stop.set)
    thread.start()
    return app.extensions["remote_training_recovery"]
