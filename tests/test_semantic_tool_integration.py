"""Semantic tool entrypoints use current metadata and immutable definitions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from Function.Knowledge import knowledge_base as knowledge
from Function.Knowledge.ontology_parser import validate_ontology
from Function.Knowledge.ontology_store import validate_binding
from agent.tools.business.data import DataToolsMixin
from data.session import ChatSession


class Source:
    def __init__(self, name, amount):
        self.name, self.amount = name, amount
        self.columns = ["id", "amount"]
        self.calls = []
        self.error = ""

    def list_tables(self):
        return ["orders"]

    def get_table_schema(self, table):
        return {"name": table, "columns": list(self.columns), "schema_available": True}

    def execute_query(self, sql):
        self.calls.append(sql)
        return pd.DataFrame({"revenue": [self.amount]}), self.error

    def format_result(self, frame):
        return str(int(frame.iloc[0, 0]))


class Agent(DataToolsMixin):
    def __init__(self, sources, descriptors):
        self.data_source = sources[0]
        self._all_sources = sources
        self._ontology_sources = descriptors
        self._knowledge_allowed_this_turn = True
        self._user_id = "synthetic-owner"
        self._schema_cache = None


class SemanticToolIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(knowledge, "_embed_batch", side_effect=lambda texts: [[] for _ in texts]))
        self.stack.enter_context(patch.object(knowledge, "_embed_query", return_value=[]))
        self.stack.enter_context(patch.object(knowledge, "_embedding_signature", return_value="tool-fixture"))
        self.kb = knowledge.KnowledgeBase(Path(self.directory.name) / "knowledge.db")
        self.addCleanup(self.kb.close)
        self.document = {
            "version": "1.0.0", "domain": {"name": "Synthetic shop"},
            "entities": {"Order": {"source": {"table": "orders", "primary_key": "id"},
                                      "properties": {"id": {"type": "integer"}, "amount": {"type": "decimal"}}}},
            "metrics": {"revenue": {"entity": "Order", "expression": "SUM(amount)",
                                     "grain": "order", "time_window": "all", "unit": "CNY",
                                     "filters": [], "deduplication": "id", "null_policy": "ignore"}},
        }
        self.sources = [Source("primary", 3), Source("secondary", 42)]
        self.descriptors = [{"source_id": "source-a" if index == 1 else "source-b", "session_id": "sid",
                             "source_key": "physical-a" if index == 1 else "physical-b",
                             "source_name": source.name, "index": index, "tables": {"orders": []}}
                            for index, source in enumerate(self.sources, 1)]
        binding, _ = validate_binding(self.document, {**self.descriptors[1], "tables": {"orders": ["id", "amount"]}})
        self.saved = self.kb.ontologies.save(validate_ontology(self.document), filename="shop.yaml",
                                            source_name="shop.yaml", category_id=1, binding=binding, embed=False)
        self.agent = Agent(self.sources, self.descriptors)

    def adopt(self):
        definitions = [self.kb.ontologies.definition(self.saved["id"], path)
                       for path in ("metrics.revenue", "entities.Order", "domain")]
        return self.agent._capture_semantic_context(self.kb, definitions)

    def test_bound_bare_name_uses_secondary_and_reports_actual_source(self):
        context = self.adopt()
        self.assertEqual("ready", context["state"], context["issues"])
        result, refs = self.agent._tool_query_data_with_refs("SELECT SUM(amount) FROM orders")
        self.assertEqual("42", result)
        self.assertEqual("secondary", refs[0]["source"])
        self.assertEqual([], self.sources[0].calls)

    def test_explicit_sql_cannot_override_the_adopted_binding(self):
        self.adopt()
        result, _refs = self.agent._tool_query_data_with_refs("SELECT SUM(amount) FROM src1__orders")
        self.assertIn("conflicts", result)
        self.assertFalse(any(source.calls for source in self.sources))

    def test_schema_drift_stops_execution_without_replacing_the_snapshot(self):
        original = self.adopt()
        self.sources[1].columns = ["id", "renamed_amount"]
        result, _refs = self.agent._tool_query_data_with_refs("SELECT SUM(amount) FROM src2__orders")
        self.assertIn("Semantic context is not ready", result)
        self.assertEqual("incomplete", self.agent._semantic_context["state"])
        self.assertEqual(original["snapshot_hash"], self.agent._semantic_context["snapshot_hash"])
        self.assertFalse(any(source.calls for source in self.sources))

    def test_selected_bound_source_failure_does_not_fall_back(self):
        self.adopt()
        self.sources[1].error = "selected source failed"
        result, refs = self.agent._tool_query_data_with_refs("SELECT SUM(amount) FROM orders")
        self.assertIn("selected source failed", result)
        self.assertEqual("secondary", refs[0]["source"])
        self.assertEqual([], self.sources[0].calls)

    def test_precise_read_passes_revision_and_returns_followup_information(self):
        original_factory = knowledge.KnowledgeBase
        factory = lambda **_kwargs: original_factory(self.kb._path)
        with patch.object(knowledge, "KnowledgeBase", side_effect=factory):
            results = self.agent._tool_query_knowledge_results("revenue", {
                "ontology_id": self.saved["id"], "path": "metrics.revenue",
                "revision_id": self.saved["revision_id"], "limit": 40,
            })
        self.assertNotIn("error", results, results)
        self.assertEqual(self.saved["revision_id"], results["definition"]["revision_id"])
        self.assertIsNotNone(results["definition"]["next_offset"])
        self.assertTrue(self.agent._knowledge_followup_required)
        text = self.agent._format_knowledge_results(results)
        self.assertIn("next_offset", text)
        self.assertIn("SEMANTIC CONTEXT", text)

    def test_selected_exact_definition_can_replace_an_ambiguous_context(self):
        self.agent._semantic_context = {"state": "needs_clarification"}
        factory = knowledge.KnowledgeBase
        with patch.object(knowledge, "KnowledgeBase", side_effect=lambda **_kwargs: factory(self.kb._path)):
            results = self.agent._tool_query_knowledge_results("selected revenue", {
                "ontology_id": self.saved["id"], "path": "metrics.revenue", "replace_context": True,
            })
        self.assertNotIn("error", results, results)
        self.assertNotIn("snapshot_mismatch", {issue["code"] for issue in results["semantic_context"]["issues"]})

    def test_context_is_isolated_between_worker_threads(self):
        self.adopt()
        original = self.agent._semantic_context
        def worker():
            self.assertIsNone(self.agent._semantic_context)
            self.agent._semantic_context = {"state": "incomplete"}
            return self.agent._semantic_context
        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertEqual("incomplete", executor.submit(worker).result()["state"])
        self.assertEqual(original, self.agent._semantic_context)

    def test_missing_usage_preserves_known_totals_and_context_anchor(self):
        session = ChatSession()
        session.record_usage(120, 30, breakdown={"usage_available": True})
        session.record_usage(None, None, breakdown={"usage_available": False})
        self.assertEqual(120, session.total_input_tokens)
        self.assertEqual(30, session.total_output_tokens)
        self.assertEqual(120, session.last_prompt_tokens)
        self.assertEqual(1, session.unavailable_usage_calls)
        self.assertIsNone(session.usage_breakdowns[-1]["actual_prompt_tokens"])
        snapshot = session.capture_rewind_state()
        session.clear_history()
        session.restore_rewind_state(snapshot)
        self.assertEqual(1, session.unavailable_usage_calls)


if __name__ == "__main__":
    unittest.main()
