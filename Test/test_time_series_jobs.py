#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B4 time-series JobRunner routing and cancellation tests."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.agent import BusinessAgent
from agent.jobs import JobRunner
from agent.tools.business.data import DataToolsMixin
from data.jobs_store import JobsStore


class _Source:
    name = "test-source"

    def __init__(self, rows: int):
        self.df = pd.DataFrame({
            "ds": pd.date_range("2024-01-01", periods=rows, freq="h"),
            "value": range(rows),
        })

    def execute_query(self, _sql):
        return self.df.copy(), ""


class _Harness(DataToolsMixin):
    _run_as_job = BusinessAgent._run_as_job

    def __init__(self, rows: int, runner: JobRunner):
        self.data_source = _Source(rows)
        self._job_runner = runner
        self._active_job_id = ""
        self._schema_cache = None
        self.writes = []
        self.write_threads = []

    def _write_analysis_df(self, df, table_name: str):
        self.writes.append((table_name, len(df)))
        self.write_threads.append(threading.get_ident())


def _consume(generator, on_event=None):
    events = []
    while True:
        try:
            event = next(generator)
            events.append(event)
            if on_event:
                on_event(event)
        except StopIteration as stop:
            return events, stop.value


def _fake_result(rows=3):
    result = pd.DataFrame({"ds": range(rows), "y_pred": range(rows)})
    breakdown = pd.DataFrame({"ds": range(rows), "trend": range(rows)})
    metrics = pd.DataFrame({"metric": ["rows"], "value": [rows]})
    entry = {"output_tables": ["analysis_result", "analysis_breakdown", "analysis_metrics"]}
    return entry, (result, breakdown, metrics, "analysis complete")


class TestTimeSeriesJobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobsStore(Path(self.tmp.name) / "jobs.db")
        self.runner = JobRunner("b4-session", self.store, max_workers=1)

    def tearDown(self):
        self.runner.shutdown(wait=True)
        self.store.close()
        self.tmp.cleanup()

    def test_small_time_series_stays_synchronous(self):
        harness = _Harness(999, self.runner)
        caller_thread = threading.get_ident()
        compute_threads = []

        def fake_execute(*_args, **_kwargs):
            compute_threads.append(threading.get_ident())
            return _fake_result()

        with patch("agent.tools.business.data._execute_analysis", side_effect=fake_execute):
            events, result = _consume(harness._tool_run_analysis_with_jobs(
                "Time_Series_Prophet", "SELECT * FROM data", "value", "ds", 12
            ))

        self.assertEqual(events, [])
        self.assertEqual(result, "analysis complete")
        self.assertEqual(compute_threads, [caller_thread])
        self.assertEqual(harness.write_threads, [caller_thread] * 3)

    def test_large_time_series_runs_in_worker_and_writes_on_request_thread(self):
        harness = _Harness(1000, self.runner)
        caller_thread = threading.get_ident()
        compute_threads = []

        def fake_execute(*_args, progress_callback=None, **_kwargs):
            compute_threads.append(threading.get_ident())
            progress_callback(40, "fitting")
            progress_callback(90, "formatting")
            return _fake_result()

        with patch("agent.tools.business.data._execute_analysis", side_effect=fake_execute):
            events, result = _consume(harness._tool_run_analysis_with_jobs(
                "Time_Series_Prophet", "SELECT * FROM data", "value", "ds", 12
            ))

        self.assertEqual(result, "analysis complete")
        self.assertNotEqual(compute_threads[0], caller_thread)
        self.assertEqual(harness.write_threads, [caller_thread] * 3)
        self.assertIn("job_started", {event["type"] for event in events})
        self.assertIn("job_progress", {event["type"] for event in events})
        self.assertIn("job_done", {event["type"] for event in events})

    def test_cancel_does_not_publish_partial_analysis_tables(self):
        harness = _Harness(1000, self.runner)
        canceled = False

        def slow_execute(*_args, progress_callback=None, **_kwargs):
            for pct in (10, 20, 30, 40):
                progress_callback(pct, "working")
                time.sleep(0.02)
            return _fake_result()

        def cancel_on_progress(event):
            nonlocal canceled
            if event["type"] == "job_progress" and not canceled:
                canceled = harness._job_runner.cancel(event["job_id"])

        with patch("agent.tools.business.data._execute_analysis", side_effect=slow_execute):
            events, result = _consume(
                harness._tool_run_analysis_with_jobs(
                    "Time_Series_Prophet", "SELECT * FROM data", "value", "ds", 12
                ),
                on_event=cancel_on_progress,
            )

        self.assertTrue(canceled)
        self.assertEqual(result, "Analysis canceled.")
        self.assertEqual(harness.writes, [])
        self.assertIn("job_canceled", {event["type"] for event in events})


if __name__ == "__main__":
    unittest.main()
