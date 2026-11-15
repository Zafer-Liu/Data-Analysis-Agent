"""Offline HTTP/Workflow integration checks for the execution context contract."""
from __future__ import annotations

from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flask import Flask

from agent.execution import ExecutionContext, ExecutionDeadlineExceeded
from agent.jobs import JobCanceled
from agent.workflows.models import NodeRunStatus
from agent.workflows.runtime import WorkflowRuntime
from agent.workflows.scheduler import WorkflowScheduler


class WorkflowExecutionTests(unittest.TestCase):
    def runtime(self):
        runtime = WorkflowRuntime.__new__(WorkflowRuntime)
        snapshot = SimpleNamespace(release=Mock())
        runtime.session = SimpleNamespace(acquire_data_source_snapshot=Mock(return_value=snapshot))
        runtime.workspace = SimpleNamespace(permission="read_write", workspace_id="w")
        runtime.session_id = "workflow-execution-test"
        return runtime, snapshot

    def test_job_cancel_waits_for_actual_child_exit_and_never_returns_output(self):
        runtime, snapshot = self.runtime()
        canceled, entered, return_body = threading.Event(), threading.Event(), threading.Event()
        captured, errors, outputs = [], [], []
        ctx = SimpleNamespace(job_id="job", is_canceled=canceled.is_set, record_event=Mock())

        def body(_node, _materials, _ctx, execution, _snapshot):
            captured.append(execution.child())
            entered.set()
            return_body.wait(2)
            return {"result": "late output"}

        runtime._execute_node_body = body

        def work():
            try:
                outputs.append(runtime._execute_node({"type": "agent", "limits": {"max_run_seconds": 10}}, {}, ctx))
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=work)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            canceled.set()
            return_body.set()
            # Event callback fires only after the execution tree exits. The
            # wrapper cannot return to JobRunner and drop its lease beforehand.
            self.assertFalse(captured[0].parent.wait_for_exit(0.05))
            self.assertTrue(worker.is_alive())
            snapshot.release.assert_not_called()
        finally:
            return_body.set()
            if captured:
                captured[0].acknowledge_exit()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual([], outputs)
        self.assertEqual(1, len(errors))
        self.assertIsInstance(errors[0], JobCanceled)
        snapshot.release.assert_called_once_with()
        for args, kwargs in ctx.record_event.call_args_list:
            json.dumps({"type": args[0], **kwargs})

    def test_workflow_agent_receives_context_and_server_profile_policy(self):
        runtime, snapshot = self.runtime()
        profile = {"role": "verifier", "instructions": "Check synthetic evidence", "allowed_tools": ["query_data"]}
        runtime.workflow_store = Mock()
        runtime.workflow_store.get_agent_profile.return_value = profile
        agent = SimpleNamespace(data_source=object(), _run_delegated_llm=Mock(return_value={
            "content": '{"decision":"pass","issues":[],"evidence":[]}', "tool_events": [],
        }))
        node = {"node_id": "review", "type": "verifier", "agent_profile_id": "p", "output_contract": ["verification"], "side_effects": []}
        with patch("api.chat._build_agent", return_value=agent) as build:
            output = runtime._execute_node(node, {}, SimpleNamespace(job_id="job", is_canceled=lambda: False))
        self.assertEqual("pass", output["verification"]["decision"])
        self.assertTrue(agent._execution_context.exit_acknowledged)
        self.assertTrue(agent._tool_invocation_policy.read_only)
        self.assertEqual(frozenset({"query_data"}), agent._tool_invocation_policy.allowed_tools)
        self.assertTrue(agent._run_delegated_llm.call_args.kwargs["member"]["_structured_output_only"])
        self.assertIn("missing_evidence", agent._run_delegated_llm.call_args.kwargs["prompt"])
        self.assertIs(snapshot, build.call_args.kwargs["source_snapshot"])
        snapshot.release.assert_called_once_with()

    def test_deadline_prevents_publication_after_blocking_body(self):
        runtime, snapshot = self.runtime()
        def body(_node, _materials, _ctx, execution, _snapshot):
            execution.deadline = 0
            return {"result": "too late"}
        runtime._execute_node_body = body
        with self.assertRaises(ExecutionDeadlineExceeded):
            runtime._execute_node({"type": "agent"}, {}, SimpleNamespace(job_id="job", is_canceled=lambda: False))
        snapshot.release.assert_called_once_with()

    def scheduler(self, decision, *, iteration=0):
        scheduler = WorkflowScheduler.__new__(WorkflowScheduler)
        node_run = {"id": "nr", "node_id": "review", "agent_profile_id": "p", "job_id": "j", "status": "running", "iteration": iteration, "attempt": 1, "created_at": "2026-01-01T00:00:00"}
        scheduler.run_store = Mock(workspace_id="w")
        scheduler.run_store.list_node_runs.return_value = [node_run]
        scheduler.job_runner = Mock()
        scheduler.job_runner.get_status.return_value = {"status": "succeeded", "result": {"verification": {"decision": decision, "issues": [], "evidence": [], "missing_evidence": []}}}
        scheduler.limiter = Mock()
        scheduler._open_node_approval = Mock()
        scheduler._requires_key_approval = Mock(return_value=False)
        return scheduler, {"nodes": [{"node_id": "review", "type": "verifier"}], "edges": []}

    def test_named_escalation_and_exhausted_rework_block_success(self):
        for decision, iteration in (("escalate", 0), ("rework", 2)):
            with self.subTest(decision=decision):
                scheduler, graph = self.scheduler(decision, iteration=iteration)
                scheduler._reconcile_jobs("run", graph)
                scheduler._open_node_approval.assert_called_once()
                scheduler.run_store.create_retry_iteration.assert_not_called()
                self.assertNotIn(NodeRunStatus.SUCCEEDED, [call.args[1] for call in scheduler.run_store.transition_node.call_args_list])

    def test_rework_uses_bounded_retry_and_pass_can_finish(self):
        scheduler, graph = self.scheduler("rework")
        scheduler._reconcile_jobs("run", graph)
        scheduler.run_store.create_retry_iteration.assert_called_once_with("nr", max_iteration=2)
        scheduler._open_node_approval.assert_not_called()
        scheduler, graph = self.scheduler("pass")
        scheduler._reconcile_jobs("run", graph)
        self.assertEqual(NodeRunStatus.SUCCEEDED, scheduler.run_store.transition_node.call_args.args[1])


class A2AExecutionTests(unittest.TestCase):
    def test_canceled_a2a_retains_snapshot_and_member_until_actual_child_exit(self):
        import api.team_a2a as api
        from agent.teams.a2a import A2AError, task_state, text_message
        from agent.tools.workspace.teams import WorkspaceTeamStore

        with tempfile.TemporaryDirectory() as folder, patch("agent.tools.workspace.teams.content_data_path", return_value=Path(folder) / "teams.json"):
            store = WorkspaceTeamStore("a2a-execution-test", workspace_id="")
            store.create_agent("analyst")
            entered, leave = threading.Event(), threading.Event()
            snapshot = SimpleNamespace(release=Mock())
            session = SimpleNamespace(session_id="a2a-execution-test", model_provider="offline", acquire_data_source_snapshot=Mock(return_value=snapshot))
            agent = SimpleNamespace(_provider="offline")
            children, tasks, errors = [], [], []

            def delegate(**kwargs):
                children.append(kwargs["member"]["_execution_context"].child())
                entered.set()
                leave.wait(3)
                return {"content": "late provider result", "tool_events": []}

            agent._run_delegated_llm = delegate
            usage = Mock()
            def send():
                try:
                    tasks.append(api._send_message(store, session, "fixture-user", "local", "default", "analyst", {"message": text_message("fixture request")}))
                except BaseException as exc:
                    errors.append(exc)

            with patch.object(api, "_build_agent", return_value=agent), patch.object(api, "_RequestUsage", return_value=usage), patch.object(api, "_result_invalid_reason", return_value=""), patch.object(api.config_manager, "get_hosted_model", return_value=None), patch.object(api, "log"):
                worker = threading.Thread(target=send)
                worker.start()
                try:
                    self.assertTrue(entered.wait(2))
                    member = store.member("default", "analyst")
                    store.cancel_task("default", member["active_task_id"])
                    snapshot.release.assert_not_called()
                    leave.set()
                    worker.join(3)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual([], errors)
                    self.assertEqual("canceled", task_state(tasks[0]))
                    self.assertEqual("running", store.member("default", "analyst")["status"])
                    with self.assertRaises(A2AError):
                        store.submit_task("default", "analyst", text_message("overlap"))
                    snapshot.release.assert_not_called()
                finally:
                    leave.set()
                    for child in children:
                        child.acknowledge_exit()
                    worker.join(3)
            self.assertEqual("idle", store.member("default", "analyst")["status"])
            snapshot.release.assert_called_once_with()
            usage.settle.assert_called_once_with()
            self.assertNotIn("late provider result", store._path.read_text(encoding="utf-8"))

    def test_plan_job_cancellation_does_not_drop_live_execution_lease(self):
        import api.teams as api
        app = Flask(__name__)
        app.register_blueprint(api.bp)
        parent = ExecutionContext()
        child = parent.child()
        runner = Mock()
        plan = {"id": "p", "team_name": "default", "status": "canceled", "tasks": [{"a2a_task_id": "t", "job_id": "j"}]}
        try:
            with patch("api.state.check_session_ownership", return_value=(True, "fixture")), patch("api.state.session_manager.get", return_value=SimpleNamespace(job_runner=runner)), patch("agent.teams.dynamic_plans.DynamicTeamPlanStore") as plans, patch.object(api, "WorkspaceTeamStore") as stores:
                plans.return_value.control.return_value = plan
                stores.return_value.cancel_task.return_value = {"metadata": {"execution": parent.to_dict()}}
                with app.test_client() as client:
                    response = client.post("/api/session/fixture/team-plans/p/actions/cancel")
                self.assertEqual(200, response.status_code)
                runner.cancel.assert_called_once_with("j")
                runner.cancel_tracked.assert_not_called()
                parent.acknowledge_exit()
                runner.cancel_tracked.assert_not_called()
                child.acknowledge_exit()
                runner.cancel_tracked.assert_called_once_with("j")
        finally:
            child.acknowledge_exit()
            parent.acknowledge_exit()


class _TrackedRunner:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()

    def begin_tracked(self, *_args, **_kwargs):
        with self.lock:
            jid = "job-" + str(len(self.jobs) + 1)
            self.jobs[jid] = {"id": jid, "status": "running", "workspace_id": ""}
            return jid

    def get_status(self, jid):
        return dict(self.jobs[jid])

    def fail_tracked(self, jid, error):
        self.jobs[jid].update(status="failed", error=error)

    def succeed_tracked(self, jid, result):
        self.jobs[jid].update(status="succeeded", result=result)

    def cancel(self, jid):
        self.jobs[jid].update(status="canceling")
        return True

    def cancel_tracked(self, jid):
        self.jobs[jid].update(status="canceled")

    def conversation_scope(self, _jid):
        return nullcontext()

    def append_tracked_event(self, *_args, **_kwargs):
        pass


class ChatExecutionTests(unittest.TestCase):
    def setUp(self):
        import api.chat as chat
        self.chat = chat
        self.app = Flask(__name__)
        self.app.register_blueprint(chat.bp)
        self.runner = _TrackedRunner()
        self.snapshots = []
        def acquire():
            snapshot = SimpleNamespace(combined_schema="", release=Mock())
            self.snapshots.append(snapshot)
            return snapshot
        self.session = SimpleNamespace(
            model_provider="fixture", total_input_tokens=0, total_output_tokens=0, total_cached_input_tokens=0,
            history=[], job_runner=self.runner, cancel_requested=False,
            record_discovered_tools=Mock(return_value=[]), acquire_data_source_snapshot=acquire,
            record_activation=Mock(), record_command_metric=Mock(), build_recovery_context=Mock(return_value=""),
            add_user=Mock(), add_assistant=Mock(), session_id="chat-execution-test",
        )
        from agent.activation import ActivationContext
        self.patch("api.chat.session_manager.get_or_create", return_value=self.session)
        self.patch("api.chat.session_manager.get", return_value=self.session)
        self.patch("api.chat.check_session_ownership", return_value=(True, "test"))
        self.patch("api.chat.config_manager.get_hosted_model", return_value=None)
        self.patch("api.auth.run_mode", return_value="local")
        self.patch("api.chat._resolve_activation", return_value=(ActivationContext(), None, None))
        self.patch("api.chat._resolve_data_context", return_value=None)
        self.patch("api.chat._apply_sql_analysis_context", return_value=[])
        self.patch("api.chat._build_team_context", return_value="")
        self.patch("filehistory.for_session", return_value=None)
        self.patch("data.hooks_store.load_engine", side_effect=RuntimeError("offline hooks"))

    def patch(self, name, *args, **kwargs):
        patcher = patch(name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def test_scoped_stop_and_overlap_hold_snapshot_until_model_returns(self):
        entered, leave = threading.Event(), threading.Event()
        agent = SimpleNamespace()
        def run(*_args, **_kwargs):
            entered.set()
            leave.wait(3)
            yield {"type": "token", "content": "late untrusted publication"}
        agent.run = run
        build = self.patch("api.chat._build_agent", return_value=agent)
        responses = []
        def request_chat():
            with self.app.test_client() as client:
                response = client.post("/api/session/chat-execution-test/chat", json={"message": "synthetic analysis", "request_id": "turn-one", "memory_enabled": False})
                responses.append(response.get_data(as_text=True))
        worker = threading.Thread(target=request_chat)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            first_snapshot = self.snapshots[0]
            with self.app.test_client() as client:
                stale_stop = client.post("/api/session/chat-execution-test/stop", json={"request_id": "older-turn"}).get_json()
                self.assertFalse(stale_stop["stopping"])
                self.assertFalse(agent._execution_context.cancellation_requested)
                stopped = client.post("/api/session/chat-execution-test/stop", json={"request_id": "turn-one"}).get_json()
                self.assertTrue(stopped["stopping"])
                overlap = client.post("/api/session/chat-execution-test/chat", json={"message": "overlap", "request_id": "turn-two"})
                self.assertEqual(409, overlap.status_code)
                self.assertEqual("execution_in_progress", overlap.get_json()["code"])
            self.assertEqual("canceling", self.runner.jobs["job-1"]["status"])
            first_snapshot.release.assert_not_called()
            build.assert_called_once()
        finally:
            leave.set()
            worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertIn('"type": "stopped"', responses[0])
        self.assertNotIn("late untrusted publication", responses[0])
        self.assertEqual("canceled", self.runner.jobs["job-1"]["status"])
        first_snapshot.release.assert_called_once_with()
        self.session.add_assistant.assert_not_called()

    def test_response_closed_before_iteration_releases_request(self):
        from agent.execution import begin_request_execution, finish_request_execution
        with self.app.test_request_context("/api/session/chat-execution-test/chat", method="POST", json={"message": "fixture", "request_id": "unstarted"}):
            response = self.chat.chat_stream("chat-execution-test")
            self.assertEqual("unstarted", response.headers["X-Request-ID"])
            response.close()
        self.snapshots[0].release.assert_called_once_with()
        self.assertEqual("canceled", self.runner.jobs["job-1"]["status"])
        next_request = begin_request_execution("chat-execution-test")
        finish_request_execution("chat-execution-test", next_request)

    def test_activation_setup_failure_releases_request(self):
        self.session.record_activation.side_effect = RuntimeError("offline persistence failure")
        with self.app.test_client() as client:
            for _ in range(2):
                response = client.post("/api/session/chat-execution-test/chat", json={"message": "fixture"})
                self.assertEqual(500, response.status_code)
                self.assertEqual("execution_setup_failed", response.get_json()["code"])
        for snapshot in self.snapshots:
            snapshot.release.assert_called_once_with()

    def test_build_failure_acknowledges_root_and_allows_next_request(self):
        self.patch("api.chat._build_agent", side_effect=RuntimeError("offline build"))
        with self.app.test_client() as client:
            for _ in range(2):
                response = client.post("/api/session/chat-execution-test/chat", json={"message": "fixture"})
                self.assertEqual(200, response.status_code)
                self.assertIn('"type": "error"', response.get_data(as_text=True))
        self.assertEqual(["failed", "failed"], [job["status"] for job in self.runner.jobs.values()])
        for snapshot in self.snapshots:
            snapshot.release.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
