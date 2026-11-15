"""Application boundaries must not eagerly materialize remote/merged tables."""
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from data.merged_source import MergedDataSource
from data.sources.base import DataSource
from data.sources.sql import SQLDataSource


class SmallSource(DataSource):
    def __init__(self, name, values):
        self.name, self.values = name, values
        self.calls = []

    def list_tables(self):
        return ["orders"]

    def get_preview(self):
        return [{"name": "orders", "columns": ["value"], "total_rows": len(self.values)}]

    def execute_query(self, sql):
        self.calls.append(sql)
        return pd.DataFrame({"value": self.values}), ""


class StreamingIntegrationTests(unittest.TestCase):
    def test_merged_catalog_and_schema_never_fetch_source_rows(self):
        first, second = SmallSource("a", [1, 2]), SmallSource("b", [3, 4])
        merged = MergedDataSource([first, second])
        self.addCleanup(merged.invalidate)
        self.assertIn("src2__orders", merged.list_tables())
        self.assertIn("src1__orders", merged.get_schema())
        self.assertEqual(2, len(merged.get_preview()))
        self.assertEqual([], first.calls)
        self.assertEqual([], second.calls)
        frame, error = merged.execute_query('SELECT SUM(value) AS total FROM "src2__orders"')
        self.assertEqual("", error)
        self.assertEqual(7, int(frame.iloc[0, 0]))
        self.assertEqual([], first.calls)
        self.assertEqual(1, len(second.calls))

    def test_uncached_remote_table_is_not_pulled_by_merge(self):
        remote, small = SmallSource("remote", [1]), SmallSource("local", [2])
        remote._loaded, remote._cache_tables = set(), set()
        merged = MergedDataSource([remote, small])
        self.addCleanup(merged.invalidate)
        frame, error = merged.execute_query('SELECT * FROM "src1__orders"')
        self.assertIn("bounded remote query", error)
        self.assertEqual([], remote.calls)
        self.assertTrue(frame.empty)

    def test_merged_result_limit_is_an_error_not_a_sample(self):
        first, second = SmallSource("a", list(range(400))), SmallSource("b", list(range(400)))
        merged = MergedDataSource([first, second])
        self.addCleanup(merged.invalidate)
        frame, error = merged.execute_query('SELECT a.value, b.value AS other FROM "src1__orders" a CROSS JOIN "src2__orders" b')
        self.assertIn("budget_exceeded", error)
        self.assertTrue(frame.empty)

    def test_remote_cache_admission_has_no_count_and_large_decision_applies_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute("CREATE TABLE orders(id TEXT, amount INTEGER)")
                connection.executemany("INSERT INTO orders VALUES (?, ?)", [(f"{index:04d}", index) for index in range(20)])
                connection.commit()
            finally:
                connection.close()
            source = SQLDataSource("sqlite:///" + path.as_posix())
            try:
                source.set_analysis_tables(["orders"])
                statements = []
                from sqlalchemy import event
                event.listen(source._engine, "before_cursor_execute", lambda _c, _cu, statement, _p, _ct, _em: statements.append(statement))
                with patch("data.sources.sql._LAZY_LOAD_ROW_LIMIT", 5):
                    frame, error = source.execute_query("SELECT id, amount FROM orders ORDER BY amount")
                self.assertEqual("", error)
                self.assertEqual("0000", frame.iloc[0]["id"])
                self.assertEqual(20, len(frame))
                self.assertIn("orders", source._large_tables)
                self.assertNotIn("orders", source._loaded)
                self.assertFalse(any("COUNT(" in statement.upper() for statement in statements), statements)
                with self.assertRaisesRegex(ValueError, "budget_exceeded"):
                    source._read_remote_bounded("SELECT * FROM orders", row_limit=2)
            finally:
                source.close()


if __name__ == "__main__":
    unittest.main()
