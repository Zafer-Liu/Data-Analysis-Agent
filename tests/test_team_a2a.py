"""Regression contracts for session agents and their A2A task lifecycle."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent.teams.a2a import A2AError, task_state, text_message, validate_message
from agent.tools.workspace.teams import WorkspaceTeamError, WorkspaceTeamStore


class SessionAgentTaskTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name) / "teams.json"
        patcher = patch("agent.tools.workspace.teams.content_data_path", return_value=path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = WorkspaceTeamStore("session-a", workspace_id="")
        self.store.create_agent("analyst", profile="sql-analyst")

    def submit(self, text="Calculate the supplied totals"):
        return self.store.submit_task("default", "analyst", text_message(text))

    def test_construct_without_workspace_does_not_execute(self):
        member = self.store.member("default", "analyst")
        self.assertEqual("idle", member["status"])
        self.assertEqual("sql-analyst", member["profile"]["key"])
        self.assertEqual([], self.store.status("default")["recent_tasks"])
        card = self.store.card("default", "analyst", url="https://example.com/a2a")
        self.assertEqual("1.0", card["supportedInterfaces"][0]["protocolVersion"])
        self.assertFalse(card["capabilities"]["streaming"])
        self.assertNotIn("protocolVersion", card)
        self.assertNotIn("workspace_write_file", card["skills"][0]["tags"])

    def test_task_result_has_distinct_ids_and_artifact(self):
        task, created = self.submit()
        self.assertTrue(created)
        turn = self.store.begin_member_turn("default", "analyst", task_id=task["id"])
        self.assertEqual("working", task_state(turn["task"]))
        done = self.store.complete_member_turn("default", "analyst", "Total is 42", task_id=task["id"])
        task = done["task"]
        self.assertEqual("completed", task_state(task))
        self.assertEqual("ROLE_AGENT", task["history"][-1]["role"])
        self.assertEqual("Total is 42", task["artifacts"][0]["parts"][0]["text"])
        self.assertEqual(4, len({task["id"], task["contextId"], task["history"][0]["messageId"], task["artifacts"][0]["artifactId"]}))
        self.assertNotIn("kind", task)
        self.assertEqual(task["id"], done["message"]["a2a"]["taskId"])

    def test_mailbox_context_does_not_prevent_task_execution(self):
        self.store.send_message("default", "analyst", "Use net revenue, excluding refunds")
        task, created = self.submit()
        self.assertTrue(created)
        turn = self.store.begin_member_turn("default", "analyst", task_id=task["id"])
        self.assertEqual("working", task_state(turn["task"]))
        self.assertEqual("Use net revenue, excluding refunds", turn["inbox"][0]["message"])

    def test_repeated_message_is_idempotent_and_conflicts_are_rejected(self):
        message = text_message("A bounded request")
        first, _ = self.store.submit_task("default", "analyst", message)
        repeated, created = self.store.submit_task("default", "analyst", message)
        self.assertFalse(created)
        self.assertEqual(first["id"], repeated["id"])
        changed = {**message, "parts": [{"text": "Different request"}]}
        with self.assertRaises(A2AError) as raised:
            self.store.submit_task("default", "analyst", changed)
        self.assertEqual(-32602, raised.exception.code)

    def test_cancel_does_not_publish_late_result_or_reopen_task(self):
        task, _ = self.submit()
        self.store.begin_member_turn("default", "analyst", task_id=task["id"])
        canceled = self.store.cancel_task("default", task["id"])
        self.assertEqual("canceled", task_state(canceled))
        with self.assertRaises(A2AError):
            self.submit("A second task while the old provider call is in flight")
        completed = self.store.complete_member_turn("default", "analyst", "Late output", task_id=task["id"])
        self.assertEqual("canceled", task_state(completed["task"]))
        self.assertFalse(completed["task"].get("artifacts"))
        self.assertNotIn("Late output", json.dumps(self.store.status("default")))
        self.assertEqual("idle", self.store.member("default", "analyst")["status"])

    def test_old_completion_cannot_change_new_active_turn(self):
        first, _ = self.submit()
        self.store.cancel_task("default", first["id"])
        second, _ = self.submit("Second request")
        self.store.begin_member_turn("default", "analyst", task_id=second["id"])
        self.store.complete_member_turn("default", "analyst", "Late first result", task_id=first["id"])
        member = self.store.member("default", "analyst")
        self.assertEqual("running", member["status"])
        self.assertEqual(second["id"], member["active_task_id"])

    def test_member_cannot_read_or_cancel_another_members_task(self):
        task, _ = self.submit()
        self.store.create_agent("writer", profile="report-writer")
        for operation in (self.store.get_task, self.store.cancel_task):
            with self.subTest(operation=operation.__name__), self.assertRaises(A2AError) as raised:
                operation("default", task["id"], member_name="writer")
            self.assertEqual(-32001, raised.exception.code)
        self.assertEqual("submitted", task_state(self.store.get_task("default", task["id"])))

    def test_reuse_preserves_evidence_and_agent_identity(self):
        task, _ = self.submit()
        self.store.begin_member_turn("default", "analyst", task_id=task["id"])
        self.store.complete_member_turn("default", "analyst", "Saved evidence", task_id=task["id"])
        identity = self.store.member("default", "analyst")["agent_id"]
        self.store.create("default", "Updated team", [{"name": "analyst", "role": "researcher"}])
        self.assertEqual(identity, self.store.member("default", "analyst")["agent_id"])
        self.assertIn("Saved evidence", json.dumps(self.store.status("default")))
        self.assertEqual("completed", task_state(self.store.get_task("default", task["id"])))

    def test_busy_member_is_not_reconfigured(self):
        task, _ = self.submit()
        self.store.begin_member_turn("default", "analyst", task_id=task["id"])
        with self.assertRaises(WorkspaceTeamError):
            self.store.create("default", "", [{"name": "analyst", "role": "writer"}])
        with self.assertRaises(WorkspaceTeamError):
            self.store.begin_member_turn("default", "analyst")

    def test_empty_output_is_a_failed_task(self):
        task, _ = self.submit()
        self.store.begin_member_turn("default", "analyst", task_id=task["id"])
        completed = self.store.complete_member_turn("default", "analyst", "", task_id=task["id"])
        self.assertEqual("failed", task_state(completed["task"]))
        self.assertEqual("failed", completed["status"])
        self.assertFalse(completed["task"]["artifacts"])

    def test_terminal_tasks_cannot_restart(self):
        task, _ = self.submit()
        self.store.cancel_task("default", task["id"])
        message = text_message("Continue", task_id=task["id"], context_id=task["contextId"])
        with self.assertRaises(A2AError) as raised:
            self.store.submit_task("default", "analyst", message)
        self.assertEqual(-32004, raised.exception.code)

    def test_history_length_and_stale_queue(self):
        task, _ = self.submit()
        self.store.release_stale_queued_members("default", max_age_seconds=-1)
        self.assertEqual("failed", task_state(self.store.get_task("default", task["id"])))
        self.assertEqual([], self.store.get_task("default", task["id"], history_length=0)["history"])
        with self.assertRaises(A2AError):
            self.store.get_task("default", task["id"], history_length=True)

    def test_legacy_mailbox_is_migrated_additively(self):
        legacy = {"teams": {"legacy": {
            "name": "legacy", "members": [{"name": "worker", "role": "analyst", "status": "idle"}],
            "messages": [{"id": "old", "message": "Preserve me", "sender": "worker", "recipient": "leader"}],
        }}}
        self.store._path.write_text(json.dumps(legacy), encoding="utf-8")
        self.store.create_agent("other", team_name="legacy")
        team = self.store.get("legacy")
        self.assertTrue(team["context_id"])
        self.assertEqual("Preserve me", team["messages"][0]["message"])

    def test_reserved_leader_and_unsupported_input_are_rejected(self):
        with self.assertRaises(WorkspaceTeamError):
            self.store.create_agent("leader")
        with self.assertRaises(A2AError):
            validate_message({"messageId": "m1", "role": "user", "parts": [{"kind": "text", "text": "old"}]})
        with self.assertRaises(A2AError) as raised:
            validate_message({"messageId": "m2", "role": "ROLE_USER", "parts": [{"url": "https://example.com/file"}]})
        self.assertEqual(-32005, raised.exception.code)


class DelegatedBoundaryTests(unittest.TestCase):
    def test_delegated_sql_cannot_mutate_session_tables(self):
        from agent.agent import BusinessAgent

        agent = BusinessAgent.__new__(BusinessAgent)
        agent._tool_query_data = Mock(return_value="should not execute")
        for sql in ("DELETE FROM sales", "CREATE TABLE stolen AS SELECT * FROM sales", "SELECT 1; DROP TABLE sales"):
            with self.subTest(sql=sql):
                result = agent._execute_delegated_tool("query_data", {"sql": sql}, allowed_tools={"query_data"})
                self.assertIn("error", result.lower())
        agent._tool_query_data.assert_not_called()

    def test_canceled_task_does_not_make_another_model_call(self):
        from agent.agent import BusinessAgent

        agent = BusinessAgent.__new__(BusinessAgent)
        agent._max_output_tokens = 2000
        agent._session_id, agent._workspace_id = "session-a", ""
        agent.model, agent._provider = "fake-model", "test"
        agent._delegated_tool_schemas = Mock(return_value=[])
        agent._new_model_audit = Mock()
        agent._apply_prompt_cache = Mock(return_value=({}, {
            "enabled": False, "mode": "none", "cache_key": "", "retention": "", "scope_isolated": True,
        }))
        agent.client = Mock()
        with patch("agent.agent.WorkspaceTeamStore") as store:
            store.return_value.get_task.return_value = {"status": {"state": "TASK_STATE_CANCELED"}}
            with self.assertRaisesRegex(RuntimeError, "stopped"):
                agent._run_delegated_llm(
                    member={"_a2a_task_id": "task", "_a2a_team_name": "default"}, prompt="Analyze this",
                )
        agent.client.chat.completions.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
