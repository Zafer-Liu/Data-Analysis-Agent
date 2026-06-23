import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api import create_app
from api.state import session_manager


class TestLocalCommands(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = create_app().test_client()

    def setUp(self):
        self.sid = f"local-command-{time.time_ns()}"
        self.session = session_manager.get_or_create(self.sid)

    def tearDown(self):
        session_manager.remove(self.sid)

    def test_clear_removes_conversation_but_keeps_connections_and_settings(self):
        source = object()
        self.session.history = [
            {"role": "user", "content": "分析销售"},
            {"role": "assistant", "content": "结果"},
        ]
        self.session.chart_ids = ["chart-one"]
        self.session._sources = [{"id": "source-one", "source": source}]
        self.session._active_ids = ["source-one"]
        self.session.model_provider = "provider-one"
        self.session.temp_prompt = "金额使用万元"
        self.session.temp_prompt_enabled = True

        response = self.client.post(f"/api/session/{self.sid}/clear")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.session.history, [])
        self.assertEqual(self.session.chart_ids, [])
        self.assertIs(self.session.data_source, source)
        self.assertEqual(self.session.model_provider, "provider-one")
        self.assertEqual(self.session.temp_prompt, "金额使用万元")
        self.assertTrue(self.session.temp_prompt_enabled)

    def test_compact_replaces_history_and_reports_reduction(self):
        self.session.history = [
            {"role": "user", "content": "问题一"},
            {"role": "assistant", "content": "回答一"},
            {"role": "user", "content": "问题二"},
            {"role": "assistant", "content": "回答二"},
        ]
        compacted = [
            {"role": "system", "content": "摘要", "_compaction_summary": True},
            self.session.history[-1],
        ]
        with (
            patch("api.commands.config_manager.get_default_provider", return_value="provider"),
            patch("api.commands.config_manager.get_config", return_value=SimpleNamespace(model="model")),
            patch("LLM.llm_config_manager.get_llm_client", return_value=object()),
            patch("agent.compaction.compact_history", return_value=(compacted, True)),
        ):
            response = self.client.post(f"/api/session/{self.sid}/commands/compact")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["before_messages"], 4)
        self.assertEqual(payload["after_messages"], 2)
        self.assertEqual(self.session.history, compacted)

    def test_compact_rejects_short_conversation_without_model_call(self):
        self.session.history = [{"role": "user", "content": "太短"}]
        response = self.client.post(f"/api/session/{self.sid}/commands/compact")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "not_enough_context")


if __name__ == "__main__":
    unittest.main()
