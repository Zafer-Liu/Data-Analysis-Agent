#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tool schema/filter/result contract tests."""
import sys
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.tools.exposure import filter_tools_for_turn
from agent.tools.parallel import is_parallel_safe_tool, should_parallelize_batch
from agent.tools import results as result_module
from agent.tools.results import (
    load_tool_result_artifact,
    make_tool_result,
    truncate_tool_result_preserving_refs,
)
from agent.tools.registry import BUILTIN_TOOL_REGISTRY, get_tool_spec, is_job_eligible
from agent.tools.business.data import DataToolsMixin
from agent.tools.schemas import (
    AGENT_TOOLS,
    TOOL_SCHEMA_VERSION,
    get_tool_schema_version,
)
from agent.agent import _as_bool_arg


def _names(tools):
    return {(t.get("function") or {}).get("name") for t in tools}


class TestToolContract(unittest.TestCase):

    def test_tool_confirmation_boolean_parsing_is_strict(self):
        for value in (True, 1, "true", "True", "yes"):
            self.assertTrue(_as_bool_arg(value))
        for value in (False, 0, "false", "False", "no", "", None, []):
            self.assertFalse(_as_bool_arg(value))

    def test_schema_versions_are_registered_without_mutating_openai_schema(self):
        self.assertEqual(get_tool_schema_version("query_data"), TOOL_SCHEMA_VERSION)
        query_tool = next(
            t for t in AGENT_TOOLS
            if (t.get("function") or {}).get("name") == "query_data"
        )
        self.assertNotIn("x-baa-tool-version", query_tool["function"])

    def test_dynamic_filter_hides_output_tools_until_command(self):
        regular = _names(filter_tools_for_turn(
            AGENT_TOOLS, command="", has_data_source=True, include_mcp=False
        ))
        self.assertIn("query_data", regular)
        self.assertIn("query_knowledge", regular)
        self.assertNotIn("propose_ppt_outline", regular)
        self.assertNotIn("generate_dashboard", regular)

        ppt = _names(filter_tools_for_turn(
            AGENT_TOOLS, command="ppt", has_data_source=True, include_mcp=False
        ))
        self.assertIn("propose_ppt_outline", ppt)
        self.assertNotIn("generate_ppt", ppt)

        ppt_skill = _names(filter_tools_for_turn(
            AGENT_TOOLS, trusted_skill="ppt", has_data_source=True, include_mcp=False
        ))
        self.assertIn("propose_ppt_outline", ppt_skill)
        self.assertNotIn("generate_ppt", ppt_skill)

        no_data = _names(filter_tools_for_turn(
            AGENT_TOOLS, command="", has_data_source=False, include_mcp=False
        ))
        self.assertIn("query_knowledge", no_data)
        self.assertNotIn("query_data", no_data)

    def test_registry_matches_all_builtin_schemas(self):
        self.assertEqual(_names(AGENT_TOOLS), BUILTIN_TOOL_REGISTRY.names())

    def test_registry_drives_workspace_command_and_job_policy(self):
        regular = _names(filter_tools_for_turn(
            AGENT_TOOLS, command="", has_data_source=False, include_mcp=False
        ))
        self.assertIn("workspace_status", regular)
        self.assertFalse(get_tool_spec("query_data").concurrency_safe)
        self.assertEqual(get_tool_spec("generate_ppt").job_threshold, "ppt_slides_gt_5")
        self.assertTrue(is_job_eligible("generate_ppt"))
        self.assertTrue(is_job_eligible("run_analysis"))
        self.assertEqual(get_tool_spec("workspace_bash").category, "write")
        self.assertTrue(get_tool_spec("workspace_bash").requires_workspace)
        self.assertTrue(is_job_eligible("query_data"))

    def test_tool_result_envelope_fields_and_error_classification(self):
        env = make_tool_result(
            "query_data",
            "SQL Error: no such column: revenue",
            sources=[{"source": "demo.xlsx"}],
        )
        data = env.to_dict()

        for key in ("ok", "error", "data", "summary", "sources", "artifacts", "debug"):
            self.assertIn(key, data)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "field_not_found")
        self.assertIn("tool_result", env.to_model_text())

    def test_schema_with_zero_row_table_is_not_empty_result_error(self):
        env = make_tool_result(
            "get_schema",
            "[Schema]\nTable: empty_table (0 rows)\n  id INTEGER",
        )

        self.assertTrue(env.ok)
        self.assertEqual(env.error, "")

    def test_schema_sample_text_does_not_trigger_empty_result(self):
        env = make_tool_result(
            "get_schema",
            "Table: logs (2 rows)\n  message VARCHAR\n"
            "  -- sample data (first 2 rows) --\n"
            "  | query returned no rows in the upstream system",
        )

        self.assertTrue(env.ok)
        self.assertEqual(env.error, "")

    def test_query_data_empty_result_still_classified(self):
        env = make_tool_result("query_data", "Query returned no rows.")

        self.assertFalse(env.ok)
        self.assertEqual(env.error, "empty_result")

    def test_large_result_is_persisted_and_model_receives_bounded_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = SimpleNamespace(cache_dir=Path(tmp))
            raw = "row,value\n" + "\n".join(f"{i},{'x' * 80}" for i in range(400))
            env = make_tool_result(
                "query_data", raw, session_id="budget-session", runtime=runtime,
                result_char_budget=1000,
            )
            artifact = next(item for item in env.artifacts if item["type"] == "tool_result")
            self.assertLess(len(env.data), len(raw))
            self.assertIn("full result persisted", env.data)
            self.assertIn(artifact["uri"], env.to_model_text())
            record = load_tool_result_artifact(artifact["artifact_id"], runtime=runtime)
            self.assertEqual(record["data"], raw)
            self.assertEqual(record["sha256"], artifact["sha256"])

    def test_history_trim_preserves_recoverable_artifact_uri(self):
        uri = "artifact://tool-result/tr_abcdef123456"
        raw = "x" * 4000 + uri
        trimmed = truncate_tool_result_preserving_refs(raw, 200)
        self.assertLess(len(trimmed), len(raw))
        self.assertIn(uri, trimmed)

    def test_corrupted_result_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = SimpleNamespace(cache_dir=Path(tmp))
            _preview, artifact, _debug = result_module.persist_large_tool_result(
                "s", "query_data", "z" * 2000, runtime=runtime, threshold=20,
            )
            path = Path(tmp) / "tool_results" / f"{artifact['artifact_id']}.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record["data"] = "tampered"
            path.write_text(json.dumps(record), encoding="utf-8")
            self.assertIsNone(load_tool_result_artifact(artifact["artifact_id"], runtime=runtime))

    def test_schema_snapshots_are_content_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = SimpleNamespace(cache_dir=Path(tmp))
            first = result_module.persist_large_tool_result(
                "s1", "get_schema", "Table: orders\ncity VARCHAR", runtime=runtime,
                threshold=1, deduplicate=True,
            )[1]
            second = result_module.persist_large_tool_result(
                "s2", "get_schema", "Table: orders\ncity VARCHAR", runtime=runtime,
                threshold=1, deduplicate=True,
            )[1]
            self.assertEqual(first["artifact_id"], second["artifact_id"])
            self.assertEqual(len(list((Path(tmp) / "tool_results").glob("*.json"))), 1)

    def test_data_refs_extract_source_tables_sql_and_rows(self):
        class Src:
            name = "demo.xlsx"

        refs = DataToolsMixin()._data_refs_for_sql(
            'SELECT city, SUM(cost) FROM "Raw_data_city" GROUP BY city',
            Src(),
            12,
        )

        self.assertEqual(refs[0]["source"], "demo.xlsx")
        self.assertEqual(refs[0]["title"], "Raw_data_city")
        self.assertEqual(refs[0]["rows"], 12)
        self.assertIn("SUM(cost)", refs[0]["snippet"])

    def test_parallel_policy_is_conservative(self):
        self.assertTrue(is_parallel_safe_tool("get_table_detail"))
        self.assertTrue(is_parallel_safe_tool("mcp__demo__search"))
        self.assertFalse(is_parallel_safe_tool("query_data"))
        self.assertTrue(should_parallelize_batch([
            (None, "get_table_detail", {}),
            (None, "query_knowledge", {}),
        ]))
        self.assertFalse(should_parallelize_batch([
            (None, "query_data", {}),
            (None, "query_knowledge", {}),
        ]))


if __name__ == "__main__":
    unittest.main()
