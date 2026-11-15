"""Synthetic regressions for shared query, analysis and chart source selection."""
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import pandas as pd

from agent.tools.business import data as tools


def finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as done:
            return done.value


class Source:
    def __init__(self, name, value):
        self.name = name
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("CREATE TABLE orders(value INTEGER)")
        self.connection.execute("INSERT INTO orders VALUES (?)", (value,))
        self.calls = []
        self.error = ""

    def list_tables(self):
        return [row[0] for row in self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )]

    def execute_query(self, sql):
        self.calls.append(sql)
        if self.error:
            return pd.DataFrame(), self.error
        try:
            return pd.read_sql_query(sql, self.connection), ""
        except Exception as exc:
            return pd.DataFrame(), str(exc)

    def format_result(self, frame):
        return frame.to_json(orient="records")


class Agent(tools.DataToolsMixin):
    def __init__(self, sources, *, jobs=False, merged=None):
        self.data_source = sources[0]
        self._all_sources = sources
        self._merged_source = merged
        self._job_runner = object() if jobs else None
        self._schema_cache = None
        self.jobs = []
        self.frames = []

    def _tt(self, _chinese, english):
        return english

    def _run_as_job(self, worker, *, job_type, label):
        class Context:
            def set_progress(self, *_args):
                pass

            def check_canceled(self):
                pass

        self.jobs.append(job_type)
        yield {"type": "job_started", "label": label}
        worker(Context())
        return {"status": "succeeded"}

    def _render_chart_from_df(self, frame, *_args):
        self.frames.append(frame.copy())
        return {"html": "<p>synthetic chart</p>", "rows": len(frame)}

    def _finalize_analysis_result(self, _entry, frame, *_args):
        self.frames.append(frame.copy())
        return "analysis complete"


class DataSourceRoutingTests(unittest.TestCase):
    def setUp(self):
        self.primary = Source("primary", 3)
        self.secondary = Source("secondary", 42)
        self.addCleanup(self.primary.connection.close)
        self.addCleanup(self.secondary.connection.close)
        self.agent = Agent([self.primary, self.secondary])
        self.sql = 'SELECT value FROM "src2__orders"'

    def analysis(self, agent=None, *, with_refs=False):
        agent = agent or self.agent
        function = agent._tool_run_analysis_with_refs if with_refs else agent._tool_run_analysis_with_jobs
        with patch.object(tools, "_execute_analysis", side_effect=lambda _name, frame, *_args, **_kw: ({}, frame.copy())):
            return finish(function("Time_Series_ARIMA", self.sql, "value"))

    def test_query_uses_secondary_and_reports_its_actual_source(self):
        result, refs = self.agent._tool_query_data_with_refs(self.sql)
        self.assertEqual([{ "value": 42 }], json.loads(result))
        self.assertEqual("secondary", refs[0]["source"])
        self.assertEqual(1, refs[0]["rows"])
        self.assertEqual([], self.primary.calls)
        self.assertNotIn("src2__", self.secondary.calls[0])

    def test_sync_analysis_and_chart_use_the_selected_input(self):
        with patch.object(tools, "_execute_analysis", side_effect=lambda _name, frame, *_args, **_kw: ({}, frame.copy())):
            self.agent._tool_run_analysis("example", self.sql, "value")
        self.agent._tool_generate_chart("Bar_Chart", self.sql, {"y": "value"})
        self.assertEqual([42, 42], [int(frame["value"].sum()) for frame in self.agent.frames])
        self.assertEqual([], self.primary.calls)

    def test_job_analysis_and_chart_keep_the_same_source_references(self):
        agent = Agent([self.primary, self.secondary], jobs=True)
        with patch.object(tools, "_ANALYSIS_JOB_ROW_THRESHOLD", 0), patch.object(tools, "_CHART_JOB_ROW_THRESHOLD", 0):
            result, analysis_refs = self.analysis(agent, with_refs=True)
            chart, chart_refs = finish(agent._tool_generate_chart_with_refs("Bar_Chart", self.sql, {"y": "value"}))
        self.assertEqual("analysis complete", result)
        self.assertIn("html", chart)
        self.assertEqual(["secondary", "secondary"], [analysis_refs[0]["source"], chart_refs[0]["source"]])
        self.assertEqual([42, 42], [int(frame["value"].sum()) for frame in agent.frames])
        self.assertEqual(["time_series_analysis", "chart_generation"], agent.jobs)
        self.assertEqual([], self.primary.calls)

    def test_query_worker_receives_rewritten_sql_for_the_selected_database(self):
        agent = Agent([self.primary, self.secondary], jobs=True)
        sources = {source.name: source for source in agent._all_sources}
        with patch.object(agent, "_query_data_job_connection_info", side_effect=lambda source: (Path(source.name + ".duckdb"), None)), \
                patch.object(agent, "_estimate_query_rows_for_job", return_value=200_000), \
                patch.object(agent, "_execute_query_in_fresh_db", side_effect=lambda database, sql, _lock: sources[database.stem].execute_query(sql)):
            result, refs = finish(agent._tool_query_data_with_jobs(self.sql))
        self.assertEqual([{"value": 42}], json.loads(result))
        self.assertEqual("secondary", refs[0]["source"])
        self.assertEqual(["query_data"], agent.jobs)
        self.assertEqual([], self.primary.calls)

    def test_compatibility_helpers_still_return_text_and_chart_objects(self):
        self.assertEqual("analysis complete", self.analysis())
        chart = finish(self.agent._tool_generate_chart_with_jobs("Bar_Chart", self.sql, {}))
        self.assertIn("html", chart)

    def test_explicit_missing_source_and_unavailable_merge_do_not_query_primary(self):
        for sql in ("SELECT * FROM src3__orders", "SELECT * FROM src1__orders JOIN src2__orders USING(value)"):
            with self.subTest(sql=sql):
                text, refs = self.agent._tool_query_data_with_refs(sql)
                chart = self.agent._tool_generate_chart("Bar_Chart", sql, {})
                self.assertIn("unavailable", text)
                self.assertIn("unavailable", chart["error"])
                self.assertEqual([], refs)
        self.assertEqual([], self.primary.calls)
        self.assertEqual([], self.secondary.calls)

    def test_selected_source_error_does_not_fall_back_to_same_named_primary_table(self):
        self.secondary.error = "selected source failed"
        result, refs = self.agent._tool_query_data_with_refs(self.sql)
        self.assertIn(self.secondary.error, result)
        self.assertEqual("secondary", refs[0]["source"])
        self.assertEqual([], self.primary.calls)

    def test_bare_name_fallback_reports_the_source_that_actually_succeeded(self):
        self.primary.error = "primary unavailable"
        result, refs = self.agent._tool_query_data_with_refs("SELECT value FROM orders")
        self.assertEqual([{"value": 42}], json.loads(result))
        self.assertEqual("secondary", refs[0]["source"])
        chart, chart_refs = finish(self.agent._tool_generate_chart_with_refs("Bar_Chart", "SELECT value FROM orders", {}))
        self.assertIn("html", chart)
        self.assertEqual("secondary", chart_refs[0]["source"])

    def test_cross_source_queries_keep_prefixes_for_the_merged_connection(self):
        merged = Source("combined", 0)
        self.addCleanup(merged.connection.close)
        merged.connection.execute("CREATE TABLE src1__orders(value INTEGER)")
        merged.connection.execute("CREATE TABLE src2__orders(value INTEGER)")
        merged.connection.execute("INSERT INTO src1__orders VALUES (3)")
        merged.connection.execute("INSERT INTO src2__orders VALUES (42)")
        self.agent._merged_source = merged
        sql = "SELECT a.value + b.value AS total FROM src1__orders a CROSS JOIN src2__orders b"
        result, refs = self.agent._tool_query_data_with_refs(sql)
        self.assertEqual([{"total": 45}], json.loads(result))
        self.assertEqual("combined", refs[0]["source"])
        self.assertEqual([sql], merged.calls)
        self.assertEqual([], self.primary.calls)
        self.assertEqual([], self.secondary.calls)

    def test_prefix_text_inside_literals_and_comments_does_not_select_a_source(self):
        sql = "SELECT 'src2__orders' AS label, value FROM orders -- src3__orders"
        result, refs = self.agent._tool_query_data_with_refs(sql)
        self.assertEqual([{"label": "src2__orders", "value": 3}], json.loads(result))
        self.assertEqual("primary", refs[0]["source"])

    def test_source_rewrite_preserves_literal_values_and_column_qualifiers(self):
        sql = "SELECT 'src1__orders' AS label, src2__orders.value FROM src2__orders"
        result, refs = self.agent._tool_query_data_with_refs(sql)
        self.assertEqual([{"label": "src1__orders", "value": 42}], json.loads(result))
        self.assertEqual("secondary", refs[0]["source"])

    def test_rewrite_keeps_quoted_names_and_quotes_reserved_bare_names(self):
        for table in ("order", "sales--archive"):
            with self.subTest(table=table):
                self.secondary.connection.execute(f'CREATE TABLE "{table}"(value INTEGER)')
                self.secondary.connection.execute(f'INSERT INTO "{table}" VALUES (42)')
                identifier = f'"src2__{table}"' if "--" in table else f"src2__{table}"
                result, refs = self.agent._tool_query_data_with_refs(f"SELECT value FROM {identifier}")
                self.assertEqual([{"value": 42}], json.loads(result))
                self.assertEqual("secondary", refs[0]["source"])

    def test_source_prefixes_are_case_insensitive(self):
        result, _refs = self.agent._tool_query_data_with_refs('SELECT value FROM "SRC2__orders"')
        self.assertEqual([{"value": 42}], json.loads(result))


if __name__ == "__main__":
    unittest.main()
