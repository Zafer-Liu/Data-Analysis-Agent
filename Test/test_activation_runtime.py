import time
import unittest
from unittest.mock import patch

from agent.activation import ActivationContext
from agent.tools.exposure import filter_tools_for_turn
from api import create_app
from api.chat import ActivationRequestError, _resolve_activation
from api.saved_sessions import _recovery_state
from api.state import session_manager
from data.session import ChatSession


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


class _CaptureAgent:
    last_kwargs = None

    def run(self, *_args, **kwargs):
        type(self).last_kwargs = kwargs
        yield {"type": "text", "content": "ok"}


class TestActivationResolution(unittest.TestCase):
    def setUp(self):
        self.session = ChatSession(session_id=f"activation-{time.time_ns()}")

    def test_skill_and_command_are_mutually_exclusive(self):
        with self.assertRaises(ActivationRequestError) as caught:
            _resolve_activation(self.session, {"skill": "funnel-analysis", "command": "sql"})
        self.assertEqual(caught.exception.code, "activation_conflict")

    def test_registry_resolves_typed_analysis_skill(self):
        activation, skill, command = _resolve_activation(
            self.session, {"skill": "funnel-analysis"},
        )
        self.assertEqual(activation.kind, "skill")
        self.assertEqual(skill.name, "funnel-analysis")
        self.assertIsNone(command)

        activation, skill, command = _resolve_activation(self.session, {"skill": "sql"})
        self.assertEqual(activation.to_dict(), {"kind": "skill", "name": "sql"})
        self.assertEqual(skill.name, "sql")
        self.assertIsNone(command)

    def test_skill_name_is_not_accepted_as_legacy_command(self):
        with self.assertRaises(ActivationRequestError) as caught:
            _resolve_activation(self.session, {"command": "funnel-analysis"})
        self.assertEqual(caught.exception.code, "unknown_command")

    def test_local_command_cannot_reach_agent(self):
        with self.assertRaises(ActivationRequestError) as caught:
            _resolve_activation(self.session, {"command": "status"})
        self.assertEqual(caught.exception.code, "local_command_only")

    def test_legacy_confirm_command_migrates_to_internal_action(self):
        activation, skill, command = _resolve_activation(
            self.session, {"command": "ppt_confirm"},
        )
        self.assertEqual(activation.kind, "internal_action")
        self.assertEqual(activation.internal_action, "ppt_confirm")
        self.assertIsNone(skill)
        self.assertIsNone(command)


class TestActivationToolPolicy(unittest.TestCase):
    def test_skill_allowed_tools_only_reduce_normal_policy(self):
        schemas = [_schema("get_schema"), _schema("query_data"), _schema("generate_ppt")]
        filtered = filter_tools_for_turn(
            schemas,
            activation=ActivationContext(skill_name="analysis"),
            skill_allowed_tools=frozenset({"get_schema", "generate_ppt"}),
            has_data_source=True,
            include_mcp=False,
        )
        self.assertEqual(
            [item["function"]["name"] for item in filtered], ["get_schema"],
        )

    def test_internal_action_unlocks_only_existing_guarded_tool(self):
        schemas = [_schema("propose_ppt_outline"), _schema("generate_ppt")]
        filtered = filter_tools_for_turn(
            schemas,
            activation=ActivationContext(internal_action="ppt_confirm"),
            has_data_source=True,
            include_mcp=False,
        )
        self.assertEqual(
            [item["function"]["name"] for item in filtered], ["generate_ppt"],
        )

    def test_trusted_output_skill_unlocks_only_its_proposal_tool(self):
        schemas = [_schema("propose_ppt_outline"), _schema("generate_ppt")]
        filtered = filter_tools_for_turn(
            schemas,
            activation=ActivationContext(skill_name="ppt"),
            skill_allowed_tools=frozenset({"propose_ppt_outline", "generate_ppt"}),
            trusted_skill="ppt",
            has_data_source=True,
            include_mcp=False,
        )
        self.assertEqual(
            [item["function"]["name"] for item in filtered], ["propose_ppt_outline"],
        )


class TestActivationAPIAndPersistence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = create_app().test_client()

    def test_chat_passes_typed_skill_and_records_job_activation(self):
        sid = f"typed-chat-{time.time_ns()}"
        with patch("api.chat._build_agent", return_value=_CaptureAgent()):
            response = self.client.post(
                f"/api/session/{sid}/chat",
                json={"message": "分析漏斗", "skill": "funnel-analysis"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("ok", response.get_data(as_text=True))
        activation = _CaptureAgent.last_kwargs["activation"]
        self.assertEqual(activation.to_dict(), {"kind": "skill", "name": "funnel-analysis"})
        self.assertEqual(_CaptureAgent.last_kwargs["active_skill"].name, "funnel-analysis")

        jobs = self.client.get(f"/api/session/{sid}/jobs").get_json()["jobs"]
        self.assertEqual(jobs[0]["result"]["activation"]["skill_name"], "funnel-analysis")
        self.assertEqual(jobs[0]["activation"]["kind"], "skill")

    def test_invalid_activation_fails_before_job_creation(self):
        sid = f"invalid-chat-{time.time_ns()}"
        response = self.client.post(
            f"/api/session/{sid}/chat",
            json={"message": "test", "skill": "funnel-analysis", "command": "sql"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "activation_conflict")
        jobs = self.client.get(f"/api/session/{sid}/jobs").get_json()["jobs"]
        self.assertEqual(jobs, [])

    def test_activation_audit_is_part_of_saved_recovery_state(self):
        sess = session_manager.get_or_create(f"saved-activation-{time.time_ns()}")
        sess.record_activation(ActivationContext(command_name="sql"), "query", "job-1")
        state = _recovery_state(sess)
        self.assertEqual(state["turn_activations"][0]["command_name"], "sql")
        self.assertEqual(state["turn_activations"][0]["job_id"], "job-1")


if __name__ == "__main__":
    unittest.main()
