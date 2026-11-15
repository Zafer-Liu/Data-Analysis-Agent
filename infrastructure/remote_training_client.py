"""Scoped transfer approvals and a durable client for the actual runner RPCv1.

No Torch import, implicit external transfer, client-supplied commands or automatic
installation. Unknown acknowledgements are reconciled using the original key.
"""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time
import uuid

from data.remote_execution_store import RemoteExecutionError, RemoteExecutionStore, canonical_hash
from infrastructure.remote_training_transfer import SftpTransfer, file_sha256, local_file
from remote_runner.protocol import KERNEL_VERSION, MAX_FRAME, RunnerError, canonical, validate_budget, validate_manifest, validate_params
from remote_runner.safe_io import atomic_json, read_json, validate_safetensors

_TERMINAL = {"succeeded", "failed", "canceled"}
_ALLOWED_OUTPUTS = {"model.safetensors", "model.json", "metrics.json", "predictions.parquet", "table0.parquet", "table1.parquet", "table2.parquet"}


def _error(code, message):
    raise RemoteExecutionError(code, message)


def reserve_payload(plan, run):
    return {"phase": "reserve", "idempotency_key": run["remote_idempotency_key"], "plan_hash": plan["plan_hash"],
            "plan": {"family": plan["family"], "params": plan["params"], "kernel_version": KERNEL_VERSION, "device": plan["device"]},
            "budget": plan["budget"], "input_manifest": plan["manifest"], "expires_at": run["deadline_at"],
            "grant_ref": run["grant_hash"], "retention_seconds": plan["retention_seconds"]}


def public_record(value):
    """UI records contain identities and bounded metadata, never server paths/secrets."""
    hidden = {"reserve_payload", "upload_manifest", "artifacts", "transfers", "root", "remote_path", "input_path",
              "credential_ref", "connection_binding", "manifest", "local_root", "absolute_path", "source"}
    if isinstance(value, dict):
        return {k: public_record(v) for k, v in value.items() if k not in hidden}
    if isinstance(value, list):
        return [public_record(v) for v in value]
    return value


class SshTrainingTransport:
    def __init__(self, connection, *, checkpoint=None):
        from infrastructure.ssh_tunnel_manager import REMOTE_RUNNER_RPC_COMMAND, fingerprint, open_trusted_ssh_client
        self.client = open_trusted_ssh_client(connection)
        self.lock = threading.Lock()
        self._closed = False
        self.host_fingerprint = fingerprint(self.client.get_transport().get_remote_server_key())
        try:
            self.stdin, self.stdout, self.stderr = self.client.exec_command(REMOTE_RUNNER_RPC_COMMAND, timeout=20)
            self.sftp = self.client.open_sftp()
            self.sftp.get_channel().settimeout(20)
            self.transfer = SftpTransfer(self.sftp, max_bytes=1024**4, max_files=1024, checkpoint=checkpoint)
            # Drain stderr without collecting it, preventing SSH window deadlock.
            def drain():
                try:
                    while not self._closed and self.stderr.read(4096):
                        pass
                except (OSError, EOFError):
                    pass
            threading.Thread(target=drain, name="runner-stderr-drain", daemon=True).start()
        except Exception:
            self.client.close()
            raise

    def rpc(self, operation, payload):
        request_id = uuid.uuid4().hex
        raw = canonical({"version": 1, "request_id": request_id, "operation": operation, "payload": payload})
        if len(raw) > MAX_FRAME - 128:
            _error("frame_too_large", "RPC request exceeds bounded protocol frame")
        with self.lock:
            self.stdin.write(raw + b"\n")
            self.stdin.flush()
            answer = self.stdout.readline(MAX_FRAME + 1)
        if not answer or len(answer) > MAX_FRAME:
            _error("remote_state_unknown", "Runner did not return a bounded response")
        try:
            data = json.loads(answer)
        except (ValueError, UnicodeError) as exc:
            raise RemoteExecutionError("protocol_error", "Runner returned invalid JSON") from exc
        if data.get("version") != 1 or data.get("request_id") != request_id:
            _error("protocol_error", "Runner response identity/version differs")
        if not data.get("ok"):
            _error(str(data.get("code", "remote_error")), str(data.get("error", "Runner rejected request"))[:1000])
        result = data.get("result")
        if operation == "submit" and isinstance(result, dict) and result.get("upload_manifest"):
            alloc = result["upload_manifest"]
            self.transfer.authorize_uploads(alloc["root"], alloc["files"])
        if operation == "artifact" and isinstance(result, dict):
            self.transfer.authorize_downloads(result["root"], result["files"])
        return result

    def upload(self, *args, **kwargs):
        return self.transfer.upload(*args, **kwargs)

    def download(self, *args, **kwargs):
        return self.transfer.download(*args, **kwargs)

    def close(self):
        self._closed = True
        self.client.close()


class RemoteTrainingService:
    def __init__(self, root=None, *, dataset_catalog=None, connection_resolver=None, transport_factory=None):
        self.store = RemoteExecutionStore(root)
        self.root = self.store.root
        self.dataset_catalog = dataset_catalog
        if connection_resolver is None:
            from infrastructure.gpu_connections import get_connection
            connection_resolver = get_connection
        self.connection_resolver = connection_resolver
        self.transport_factory = transport_factory or SshTrainingTransport
        self.job_runners, self._cancel_callbacks = {}, {}
        self._job_runner_lock = threading.RLock()
        self._closed = False

    def _catalog(self, scope):
        if self.dataset_catalog is None:
            from data.dataset_catalog import DatasetCatalog
            return DatasetCatalog(**scope)
        return self.dataset_catalog(**scope) if callable(self.dataset_catalog) else self.dataset_catalog

    def _handle(self, plan, scope, *, current=False):
        catalog = self._catalog(scope)
        try:
            handle = catalog.get(plan["dataset_id"], snapshot_id=plan["snapshot_id"], verify=True)
            if current and plan.get("bind_active_snapshot") and catalog.get(plan["dataset_id"]).snapshot_id != plan["snapshot_id"]:
                _error("plan_invalidated", "Active dataset version changed; prepare a new training plan")
            m = handle.manifest
            if any(m.get(key, "") != value for key, value in scope.items()):
                _error("permission_denied", "Dataset snapshot is outside this owner/session/workspace")
            if m.get("status") != "ready":
                _error("snapshot_unavailable", "Training requires a complete immutable ready snapshot")
            if canonical_hash(m) != plan["manifest_hash"]:
                _error("plan_invalidated", "Snapshot metadata changed after planning")
            # Even controlled catalogs must not bypass full file verification.
            for f in m["files"]:
                path = local_file(handle.root, f["relative_path"])
                if path.stat().st_size != f["size_bytes"] or file_sha256(path) != f["sha256"]:
                    _error("dataset_changed", "Input snapshot bytes changed")
            return handle
        finally:
            if self.dataset_catalog is None and hasattr(catalog, "close"):
                catalog.close()

    @staticmethod
    def _scope(owner_id, session_id, workspace_id=""):
        a, b, c = RemoteExecutionStore.scope(owner_id, session_id, workspace_id)
        return {"owner_id": a, "session_id": b, "workspace_id": c}

    def _connect(self, connection_id):
        from infrastructure.paths import is_cloud_managed_runtime
        if is_cloud_managed_runtime():
            _error("permission_denied", "Remote training requires tenant-isolated SSH configuration; shared hosted configuration is disabled")
        connection = self.connection_resolver(connection_id)
        if not connection or connection.get("connection_type") != "ssh" or connection.get("purpose", "inference") not in {"training", "both"}:
            _error("connection_unavailable", "Select an existing SSH training connection")
        if any(k in connection for k in ("password", "command", "env", "module")):
            _error("invalid_connection", "Connection may contain credential references and fixed runner configuration only")
        try:
            transport = self.transport_factory(connection)
        except RemoteExecutionError:
            raise
        except Exception as exc:
            raise RemoteExecutionError("connection_unavailable", "SSH training connection failed; verify the trusted host key and OS credential reference") from exc
        try:
            fingerprint = getattr(transport, "host_fingerprint", "")
            if not fingerprint.startswith("SHA256:") or (connection.get("host_fingerprint") and connection["host_fingerprint"] != fingerprint):
                _error("host_key_changed", "Connected SSH fingerprint differs from the trusted training host")
            handshake = transport.rpc("handshake", {})
            if (not isinstance(handshake, dict) or handshake.get("protocol_version") != 1
                    or handshake.get("kernel_version") != KERNEL_VERSION
                    or not handshake.get("runner_instance_id") or not handshake.get("ready")
                    or handshake.get("expires_at", 0) <= time.time()
                    or not {"Torch_MLP", "Torch_RNN"} <= set(handshake.get("families", []))):
                _error("runner_incompatible", "Fresh runner handshake does not support the fixed kernels")
            binding = {k: connection.get(k) for k in ("id", "host", "port", "username", "purpose", "auth_method", "credential_ref", "key_file")}
            binding.update(host_fingerprint=fingerprint, runner_instance_id=handshake["runner_instance_id"],
                           kernel_version=handshake["kernel_version"], device=handshake["device"])
            return transport, handshake, binding
        except BaseException:
            transport.close()
            raise

    @staticmethod
    def _assert_binding(plan, binding):
        if binding != plan["connection_binding"]:
            _error("plan_invalidated", "Host, credentials, runner instance or compute target changed after planning")

    def plan(self, payload, *, owner_id, session_id, workspace_id=""):
        scope = self._scope(owner_id, session_id, workspace_id)
        allowed = {"family", "params", "budget", "dataset_id", "snapshot_id", "connection_id", "retention_seconds"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            _error("invalid_request", "Plan accepts dataset, builtin parameters, budget and a configured connection only")
        family = payload.get("family")
        params, budget = validate_params(family, payload.get("params", {})), validate_budget(payload.get("budget"))
        catalog = self._catalog(scope)
        try:
            handle = catalog.get(payload.get("dataset_id"), snapshot_id=payload.get("snapshot_id", ""), verify=True)
            manifest = validate_manifest(handle.manifest, budget)
            for name, val in scope.items():
                if manifest.get(name, "") != val:
                    _error("permission_denied", "Dataset is not in the requested training scope")
            if manifest.get("status") != "ready":
                _error("snapshot_unavailable", "Only ready immutable snapshots can train")
            plan = dict(id=uuid.uuid4().hex, **scope, family=family, params=params, budget=budget,
                        connection_id=str(payload.get("connection_id") or ""), dataset_id=manifest["dataset_id"],
                        snapshot_id=manifest["snapshot_id"], manifest=manifest, manifest_hash=canonical_hash(manifest),
                        bind_active_snapshot=not bool(payload.get("snapshot_id")), created_at=time.time(), expires_at=time.time() + 900)
        finally:
            if self.dataset_catalog is None and hasattr(catalog, "close"):
                catalog.close()
        self._handle(plan, scope)
        transport, handshake, binding = self._connect(plan["connection_id"])
        try:
            retention = payload.get("retention_seconds", 86400)
            if type(retention) is not int or not 60 <= retention <= 604800:
                _error("invalid_parameter", "retention_seconds must be in [60,604800]")
            plan.update(connection_binding=binding, runner_instance_id=handshake["runner_instance_id"],
                        host_fingerprint=binding["host_fingerprint"], device=handshake["device"], retention_seconds=retention,
                        target={key: binding[key] for key in ("host", "port", "username", "host_fingerprint", "runner_instance_id", "device")},
                        row_count=manifest["row_count"], input_bytes=sum(f["size_bytes"] for f in manifest["files"]),
                        columns=[f["name"] for f in (manifest.get("schema", {}).get("fields", []) if isinstance(manifest.get("schema"), dict) else manifest.get("schema", []))])
            plan["plan_hash"] = canonical_hash(plan)
            estimate = reserve_payload(plan, {"remote_idempotency_key": "0" * 64, "grant_hash": "0" * 64,
                                              "deadline_at": time.time() + budget["max_run_seconds"]})
            if len(canonical({"version": 1, "request_id": "0" * 32, "operation": "submit", "payload": estimate})) > MAX_FRAME - 128:
                _error("frame_too_large", "Input manifest cannot fit the fixed runner RPC; prepare a bounded snapshot manifest")
            plan["plan_id"] = plan["id"]
            return public_record(self.store.put("plan", plan))
        finally:
            transport.close()

    def approve(self, plan_id, *, owner_id, session_id, workspace_id="", expected_plan_hash):
        scope = self._scope(owner_id, session_id, workspace_id)
        plan = self.store.get("plan", plan_id, **scope)
        if plan["plan_hash"] != expected_plan_hash:
            _error("plan_invalidated", "Approval hash differs from the displayed plan")
        if plan["expires_at"] <= time.time():
            _error("plan_expired", "Training plan expired")
        self._handle(plan, scope, current=True)
        transport, _handshake, binding = self._connect(plan["connection_id"])
        try:
            self._assert_binding(plan, binding)
            grant = dict(id=uuid.uuid4().hex, **scope, plan_id=plan_id, plan_hash=plan["plan_hash"],
                         manifest_hash=plan["manifest_hash"], connection_binding=binding, created_at=time.time(),
                         expires_at=min(plan["expires_at"], time.time() + 600),
                         deadline_at=time.time() + plan["budget"]["max_run_seconds"], run_id="")
            grant["grant_hash"] = canonical_hash(grant)
            grant["grant_id"] = grant["id"]
            return public_record(self.store.put("grant", grant))
        finally:
            transport.close()

    def attach_job_runner(self, run_id, job_runner):
        with self._job_runner_lock:
            self.job_runners[run_id] = job_runner

    def detach_job_runner(self, job_runner):
        """Release a temporary recovery bridge without touching remote lifetime."""
        with self._job_runner_lock:
            for run_id, runner in list(self.job_runners.items()):
                if runner is job_runner:
                    self.job_runners.pop(run_id, None)
            for run_id, registered in list(self._cancel_callbacks.items()):
                if registered[0] is job_runner:
                    self._cancel_callbacks.pop(run_id, None)
                    registered[0].unbind_remote_cancel(registered[1], registered[2])

    def _import_job_cancel(self, run, scope, runner):
        job = runner.get_status(run["job_id"]) if run.get("job_id") else None
        if job and (job["status"] in {"canceling", "canceled"}
                    or (job.get("recovery_metadata") or {}).get("cancel_intent") is True):
            # Job cancellation is durable before the network callback. A crash
            # in between must still cancel the original remote task on recovery.
            return self.store.request_cancel(run["id"], scope)
        return run

    def _job(self, run, scope, runner=None):
        if runner is not None:
            if runner.session_id != scope["session_id"]:
                _error("permission_denied", "JobRunner belongs to another session")
            self.attach_job_runner(run["id"], runner)
        runner = self.job_runners.get(run["id"])
        if runner:
            if run["status"] in _TERMINAL:
                if not run.get("job_id"):
                    return run
                job = runner.get_status(run["job_id"])
                if job is None:
                    self.store.acknowledge_job_delivery(run["id"], scope, run["job_id"])
                    return run  # Cleared history is not recreated.
            jid = runner.begin_remote(run["id"], owner_id=scope["owner_id"], workspace_id=scope["workspace_id"], store_root=str(self.root))
            if run.get("job_id") != jid:
                run = self.store.update("run", run["id"], scope=scope, job_id=jid, native_seq=0,
                                        job_delivery_acknowledged=False)
            run = self._import_job_cancel(run, scope, runner)
            with self._job_runner_lock:
                registered = self._cancel_callbacks.get(run["id"])
                if self.job_runners.get(run["id"]) is runner and (registered is None or registered[0] is not runner or registered[1] != jid):
                    if registered:
                        registered[0].unbind_remote_cancel(registered[1], registered[2])
                    callback = lambda: self.cancel(run["id"], **scope)
                    runner.bind_remote_cancel(jid, callback)
                    self._cancel_callbacks[run["id"]] = (runner, jid, callback)
        return run

    def submit(self, plan_id, grant_id, *, idempotency_key, owner_id, session_id, workspace_id="", job_runner=None):
        scope = self._scope(owner_id, session_id, workspace_id)
        plan, grant = self.store.get("plan", plan_id, **scope), self.store.get("grant", grant_id, **scope)
        if grant["plan_id"] != plan_id or grant["plan_hash"] != plan["plan_hash"]:
            _error("plan_invalidated", "Grant does not bind this training plan")
        # Store checks existing key first, so a lost acknowledgement remains
        # queryable even after the original grant expires.
        existing = self.store.submission(idempotency_key, plan, scope)
        if existing:
            run = existing
        else:
            if grant["deadline_at"] <= time.time():
                _error("deadline_expired", "Approved training deadline elapsed before submission")
            if plan["expires_at"] <= time.time() or grant["expires_at"] <= time.time():
                _error("grant_expired", "Training plan/approval expired")
            self._handle(plan, scope, current=True)
            transport, _hello, binding = self._connect(plan["connection_id"])
            try:
                self._assert_binding(plan, binding)
            finally:
                transport.close()
            run, _created = self.store.create_submission(plan, grant, idempotency_key)
        run = self._job(run, scope, job_runner)
        return public_record(self._drive(run["id"], scope))

    def _update(self, run_id, scope, **changes):
        return self.store.update("run", run_id, scope=scope, **changes)

    def _checkpoint_job(self, run, scope):
        runner = self.job_runners.get(run["id"])
        if not runner or not run.get("job_id"):
            return
        job = runner.get_status(run["job_id"])
        if job is None:
            if run["status"] in _TERMINAL:
                self.store.acknowledge_job_delivery(run["id"], scope, run["job_id"])
            return
        run = self._import_job_cancel(self.store.get("run", run["id"], **scope), scope, runner)
        runner.checkpoint_remote(run["job_id"], external_id=run.get("task_id", ""), phase=run["phase"],
                                 metadata={"run_id": run["id"], "owner_id": scope["owner_id"], "workspace_id": scope["workspace_id"],
                                           "store_root": str(self.root),
                                           **({"cancel_intent": True} if run["cancel_intent"] else {}),
                                           "deadline_at": run["deadline_at"], "runner_instance_id": run.get("runner_instance_id"),
                                           "exit_acknowledged": run["exit_acknowledged"], "durable_handoff": True})
        for native_id, native_seq, event in self.store.pending_job_events(run["id"], scope):
            runner.append_remote_event(run["job_id"], native_id, native_seq, event)
            # A crash after the Job append but before this acknowledgement is
            # safe: JobsStore deduplicates the same native identity on replay.
            self.store.acknowledge_job_event(run["id"], scope, native_id, native_seq)
        if run["status"] == "succeeded" and run.get("result"):
            runner.append_remote_event(run["job_id"], "delivery:" + run["id"], 1,
                                       {"type": "artifact_created", "artifact": {"artifact_id": run["result"]["artifact_id"],
                                                                                "run_id": run["id"], "kind": "training_bundle",
                                                                                "files": run["result"].get("files", [])}})
        if run["status"] in _TERMINAL and run["exit_acknowledged"]:
            runner.finish_remote(run["job_id"], run["status"], exit_acknowledged=True,
                                 result=run.get("result"), error=str(run.get("error") or "Remote training failed"),
                                 result_committed=run["status"] == "succeeded" and run["phase"] == "complete")
            job = runner.get_status(run["job_id"])
            if job is None or job["status"] == run["status"]:
                self.store.acknowledge_job_delivery(run["id"], scope, run["job_id"])

    def _drive(self, run_id, scope):
        run = self.store.get("run", run_id, **scope)
        if run_id in self.job_runners:
            run = self._job(run, scope)
        if run["status"] in _TERMINAL:
            self._checkpoint_job(run, scope)
            return self.store.get("run", run_id, **scope)
        claim = self.store.claim(run_id)
        if claim is None:
            return run
        transport = None
        try:
            plan = self.store.get("plan", run["plan_id"], **scope)
            transport, hello, binding = self._connect(run["connection_id"])
            if binding != plan["connection_binding"]:
                _error("remote_state_unknown", "Training target/runner identity changed; the original process exit is unproven")
            if run.get("runner_instance_id") and run["runner_instance_id"] != hello["runner_instance_id"]:
                _error("remote_state_unknown", "Runner instance changed; original process exit is unproven")
            last_renewed = time.monotonic()
            def heartbeat():
                nonlocal last_renewed
                if time.monotonic() - last_renewed >= 10:
                    if not self.store.renew_claim(run_id, claim):
                        _error("recovery_busy", "Another client owns this run reconciliation")
                    last_renewed = time.monotonic()
            def canceled():
                heartbeat()
                latest = self.store.get("run", run_id, **scope)
                return latest["cancel_intent"] or time.time() >= latest["deadline_at"]
            def checkpoint(**value):
                heartbeat()
                self.store.checkpoint(run_id, value, scope)
            if hasattr(transport, "transfer"):
                transport.transfer.max_bytes = max(plan["budget"]["max_input_bytes"], plan["budget"]["max_output_bytes"])
                transport.transfer.max_files = plan["budget"]["max_files"]
                transport.transfer.checkpoint = checkpoint
            if not run.get("reserve_payload"):
                payload = reserve_payload(plan, run)
                run = self._update(run_id, scope, reserve_payload=payload, runner_instance_id=hello["runner_instance_id"])
            identity_query = {"task_id": run["task_id"]} if run["task_id"] else {"idempotency_key": run["remote_idempotency_key"]}
            try:
                remote = transport.rpc("status", identity_query)
            except (RemoteExecutionError, RunnerError) as exc:
                if exc.code != "task_unknown" or run["task_id"]:
                    raise
                if canceled():
                    run = self._update(run_id, scope, status="canceled" if run["cancel_intent"] else "failed", phase="not_submitted",
                                       exit_acknowledged=True, leases_released=True)
                    return run
                self._handle(plan, scope, current=True)
                heartbeat()
                remote = transport.rpc("submit", run["reserve_payload"])
            if remote.get("runner_instance_id") != hello["runner_instance_id"] or remote.get("plan_hash") != plan["plan_hash"] or remote.get("manifest_hash") != plan["manifest_hash"]:
                _error("remote_state_unknown", "Remote task identity does not match persisted plan/input")
            run = self._update(run_id, scope, task_id=remote["task_id"], remote_status=remote["status"], upload_manifest=remote["upload_manifest"],
                               exit_acknowledged=bool(remote.get("exit_acknowledged")))
            identity_query = {"task_id": run["task_id"]}
            if canceled() and remote["status"] not in _TERMINAL:
                remote = transport.rpc("cancel", {**identity_query, "reason": "user" if self.store.get("run", run_id, **scope)["cancel_intent"] else "deadline_expired"})
                run = self._update(run_id, scope, status="canceling", phase="cancel_pending")
            elif remote["status"] == "reserved":
                handle = self._handle(plan, scope)
                alloc = remote["upload_manifest"]
                entries = alloc.get("files", [])
                SftpTransfer(None, max_bytes=plan["budget"]["max_input_bytes"], max_files=plan["budget"]["max_files"]).authorize_uploads(alloc["root"], entries)
                if [{k: f[k] for k in ("relative_path", "size_bytes", "sha256")} for f in entries] != [{k: f[k] for k in ("relative_path", "size_bytes", "sha256")} for f in plan["manifest"]["files"]]:
                    _error("protocol_error", "Reserve upload manifest differs from approved input")
                if hasattr(transport, "transfer"):
                    transport.transfer.authorize_uploads(alloc["root"], entries)
                uploading = self._update(run_id, scope, status="uploading", phase="uploading")
                self._checkpoint_job(uploading, scope)
                for entry in entries:
                    transport.upload(local_file(handle.root, entry["relative_path"]), entry["remote_path"],
                                     sha256=entry["sha256"], cancel=canceled)
                self._handle(plan, scope)
                if canceled():
                    remote = transport.rpc("cancel", {**identity_query, "reason": "user" if self.store.get("run", run_id, **scope)["cancel_intent"] else "deadline_expired"})
                else:
                    heartbeat()
                    self._update(run_id, scope, phase="commit_pending")
                    remote = transport.rpc("submit", {"phase": "commit", "task_id": run["task_id"],
                                                       "manifest_hash": plan["manifest_hash"], "grant_ref": run["grant_hash"]})
            # Stage native bytes and the cursor together before any terminal
            # commit, including reconciliation without an in-memory session.
            run = self.store.get("run", run_id, **scope)
            batch = transport.rpc("events", {**identity_query, "after_seq": run["native_seq"], "limit": 100})
            seq = batch["events"][-1]["seq"] if batch.get("events") else run["native_seq"]
            events = [{**event, "type": "remote_training_event", "native_type": event["type"],
                       "events_truncated": batch.get("truncated", False)} for event in batch.get("events", [])]
            run = self.store.checkpoint_events(run_id, scope, hello["runner_instance_id"] + ":" + run["task_id"], events,
                                               native_seq=seq, remote_status=remote["status"],
                                               exit_acknowledged=bool(remote.get("exit_acknowledged")), usage=remote.get("usage", {}))
            self._checkpoint_job(run, scope)
            state = remote["status"]
            if seq < batch.get("last_seq", seq):
                return self._update(run_id, scope, status="canceling" if run["cancel_intent"] else "running",
                                    phase="events_replay", events_truncated=bool(batch.get("truncated")))
            if state in _TERMINAL and not remote.get("exit_acknowledged"):
                _error("remote_state_unknown", "Remote terminal label has no process exit acknowledgement")
            if state == "succeeded":
                if canceled():
                    run = self._update(run_id, scope, status="canceled", phase="delivery_canceled", leases_released=True)
                else:
                    run = self._deliver(transport, run, plan, scope, canceled)
            elif state in {"failed", "canceled"}:
                run = self._update(run_id, scope, status=state, phase=state, error=remote.get("error"), leases_released=True)
            elif state == "unknown":
                run = self._update(run_id, scope, status="unknown", phase="recovery_pending", error={"code": "remote_state_unknown"})
            else:
                run = self._update(run_id, scope, status="canceling" if run["cancel_intent"] else state,
                                   phase="cancel_pending" if run["cancel_intent"] else state)
            return run
        except Exception as exc:
            run = self.store.get("run", run_id, **scope)
            code = getattr(exc, "code", "remote_state_unknown")
            # A bad download is a local delivery failure after a proven remote
            # exit. All other uncertain network states retain the remote lease.
            known_exit = run["exit_acknowledged"] and code in {"artifact_corrupt", "artifact_expired", "artifact_unavailable", "hash_mismatch", "size_mismatch", "invalid_tensor", "protocol_error"}
            run = self._update(run_id, scope, status="failed" if known_exit else "canceling" if run["cancel_intent"] else "unknown",
                               phase="artifact_failed" if known_exit else "recovery_pending", leases_released=known_exit,
                               error={"code": code, "message": str(exc)[:1000]})
            return run
        finally:
            if transport:
                transport.close()
            self.store.release_claim(run_id, claim)
            self._checkpoint_job(self.store.get("run", run_id, **scope), scope)

    def _deliver(self, transport, run, plan, scope, canceled):
        downloading = self._update(run["id"], scope, status="downloading", phase="downloading")
        self._checkpoint_job(downloading, scope)
        manifest = transport.rpc("artifact", {"task_id": run["task_id"]})
        entries = manifest.get("files", [])
        if manifest.get("kernel_version") != KERNEL_VERSION or not entries or len(entries) > min(8, plan["budget"]["max_files"]):
            _error("artifact_corrupt", "Remote output version/file count is invalid")
        names = [f.get("relative_path") for f in entries]
        if len(set(names)) != len(names) or not set(names) <= _ALLOWED_OUTPUTS or not {"model.safetensors", "model.json", "metrics.json"} <= set(names):
            _error("artifact_corrupt", "Output manifest contains unsupported/missing artifacts")
        if sum(f["size_bytes"] for f in entries) > plan["budget"]["max_output_bytes"]:
            _error("artifact_corrupt", "Output manifest exceeds approved budget")
        SftpTransfer(None, max_bytes=plan["budget"]["max_output_bytes"], max_files=plan["budget"]["max_files"]).authorize_downloads(manifest["root"], entries)
        if hasattr(transport, "transfer"):
            transport.transfer.max_bytes = plan["budget"]["max_output_bytes"]
            transport.transfer.authorize_downloads(manifest["root"], entries)
        directory = self.root / "artifacts" / run["id"]
        directory.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            transport.download(entry["remote_path"], local_file(directory, entry["relative_path"], must_exist=False),
                               sha256=entry["sha256"], size_bytes=entry["size_bytes"], cancel=canceled)
        validate_safetensors(directory / "model.safetensors", plan["budget"]["max_output_bytes"])
        model = read_json(directory / "model.json")
        metrics = read_json(directory / "metrics.json")
        if (model.get("kernel_version") != KERNEL_VERSION or model.get("family") != plan["family"] or model.get("params") != plan["params"]
                or model.get("lineage", {}).get("input_manifest_hash") != plan["manifest_hash"]
                or model.get("lineage", {}).get("plan_hash") != plan["plan_hash"]):
            _error("artifact_corrupt", "Model metadata differs from the approved kernel/input/plan")
        if canceled():
            return self._update(run["id"], scope, status="canceled", phase="delivery_canceled", leases_released=True)
        # Manifest is the publication point. Files before this are private staging;
        # no ArtifactCreated event or succeeded Job is emitted for partial delivery.
        local_manifest = {"version": 1, "run_id": run["id"], **scope, "kernel_version": KERNEL_VERSION,
                          "dataset_id": plan["dataset_id"], "snapshot_id": plan["snapshot_id"], "input_manifest_hash": plan["manifest_hash"],
                          "plan_hash": plan["plan_hash"], "task_id": run["task_id"], "runner_instance_id": run["runner_instance_id"],
                          "files": [{k: f[k] for k in ("relative_path", "size_bytes", "sha256", "format")} for f in entries]}
        result = {"run_id": run["id"], "artifact_id": "remote-training-" + run["id"], "manifest": local_manifest,
                  "markdown": str(metrics.get("markdown", ""))[:16000], "device": model.get("device"), "model_load_verified": False,
                  "files": [{**entry, "url": f"/api/session/{scope['session_id']}/training/runs/{run['id']}/artifacts/{entry['relative_path']}"}
                            for entry in local_manifest["files"]]}
        def register():
            from infrastructure.paths import content_data_path
            if not directory.resolve().is_relative_to(content_data_path().resolve()):
                return False  # Explicit controlled roots retain their scoped manifest.
            from infrastructure.artifact_lifecycle import register_artifact
            for filename in ["manifest.json", *names]:
                register_artifact(directory / filename, artifact_type="training", session_id=scope["session_id"],
                                  workspace_id=scope["workspace_id"], artifact_id=result["artifact_id"] + ":" + filename)
            return True
        return self.store.publish_delivery(run["id"], scope, result, local_manifest, directory / "manifest.json", register=register)

    def artifact(self, run_id, artifact_id, *, owner_id, session_id, workspace_id=""):
        scope = self._scope(owner_id, session_id, workspace_id)
        run = self.store.get("run", run_id, **scope)
        if run["status"] != "succeeded" or artifact_id not in _ALLOWED_OUTPUTS:
            _error("not_found", "Training artifact is not published in this scope")
        entry = next((f for f in run["artifacts"]["files"] if f["relative_path"] == artifact_id), None)
        if not entry:
            _error("not_found", "Artifact is not in the committed training bundle")
        directory = self.root / "artifacts" / run["id"]
        if read_json(local_file(directory, "manifest.json")) != run["artifacts"]:
            _error("artifact_corrupt", "Local published manifest changed")
        path = local_file(directory, artifact_id)
        if path.stat().st_size != entry["size_bytes"] or file_sha256(path) != entry["sha256"]:
            _error("artifact_corrupt", "Local published artifact changed")
        return path, entry

    def status(self, run_id, *, owner_id, session_id, workspace_id=""):
        scope = self._scope(owner_id, session_id, workspace_id)
        return public_record(self._drive(run_id, scope))

    def cancel(self, run_id, *, owner_id, session_id, workspace_id=""):
        scope = self._scope(owner_id, session_id, workspace_id)
        self.store.request_cancel(run_id, scope)
        return public_record(self._drive(run_id, scope))

    def reconcile_pending(self, *, owner_id, session_id, workspace_id="", job_runner=None):
        scope = self._scope(owner_id, session_id, workspace_id)
        result = []
        for run in self.store.list("run", **scope):
            if (run["status"] not in _TERMINAL or (job_runner and run.get("job_id"))
                    or (run.get("job_id") and not run.get("job_delivery_acknowledged"))):
                self._job(run, scope, job_runner)
                result.append(public_record(self._drive(run["id"], scope)))
        return result

    def close(self):
        if not self._closed:
            self._closed = True
            with self._job_runner_lock:
                for runner, jid, callback in self._cancel_callbacks.values():
                    runner.unbind_remote_cancel(jid, callback)
                self._cancel_callbacks.clear()
            self.store.close()
