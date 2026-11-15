"""Owned M3 regressions: real local service/RPC/worker, controlled SFTP, tiny CPU data.

No SSH server, external endpoint, GPU claim, dependency download or large fixture.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from data.dataset_manifest import DatasetHandle
from data.jobs_store import JobsStore
from data.remote_execution_store import RemoteExecutionError, canonical_hash
from infrastructure.remote_training_client import RemoteTrainingService
from infrastructure.remote_training_transfer import BLOCK_BYTES, SftpTransfer, file_sha256
from remote_runner.protocol import KERNEL_VERSION, MAX_FRAME, RunnerError, canonical, decode_frame, digest, validate_budget, validate_params
from remote_runner.safe_io import atomic_json, safe_path, validate_safetensors
from remote_runner.supervisor import Supervisor, alive, identity, proxy_frame, rpc_path

ROOT = Path(__file__).resolve().parents[1]
SCOPE = {"owner_id": "local-default", "session_id": "m3-local", "workspace_id": ""}
FINGERPRINT = "SHA256:" + "A" * 43
BUDGET = {"max_run_seconds": 90, "max_input_bytes": 4 * 1024**2, "max_output_bytes": 4 * 1024**2,
          "temp_bytes": 16 * 1024**2, "rss_bytes": 2 * 1024**3, "batch_bytes": 1024**2,
          "duckdb_memory_bytes": 64 * 1024**2, "threads": 1}


class SnapshotCatalog:
    """Controlled catalog, actual canonical immutable DatasetHandles and file bytes."""
    def __init__(self, root):
        self.root = Path(root)
        self.handles, self.active = {}, {}

    def add(self, frame, *, dataset_id="tiny", scope=SCOPE):
        snapshot = "snap-" + uuid.uuid4().hex
        directory = self.root / snapshot
        directory.mkdir(parents=True)
        path = directory / "data.parquet"
        table = pa.Table.from_pandas(frame, preserve_index=False)
        pq.write_table(table, path, row_group_size=16)
        manifest = {"version": 1, "dataset_id": dataset_id, "snapshot_id": snapshot, **scope,
                    "status": "ready", "schema": [{"name": f.name, "type": str(f.type), "nullable": f.nullable} for f in table.schema],
                    "row_count": len(frame), "source": {"kind": "controlled-local-test"}, "lineage": {}, "schema_policy": {},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "files": [{"relative_path": "data.parquet", "format": "parquet", "size_bytes": path.stat().st_size,
                               "row_count": len(frame), "sha256": file_sha256(path)}]}
        handle = DatasetHandle(manifest, directory)
        self.handles[snapshot], self.active[dataset_id] = handle, snapshot
        return handle

    def get(self, dataset_id, *, snapshot_id="", verify=False):
        handle = self.handles[snapshot_id or self.active[dataset_id]]
        if handle.dataset_id != dataset_id:
            raise KeyError(dataset_id)
        return handle


def mlp_frame(n=80):
    return pd.DataFrame({"x": np.arange(n, dtype=float) / 10, "category": ["a", "b"] * (n // 2),
                         "y": ["no", "yes"] * (n // 2)})


class LocalSftp:
    """SFTP-shaped controlled filesystem, used by the production bounded transfer."""
    def __init__(self):
        self.max_read = self.max_write = 0

    @staticmethod
    def path(value):
        if os.name == "nt" and len(value) > 2 and value[0] == "/" and value[2] == ":":
            value = value[1:]
            if len(value) == 2:
                value += "/"
        return Path(value)

    def lstat(self, value):
        return self.path(value).lstat()

    def open(self, value, mode):
        owner, stream = self, self.path(value).open(mode)
        class Bounded:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                stream.close()
            def read(self, amount=-1):
                if not 0 <= amount <= BLOCK_BYTES:
                    raise AssertionError("Unbounded SFTP read")
                owner.max_read = max(owner.max_read, amount)
                return stream.read(amount)
            def write(self, data):
                if len(data) > BLOCK_BYTES:
                    raise AssertionError("Unbounded SFTP write")
                owner.max_write = max(owner.max_write, len(data))
                return stream.write(data)
            def __getattr__(self, name):
                return getattr(stream, name)
        return Bounded()

    def rename(self, left, right):
        self.path(left).rename(self.path(right))


class ControlledTransport:
    host_fingerprint = FINGERPRINT
    def __init__(self, fixture, connection):
        self.fixture = fixture
        self.transfer = SftpTransfer(fixture.sftp, max_bytes=32 * 1024**2)

    def rpc(self, operation, payload):
        fixture = self.fixture
        if fixture.offline:
            raise OSError("controlled disconnect")
        raw = canonical({"version": 1, "request_id": uuid.uuid4().hex, "operation": operation, "payload": payload})
        value = json.loads(proxy_frame(raw, fixture.runner_root))
        if not value["ok"]:
            raise RemoteExecutionError(value["code"], value["error"])
        result = value["result"]
        fixture.calls.append((operation, copy.deepcopy(payload), copy.deepcopy(result)))
        phase = payload.get("phase")
        if operation == "submit" and fixture.drop_phase == phase:
            fixture.drop_phase = None
            raise OSError("accepted response intentionally dropped")
        return result

    def upload(self, *args, **kwargs):
        return self.transfer.upload(*args, **kwargs)

    def download(self, remote_path, local_path, **kwargs):
        if self.fixture.corrupt_download:
            original = kwargs["sha256"]
            # Preserve manifest metadata and alter the real file only after the
            # artifact RPC verified it, exercising download integrity itself.
            path = self.fixture.sftp.path(remote_path)
            if path.name == "model.safetensors":
                data = bytearray(path.read_bytes())
                data[-1] ^= 1
                path.write_bytes(data)
                self.fixture.corrupt_download = False
        return self.transfer.download(remote_path, local_path, **kwargs)

    def close(self):
        pass


class Fixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="baa-m3-owned-", dir=os.environ.get("BAA_DATA_DIR"))
        self.root = Path(self.temp.name)
        self.runner_root = self.root / "runner"
        self.catalog = SnapshotCatalog(self.root / "datasets")
        self.handle = self.catalog.add(mlp_frame())
        self.connection = {"id": "cpu-controlled", "name": "local controlled CPU", "purpose": "training", "connection_type": "ssh",
                           "host": "controlled.invalid", "port": 22, "username": "test", "auth_method": "agent"}
        self.sftp, self.calls = LocalSftp(), []
        self.offline = self.corrupt_download = False
        self.drop_phase = None
        self.process = None
        self.service = self.new_service()
        try:
            self.start()
        except BaseException:
            # setUp never reaches addCleanup if construction fails.
            self.close()
            raise

    def new_service(self):
        return RemoteTrainingService(self.root / "client", dataset_catalog=self.catalog,
                                     connection_resolver=lambda _id: copy.deepcopy(self.connection),
                                     transport_factory=lambda c: ControlledTransport(self, c))

    def start(self):
        self.log_path = self.root / "service-errors.log"
        with self.log_path.open("ab") as error_log:
            self.process = subprocess.Popen([sys.executable, "-m", "remote_runner.baa_remote_runner", "--serve", "--device", "cpu", "--root", str(self.runner_root)],
                                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=error_log)
        until = time.monotonic() + 35
        while time.monotonic() < until:
            if self.process.poll() is not None:
                raise AssertionError("Local CPU supervisor exited during startup")
            try:
                hello = json.loads(proxy_frame(canonical({"version": 1, "request_id": "startup", "operation": "handshake", "payload": {}}), self.runner_root))
                if hello.get("ok"):
                    capabilities = hello["result"]
                    if not capabilities.get("ready"):
                        raise AssertionError(f"CPU supervisor probe is not ready: {capabilities}")
                    assert capabilities["device"] == "cpu" and capabilities["cuda"] is False
                    return
            except (OSError, RunnerError, ValueError):
                pass
            time.sleep(.05)
        raise AssertionError("Local supervisor did not become ready")

    def rpc(self, operation, payload):
        return ControlledTransport(self, self.connection).rpc(operation, payload)

    def plan(self, *, family="Torch_MLP", params=None, budget=None, dataset_id="tiny"):
        return self.service.plan({"family": family, "params": params or {"target_column": "y", "epochs": 2, "batch_size": 16},
                                  "dataset_id": dataset_id, "connection_id": "cpu-controlled", "budget": budget or BUDGET}, **SCOPE)

    def submit(self, *, key="tiny-run", **kwargs):
        plan = self.plan(**kwargs)
        grant = self.service.approve(plan["id"], expected_plan_hash=plan["plan_hash"], **SCOPE)
        run = self.service.submit(plan["id"], grant["id"], idempotency_key=key, **SCOPE)
        return plan, grant, run

    def wait(self, run, *, timeout=None):
        # The fixture must observe the task's real persisted deadline plus exit
        # grace, rather than declare failure before its allowed runtime expires.
        if timeout is None:
            timeout = max(5, min(180, float(run["deadline_at"]) - time.time() + 15))
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if self.process.poll() is not None:
                raise AssertionError("Supervisor exited: " + self.log_path.read_text(encoding="utf-8")[-4000:])
            run = self.service.status(run["id"], **SCOPE)
            if run["status"] in {"succeeded", "failed", "canceled"}:
                return run
            time.sleep(.1)
        raise AssertionError(f"Run did not settle: {run}")

    def stop(self, *, kill=False):
        if self.process and self.process.poll() is None:
            self.process.kill() if kill else self.process.terminate()
            self.process.wait(timeout=10)

    def close(self):
        try:
            self.offline = False
            if self.process is not None and self.process.poll() is None:
                for run in self.service.store.list("run", **SCOPE):
                    if run.get("task_id"):
                        self.rpc("cancel", {"task_id": run["task_id"]})
                until = time.monotonic() + 8
                while time.monotonic() < until:
                    active = [self.rpc("status", {"task_id": r["task_id"]}) for r in self.service.store.list("run", **SCOPE) if r.get("task_id")]
                    if all(r["exit_acknowledged"] for r in active):
                        break
                    time.sleep(.05)
        finally:
            self.stop()
            self.service.close()
            self.temp.cleanup()


class RemoteM3IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def test_real_rpc_cpu_mlp_safe_weights_parquet_and_late_cancel(self):
        import builtins
        original_import = builtins.__import__
        def without_local_torch(name, *args, **kwargs):
            if name == "torch" or name.startswith("torch."):
                raise AssertionError("Control client imported local Torch")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=without_local_torch):
            plan, grant, run = self.f.submit()
            result = self.f.wait(run)
        self.assertEqual("succeeded", result["status"], result)
        self.assertEqual("cpu", result["result"]["device"])
        self.assertTrue(result["exit_acknowledged"] and result["leases_released"])
        path, _meta = self.f.service.artifact(run["id"], "model.safetensors", **SCOPE)
        from remote_runner.torch_worker import load_model_bundle
        model, metadata = load_model_bundle(path.parent)
        self.assertEqual(KERNEL_VERSION, metadata["kernel_version"])
        self.assertEqual(plan["plan_hash"], metadata["lineage"]["plan_hash"])
        self.assertEqual(plan["snapshot_id"], metadata["lineage"]["snapshot_id"])
        from infrastructure.paths import content_data_path
        self.assertEqual(path.resolve().is_relative_to(content_data_path().resolve()), result["result"]["lifecycle_registered"])
        self.assertEqual("cpu", str(next(model.parameters()).device))
        predictions = pq.read_table(path.parent / "predictions.parquet")
        self.assertGreater(predictions.num_rows, 0)
        repeated = self.f.service.submit(plan["id"], grant["id"], idempotency_key="tiny-run", **SCOPE)
        self.assertEqual(run["id"], repeated["id"])
        self.assertEqual("succeeded", self.f.service.cancel(run["id"], **SCOPE)["status"])
        with self.assertRaises(RemoteExecutionError):
            self.f.service.artifact(run["id"], "model.safetensors", **{**SCOPE, "workspace_id": "other"})
        self.assertLessEqual(self.f.sftp.max_read, BLOCK_BYTES)
        self.assertTrue(all(c[2].get("device") != "cuda" for c in self.f.calls if c[0] == "handshake"))

    def test_lost_reserve_and_commit_replies_reconnect_same_task_and_conflict(self):
        self.f.drop_phase = "reserve"
        plan, grant, run = self.f.submit()
        self.assertEqual("unknown", run["status"])
        self.assertFalse(run["exit_acknowledged"])
        self.f.service.close()
        self.f.service = self.f.new_service()
        self.f.drop_phase = "commit"
        run = self.f.service.status(run["id"], **SCOPE)
        self.assertEqual("unknown", run["status"])
        result = self.f.wait(run)
        self.assertEqual("succeeded", result["status"], result)
        tasks = {c[2]["task_id"] for c in self.f.calls if c[0] == "submit"}
        self.assertEqual(1, len(tasks))
        reserve = next(c[1] for c in self.f.calls if c[0] == "submit" and c[1]["phase"] == "reserve")
        changed = copy.deepcopy(reserve)
        changed["plan"]["params"]["epochs"] += 1
        with self.assertRaisesRegex(RemoteExecutionError, "different content"):
            self.f.rpc("submit", changed)
        new_plan = self.f.plan(params={"target_column": "y", "epochs": 3})
        new_grant = self.f.service.approve(new_plan["id"], expected_plan_hash=new_plan["plan_hash"], **SCOPE)
        with self.assertRaises(RemoteExecutionError) as conflict:
            self.f.service.submit(new_plan["id"], new_grant["id"], idempotency_key="tiny-run", **SCOPE)
        self.assertEqual("idempotency_conflict", conflict.exception.code)

    def test_kill_worker_holds_slot_until_proven_exit_and_no_late_output(self):
        plan, grant, run = self.f.submit(params={"target_column": "y", "epochs": 500, "batch_size": 1})
        until = time.monotonic() + 15
        while time.monotonic() < until:
            state = self.f.rpc("status", {"task_id": run["task_id"]})
            if state["process_identity"]:
                break
            time.sleep(.05)
        self.assertTrue(state["slot_owned"])
        receipt = self.f.rpc("cancel", {"task_id": run["task_id"]})
        self.assertFalse(receipt["exit_acknowledged"])
        self.assertTrue(receipt["slot_owned"])
        self.assertEqual("canceling", receipt["status"])
        result = self.f.wait(run)
        self.assertEqual("canceled", result["status"])
        self.assertTrue(result["exit_acknowledged"])
        self.assertFalse((self.f.root / "client" / "artifacts" / run["id"] / "manifest.json").exists())
        terminal = self.f.rpc("status", {"task_id": run["task_id"]})
        self.assertFalse(terminal["slot_owned"])
        self.assertFalse(alive(state["process_identity"]))

    def test_service_restart_adopts_same_worker_and_deadline_survives_disconnect(self):
        budget = {**BUDGET, "max_run_seconds": 8}
        plan, grant, run = self.f.submit(params={"target_column": "y", "epochs": 500, "batch_size": 1}, budget=budget)
        until = time.monotonic() + 5
        while time.monotonic() < until:
            state = self.f.rpc("status", {"task_id": run["task_id"]})
            if state["process_identity"]:
                break
            time.sleep(.05)
        original = state["process_identity"]
        self.f.stop(kill=True)
        self.f.offline = True
        unknown = self.f.service.status(run["id"], **SCOPE)
        self.assertEqual("unknown", unknown["status"])
        self.assertFalse(unknown["leases_released"])
        self.f.offline = False
        self.f.start()
        adopted = self.f.rpc("status", {"task_id": run["task_id"]})
        self.assertEqual(original, adopted["process_identity"])
        self.assertEqual(state["attempt_id"], adopted["attempt_id"])
        result = self.f.wait(run, timeout=20)
        self.assertEqual("failed", result["status"], result)
        self.assertTrue(result["exit_acknowledged"])
        self.assertEqual("deadline_expired", result["error"]["code"])

    def test_corrupt_download_does_not_publish_and_native_output_corruption_rejected(self):
        _p, _g, run = self.f.submit()
        self.f.corrupt_download = True
        result = self.f.wait(run)
        self.assertEqual("failed", result["status"], result)
        self.assertEqual("hash_mismatch", result["error"]["code"])
        self.assertTrue(result["exit_acknowledged"])
        self.assertFalse((self.f.root / "client" / "artifacts" / run["id"] / "manifest.json").exists())
        with self.assertRaises(RemoteExecutionError) as corrupt:
            self.f.rpc("artifact", {"task_id": run["task_id"]})
        self.assertEqual("artifact_corrupt", corrupt.exception.code)

    def test_plan_and_grant_scope_data_target_invalidation_and_model_cannot_approve(self):
        plan = self.f.plan()
        with self.assertRaises(RemoteExecutionError):
            self.f.service.approve(plan["id"], expected_plan_hash=plan["plan_hash"], **{**SCOPE, "session_id": "foreign"})
        with self.assertRaises(RemoteExecutionError):
            self.f.service.approve(plan["id"], expected_plan_hash="0" * 64, **SCOPE)
        self.f.connection["host"] = "changed.invalid"
        with self.assertRaises(RemoteExecutionError) as changed:
            self.f.service.approve(plan["id"], expected_plan_hash=plan["plan_hash"], **SCOPE)
        self.assertEqual("plan_invalidated", changed.exception.code)
        self.f.connection["host"] = "controlled.invalid"
        self.f.catalog.add(mlp_frame(), dataset_id="tiny")
        with self.assertRaises(RemoteExecutionError) as changed:
            self.f.service.approve(plan["id"], expected_plan_hash=plan["plan_hash"], **SCOPE)
        self.assertEqual("plan_invalidated", changed.exception.code)
        with self.assertRaises(RunnerError):
            self.f.plan(params={"target_column": "y", "approve": True})
        from unittest.mock import Mock
        constructor = Mock(return_value=self.f.catalog)
        self.f.service.dataset_catalog = None
        try:
            with patch.dict(sys.modules, {"data.dataset_catalog": SimpleNamespace(DatasetCatalog=constructor)}):
                plan = self.f.plan()
                self.assertTrue(constructor.call_args_list)
                for call in constructor.call_args_list:
                    self.assertEqual((), call.args)
                    self.assertEqual(SCOPE, call.kwargs)
        finally:
            self.f.service.dataset_catalog = self.f.catalog
        original_handle = self.f.catalog.get("tiny")
        oversized = copy.deepcopy(original_handle.manifest)
        oversized.pop("manifest_hash", None)
        oversized["lineage"]["notes"] = "x" * MAX_FRAME
        self.f.catalog.handles[original_handle.snapshot_id] = DatasetHandle(oversized, original_handle.root)
        try:
            with self.assertRaises(RemoteExecutionError) as large:
                self.f.plan()
            self.assertEqual("frame_too_large", large.exception.code)
        finally:
            self.f.catalog.handles[original_handle.snapshot_id] = original_handle
        path = self.f.catalog.get("tiny").root / "data.parquet"
        with path.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaises(RemoteExecutionError) as changed:
            self.f.service.approve(plan["id"], expected_plan_hash=plan["plan_hash"], **SCOPE)
        self.assertEqual("dataset_changed", changed.exception.code)
        # Exercise the main foundation's real default catalog, not an API mock.
        from data.dataset_catalog import DatasetCatalog
        catalog = DatasetCatalog(**SCOPE)
        operation = catalog.begin_import({**SCOPE, "options": {"dataset_id": "m3_default_catalog"}}, idempotency_key="m3-default-publish")
        stage = catalog.staging_dir(operation["operation_id"])
        stage.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(mlp_frame(), preserve_index=False)
        pq.write_table(table, stage / "data.parquet", row_group_size=16)
        manifest = copy.deepcopy(self.f.handle.manifest)
        manifest.pop("manifest_hash", None)
        manifest.update(dataset_id=operation["dataset_id"], snapshot_id=operation["snapshot_id"])
        manifest["files"][0].update(size_bytes=(stage / "data.parquet").stat().st_size, sha256=file_sha256(stage / "data.parquet"))
        published = catalog.publish(manifest, idempotency_key="m3-default-publish")
        self.f.service.dataset_catalog = None
        try:
            plan = self.f.plan(dataset_id=published["dataset_id"])
            grant = self.f.service.approve(plan["id"], expected_plan_hash=plan["plan_hash"], **SCOPE)
            run = self.f.service.submit(plan["id"], grant["id"], idempotency_key="real-default-catalog", **SCOPE)
            result = self.f.wait(run)
            self.assertEqual("succeeded", result["status"], result)
            model_path, _entry = self.f.service.artifact(run["id"], "model.json", **SCOPE)
            self.assertEqual(published["snapshot_id"], json.loads(model_path.read_text(encoding="utf-8"))["lineage"]["snapshot_id"])
        finally:
            self.f.service.dataset_catalog = self.f.catalog

    def test_rpc_regression_gru_and_lstm_use_same_safe_kernel_bundle(self):
        self.f.catalog.add(pd.DataFrame({"x": np.arange(64, dtype=float), "y": np.arange(64, dtype=float) / 4}), dataset_id="reg")
        self.f.catalog.add(pd.DataFrame({"ds": pd.date_range("2025-01-01", periods=32), "y": np.sin(np.arange(32) / 4)}), dataset_id="series")
        cases = [("Torch_MLP", "reg", {"target_column": "y", "mode": "regression", "epochs": 1, "batch_size": 8})]
        cases += [("Torch_RNN", "series", {"target_column": "y", "time_column": "ds", "cell": cell, "window": 3,
                                           "steps": 2, "epochs": 1, "batch_size": 4, "mc_samples": 2}) for cell in ("gru", "lstm")]
        from remote_runner.torch_worker import load_model_bundle
        for family, dataset_id, params in cases:
            with self.subTest(family=family, cell=params.get("cell")):
                _p, _g, run = self.f.submit(key=dataset_id + params.get("cell", "reg"), family=family, dataset_id=dataset_id, params=params)
                result = self.f.wait(run)
                self.assertEqual("succeeded", result["status"], result)
                path, _entry = self.f.service.artifact(run["id"], "model.safetensors", **SCOPE)
                model, meta = load_model_bundle(path.parent)
                self.assertEqual(family, meta["family"])
                self.assertEqual("cpu", meta["device"])
                self.assertGreater(pq.ParquetFile(path.parent / "predictions.parquet").metadata.num_rows, 0)

    def test_cli_protocol_is_bounded_and_unknown_native_task_keeps_lease(self):
        request = canonical({"version": 1, "request_id": "fixed-cli", "operation": "handshake", "payload": {}})
        command = [sys.executable, "-m", "remote_runner.baa_remote_runner", "--rpc-v1", "--root", str(self.f.runner_root)]
        result = subprocess.run(command, input=request + b"\n", capture_output=True, timeout=10, cwd=ROOT)
        self.assertEqual(0, result.returncode)
        reply = json.loads(result.stdout)
        self.assertTrue(reply["ok"])
        self.assertEqual("fixed-cli", reply["request_id"])
        self.assertFalse(reply["result"]["cuda"])
        self.assertEqual(b"", result.stderr)
        malformed = subprocess.run(command, input=b"x" * (MAX_FRAME + 1) + b"\n", capture_output=True, timeout=10, cwd=ROOT)
        self.assertEqual("frame_too_large", json.loads(malformed.stdout)["code"])
        self.f.drop_phase = "reserve"
        _p, _g, run = self.f.submit()
        original_task = next(c[2]["task_id"] for c in self.f.calls if c[0] == "submit")
        self.f.service.store.update("run", run["id"], scope=SCOPE, task_id="missing-task")
        unknown = self.f.service.status(run["id"], **SCOPE)
        self.assertEqual("unknown", unknown["status"])
        self.assertFalse(unknown["exit_acknowledged"] or unknown["leases_released"])
        self.assertEqual("task_unknown", unknown["error"]["code"])
        self.f.rpc("cancel", {"task_id": original_task})
        self.f.service.store.update("run", run["id"], scope=SCOPE, task_id=original_task)
        self.assertEqual("canceled", self.f.wait(run)["status"])

    def test_queue_cancel_and_worker_crash_never_publish_or_launch_again(self):
        _p, _g, first = self.f.submit(params={"target_column": "y", "epochs": 500, "batch_size": 1})
        until = time.monotonic() + 10
        while time.monotonic() < until:
            state = self.f.rpc("status", {"task_id": first["task_id"]})
            if state["process_identity"]:
                break
            time.sleep(.05)
        plan, grant, queued = self.f.submit(key="queued", params={"target_column": "y", "epochs": 1})
        queued_state = self.f.rpc("status", {"task_id": queued["task_id"]})
        self.assertEqual("queued", queued_state["status"])
        self.assertFalse(queued_state["slot_owned"])
        canceled = self.f.service.cancel(queued["id"], **SCOPE)
        self.assertEqual("canceled", self.f.wait(canceled)["status"])
        import psutil
        psutil.Process(state["process_identity"]["pid"]).kill()
        failed = self.f.wait(first)
        self.assertEqual("failed", failed["status"], failed)
        self.assertTrue(failed["exit_acknowledged"])
        self.assertEqual("worker_lost", failed["error"]["code"])
        # A stale successful outcome cannot resurrect a canceled queue entry.
        directory = self.f.runner_root / "tasks" / queued["task_id"]
        atomic_json(directory / "outcome.json", {"ok": True, "attempt_id": "late", "artifacts": {}})
        time.sleep(.2)
        terminal = self.f.rpc("status", {"task_id": queued["task_id"]})
        self.assertEqual("canceled", terminal["status"])
        self.assertEqual("", terminal["attempt_id"])
        self.assertFalse(terminal["slot_owned"])
        self.assertIsNone(terminal["artifacts"])

    def test_training_blueprint_real_service_scopes_and_explicit_approval(self):
        from flask import Flask
        import api.training as training
        from agent.jobs import JobRunner
        store = JobsStore(self.f.root / "jobs.sqlite3")
        runner = JobRunner(SCOPE["session_id"], store)
        self.addCleanup(store.close)
        self.addCleanup(runner.shutdown)
        session = SimpleNamespace(job_runner=runner, workspace_id="")
        app = Flask(__name__)
        app.extensions["remote_training_service"] = self.f.service
        app.register_blueprint(training.bp)
        client = app.test_client()
        prefix = "/api/session/m3-local/training"
        with patch.object(training, "_session_scope", return_value=(session, SCOPE)):
            response = client.post(prefix + "/plans", json={"family": "Torch_MLP", "params": {"target_column": "y", "epochs": 1},
                                   "dataset_id": "tiny", "connection_id": "cpu-controlled", "budget": BUDGET})
            self.assertEqual(201, response.status_code, response.json)
            plan = response.json["plan"]
            rejected = client.post(prefix + "/runs", json={"plan_id": plan["id"], "grant_id": "", "idempotency_key": "api", "approve": True})
            self.assertEqual(400, rejected.status_code)
            response = client.post(prefix + "/plans/" + plan["id"] + "/approve", json={"expected_plan_hash": plan["plan_hash"]})
            self.assertEqual(201, response.status_code, response.json)
            grant = response.json["grant"]
            response = client.post(prefix + "/runs", json={"plan_id": plan["id"], "grant_id": grant["id"], "idempotency_key": "api"})
            self.assertEqual(202, response.status_code, response.json)
            run = response.json["run"]
            original_publish = self.f.service.store.publish_delivery
            late_cancels = []
            def cancel_after_committed(*args, **kwargs):
                published = original_publish(*args, **kwargs)
                if published["status"] == "succeeded":
                    late_cancels.append(runner.cancel(run["job_id"]))
                return published
            with patch.object(self.f.service.store, "publish_delivery", side_effect=cancel_after_committed):
                final = self.f.wait(run)
            self.assertEqual([True], late_cancels)
            self.assertEqual("succeeded", final["status"], final)
            read = client.get(prefix + "/runs/" + run["id"])
            self.assertEqual(200, read.status_code)
            artifact = client.get(prefix + "/runs/" + run["id"] + "/artifacts/model.safetensors")
            self.assertEqual(200, artifact.status_code)
            artifact.close()
            self.assertEqual("succeeded", runner.get_status(run["job_id"])["status"])
            before = store.last_sequence(SCOPE["session_id"])
            self.f.service.reconcile_pending(job_runner=runner, **SCOPE)
            self.assertEqual(before, store.last_sequence(SCOPE["session_id"]))
            long_plan = self.f.plan(params={"target_column": "y", "epochs": 500, "batch_size": 1})
            long_grant = self.f.service.approve(long_plan["id"], expected_plan_hash=long_plan["plan_hash"], **SCOPE)
            managed = self.f.service.submit(long_plan["id"], long_grant["id"], idempotency_key="job-cancel", job_runner=runner, **SCOPE)
            self.f.offline = True
            self.assertTrue(runner.cancel(managed["job_id"]))
            self.assertEqual("canceling", runner.get_status(managed["job_id"])["status"])
            pending = self.f.service.store.get("run", managed["id"], **SCOPE)
            self.assertTrue(pending["cancel_intent"])
            self.assertFalse(pending["leases_released"])
            self.f.offline = False
            cancel_reply = client.post(prefix + "/runs/" + managed["id"] + "/cancel")
            self.assertEqual(202, cancel_reply.status_code, cancel_reply.json)
            self.assertEqual("canceled", self.f.wait(managed)["status"])
            self.assertEqual("canceled", runner.get_status(managed["job_id"])["status"])
        events = store.list_events(SCOPE["session_id"], limit=1000)
        native = [(e["native_id"], e["native_seq"]) for e in events if "native_id" in e]
        self.assertEqual(len(native), len(set(native)))
        self.assertEqual(2, runner.clear_terminal())
        self.assertEqual(0, store._conn.execute("SELECT count(*) FROM remote_job_event_cursors").fetchone()[0])
        self.f.service.reconcile_pending(job_runner=runner, **SCOPE)
        self.assertEqual([], runner.list_jobs())
        self.assertEqual("succeeded", self.f.service.status(run["id"], **SCOPE)["status"])


class RemoteM3UnitTests(unittest.TestCase):
    def test_failed_or_timed_out_probe_reports_unavailable_without_readiness(self):
        with tempfile.TemporaryDirectory(prefix="baa-probe-") as directory:
            cases = [(subprocess.CompletedProcess(["probe"], 1, b"", b"private diagnostics"), None, "probe_failed"),
                     (None, subprocess.TimeoutExpired(["probe"], 30), "probe_timeout")]
            for index, (completed, error, code) in enumerate(cases):
                with self.subTest(code=code), patch("remote_runner.supervisor.subprocess.run", return_value=completed, side_effect=error):
                    service = Supervisor(Path(directory) / str(index), device="cpu")
                    try:
                        handshake = service.handshake()
                        self.assertFalse(handshake["ready"])
                        self.assertEqual(code, handshake["probe_error"])
                        self.assertNotIn("private diagnostics", json.dumps(handshake))
                    finally:
                        service.close()

    def test_bounded_sftp_resume_revalidates_each_block_and_whole_hash(self):
        with tempfile.TemporaryDirectory(prefix="baa-m3-transfer-", dir=os.environ.get("BAA_DATA_DIR")) as temp:
            root = Path(temp)
            remote = root / "remote"
            remote.mkdir()
            data = b"a" * BLOCK_BYTES + b"b" * BLOCK_BYTES + b"tail"
            source = root / "source.parquet"
            source.write_bytes(data)
            sha = file_sha256(source)
            entry = {"relative_path": "data.parquet", "sha256": sha, "size_bytes": len(data)}
            sftp = LocalSftp()
            transfer = SftpTransfer(sftp, max_bytes=len(data) * 2)
            transfer.authorize_uploads(rpc_path(remote), [entry])
            part = remote / "data.parquet.part"
            part.write_bytes(data[:BLOCK_BYTES] + b"x" * BLOCK_BYTES + b"junk")
            transfer.upload(source, rpc_path(remote / "data.parquet"), sha256=sha)
            self.assertEqual(data, (remote / "data.parquet").read_bytes())
            transfer.authorize_downloads(rpc_path(remote), [entry])
            target = root / "download.parquet"
            target.with_suffix(".parquet.part").write_bytes(data[:BLOCK_BYTES] + b"z" * BLOCK_BYTES)
            transfer.download(rpc_path(remote / "data.parquet"), target, sha256=sha, size_bytes=len(data))
            self.assertEqual(data, target.read_bytes())
            self.assertLessEqual(sftp.max_read, BLOCK_BYTES)
            self.assertLessEqual(sftp.max_write, BLOCK_BYTES)
            with self.assertRaises(RemoteExecutionError):
                transfer.upload(source, rpc_path(root / "escape.parquet"), sha256=sha)
            with self.assertRaises(RemoteExecutionError):
                transfer.authorize_uploads(rpc_path(remote), [{**entry, "relative_path": "../escape.parquet"}])

    def test_jobs_migrate_before_recovery_and_preserve_remote_with_dedup(self):
        with tempfile.TemporaryDirectory(prefix="baa-m3-jobs-", dir=os.environ.get("BAA_DATA_DIR")) as temp:
            path = Path(temp) / "jobs.db"
            old = sqlite3.connect(path)
            old.executescript("CREATE TABLE jobs(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,type TEXT NOT NULL,status TEXT NOT NULL,progress INTEGER DEFAULT 0,result TEXT,error TEXT,created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT);")
            old.execute("INSERT INTO jobs(id,session_id,type,status,created_at) VALUES('old','s','legacy','running','2026-01-01')")
            old.commit()
            old.close()
            store = JobsStore(path)
            self.assertEqual("failed", store.get("old")["status"])
            remote = store.create("s", "training", execution_backend="remote", external_id="native")
            store.mark_queued(remote["id"])
            store.mark_started(remote["id"])
            store.append_remote_event(remote["id"], "native", 1, {"type": "remote_training_event"})
            store.close()
            store = JobsStore(path)
            self.assertEqual("running", store.get(remote["id"])["status"])
            self.assertEqual("recovery_pending", store.get(remote["id"])["recovery_phase"])
            self.assertIsNone(store.append_remote_event(remote["id"], "native", 1, {"type": "remote_training_event"}))
            progress = store.append_remote_event(remote["id"], "native", 2,
                                                {"type": "remote_training_event", "native_type": "progress", "progress": 50, "message": "epoch"})
            self.assertEqual("job_progress", progress["type"])
            self.assertEqual(52, store.get(remote["id"])["progress"])
            store.mark_canceling(remote["id"])
            self.assertTrue(store.mark_succeeded(remote["id"], {"committed": True}, remote_commit=True))
            local = store.create("s", "local")
            store.mark_queued(local["id"])
            store.mark_started(local["id"])
            store.mark_canceling(local["id"])
            self.assertFalse(store.mark_succeeded(local["id"], {}, remote_commit=True))
            self.assertEqual("canceling", store.get(local["id"])["status"])
            from agent.jobs import JobRunner
            from concurrent.futures import ThreadPoolExecutor
            runner = JobRunner("s", store)
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    jobs = list(pool.map(lambda _: runner.begin_remote("concurrent", owner_id="local-default"), range(4)))
                self.assertEqual(1, len(set(jobs)))
                self.assertEqual(1, sum(j["recovery_metadata"].get("run_id") == "concurrent" for j in store.list_by_session("s")))
            finally:
                runner.shutdown()
            store.close()

    def test_safe_protocol_rejects_arbitrary_code_paths_frames_and_model_headers(self):
        for params in ({"target_column": "y", "module": "os"}, {"target_column": "y", "env": {"X": "Y"}}, {"target_column": "y", "epochs": True}):
            with self.assertRaises(RunnerError):
                validate_params("Torch_MLP", params)
        with self.assertRaises(RunnerError):
            decode_frame(b"x" * (MAX_FRAME + 1))
        with tempfile.TemporaryDirectory(prefix="baa-m3-safety-", dir=os.environ.get("BAA_DATA_DIR")) as temp:
            root = Path(temp)
            with self.assertRaises(RunnerError):
                safe_path(root, "../escape")
            original = root / "original"
            original.write_bytes(b"test")
            linked = root / "linked"
            os.link(original, linked)
            with self.assertRaises(RunnerError):
                safe_path(root, "linked", exists=True)
            weight = root / "bad.safetensors"
            weight.write_bytes((2**62).to_bytes(8, "little") + b"invalid")
            with self.assertRaises(RunnerError):
                validate_safetensors(weight, 1024)

    def test_local_kernels_regression_gru_lstm_train_only_statistics_and_lazy_windows(self):
        from Function.Analyze.Torch_MLP.kernel import _train_mask, train_mlp
        from Function.Analyze.Torch_RNN.kernel import LazyWindows, train_rnn
        frame = pd.DataFrame({"x": np.arange(80, dtype=float), "y": np.arange(80, dtype=float) / 2})
        mask = _train_mask(0, 80, 42, .2)
        frame.loc[~mask, "x"] = 1e6
        regression = train_mlp(frame, {"target_column": "y", "mode": "regression", "epochs": 1, "batch_size": 8}, budget=BUDGET)
        self.assertAlmostEqual(frame.loc[mask, "x"].mean(), regression.metadata["means"][0])
        self.assertLess(regression.metadata["means"][0], 100)
        series = pd.DataFrame({"ds": pd.date_range("2025-01-01", periods=48), "y": np.sin(np.arange(48) / 4), "series": ["a"] * 24 + ["b"] * 24})
        for cell in ("gru", "lstm"):
            result = train_rnn(series.iloc[::-1], {"target_column": "y", "time_column": "ds", "sequence_column": "series", "cell": cell,
                                                   "window": 3, "steps": 2, "epochs": 1, "batch_size": 4, "mc_samples": 2}, budget=BUDGET)
            self.assertEqual(4, len(result.tables[0].query("segment == 'forecast'")))
            self.assertEqual("cpu", result.metadata["device"])
            self.assertEqual(2, len(result.metadata["segments"]))
            self.assertGreater(result.metadata["validation_windows"], 0)
        segs = [{"start": 0, "split": 10, "end": 20, "mean": 0., "std": 1.}, {"start": 20, "split": 30, "end": 40, "mean": 0., "std": 1.}]
        windows = LazyWindows(np.arange(40), segs, 3, "validation")
        self.assertEqual(14, len(windows))
        self.assertEqual([10., 11., 12.], windows[0][0][:, 0].tolist())
        self.assertEqual([20 + 10., 31., 32.], windows[7][0][:, 0].tolist())

    def test_remote_routing_precedes_torch_and_auto_never_external(self):
        from infrastructure.training_router import select_training_target
        import Function.Analyze.Torch_MLP.analyze as mlp
        with patch("infrastructure.training_router.select_training_device", side_effect=AssertionError("local probe on remote route")):
            target = select_training_target(execution_target="remote(connection)", approved_plan_id="plan", grant_id="grant")
            self.assertEqual("remote", target["kind"])
        with patch("infrastructure.training_router.select_training_device", return_value={"device": "cpu", "reason": "test"}):
            self.assertEqual("cpu", select_training_target([{"runner_ready": True, "id": "remote"}])["kind"])
        with patch.object(mlp, "_require_torch", side_effect=AssertionError("local torch import")), patch.object(mlp, "submit_approved_analysis", return_value={"status": "queued"}) as submit:
            self.assertEqual("queued", mlp.run(None, "y", execution_target="remote(connection)", approved_plan_id="p", grant_id="g")["status"])
            submit.assert_called_once()
        with self.assertRaises(ValueError):
            mlp.run(None, "y", execution_target="remote(connection)", confirm=True)

    def test_commit_hashing_keeps_status_and_cancel_live(self):
        import threading
        with tempfile.TemporaryDirectory(prefix="baa-m3-verify-", dir=os.environ.get("BAA_DATA_DIR")) as temp:
            supervisor = Supervisor(temp, device="cpu", probe={"ready": True, "cuda": False})
            entered, proceed = threading.Event(), threading.Event()
            errors = []
            try:
                catalog = SnapshotCatalog(Path(temp) / "datasets")
                handle = catalog.add(mlp_frame())
                receipt = supervisor.rpc("submit", {"phase": "reserve", "idempotency_key": "verify-cancel", "plan_hash": "a" * 64,
                    "plan": {"family": "Torch_MLP", "params": {"target_column": "y", "epochs": 1}, "kernel_version": KERNEL_VERSION, "device": "cpu"},
                    "input_manifest": handle.manifest, "budget": BUDGET, "expires_at": time.time() + 60, "grant_ref": "b" * 64})
                def blocked_hash(_root, _manifest, _budget, *, cancel):
                    entered.set()
                    if not proceed.wait(3):
                        raise AssertionError("Hash cancellation test exceeded its local wait")
                    cancel()
                def commit():
                    try:
                        supervisor.rpc("submit", {"phase": "commit", "task_id": receipt["task_id"], "manifest_hash": receipt["manifest_hash"], "grant_ref": "b" * 64})
                    except RunnerError as exc:
                        errors.append(exc.code)
                with patch("remote_runner.supervisor.verify_files", side_effect=blocked_hash):
                    thread = threading.Thread(target=commit)
                    thread.start()
                    self.assertTrue(entered.wait(2))
                    self.assertEqual("verifying", supervisor.rpc("status", {"task_id": receipt["task_id"]})["status"])
                    self.assertEqual("canceling", supervisor.rpc("cancel", {"task_id": receipt["task_id"]})["status"])
                    proceed.set()
                    thread.join(3)
                    self.assertFalse(thread.is_alive())
                self.assertEqual(["canceled"], errors)
                supervisor.tick()
                terminal = supervisor.rpc("status", {"task_id": receipt["task_id"]})
                self.assertEqual("canceled", terminal["status"])
                self.assertTrue(terminal["exit_acknowledged"])
                self.assertFalse(terminal["slot_owned"])
            finally:
                proceed.set()
                supervisor.close()

    def test_single_supervisor_lock_and_unproven_launch_keeps_slot(self):
        with tempfile.TemporaryDirectory(prefix="baa-m3-slot-", dir=os.environ.get("BAA_DATA_DIR")) as temp:
            s = Supervisor(temp, device="cpu", probe={"ready": True, "cuda": False})
            try:
                with self.assertRaises(RunnerError):
                    Supervisor(temp, device="cpu", probe={"ready": True})
                # Process identity checks never treat a reused PID as the old worker.
                self.assertFalse(alive({**identity(), "birth": 0}))
                catalog = SnapshotCatalog(Path(temp) / "datasets")
                handle = catalog.add(mlp_frame())
                payload = {"phase": "reserve", "idempotency_key": "unproven-launch", "plan_hash": "a" * 64,
                           "plan": {"family": "Torch_MLP", "params": {"target_column": "y", "epochs": 1},
                                    "kernel_version": KERNEL_VERSION, "device": "cpu"},
                           "input_manifest": handle.manifest, "budget": {**BUDGET, "max_log_bytes": 4096},
                           "expires_at": time.time() + 60, "grant_ref": "b" * 64}
                receipt = s.rpc("submit", payload)
                task = s._task({"task_id": receipt["task_id"]})
                input_file = s._directory(task) / "input" / "data.parquet"
                input_file.write_bytes(b"corrupt")
                commit = {"phase": "commit", "task_id": task["task_id"], "manifest_hash": task["manifest_hash"], "grant_ref": "b" * 64}
                with self.assertRaises(RunnerError) as corrupt:
                    s.rpc("submit", commit)
                self.assertEqual("artifact_corrupt", corrupt.exception.code)
                input_file.write_bytes((handle.root / "data.parquet").read_bytes())
                s.rpc("submit", commit)
                task = s._task({"task_id": task["task_id"]})
                task.update(status="starting", attempt_id="unknown-attempt", slot_owned=True, launch_at=time.time())
                s._save(task)
                s.tick()
                state = s.rpc("status", {"task_id": task["task_id"]})
                self.assertEqual("unknown", state["status"])
                self.assertTrue(state["slot_owned"])
                self.assertFalse(state["exit_acknowledged"])
                s._event(task, "progress", message="bounded" * 1000, progress=0)
                for i in range(40):
                    s._event(task, "progress", message="bounded" * 80, progress=i)
                events = s.rpc("events", {"task_id": task["task_id"], "after_seq": 0, "limit": 100})
                self.assertTrue(events["truncated"])
                size = s.db.execute("SELECT sum(length(CAST(body AS BLOB))) FROM events").fetchone()[0]
                self.assertLessEqual(size, 4096)
                # Retention deletes only a proven-exited task allocation.
                task.update(status="canceled", exit_acknowledged=True, slot_owned=False, retain_until=time.time() - 1)
                s._save(task)
                s.tick()
                self.assertFalse(input_file.exists())
                self.assertTrue((handle.root / "data.parquet").exists())
                self.assertEqual("canceled", s.rpc("submit", commit)["status"])
            finally:
                s.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
