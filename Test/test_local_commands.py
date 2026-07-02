import time
import unittest
import os
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

    def test_compact_uses_generic_backend_command_route(self):
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
            patch("agent.compaction.compact_history", return_value=(compacted, True)) as compact,
        ):
            response = self.client.post(
                f"/api/session/{self.sid}/commands/compact/execute",
                json={"arguments": "保留数据库口径"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["command"], "compact")
        self.assertEqual(compact.call_args.kwargs["focus"], "保留数据库口径")

    def test_legacy_compact_route_can_be_disabled_for_migration(self):
        with patch.dict(os.environ, {"BAA_ENABLE_LEGACY_COMPACT_ROUTE": "0"}):
            response = self.client.post(
                f"/api/session/{self.sid}/commands/compact"
            )
        self.assertEqual(response.status_code, 410)
        self.assertEqual(
            response.get_json()["code"],
            "legacy_command_route_disabled",
        )

    def test_non_backend_command_is_rejected_by_backend_route(self):
        response = self.client.post(
            f"/api/session/{self.sid}/commands/status/execute",
            json={"arguments": ""},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "invalid_command_route")

    def test_compact_records_provider_usage_without_updating_last_prompt_anchor(self):
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

        def compact_with_usage(history, client, model, **kwargs):
            kwargs["usage_callback"](SimpleNamespace(
                prompt_tokens=120,
                completion_tokens=30,
                total_tokens=150,
                prompt_tokens_details=SimpleNamespace(cached_tokens=80),
            ))
            return compacted, True

        with (
            patch("api.commands.config_manager.get_default_provider", return_value="provider"),
            patch("api.commands.config_manager.get_config", return_value=SimpleNamespace(model="model")),
            patch("LLM.llm_config_manager.get_llm_client", return_value=object()),
            patch("agent.compaction.compact_history", side_effect=compact_with_usage),
        ):
            response = self.client.post(
                f"/api/session/{self.sid}/commands/compact/execute",
                json={"arguments": ""},
            )

        payload = response.get_json()
        self.assertEqual(payload["usage"]["input_tokens"], 120)
        self.assertEqual(payload["usage"]["output_tokens"], 30)
        self.assertEqual(payload["usage"]["cached_tokens"], 80)
        self.assertEqual(self.session.total_input_tokens, 120)
        self.assertEqual(self.session.total_output_tokens, 30)
        self.assertNotEqual(self.session.last_prompt_tokens, 120)

    def test_compact_rejects_short_conversation_without_model_call(self):
        self.session.history = [{"role": "user", "content": "太短"}]
        response = self.client.post(f"/api/session/{self.sid}/commands/compact")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "not_enough_context")

    def test_compact_preserves_history_when_summary_is_larger(self):
        original = [
            {"role": "user", "content": "一"},
            {"role": "assistant", "content": "二"},
            {"role": "user", "content": "三"},
            {"role": "assistant", "content": "四"},
        ]
        self.session.history = list(original)
        expanded = [
            {
                "role": "system",
                "content": "很长的摘要" * 100,
                "_compaction_summary": True,
            },
            original[-1],
        ]

        def expanded_with_usage(history, client, model, **kwargs):
            kwargs["usage_callback"](SimpleNamespace(
                prompt_tokens=50,
                completion_tokens=100,
                total_tokens=150,
                prompt_tokens_details=SimpleNamespace(cached_tokens=10),
            ))
            return expanded, True

        with (
            patch("api.commands.config_manager.get_default_provider", return_value="provider"),
            patch(
                "api.commands.config_manager.get_config",
                return_value=SimpleNamespace(model="model", enabled=True),
            ),
            patch("LLM.llm_config_manager.get_llm_client", return_value=object()),
            patch("agent.compaction.compact_history", side_effect=expanded_with_usage),
        ):
            response = self.client.post(
                f"/api/session/{self.sid}/commands/compact/execute",
                json={"arguments": ""},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "compaction_not_smaller")
        self.assertEqual(self.session.history, original)
        self.assertEqual(self.session.total_input_tokens, 50)
        self.assertEqual(self.session.command_metrics[-1]["outcome"], "error")

    def test_catalog_returns_session_specific_availability(self):
        self.session.history = [
            {"role": "user", "content": "问题一"},
            {"role": "assistant", "content": "回答一"},
            {"role": "user", "content": "问题二"},
            {"role": "assistant", "content": "回答二"},
        ]
        self.session.model_provider = "provider"
        with patch(
            "api.commands.config_manager.get_config",
            return_value=SimpleNamespace(model="model", enabled=True),
        ):
            response = self.client.get(f"/api/commands?sid={self.sid}")
        self.assertEqual(response.status_code, 200)
        commands = {
            item["name"]: item
            for item in response.get_json()["commands"]
        }
        self.assertTrue(commands["compact"]["available"])
        self.assertFalse(commands["checkpoint"]["available"])
        self.assertEqual(commands["checkpoint"]["unavailable_code"], "workspace_required")
        self.assertFalse(commands["teams"]["available"])

    def test_backend_availability_cannot_be_bypassed(self):
        self.session.history = [
            {"role": "user", "content": "问题一"},
            {"role": "assistant", "content": "回答一"},
            {"role": "user", "content": "问题二"},
            {"role": "assistant", "content": "回答二"},
        ]
        with patch("api.commands.config_manager.get_default_provider", return_value=""):
            response = self.client.post(
                f"/api/session/{self.sid}/commands/compact/execute",
                json={"arguments": ""},
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "model_required")

    def test_local_command_metrics_are_content_free_and_aggregated(self):
        response = self.client.post(
            f"/api/session/{self.sid}/command-metrics",
            json={
                "command": "status",
                "outcome": "success",
                "duration_ms": 12,
                "arguments": "must not be retained",
            },
        )
        self.assertEqual(response.status_code, 200)
        metrics = self.client.get(
            f"/api/session/{self.sid}/command-metrics"
        ).get_json()
        self.assertEqual(metrics["summary"]["local"]["count"], 1)
        self.assertEqual(metrics["summary"]["local"]["input_tokens"], 0)
        self.assertNotIn("arguments", metrics["entries"][0])

    def test_backend_command_metric_includes_usage_and_compression_ratio(self):
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

        def compact_with_usage(history, client, model, **kwargs):
            kwargs["usage_callback"](SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                prompt_tokens_details=SimpleNamespace(cached_tokens=40),
            ))
            return compacted, True

        with (
            patch("api.commands.config_manager.get_default_provider", return_value="provider"),
            patch(
                "api.commands.config_manager.get_config",
                return_value=SimpleNamespace(model="model", enabled=True),
            ),
            patch("LLM.llm_config_manager.get_llm_client", return_value=object()),
            patch("agent.compaction.compact_history", side_effect=compact_with_usage),
        ):
            response = self.client.post(
                f"/api/session/{self.sid}/commands/compact/execute",
                json={"arguments": ""},
            )

        self.assertEqual(response.status_code, 200)
        metric = self.session.command_metrics[-1]
        self.assertEqual(metric["command_type"], "backend")
        self.assertEqual(metric["input_tokens"], 100)
        self.assertEqual(metric["cached_input_tokens"], 40)
        self.assertIn("compression_ratio", metric)


if __name__ == "__main__":
    unittest.main()
