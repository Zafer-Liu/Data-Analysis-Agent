"""Committed local receipts and durable remote recovery; no SSH/GPU services."""
from __future__ import annotations

from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent.execution import ExecutionCancelled, ExecutionContext
from agent.jobs import JobRunner
from data.jobs_store import JobsStore
from infrastructure.remote_training_client import RemoteTrainingService
from infrastructure.remote_training_recovery import reconcile_once


class JobTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="baa-commit-jobs-", dir=os.environ.get("BAA_DATA_DIR"))
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = JobsStore(self.root / "jobs.sqlite3")
        self.addCleanup(self.store.close)
        self.runner = JobRunner("commit-test", self.store, max_workers=1)
        self.addCleanup(self.runner.shutdown)

    def wait_job(self, jid):
        finished = threading.Event()
        self.runner.add_terminal_listener(jid, lambda _job: finished.set())
        self.assertTrue(finished.wait(30), self.runner.get_status(jid))
        return self.runner.get_status(jid)

    def agent(self, context=None):
        from agent.agent import BusinessAgent
        agent = BusinessAgent.__new__(BusinessAgent)
        agent._job_runner = self.runner
        agent._semantic_context = {}
        agent._execution_context = context
        return agent

    @staticmethod
    def collect_bridge(bridge):
        events = []
        while True:
            try:
                events.append(next(bridge))
            except StopIteration as done:
                return done.value, events


class LocalCommittedJobTests(JobTestCase):
    def test_late_stop_retains_receipt_until_worker_exit_and_after_reopen(self):
        entered, leave = threading.Event(), threading.Event()
        self.addCleanup(leave.set)
        receipt = {"result_committed": True, "snapshot_id": "immutable-snapshot", "cancel_requested": False}

        def worker(_ctx):
            entered.set()
            if not leave.wait(10):
                raise AssertionError("Worker was not released")
            return receipt

        jid = self.runner.create(worker, "dataset_import")
        self.assertTrue(entered.wait(5))
        terminal = Mock()
        self.runner.add_terminal_listener(jid, terminal)
        self.assertTrue(self.runner.cancel(jid))
        self.assertEqual("canceling", self.store.get(jid)["status"])
        self.assertIsNone(self.store.get(jid)["finished_at"])
        terminal.assert_not_called()
        leave.set()
        job = self.wait_job(jid)
        self.assertEqual("succeeded", job["status"])
        self.assertTrue(job["result"]["cancel_requested"])
        self.assertEqual("immutable-snapshot", job["result"]["snapshot_id"])
        events = self.store.list_events("commit-test", job_id=jid)
        self.assertEqual(["job_done"], [e["type"] for e in events if e["type"] in {"job_done", "job_canceled"}])
        self.assertEqual(job["result"], events[-1]["result"])
        self.assertFalse(receipt["cancel_requested"])
        self.runner.shutdown()
        self.store.close()
        reopened = JobsStore(self.root / "jobs.sqlite3")
        self.addCleanup(reopened.close)
        self.assertEqual(job["result"], reopened.get(jid)["result"])
        self.assertEqual("succeeded", reopened.get(jid)["status"])

    def test_store_catches_stop_racing_runner_post_return_check(self):
        original = self.store.mark_succeeded

        def stop_then_commit(jid, result, **kwargs):
            self.assertTrue(self.runner.cancel(jid))
            return original(jid, result, **kwargs)

        with patch.object(self.store, "mark_succeeded", side_effect=stop_then_commit):
            jid = self.runner.create(lambda _ctx: {"result_committed": True, "cancel_requested": False}, "dataset_import")
            job = self.wait_job(jid)
        self.assertEqual("succeeded", job["status"])
        self.assertIs(True, job["result"]["cancel_requested"])
        self.assertIs(True, self.store.list_events("commit-test", job_id=jid)[-1]["result"]["cancel_requested"])

    def test_noncommitted_and_truthy_or_nested_markers_stay_canceled(self):
        values = [{}, {"result_committed": False}, {"result_committed": 1}, {"result_committed": "true"},
                  {"result": {"result_committed": True}}, [{"result_committed": True}],
                  '{"result_committed": true}']
        for value in values:
            with self.subTest(result=value):
                def worker(ctx):
                    self.runner.cancel(ctx.job_id)
                    return value
                job = self.wait_job(self.runner.create(worker, "dataset_import"))
                self.assertEqual("canceled", job["status"])
                self.assertIsNone(job["result"])

    def test_queued_stop_does_not_start_even_a_committing_worker(self):
        entered, leave = threading.Event(), threading.Event()
        self.addCleanup(leave.set)
        def blocker(_ctx):
            entered.set()
            return leave.wait(10)
        first = self.runner.create(blocker, "blocking")
        self.assertTrue(entered.wait(5))
        worker = Mock(return_value={"result_committed": True})
        jid = self.runner.create(worker, "dataset_import")
        self.assertTrue(self.runner.cancel(jid))
        self.assertEqual("canceled", self.wait_job(jid)["status"])
        worker.assert_not_called()
        leave.set()
        self.wait_job(first)

    def test_store_marker_does_not_bypass_remote_ack_or_resurrect_terminal_job(self):
        for backend in ("local", "remote"):
            job = self.store.create("commit-test", "import", execution_backend=backend)
            self.store.mark_queued(job["id"])
            self.store.mark_started(job["id"])
            self.store.mark_canceling(job["id"])
            if backend == "remote":
                self.assertFalse(self.store.mark_succeeded(job["id"], {"result_committed": True}))
            self.store.mark_canceled(job["id"])
            self.assertFalse(self.store.mark_succeeded(job["id"], {"result_committed": True}))
            self.assertEqual("canceled", self.store.get(job["id"])["status"])

    def test_execution_wrapper_preserves_only_committed_results_and_precheck(self):
        agent = self.agent()
        for result in ({"result_committed": True}, {"result_committed": 1}, {"result": {"result_committed": True}}):
            context = ExecutionContext()
            def worker():
                context.cancel()
                return result
            if result.get("result_committed") is True:
                actual = agent._run_execution_worker(context, worker)
                self.assertTrue(actual["cancel_requested"])
            else:
                with self.assertRaises(ExecutionCancelled):
                    agent._run_execution_worker(context, worker)
            self.assertTrue(context.exit_acknowledged)
        context = ExecutionContext()
        context.cancel()
        worker = Mock(return_value={"result_committed": True})
        with self.assertRaises(ExecutionCancelled):
            agent._run_execution_worker(context, worker)
        worker.assert_not_called()
        self.assertTrue(context.exit_acknowledged)

    def test_real_agent_job_bridge_preserves_commit_but_cancels_uncommitted_work(self):
        parent = ExecutionContext()
        self.addCleanup(parent.acknowledge_exit)
        agent = self.agent(parent)
        for committed in (True, False):
            with self.subTest(committed=committed):
                def worker(ctx):
                    self.runner.cancel(ctx.job_id)
                    return {"result_committed": committed, "snapshot_id": "published"}
                job, events = self.collect_bridge(agent._run_as_job(worker, "dataset_import"))
                self.assertEqual("succeeded" if committed else "canceled", job["status"])
                self.assertTrue(any(e["type"] == ("job_done" if committed else "job_canceled") for e in events))
                if committed:
                    self.assertEqual("published", job["result"]["snapshot_id"])
        parent.acknowledge_exit()
        self.assertTrue(parent.wait_for_exit(5))

    def test_closed_bridge_keeps_child_until_actual_committing_worker_exit(self):
        parent = ExecutionContext()
        self.addCleanup(parent.acknowledge_exit)
        agent = self.agent(parent)
        entered, leave = threading.Event(), threading.Event()
        self.addCleanup(leave.set)
        def worker(_ctx):
            entered.set()
            if not leave.wait(10):
                raise AssertionError("Worker was not released")
            return {"result_committed": True, "snapshot_id": "published"}
        bridge = agent._run_as_job(worker, "dataset_import")
        next(bridge)
        self.assertTrue(entered.wait(5))
        jid = agent._active_job_id
        bridge.close()
        parent.acknowledge_exit()
        self.assertFalse(parent.exit_acknowledged)
        self.assertEqual("canceling", self.store.get(jid)["status"])
        leave.set()
        job = self.wait_job(jid)
        self.assertEqual("succeeded", job["status"])
        self.assertTrue(job["result"]["cancel_requested"])
        self.assertTrue(parent.wait_for_exit(5))

    def test_actual_import_keeps_ready_snapshot_and_launch_receipt_without_activation(self):
        from flask import Flask
        import api.datasets as datasets
        import data.import_pipeline as pipeline
        from data.dataset_catalog import DatasetCatalog
        catalog = DatasetCatalog(self.root / "catalog", owner_id="local-default", session_id="commit-test")
        session = SimpleNamespace(workspace_id="", owner_user_id="", job_runner=self.runner, _sources=[], add_source=Mock())
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(datasets.bp)
        client = app.test_client()
        prefix = "/api/session/commit-test/datasets"
        published, leave = threading.Event(), threading.Event()
        stop_during_verification = threading.Event()
        verification_stops = []
        self.addCleanup(leave.set)
        original = pipeline.run_import
        original_get = catalog.get
        def verified_handle_then_stop(*args, **kwargs):
            handle = original_get(*args, **kwargs)
            if stop_during_verification.is_set():
                stop_during_verification.clear()
                verification_stops.append(self.runner.cancel(submitted["job_id"]))
            return handle
        def import_then_wait(*args, **kwargs):
            manifest = original(*args, **kwargs)
            published.set()
            if not leave.wait(10):
                raise AssertionError("Published worker was not released")
            return manifest
        with ExitStack() as stack:
            stack.enter_context(patch.object(datasets, "check_session_ownership", return_value=(True, "")))
            stack.enter_context(patch.object(datasets, "session_manager", SimpleNamespace(get_or_create=lambda _sid: session)))
            stack.enter_context(patch.object(datasets, "catalog_for_session", return_value=catalog))
            stack.enter_context(patch.object(pipeline, "run_import", side_effect=import_then_wait))
            stack.enter_context(patch.object(catalog, "get", side_effect=verified_handle_then_stop))
            plan = client.post(prefix + "/plans", data={"file": (io.BytesIO(b"id,amount\n001,10\n002,20\n"), "small.csv")})
            self.assertEqual(200, plan.status_code, plan.get_json())
            body = {"plan_id": plan.get_json()["plan_id"], "idempotency_key": "committed-once"}
            submitted = client.post(prefix + "/imports", json=body).get_json()
            self.assertTrue(published.wait(45))
            # Stop during the API's post-publication file verification, after
            # run_import returned but before source activation can start.
            stop_during_verification.set()
            leave.set()
            job = self.wait_job(submitted["job_id"])
            self.assertEqual([True], verification_stops)
            self.assertEqual("succeeded", job["status"], job)
            response = client.get(prefix + "/imports/" + submitted["operation_id"]).get_json()
            self.assertEqual("succeeded", response["status"])
            self.assertTrue(response["result"]["result_committed"])
            self.assertTrue(response["result"]["cancel_requested"])
            self.assertFalse(response["result"]["activated"])
            self.assertIsNone(response["result"]["source_id"])
            session.add_source.assert_not_called()
            handle = catalog.get(response["result"]["dataset_id"], snapshot_id=response["result"]["snapshot_id"], verify=True)
            self.assertEqual("ready", handle.manifest["status"])
            self.assertEqual(2, handle.manifest["row_count"])
            self.runner.clear_terminal()
            replay = client.post(prefix + "/imports", json=body).get_json()
            self.assertEqual("succeeded", replay["status"])
            self.assertEqual(response["result"], replay["result"])
            self.assertIsNone(replay["job"])
            with datasets._database(catalog) as connection:
                row = connection.execute("SELECT state,result FROM launches WHERE id=?", (submitted["operation_id"],)).fetchone()
            self.assertEqual("succeeded", row["state"])
            self.assertEqual(response["result"], json.loads(row["result"]))


class RemoteReceiptTransport:
    """Controlled acknowledged RPC replies; no server, SSH, training or GPU."""
    def __init__(self, status="succeeded", *, acknowledged=True):
        self.status, self.acknowledged = status, acknowledged
        self.calls = []
        self.events = [{"seq": 1, "type": "progress", "progress": 50, "message": "epoch"},
                       {"seq": 2, "type": "worker_exited", "status": status}]

    def rpc(self, operation, payload):
        self.calls.append((operation, payload))
        if operation == "events":
            return {"events": [e for e in self.events if e["seq"] > payload["after_seq"]], "last_seq": 2, "truncated": False}
        if operation == "cancel":
            self.status = "canceled" if self.acknowledged else "canceling"
        if operation in {"status", "cancel"}:
            return {"task_id": "native-task", "runner_instance_id": "native-runner", "plan_hash": "plan-hash",
                    "manifest_hash": "manifest-hash", "status": self.status, "upload_manifest": {},
                    "exit_acknowledged": self.acknowledged, "usage": {}}
        raise AssertionError("Unexpected remote operation: " + operation)

    def close(self):
        pass


class RemoteDurableRecoveryTests(JobTestCase):
    def setUp(self):
        super().setUp()
        self.scope = {"owner_id": "local-default", "session_id": "commit-test", "workspace_id": ""}
        self.service = self.new_service()
        self.addCleanup(lambda: self.service.close())
        self.transport = RemoteReceiptTransport()
        self.service.store.put("plan", {"id": "plan", **self.scope, "connection_binding": {},
                                        "plan_hash": "plan-hash", "manifest_hash": "manifest-hash"})
        self.jid = self.runner.begin_remote("run", owner_id="local-default", store_root=str(self.service.root))
        self.service.store.put("run", {"id": "run", "run_id": "run", **self.scope, "plan_id": "plan", "connection_id": "connection",
                                       "task_id": "native-task", "runner_instance_id": "native-runner", "status": "running", "phase": "running",
                                       "cancel_intent": False, "native_seq": 0, "transfers": {}, "exit_acknowledged": False,
                                       "leases_released": False, "job_id": self.jid, "deadline_at": time.time() + 300,
                                       "reserve_payload": {"phase": "reserve"}})
        self.result = {"artifact_id": "remote-training-run", "run_id": "run", "files": []}
        self.manifest = {"run_id": "run", "files": []}

    def new_service(self):
        return RemoteTrainingService(self.root / "remote", connection_resolver=lambda _id: None)

    def controlled_connect(self, _connection):
        return self.transport, {"runner_instance_id": "native-runner"}, {}

    def publish(self, *_args):
        return self.service.store.publish_delivery("run", self.scope, self.result, self.manifest, self.root / "manifest.json")

    def drive_without_runner(self):
        with patch.object(self.service, "_connect", side_effect=self.controlled_connect), patch.object(self.service, "_deliver", side_effect=self.publish):
            return self.service._drive("run", self.scope)

    def recover_without_session(self):
        with patch("data.session.get_global_jobs_store", return_value=self.store):
            return reconcile_once(self.service, SimpleNamespace(get=lambda _sid: None))

    def test_terminal_run_and_events_survive_completion_before_session_restore(self):
        run = self.drive_without_runner()
        self.assertEqual("succeeded", run["status"])
        self.assertEqual(2, run["native_seq"])
        self.assertEqual("running", self.store.get(self.jid)["status"])
        self.assertEqual(2, len(self.service.store.pending_job_events("run", self.scope)))
        self.assertEqual([self.scope], self.service.store.pending_scopes())
        self.service.close()
        self.service = self.new_service()
        with patch.object(self.service, "_connect", side_effect=AssertionError("Terminal replay touched the network")):
            self.assertEqual(1, len(self.recover_without_session()))
        job = self.store.get(self.jid)
        self.assertEqual("succeeded", job["status"])
        self.assertEqual(self.result, job["result"])
        self.assertEqual([], self.service.store.pending_scopes())
        self.assertEqual([], self.service.store.pending_job_events("run", self.scope))
        self.assertTrue(self.service.store.get("run", "run", **self.scope)["job_delivery_acknowledged"])
        events = self.store.list_events("commit-test", job_id=self.jid)
        self.assertEqual([1, 2], [e["native_seq"] for e in events if e.get("native_id") == "native-runner:native-task"])
        self.assertEqual(1, sum(e["type"] == "artifact_created" for e in events))
        self.assertEqual(1, sum(e["type"] == "job_done" for e in events))
        sequence = self.store.last_sequence("commit-test")
        self.service.reconcile_pending(job_runner=self.runner, **self.scope)
        self.assertEqual(sequence, self.store.last_sequence("commit-test"))
        self.runner.clear_terminal()
        self.service.reconcile_pending(job_runner=self.runner, **self.scope)
        self.assertEqual([], self.runner.list_jobs())

    def test_native_append_before_outbox_ack_replays_exactly_once(self):
        self.drive_without_runner()
        with patch.object(self.service.store, "acknowledge_job_event", side_effect=RuntimeError("crash after Job append")):
            self.recover_without_session()
        self.assertEqual(2, len(self.service.store.pending_job_events("run", self.scope)))
        self.assertEqual("running", self.store.get(self.jid)["status"])
        self.service.close()
        self.service = self.new_service()
        self.recover_without_session()
        events = self.store.list_events("commit-test", job_id=self.jid)
        native = [(e["native_id"], e["native_seq"]) for e in events if "native_id" in e]
        self.assertEqual(len(native), len(set(native)))
        self.assertEqual("succeeded", self.store.get(self.jid)["status"])
        self.assertEqual([], self.service.store.pending_scopes())

    def test_terminal_job_before_delivery_ack_recovers_without_duplicate_events(self):
        self.drive_without_runner()
        with patch.object(self.service.store, "acknowledge_job_delivery", side_effect=RuntimeError("crash after Job done")):
            self.recover_without_session()
        self.assertEqual("succeeded", self.store.get(self.jid)["status"])
        self.assertEqual([self.scope], self.service.store.pending_scopes())
        sequence = self.store.last_sequence("commit-test")
        self.service.close()
        self.service = self.new_service()
        self.recover_without_session()
        self.assertEqual(sequence, self.store.last_sequence("commit-test"))
        self.assertEqual([], self.service.store.pending_scopes())

    def test_created_and_queued_remote_jobs_normalize_before_terminal_delivery(self):
        for initial, outcome in (("created", "succeeded"), ("queued", "failed"), ("canceling", "canceled")):
            with self.subTest(initial=initial):
                run_id = initial + "-run"
                job = self.store.create("commit-test", "remote_training", execution_backend="remote",
                                        recovery_metadata={"run_id": run_id, "owner_id": "local-default"})
                jid = job["id"]
                if initial == "queued":
                    self.store.mark_queued(jid)
                elif initial == "canceling":
                    self.store.mark_canceling(jid)
                base = self.service.store.get("run", "run", **self.scope)
                self.service.store.put("run", {**base, "id": run_id, "run_id": run_id, "job_id": jid,
                                               "status": outcome, "phase": "complete" if outcome == "succeeded" else outcome,
                                               "exit_acknowledged": True, "leases_released": True,
                                               "result": self.result if outcome == "succeeded" else None})
                self.service.attach_job_runner(run_id, self.runner)
                self.service._drive(run_id, self.scope)
                actual = self.store.get(jid)
                self.assertEqual(outcome, actual["status"])
                starts = [e for e in self.store.list_events("commit-test", job_id=jid) if e["type"] == "job_started"]
                self.assertEqual(0 if initial == "canceling" else 1, len(starts))
                self.assertEqual(jid, self.service.store.get("run", run_id, **self.scope)["job_id"])

    def test_remote_terminal_label_without_exit_ack_never_finishes_job(self):
        self.transport.acknowledged = False
        self.service.attach_job_runner("run", self.runner)
        run = self.drive_without_runner()
        self.assertEqual("unknown", run["status"])
        self.assertEqual("running", self.store.get(self.jid)["status"])
        self.assertIsNone(self.store.get(self.jid)["finished_at"])
        self.assertFalse(run["leases_released"])

    def test_persisted_job_stop_is_imported_after_cold_restart_before_remote_drive(self):
        # Simulate the crash directly after JobsStore.mark_canceling, before
        # JobRunner can write recovery metadata or call the remote service.
        self.store.mark_canceling(self.jid)
        self.runner.shutdown()
        self.store.close()
        self.store = JobsStore(self.root / "jobs.sqlite3")
        self.addCleanup(self.store.close)
        self.transport = RemoteReceiptTransport("running", acknowledged=False)
        with patch.object(self.service, "_connect", side_effect=self.controlled_connect):
            self.recover_without_session()
        run = self.service.store.get("run", "run", **self.scope)
        self.assertTrue(run["cancel_intent"])
        self.assertEqual("canceling", run["status"])
        self.assertTrue(any(op == "cancel" for op, _payload in self.transport.calls))
        self.assertFalse(run["exit_acknowledged"] or run["leases_released"])
        self.assertEqual("canceling", self.store.get(self.jid)["status"])
        self.assertTrue(self.store.get(self.jid)["recovery_metadata"]["cancel_intent"])
        self.transport.status, self.transport.acknowledged = "canceled", True
        with patch.object(self.service, "_connect", side_effect=self.controlled_connect):
            self.recover_without_session()
        self.assertEqual("canceled", self.store.get(self.jid)["status"])
        self.assertTrue(self.store.get(self.jid)["recovery_metadata"]["cancel_intent"])

    def test_checkpoint_cannot_erase_a_concurrent_durable_job_stop(self):
        self.service.attach_job_runner("run", self.runner)
        original = self.runner.checkpoint_remote
        def stop_before_metadata_write(*args, **kwargs):
            self.store.mark_canceling(self.jid)
            self.store.update_recovery(self.jid, phase="cancel_pending", metadata={"cancel_intent": True})
            return original(*args, **kwargs)
        with patch.object(self.runner, "checkpoint_remote", side_effect=stop_before_metadata_write):
            self.service._checkpoint_job(self.service.store.get("run", "run", **self.scope), self.scope)
        self.assertTrue(self.store.get(self.jid)["recovery_metadata"]["cancel_intent"])
        self.transport = RemoteReceiptTransport("running", acknowledged=False)
        with patch.object(self.service, "_connect", side_effect=self.controlled_connect):
            self.service._drive("run", self.scope)
        self.assertTrue(self.service.store.get("run", "run", **self.scope)["cancel_intent"])

    def test_publish_winning_a_pending_cancel_keeps_committed_result(self):
        entered, leave = threading.Event(), threading.Event()
        self.addCleanup(leave.set)
        original = self.service.store.request_cancel
        def delayed_cancel(*args, **kwargs):
            entered.set()
            if not leave.wait(10):
                raise AssertionError("Cancel was not released")
            return original(*args, **kwargs)
        answers, errors = [], []
        def cancel():
            try:
                answers.append(self.service.cancel("run", **self.scope))
            except BaseException as exc:
                errors.append(exc)
        with patch.object(self.service.store, "request_cancel", side_effect=delayed_cancel):
            thread = threading.Thread(target=cancel)
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual("succeeded", self.publish()["status"])
            finally:
                leave.set()
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual("succeeded", answers[0]["status"])
        self.assertEqual(self.result, answers[0]["result"])
        self.assertFalse(answers[0]["cancel_intent"])
        self.assertEqual(self.manifest, json.loads((self.root / "manifest.json").read_text(encoding="utf-8")))

    def test_cancel_winning_publication_does_not_publish_manifest(self):
        self.service.store.request_cancel("run", self.scope)
        published = self.publish()
        self.assertEqual("canceled", published["status"])
        self.assertTrue(published["cancel_intent"])
        self.assertNotIn("result", published)
        self.assertFalse((self.root / "manifest.json").exists())

    def test_sessionless_bridge_does_not_leave_workspace_leases_on_loaded_runtime(self):
        scope = {**self.scope, "workspace_id": "mounted-workspace"}
        job = self.store.create("commit-test", "remote_training", workspace_id=scope["workspace_id"], execution_backend="remote",
                                recovery_metadata={"run_id": "workspace-run", "owner_id": "local-default"})
        self.store.mark_queued(job["id"])
        self.store.mark_started(job["id"])
        plan = self.service.store.get("plan", "plan", **self.scope)
        self.service.store.put("plan", {**plan, **scope, "id": "workspace-plan"})
        run = self.service.store.get("run", "run", **self.scope)
        self.service.store.put("run", {**run, **scope, "id": "workspace-run", "run_id": "workspace-run",
                                       "job_id": job["id"], "plan_id": "workspace-plan"})
        self.transport = RemoteReceiptTransport("running", acknowledged=False)
        with patch.object(self.service, "_connect", side_effect=self.controlled_connect), \
                patch("data.workspace.workspace_manager.acquire_job", return_value=object()) as acquire, \
                patch("data.workspace.workspace_manager.release_job"):
            self.recover_without_session()
            self.recover_without_session()
            acquire.assert_not_called()
            # A live restored session keeps the normal runtime lifetime policy.
            session = SimpleNamespace(workspace_id=scope["workspace_id"], job_runner=self.runner)
            with patch("data.session.get_global_jobs_store", return_value=self.store):
                reconcile_once(self.service, SimpleNamespace(get=lambda _sid: session))
            acquire.assert_called_once_with(scope["workspace_id"])
            self.runner._release_job_lease(job["id"])

    def test_detaching_recovery_runner_retains_concurrently_attached_live_runner(self):
        temporary = JobRunner("commit-test", self.store, acquire_workspace_leases=False)
        self.addCleanup(temporary.shutdown)
        run = self.service.store.get("run", "run", **self.scope)
        self.service._job(run, self.scope, temporary)
        self.assertIn(self.jid, temporary._remote_cancel_handlers)
        self.service.attach_job_runner("run", self.runner)
        self.service.detach_job_runner(temporary)
        self.assertIs(self.runner, self.service.job_runners["run"])
        self.assertEqual({}, temporary._remote_cancel_handlers)
        self.service._job(run, self.scope)
        with patch.object(self.service, "cancel", return_value={"status": "canceling"}) as cancel:
            self.assertTrue(self.runner.cancel(self.jid))
        cancel.assert_called_once_with("run", **self.scope)


if __name__ == "__main__":
    unittest.main(verbosity=2)
