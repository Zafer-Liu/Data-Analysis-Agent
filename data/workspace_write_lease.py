"""Cross-process, SQLite-backed write leases for persistent Workspace DuckDB."""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


LEASE_SECONDS = max(10, int(os.environ.get("BAA_WORKSPACE_WRITE_LEASE_SECONDS", "120")))
RETRY_SECONDS = max(0.1, float(os.environ.get("BAA_WORKSPACE_WRITE_LEASE_RETRY_SECONDS", "1")))
SYNC_WAIT_SECONDS = max(0.0, float(os.environ.get("BAA_WORKSPACE_WRITE_SYNC_WAIT_SECONDS", "5")))


class WorkspaceWriteLeaseUnavailable(RuntimeError):
    pass


class WorkspaceWriteLease:
    """A lease that renews itself while a worker owns the Workspace writer."""

    def __init__(self, store: "WorkspaceWriteLeaseStore", workspace_id: str, owner_id: str):
        self._store = store
        self.workspace_id = workspace_id
        self.owner_id = owner_id
        self._stop = threading.Event()
        self._renewer: threading.Thread | None = None

    def start(self) -> None:
        interval = max(1.0, LEASE_SECONDS / 3)

        def renew() -> None:
            while not self._stop.wait(interval):
                if not self._store.renew(self.workspace_id, self.owner_id):
                    return

        self._renewer = threading.Thread(target=renew, name="workspace-write-lease", daemon=True)
        self._renewer.start()

    def release(self) -> None:
        self._stop.set()
        if self._renewer is not None:
            self._renewer.join(timeout=1)
        self._store.release(self.workspace_id, self.owner_id)


class WorkspaceWriteLeaseStore:
    """Atomic SQLite compare-and-swap lease operations.

    The database lives beside ``workspace.duckdb`` so independent application
    processes that mount the same Workspace coordinate through the same file.
    """

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS workspace_write_leases ("
                "workspace_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, expires_at REAL NOT NULL)"
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def acquire(self, workspace_id: str, owner_id: str) -> WorkspaceWriteLease | None:
        now = time.time()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner_id, expires_at FROM workspace_write_leases WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            if row and row[0] != owner_id and row[1] > now:
                conn.rollback()
                return None
            conn.execute(
                "INSERT INTO workspace_write_leases(workspace_id, owner_id, expires_at) VALUES (?, ?, ?) "
                "ON CONFLICT(workspace_id) DO UPDATE SET owner_id = excluded.owner_id, expires_at = excluded.expires_at",
                (workspace_id, owner_id, now + LEASE_SECONDS),
            )
            conn.commit()
        lease = WorkspaceWriteLease(self, workspace_id, owner_id)
        lease.start()
        return lease

    def renew(self, workspace_id: str, owner_id: str) -> bool:
        with self._connection() as conn:
            updated = conn.execute(
                "UPDATE workspace_write_leases SET expires_at = ? "
                "WHERE workspace_id = ? AND owner_id = ?",
                (time.time() + LEASE_SECONDS, workspace_id, owner_id),
            ).rowcount
            conn.commit()
        return bool(updated)

    def release(self, workspace_id: str, owner_id: str) -> bool:
        with self._connection() as conn:
            deleted = conn.execute(
                "DELETE FROM workspace_write_leases WHERE workspace_id = ? AND owner_id = ?",
                (workspace_id, owner_id),
            ).rowcount
            conn.commit()
        return bool(deleted)


@contextmanager
def acquire_workspace_write_lease(ctx, runtime) -> Iterator[WorkspaceWriteLease]:
    """Wait cooperatively until this Job is the Workspace's only DuckDB writer."""
    store = WorkspaceWriteLeaseStore(runtime.meta_dir / "workspace_write_leases.db")
    owner_id = str(getattr(ctx, "job_id", f"inline-{threading.get_ident()}"))
    lease_key = str(runtime.db_path.resolve())
    waiting_reported = False
    while True:
        ctx.check_canceled()
        lease = store.acquire(lease_key, owner_id)
        if lease is not None:
            if hasattr(ctx, "record_event"):
                ctx.record_event("workspace_write_lease_acquired", workspace_id=runtime.workspace_id)
            break
        if not waiting_reported:
            ctx.set_progress(1, "等待此工作区的 DuckDB 写入任务完成")
            if hasattr(ctx, "record_event"):
                ctx.record_event("workspace_write_lease_waiting", workspace_id=runtime.workspace_id)
            waiting_reported = True
        time.sleep(RETRY_SECONDS)
    try:
        yield lease
    finally:
        lease.release()
        if hasattr(ctx, "record_event"):
            ctx.record_event("workspace_write_lease_released", workspace_id=runtime.workspace_id)


@contextmanager
def acquire_synchronous_workspace_write_lease(db_path: Path, owner_id: str) -> Iterator[WorkspaceWriteLease]:
    """Bounded wait used by request-thread writes such as analysis tables."""
    store = WorkspaceWriteLeaseStore(db_path.parent / "workspace_write_leases.db")
    lease_key = str(db_path.resolve())
    deadline = time.monotonic() + SYNC_WAIT_SECONDS
    while True:
        lease = store.acquire(lease_key, owner_id)
        if lease is not None:
            break
        if time.monotonic() >= deadline:
            raise WorkspaceWriteLeaseUnavailable("该工作区正在执行其他 DuckDB 写入任务，请稍后重试。")
        time.sleep(RETRY_SECONDS)
    try:
        yield lease
    finally:
        lease.release()
