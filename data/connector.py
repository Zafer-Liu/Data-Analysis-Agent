#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Data source connectors: Excel (via SQLite in-memory) and SQL databases."""
import logging
import re
import sqlite3
import pandas as pd
from typing import Tuple, List, Optional

log = logging.getLogger(__name__)

MAX_DISPLAY_ROWS = 200   # max rows shown to the LLM in query results


class DataSource:
    name: str = ""

    def get_schema(self) -> str:
        raise NotImplementedError

    def execute_query(self, sql: str) -> Tuple[pd.DataFrame, str]:
        """Returns (dataframe, error_string). error_string is empty on success."""
        raise NotImplementedError

    def create_analysis_table(
        self, sql: str, table_name: str = "analysis_data", _df=None
    ) -> str:
        """
        Materialise a result as a new SQLite table called *table_name*.
        Either run *sql* against the data source, OR pass a pre-computed
        DataFrame via *_df* (used by run_analysis to bypass SQL).
        Returns a human-readable schema string.
        Subsequent calls to execute_query can SELECT from *table_name*.
        """
        raise NotImplementedError

    @staticmethod
    def format_result(df: pd.DataFrame) -> str:
        if df.empty:
            return "Query returned no results."
        total = len(df)
        preview = df.head(MAX_DISPLAY_ROWS)
        text = preview.to_string(index=False, max_cols=30)
        if total > MAX_DISPLAY_ROWS:
            text += f"\n\n... showing {MAX_DISPLAY_ROWS} of {total} rows"
        return text


# ── helpers ────────────────────────────────────────────────────────────────

def _clean_identifier(raw: str) -> str:
    """
    Turn an arbitrary string into a safe SQLite identifier (table or column name).

    Strategy:
    - Flatten MultiIndex tuples produced by pandas (e.g. ('A', '2023') → 'A_2023')
    - Replace every run of characters that are NOT Unicode word-chars (letters,
      digits, underscore — includes CJK) with a single underscore
    - Strip leading/trailing underscores
    - Prefix with '_' when the result starts with a digit
    - Fall back to 'col' for empty results
    """
    # Flatten tuple/list column names (pandas MultiIndex after parse)
    if isinstance(raw, (tuple, list)):
        raw = "_".join(str(x) for x in raw)
    s = str(raw).strip()
    # Replace all non-word characters (including quotes, parens, commas…) with _
    s = re.sub(r"[^\w]+", "_", s, flags=re.UNICODE)
    s = s.strip("_")
    # Identifiers cannot start with a digit in SQL
    if s and s[0].isdigit():
        s = "_" + s
    return s or "col"


def _dedup_columns(cols: List[str]) -> List[str]:
    """Append _2, _3 … to duplicate column names so SQLite CREATE TABLE succeeds."""
    seen: dict = {}
    result = []
    for c in cols:
        if c not in seen:
            seen[c] = 1
            result.append(c)
        else:
            seen[c] += 1
            result.append(f"{c}_{seen[c]}")
    return result


def _table_schema_str(conn: sqlite3.Connection, table: str, row_count: int) -> str:
    cur = conn.cursor()
    cur.execute(f'PRAGMA table_info("{table}")')
    cols = cur.fetchall()
    col_lines = [f"  {c[1]}  {c[2]}" for c in cols]
    return f"Table: {table}  ({row_count} rows)\n" + "\n".join(col_lines)


# ── File-based sources ─────────────────────────────────────────────────────

class ExcelDataSource(DataSource):
    """Load one or more sheets from an Excel file into a shared SQLite in-memory DB."""

    def __init__(self, file_path: str, filename: str):
        self.name = filename
        self.file_path = file_path
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._tables: List[str] = []
        self._load(file_path)

    def _load(self, path: str):
        xl = pd.ExcelFile(path)
        log.info("[ExcelDS] sheets: %s", xl.sheet_names)
        for sheet in xl.sheet_names:
            log.info("[ExcelDS] parsing sheet: %r", sheet)
            df = xl.parse(sheet)
            log.info("[ExcelDS] raw columns (%d): %s", len(df.columns), list(df.columns))
            # Flatten MultiIndex headers, clean every column name, dedup
            cleaned = [_clean_identifier(c) for c in df.columns]
            log.info("[ExcelDS] cleaned columns: %s", cleaned)
            df.columns = _dedup_columns(cleaned)
            log.info("[ExcelDS] final columns: %s", list(df.columns))
            df = df.dropna(how="all")
            table = _clean_identifier(sheet) or f"sheet{len(self._tables) + 1}"
            if df.empty or len(df.columns) == 0:
                log.info("[ExcelDS] sheet %r skipped (no data)", sheet)
                continue
            log.info("[ExcelDS] to_sql → table=%r  rows=%d", table, len(df))
            df.to_sql(table, self._conn, if_exists="replace", index=False)
            self._tables.append(table)
            log.info("[ExcelDS] sheet %r loaded OK", sheet)
        if not self._tables:
            raise ValueError("Excel 文件中未发现有效工作表。")

    def get_schema(self) -> str:
        cursor = self._conn.cursor()
        parts: List[str] = []
        for table in self._tables:
            cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
            rows = cursor.fetchone()[0]
            parts.append(_table_schema_str(self._conn, table, rows))
        return "\n\n".join(parts)

    def execute_query(self, sql: str) -> Tuple[pd.DataFrame, str]:
        try:
            return pd.read_sql_query(sql, self._conn), ""
        except Exception as exc:
            return pd.DataFrame(), str(exc)

    def create_analysis_table(self, sql: str, table_name: str = "analysis_data", _df=None) -> str:
        if _df is not None:
            df = _df
        else:
            df, err = self.execute_query(sql)
            if err:
                return f"Error building analysis table: {err}"
        df.to_sql(table_name, self._conn, if_exists="replace", index=False)
        return _table_schema_str(self._conn, table_name, len(df))

    def get_preview(self, max_rows: int = 500) -> List[dict]:
        """Return [{name, columns, rows, total_rows}] for all tables in the DB."""
        cur = self._conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid")
        all_tables = [r[0] for r in cur.fetchall()]
        result = []
        for t in all_tables:
            try:
                total = self._conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                df = pd.read_sql_query(f'SELECT * FROM "{t}" LIMIT {max_rows}', self._conn)
                result.append({
                    "name": t,
                    "columns": list(df.columns),
                    "rows": df.fillna("").astype(str).values.tolist(),
                    "total_rows": total,
                })
            except Exception:
                continue
        return result


class CSVDataSource(DataSource):
    """Load a single CSV file into a SQLite in-memory DB."""

    def __init__(self, file_path: str, filename: str):
        self.name = filename
        self.file_path = file_path
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        table = _clean_identifier(filename.rsplit(".", 1)[0]) or "data"
        self._table = table
        df = pd.read_csv(file_path, encoding="utf-8-sig")
        df.columns = _dedup_columns([_clean_identifier(c) for c in df.columns])
        df = df.dropna(how="all")
        df.to_sql(table, self._conn, if_exists="replace", index=False)

    def get_schema(self) -> str:
        cursor = self._conn.cursor()
        cursor.execute(f'SELECT COUNT(*) FROM "{self._table}"')
        rows = cursor.fetchone()[0]
        return _table_schema_str(self._conn, self._table, rows)

    def execute_query(self, sql: str) -> Tuple[pd.DataFrame, str]:
        try:
            return pd.read_sql_query(sql, self._conn), ""
        except Exception as exc:
            return pd.DataFrame(), str(exc)

    def create_analysis_table(self, sql: str, table_name: str = "analysis_data", _df=None) -> str:
        if _df is not None:
            df = _df
        else:
            df, err = self.execute_query(sql)
            if err:
                return f"Error building analysis table: {err}"
        df.to_sql(table_name, self._conn, if_exists="replace", index=False)
        return _table_schema_str(self._conn, table_name, len(df))

    def get_preview(self, max_rows: int = 500) -> List[dict]:
        try:
            total = self._conn.execute(f'SELECT COUNT(*) FROM "{self._table}"').fetchone()[0]
            df = pd.read_sql_query(f'SELECT * FROM "{self._table}" LIMIT {max_rows}', self._conn)
            return [{"name": self._table, "columns": list(df.columns),
                     "rows": df.fillna("").astype(str).values.tolist(),
                     "total_rows": total}]
        except Exception:
            return []


# ── SQL database source ────────────────────────────────────────────────────

class SQLDataSource(DataSource):
    """Connect to any SQLAlchemy-supported database."""

    def __init__(self, connection_string: str, display_name: str = ""):
        from sqlalchemy import create_engine, text, inspect as sa_inspect

        self._engine = create_engine(connection_string, pool_pre_ping=True)
        with self._engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        if display_name:
            self.name = display_name
        else:
            try:
                url = self._engine.url
                self.name = f"{url.host}/{url.database or ''}"
            except Exception:
                self.name = "SQL Database"

        self._inspect = sa_inspect(self._engine)
        # Local SQLite for materialised analysis tables
        self._cache_conn: Optional[sqlite3.Connection] = None
        self._cache_tables: set = set()

    def get_schema(self) -> str:
        parts: List[str] = []
        try:
            tables = self._inspect.get_table_names()[:50]
        except Exception:
            tables = []
        for table in tables:
            try:
                cols = self._inspect.get_columns(table)
                col_lines = [f"  {c['name']}  {c['type']}" for c in cols]
                parts.append(f"Table: {table}\n" + "\n".join(col_lines))
            except Exception:
                parts.append(f"Table: {table}  (schema unavailable)")
        # Also expose cached analysis tables
        for t in sorted(self._cache_tables):
            parts.append(_table_schema_str(self._cache_conn, t,
                         pd.read_sql_query(f'SELECT COUNT(*) AS n FROM "{t}"',
                                           self._cache_conn).iloc[0, 0]))
        return "\n\n".join(parts) if parts else "No tables found."

    def execute_query(self, sql: str) -> Tuple[pd.DataFrame, str]:
        # If the SQL references a cached analysis table, route to local SQLite
        if self._cache_conn and any(t in sql for t in self._cache_tables):
            try:
                return pd.read_sql_query(sql, self._cache_conn), ""
            except Exception as exc:
                return pd.DataFrame(), str(exc)
        # Otherwise use external DB
        from sqlalchemy import text
        try:
            with self._engine.connect() as conn:
                df = pd.read_sql(text(sql), conn)
            return df, ""
        except Exception as exc:
            return pd.DataFrame(), str(exc)

    def create_analysis_table(self, sql: str, table_name: str = "analysis_data", _df=None) -> str:
        # Fetch from external DB (or local cache) into pandas, then store locally
        if _df is not None:
            df = _df
        else:
            df, err = self.execute_query(sql)
            if err:
                return f"Error building analysis table: {err}"
        if self._cache_conn is None:
            self._cache_conn = sqlite3.connect(":memory:", check_same_thread=False)
        df.to_sql(table_name, self._cache_conn, if_exists="replace", index=False)
        self._cache_tables.add(table_name)
        return _table_schema_str(self._cache_conn, table_name, len(df))

    def get_preview(self, max_rows: int = 500) -> List[dict]:
        result = []
        # Cached analysis tables first
        if self._cache_conn:
            for t in sorted(self._cache_tables):
                try:
                    total = self._cache_conn.execute(
                        f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    df = pd.read_sql_query(
                        f'SELECT * FROM "{t}" LIMIT {max_rows}', self._cache_conn)
                    result.append({"name": f"[分析表] {t}", "columns": list(df.columns),
                                   "rows": df.fillna("").astype(str).values.tolist(),
                                   "total_rows": total})
                except Exception:
                    continue
        # External DB tables (limit to 20 tables, 200 rows each to stay responsive)
        try:
            tables = self._inspect.get_table_names()[:20]
        except Exception:
            tables = []
        for t in tables:
            try:
                df, err = self.execute_query(f'SELECT * FROM "{t}" LIMIT {min(max_rows, 200)}')
                if err or df.empty:
                    continue
                result.append({"name": t, "columns": list(df.columns),
                               "rows": df.fillna("").astype(str).values.tolist(),
                               "total_rows": None})
            except Exception:
                continue
        return result
