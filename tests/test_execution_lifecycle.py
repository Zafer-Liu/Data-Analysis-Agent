"""Synthetic execution attempts, cancellation and persisted member lease regressions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from agent.execution import (
    ExecutionCancelled, ExecutionContext, ExecutionDeadlineExceeded, ExecutionStopped,
    begin_request_execution, cancel_request_execution, execution_owner_alive,
    finish_request_execution, get_execution_context,
)
from agent.teams.a2a import A2AError, task_state, text_message
from agent.tools.workspace.teams import WorkspaceTeamError, WorkspaceTeamStore


class ExecutionContextTests(unittest.TestCase):
    def test_parent_cancel_blocks_next_call_and_keeps_lease_until_real_exit(self):
        root = ExecutionContext(request_id="parent")
        child = root.child()
        released = Mock()
        root.add_exit_callback(released)
        entered, leave = threading.Event(), threading.Event()
        calls = []

        def body():
            try:
                child.checkpoint()
                calls.append("blocking tool")
                entered.set()
                leave.wait(2)
                child.checkpoint()
                calls.append("next model")
            except ExecutionCancelled:
                pass
            finally:
                child.acknowledge_exit()

        worker = threading.Thread(target=body)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            root.cancel()
            root.acknowledge_exit()
            self.assertFalse(root.exit_acknowledged)
            self.assertTrue(execution_owner_alive(child.to_dict()))
            released.assert_not_called()
        finally:
            leave.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(root.exit_acknowledged)
        released.assert_called_once_with()
        self.assertEqual(["blocking tool"], calls)

    def test_deadline_inheritance_and_ack_are_separate(self):
        with patch("agent.execution.time.monotonic", return_value=100):
            root = ExecutionContext(timeout_seconds=20)
            child = root.child(timeout_seconds=200)
            narrower = root.child(timeout_seconds=2)
        self.assertEqual(120, child.deadline)
        self.assertEqual(102, narrower.deadline)
        with patch("agent.execution.time.monotonic", return_value=121):
            with self.assertRaises(ExecutionDeadlineExceeded):
                child.checkpoint()
        self.assertFalse(child.exit_acknowledged)
        root.acknowledge_exit()
        child.acknowledge_exit()
        self.assertFalse(root.exit_acknowledged)
        narrower.acknowledge_exit()
        self.assertTrue(root.exit_acknowledged)
        self.assertIsNone(get_execution_context(child.attempt_id))
        self.assertTrue(json.dumps(root.to_dict()))

    def test_request_overlap_and_old_stop_cannot_cancel_new_turn(self):
        first = begin_request_execution("execution-overlap", request_id="first")
        child = first.child()
        try:
            finish_request_execution("execution-overlap", first)
            with self.assertRaises(ExecutionStopped):
                begin_request_execution("execution-overlap")
            child.acknowledge_exit()
            second = begin_request_execution("execution-overlap", request_id="second")
            try:
                self.assertIsNone(cancel_request_execution("execution-overlap", request_id="first"))
                self.assertFalse(second.cancellation_requested)
                finish_request_execution("execution-overlap", first)
                self.assertIs(second, cancel_request_execution("execution-overlap", request_id="second"))
                with self.assertRaises(ExecutionCancelled):
                    second.checkpoint()
            finally:
                finish_request_execution("execution-overlap", second)
        finally:
            child.acknowledge_exit()
            finish_request_execution("execution-overlap", first)

    def test_unknown_legacy_owner_is_not_proven_dead(self):
        self.assertIsNone(execution_owner_alive({"owner_pid": os.getpid()}))
        self.assertIsNone(execution_owner_alive({}))
        self.assertFalse(execution_owner_alive({"exit_acknowledged": True}))


class MemberExecutionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch("agent.tools.workspace.teams.content_data_path", return_value=Path(directory.name) / "teams.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = WorkspaceTeamStore("execution-test", workspace_id="")
        self.store.create_agent("analyst")
        self.contexts = []
        self.addCleanup(self._finish_contexts)

    def _finish_contexts(self):
        for context in reversed(self.contexts):
            context.acknowledge_exit()

    def start(self, *, parent=None, timeout=300):
        task, _ = self.store.submit_task("default", "analyst", text_message("Analyze synthetic values"))
        turn = self.store.begin_member_turn("default", "analyst", task_id=task["id"], execution_context=parent, timeout_seconds=timeout)
        self.contexts.append(turn["_execution_context"])
        return task, turn

    def test_serializable_metadata_and_parent_cancel_rejects_late_result(self):
        parent = ExecutionContext()
        self.contexts.append(parent)
        task, turn = self.start(parent=parent)
        context = turn["member"]["_execution_context"]
        metadata = self.store.get_task("default", task["id"])["metadata"]["execution"]
        self.assertEqual(parent.attempt_id, metadata["parent_attempt_id"])
        self.assertEqual(context.attempt_id, turn["_execution_attempt_id"])
        json.loads(self.store._path.read_text(encoding="utf-8"))
        parent.cancel()
        done = self.store.complete_member_turn("default", "analyst", "late secret result", task_id=task["id"], execution_context=context)
        self.assertEqual("canceled", task_state(done["task"]))
        self.assertEqual([], done["task"]["artifacts"])
        self.assertNotIn("late secret result", self.store._path.read_text(encoding="utf-8"))
        self.assertEqual("idle", self.store.member("default", "analyst")["status"])

    def test_timeout_never_releases_live_child(self):
        task, turn = self.start()
        context = turn["_execution_context"]
        child = context.child()
        self.contexts.append(child)
        self.assertEqual(1, self.store.fail_stale_members("default", max_age_seconds=-1))
        self.assertEqual("failed", task_state(self.store.get_task("default", task["id"])))
        self.assertEqual("running", self.store.member("default", "analyst")["status"])
        self.assertEqual(0, self.store.fail_stale_members("default", max_age_seconds=-1))
        done = self.store.complete_member_turn("default", "analyst", "late", task_id=task["id"], execution_context=context)
        self.assertTrue(done["ignored"])
        with self.assertRaises(A2AError):
            self.store.submit_task("default", "analyst", text_message("Overlapping work"))
        child.acknowledge_exit()
        self.assertEqual("failed", self.store.member("default", "analyst")["status"])
        self.assertTrue(self.store.get_task("default", task["id"])["metadata"]["execution"]["exit_acknowledged"])

    def test_deadline_rejects_result_without_waiting_for_stale_cleanup(self):
        task, turn = self.start(timeout=0)
        done = self.store.complete_member_turn(
            "default", "analyst", "late deadline result", task_id=task["id"], execution_context=turn["_execution_context"],
        )
        self.assertEqual("failed", task_state(done["task"]))
        self.assertEqual([], done["task"]["artifacts"])
        self.assertNotIn("late deadline result", self.store._path.read_text(encoding="utf-8"))
        self.assertTrue(turn["_execution_context"].exit_acknowledged)

    def test_attempt_mismatch_cannot_publish_or_ack_owner(self):
        task, turn = self.start()
        with self.assertRaisesRegex(WorkspaceTeamError, "attempt"):
            self.store.complete_member_turn("default", "analyst", "spoof", task_id=task["id"], attempt_id="wrong")
        self.assertFalse(turn["_execution_context"].exit_acknowledged)
        self.assertEqual("working", task_state(self.store.get_task("default", task["id"])))
        self.store.complete_member_turn("default", "analyst", "first", task_id=task["id"], execution_context=turn["_execution_context"])
        second, second_turn = self.start()
        self.store.complete_member_turn("default", "analyst", "old late", task_id=task["id"], attempt_id=turn["_execution_attempt_id"])
        member = self.store.member("default", "analyst")
        self.assertEqual(second["id"], member["active_task_id"])
        self.assertFalse(second_turn["_execution_context"].exit_acknowledged)

    def test_dead_process_orphan_converges_and_legacy_unknown_stays_occupied(self):
        task, turn = self.start()
        data = self.store._load()
        team = data["teams"]["default"]
        orphan = {**turn["_execution_context"].to_dict(), "owner_process_id": "previous-process", "attempt_id": "orphan"}
        team["a2a_tasks"][task["id"]]["metadata"]["execution"] = orphan
        team["members"][0]["execution"] = orphan
        self.store._save(data)
        self.assertEqual(1, self.store.fail_stale_members("default"))
        self.assertEqual("failed", self.store.member("default", "analyst")["status"])
        self.assertTrue(self.store.get_task("default", task["id"])["metadata"]["execution"]["exit_acknowledged"])
        data = self.store._load()
        member = data["teams"]["default"]["members"][0]
        member.update(status="running", execution={}, active_task_id="legacy")
        self.store._save(data)
        self.store.fail_stale_members("default", max_age_seconds=-1)
        self.assertEqual("running", self.store.member("default", "analyst")["status"])

    def test_evidence_outlives_preview_and_is_present_in_task_and_mailbox(self):
        task, turn = self.start()
        long_result = "x" * 30000
        evidence = {"claim_ids": ["child-claim"], "artifact_refs": [{"artifact_id": "full-result", "sha256": "a" * 64}]}
        done = self.store.complete_member_turn(
            "default", "analyst", long_result, task_id=task["id"], execution_context=turn["_execution_context"],
            tool_events=[{"tool": "query_data", "result": long_result, "data": {"claim_id": "sql-claim"}}], evidence=evidence,
        )
        self.assertLessEqual(len(done["message"]["message"]), 20000)
        self.assertEqual({"child-claim", "sql-claim"}, set(done["evidence"]["claim_ids"]))
        self.assertEqual(done["evidence"], done["task"]["metadata"]["evidence"])
        self.assertTrue(any(ref.get("artifact_id") == "full-result" for ref in done["message"]["evidence"]["artifact_refs"]))


if __name__ == "__main__":
    unittest.main()
