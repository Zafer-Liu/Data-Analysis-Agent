"""Durable single-host supervisor. SSH clients only proxy its bounded RPC frames.

One configured CUDA device (or an explicit CPU test slot) belongs to one attempt
until all observed processes have exited. No client command, module or environment
is accepted. Restart adopts the same worker; it never restarts training epochs.
"""
from __future__ import annotations

import contextlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from remote_runner.protocol import (BUILD_VERSION, KERNEL_VERSION, MAX_FRAME, TERMINAL,
                                    RunnerError, canonical, decode_frame, digest, integer,
                                    response, token, validate_budget, validate_manifest, validate_params)
from remote_runner.safe_io import atomic_json, file_sha256, read_json, safe_path, verify_files, validate_safetensors

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_NAMES = {"model.safetensors", "model.json", "metrics.json", "predictions.parquet",
                  "table0.parquet", "table1.parquet", "table2.parquet"}


def rpc_path(path):
    """POSIX SFTP spelling; /C:/ is reserved for controlled Windows CPU tests."""
    text = Path(path).absolute().as_posix()
    return "/" + text if os.name == "nt" else text


def default_root():
    return Path(os.environ.get("BAA_RUNNER_ROOT") or Path.home() / ".local/share/baa-runner").absolute()


def boot_identity():
    if sys.platform == "linux":
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    return ""  # CPU test platforms still use PID birth; Linux is the remote profile.


def identity(pid=None):
    import psutil
    p = psutil.Process(pid or os.getpid())
    return {"pid": p.pid, "birth": p.create_time(), "boot_id": boot_identity()}


def alive(value):
    if not value:
        return False
    if value.get("boot_id") and value["boot_id"] != boot_identity():
        return False
    import psutil
    try:
        p = psutil.Process(value["pid"])
        return abs(p.create_time() - value["birth"]) < .001 and p.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, OSError):
        return True  # Uninspectable is not an exit acknowledgement.


def trusted_environment():
    # Admin installation paths only; no PYTHONPATH, user site, task env or plugins.
    names = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE",
             "LD_LIBRARY_PATH", "CUDA_HOME", "CUDA_PATH", "SSL_CERT_FILE")
    env = {k: os.environ[k] for k in names if k in os.environ}
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONNOUSERSITE="1",
               OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    return env


class InstanceLock:
    def __init__(self, path):
        self.stream = open(path, "a+b")
        self.stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                if os.fstat(self.stream.fileno()).st_size == 0:
                    self.stream.write(b"0")
                    self.stream.flush()
            except OSError as exc:
                self.stream.close()
                raise RunnerError("supervisor_busy", "A supervisor already owns this root") from exc
        else:
            import fcntl
            try:
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                self.stream.close()
                raise RunnerError("supervisor_busy", "A supervisor already owns this root") from exc

    def close(self):
        self.stream.close()


def _disk_usage(root):
    total = files = 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in dirs:
            safe_path(root, (Path(directory) / name).relative_to(root).as_posix())
        for name in names:
            try:
                path = safe_path(root, (Path(directory) / name).relative_to(root).as_posix(), exists=True)
                total += path.stat().st_size
                files += 1
            except FileNotFoundError:
                continue  # An atomic publication renamed a temporary file.
            except RunnerError as exc:
                if exc.code != "input_missing":
                    raise
    return total, files


class Supervisor:
    def __init__(self, root=None, *, device="cuda:0", probe=None):
        if device != "cpu" and not (device.startswith("cuda:") and device[5:].isdigit()):
            raise RunnerError("invalid_device", "Administrator must configure cpu or cuda:N")
        self.root = Path(root or default_root()).absolute()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        safe_path(self.root, "tasks")
        (self.root / "tasks").mkdir(exist_ok=True, mode=0o700)
        self.instance_lock = InstanceLock(self.root / "supervisor.lock")
        self.lock, self.device = threading.RLock(), device
        self.db = sqlite3.connect(self.root / "tasks.sqlite3", check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, idem TEXT UNIQUE NOT NULL,
                request_hash TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (task_id TEXT NOT NULL, seq INTEGER NOT NULL,
                body TEXT NOT NULL, PRIMARY KEY(task_id,seq));
        """)
        row = self.db.execute("SELECT value FROM meta WHERE key='instance'").fetchone()
        self.instance_id = row[0] if row else uuid.uuid4().hex
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('instance',?)", (self.instance_id,))
        self.db.commit()
        self.processes, self.deadlines = {}, {}
        self._closed = False
        # A fixed subprocess probes dependencies; torch is absent from the RPC process.
        if probe is None:
            try:
                result = subprocess.run([sys.executable, "-m", "remote_runner.torch_worker", "--probe", "--device", device],
                                        cwd=PACKAGE_ROOT, env=trusted_environment(), capture_output=True, timeout=30)
                probe = json.loads(result.stdout[:MAX_FRAME]) if result.returncode == 0 else {"ready": False, "probe_error": "probe_failed", "probe_returncode": result.returncode}
            except subprocess.TimeoutExpired:
                probe = {"ready": False, "probe_error": "probe_timeout"}
            except (OSError, ValueError):
                probe = {"ready": False, "probe_error": "probe_unavailable"}
        self.capabilities = probe
        self.generation = uuid.uuid4().hex
        with self.lock:
            for task in self._all():
                if task["status"] not in TERMINAL:
                    self.deadlines[task["task_id"]] = time.monotonic() + min(task["budget"]["max_run_seconds"], max(0, task["expires_at"] - time.time()))
                    if task["status"] == "verifying":
                        task["status"] = "reserved"
                        self._event(task, "verification_recovery_pending")
        self.tick()

    def _all(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT body FROM tasks")]

    def _save(self, t):
        self.db.execute("UPDATE tasks SET body=? WHERE id=?", (canonical(t).decode(), t["task_id"]))
        self.db.commit()

    def _event(self, t, kind, **payload):
        t["last_seq"] += 1
        event = {"seq": t["last_seq"], "type": kind, "at": time.time(), **payload}
        if len(canonical(event)) > 4096:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            message = str(error.get("message") or payload.get("message") or "Event details exceeded the log frame")
            event = {"seq": t["last_seq"], "type": kind, "at": time.time(), "details_truncated": True,
                     "message": message.encode("utf-8")[:2048].decode("utf-8", "ignore"),
                     "code": str(error.get("code", ""))[:64]}
        self.db.execute("INSERT INTO events VALUES (?,?,?)", (t["task_id"], t["last_seq"], canonical(event).decode()))
        self.db.execute("DELETE FROM events WHERE task_id=? AND seq <= ?", (t["task_id"], t["last_seq"] - 2000))
        while self.db.execute("SELECT coalesce(sum(length(CAST(body AS BLOB))),0) FROM events WHERE task_id=?", (t["task_id"],)).fetchone()[0] > t["budget"]["max_log_bytes"]:
            self.db.execute("DELETE FROM events WHERE task_id=? AND seq=(SELECT min(seq) FROM events WHERE task_id=?)", (t["task_id"], t["task_id"]))
        self._save(t)

    def _task(self, p):
        field, val = ("id", p["task_id"]) if p.get("task_id") else ("idem", p.get("idempotency_key"))
        token(val, field)
        row = self.db.execute(f"SELECT body FROM tasks WHERE {field}=?", (val,)).fetchone()
        if row is None:
            raise RunnerError("task_unknown", "No task exists for this identity")
        return json.loads(row[0])

    def _directory(self, t):
        return self.root / "tasks" / t["task_id"]

    def _public(self, t):
        return {k: t[k] for k in ("task_id", "runner_instance_id", "status", "attempt_id", "expires_at", "last_seq",
                                   "exit_acknowledged", "slot_owned", "error", "plan_hash", "manifest_hash",
                                   "upload_manifest", "artifacts", "cancel_requested", "usage", "process_identity",
                                   "retain_until", "artifacts_expired")}

    def handshake(self):
        return {"protocol_version": 1, "runner_version": BUILD_VERSION, "kernel_version": KERNEL_VERSION,
                "runner_instance_id": self.instance_id, "generation_id": self.generation, "boot_id": boot_identity(),
                "families": ["Torch_MLP", "Torch_RNN"], "max_frame_bytes": MAX_FRAME,
                "expires_at": time.time() + 60, "device": self.device, "ready": bool(self.capabilities.get("ready")),
                "supervision": "persistent-service+worker-watchdog", "limits": "monitored-RSS-disk-CPU;may-overshoot",
                "slot_policy": "one-attempt-per-configured-device", **self.capabilities}

    def rpc(self, operation, payload):
        if not isinstance(payload, dict):
            raise RunnerError("invalid_request", "payload must be an object")
        if operation == "submit" and payload.get("phase") == "commit":
            return self.commit(payload)
        with self.lock:
            if operation == "handshake":
                return self.handshake()
            if operation == "submit":
                if payload.get("phase") == "reserve":
                    return self.reserve(payload)
                raise RunnerError("invalid_request", "submit phase must be reserve or commit")
            task = self._task(payload)
            if operation == "status":
                return self._public(task)
            if operation == "cancel":
                if payload.get("attempt_id") and payload["attempt_id"] != task["attempt_id"]:
                    raise RunnerError("attempt_conflict", "Cancellation refers to another attempt")
                if task["status"] not in TERMINAL and not task["cancel_requested"]:
                    if payload.get("reason") == "deadline_expired":
                        task.update(status="canceling", error={"code": "deadline_expired", "message": "Approved deadline expired"})
                    else:
                        task.update(cancel_requested=True, status="canceling")
                    self._event(task, "cancel_requested")  # Durable before signaling.
                    atomic_json(self._directory(task) / "cancel.json", {"attempt_id": task["attempt_id"]})
                return self._public(task)
            if operation == "events":
                after = integer(payload.get("after_seq", 0), "after_seq", 0, 2**63 - 1)
                limit = integer(payload.get("limit", 100), "limit", 1, 100)
                rows = self.db.execute("SELECT body FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?",
                                       (task["task_id"], after, limit)).fetchall()
                oldest = self.db.execute("SELECT min(seq) FROM events WHERE task_id=?", (task["task_id"],)).fetchone()[0]
                return {"events": [json.loads(r[0]) for r in rows], "truncated": bool(oldest and after < oldest - 1),
                        "oldest_seq": oldest or task["last_seq"] + 1, "last_seq": task["last_seq"]}
            if operation == "artifact":
                if task.get("artifacts_expired"):
                    raise RunnerError("artifact_expired", "Approved remote retention period expired")
                if task["status"] != "succeeded" or not task["exit_acknowledged"]:
                    raise RunnerError("artifact_unavailable", "Task has no published artifacts")
                self._verify_artifacts(task, task["artifacts"])
                artifact_id = payload.get("artifact_id")
                entries = task["artifacts"]["files"]
                if artifact_id:
                    entries = [f for f in entries if f["relative_path"] == artifact_id]
                    if not entries:
                        raise RunnerError("artifact_unavailable", "Artifact is not published by this task")
                return {**task["artifacts"], "files": entries}
            raise RunnerError("invalid_request", "Unknown operation")

    def reserve(self, p):
        allowed = {"phase", "idempotency_key", "plan", "plan_hash", "budget", "input_manifest", "expires_at", "grant_ref", "retention_seconds"}
        if set(p) - allowed:
            raise RunnerError("invalid_request", "Unknown reserve fields")
        key = token(p.get("idempotency_key"), "idempotency_key")
        request_hash = digest(p)
        row = self.db.execute("SELECT body, request_hash FROM tasks WHERE idem=?", (key,)).fetchone()
        if row:
            if row[1] != request_hash:
                raise RunnerError("idempotency_conflict", "Idempotency key already binds different content")
            return self._public(json.loads(row[0]))
        if not self.capabilities.get("ready"):
            raise RunnerError("runner_incompatible", "Configured compute device is unavailable")
        b = validate_budget(p.get("budget"))
        retention = integer(p.get("retention_seconds", 86400), "retention_seconds", 60, 604800)
        if self.db.execute("SELECT count(*) FROM tasks").fetchone()[0] >= 4096 or sum(t["status"] not in TERMINAL for t in self._all()) >= 64:
            raise RunnerError("resource_busy", "Runner task registry requires administrator archival")
        plan = p.get("plan", {})
        if not isinstance(plan, dict) or set(plan) != {"family", "params", "kernel_version", "device"}:
            raise RunnerError("invalid_request", "Expected fixed builtin training plan")
        if plan["kernel_version"] != KERNEL_VERSION or plan["device"] != self.device:
            raise RunnerError("runner_incompatible", "Kernel version or allocated device differs")
        plan = {**plan, "params": validate_params(plan["family"], plan["params"])}
        manifest = validate_manifest(p.get("input_manifest"), b)
        token(p.get("plan_hash"), "plan_hash", 64)
        token(p.get("grant_ref"), "grant_ref", 128)
        expires = p.get("expires_at")
        if type(expires) not in (float, int) or not time.time() < expires <= time.time() + b["max_run_seconds"] + 5:
            raise RunnerError("deadline_expired", "Deadline must be finite and within approved runtime")
        if shutil.disk_usage(self.root).free < sum(f["size_bytes"] for f in manifest["files"]) + b["max_output_bytes"] + b["temp_bytes"]:
            raise RunnerError("budget_exceeded", "Insufficient disk for input, scratch and output reservations")
        # Reserve aggregate disk as well as checking this task's requested space.
        reserved = sum(sum(f["size_bytes"] for f in t["input_manifest"]["files"]) + t["budget"]["max_output_bytes"] + t["budget"]["temp_bytes"]
                       for t in self._all() if t["status"] not in TERMINAL)
        if shutil.disk_usage(self.root).free < reserved + b["max_input_bytes"] + b["max_output_bytes"] + b["temp_bytes"]:
            raise RunnerError("resource_busy", "Other active tasks reserve the available disk")
        identifier = uuid.uuid4().hex
        directory = self.root / "tasks" / identifier
        inputs = directory / "input"
        inputs.mkdir(parents=True, mode=0o700)
        for item in manifest["files"]:
            safe_path(inputs, item["relative_path"]).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        uploads = {"root": rpc_path(inputs), "files": [dict(f, remote_path=rpc_path(inputs / f["relative_path"])) for f in manifest["files"]]}
        t = dict(task_id=identifier, runner_instance_id=self.instance_id, idempotency_key=key, plan=plan,
                 plan_hash=p["plan_hash"], manifest_hash=digest(manifest), input_manifest=manifest, budget=b,
                 grant_ref=p["grant_ref"], expires_at=expires, status="reserved", attempt_id="", process_identity=None,
                 descendants=[], exit_acknowledged=False, slot_owned=False, cancel_requested=False,
                 last_seq=0, error=None, upload_manifest=uploads, artifacts=None, usage={}, launch_at=None,
                 retain_until=expires + retention, retention_seconds=retention, artifacts_expired=False)
        if len(canonical(self._public(t))) > MAX_FRAME - 65536:
            shutil.rmtree(directory)
            raise RunnerError("frame_too_large", "Task allocation leaves insufficient room for bounded status/artifact responses")
        self.db.execute("INSERT INTO tasks VALUES (?,?,?,?)", (identifier, key, request_hash, canonical(t).decode()))
        self._event(t, "reserved")
        self.deadlines[identifier] = time.monotonic() + (expires - time.time())
        return self._public(t)

    def commit(self, p):
        if set(p) - {"phase", "task_id", "manifest_hash", "grant_ref"}:
            raise RunnerError("invalid_request", "Unknown commit fields")
        with self.lock:
            t = self._task(p)
            if p.get("manifest_hash") != t["manifest_hash"] or p.get("grant_ref") != t["grant_ref"]:
                raise RunnerError("idempotency_conflict", "Commit is not bound to reserved input and grant")
            if t["status"] != "reserved":
                return self._public(t)
            if time.time() >= t["expires_at"]:
                raise RunnerError("deadline_expired", "Task deadline expired before commit")
            t["status"] = "verifying"
            self._event(t, "input_verifying")
        def check():
            with self.lock:
                latest = self._task({"task_id": t["task_id"]})
                if latest["cancel_requested"]:
                    raise RunnerError("canceled", "Input verification was canceled")
                if time.time() >= latest["expires_at"] or time.monotonic() >= self.deadlines[t["task_id"]]:
                    raise RunnerError("deadline_expired", "Input verification deadline expired")
                if latest["status"] != "verifying":
                    raise RunnerError("canceled", "Task left input verification")
        try:
            # Hash blocks outside the service lock. Cancel/status/tick can persist
            # and enforce deadlines while a large upload is being verified.
            verify_files(self._directory(t) / "input", t["input_manifest"], t["budget"], cancel=check)
            check()
        except BaseException:
            with self.lock:
                latest = self._task({"task_id": t["task_id"]})
                if latest["status"] == "verifying":
                    latest["status"] = "reserved"
                    self._save(latest)
            raise
        with self.lock:
            latest = self._task({"task_id": t["task_id"]})
            if latest["status"] == "verifying":
                latest["status"] = "queued"
                self._event(latest, "input_verified")
            return self._public(latest)

    def _worker_identity(self, t):
        path = self._directory(t) / "identity.json"
        if not path.exists():
            return None
        value = read_json(path)
        if value.get("attempt_id") != t["attempt_id"]:
            return None
        candidate = value.get("identity")
        if not alive(candidate):
            return candidate  # Still useful as proof that a launched worker exited.
        import psutil
        try:
            argv = psutil.Process(candidate["pid"]).cmdline()
            if "remote_runner.torch_worker" not in argv or t["attempt_id"] not in argv or str(self._directory(t)) not in argv:
                raise RunnerError("remote_state_unknown", "PID identity does not match the owned attempt")
        except psutil.NoSuchProcess:
            return candidate
        except psutil.AccessDenied as exc:
            raise RunnerError("remote_state_unknown", "Cannot inspect worker identity") from exc
        return candidate

    def _external_gpu_busy(self, t=None):
        if self.device == "cpu":
            return False
        gpu = self.capabilities.get("gpu_uuid", "")
        if not gpu:
            return True  # Refuse uninspectable shared CUDA occupancy.
        try:
            result = subprocess.run(["nvidia-smi", "--query-compute-apps=gpu_uuid,pid", "--format=csv,noheader,nounits"],
                                    capture_output=True, timeout=3, text=True)
            if result.returncode:
                return True
            owned = {x["pid"] for x in ([t.get("process_identity")] + t.get("descendants", [])) if x} if t else set()
            for row in result.stdout.splitlines():
                fields = [s.strip() for s in row.split(",")]
                if len(fields) == 2 and fields[0] == gpu and int(fields[1]) not in owned:
                    return True
            return False
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return True

    def _launch(self, t):
        directory = self._directory(t)
        t.update(status="starting", attempt_id=uuid.uuid4().hex, slot_owned=True, launch_at=time.time())
        self._event(t, "slot_acquired", device=self.device)
        (directory / "scratch").mkdir(exist_ok=True)
        (directory / "output").mkdir(exist_ok=True)
        atomic_json(directory / "spec.json", {"attempt_id": t["attempt_id"], "plan": t["plan"], "budget": t["budget"],
                                               "input_manifest": t["input_manifest"], "expires_at": t["expires_at"],
                                               "plan_hash": t["plan_hash"], "task_id": t["task_id"], "runner_instance_id": self.instance_id})
        args = [sys.executable, "-m", "remote_runner.torch_worker", "--root", str(directory), "--attempt", t["attempt_id"]]
        kwargs = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        try:
            process = subprocess.Popen(args, cwd=PACKAGE_ROOT, env=trusted_environment(), stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
            self.processes[t["task_id"]] = process
            t["process_identity"] = identity(process.pid)
            self._save(t)
            atomic_json(directory / "start.json", {"attempt_id": t["attempt_id"]})
        except OSError:
            # Popen raised before creating a child; this is a proven no-worker case.
            t.update(status="failed", slot_owned=False, exit_acknowledged=True,
                     error={"code": "worker_launch_failed", "message": "Unable to start builtin worker"})
            self._event(t, "failed", error=t["error"])

    def _observe(self, t):
        import psutil
        p = t["process_identity"]
        if not alive(p):
            return
        try:
            leader = psutil.Process(p["pid"])
            children = leader.children(recursive=True)
            known = {(v["pid"], v["birth"]): v for v in t["descendants"]}
            for child in children:
                child_id = identity(child.pid)
                known[(child_id["pid"], child_id["birth"])] = child_id
            t["descendants"] = list(known.values())
            processes = [leader, *children]
            rss = sum(x.memory_info().rss for x in processes if x.is_running())
            cpu = sum(x.cpu_times().user + x.cpu_times().system for x in processes if x.is_running())
            t["usage"].update(rss_bytes=rss, cpu_seconds=cpu, enforcement="monitored;may-overshoot")
            if rss > t["budget"]["rss_bytes"] or cpu > t["budget"]["max_cpu_seconds"]:
                t["error"] = {"code": "budget_exceeded", "message": "Worker RSS/CPU budget exceeded"}
                t["status"] = "canceling"
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            t.update(status="unknown", error={"code": "remote_state_unknown", "message": "Worker cannot be inspected"})

    def _signal_owned(self, t):
        import psutil
        t.setdefault("cancel_at", time.time())
        if time.time() - t["cancel_at"] < .5:
            return  # Give the worker's persisted cancel watchdog a cooperative interval.
        # Kill only birth-verified owned processes. No naked PID or SSH-channel kill.
        for item in reversed([t.get("process_identity"), *t["descendants"]]):
            if not alive(item):
                continue
            try:
                proc = psutil.Process(item["pid"])
                if abs(proc.create_time() - item["birth"]) >= .001:
                    continue
                if time.time() - t.setdefault("cancel_at", time.time()) > 2:
                    proc.kill()
                else:
                    proc.terminate()
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                t["error"] = {"code": "remote_state_unknown", "message": "Owned process exit is not yet confirmed"}

    def _verify_artifacts(self, t, manifest):
        if not isinstance(manifest, dict) or manifest.get("kernel_version") != KERNEL_VERSION:
            raise RunnerError("artifact_corrupt", "Invalid output manifest")
        entries = manifest.get("files", [])
        if not entries or len(entries) > min(8, t["budget"]["max_files"]):
            raise RunnerError("artifact_corrupt", "Invalid output file count")
        root = self._directory(t) / "output"
        total, names = 0, set()
        for f in entries:
            name = f["relative_path"]
            if name not in ARTIFACT_NAMES or name in names:
                raise RunnerError("artifact_corrupt", "Unapproved output format/name")
            names.add(name)
            path = safe_path(root, name, exists=True)
            sha, size = file_sha256(path, max_bytes=t["budget"]["max_output_bytes"])
            if sha != f["sha256"] or size != f["size_bytes"]:
                raise RunnerError("artifact_corrupt", "Published output hash/size changed")
            total += size
            if name.endswith(".safetensors"):
                validate_safetensors(path, t["budget"]["max_output_bytes"])
            if name.endswith(".json"):
                read_json(path)
        if total > t["budget"]["max_output_bytes"] or not {"model.safetensors", "model.json", "metrics.json"} <= names:
            raise RunnerError("artifact_corrupt", "Incomplete or oversized model bundle")

    def tick(self):
        with self.lock:
            tasks = self._all()
            for t in tasks:
                if t["status"] in TERMINAL:
                    if t["exit_acknowledged"] and not t.get("artifacts_expired") and time.time() >= t["retain_until"]:
                        for folder in ("input", "scratch", "output"):
                            path = safe_path(self._directory(t), folder)
                            if path.exists():
                                shutil.rmtree(path)
                        t["artifacts_expired"] = True
                        self._event(t, "retention_expired")
                    continue
                directory = self._directory(t)
                expired = time.time() >= t["expires_at"] or time.monotonic() >= self.deadlines[t["task_id"]]
                if expired:
                    t.update(status="canceling", error={"code": "deadline_expired", "message": "Durable task deadline expired"})
                if not t["attempt_id"]:
                    if t["status"] == "canceling":
                        t.update(status="canceled" if t["cancel_requested"] else "failed", slot_owned=False, exit_acknowledged=True)
                        self._event(t, t["status"])
                    continue
                try:
                    if t["process_identity"] is None:
                        t["process_identity"] = self._worker_identity(t)
                        if t["process_identity"] is None:
                            raise RunnerError("remote_state_unknown", "Launch identity is not yet proven")
                    # Verify the independently written registration on restart, too.
                    if (directory / "identity.json").exists():
                        registered = self._worker_identity(t)
                        if registered and registered != t["process_identity"]:
                            raise RunnerError("remote_state_unknown", "Worker birth identity changed")
                except RunnerError as exc:
                    t.update(status="unknown", error={"code": "remote_state_unknown", "message": str(exc)[:512]})
                    self._save(t)
                    continue
                if t["status"] == "unknown" and (t.get("error") or {}).get("code") == "remote_state_unknown":
                    t.update(status="canceling" if t["cancel_requested"] or expired else "starting", error=None)
                    self._event(t, "ownership_reconciled", attempt_id=t["attempt_id"])
                self._observe(t)
                if alive(t["process_identity"]) and not (directory / "start.json").exists() and not expired:
                    atomic_json(directory / "start.json", {"attempt_id": t["attempt_id"]})
                if t["status"] not in {"canceling", "unknown"} and alive(t["process_identity"]):
                    if t["status"] != "running":
                        t["status"] = "running"
                        self._event(t, "running", attempt_id=t["attempt_id"])
                    if self._external_gpu_busy(t):
                        t.update(status="canceling", error={"code": "gpu_external_occupancy", "message": "External GPU occupancy detected"})
                try:
                    scratch, count = _disk_usage(directory / "scratch")
                    output, output_files = _disk_usage(directory / "output")
                    t["usage"].update(temp_bytes=scratch, output_bytes=output)
                    if scratch > t["budget"]["temp_bytes"] or output > t["budget"]["max_output_bytes"] or output_files > min(8, t["budget"]["max_files"]):
                        t.update(status="canceling", error={"code": "budget_exceeded", "message": "Worker disk budget exceeded"})
                except RunnerError as exc:
                    t.update(status="canceling", error={"code": exc.code, "message": str(exc)})
                if t["status"] == "canceling":
                    atomic_json(directory / "cancel.json", {"attempt_id": t["attempt_id"]})
                    self._signal_owned(t)
                progress_path = directory / "progress.json"
                if progress_path.exists():
                    progress = read_json(progress_path, 4096)
                    if progress.get("attempt_id") == t["attempt_id"] and progress.get("counter", 0) > t.get("progress_counter", 0):
                        t["progress_counter"] = progress["counter"]
                        self._event(t, "progress", progress=progress.get("progress", 0), message=str(progress.get("message", ""))[:512])
                if not any(alive(x) for x in [t["process_identity"], *t["descendants"]]):
                    process = self.processes.pop(t["task_id"], None)
                    if process:
                        process.wait(timeout=1)
                    outcome_path = directory / "outcome.json"
                    outcome = read_json(outcome_path) if outcome_path.exists() else {}
                    if t["cancel_requested"]:
                        status = "canceled"
                    elif t["error"]:
                        status = "failed"
                    elif outcome.get("attempt_id") != t["attempt_id"]:
                        status = "failed"
                        t["error"] = {"code": "worker_lost", "message": "Worker exited without a matching durable outcome"}
                    elif outcome.get("ok"):
                        try:
                            manifest = outcome["artifacts"]
                            self._verify_artifacts(t, manifest)
                            manifest["root"] = rpc_path(directory / "output")
                            manifest["files"] = [dict(f, remote_path=rpc_path(directory / "output" / f["relative_path"])) for f in manifest["files"]]
                            t["artifacts"], status = manifest, "succeeded"
                        except RunnerError as exc:
                            status, t["error"] = "failed", {"code": exc.code, "message": str(exc)}
                    else:
                        status = "failed"
                        t["error"] = outcome.get("error") or {"code": "worker_failed", "message": "Builtin worker failed"}
                    t.update(status=status, exit_acknowledged=True, slot_owned=False)
                    self._event(t, status, exit_acknowledged=True)
                else:
                    self._save(t)
            tasks = self._all()
            if not any(t["slot_owned"] for t in tasks):
                for t in tasks:
                    if t["status"] == "queued" and not self._external_gpu_busy():
                        self._launch(t)
                        break

    def close(self):
        if not self._closed:
            self._closed = True
            self.db.close()
            self.instance_lock.close()


class _RpcServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(20)
        for _ in range(1000):
            raw = self.rfile.readline(MAX_FRAME + 2)
            if not raw:
                return
            request_id = "invalid"
            try:
                frame = decode_frame(raw)
                request_id = frame["request_id"]
                if not hmac.compare_digest(str(frame.pop("auth", "")), self.server.secret):
                    raise RunnerError("permission_denied", "Invalid local supervisor authentication")
                result = self.server.supervisor.rpc(frame["operation"], frame.get("payload", {}))
                out = response(request_id, result)
            except RunnerError as exc:
                out = response(request_id, error=exc)
            except Exception:
                out = response(request_id, error=RunnerError("internal_error", "Supervisor could not complete request"))
            self.wfile.write(out + b"\n")
            self.wfile.flush()
            if len(raw) > MAX_FRAME:
                return


def serve(root=None, *, device="cuda:0"):
    supervisor = Supervisor(root, device=device)
    secret = secrets.token_hex(32)
    server = _RpcServer(("127.0.0.1", 0), _Handler)
    server.supervisor, server.secret = supervisor, secret
    endpoint = supervisor.root / "service.json"
    atomic_json(endpoint, {"port": server.server_address[1], "secret": secret, "identity": identity(), "generation": supervisor.generation})
    if os.name != "nt":
        os.chmod(endpoint, 0o600)
    stopped = threading.Event()
    def stop(*_):
        stopped.set()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        while not stopped.wait(.1):
            supervisor.tick()
    finally:
        server.shutdown()
        server.server_close()
        supervisor.close()  # Workers retain watchdogs; new service adopts identities.


def proxy_frame(raw, root=None):
    frame = decode_frame(raw)
    endpoint = read_json(Path(root or default_root()) / "service.json", 4096)
    if not alive(endpoint.get("identity")):
        raise RunnerError("supervisor_unavailable", "Persistent runner service is not running")
    frame["auth"] = endpoint["secret"]
    encoded = canonical(frame)
    if len(encoded) > MAX_FRAME:
        raise RunnerError("frame_too_large", "Authenticated RPC exceeds frame limit")
    try:
        with socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=20) as sock:
            sock.sendall(encoded + b"\n")
            with sock.makefile("rb") as stream:
                result = stream.readline(MAX_FRAME + 1)
        if not result or len(result) > MAX_FRAME:
            raise RunnerError("protocol_error", "Supervisor returned an invalid frame")
        return result.rstrip(b"\n")
    except OSError as exc:
        raise RunnerError("supervisor_unavailable", "Persistent runner service is unreachable") from exc
