"""Request/attempt identity and cooperative executor lifetime, independent of LLMs.

Cancellation and public task completion never acknowledge executor exit. A scope
may close while a blocking child is still returning; exit callbacks run only once
that child has acknowledged its own exit. Use them to release snapshots/leases.
"""
from __future__ import annotations

from contextlib import contextmanager
import logging
import math
import os
import threading
import time
import uuid
from typing import Callable, Iterator

log = logging.getLogger(__name__)
_PROCESS_ID = uuid.uuid4().hex
_ACTIVE: dict[str, "ExecutionContext"] = {}
_ACTIVE_LOCK = threading.RLock()
_REQUESTS: dict[tuple[str, str], "ExecutionContext"] = {}


class ExecutionStopped(RuntimeError):
    """A cooperative boundary refused to start or publish more work."""


class ExecutionCancelled(ExecutionStopped):
    pass


class ExecutionDeadlineExceeded(ExecutionStopped):
    pass


def _process_birth(pid: int) -> str | None:
    """Read a process birth identity without signaling or terminating it."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            birth, end, system, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, *map(ctypes.byref, (birth, end, system, user))):
                return None
            return str((birth.dwHighDateTime << 32) | birth.dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    try:
        from pathlib import Path
        # The comm field can contain spaces and parentheses.
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


_PROCESS_BIRTH = _process_birth(os.getpid())


def execution_owner_alive(metadata: dict) -> bool | None:
    """True=live, False=proven orphan/exited, None=foreign owner unknown.

    An elapsed deadline alone is never proof that an executor has exited.
    Persisted PID + birth identity protects recovery against PID reuse.
    """
    if metadata.get("exit_acknowledged") is True:
        return False
    try:
        pid = int(metadata.get("owner_pid") or 0)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if pid == os.getpid():
        if not metadata.get("owner_process_id"):
            return None
        if metadata["owner_process_id"] != _PROCESS_ID:
            return False
        context = get_execution_context(str(metadata.get("attempt_id") or ""))
        return context is not None and not context.exit_acknowledged
    birth = _process_birth(pid)
    expected = metadata.get("owner_started_at")
    if birth is not None:
        return None if not expected else birth == expected
    if os.name == "nt":
        import ctypes
        # ERROR_INVALID_PARAMETER proves that the PID no longer exists.
        return False if ctypes.get_last_error() == 87 else None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return None
    return None


def get_execution_context(attempt_id: str) -> "ExecutionContext | None":
    with _ACTIVE_LOCK:
        return _ACTIVE.get(str(attempt_id or ""))


class ExecutionContext:
    """One executor attempt, optionally nested beneath a request or Job.

    ``deadline`` is an absolute monotonic time. ``cancellation_check`` is a
    request-scoped predicate (for example JobContext.is_canceled); never pass a
    mutable Session-wide flag that a later turn can reset.
    """

    def __init__(
        self, *, request_id: str = "", parent: "ExecutionContext | None" = None,
        timeout_seconds: float | None = None, deadline: float | None = None,
        cancellation_check: Callable[[], bool] | None = None,
    ) -> None:
        if timeout_seconds is not None:
            timeout_seconds = float(timeout_seconds)
            if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
                raise ValueError("timeout_seconds must be finite and non-negative")
            timeout_deadline = time.monotonic() + timeout_seconds
            deadline = min(deadline, timeout_deadline) if deadline is not None else timeout_deadline
        if deadline is not None and not math.isfinite(float(deadline)):
            raise ValueError("deadline must be finite")
        self.parent = parent
        self.request_id = parent.request_id if parent is not None else str(request_id or uuid.uuid4().hex)
        self.attempt_id = uuid.uuid4().hex
        inherited = parent.deadline if parent is not None else None
        self.deadline = min(deadline, inherited) if deadline is not None and inherited is not None else (
            deadline if deadline is not None else inherited
        )
        self._cancellation_check = cancellation_check
        self._cancel = threading.Event()
        self._exited = threading.Event()
        self._lock = threading.RLock()
        self._closing = False
        self._children: dict[str, ExecutionContext] = {}
        self._exit_callbacks: list[Callable[[], None]] = []
        self.cancel_reason = ""
        if parent is not None:
            with parent._lock:
                parent.checkpoint()
                if parent._closing:
                    raise ExecutionStopped("Parent executor is already closing")
                parent._children[self.attempt_id] = self
        with _ACTIVE_LOCK:
            _ACTIVE[self.attempt_id] = self

    def child(
        self, *, timeout_seconds: float | None = None, deadline: float | None = None,
        cancellation_check: Callable[[], bool] | None = None,
    ) -> "ExecutionContext":
        return ExecutionContext(
            parent=self, timeout_seconds=timeout_seconds, deadline=deadline,
            cancellation_check=cancellation_check,
        )

    def cancel(self, reason: str = "Cancellation requested") -> None:
        with self._lock:
            if not self._cancel.is_set():
                self.cancel_reason = str(reason)[:500]
                self._cancel.set()

    @property
    def cancellation_requested(self) -> bool:
        if self._cancel.is_set():
            return True
        if self.parent is not None and self.parent.cancellation_requested:
            return True
        if self._cancellation_check is not None and self._cancellation_check():
            self.cancel("Parent task cancellation requested")
            return True
        return False

    @property
    def exit_acknowledged(self) -> bool:
        return self._exited.is_set()

    def checkpoint(self) -> float | None:
        """Raise before another call/publication; otherwise return time left."""
        if self.cancellation_requested:
            raise ExecutionCancelled(self.cancel_reason or "Parent execution canceled")
        if self.parent is not None:
            self.parent.checkpoint()
        remaining = None if self.deadline is None else self.deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise ExecutionDeadlineExceeded("Execution deadline exceeded")
        if self.exit_acknowledged:
            raise ExecutionStopped("Executor has already exited")
        return remaining

    def add_exit_callback(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if not self._exited.is_set():
                self._exit_callbacks.append(callback)
                return
        self._call_exit_callback(callback)

    @staticmethod
    def _call_exit_callback(callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            log.exception("Execution exit callback failed")

    def acknowledge_exit(self) -> None:
        """Declare this body exited; children still retain the enclosing lease."""
        with self._lock:
            self._closing = True
        self._finish_exit()

    def _finish_exit(self) -> None:
        with self._lock:
            if self._exited.is_set() or not self._closing or self._children:
                return
            self._exited.set()
            callbacks, self._exit_callbacks = self._exit_callbacks, []
        with _ACTIVE_LOCK:
            _ACTIVE.pop(self.attempt_id, None)
        for callback in callbacks:
            self._call_exit_callback(callback)
        if self.parent is not None:
            with self.parent._lock:
                self.parent._children.pop(self.attempt_id, None)
            self.parent._finish_exit()

    def wait_for_exit(self, timeout: float | None = None) -> bool:
        return self._exited.wait(timeout)

    @contextmanager
    def executor(self) -> Iterator["ExecutionContext"]:
        try:
            self.checkpoint()
            yield self
        finally:
            self.acknowledge_exit()

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id, "attempt_id": self.attempt_id,
            "parent_attempt_id": self.parent.attempt_id if self.parent else "",
            "owner_pid": os.getpid(), "owner_process_id": _PROCESS_ID,
            "owner_started_at": _PROCESS_BIRTH, "deadline_monotonic": self.deadline,
            "cancel_requested": self._cancel.is_set(), "cancel_reason": self.cancel_reason,
            "exit_acknowledged": self.exit_acknowledged,
        }


def begin_request_execution(
    session_id: str, *, request_id: str = "", timeout_seconds=None,
    cancellation_check: Callable[[], bool] | None = None,
) -> ExecutionContext:
    """Register a chat turn; overlapping unexited requests are rejected."""
    key = (str(session_id), "chat")
    with _ACTIVE_LOCK:
        previous = _REQUESTS.get(key)
        if previous is not None and not previous.exit_acknowledged:
            raise ExecutionStopped("A conversation request is still executing")
        context = ExecutionContext(
            request_id=request_id, timeout_seconds=timeout_seconds,
            cancellation_check=cancellation_check,
        )
        _REQUESTS[key] = context
    return context


def cancel_request_execution(session_id: str, *, request_id: str = "") -> ExecutionContext | None:
    """Cancel only the active turn captured now, optionally requiring its ID."""
    with _ACTIVE_LOCK:
        context = _REQUESTS.get((str(session_id), "chat"))
    if context is None or context.exit_acknowledged or (request_id and context.request_id != request_id):
        return None
    context.cancel("Stopped by the caller")
    return context


def finish_request_execution(session_id: str, context: ExecutionContext) -> None:
    """Close a turn without allowing its cleanup to clear a newer turn."""
    def forget() -> None:
        with _ACTIVE_LOCK:
            key = (str(session_id), "chat")
            if _REQUESTS.get(key) is context:
                _REQUESTS.pop(key, None)
    context.add_exit_callback(forget)
    context.acknowledge_exit()
