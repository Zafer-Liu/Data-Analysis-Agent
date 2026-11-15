"""Bounded, process-exclusive access to an authoritative DuckDB file.

Callbacks run on one connection-owning thread. Only detached values may leave a
callback; compute workers should instead read immutable dataset shards. The OS
lock is held for the connection's lifetime, not a renewable SQLite TTL.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class WorkspaceOwnerError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class _FileLock:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a+b")
        try:
            self.file.seek(0, os.SEEK_END)
            if self.file.tell() == 0:
                self.file.write(b"\0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise WorkspaceOwnerError("workspace_owner_busy", "Database belongs to another owner process") from exc

    def close(self):
        if not self.file.closed:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    types: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    @property
    def description(self):
        return tuple((name, dtype, None, None, None, None, None) for name, dtype in zip(self.columns, self.types))

    def df(self):
        import pandas as pd
        return pd.DataFrame.from_records(self.rows, columns=list(self.columns))


class _Dispatcher:
    def __init__(self, path, max_pending, enqueue_timeout, config):
        self.path = path
        self.pid = os.getpid()
        self.refs = 0
        self.queue = queue.Queue(maxsize=max_pending)
        self.enqueue_timeout = enqueue_timeout
        self.lock = _FileLock(path.with_name(path.name + ".owner.lock"))
        self.ready = Future()
        self.completed = 0
        self.active_future = None
        self.active_lock = threading.Lock()
        self.started_at = time.time()
        self.thread = threading.Thread(target=self._run, args=(config,), name="workspace-db-owner", daemon=True)
        self.thread.start()
        try:
            self.ready.result(timeout=30)
        except BaseException:
            # A connection failure finishes _run and releases the OS lock.
            self.thread.join(timeout=30)
            raise

    def _run(self, config):
        connection = None
        try:
            import duckdb  # Optional dependency: no connection/import at module load.
            connection = duckdb.connect(str(self.path), config=config)
            self.connection = connection
            connection.execute('SET autoinstall_known_extensions=false')
            connection.execute('SET autoload_known_extensions=false')
            spill = self.path.with_name(self.path.name + '.spill')
            spill.mkdir(exist_ok=True)
            from data.dataset_manifest import safe_snapshot_root
            safe_snapshot_root(spill)
            connection.execute('SET temp_directory=?', [str(spill)])
            if not config:
                connection.execute("SET memory_limit='1GB'")
                connection.execute('SET threads=4')
                connection.execute("SET max_temp_directory_size='8GB'")
            self.ready.set_result(True)
            while True:
                task = self.queue.get()
                try:
                    if task is None:
                        return
                    future, callback, transaction = task
                    if not future.set_running_or_notify_cancel():
                        continue
                    try:
                        with self.active_lock:
                            self.active_future = future
                        if transaction:
                            connection.execute("BEGIN TRANSACTION")
                        value = callback(connection)
                        def detached(item):
                            if isinstance(item, (duckdb.DuckDBPyConnection, duckdb.DuckDBPyRelation)) or type(item).__name__ == 'RecordBatchReader':
                                raise WorkspaceOwnerError('owner_cursor_escape', 'Return detached values from owner callbacks')
                            if isinstance(item, dict):
                                for child in item.values():
                                    detached(child)
                            elif isinstance(item, (tuple, list)):
                                for child in item:
                                    detached(child)
                        detached(value)
                        if transaction:
                            connection.execute("COMMIT")
                    except BaseException as exc:
                        if transaction:
                            try:
                                connection.execute("ROLLBACK")
                            except Exception:
                                pass
                        future.set_exception(exc)
                    else:
                        future.set_result(value)
                    finally:
                        with self.active_lock:
                            self.active_future = None
                        self.completed += 1
                finally:
                    self.queue.task_done()
        except BaseException as exc:
            if not self.ready.done():
                self.ready.set_exception(exc)
            while True:
                try:
                    pending = self.queue.get_nowait()
                except queue.Empty:
                    break
                if pending is not None:
                    pending[0].set_exception(WorkspaceOwnerError("workspace_owner_stopped", str(exc)))
                self.queue.task_done()
        finally:
            if connection is not None:
                connection.close()
            self.lock.close()


_registry_lock = threading.RLock()
_registry: dict[tuple[int, str], _Dispatcher] = {}


class WorkspaceDBOwner:
    """Reference-counted facade; independent facades in one process share a queue.

    A different process gets workspace_owner_busy, and must route to the owner
    or retry after its real exit. There is deliberately no unsafe write fallback.
    Callbacks and SQL are backend-only interfaces, not an RPC for user code.
    """
    def __init__(self, path, *, max_pending=32, enqueue_timeout=5.0, budget=None):
        if isinstance(max_pending, bool) or not isinstance(max_pending, int) or not 1 <= max_pending <= 1024:
            raise ValueError("max_pending must be an integer between 1 and 1024")
        if isinstance(enqueue_timeout, bool) or not isinstance(enqueue_timeout, (int, float)) or not 0 <= enqueue_timeout <= 300:
            raise ValueError("enqueue_timeout must be between 0 and 300 seconds")
        self.path = Path(path).absolute().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._key = (os.getpid(), os.path.normcase(str(self.path)))
        self._closed = False
        config = {}
        if budget is not None:
            from infrastructure.resource_budget import BudgetSpec
            spec = BudgetSpec.from_dict(budget)
            config = {"memory_limit": f"{spec.duckdb_memory_bytes}B", "threads": str(spec.threads),
                      'max_temp_directory_size': f'{spec.temp_bytes}B'}
        with _registry_lock:
            dispatcher = _registry.get(self._key)
            if dispatcher is None:
                dispatcher = _Dispatcher(self.path, max_pending, enqueue_timeout, config)
                dispatcher.config = config
                _registry[self._key] = dispatcher
            elif config and dispatcher.config != config:
                raise WorkspaceOwnerError("owner_budget_conflict", "A live database owner already has different limits")
            dispatcher.refs += 1
            self._dispatcher = dispatcher

    def _check(self):
        if self._closed or not self._dispatcher.thread.is_alive():
            raise WorkspaceOwnerError("workspace_owner_stopped", "Database owner is closed")
        if os.getpid() != self._dispatcher.pid:
            raise WorkspaceOwnerError("owner_process_changed", "Create a new owner facade after process creation")
        if threading.current_thread() is self._dispatcher.thread:
            raise WorkspaceOwnerError("owner_reentrant_call", "Do not enqueue work from an owner callback")

    def submit(self, callback: Callable, *, transaction=False) -> Future:
        self._check()
        future = Future()
        # Submission and final-close sentinel must be ordered together.
        with _registry_lock:
            self._check()
            try:
                self._dispatcher.queue.put((future, callback, transaction), timeout=self._dispatcher.enqueue_timeout)
            except queue.Full as exc:
                raise WorkspaceOwnerError("workspace_owner_backpressure", "Database owner queue is full") from exc
        return future

    def call(self, callback: Callable, *, transaction=False):
        return self.submit(callback, transaction=transaction).result()

    def transaction(self, callback: Callable):
        return self.call(callback, transaction=True)

    def execute(self, sql: str, parameters=None, *, max_rows=10000, max_bytes=64 << 20) -> QueryResult:
        if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 0:
            raise ValueError("max_rows must be a nonnegative integer")
        def query(connection):
            cursor = connection.execute(sql, parameters or [])
            description = cursor.description or []
            rows, size = [], 0
            while True:
                batch = cursor.fetchmany(min(256, max_rows + 1))
                if not batch:
                    break
                size += sum(sum(len(v) if isinstance(v, bytes) else len(str(v).encode('utf-8')) for v in row) for row in batch)
                if len(rows) + len(batch) > max_rows or size > max_bytes:
                    raise WorkspaceOwnerError("owner_result_too_large", "Read large results from immutable shards")
                rows.extend(batch)
            return QueryResult(tuple(item[0] for item in description), tuple(str(item[1]) for item in description), tuple(rows))
        return self.call(query)

    def interrupt(self, future=None):
        self._check()
        with self._dispatcher.active_lock:
            if future is None or self._dispatcher.active_future is future:
                self._dispatcher.connection.interrupt()

    def snapshot(self):
        return {"pid": self._dispatcher.pid, "pending": self._dispatcher.queue.qsize(),
                "capacity": self._dispatcher.queue.maxsize, "completed": self._dispatcher.completed,
                "started_at": self._dispatcher.started_at, "closed": self._closed,
                "access": "single_owner_thread", "cross_process": "exclusive_os_lock"}

    def close(self):
        with _registry_lock:
            if self._closed:
                return
            self._check()
            self._closed = True
            dispatcher = self._dispatcher
            dispatcher.refs -= 1
            if dispatcher.refs:
                return
            # Keep the registry lock until queued writes and close have finished.
            dispatcher.queue.put(None)
            dispatcher.thread.join()
            _registry.pop(self._key, None)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
