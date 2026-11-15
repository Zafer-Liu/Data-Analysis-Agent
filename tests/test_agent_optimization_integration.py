"""Controlled model/tool contracts; no provider calls or application server."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import tempfile
import threading
import uuid
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from agent.agent import (
    BusinessAgent, _CURRENT_EXECUTION, _CURRENT_INVOCATION_POLICY, _CURRENT_TURN_STATE,
    _quality_review_blocking_reason, _remember_turn_tool_result_artifacts,
    _scope_tool_result_reader,
)
from agent.activation import ActivationContext
from agent.execution import ExecutionContext, ExecutionStopped
from agent.number_audit import audit_report_numbers
from agent.tools.invocation import ToolAuthorizationError, ToolInvocationPolicy
from agent.tools.results import read_evidence_claim, ToolResultAccessError
from agent.tools.schemas import AGENT_TOOLS
from agent.tools.workspace.teams import WorkspaceTeamStore


def tool_call(name, args, identity="tool-1"):
    return NS(id=identity, function=NS(name=name, arguments=json.dumps(args)))


def response(content="", calls=(), usage=None):
    return NS(model="", usage=usage, choices=[NS(message=NS(content=content, tool_calls=list(calls)))])


def streamed(content="", calls=()):
    def stream():
        delta_calls = [NS(index=i, id=call.id, function=call.function) for i, call in enumerate(calls)]
        delta = NS(content=content, reasoning_content=None, tool_calls=delta_calls)
        yield NS(usage=None, choices=[NS(delta=delta, finish_reason="tool_calls" if calls else "stop")])
    return stream()


class FakeModel:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.lock = threading.Lock()
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        with self.lock:
            self.calls.append(copy.deepcopy(kwargs))
            if not self.responses:
                raise AssertionError("Unexpected model invocation")
            item = self.responses.pop(0)
        return item(kwargs) if callable(item) else item


class EngineContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.session_id = "core-offline-session"
        self.patchers = []
        self.apply_patch("agent.agent.get_mcp_manager", return_value=NS(get_all_openai_schemas=lambda: []))
        self.apply_patch("agent.tools.workspace.teams.content_data_path", return_value=self.root / "teams.json")
        self.apply_patch("agent.tools.results._GLOBAL_RESULT_ROOT", self.root / "tool_results")
        self.apply_patch("agent.tools.results._evidence_root", return_value=self.root / "evidence_claims")
        self.apply_patch("agent.agent.audit_model_call", side_effect=lambda **_kwargs: NS(
            attempt=Mock(), succeed=Mock(), fail=Mock(), missing_usage=Mock(), cancel=Mock(),
            request_id="offline-audit-" + uuid.uuid4().hex,
        ))
        self.apply_patch("agent.agent.load_instruction_section", return_value="")
        self.apply_patch("agent.agent.render_memory_section", return_value="")
        self.apply_patch("agent.agent.WorkspaceTaskStore", Mock())

    def apply_patch(self, *args, **kwargs):
        patcher = patch(*args, **kwargs)
        self.patchers.append(patcher)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def agent(self, model=None, *, data=True, **kwargs):
        model = model or FakeModel(response("A complete offline answer with no invented values."))
        agent = BusinessAgent(model, "selected-model", session_id=self.session_id, workspace_id="",
                              data_source=NS(name="offline") if data else None,
                              combined_schema="table t(value INTEGER)" if data else None,
                              provider="offline-provider", **kwargs)
        agent._prepare_ontology_knowledge = lambda _message: False
        agent._workspace_path_authorization = lambda: None
        return agent

    def run_agent(self, agent, text="Explain the supplied data", **kwargs):
        return list(agent.run(text, [], auto_match_skill=False, memory_enabled=False, **kwargs))

    def scoped(self, agent, *, context=None, policy=None, grants=()):
        state = {"grants": list(grants), "number_evidence": [], "data_tools": set(), "parent_claim_ids": []}
        tokens = [(_CURRENT_TURN_STATE, _CURRENT_TURN_STATE.set((id(agent), state)))]
        if context is not None:
            tokens.append((_CURRENT_EXECUTION, _CURRENT_EXECUTION.set((id(agent), context))))
        if policy is not None:
            tokens.append((_CURRENT_INVOCATION_POLICY, _CURRENT_INVOCATION_POLICY.set((id(agent), policy))))
        for variable, token in tokens:
            self.addCleanup(variable.reset, token)
        return state

    def test_main_model_uses_policy_not_visible_twenty_tool_cap(self):
        model = FakeModel(streamed(calls=[tool_call("get_schema", {})]), streamed("Schema was read."))
        agent = self.agent(model)
        agent._tool_get_schema = Mock(return_value="table t(value INTEGER)")
        self.apply_patch("agent.agent.limit_tools_for_turn", return_value=[])
        events = self.run_agent(agent)
        agent._tool_get_schema.assert_called_once()
        self.assertTrue(any(event.get("type") == "tool_audit" and event.get("ok") for event in events))
        self.assertEqual(2, len(model.calls))

    def test_native_empty_parent_whitelist_denies(self):
        model = FakeModel(streamed(calls=[tool_call("get_schema", {})]), streamed("Cannot access schema."))
        agent = self.agent(model)
        agent._tool_invocation_policy = ToolInvocationPolicy(has_data_source=True, allowed_tools=frozenset())
        agent._tool_get_schema = Mock()
        events = self.run_agent(agent)
        agent._tool_get_schema.assert_not_called()
        self.assertTrue(any(event.get("error") == "capability_denied" for event in events))

    def test_fallback_cannot_bypass_activation_or_parent(self):
        text = '<display_diagram>{"title":"x","mode":"prompt","prompt":"draw x"}</display_diagram>'
        model = FakeModel(streamed(text), streamed("No diagram was created."))
        agent = self.agent(model, data=False)
        handler = self.apply_patch("agent.tools.business.diagram.handle_display_diagram")
        events = self.run_agent(agent, "Say hello")
        handler.assert_not_called()
        self.assertTrue(any(event.get("error") == "activation_required" for event in events))
        model2 = FakeModel(streamed(text), streamed("No diagram was created."))
        agent2 = self.agent(model2, data=False)
        agent2._tool_invocation_policy = ToolInvocationPolicy(allowed_tools=frozenset())
        events = self.run_agent(agent2, "Create a SWOT diagram")
        handler.assert_not_called()
        self.assertTrue(any(event.get("error") == "capability_denied" for event in events))

    def test_fallback_uses_argument_validation_and_hooks(self):
        text = '<display_diagram>{"title":"x","mode":"prompt","prompt":"draw x"}</display_diagram>'
        model = FakeModel(streamed(text), streamed("Diagram tool was blocked."))
        hooks = NS(run_hooks=Mock(return_value=[]), drain_prompt_messages=Mock(return_value=[]),
                   run_pre_tool_hooks=Mock(return_value=NS(reason="offline gate", hook_id="gate")),
                   drain_notifications=Mock(return_value=[]))
        agent = self.agent(model, data=False, hook_engine=hooks)
        handler = self.apply_patch("agent.tools.business.diagram.handle_display_diagram")
        events = self.run_agent(agent, "Create a SWOT diagram")
        handler.assert_not_called()
        hooks.run_pre_tool_hooks.assert_called_once()
        self.assertTrue(any(str(event.get("error", "")).startswith("hook_rejected") for event in events))
        self.assertTrue(any("[TOOL_RESULT]" in message.get("content", "")
                            for message in model.calls[-1]["messages"] if message.get("role") == "tool"))

    def test_model_confirm_argument_cannot_authorize_delete(self):
        agent = self.agent()
        policy = ToolInvocationPolicy(has_data_source=True, discovered_tools=frozenset({"delete_analysis_tables"}))
        with self.assertRaises(ToolAuthorizationError):
            agent._authorize_tool("delete_analysis_tables", {"confirm": True}, policy=policy)

    def test_delegate_parent_policy_and_empty_child_whitelist(self):
        model = FakeModel(response(calls=[tool_call("query_data", {"sql": "SELECT 1"})]), response("No authorized query was executed; evidence is unavailable."))
        agent = self.agent(model)
        agent._tool_query_data = Mock()
        parent = ToolInvocationPolicy(has_data_source=True, allowed_tools=frozenset({"get_schema"}))
        result = agent._run_delegated_llm(member={}, prompt="Query data", allowed_tools={"query_data"}, invocation_policy=parent)
        agent._tool_query_data.assert_not_called()
        self.assertFalse(result["tool_events"][0]["envelope"]["ok"])
        self.assertEqual("capability_denied", result["tool_events"][0]["error"])
        agent._tool_get_schema = Mock()
        denied = agent._execute_delegated_tool("get_schema", {}, allowed_tools=frozenset())
        self.assertIn("Unauthorized", denied)
        agent._tool_get_schema.assert_not_called()

    def test_delegated_envelope_full_persistence_claim_and_numeric_channel(self):
        model = FakeModel(response(calls=[tool_call("query_data", {"sql": "SELECT value FROM t"})]),
                          response("The member claims 2956 rows and 88 percent without evidence."))
        agent = self.agent(model)
        raw = "1956 rows\n" + "line with actual data\n" * 300 + "tail 2468"
        agent._tool_query_data = Mock(return_value=raw)
        result = agent._run_delegated_llm(member={}, prompt="Read actual values")
        event = result["tool_events"][0]
        self.assertEqual("tool_result", event["envelope"]["type"])
        self.assertTrue(event["envelope"]["ok"])
        self.assertLessEqual(len(event["result"]), 2000)
        claims = [item for item in result["evidence"]["artifacts"] if item.get("claim_id")]
        self.assertEqual(1, len(claims))
        claim = claims[0]
        page = read_evidence_claim(claim["claim_id"], allowed_artifacts=claims,
                                   session_id=self.session_id, section="result", offset=len(raw) - 20)
        self.assertIn("tail 2468", page["content"])
        self.assertIn("request_id", json.loads((self.root / "evidence_claims" / (claim["claim_id"] + ".json")).read_text(encoding="utf-8"))["execution_context"])
        self.assertEqual([raw], result["_number_evidence"])
        self.assertEqual((["2956", "88%"], 2), audit_report_numbers("2956 rows, 88%", result["_number_evidence"]))

    def test_claim_reader_preserves_grants_and_rejects_cross_session(self):
        agent = self.agent()
        state = self.scoped(agent)
        envelope = agent._make_execution_tool_result("query_data", "1956 rows", args={"sql": "SELECT 1"}, session_id=self.session_id)
        claim = next(item for item in envelope.artifacts if item.get("claim_id"))
        grants = []
        _remember_turn_tool_result_artifacts(grants, [claim], session_id=self.session_id)
        reader_schema = next(item for item in AGENT_TOOLS if item["function"]["name"] == "read_tool_result")
        scoped = _scope_tool_result_reader([reader_schema], grants)
        self.assertIn(claim["claim_id"], scoped[0]["function"]["parameters"]["properties"]["artifact_id"]["enum"])
        page = agent._read_controlled_result({"artifact_id": claim["claim_id"], "section": "sql", "limit": 99999})
        self.assertEqual("SELECT 1", page["content"])
        self.assertLessEqual(page["returned_chars"], 4000)
        agent._session_id = "other-session"
        with self.assertRaises(ToolResultAccessError):
            agent._read_controlled_result({"artifact_id": claim["claim_id"], "section": "result"})

    def test_structured_review_is_raw_json_and_rejects_contradictory_pass(self):
        model = FakeModel(response(calls=[tool_call("get_schema", {})]), response(json.dumps({
            "decision": "pass", "issues": ["bad data"], "evidence": [], "missing_evidence": [],
        })))
        agent = self.agent(model)
        agent._tool_get_schema = Mock(return_value="table t(value INTEGER)")
        result = agent._run_delegated_llm(member={}, prompt="Review", quality_review=True)
        self.assertEqual("needs_review", result["status"])
        self.assertEqual("escalate", json.loads(result["content"])["decision"])
        self.assertNotIn("工具使用", result["content"])
        self.assertTrue(_quality_review_blocking_reason("通过项：全部通过"))
        self.assertFalse(_quality_review_blocking_reason(json.dumps({"decision": "pass", "issues": [], "evidence": [], "missing_evidence": []})))

    def test_single_delegate_review_failure_marks_parent_envelope_failure(self):
        model = FakeModel(response("The actual member output is complete and contains no numeric assertions."),
                          response(json.dumps({"decision": "escalate", "issues": [], "evidence": [], "missing_evidence": ["query result unavailable"]})))
        agent = self.agent(model)
        state = self.scoped(agent)
        generator = agent._delegate_member_task({"prompt": "Inspect the supplied business material"})
        while True:
            try:
                next(generator)
            except StopIteration as stop:
                result = stop.value
                break
        self.assertEqual("needs_review", result["status"])
        envelope = agent._make_execution_tool_result("agent_delegate", result, session_id=self.session_id)
        self.assertFalse(envelope.ok)
        self.assertEqual("needs_review", envelope.error)
        store = WorkspaceTeamStore(self.session_id, workspace_id="")
        self.assertEqual("escalate", store.get("default")["quality_review"]["decision"])

    def test_minimal_legacy_fixture_refuses_mutating_sql(self):
        agent = BusinessAgent.__new__(BusinessAgent)
        agent._tool_query_data = Mock()
        for sql in ("DELETE FROM t", "CREATE TABLE stolen AS SELECT * FROM t", "SELECT 1; DROP TABLE t"):
            denied = agent._execute_delegated_tool("query_data", {"sql": sql}, allowed_tools={"query_data"})
            self.assertIn("error", denied.lower())
            self.assertIn("read-only", denied)
        agent._tool_query_data.assert_not_called()

    def test_usage_preserves_each_failed_retry_and_measured_call(self):
        def failed(_kwargs):
            raise RuntimeError("503 transient")
        usage = NS(prompt_tokens=20, completion_tokens=4, total_tokens=24,
                   prompt_tokens_details=NS(cached_tokens=0))
        model = FakeModel(failed, response("Complete measured response.", usage=usage))
        agent = self.agent(model)
        self.apply_patch("agent.retry.time.sleep", return_value=None)
        result = agent._run_delegated_llm(member={}, prompt="Write")
        calls = result["usage"]["model_calls"]
        self.assertEqual([1, 2], [item["attempt"] for item in calls])
        self.assertEqual(["failed", "succeeded"], [item["status"] for item in calls])
        self.assertIsNone(calls[0]["input_tokens"])
        self.assertEqual("unavailable", calls[0]["usage_source"])
        self.assertEqual(20, calls[1]["input_tokens"])
        self.assertTrue(calls[1]["usage_available"])
        self.assertEqual("provider_reported", calls[1]["usage_source"])

    def test_workflow_structured_output_flag_omits_tool_footer(self):
        model = FakeModel(response(calls=[tool_call("get_schema", {})]), response('{"result":"complete"}'))
        agent = self.agent(model)
        agent._tool_get_schema = Mock(return_value="table t(value INTEGER)")
        result = agent._run_delegated_llm(member={"_structured_output_only": True}, prompt="Produce structured result")
        self.assertEqual({"result": "complete"}, json.loads(result["content"]))
        self.assertNotIn("工具使用", result["content"])

    def test_default_model_inheritance_and_no_shared_mutation(self):
        model = FakeModel(response("Complete inherited response."))
        agent = self.agent(model)
        result = agent._run_delegated_llm(member={"profile": {"model_tier": "low_cost"}}, prompt="Write")
        self.assertEqual("selected-model", model.calls[0]["model"])
        self.assertFalse(result["model_routing"]["applied"])
        self.assertEqual("selected-model", agent.model)
        self.assertIsNone(result["usage"]["input_tokens"])
        self.assertFalse(result["usage"]["usage_available"])
        self.assertEqual("unavailable", result["usage"]["usage_source"])

    def test_approved_routing_is_local_and_other_provider_is_refused(self):
        model = FakeModel(response("Routed response."), response("Inherited response."))
        agent = self.agent(model)
        agent._approved_role_model_policy = {"approved": True, "version": 1, "policy_id": "p", "tiers": {
            "low_cost": {"provider": "offline-provider", "model": "approved-small"}}}
        agent._model_routing_gate = {"eligible": True, "eligibility": "eligible", "measurement_mode": "actual_model",
            "policy_id": "p", "manifest_hash": "m", "comparison_hash": "c", "reasons": [],
            "models": [{"provider": "offline-provider", "model": "approved-small"}]}
        first = agent._run_delegated_llm(member={"profile": {"model_tier": "low_cost"}}, prompt="Write")
        self.assertEqual("approved-small", model.calls[0]["model"])
        self.assertTrue(first["model_routing"]["applied"])
        self.assertEqual("selected-model", agent.model)
        agent._approved_role_model_policy["tiers"]["low_cost"]["provider"] = "unapproved-provider"
        agent._run_delegated_llm(member={"profile": {"model_tier": "low_cost"}}, prompt="Write")
        self.assertEqual("selected-model", model.calls[1]["model"])

    def test_cancel_during_model_prevents_tools_or_late_publication(self):
        started, release = threading.Event(), threading.Event()
        def blocked(_kwargs):
            started.set()
            self.assertTrue(release.wait(3))
            return response(calls=[tool_call("query_data", {"sql": "SELECT 1"})])
        model = FakeModel(blocked)
        agent = self.agent(model)
        agent._tool_query_data = Mock()
        root = ExecutionContext()
        self.addCleanup(root.acknowledge_exit)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(agent._run_delegated_llm, member={}, prompt="Query", execution_context=root)
            self.assertTrue(started.wait(2))
            root.cancel("test cancellation")
            root.acknowledge_exit()
            self.assertFalse(root.exit_acknowledged)
            release.set()
            with self.assertRaises(ExecutionStopped):
                future.result(3)
        self.assertTrue(root.wait_for_exit(1))
        self.assertEqual(1, len(model.calls))
        agent._tool_query_data.assert_not_called()

    def test_cancel_during_tool_retains_child_until_actual_exit(self):
        started, release = threading.Event(), threading.Event()
        model = FakeModel(response(calls=[tool_call("query_data", {"sql": "SELECT 1"})]))
        agent = self.agent(model)
        def blocked(_sql):
            started.set()
            self.assertTrue(release.wait(3))
            return "1956 rows"
        agent._tool_query_data = blocked
        root = ExecutionContext()
        self.addCleanup(root.acknowledge_exit)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(agent._run_delegated_llm, member={}, prompt="Query", execution_context=root)
            self.assertTrue(started.wait(2))
            root.cancel("test cancellation")
            root.acknowledge_exit()
            self.assertFalse(root.exit_acknowledged)
            release.set()
            with self.assertRaises(ExecutionStopped):
                future.result(3)
        self.assertTrue(root.wait_for_exit(1))
        self.assertEqual(1, len(model.calls))
        self.assertFalse((self.root / "evidence_claims").exists())

    def test_retry_cancellation_cannot_start_second_model_call(self):
        root = ExecutionContext()
        self.addCleanup(root.acknowledge_exit)
        def failed(_kwargs):
            root.cancel("cancel before retry")
            raise RuntimeError("503 transient")
        model = FakeModel(failed)
        agent = self.agent(model)
        self.apply_patch("agent.retry.time.sleep", return_value=None)
        with self.assertRaises(ExecutionStopped):
            agent._run_delegated_llm(member={}, prompt="Query", execution_context=root)
        self.assertEqual(1, len(model.calls))

    def test_copy_context_workers_isolate_semantics_and_permissions(self):
        agent = self.agent()
        root = ExecutionContext()
        self.addCleanup(root.acknowledge_exit)
        policy = ToolInvocationPolicy(has_data_source=True, allowed_tools=frozenset({"get_schema"}))
        self.scoped(agent, context=root, policy=policy)
        agent._semantic_context = {"context_id": "parent"}
        barrier = threading.Barrier(2)
        def worker(label):
            self.assertEqual("parent", agent._semantic_context["context_id"])
            agent._semantic_context["context_id"] = label
            barrier.wait(2)
            agent._authorize_tool("get_schema", {})
            with self.assertRaises(ToolAuthorizationError):
                agent._authorize_tool("query_data", {"sql": "SELECT 1"})
            return agent._semantic_context["context_id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = agent._submit_execution_worker(pool, worker, "first")
            second = agent._submit_execution_worker(pool, worker, "second")
            self.assertEqual(["first", "second"], [first.result(3), second.result(3)])
        self.assertEqual("parent", agent._semantic_context["context_id"])

    def test_main_turn_with_only_delegated_data_still_audits_final_numbers(self):
        def reviewer(kwargs):
            import re
            claim = re.search(r"ec_[a-f0-9]{32}", json.dumps(kwargs["messages"]))
            self.assertIsNotNone(claim)
            self.assertTrue(any(schema["function"]["name"] == "read_tool_result" for schema in kwargs.get("tools", [])))
            return response(json.dumps({"decision": "pass", "issues": [], "evidence": [claim[0]], "missing_evidence": []}))
        model = FakeModel(
            streamed(calls=[tool_call("agent_delegate", {"prompt": "Query the actual row count"})]),
            response(calls=[tool_call("query_data", {"sql": "SELECT COUNT(*) AS row_count FROM t"})]),
            response("The member claims 4956 rows; the actual query must be checked before using this summary."),
            reviewer,
            streamed("There are 4956 rows, representing 88% of the total."),
            streamed("There are 3956 rows. The denominator for a percentage is unavailable."),
        )
        agent = self.agent(model)
        agent._tool_query_data = Mock(return_value='{"row_count":3956}')
        events = self.run_agent(agent, "Delegate the data inspection")
        agent._tool_query_data.assert_called_once()
        final = next(event["content"] for event in events if event["type"] == "text")
        self.assertIn("3956", final)
        self.assertNotIn("4956", final)
        self.assertEqual(6, len(model.calls))
        repairs = [message["content"] for message in model.calls[-1]["messages"] if message.get("role") == "user"]
        self.assertTrue(any("4956" in text and "88%" in text for text in repairs))
        audits = [event for event in events if event.get("type") == "tool_audit" and event.get("tool") == "agent_delegate"]
        self.assertTrue(audits[0]["ok"])
        claims = [item for item in audits[0]["artifacts"] if item.get("claim_id")]
        self.assertGreaterEqual(len(claims), 2)
        lineage = [json.loads((self.root / "evidence_claims" / (item["claim_id"] + ".json")).read_text(encoding="utf-8")) for item in claims]
        self.assertTrue(any(record["tool"] == "agent_delegate" and record["parent_claim_ids"] for record in lineage))

    def test_model_canceled_before_stream_publication_emits_no_late_text(self):
        root = ExecutionContext()
        self.addCleanup(root.acknowledge_exit)
        def canceled(_kwargs):
            root.cancel("canceled provider")
            return streamed("late provider text")
        model = FakeModel(canceled)
        agent = self.agent(model, data=False)
        agent._execution_context = root
        events = self.run_agent(agent, "Say hello")
        self.assertFalse(any(event.get("type") in {"text", "text_delta"} for event in events))
        self.assertTrue(any(event.get("canceled") for event in events))
        self.assertEqual(1, len(model.calls))

    def test_native_job_bridge_close_retains_worker_execution(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        class Runner:
            def __init__(self):
                self.status = {"status": "running"}
                self.listeners = []
            def create(self, fn, **_kwargs):
                def body():
                    try:
                        fn(NS(check_canceled=lambda: None))
                    except ExecutionStopped:
                        pass
                    finally:
                        self.status = {"status": "canceled"}
                        for callback in self.listeners:
                            callback(self.status)
                        finished.set()
                self.thread = threading.Thread(target=body)
                self.thread.start()
                return "offline-job"
            def add_terminal_listener(self, _jid, callback):
                self.listeners.append(callback)
            def iter_events(self, _jid):
                self_outer.assertTrue(started.wait(2))
                yield {"type": "job_progress"}
                release.wait(3)
            def get_status(self, _jid):
                return self.status
            def cancel(self, _jid):
                self.status = {"status": "canceling"}
        self_outer = self
        runner = Runner()
        agent = self.agent(job_runner=runner)
        root = ExecutionContext()
        self.addCleanup(root.acknowledge_exit)
        self.scoped(agent, context=root)
        def worker(_context):
            started.set()
            release.wait(3)
            return "late result"
        bridge = agent._run_as_job(worker, "offline")
        self.assertEqual("job_progress", next(bridge)["type"])
        bridge.close()
        root.acknowledge_exit()
        self.assertFalse(root.exit_acknowledged)
        release.set()
        self.assertTrue(finished.wait(3))
        runner.thread.join(1)
        self.assertTrue(root.wait_for_exit(1))

    def test_dynamic_team_uses_structured_review(self):
        store = WorkspaceTeamStore(self.session_id, workspace_id="")
        store.create("core-team", "offline", [{"name": "worker", "role": "analyst"}])
        model = FakeModel(
            streamed(calls=[tool_call("team_delegate", {"team_name": "core-team", "assignments": [
                {"member_name": "worker", "prompt": "Review the supplied text", "description": "text review"}
            ], "max_concurrency": 1})]),
            response("The member delivered a complete textual analysis and explicitly documented the remaining uncertainty."),
            response(json.dumps({"decision": "escalate", "issues": [], "evidence": [], "missing_evidence": ["independent evidence"]})),
            streamed("The team result requires review because independent evidence is missing."),
        )
        agent = self.agent(model)
        events = self.run_agent(agent, "Run a parallel team review", teams_enabled=True)
        audits = [event for event in events if event.get("type") == "tool_audit" and event.get("tool") == "team_delegate"]
        self.assertEqual(1, len(audits))
        self.assertFalse(audits[0]["ok"])
        self.assertEqual("needs_review", audits[0]["error"])
        self.assertEqual("escalate", store.get("core-team")["quality_review"]["decision"])


if __name__ == "__main__":
    unittest.main()
