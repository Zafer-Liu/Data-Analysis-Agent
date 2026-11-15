#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A6 shadow-copy behavior: agent write SQL never touches on-disk databases."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb
import pandas as pd

from data.sources._utils import is_write_sql
from data.sources.excel import ExcelDataSource
from data.sources.workspace_persistent import WorkspacePersistentSource


def _make_file_db(tmp: Path) -> Path:
    """Create a small on-disk DuckDB with one table of 3 rows."""
    db_path = tmp / "ws.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE t (i INT, s VARCHAR)")
    conn.execute("INSERT INTO t VALUES (1, 'a'), (2, 'b'), (3, 'c')")
    conn.close()
    return db_path


class IsWriteSqlTests(unittest.TestCase):
    def test_reads_are_not_writes(self):
        self.assertFalse(is_write_sql("SELECT * FROM t"))
        self.assertFalse(is_write_sql("WITH x AS (SELECT 1) SELECT * FROM x"))
        self.assertFalse(is_write_sql("SELECT 1 UNION SELECT 2"))
        self.assertFalse(is_write_sql(""))

    def test_writes_are_detected(self):
        for sql in (
            "CREATE TABLE u AS SELECT 1",
            "INSERT INTO t VALUES (9)",
            "UPDATE t SET i = 1",
            "DELETE FROM t",
            "DROP TABLE t",
            "ALTER TABLE t ADD COLUMN x INT",
            "TRUNCATE TABLE t",
            "MERGE INTO t USING s ON t.i = s.i WHEN MATCHED THEN UPDATE SET i = 0",
        ):
            self.assertTrue(is_write_sql(sql), sql)


class WorkspaceShadowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = _make_file_db(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _file_row_count(self) -> int:
        # Same config as the source's own connection: DuckDB refuses mixed
        # read-only/read-write connections to one file within a process.
        conn = duckdb.connect(str(self.db_path))
        try:
            return conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        finally:
            conn.close()

    def test_write_goes_to_shadow_not_file(self):
        src = WorkspacePersistentSource(str(self.db_path))
        try:
            self.assertEqual(3, self._file_row_count())

            df, err = src.execute_query("INSERT INTO t VALUES (9, 'z')")
            self.assertEqual("", err, err)

            # Shadow exists and observes the write …
            self.assertIsNotNone(src._shadow_conn)
            df, err = src.execute_query("SELECT COUNT(*) AS n FROM t")
            self.assertEqual("", err, err)
            self.assertEqual(4, int(df["n"][0]))

            # … but the on-disk file is untouched.
            self.assertEqual(3, self._file_row_count())

            # list_tables / get_schema read the shadow (post-write state)
            self.assertIn("t", src.list_tables())
        finally:
            src.close()

    def test_drop_table_only_drops_in_memory(self):
        src = WorkspacePersistentSource(str(self.db_path))
        try:
            df, err = src.execute_query("DROP TABLE t")
            self.assertEqual("", err, err)

            df, err = src.execute_query(
                "SELECT COUNT(*) AS n FROM information_schema.tables "
                "WHERE table_name = 't'"
            )
            self.assertEqual("", err, err)
            self.assertEqual(0, int(df["n"][0]))

            # On-disk file still has the table.
            conn = duckdb.connect(str(self.db_path))
            try:
                n = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(3, n)
        finally:
            src.close()

    def test_update_via_shadow_visible_to_reads(self):
        src = WorkspacePersistentSource(str(self.db_path))
        try:
            _, err = src.execute_query("UPDATE t SET s = 'Z' WHERE i = 1")
            self.assertEqual("", err, err)
            df, err = src.execute_query("SELECT s FROM t WHERE i = 1")
            self.assertEqual("", err, err)
            self.assertEqual("Z", df["s"][0])
            self.assertEqual(3, self._file_row_count())
        finally:
            src.close()

    def test_analysis_table_stays_in_shadow(self):
        src = WorkspacePersistentSource(str(self.db_path))
        try:
            # Trigger the shadow with a write first (A6 activation rule).
            _, err = src.execute_query("INSERT INTO t VALUES (8, 'y')")
            self.assertEqual("", err, err)

            result = src.create_analysis_table(
                "SELECT * FROM t", table_name="analysis_x"
            )
            self.assertIn("analysis_x", result)
            self.assertIn("analysis_x", src.list_tables())

            # File DB must not contain the analysis table.
            conn = duckdb.connect(str(self.db_path))
            try:
                names = conn.execute(
                    "SELECT table_name FROM information_schema.tables"
                ).fetchall()
            finally:
                conn.close()
            self.assertNotIn(("analysis_x",), names)
        finally:
            src.close()


class ExcelShadowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = _make_file_db(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_from_database_write_stays_in_memory(self):
        src = ExcelDataSource.from_database(
            "fake.xlsx", "fake.xlsx", str(self.db_path)
        )
        try:
            self.assertEqual(["t"], src.list_tables())

            df, err = src.execute_query("INSERT INTO t VALUES (9, 'z')")
            self.assertEqual("", err, err)

            df, err = src.execute_query("SELECT COUNT(*) AS n FROM t")
            self.assertEqual("", err, err)
            self.assertEqual(4, int(df["n"][0]))

            # On-disk job DB untouched.
            conn = duckdb.connect(str(self.db_path))
            try:
                n = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(3, n)
        finally:
            src.close()

    def test_plain_excel_source_writes_directly(self):
        """Regular :memory: instances keep their existing behavior."""
        from data.sources._utils import _register
        src = ExcelDataSource.__new__(ExcelDataSource)
        src.name = "m"
        src.file_path = "m.xlsx"
        src._conn = duckdb.connect(":memory:")
        src._shadow_conn = None
        src._tables = []
        _register(src._conn, "t", pd.DataFrame({"i": [1, 2, 3]}))
        try:
            df, err = src.execute_query("INSERT INTO t VALUES (9)")
            self.assertEqual("", err, err)
            df, err = src.execute_query("SELECT COUNT(*) AS n FROM t")
            self.assertEqual(4, int(df["n"][0]))
            self.assertIsNone(src._shadow_conn)  # :memory: never shadows
        finally:
            src._conn.close()


class SQLDataSourceWriteIsolationTests(unittest.TestCase):
    """Write SQL on a SQLDataSource must never reach the remote engine."""

    def _make_source(self):
        from data.sources.sql import SQLDataSource
        src = SQLDataSource.__new__(SQLDataSource)
        src.name = "remote"
        src._duck = duckdb.connect(":memory:")
        src._duck.execute("CREATE TABLE small (i INT)")
        src._duck.execute("INSERT INTO small VALUES (1), (2)")
        src._loaded = {"small"}
        src._large_tables = {"big"}
        src._cache_tables = set()
        src._analysis_tables = {"small", "big"}
        # Mirror __init__'s lock so the thread-safety guards in execute_query
        # work even though we bypassed __init__ with __new__.
        src._duck_lock = threading.RLock()
        # Placeholder engine — the tests patch it to assert it is never used
        # for writes; only the read-fallback path touches it.
        src._engine = object()
        return src

    def test_write_never_falls_back_to_remote(self):
        src = self._make_source()
        # Simulate a remote engine that must NOT be contacted for writes.
        with patch.object(src, "_engine") as engine, \
                patch.object(src, "_tables_in_sql", return_value=["small"]):
            # Local DuckDB write should succeed without touching _engine.
            df, err = src.execute_query("INSERT INTO small VALUES (9)")
            self.assertEqual("", err, err)
            engine.connect.assert_not_called()

    def test_write_on_large_table_errors_without_remote(self):
        src = self._make_source()
        with patch.object(src, "_engine") as engine, \
                patch.object(src, "_tables_in_sql", return_value=["big"]):
            df, err = src.execute_query("UPDATE big SET i = 1")
            self.assertTrue(err)
            self.assertIn("大表", err)
            engine.connect.assert_not_called()

    def test_read_still_falls_back_to_remote(self):
        src = self._make_source()
        with patch.object(src, "_tables_in_sql", return_value=["big"]):
            with patch.object(src, "_engine") as engine:
                conn = engine.connect.return_value.__enter__.return_value
                cursor = conn.execution_options.return_value.execute.return_value
                cursor.keys.return_value = ["i"]
                cursor.fetchmany.side_effect = [[(1,)], []]
                df, err = src.execute_query("SELECT * FROM big")
                self.assertEqual("", err, err)
                self.assertEqual([1], df["i"].tolist())
                conn.execution_options.assert_called_once_with(stream_results=True, max_row_buffer=1024)
                cursor.close.assert_called_once()


class SQLDataSourceCloseTests(unittest.TestCase):
    """close() must release both the DuckDB cache and the SQLAlchemy engine."""

    def _make_source(self):
        from data.sources.sql import SQLDataSource
        src = SQLDataSource.__new__(SQLDataSource)
        src.name = "remote"
        src._duck = duckdb.connect(":memory:")
        src._duck.execute("CREATE TABLE t (i INT)")
        src._duck.execute("INSERT INTO t VALUES (1)")
        src._loaded = {"t"}
        src._large_tables = set()
        src._cache_tables = set()
        src._analysis_tables = {"t"}
        src._duck_lock = threading.RLock()
        src._engine = MagicMock()
        return src

    def test_close_disposes_engine_and_closes_duck(self):
        src = self._make_source()
        engine_mock = src._engine
        duck_ref = src._duck
        src.close()
        # DuckDB connection is closed and cleared.
        self.assertIsNone(src._duck)
        # Engine pool is disposed and cleared.
        engine_mock.dispose.assert_called_once()
        # Using the closed DuckDB connection now raises.
        with self.assertRaises(Exception):
            duck_ref.execute("SELECT 1")

    def test_close_is_idempotent(self):
        src = self._make_source()
        src.close()
        # A second close() must not raise.
        src.close()
        self.assertIsNone(src._duck)


if __name__ == "__main__":
    unittest.main()
