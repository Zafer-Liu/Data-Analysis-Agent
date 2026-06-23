#!/usr/bin/env python3
"""Conversation-analysis parent jobs and expandable tool-step history."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.jobs import JobRunner, empty_job
from api import create_app
from api.state import session_manager
from data.jobs_store import JobsStore, _TERMINAL
from agent.tools import results as result_module


class _FakeAgent:
    def run(self, *_args, **_kwargs):
        for index in range(12):
            tool = f"tool_{index}"
            yield {"type": "tool_start", "tool": tool, "display": f"步骤 {index + 1}"}
            yield {
                "type": "tool_audit", "tool": tool, "ok": index != 4,
                "error": "boom" if index == 4 else "", "content": "",
                "artifacts": [], "elapsed_seconds": 0.01 + index / 100,
            }
            yield {"type": "tool_end", "tool": tool}
        yield {"type": "text", "content": "最终分析答案"}


class TestConversationJobAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = create_app().test_client()

    def test_one_message_creates_parent_with_twelve_steps_and_answer(self):
        sid = f"conversation-{time.time_ns()}"
        sess = session_manager.get_or_create(sid)
        sess._combined_schema_cache = "Table: metrics\ncity VARCHAR\nvalue DOUBLE"
        with tempfile.TemporaryDirectory() as tmp, patch(
            "api.chat._build_agent", return_value=_FakeAgent(),
        ), patch.object(result_module, "_GLOBAL_RESULT_ROOT", Path(tmp)):
            response = self.client.post(
                f"/api/session/{sid}/chat", json={"message": "综合分析十二个指标"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("最终分析答案", response.get_data(as_text=True))
            payload = self.client.get(f"/api/session/{sid}/jobs").get_json()["jobs"]
        self.assertEqual(len(payload), 1)
        parent = payload[0]
        self.assertEqual(parent["type"], "conversation_analysis")
        self.assertEqual(parent["status"], "succeeded")
        self.assertEqual(parent["result"]["answer"], "最终分析答案")
        self.assertEqual(parent["result"]["step_count"], 12)
        self.assertEqual(parent["message"], "已执行 12 个步骤")
        self.assertEqual(parent["artifacts"][0]["name"], "get_schema 数据结构")
        self.assertEqual(len(parent["steps"]), 24)
        failures = [
            event for event in parent["steps"]
            if event["type"] == "conversation_step_finished"
            and event["status"] == "failed"
        ]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["tool"], "tool_4")


class TestConversationParenting(unittest.TestCase):
    def test_child_worker_is_hidden_from_top_level_history_and_canceled_with_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobsStore(Path(tmp) / "jobs.db")
            runner = JobRunner("parent-sid", store, max_workers=1)
            parent = runner.begin_tracked("conversation_analysis", "question")
            with runner.conversation_scope(parent):
                child = runner.create(lambda ctx: empty_job(ctx, 0.5), "analysis")
            self.assertEqual(store.get(child)["parent_id"], parent)
            self.assertEqual(
                [job["id"] for job in runner.list_jobs(top_level_only=True)], [parent],
            )
            self.assertTrue(runner.cancel(parent))
            for _ in range(60):
                if store.get(child)["status"] in _TERMINAL:
                    break
                time.sleep(0.02)
            self.assertEqual(store.get(child)["status"], "canceled")
            runner.cancel_tracked(parent)
            runner.shutdown()
            store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
