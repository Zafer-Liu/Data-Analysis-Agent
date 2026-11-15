"""Validated resource limits, durable admission, and supervised local workers.

RSS, wall time and disk limits are monitored and can overshoot between samples.
DuckDB memory_limit covers its allocator, not total RSS. Leases never expire by
TTL: PID plus process creation time must prove that a bound process has exited.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import signal
import sqlite3
import stat
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from pathlib import Path


class BudgetExceeded(RuntimeError):
    code = "budget_exceeded"

    def __init__(self, resource, observed=None, limit=None):
        self.resource, self.observed, self.limit = resource, observed, limit
        super().__init__(f"Resource budget exceeded: {resource} ({observed} > {limit})")


class ResourceError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _number(name, value, minimum=0, maximum=1 << 60, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside its finite bounds")
    if integer and not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


@dataclass(frozen=True)
class BudgetSpec:
    max_run_seconds: float = 3600.0
    max_input_bytes: int = 16 << 30
    max_output_bytes: int = 16 << 30
    max_rows: int = 100_000_000
    max_files: int = 10000
    max_log_bytes: int = 8 << 20
    duckdb_memory_bytes: int = 1 << 30
    rss_bytes: int = 2 << 30
    batch_bytes: int = 16 << 20
    queue_batches: int = 2
    threads: int = 4
    temp_bytes: int = 8 << 30
    max_scan_bytes: int = 1 << 40
    max_cpu_seconds: float = 3600.0

    def __post_init__(self):
        caps = {"max_run_seconds": 31_536_000, "max_cpu_seconds": 31_536_000,
                "threads": 256, "queue_batches": 1024, "max_files": 1_000_000,
                "max_rows": 10**12}
        for field in fields(self):
            _number(field.name, getattr(self, field.name), 0.001 if field.name.endswith("seconds") else 1,
                    caps.get(field.name, 1 << 60), integer=not field.name.endswith("seconds"))
        if self.batch_bytes > self.rss_bytes:
            raise ValueError("batch_bytes must not exceed rss_bytes")
        if self.duckdb_memory_bytes > self.rss_bytes:
            raise ValueError("duckdb_memory_bytes must not exceed rss_bytes")
        if self.batch_bytes * self.queue_batches > self.rss_bytes:
            raise ValueError("Queued batches must fit within rss_bytes")

    @classmethod
    def from_dict(cls, value=None):
        if isinstance(value, cls):
            return value
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("budget must be a dictionary")
        unknown = set(value) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown budget fields: {', '.join(sorted(map(str, unknown)))}")
        return cls(**value)

    def to_dict(self):
        return asdict(self)

    def check_usage(self, **measurements):
        aliases = {"run_seconds": "max_run_seconds", "elapsed_seconds": "max_run_seconds",
                   "input_bytes": "max_input_bytes", "output_bytes": "max_output_bytes",
                   "rows": "max_rows", "files": "max_files", "log_bytes": "max_log_bytes",
                   "scan_bytes": "max_scan_bytes", "cpu_seconds": "max_cpu_seconds"}
        limits = self.to_dict()
        for name, value in measurements.items():
            target = aliases.get(name, name)
            if target not in limits:
                raise ValueError(f"Unknown resource measurement: {name}")
            _number(name, value)
            if value > limits[target]:
                raise BudgetExceeded(name, value, limits[target])
        return dict(measurements)


def process_identity(pid=None):
    import psutil
    process = psutil.Process(pid or os.getpid())
    return {"pid": process.pid, "created": process.create_time()}


def process_alive(identity):
    if not identity:
        return False
    import psutil
    try:
        process = psutil.Process(identity["pid"])
        return abs(process.create_time() - identity["created"]) < 0.001 and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, OSError):
        # Unable to inspect is not evidence of exit.
        return True


def directory_usage(path):
    """Count real files without following links, including old snapshots/staging."""
    path = Path(path)
    if not path.exists():
        return {"bytes": 0, "files": 0}
    total = count = 0
    pending = [path]
    while pending:
        entry = pending.pop()
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ResourceError("unsafe_disk_path", "Disk accounting does not follow links or reparse points")
        if stat.S_ISDIR(info.st_mode):
            pending.extend(entry.iterdir())
        elif stat.S_ISREG(info.st_mode):
            total += info.st_size
            count += 1
        else:
            raise ResourceError("unsafe_disk_path", "Disk accounting requires ordinary files")
    return {"bytes": total, "files": count}


class ResourceLease:
    def __init__(self, coordinator, owner_id, execution_id, token):
        self.coordinator, self.owner_id, self.execution_id, self.token = coordinator, owner_id, execution_id, token

    def bind_process(self, process):
        identity = process_identity(process.pid if hasattr(process, "pid") else process)
        import psutil
        if psutil.Process(identity["pid"]).ppid() != os.getpid():
            raise ResourceError("foreign_worker", "Only direct children may be bound as supervised workers")
        self.coordinator._update_processes(self, [identity], append=True)
        return identity

    def observe_processes(self, identities):
        self.coordinator._update_processes(self, identities, append=True)

    def release(self):
        return self.coordinator._release(self)

    def snapshot(self):
        return self.coordinator._lease_row(self.owner_id, self.execution_id)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.release()


class ResourceCoordinator:
    """One SQLite admission ledger shared across sessions and application processes.

    Settings are persisted by the first coordinator; subsequent instances cannot
    silently enlarge a live ledger. Disk reservations conservatively include
    input materialization + output + spill alongside already existing files.
    """
    def __init__(self, root=None, *, max_slots=None, max_owner_slots=None, max_rss_bytes=None,
                 max_disk_bytes=None, disk_safety_bytes=None, account_paths=None):
        from infrastructure.paths import content_data_path
        self.root = Path(root) if root is not None else content_data_path("resource-coordinator")
        self.root = self.root.absolute().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "reservations.sqlite3"
        requested = {"max_slots": max_slots, "max_owner_slots": max_owner_slots,
                     "max_rss_bytes": max_rss_bytes, "max_disk_bytes": max_disk_bytes,
                     "disk_safety_bytes": disk_safety_bytes}
        defaults = {"max_slots": 2, "max_owner_slots": 2, "max_rss_bytes": 6 << 30,
                    "max_disk_bytes": 1 << 50, "disk_safety_bytes": 256 << 20}
        for key, value in requested.items():
            if value is not None:
                _number(key, value, 0 if key == "disk_safety_bytes" else 1, integer=True)
        if root is None and account_paths is None:
            account_paths = [content_data_path(name) for name in ('datasets', 'outputs', 'uploads')]
        paths = [str(Path(p).absolute().resolve()) for p in (account_paths or [])]
        if any(self.root == Path(p) or self.root.is_relative_to(Path(p)) for p in paths):
            raise ValueError("Accounted paths must not contain the coordination ledger")
        with self._connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute('CREATE TABLE IF NOT EXISTS accounted_paths (path TEXT PRIMARY KEY)')
            conn.execute("CREATE TABLE IF NOT EXISTS reservations (owner_id TEXT NOT NULL, execution_id TEXT NOT NULL, "
                         "token TEXT NOT NULL, budget TEXT NOT NULL, controller TEXT NOT NULL, processes TEXT NOT NULL, "
                         "rss_bytes INTEGER NOT NULL, disk_bytes INTEGER NOT NULL, created_at REAL NOT NULL, "
                         "PRIMARY KEY(owner_id, execution_id))")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM policy WHERE id=1").fetchone()
            if row:
                self.policy = json.loads(row[0])
                for key, value in requested.items():
                    if value is not None and value != self.policy[key]:
                        raise ResourceError("resource_policy_conflict", "Coordinator policy is already configured")
                if account_paths is not None and paths != self.policy["account_paths"]:
                    raise ResourceError("resource_policy_conflict", "Coordinator disk roots are already configured")
            else:
                self.policy = {key: requested[key] if requested[key] is not None else value for key, value in defaults.items()}
                self.policy["account_paths"] = paths
                conn.execute("INSERT INTO policy VALUES(1, ?)", (json.dumps(self.policy),))
            conn.commit()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=10000")
            yield conn
        finally:
            conn.close()

    def _rows(self, conn):
        names = ["owner_id", "execution_id", "token", "budget", "controller", "processes", "rss_bytes", "disk_bytes", "created_at"]
        return [dict(zip(names, row)) for row in conn.execute("SELECT * FROM reservations").fetchall()]

    def _reap(self, conn):
        removed = []
        for row in self._rows(conn):
            if not process_alive(json.loads(row["controller"])) and not any(process_alive(p) for p in json.loads(row["processes"])):
                conn.execute("DELETE FROM reservations WHERE token=?", (row["token"],))
                removed.append(row["execution_id"])
                import re
                from infrastructure.paths import content_data_path
                if self.root == content_data_path('resource-coordinator').absolute().resolve() and re.fullmatch(r'plan-[0-9a-f]{32}', row['execution_id']):
                    work = content_data_path('dataset-plans', row['execution_id'])
                    if work.exists():
                        directory_usage(work)
                        shutil.rmtree(work)
        return removed

    def register_account_path(self, path):
        """Register a trusted backend content path; overlapping roots count once."""
        path = Path(path).absolute()
        if self.root == path or self.root.is_relative_to(path):
            raise ValueError('Accounted paths must not contain the coordinator ledger')
        with self._connection() as conn:
            conn.execute('INSERT OR IGNORE INTO accounted_paths VALUES (?)', [str(path)])

    def _account_paths(self):
        with self._connection() as conn:
            values = [Path(row[0]) for row in conn.execute('SELECT path FROM accounted_paths')]
        selected = []
        for path in sorted(set(values + [Path(p) for p in self.policy['account_paths']]), key=lambda p: len(p.parts)):
            if not any(path == parent or path.is_relative_to(parent) for parent in selected):
                selected.append(path)
        return selected

    def _usage(self, rows):
        paths = self._account_paths()
        existing = sum(directory_usage(path)["bytes"] for path in paths) + directory_usage(self.root / 'logs')['bytes']
        free_spaces = [shutil.disk_usage(self.root).free]
        for path in paths:
            while not path.is_dir() and path.parent != path:
                path = path.parent
            free_spaces.append(shutil.disk_usage(path).free)
        owners = {row["controller"] for row in rows}
        owner_rss = 0
        import psutil
        for value in owners:
            identity = json.loads(value)
            if process_alive(identity):
                try:
                    owner_rss += psutil.Process(identity["pid"]).memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        return {"slots": len(rows), "reserved_rss_bytes": sum(row["rss_bytes"] for row in rows),
                "owner_rss_bytes": owner_rss, "existing_disk_bytes": existing,
                "reserved_disk_bytes": sum(row["disk_bytes"] for row in rows),
                "free_disk_bytes": min(free_spaces), 'disk_admission': 'conservative_minimum_across_registered_volumes'}

    def reserve(self, owner_id, execution_id, budget):
        for name, value in (("owner_id", owner_id), ("execution_id", execution_id)):
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ValueError(f"{name} must be a nonempty bounded string")
        spec = BudgetSpec.from_dict(budget)
        encoded = json.dumps(spec.to_dict(), sort_keys=True, separators=(",", ":"))
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._reap(conn)
            rows = self._rows(conn)
            for row in rows:
                if (row["owner_id"], row["execution_id"]) == (owner_id, execution_id):
                    if row["budget"] != encoded:
                        raise ResourceError("idempotency_conflict", "Execution already reserved different resource limits")
                    return ResourceLease(self, owner_id, execution_id, row["token"])
            usage = self._usage(rows)
            disk_bytes = spec.max_input_bytes + spec.max_output_bytes + spec.temp_bytes + spec.max_log_bytes
            owner_slots = sum(row["owner_id"] == owner_id for row in rows)
            if usage["slots"] >= self.policy["max_slots"]:
                raise BudgetExceeded("global_slots", usage["slots"] + 1, self.policy["max_slots"])
            if owner_slots >= self.policy["max_owner_slots"]:
                raise BudgetExceeded("owner_slots", owner_slots + 1, self.policy["max_owner_slots"])
            controller = process_identity()
            import psutil
            controller_rss = 0 if any(json.loads(row["controller"]) == controller for row in rows) else psutil.Process().memory_info().rss
            rss = usage["reserved_rss_bytes"] + usage["owner_rss_bytes"] + controller_rss + spec.rss_bytes
            if rss > self.policy["max_rss_bytes"]:
                raise BudgetExceeded("global_rss_bytes", rss, self.policy["max_rss_bytes"])
            disk = usage["existing_disk_bytes"] + usage["reserved_disk_bytes"] + disk_bytes
            if disk > self.policy["max_disk_bytes"]:
                raise BudgetExceeded("global_disk_bytes", disk, self.policy["max_disk_bytes"])
            available = usage["free_disk_bytes"] - usage["reserved_disk_bytes"] - self.policy["disk_safety_bytes"]
            if disk_bytes > available:
                raise BudgetExceeded("free_disk_bytes", disk_bytes, available)
            token = uuid.uuid4().hex
            conn.execute("INSERT INTO reservations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (owner_id, execution_id, token, encoded, json.dumps(controller), "[]", spec.rss_bytes, disk_bytes, time.time()))
            conn.commit()
        return ResourceLease(self, owner_id, execution_id, token)

    def _lease_row(self, owner_id, execution_id):
        with self._connection() as conn:
            row = next((r for r in self._rows(conn) if (r["owner_id"], r["execution_id"]) == (owner_id, execution_id)), None)
        if row:
            for name in ("controller", "processes", "budget"):
                row[name] = json.loads(row[name])
        return row

    def _update_processes(self, lease, identities, *, append):
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT controller, processes FROM reservations WHERE token=?", (lease.token,)).fetchone()
            if row is None:
                raise ResourceError("lease_missing", "Reservation no longer exists")
            if json.loads(row[0]) != process_identity():
                raise ResourceError("lease_owned_elsewhere", "Only the reserving supervisor may bind workers")
            merged = json.loads(row[1]) if append else []
            for identity in identities:
                if identity not in merged:
                    merged.append(identity)
            conn.execute("UPDATE reservations SET processes=? WHERE token=?", (json.dumps(merged), lease.token))
            conn.commit()

    def _release(self, lease):
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT controller, processes FROM reservations WHERE token=?", (lease.token,)).fetchone()
            if row is None:
                return False
            if any(process_alive(p) for p in json.loads(row[1])):
                raise ResourceError("lease_process_alive", "A worker has not really exited; reservation retained")
            controller = json.loads(row[0])
            if controller != process_identity() and process_alive(controller):
                raise ResourceError("lease_owned_elsewhere", "A live supervisor still owns this reservation")
            conn.execute("DELETE FROM reservations WHERE token=?", (lease.token,))
            conn.commit()
        return True

    def recover(self):
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = self._reap(conn)
            conn.commit()
            return result

    def snapshot(self):
        with self._connection() as conn:
            rows = self._rows(conn)
        return {**self._usage(rows), "policy": dict(self.policy), "executions": [row["execution_id"] for row in rows],
                "limits": {"rss": "monitored", "disk": "monitored", "deadline": "monitored", "hard": []}}

    def run_worker(self, argv, *, owner_id, execution_id, budget=None, **kwargs):
        return run_supervised_worker(argv, coordinator=self, owner_id=owner_id, execution_id=execution_id, budget=budget, **kwargs)


class _WindowsJob:
    """Best-effort process-tree containment; this is not an RSS hard limit."""
    def __init__(self, process):
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes
        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = self.kernel.CreateJobObjectW(None, None)
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if handle and self.kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) and self.kernel.AssignProcessToJobObject(handle, int(process._handle)):
            self.handle = handle
        elif handle:
            self.kernel.CloseHandle(handle)

    def terminate(self):
        if self.handle:
            self.kernel.TerminateJobObject(self.handle, 1)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def run_supervised_worker(argv, *, budget=None, coordinator=None, owner_id, execution_id,
                          cwd=None, temp_dir=None, output_dir=None, env=None, cancel=None,
                          progress=None, poll_interval=0.05, on_spawn=None):
    from data.workspace_db_owner import _FileLock, WorkspaceOwnerError
    import hashlib
    coordinator = coordinator or ResourceCoordinator()
    name = hashlib.sha256(json.dumps([owner_id, execution_id]).encode('utf-8')).hexdigest()
    try:
        lock = _FileLock(coordinator.root / 'launches' / (name + '.lock'))
    except WorkspaceOwnerError as exc:
        raise ResourceError('execution_already_started', 'Another supervisor owns this execution') from exc
    try:
        return _run_supervised_worker(argv, budget=budget, coordinator=coordinator, owner_id=owner_id,
            execution_id=execution_id, cwd=cwd, temp_dir=temp_dir, output_dir=output_dir, env=env,
            cancel=cancel, progress=progress, poll_interval=poll_interval, on_spawn=on_spawn)
    finally:
        lock.close()


def _run_supervised_worker(argv, *, budget, coordinator, owner_id, execution_id,
                          cwd, temp_dir, output_dir, env, cancel,
                          progress, poll_interval, on_spawn):
    """Run a trusted backend command without a shell, returning measurable peaks.

    Nonzero exit is worker_failed. Budget/cancel exceptions carry usage. Logs are
    disk-spooled and sampled, not buffered in unbounded pipes. Only this child
    tree is terminated. A lease survives failure to confirm a descendant's exit.
    """
    import psutil
    spec = BudgetSpec.from_dict(budget)
    _number("poll_interval", poll_interval, 0.01, 5)
    if not isinstance(argv, (tuple, list)) or not argv or not all(isinstance(arg, str) for arg in argv):
        raise ValueError("argv must be a nonempty list of strings")
    coordinator = coordinator or ResourceCoordinator()
    lease = coordinator.reserve(owner_id, execution_id, spec)
    prior = lease.snapshot()
    if prior['controller'] != process_identity():
        raise ResourceError('execution_owned_elsewhere', 'The execution belongs to another supervisor')
    if prior["processes"]:
        raise ResourceError("execution_already_started", "A supervised attempt cannot be launched twice")
    log_dir = coordinator.root / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / (lease.token + ".log")
    process = job = None
    started = time.monotonic()
    identities = []
    cpu_totals = {}
    usage = {"rss_bytes": 0, "temp_bytes": 0, "output_bytes": 0, "files": 0, "log_bytes": 0, "run_seconds": 0.0, 'cpu_seconds': 0.0}
    failure = None
    try:
        with log_path.open("wb") as log:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, shell=False,
                                       start_new_session=os.name != "nt",
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
            job = _WindowsJob(process)
            try:
                identity = lease.bind_process(process)
                identities.append(identity)
                if on_spawn:
                    on_spawn(identity)
            except psutil.NoSuchProcess:
                if process.poll() is None:
                    raise
            while True:
                try:
                    children = psutil.Process(process.pid).children(recursive=True)
                except psutil.NoSuchProcess:
                    children = []
                for child in children:
                    identity = {"pid": child.pid, "created": child.create_time()}
                    if identity not in identities:
                        identities.append(identity)
                        if on_spawn:
                            on_spawn(identity)
                lease.observe_processes(identities)
                rss = 0
                for identity in identities:
                    if process_alive(identity):
                        try:
                            tracked = psutil.Process(identity['pid'])
                            rss += tracked.memory_info().rss
                            cpu = tracked.cpu_times()
                            cpu_totals[(identity['pid'], identity['created'])] = cpu.user + cpu.system
                        except psutil.NoSuchProcess:
                            pass
                temp = directory_usage(temp_dir) if temp_dir else {"bytes": 0, "files": 0}
                output = directory_usage(output_dir) if output_dir else {"bytes": 0, "files": 0}
                current = {"rss_bytes": rss, "run_seconds": time.monotonic() - started,
                           "temp_bytes": temp["bytes"], "output_bytes": output["bytes"],
                           "files": temp["files"] + output["files"], "log_bytes": log_path.stat().st_size,
                           'cpu_seconds': sum(cpu_totals.values())}
                for key, value in current.items():
                    usage[key] = max(usage[key], value)
                spec.check_usage(**current)
                if cancel and cancel():
                    raise ResourceError("worker_cancelled", "Worker cancellation requested")
                if progress:
                    progress(dict(usage))
                if process.poll() is not None:
                    if any(process_alive(identity) for identity in identities[1:]):
                        raise ResourceError("worker_descendants_alive", "Worker exited leaving running descendants")
                    if process.returncode:
                        raise ResourceError("worker_failed", f"Worker exited with code {process.returncode}")
                    break
                threading.Event().wait(poll_interval)
    except BaseException as exc:
        failure = exc
        if process is not None:
            if job:
                job.terminate()
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for identity in reversed(identities):
                if process_alive(identity):
                    try:
                        psutil.Process(identity["pid"]).kill()
                    except psutil.NoSuchProcess:
                        pass
            process.wait(timeout=10)
            descendants = []
            for identity in identities[1:]:
                if process_alive(identity):
                    try:
                        descendants.append(psutil.Process(identity["pid"]))
                    except psutil.NoSuchProcess:
                        pass
            psutil.wait_procs(descendants, timeout=5)
        exc.usage = dict(usage)
        raise
    finally:
        hard = ["process_tree_lifetime"] if job and job.handle else []
        if job:
            job.close()
        # Raises rather than reporting that the slot is free if real exit is unknown.
        try:
            lease.release()
            if failure is not None:
                failure.exit_confirmed = True
        except ResourceError:
            if failure is None:
                raise
            failure.exit_confirmed = False
    return {"returncode": process.returncode, "usage": usage, "log_path": str(log_path),
            "limits": {"rss": "monitored", "disk": "monitored", "deadline": "monitored", "hard": hard},
            "exit_confirmed": True}
