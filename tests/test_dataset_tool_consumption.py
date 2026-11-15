"""Real snapshot/tool consumption without full-frame reads or remote services."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent.tools.business.data import DataToolsMixin
from data.dataset_catalog import DatasetCatalog
from data.import_pipeline import create_import_plan, run_import
from data.sources.dataset import DatasetDataSource
from infrastructure.resource_budget import BudgetSpec


def finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as done:
            return done.value


def receipt(text):
    return json.loads(text.split("\n", 1)[1])


class Agent(DataToolsMixin):
    def __init__(self, source, *, jobs=False):
        self._user_id = "local-default"
        self._session_id = "dataset-tools"
        self._workspace_id = ""
        self.data_source = source
        self._all_sources = [source]
        self._merged_source = None
        self._schema_cache = None
        self._job_runner = object() if jobs else None
        self.jobs = []

    def _run_as_job(self, worker, *, job_type, label):
        self.jobs.append(job_type)
        yield {"type": "job_started", "label": label}
        result = worker(SimpleNamespace(check_canceled=lambda: None,
                                        is_canceled=lambda: False, set_progress=lambda *_: None))
        return {"status": "succeeded", "result": result}


class DatasetToolConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="baa-tool-snapshot-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.catalog = DatasetCatalog(self.root / "catalog", owner_id="local-default", session_id="dataset-tools")
        self.budget = BudgetSpec(max_input_bytes=8 << 20, max_output_bytes=8 << 20,
                                 temp_bytes=8 << 20, batch_bytes=65536, rss_bytes=768 << 20,
                                 duckdb_memory_bytes=128 << 20, max_run_seconds=30)
        path = self.root / "data.csv"
        path.write_text("id,value\n001,2\n002,\n003,8\n004,10\n", encoding="utf-8", newline="\n")
        plan = create_import_plan(path, owner_id="local-default", session_id="dataset-tools", budget=self.budget)
        manifest = run_import(plan, catalog=self.catalog)
        self.source = DatasetDataSource(self.catalog.get(manifest["dataset_id"], verify=True), budget=self.budget)
        self.agent = Agent(self.source)
        self.addCleanup(self.close_sources)
        self.catalog_patch = patch("data.dataset_catalog.DatasetCatalog", return_value=self.catalog)
        self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)
        self.session_patch = patch("api.state.session_manager", SimpleNamespace(get=lambda _sid: None))
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)

    def close_sources(self):
        merged = self.agent._merged_source
        if merged is not None:
            merged.close()
        for source in self.agent._all_sources:
            source.close()

    def test_derived_analysis_is_queryable_and_bare_names_do_not_match_substrings(self):
        original = self.source.handle.snapshot_id
        text, refs = self.agent._tool_create_analysis_table_with_refs(
            "SELECT id,value FROM data WHERE value >= 8", "analysis_data")
        result = receipt(text)
        self.assertTrue(result["result_committed"])
        self.assertEqual("src2__analysis_data", result["query_table"])
        frame, error = self.agent._all_sources[1].execute_query("SELECT COUNT(*) AS n FROM analysis_data")
        self.assertFalse(error, error)
        self.assertEqual(2, int(frame.iloc[0, 0]))
        source, _frame, error = self.agent._execute_routed_query("SELECT COUNT(*) AS n FROM analysis_data")
        self.assertFalse(error, error)
        self.assertIs(self.agent._all_sources[1], source)
        self.assertEqual(result["snapshot_id"], refs[0]["snapshot_id"])
        self.assertEqual(original, self.source.handle.snapshot_id)
        self.assertEqual(4, self.source.handle.manifest["row_count"])

    def test_cleaning_uses_a_typed_derived_snapshot_and_keeps_original(self):
        self.source.execute_query = Mock(side_effect=AssertionError("Full-frame input is forbidden"))
        text = self.agent._tool_clean_data("fill_na", "data", columns=["value"], fill_method="zero")
        result = receipt(text)
        self.assertTrue(result["complete"])
        self.assertFalse(result["original_source_modified"])
        _, frame, error = self.agent._execute_routed_query('SELECT SUM(value) AS total, COUNT(value) AS n FROM "src2__cleaned_data"')
        self.assertFalse(error, error)
        self.assertEqual((20, 4), tuple(int(value) for value in frame.iloc[0]))
        self.source.execute_query.assert_not_called()

    def test_background_cleaning_preserves_the_commit_receipt(self):
        self.agent._job_runner = object()
        result = receipt(finish(self.agent._tool_clean_data_with_jobs(
            "trimming", "data", trim_column="value", min_val=8, max_val=10)))
        self.assertTrue(result["result_committed"])
        self.assertEqual(2, result["rows"])
        self.assertEqual(["create_analysis_table"], self.agent.jobs)

    def test_profile_aggregates_all_rows_with_batch_cancellation(self):
        self.source.execute_query = Mock(side_effect=AssertionError("Full-frame input is forbidden"))
        result = self.agent._tool_profile_data("data", columns=["value"])
        self.assertEqual(4, result["data"]["row_count"])
        self.assertEqual(1, result["data"]["columns"][0]["missing_count"])
        self.assertFalse(result["data"]["sampling"])
        self.source.execute_query.assert_not_called()
        from data.dataset_operations import profile_dataset
        with self.assertRaisesRegex(RuntimeError, "query_canceled"):
            profile_dataset(self.source, "data", cancel=lambda: True)

    def test_late_cancel_keeps_publication_without_activating_new_source(self):
        canceled = [False]
        derive = self.source.derive_snapshot

        def publish_then_cancel(*args, **kwargs):
            result = derive(*args, **kwargs)
            canceled[0] = True
            return result

        with patch.object(self.source, "derive_snapshot", side_effect=publish_then_cancel):
            text, _refs = self.agent._derive_dataset_table(
                self.source, "SELECT id,value FROM data", "late_result", cancel=lambda: canceled[0])
        result = receipt(text)
        self.assertTrue(result["result_committed"])
        self.assertTrue(result["cancel_requested"])
        self.assertFalse(result["activated"])
        self.assertEqual([self.source], self.agent._all_sources)
        self.assertEqual("ready", self.catalog.get(result["dataset_id"], verify=True).manifest["status"])

    def test_invalid_cleaning_never_publishes_another_version(self):
        before = len(self.catalog.list())
        text = self.agent._tool_clean_data("winsorize", "data", columns=["value"], lower_pct=90, upper_pct=10)
        self.assertIn("清洗失败", text)
        self.assertEqual(before, len(self.catalog.list()))


if __name__ == "__main__":
    unittest.main()
