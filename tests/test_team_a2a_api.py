"""Mock-based regression coverage for the team A2A HTTP boundary."""
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

import api.team_a2a as a2a_api
import api.teams as teams_api
from agent.teams.a2a import A2AError


class TeamA2AApiTests(unittest.TestCase):
    rpc_url = "/api/session/sid/teams/research/agents/analyst/a2a"
    card_url = "/api/session/sid/teams/research/agents/analyst/.well-known/agent-card.json"

    def setUp(self):
        app = Flask(__name__)
        app.secret_key = "a2a-test-secret"
        app.register_blueprint(teams_api.bp)
        self.client = app.test_client()
        self.message = {
            "messageId": "message-1", "role": "ROLE_USER",
            "parts": [{"text": "Analyze the sample"}],
        }
        self.task = {
            "id": "task-1", "contextId": "context-1",
            "status": {"state": "TASK_STATE_SUBMITTED"},
            "metadata": {"member_name": "analyst"}, "history": [deepcopy(self.message)],
        }
        self.member = {
            "name": "analyst", "role": "reader",
            "profile": {"allowed_tools": ["query_data"], "max_tool_calls": 1},
            "_a2a_task_id": "task-1", "_a2a_team_name": "research",
        }
        self.snapshot = Mock()
        self.session = SimpleNamespace(
            session_id="sid", owner_user_id="owner", model_provider="provider",
            total_input_tokens=900_000, total_output_tokens=800_000,
            history=[{"role": "user", "content": "Existing conversation"}],
            acquire_data_source_snapshot=Mock(return_value=self.snapshot), record_usage=Mock(),
        )
        self.store = Mock(spec=a2a_api.WorkspaceTeamStore)
        self.store.workspace_id = "workspace-1"
        self.store.member.return_value = self.member
        self.store.submit_task.return_value = (self.task, True)
        self.store.begin_member_turn.return_value = {
            "task": self.task, "member": self.member,
            "inbox": [{"sender": "leader", "message": "Use the current snapshot"}],
        }
        self.store.complete_member_turn.side_effect = self._complete
        self.agent = SimpleNamespace(
            model="model", _provider="provider", _usage_recorder=self.session.record_usage,
            _new_model_audit=Mock(return_value=SimpleNamespace(request_id="model-call-1")),
            _run_delegated_llm=Mock(return_value={"content": "Analysis", "tool_events": []}),
        )
        self.config = Mock()
        self.config.get_hosted_model.return_value = None
        self.ownership = self._patch("api.state.check_session_ownership", return_value=(True, "owner"))
        self.mode = self._patch("api.team_a2a.run_mode", return_value="local")
        self.auth = self._patch("api.team_a2a.current_user", return_value={"id": "owner"})
        self._patch("api.team_a2a.session_manager.get", return_value=self.session)
        self.store_factory = self._patch("api.team_a2a.WorkspaceTeamStore", return_value=self.store)
        self._patch("api.team_a2a.config_manager", self.config)
        self.build = self._patch("api.team_a2a._build_agent", return_value=self.agent)
        self.result_check = self._patch("api.team_a2a._result_invalid_reason", return_value="")
        self.quota = self._patch("data.auth_store.check_quota", return_value={"exceeded": False})
        self.balance = self._patch("data.auth_store.get_bonus_balance", return_value=100)
        self.billing = self._patch("data.auth_store.record_usage_event")
        self.costs = Mock()
        self._patch("data.cost_event_store.get_cost_event_store", return_value=self.costs)

    def _patch(self, target, *args, **kwargs):
        patcher = patch(target, *args, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def _complete(self, _team, _member, result, *, ok, **_kwargs):
        task = deepcopy(self.task)
        task["status"] = {"state": "TASK_STATE_COMPLETED" if ok else "TASK_STATE_FAILED"}
        task["history"].append({"role": "ROLE_AGENT", "messageId": "reply-1", "parts": [{"text": result}]})
        return {"task": task, "status": "idle" if ok else "failed"}

    def _rpc(self, method="SendMessage", params=None):
        return self.client.post(
            self.rpc_url, headers={"A2A-Version": "1.0", "X-BAA-User-ID": "attacker"},
            json={"jsonrpc": "2.0", "id": "request-1", "method": method,
                  "params": {"message": self.message} if params is None else params},
        )

    def _hosted(self, *, paid=False):
        self.mode.return_value = "cloud"
        self.config.get_hosted_model.return_value = {
            "id": "hosted", "tier": "standard" if paid else "free",
            "display_name": "Hosted", "requires_paid_balance": paid,
            "quota_source": "persistent_balance" if paid else "daily_gift", "quota_multiplier": 1,
            "input_multiplier": 0.7, "output_multiplier": 2.1,
            "cache_read_multiplier": 0.03, "cache_write_multiplier": 2.1,
        }

    def _record_model_usage(self, **_kwargs):
        self.agent._new_model_audit("team_delegate")
        self.agent._usage_recorder(
            12, 3, cached_input_tokens=4, cache_write_tokens=2,
            breakdown={"provider": "provider", "model": "model"}, update_last_prompt=False,
        )
        # Another request's Session counters must never affect this invoice.
        self.session.total_input_tokens += 50_000
        return {"content": "Analysis", "tool_events": []}

    def test_strict_envelopes_and_parse_errors_have_no_execution(self):
        envelopes = [[], [self.message], {},
                     {"jsonrpc": "2.0", "method": "SendMessage", "params": {"message": self.message}},
                     {"jsonrpc": "1.0", "id": 1, "method": "SendMessage"},
                     {"jsonrpc": "2.0", "id": True, "method": "SendMessage"},
                     {"jsonrpc": "2.0", "id": {}, "method": "SendMessage"}]
        for body in envelopes:
            with self.subTest(body=body):
                response = self.client.post(self.rpc_url, json=body, headers={"A2A-Version": "1.0"})
                self.assertEqual(-32600, response.get_json()["error"]["code"])
                self.assertIsNone(response.get_json()["id"])
        for raw in ('{"jsonrpc":', '{"id":1,"id":2}', '{"id":NaN}', '{"id":1e9999}', b"\xff"):
            with self.subTest(raw=raw):
                response = self.client.post(self.rpc_url, data=raw, content_type="application/json")
                self.assertEqual(-32700, response.get_json()["error"]["code"])
        response = self.client.post(self.rpc_url, data="{}", content_type="text/plain")
        self.assertEqual(-32005, response.get_json()["error"]["code"])
        self.store_factory.assert_not_called()
        self.build.assert_not_called()

    def test_version_and_unsupported_methods_are_explicit(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": self.message}}
        for version in (None, "0.3.0", "1.0.1", "2.0"):
            with self.subTest(version=version):
                headers = {} if version is None else {"A2A-Version": version}
                response = self.client.post(self.rpc_url, json=body, headers=headers)
                self.assertEqual(-32009, response.get_json()["error"]["code"])
                self.assertIn("A2A-Version: 1.0", response.get_json()["error"]["message"])
        for method, code in (
            ("SendStreamingMessage", -32004), ("SubscribeToTask", -32004),
            ("CreateTaskPushNotificationConfig", -32003), ("GetTaskPushNotificationConfig", -32003),
            ("ListTaskPushNotificationConfigs", -32003), ("DeleteTaskPushNotificationConfig", -32003),
            ("message/send", -32601), ("unknown", -32601),
        ):
            with self.subTest(method=method):
                self.assertEqual(code, self._rpc(method).get_json()["error"]["code"])
        self.store.submit_task.assert_not_called()
        self.build.assert_not_called()

    def test_invalid_params_and_output_modes_never_submit(self):
        cases = [([], -32602), ({"message": self.message, "user_id": "attacker"}, -32602),
                 ({"message": {**self.message, "role": "ROLE_AGENT"}}, -32602),
                 ({"message": {**self.message, "parts": [{"url": "https://example.invalid/file"}]}}, -32005),
                 ({"message": self.message, "tenant": "other"}, -32004)]
        for config, code in (
            ({"acceptedOutputModes": ["application/json"]}, -32005),
            ({"acceptedOutputModes": []}, -32602), ({"returnImmediately": True}, -32004),
            ({"returnImmediately": "false"}, -32602), ({"taskPushNotificationConfig": {}}, -32003),
            ({"historyLength": True}, -32602), ({"historyLength": -1}, -32602),
        ):
            cases.append(({"message": self.message, "configuration": config}, code))
        for params, code in cases:
            with self.subTest(params=params):
                payload = self._rpc(params=params).get_json()
                self.assertEqual(code, payload["error"]["code"])
                self.assertEqual("request-1", payload["id"])
        self.store.submit_task.assert_not_called()

    def test_send_preserves_profile_history_and_snapshot_then_deduplicates_billing(self):
        self._hosted()
        history = deepcopy(self.session.history)
        completed = self._complete("research", "analyst", "Analysis", ok=True)["task"]
        self.store.submit_task.side_effect = [(self.task, True), (completed, False)]
        self.agent._run_delegated_llm.side_effect = self._record_model_usage
        for _ in range(2):
            response = self._rpc(params={"message": self.message, "configuration": {
                "acceptedOutputModes": ["text/plain"], "returnImmediately": False, "historyLength": 0,
            }})
            self.assertEqual("TASK_STATE_COMPLETED", response.get_json()["result"]["task"]["status"]["state"])
            self.assertEqual([], response.get_json()["result"]["task"]["history"])
        self.agent._run_delegated_llm.assert_called_once_with(
            member=self.member, prompt="Analyze the sample",
            inbox_context="\n\n[Unread team mailbox]\n- From leader: Use the current snapshot",
            timeout_seconds=300, max_tokens=2000, max_tool_calls=30,
        )
        self.build.assert_called_once_with(
            self.session, workspace_id="workspace-1", source_snapshot=self.snapshot, user_id="owner",
        )
        self.snapshot.release.assert_called_once_with()
        self.assertEqual(history, self.session.history)
        self.session.record_usage.assert_called_once()
        self.billing.assert_called_once()
        invoice = self.billing.call_args
        self.assertEqual(("owner",), invoice.args)
        self.assertEqual((12, 3, 4, 2), tuple(invoice.kwargs[key] for key in (
            "prompt_tokens", "completion_tokens", "cached_input_tokens", "cache_write_tokens")))
        self.assertEqual("model-call-1", invoice.kwargs["model_call_id"])
        self.assertEqual(0.03, invoice.kwargs["cache_read_multiplier"])
        self.agent._new_model_audit.assert_called_once_with("team_delegate", billing_scope="user")
        self.quota.assert_called_once_with("owner")
        self.costs.record.assert_called_once()

    def test_desktop_hosted_usage_is_left_to_authoritative_cloud_gateway(self):
        self._hosted()
        self.mode.return_value = "desktop"
        self.agent._run_delegated_llm.side_effect = self._record_model_usage
        response = self._rpc()
        self.assertEqual("TASK_STATE_COMPLETED", response.get_json()["result"]["task"]["status"]["state"])
        self.session.record_usage.assert_called_once()
        self.quota.assert_not_called()
        self.balance.assert_not_called()
        self.billing.assert_not_called()
        self.assertEqual("owner", self.build.call_args.kwargs["user_id"])

    def test_task_get_cancel_and_wrong_member_are_scoped(self):
        self.store.get_task.return_value = self.task
        self.store.cancel_task.return_value = {**self.task, "status": {"state": "TASK_STATE_CANCELED"}}
        self.assertEqual("task-1", self._rpc("GetTask", {"id": "task-1", "historyLength": 0}).get_json()["result"]["id"])
        self.store.get_task.assert_called_once_with("research", "task-1", member_name="analyst", history_length=0)
        self.assertEqual("TASK_STATE_CANCELED", self._rpc("CancelTask", {"id": "task-1"}).get_json()["result"]["status"]["state"])
        self.store.cancel_task.assert_called_once_with("research", "task-1", member_name="analyst")
        for method, operation in (("GetTask", self.store.get_task), ("CancelTask", self.store.cancel_task)):
            with self.subTest(method=method):
                operation.side_effect = A2AError(-32001, "Task not found")
                response = self.client.post(self.rpc_url.replace("/analyst/", "/other/"),
                    headers={"A2A-Version": "1.0"}, json={"jsonrpc": "2.0", "id": 2, "method": method, "params": {"id": "task-1"}})
                self.assertEqual(-32001, response.get_json()["error"]["code"])
                self.assertEqual("other", operation.call_args.kwargs["member_name"])
        self.store.cancel_task.side_effect = A2AError(-32002, "Task is not cancelable")
        self.assertEqual(-32002, self._rpc("CancelTask", {"id": "task-1"}).get_json()["error"]["code"])
        self.build.assert_not_called()

    def test_quota_and_paid_balance_denials_finish_new_task(self):
        for paid, status in ((False, 403), (True, 402)):
            with self.subTest(paid=paid):
                self._hosted(paid=paid)
                self.quota.return_value = {"exceeded": True}
                self.balance.return_value = 0
                response = self._rpc()
                self.assertEqual(status, response.status_code)
                self.assertEqual(-32000, response.get_json()["error"]["code"])
                self.assertFalse(self.store.complete_member_turn.call_args.kwargs["ok"])
                self.assertEqual("task-1", self.store.complete_member_turn.call_args.kwargs["task_id"])
        self.build.assert_not_called()
        self.store.begin_member_turn.assert_not_called()
        self.billing.assert_not_called()

    def test_config_snapshot_and_agent_failures_finish_submitted_task(self):
        for stage in ("config", "snapshot", "build"):
            with self.subTest(stage=stage):
                self.config.get_hosted_model.side_effect = RuntimeError("private configuration") if stage == "config" else None
                self.session.acquire_data_source_snapshot.side_effect = RuntimeError("snapshot failed") if stage == "snapshot" else None
                self.build.side_effect = RuntimeError("private upstream") if stage == "build" else None
                self.snapshot.release.reset_mock()
                response = self._rpc()
                self.assertEqual("TASK_STATE_FAILED", response.get_json()["result"]["task"]["status"]["state"])
                self.assertNotIn("private", response.get_data(as_text=True))
                self.assertEqual("task-1", self.store.complete_member_turn.call_args.kwargs["task_id"])
                if stage == "build":
                    self.snapshot.release.assert_called_once_with()
                else:
                    self.snapshot.release.assert_not_called()
        self.store.begin_member_turn.assert_not_called()
        self.agent._run_delegated_llm.assert_not_called()

    def test_exception_after_usage_is_charged_and_failed_without_leaking_provider(self):
        self._hosted()
        def execute(**kwargs):
            self._record_model_usage(**kwargs)
            raise RuntimeError("secret upstream hostname")
        self.agent._run_delegated_llm.side_effect = execute
        response = self._rpc()
        self.assertEqual("TASK_STATE_FAILED", response.get_json()["result"]["task"]["status"]["state"])
        self.assertNotIn("secret upstream", response.get_data(as_text=True))
        self.billing.assert_called_once()
        self.snapshot.release.assert_called_once_with()
        self.assertFalse(self.store.complete_member_turn.call_args.kwargs["ok"])

    def test_cancellation_during_setup_or_execution_preserves_canceled_task(self):
        canceled = {**self.task, "status": {"state": "TASK_STATE_CANCELED"}}
        self.store.complete_member_turn.side_effect = None
        self.store.complete_member_turn.return_value = {"task": canceled, "status": "canceled"}
        for stage in ("begin", "execute"):
            with self.subTest(stage=stage):
                self.store.begin_member_turn.side_effect = A2AError(-32004, "Task is already terminal") if stage == "begin" else None
                self.agent._run_delegated_llm.reset_mock()
                self.agent._run_delegated_llm.side_effect = RuntimeError("Delegated task stopped: canceled")
                self.snapshot.release.reset_mock()
                response = self._rpc()
                self.assertEqual("TASK_STATE_CANCELED", response.get_json()["result"]["task"]["status"]["state"])
                self.assertEqual("task-1", self.store.complete_member_turn.call_args.kwargs["task_id"])
                self.snapshot.release.assert_called_once_with()
                if stage == "begin":
                    self.agent._run_delegated_llm.assert_not_called()

    def test_canceling_dynamic_plan_also_cancels_its_a2a_tasks(self):
        plan = {"id": "plan-1", "team_name": "research", "status": "canceled", "tasks": [
            {"id": "step-1", "a2a_task_id": "task-1", "job_id": "job-1"},
        ]}
        self.session.job_runner = Mock()
        with patch("agent.teams.dynamic_plans.DynamicTeamPlanStore") as plans, patch("api.teams.WorkspaceTeamStore") as stores:
            plans.return_value.control.return_value = plan
            response = self.client.post("/api/session/sid/team-plans/plan-1/actions/cancel")
        self.assertEqual(200, response.status_code)
        stores.return_value.cancel_task.assert_called_once_with("research", "task-1")
        self.session.job_runner.cancel_tracked.assert_called_once_with("job-1")
        self.assertEqual(1, response.get_json()["canceled_a2a_tasks"])

    def test_discovery_authentication_and_owner_checks_precede_store_access(self):
        for mode, user, owner, expected in (
            ("cloud", None, "owner", 401), ("desktop", None, "owner", 401),
            ("desktop", {"id": "other"}, "owner", 403),
        ):
            with self.subTest(mode=mode, user=user):
                self.mode.return_value, self.auth.return_value = mode, user
                self.session.owner_user_id = owner
                self.assertEqual(expected, self.client.get(self.card_url).status_code)
                self.assertEqual(expected, self._rpc("GetTask", {"id": "task-1"}).status_code)
        self.store_factory.assert_not_called()
        self.ownership.return_value = (False, "other")
        self.assertEqual(403, self.client.get(self.card_url).status_code)
        self.assertEqual(403, self._rpc().status_code)
        self.store.card.assert_not_called()
        self.store.submit_task.assert_not_called()

    def test_discovery_uses_absolute_local_rpc_url_and_private_cache_policy(self):
        self.store.card.return_value = {
            "name": "analyst", "version": "1.0.0", "capabilities": {"streaming": False, "pushNotifications": False},
            "supportedInterfaces": [{"url": "http://localhost" + self.rpc_url, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}],
            "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"],
        }
        response = self.client.get(self.card_url)
        self.assertEqual(200, response.status_code)
        self.store.card.assert_called_once_with("research", "analyst", url="http://localhost" + self.rpc_url)
        self.assertEqual("1.0", response.get_json()["supportedInterfaces"][0]["protocolVersion"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual("1.0", response.headers["A2A-Version"])


if __name__ == "__main__":
    unittest.main()
