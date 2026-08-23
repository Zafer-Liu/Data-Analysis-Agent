#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local JobRunner with durable events and cooperative cancellation."""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterator, List, Optional

from agent.events import ArtifactCreatedEvent, JobEvent, serialize_event
from data.jobs_store import (
    JobsStore,
    STATUS_CANCELED,
    STATUS_CANCELING,
    STATUS_CREATED,
    STATUS_QUEUED,
    _TERMINAL,
)

log = logging.getLogger(__name__)

JobFn = Callable[["JobContext"], Any]


class JobCanceled(Exception):
    """Raised by a job after observing its cooperative cancellation flag."""


class JobContext:
    """Worker-facing progress, artifact and cancellation facade."""

    def __init__(
        self,
        job_id: str,
        store: JobsStore,
        is_canceled_fn: Callable[[str], bool],
        notify_fn: Optional[Callable[[], None]] = None,
        workspace_id: str = "",
        runtime=None,
    ):
        self.job_id = job_id
        self._store = store
        self._is_canceled = is_canceled_fn
        self._notify = notify_fn or (lambda: None)
        self.workspace_id = workspace_id
        self.runtime = runtime

    def set_progress(self, pct: int, message: str = "") -> None:
        if self._store.set_progress(self.job_id, pct, message):
            self._notify()

    def artifact_created(self, artifact: Dict[str, Any]) -> None:
        event = ArtifactCreatedEvent(job_id=self.job_id, artifact=artifact)
        if self._store.append_event(self.job_id, serialize_event(event)) is not None:
            self._notify()

    def record_event(self, event_type: str, **payload: Any) -> None:
        """Persist a non-sensitive worker lifecycle event for SSE replay."""
        event = {"type": event_type, **payload}
        if self._store.append_event(self.job_id, event) is not None:
            self._notify()

    def is_canceled(self) -> bool:
        return self._is_canceled(self.job_id)

    def check_canceled(self) -> None:
        if self._is_canceled(self.job_id):
            raise JobCanceled(self.job_id)


class JobRunner:
    """Per-session worker pool backed by the process-wide ``JobsStore``."""

    def __init__(self, session_id: str, store: JobsStore, max_workers: int = 2):
        self._sid = session_id
        self._store = store
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=f"job-{session_id[:8]}",
        )
        self._futures: Dict[str, Future] = {}
        self._canceled: set[str] = set()
        self._lock = threading.RLock()
        self._event_condition = threading.Condition()
        self._scope = threading.local()
        self._job_leases: Dict[str, str] = {}
        self._terminal_listeners: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}

    # ── Submission/execution ──────────────────────────────────────────────

    def create(self, fn: JobFn, job_type: str, label: str = "") -> str:
        parent_id = getattr(self._scope, "parent_id", "")
        workspace_id, lease_acquired = self._acquire_workspace_lease()
        try:
            job = self._store.create(
                self._sid,
                job_type,
                label=label,
                parent_id=parent_id,
                workspace_id=workspace_id,
            )
        except Exception:
            if lease_acquired:
                self._release_workspace_id(workspace_id)
            raise
        jid = job["id"]
        if lease_acquired:
            with self._lock:
                self._job_leases[jid] = workspace_id
        self._store.mark_queued(jid)
        self._notify_event()
        log.info("[job %s] queued (type=%s, session=%s)", jid, job_type, self._sid)

        try:
            future = self._pool.submit(self._run, jid, fn)
        except Exception:
            self._store.mark_failed(jid, "Job worker submission failed.")
            self._release_job_lease(jid)
            raise
        with self._lock:
            self._futures[jid] = future
        future.add_done_callback(
            lambda completed, job_id=jid: self._future_done(job_id, completed)
        )
        return jid

    def _acquire_workspace_lease(self) -> tuple[str, bool]:
        from data.workspace import workspace_manager
        scoped_id = str(getattr(self._scope, "workspace_id", "") or "")
        if scoped_id:
            return scoped_id, workspace_manager.acquire_job(scoped_id) is not None
        workspace_id, runtime = workspace_manager.acquire_job_for_session(self._sid)
        return str(workspace_id or ""), runtime is not None

    @staticmethod
    def _release_workspace_id(workspace_id: str) -> None:
        if workspace_id:
            from data.workspace import workspace_manager
            workspace_manager.release_job(workspace_id)

    def _release_job_lease(self, jid: str) -> None:
        with self._lock:
            workspace_id = self._job_leases.pop(jid, "")
        self._release_workspace_id(workspace_id)

    @contextmanager
    def conversation_scope(self, parent_id: str):
        """Attach jobs created by this request to a visible conversation parent."""
        previous = getattr(self._scope, "parent_id", "")
        previous_workspace = getattr(self._scope, "workspace_id", "")
        parent = self._store.get_for_session(self._sid, parent_id) or {}
        self._scope.parent_id = parent_id
        self._scope.workspace_id = str(parent.get("workspace_id") or "")
        try:
            yield
        finally:
            self._scope.parent_id = previous
            self._scope.workspace_id = previous_workspace

    def begin_tracked(self, job_type: str, label: str = "") -> str:
        workspace_id, lease_acquired = self._acquire_workspace_lease()
        try:
            job = self._store.create(
                self._sid, job_type, label=label,
                parent_id=getattr(self._scope, "parent_id", ""),
                workspace_id=workspace_id,
            )
        except Exception:
            if lease_acquired:
                self._release_workspace_id(workspace_id)
            raise
        jid = job["id"]
        if lease_acquired:
            with self._lock:
                self._job_leases[jid] = workspace_id
        self._store.mark_queued(jid)
        self._store.mark_started(jid)
        self._notify_event()
        return jid

    def append_tracked_event(self, jid: str, event: Dict[str, Any]) -> None:
        if self._store.append_event(jid, event) is not None:
            self._notify_event()

    def update_tracked(self, jid: str, progress: int, message: str = "") -> None:
        if self._store.set_progress(jid, progress, message):
            self._notify_event()

    def succeed_tracked(self, jid: str, result: Any) -> None:
        if self._store.mark_succeeded(jid, result):
            self._release_job_lease(jid)
            self._notify_event()

    def fail_tracked(self, jid: str, error: str) -> None:
        if self._store.mark_failed(jid, error):
            self._release_job_lease(jid)
            self._notify_event()

    def cancel_tracked(self, jid: str) -> None:
        job = self._store.get_for_session(self._sid, jid)
        if job and job["status"] not in _TERMINAL:
            for child in self._store.list_children(self._sid, jid):
                if child["status"] not in _TERMINAL:
                    self.cancel(child["id"])
            if job["status"] != STATUS_CANCELING:
                self._store.mark_canceling(jid)
            self._store.mark_canceled(jid)
            self._release_job_lease(jid)
            self._notify_event()

    def _run(self, jid: str, fn: JobFn) -> None:
        try:
            if self._is_canceled(jid):
                self._store.mark_canceled(jid)
                return
            if not self._store.mark_started(jid):
                return
            self._notify_event()
            log.info("[job %s] running", jid)
            job = self._store.get(jid) or {}
            workspace_id = str(job.get("workspace_id") or "")
            runtime = None
            if workspace_id:
                from data.workspace import workspace_manager
                runtime = workspace_manager.get_by_workspace(workspace_id)
            ctx = JobContext(
                jid,
                self._store,
                self._is_canceled,
                self._notify_event,
                workspace_id=workspace_id,
                runtime=runtime,
            )
            try:
                result = fn(ctx)
                if self._is_canceled(jid):
                    self._store.mark_canceled(jid)
                    log.info("[job %s] canceled after current step", jid)
                else:
                    self._store.mark_succeeded(jid, result)
                    log.info("[job %s] succeeded", jid)
            except JobCanceled:
                self._store.mark_canceled(jid)
                log.info("[job %s] canceled cooperatively", jid)
            except Exception as exc:
                self._store.mark_failed(jid, f"{type(exc).__name__}: {exc}")
                log.exception("[job %s] failed", jid)
        finally:
            self._release_job_lease(jid)
            self._notify_event()
            self._notify_terminal_listeners(jid)

    def _forget_future(self, jid: str) -> None:
        with self._lock:
            self._futures.pop(jid, None)

    def _future_done(self, jid: str, future: Future) -> None:
        self._forget_future(jid)
        if future.cancelled():
            self._release_job_lease(jid)

    def add_terminal_listener(
        self,
        jid: str,
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
        """Call *callback* once after the Job terminal state is durable."""
        immediate = None
        with self._lock:
            job = self._store.get_for_session(self._sid, jid)
            if job is None:
                raise ValueError(f"job not found: {jid}")
            if job["status"] in _TERMINAL:
                immediate = job
            else:
                self._terminal_listeners.setdefault(jid, []).append(callback)
        if immediate is not None:
            callback(immediate)

    def _notify_terminal_listeners(self, jid: str) -> None:
        job = self._store.get_for_session(self._sid, jid)
        if job is None or job["status"] not in _TERMINAL:
            return
        with self._lock:
            callbacks = self._terminal_listeners.pop(jid, [])
        for callback in callbacks:
            try:
                callback(job)
            except Exception:
                log.exception("[job %s] terminal listener failed", jid)

    def remove_terminal_listeners(self, jid: str) -> int:
        """Remove pending callbacks when an owning workflow is disposed."""
        with self._lock:
            callbacks = self._terminal_listeners.pop(jid, [])
        return len(callbacks)

    def _is_canceled(self, jid: str) -> bool:
        with self._lock:
            return jid in self._canceled

    # ── Cancellation ──────────────────────────────────────────────────────

    def cancel(self, jid: str) -> bool:
        job = self._store.get_for_session(self._sid, jid)
        if job is None or job["status"] in _TERMINAL:
            return False

        with self._lock:
            self._canceled.add(jid)
            future = self._futures.get(jid)

        for child in self._store.list_children(self._sid, jid):
            if child["status"] not in _TERMINAL:
                self.cancel(child["id"])

        if not self._store.mark_canceling(jid):
            with self._lock:
                self._canceled.discard(jid)
            return False
        canceled_before_start = future is not None and future.cancel()
        if canceled_before_start:
            self._store.mark_canceled(jid)
            self._release_job_lease(jid)
            self._notify_terminal_listeners(jid)
        self._notify_event()
        return True

    # ── Events/query bridge ───────────────────────────────────────────────

    def _notify_event(self) -> None:
        with self._event_condition:
            self._event_condition.notify_all()

    def wait_for_events(self, after_sequence: int, timeout: float = 0.2) -> bool:
        if self._store.last_sequence(self._sid) > after_sequence:
            return True
        with self._event_condition:
            self._event_condition.wait(timeout=max(0.0, timeout))
        return self._store.last_sequence(self._sid) > after_sequence

    def list_events(
        self,
        after_sequence: int = 0,
        limit: int = 200,
        job_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if job_id and self._store.get_for_session(self._sid, job_id) is None:
            return []
        return self._store.list_events(
            self._sid,
            after_sequence=after_sequence,
            limit=limit,
            job_id=job_id,
        )

    def iter_events(
        self,
        jid: str,
        after_sequence: int = 0,
        timeout: Optional[float] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield persisted events until ``jid`` reaches a terminal state."""
        deadline = None if timeout is None else time.monotonic() + timeout
        sequence = max(0, int(after_sequence))
        while True:
            events = self.list_events(sequence, job_id=jid)
            for event in events:
                sequence = event["sequence"]
                yield event
            job = self.get_status(jid)
            if job is None or (job["status"] in _TERMINAL and not events):
                return
            if deadline is not None and time.monotonic() >= deadline:
                return
            wait = 0.2 if deadline is None else min(0.2, max(0.0, deadline - time.monotonic()))
            self.wait_for_events(sequence, wait)

    def publish_event(self, event: JobEvent) -> Optional[Dict[str, Any]]:
        persisted = self._store.append_event(event.job_id, serialize_event(event))
        if persisted is not None:
            self._notify_event()
        return persisted

    def get_status(self, jid: str) -> Optional[Dict[str, Any]]:
        return self._store.get_for_session(self._sid, jid)

    def list_jobs(
        self, active_only: bool = False, limit: int = 100, top_level_only: bool = False,
    ) -> List[Dict[str, Any]]:
        if active_only:
            return self._store.list_active(
                self._sid, top_level_only=top_level_only,
            )[:max(1, int(limit))]
        return self._store.list_by_session(
            self._sid, limit=limit, top_level_only=top_level_only,
        )

    def list_artifacts(self, job_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        return self._store.list_artifacts(self._sid, job_ids)

    def list_detail_events(self, job_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        return self._store.list_detail_events(self._sid, job_ids)

    def get_status_for_session(self, session_id: str, job_id: str) -> Optional[Dict[str, Any]]:
        return self._store.get_for_session(session_id, job_id)

    def purge_terminal_for_session(self, session_id: str, job_ids: List[str]) -> int:
        return self._store.purge_terminal_ids(session_id, job_ids)

    def clear_terminal(self) -> int:
        return self._store.clear_terminal(self._sid)

    @property
    def session_id(self) -> str:
        return self._sid

    @property
    def last_sequence(self) -> int:
        return self._store.last_sequence(self._sid)

    @property
    def oldest_sequence(self) -> int:
        return self._store.oldest_sequence(self._sid)

    def shutdown(self, wait: bool = True) -> None:
        try:
            self._pool.shutdown(wait=wait, cancel_futures=True)
            log.info("[job] runner shutdown (session=%s)", self._sid)
        except Exception:
            log.exception("[job] shutdown error")


def empty_job(ctx: JobContext, duration: float = 0.3) -> Dict[str, Any]:
    """Small progress-reporting task used by the B1 end-to-end tests."""
    ticks = 0
    steps = max(1, int(duration / 0.05))
    for index in range(steps + 1):
        ctx.check_canceled()
        progress = int(index * 100 / steps)
        ctx.set_progress(progress, f"step {index}/{steps}")
        ticks += 1
        time.sleep(0.05)
    return {"duration": duration, "ticks": ticks}
