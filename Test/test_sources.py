#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for data/sources/ — DataSource contract + Excel/CSV round-trips.

These hit the DuckDB in-memory backend with tiny synthetic data, so they
run in <1s and need no external services.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from data.sources import (
    CSVDataSource,
    DataSource,
    ExcelDataSource,
    SQLDataSource,
    MAX_DISPLAY_ROWS,
)
from data.sources._utils import (
    _clean_identifier,
    _dedup_columns,
    _detect_header_row,
)
from data.session import ChatSession


class _LeaseSource:
    def __init__(self, name="leased"):
        self.name = name
        self.closed = False

    def get_schema(self):
        if self.closed:
            raise RuntimeError("source is closed")
        return f"Table: {self.name}  [value (INTEGER)]"

    def list_tables(self):
        if self.closed:
            raise RuntimeError("source is closed")
        return [self.name]

    def close(self):
        self.closed = True


class _LeaseMerged:
    def __init__(self):
        self.closed = False

    def invalidate(self):
        self.closed = True


class TestDataSourceSnapshots(unittest.TestCase):
    def test_removed_source_closes_only_after_last_snapshot_release(self):
        sess = ChatSession(session_id="source-lease")
        source = _LeaseSource()
        source_id = sess.add_source(source)
        first = sess.acquire_data_source_snapshot()
        second = sess.acquire_data_source_snapshot()

        self.assertTrue(sess.remove_source(source_id))
        self.assertFalse(source.closed)
        self.assertEqual(first.primary.list_tables(), ["leased"])
        first.release()
        self.assertFalse(source.closed)
        second.release()
        self.assertTrue(source.closed)
        second.release()  # idempotent

    def test_merged_source_is_leased_with_underlying_sources(self):
        sess = ChatSession(session_id="merged-lease")
        first = _LeaseSource("first")
        second = _LeaseSource("second")
        first_id = sess.add_source(first)
        sess.add_source(second)
        merged = _LeaseMerged()
        sess._merged_source_cache = merged

        snapshot = sess.acquire_data_source_snapshot()
        self.assertIs(snapshot.merged_source, merged)
        sess.remove_source(first_id)
        self.assertFalse(first.closed)
        self.assertFalse(merged.closed)

        snapshot.release()
        self.assertTrue(first.closed)
        self.assertTrue(merged.closed)
        self.assertFalse(second.closed)
        sess.close_sources()
        self.assertTrue(second.closed)


class TestUtilsHelpers(unittest.TestCase):

    def test_clean_identifier_basic(self):
        self.assertEqual(_clean_identifier("Sales 2024"), "Sales_2024")
        self.assertEqual(_clean_identifier("  trim  "),    "trim")

    def test_clean_identifier_leading_digit(self):
        self.assertEqual(_clean_identifier("2024_Q1"), "_2024_Q1")

    def test_clean_identifier_unicode(self):
        # CJK should be preserved (re.UNICODE \w matches CJK)
        out = _clean_identifier("销售额")
        self.assertTrue(out.startswith("销售额") or out == "销售额")

    def test_clean_identifier_empty(self):
        self.assertEqual(_clean_identifier(""),    "col")
        self.assertEqual(_clean_identifier("---"), "col")

    def test_clean_identifier_tuple_input(self):
        # MultiIndex columns arrive as tuples
        self.assertEqual(_clean_identifier(("A", "B")), "A_B")

    def test_dedup_columns(self):
        self.assertEqual(_dedup_columns(["a", "a", "a"]),  ["a", "a_2", "a_3"])
        self.assertEqual(_dedup_columns(["x", "y", "x"]), ["x", "y", "x_2"])
        self.assertEqual(_dedup_columns([]),               [])

    def test_detect_header_row_picks_text_row(self):
        # Row 0 is a comment/blank, row 1 is the real header
        rows = [
            ["销售报表", "", ""],            # only one cell, low score
            ["category", "amount", "date"],  # 3 textual cells, win
            ["A",        "10",     "2024"],
        ]
        self.assertEqual(_detect_header_row(rows), 1)

    def test_detect_header_row_numeric_skip(self):
        # Numeric cells don't count as headers
        rows = [
            ["1", "2", "3"],
            ["Q1", "amount", "year"],
        ]
        self.assertEqual(_detect_header_row(rows), 1)


class TestDataSourceContract(unittest.TestCase):
    """Every concrete source must implement the same surface."""

    def test_base_class_raises_not_implemented(self):
        ds = DataSource()
        with self.assertRaises(NotImplementedError):
            ds.get_schema()
        with self.assertRaises(NotImplementedError):
            ds.execute_query("SELECT 1")
        with self.assertRaises(NotImplementedError):
            ds.create_analysis_table("SELECT 1")

    def test_format_result_handles_empty(self):
        self.assertEqual(
            DataSource.format_result(pd.DataFrame()),
            "Query returned no results."
        )

    def test_format_result_truncates_at_display_cap(self):
        df = pd.DataFrame({"x": range(MAX_DISPLAY_ROWS + 50)})
        out = DataSource.format_result(df)
        self.assertIn(f"showing {MAX_DISPLAY_ROWS} of {MAX_DISPLAY_ROWS + 50}", out)


class TestCSVDataSource(unittest.TestCase):
    """End-to-end: write a tiny CSV, load it, query it, build an analysis table."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.csv_path = os.path.join(cls.tmpdir, "sales.csv")
        pd.DataFrame({
            "category": ["A", "B", "C", "A", "B"],
            "amount":   [100, 200, 150, 110, 220],
        }).to_csv(cls.csv_path, index=False)
        cls.ds = CSVDataSource(cls.csv_path, "sales.csv")

    @classmethod
    def tearDownClass(cls):
        # In-memory DuckDB — connection auto-closed when ds is GC'd
        try:
            os.remove(cls.csv_path)
            os.rmdir(cls.tmpdir)
        except Exception:
            pass

    def test_schema_includes_table_and_columns(self):
        schema = self.ds.get_schema()
        self.assertIn("Table:", schema)
        self.assertIn("category", schema)
        self.assertIn("amount", schema)

    def test_query_returns_rows(self):
        df, err = self.ds.execute_query("SELECT COUNT(*) AS n FROM sales")
        self.assertEqual(err, "")
        self.assertEqual(int(df.iloc[0]["n"]), 5)

    def test_query_returns_error_string_on_bad_sql(self):
        df, err = self.ds.execute_query("SELECT * FROM does_not_exist")
        self.assertNotEqual(err, "")
        self.assertTrue(df.empty)

    def test_groupby_query(self):
        df, err = self.ds.execute_query(
            "SELECT category, SUM(amount) AS total FROM sales GROUP BY category ORDER BY category"
        )
        self.assertEqual(err, "")
        self.assertEqual(len(df), 3)
        # Category A: 100 + 110 = 210
        self.assertEqual(int(df[df.category == "A"].iloc[0]["total"]), 210)

    def test_create_analysis_table(self):
        result = self.ds.create_analysis_table(
            "SELECT category, SUM(amount) AS total FROM sales GROUP BY category",
            "by_cat"
        )
        self.assertIn("by_cat", result)
        # Now queryable
        df, err = self.ds.execute_query("SELECT * FROM by_cat")
        self.assertEqual(err, "")
        self.assertEqual(len(df), 3)

    def test_create_analysis_table_from_df(self):
        df_in = pd.DataFrame({"x": [1, 2, 3]})
        result = self.ds.create_analysis_table("", "from_df", _df=df_in)
        self.assertIn("from_df", result)
        df_out, err = self.ds.execute_query("SELECT COUNT(*) c FROM from_df")
        self.assertEqual(int(df_out.iloc[0]["c"]), 3)

    def test_create_analysis_table_bad_sql_returns_error(self):
        result = self.ds.create_analysis_table("NOT SQL", "broken")
        self.assertIn("Error", result)

    def test_list_tables_includes_analysis_tables(self):
        self.ds.create_analysis_table("SELECT 1 AS a", "list_check")
        tables = self.ds.list_tables()
        self.assertIn("list_check", tables)

    def test_preview_metadata(self):
        rows = self.ds.get_preview()
        self.assertIsInstance(rows, list)
        self.assertGreater(len(rows), 0)
        self.assertIn("columns", rows[0])

    def test_preview_table_returns_rows(self):
        out = self.ds.get_preview_table("sales", max_rows=3)
        self.assertEqual(len(out["rows"]), 3)
        self.assertEqual(out["total_rows"], 5)


class TestSQLDataSourcePreview(unittest.TestCase):
    """Remote SQL preview must stay bounded and must not warm the DuckDB cache."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tmpdir.name) / "remote.sqlite"
        conn = sqlite3.connect(cls.db_path)
        conn.execute("CREATE TABLE orders (id INTEGER, amount REAL)")
        conn.executemany(
            "INSERT INTO orders VALUES (?, ?)",
            [(i, float(i * 10)) for i in range(1, 11)],
        )
        conn.commit()
        conn.close()
        cls.ds = SQLDataSource(f"sqlite:///{cls.db_path.as_posix()}", "test-db")

    @classmethod
    def tearDownClass(cls):
        cls.ds._engine.dispose()
        cls.tmpdir.cleanup()

    def test_metadata_preview_does_not_count_or_load_table(self):
        preview = self.ds.get_preview()
        orders = next(item for item in preview if item["name"] == "orders")
        self.assertEqual(orders["columns"], ["id", "amount"])
        self.assertIsNone(orders["total_rows"])
        self.assertNotIn("orders", self.ds.cache_status()["loaded_tables"])

    def test_agent_scope_is_empty_until_table_is_selected(self):
        self.ds.set_analysis_tables([])
        self.assertEqual(self.ds.list_tables(), [])
        self.assertEqual(self.ds.get_schema(), "")
        _, error = self.ds.execute_query("SELECT * FROM orders")
        self.assertIn("未加入当前分析范围", error)

    def test_selected_sql_schema_contains_bounded_sample_and_query_is_allowed(self):
        self.ds.set_analysis_tables(["orders"])
        schema = self.ds.get_schema()
        self.assertIn("Table: orders", schema)
        self.assertIn("-- sample data (first 2 rows) --", schema)
        self.assertIn("| 1 | 10.0", schema)
        frame, error = self.ds.execute_query("SELECT * FROM orders LIMIT 1")
        self.assertEqual(error, "")
        self.assertEqual(len(frame), 1)

    def test_row_preview_is_limited_without_loading_table(self):
        out = self.ds.get_preview_table("orders", max_rows=3)
        self.assertEqual(len(out["rows"]), 3)
        self.assertIsNone(out["total_rows"])
        self.assertNotIn("orders", self.ds.cache_status()["loaded_tables"])


class TestLegacyConnectorShim(unittest.TestCase):
    """data/connector.py must still re-export every class."""

    def test_legacy_imports_resolve(self):
        from data.connector import (
            CSVDataSource as CSV_legacy,
            DataSource as DS_legacy,
            ExcelDataSource as Excel_legacy,
            GoogleSheetsDataSource as GS_legacy,
            HTTPAPIDataSource as HTTP_legacy,
            SQLDataSource as SQL_legacy,
        )
        from data.sources import (
            CSVDataSource, DataSource, ExcelDataSource,
            GoogleSheetsDataSource, HTTPAPIDataSource, SQLDataSource,
        )
        self.assertIs(CSV_legacy,   CSVDataSource)
        self.assertIs(DS_legacy,    DataSource)
        self.assertIs(Excel_legacy, ExcelDataSource)
        self.assertIs(GS_legacy,    GoogleSheetsDataSource)
        self.assertIs(HTTP_legacy,  HTTPAPIDataSource)
        self.assertIs(SQL_legacy,   SQLDataSource)


if __name__ == "__main__":
    unittest.main()
