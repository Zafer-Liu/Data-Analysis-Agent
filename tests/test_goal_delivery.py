"""End-to-end local delivery glue, without provider calls or production data."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from agent.tools.business.data import DataToolsMixin
from agent.tools.results import read_tool_result_artifact
from data.cost_event_store import CostEventStore
from data.cost_recording import record_model_calls
from infrastructure.optimization_config import load_optimization_config


class AnalysisAgent(DataToolsMixin):
    def __init__(self):
        self._session_id = "synthetic-session"
        self._workspace_id = ""
        self.saved = {}

    def _write_analysis_df(self, frame, name):
        self.saved[name] = frame.copy()


class GoalDeliveryTests(unittest.TestCase):
    def test_full_analysis_results_survive_replacement_of_the_queryable_view(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = AnalysisAgent()
            with patch("agent.tools.results._GLOBAL_RESULT_ROOT", Path(directory)):
                agent._remember_analysis_input(pd.DataFrame({"value": [5]}), "forecast", "SELECT value FROM orders", "value", "", 10, {"horizon": 7})
                output = pd.DataFrame({"date": pd.to_datetime(["2026-01-01"]), "forecast": [12.5]})
                metadata = pd.DataFrame({"model_revision": ["synthetic-v1"]})
                agent._finalize_analysis_result({"output_tables": ["a", "b", "model_metadata"]},
                                                (output, pd.DataFrame(), metadata, "done"),
                                                "forecast", "SELECT value FROM orders", "value", 10)
                first = agent._analysis_result_artifacts
                agent._finalize_analysis_result({}, (pd.DataFrame({"forecast": [99.0]}), pd.DataFrame(), "new"),
                                                "forecast", "SELECT value FROM orders", "value", 10)
                page = read_tool_result_artifact(first[0]["artifact_id"], allowed_artifacts=first,
                                                 session_id=agent._session_id, workspace_id="", limit=4000)
                self.assertIn("12.5", page["content"])
                self.assertIn("synthetic-v1", page["content"])
                self.assertIn("input_snapshot_sha256", page["content"])
                self.assertEqual(99.0, agent.saved["analysis_result"]["forecast"].iloc[0])
                self.assertNotEqual(first[0]["artifact_id"], agent._analysis_result_artifacts[0]["artifact_id"])

    def test_attempt_recording_deduplicates_scope_views_and_preserves_unknown_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CostEventStore(Path(directory) / "cost.sqlite3")
            failed = {"model_call_id": "call-1", "attempt": 1, "status": "failed",
                      "usage_available": False, "input_tokens": None, "output_tokens": None}
            success = {"model_call_id": "call-1", "attempt": 2, "status": "succeeded",
                       "usage_available": True, "usage_source": "provider_reported",
                       "input_tokens": 120, "output_tokens": 30}
            with patch("agent.workflows.pricing.estimate_model_cost", return_value=None):
                record_model_calls([failed, success], session_id="s", scope_id="s", store=store)
                record_model_calls([failed, success], session_id="s", scope_type="team_task", scope_id="t", store=store)
            events = store.list_events(session_id="s")
            self.assertEqual(2, len(events))
            self.assertIsNone(next(event for event in events if event["attempt"] == 1)["input_tokens"])
            self.assertEqual(2, len(store.list_events(session_id="s", scope_type="team_task", scope_id="t")))
            self.assertEqual(120, store.summary(session_id="s")["known_input_tokens"])

    def test_optional_operator_policy_defaults_to_inherit_and_rejects_bad_json(self):
        self.assertEqual({}, load_optimization_config({}))
        self.assertEqual({}, load_optimization_config({"BAA_APPROVED_ROLE_MODEL_POLICY": "invalid"}))
        config = load_optimization_config({"BAA_APPROVED_ROLE_MODEL_POLICY": '{"version":"v1","approved":false}'})
        self.assertFalse(config["tier_mapping"]["approved"])


if __name__ == "__main__":
    unittest.main()
