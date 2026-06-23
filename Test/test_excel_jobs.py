# -*- coding: utf-8 -*-
"""B2 Excel parse job tests: progress, handoff and cooperative cancellation."""
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from agent.jobs import JobCanceled
from data.sources.excel import ExcelDataSource, parse_excel_job


class _RecordingContext:
    def __init__(self, cancel_after_checks=None):
        self.progress = []
        self.checks = 0
        self.cancel_after_checks = cancel_after_checks

    def set_progress(self, pct, message=""):
        self.progress.append((pct, message))

    def check_canceled(self):
        self.checks += 1
        if self.cancel_after_checks is not None and self.checks >= self.cancel_after_checks:
            raise JobCanceled("test-job")


class TestExcelParseJob(unittest.TestCase):
    def _workbook(self, root: Path) -> Path:
        path = root / "multi-sheet.xlsx"
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            pd.DataFrame({"region": ["华北", "华南"], "sales": [10, 20]}).to_excel(
                writer, sheet_name="销售", index=False
            )
            pd.DataFrame({"sku": ["A", "B"], "stock": [3, 4]}).to_excel(
                writer, sheet_name="库存", index=False
            )
        return path

    def test_parse_reports_per_sheet_progress_and_can_be_attached(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = self._workbook(root)
            database = root / "parsed.duckdb"
            ctx = _RecordingContext()

            result = parse_excel_job(ctx, str(workbook), str(database), workbook.name)

            self.assertEqual(result["sheet_count"], 2)
            self.assertTrue(database.exists())
            messages = [message for _, message in ctx.progress]
            self.assertTrue(any("1/2" in message and "销售" in message for message in messages))
            self.assertTrue(any("2/2" in message and "库存" in message for message in messages))
            self.assertEqual(ctx.progress[-1][0], 100)

            source = ExcelDataSource.from_database(str(workbook), workbook.name, str(database))
            try:
                self.assertEqual(set(source.list_tables()), {"销售", "库存"})
                frame, error = source.execute_query('SELECT SUM(sales) AS total FROM "销售"')
                self.assertEqual(error, "")
                self.assertEqual(int(frame.iloc[0]["total"]), 30)
            finally:
                source._conn.close()

    def test_cancel_removes_incomplete_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = self._workbook(root)
            database = root / "canceled.duckdb"
            ctx = _RecordingContext(cancel_after_checks=2)

            with self.assertRaises(JobCanceled):
                parse_excel_job(ctx, str(workbook), str(database), workbook.name)

            self.assertFalse(database.exists())


if __name__ == "__main__":
    unittest.main()
