#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLDataSource — SQLAlchemy connector with on-demand DuckDB caching.

Design
------
- On connect: only schema metadata is fetched (no data pulled).
- On first query of a table: the full table is loaded into DuckDB once,
  then all subsequent queries against that table run entirely in DuckDB
  (fast, no round-trips to the remote DB).
- Analysis/derived tables (create_analysis_table) also live in DuckDB.
- The remote DB is only contacted again when a table has not yet been cached.

This gives the best of both worlds:
  • Large databases: only tables actually queried are loaded (lazy).
  • Repeated queries: zero remote round-trips after first load.
  • Full SQL power of DuckDB (window functions, JSON, etc.) on remote data.
"""
import logging
import os
import re
from typing import List, Optional, Set, Tuple

import duckdb
import pandas as pd

from ._utils import _new_conn, _preview_table_dict, _query, _register, _table_schema_str
from .base import DataSource

log = logging.getLogger(__name__)

# Maximum rows to pull from a remote table into DuckDB in one shot.
# Tables larger than this are flagged as "large" and queries against them are
# routed directly to the remote DB (no full-table pull).
# Override via env var: BAA_LAZY_LOAD_LIMIT=200000
_LAZY_LOAD_ROW_LIMIT = int(os.getenv("BAA_LAZY_LOAD_LIMIT", "500000"))


class SQLDataSource(DataSource):
    """Connect to any SQLAlchemy-supported database with on-demand DuckDB caching."""

    def __init__(self, connection_string: str, display_name: str = ""):
        from sqlalchemy import create_engine, text, inspect as sa_inspect

        # SQL Server connections otherwise use the ODBC driver's long default
        # login timeout.  Keep the UI responsive when the host/port is wrong.
        # `timeout` is a pyodbc-specific connect argument, so never pass it to
        # the other SQLAlchemy dialects.
        is_sql_server = connection_string.lower().startswith("mssql")
        engine_options = {"pool_pre_ping": True}
        if is_sql_server:
            engine_options["connect_args"] = {"timeout": 10}
        self._engine = create_engine(connection_string, **engine_options)
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

        # Single DuckDB connection for ALL cached data (source tables + analysis tables)
        self._duck: duckdb.DuckDBPyConnection = _new_conn()
        # Tables already loaded into DuckDB from the remote DB
        self._loaded: Set[str] = set()
        # Tables that exceeded _LAZY_LOAD_ROW_LIMIT — queries are routed to remote DB
        self._large_tables: Set[str] = set()
        # Analysis/derived tables created by create_analysis_table
        self._cache_tables: Set[str] = set()
        # Remote SQL catalogs are potentially huge.  A newly connected source
        # exposes metadata to the preview UI, but exposes no source table to the
        # agent until the user explicitly selects an analysis scope.
        self._analysis_tables: Set[str] = set()

    # ── Schema helpers ────────────────────────────────────────────────────────

    def _all_table_names(self) -> List[str]:
        """Return all tables + views from the source DB, no artificial cap."""
        try:
            self._inspect.clear_cache()
        except Exception:
            pass
        try:
            tables = self._inspect.get_table_names()
        except Exception:
            tables = []
        try:
            views = self._inspect.get_view_names()
        except Exception:
            views = []
        seen = set(tables)
        return tables + [v for v in views if v not in seen]

    def _quote(self, name: str) -> str:
        """Dialect-aware SQL identifier quoting for the remote DB."""
        try:
            dialect = self._engine.dialect.name
        except Exception:
            dialect = ""
        return f"`{name}`" if dialect in ("mysql", "mariadb") else f'"{name}"'

    def list_catalog_tables(self) -> List[str]:
        """Full remote catalog for preview/selection validation only."""
        return self._all_table_names()

    def get_analysis_tables(self) -> List[str]:
        """Remote tables currently authorized for agent analysis."""
        catalog = self._all_table_names()
        allowed = getattr(self, "_analysis_tables", set())
        return [name for name in catalog if name in allowed]

    def set_analysis_tables(self, table_names: List[str]) -> List[str]:
        """Replace the agent-visible SQL table scope after strict validation."""
        catalog = self._all_table_names()
        catalog_set = set(catalog)
        requested = list(dict.fromkeys(str(name).strip() for name in table_names if str(name).strip()))
        unknown = [name for name in requested if name not in catalog_set]
        if unknown:
            raise ValueError(f"数据库中不存在这些表：{', '.join(unknown)}")
        if len(requested) > 20:
            raise ValueError("一次最多选择 20 张 SQL 分析表")
        self._analysis_tables = set(requested)
        return [name for name in catalog if name in self._analysis_tables]

    def _assert_analysis_scope(self, sql: str) -> List[str]:
        referenced = self._tables_in_sql(sql)
        allowed = set(getattr(self, "_analysis_tables", set()))
        denied = [name for name in referenced if name not in allowed]
        if denied:
            raise PermissionError(
                f"SQL 表未加入当前分析范围：{', '.join(denied)}。请先在数据预览中选择分析表。"
            )
        if not allowed and self._all_table_names():
            raise PermissionError("尚未选择 SQL 分析表，请先在数据预览中选择一张或多张表。")
        return referenced

    def _remote_schema_with_sample(self, table: str) -> str:
        """Return metadata plus at most two remote rows, without COUNT/full pull."""
        cols = self._inspect.get_columns(table)
        lines = [f"  {c['name']}  {c['type']}" for c in cols]
        try:
            from sqlalchemy import MetaData, Table, select
            remote = Table(table, MetaData(), autoload_with=self._engine)
            with self._engine.connect() as conn:
                df = pd.read_sql(select(remote).limit(2), conn)
            if not df.empty:
                lines.append("  -- sample data (first 2 rows) --")
                for row in df.itertuples(index=False, name=None):
                    lines.append("  | " + " | ".join(
                        str(value).replace("\n", " ")[:30] if value is not None else "NULL"
                        for value in row
                    ))
        except Exception as exc:
            log.warning("[SQLDataSource] sample fetch failed for %r: %s", table, exc)
        return f"Table: {table}\n" + "\n".join(lines)

    # ── On-demand table loading ───────────────────────────────────────────────

    def _ensure_loaded(self, table_name: str) -> bool:
        """Load `table_name` from remote DB into DuckDB if not already cached.

        Row-count gate: if the table has more than _LAZY_LOAD_ROW_LIMIT rows it
        is flagged as a "large table" and NOT pulled into DuckDB.  Queries
        against large tables are routed to the remote DB in execute_query().

        Returns True if the table is available in DuckDB after this call,
        False if it is a large table (remote-only) or if loading failed.
        """
        if table_name in self._loaded or table_name in self._cache_tables:
            return True
        if table_name in self._large_tables:
            return False  # already decided: remote-only

        q = self._quote(table_name)
        # ── Row count check ────────────────────────────────────────────────────
        row_count: Optional[int] = None
        try:
            from sqlalchemy import text as _text
            with self._engine.connect() as _c:
                row_count = _c.execute(_text(f"SELECT COUNT(*) FROM {q}")).scalar()
        except Exception as exc:
            log.warning("[SQLDataSource] row count check failed for %r: %s", table_name, exc)

        if row_count is not None and row_count > _LAZY_LOAD_ROW_LIMIT:
            self._large_tables.add(table_name)
            log.warning(
                "[SQLDataSource] table %r has %d rows (> limit %d) — "
                "marked as large table, queries will run on remote DB",
                table_name, row_count, _LAZY_LOAD_ROW_LIMIT,
            )
            return False

        # ── Pull into DuckDB ───────────────────────────────────────────────────
        log.info("[SQLDataSource] loading table %r into DuckDB …", table_name)
        try:
            from sqlalchemy import text as _text
            with self._engine.connect() as conn:
                df = pd.read_sql(_text(f"SELECT * FROM {q}"), conn)
            # Register directly without _sanitize_df — SQL types are already correct
            # and _sanitize_df's float→datetime heuristic is for Excel files only.
            self._duck.register("_tmp_sql_", df)
            self._duck.execute(
                f'CREATE OR REPLACE TABLE "{table_name}" AS SELECT * FROM _tmp_sql_'
            )
            self._duck.unregister("_tmp_sql_")
            self._loaded.add(table_name)
            log.info("[SQLDataSource] loaded %r → %d rows into DuckDB", table_name, len(df))
            return True
        except Exception as exc:
            log.warning("[SQLDataSource] failed to load %r: %s", table_name, exc)
            return False

    def _tables_in_sql(self, sql: str) -> List[str]:
        """Extract table names referenced in a SQL string via sqlglot AST.

        Falls back to the old substring-match heuristic when sqlglot is not
        installed or fails to parse, so existing behaviour is preserved.
        """
        known = set(self._all_table_names())
        if not known:
            return []

        # ── sqlglot AST path ───────────────────────────────────────────────────
        try:
            import sqlglot
            import sqlglot.expressions as exp

            parsed = sqlglot.parse(sql, dialect="duckdb", error_level=sqlglot.ErrorLevel.WARN)
            found: Set[str] = set()
            for stmt in parsed:
                if stmt is None:
                    continue
                for tbl_node in stmt.find_all(exp.Table):
                    tbl_name = tbl_node.name
                    if tbl_name:
                        found.add(tbl_name)
                        found.add(tbl_name.lower())
                        found.add(tbl_name.upper())

            result = [t for t in known if t in found or t.lower() in found or t.upper() in found]
            if result:
                return result
            # If AST found nothing (e.g. all CTEs), fall through to heuristic
        except Exception as exc:
            log.debug("[SQLDataSource] sqlglot table extraction failed (%s), using heuristic", exc)

        # ── Fallback: substring match on known table names ─────────────────────
        sql_upper = sql.upper()
        return [t for t in known if t.upper() in sql_upper]

    def _ensure_sql_tables_loaded(self, sql: str) -> None:
        """Load any tables mentioned in `sql` that aren't in DuckDB yet."""
        for table in self._tables_in_sql(sql):
            if table not in self._loaded and table not in self._cache_tables:
                self._ensure_loaded(table)

    # ── DataSource interface ──────────────────────────────────────────────────

    def get_schema(self) -> str:
        all_tables = self.get_analysis_tables()
        n = len(all_tables)
        MAX_FULL = 20
        parts: List[str] = []

        if n > MAX_FULL:
            loaded_note = f" ({len(self._loaded)} 张已缓存到 DuckDB)" if self._loaded else ""
            parts.append(
                f"[Database schema — {n} tables total{loaded_note}. "
                f"Full column details shown for first {MAX_FULL} tables. "
                f"Use get_table_detail(table_name) for any other table.]\n"
                f"All tables: {', '.join(all_tables)}"
            )
            detail_tables = all_tables[:MAX_FULL]
        else:
            detail_tables = all_tables

        for table in detail_tables:
            cached = " [已缓存]" if table in self._loaded else ""
            try:
                detail = self._remote_schema_with_sample(table)
                if cached:
                    detail = detail.replace(f"Table: {table}", f"Table: {table}{cached}", 1)
                parts.append(detail)
            except Exception:
                parts.append(f"Table: {table}{cached}  (schema unavailable)")

        # Analysis cache tables always shown in full
        for t in sorted(self._cache_tables):
            try:
                rows = self._duck.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                parts.append(_table_schema_str(self._duck, t, rows))
            except Exception:
                parts.append(f"Table: {t}  (analysis cache)")

        return "\n\n".join(parts) if parts else ""

    def get_table_detail(self, table_name: str) -> str:
        """Full column list + row count for a single table."""
        if table_name not in set(self.get_analysis_tables()) and table_name not in self._cache_tables:
            return f"Error: SQL 表 {table_name} 未加入当前分析范围。"
        try:
            self._inspect.clear_cache()
        except Exception:
            pass

        # If already in DuckDB, get row count from there (fast)
        row_hint = ""
        if table_name in self._loaded:
            try:
                total = self._duck.execute(
                    f'SELECT COUNT(*) FROM "{table_name}"'
                ).fetchone()[0]
                row_hint = f"  ({total:,} rows — cached in DuckDB)"
            except Exception:
                pass
        else:
            try:
                from sqlalchemy import text as _text
                q = self._quote(table_name)
                with self._engine.connect() as _c:
                    total = _c.execute(_text(f"SELECT COUNT(*) FROM {q}")).scalar()
                row_hint = f"  ({total:,} rows)"
            except Exception:
                pass

        try:
            detail = self._remote_schema_with_sample(table_name)
            return detail.replace(f"Table: {table_name}", f"Table: {table_name}{row_hint}", 1)
        except Exception as exc:
            return f"Table: {table_name}  — error: {exc}"

    def execute_query(self, sql: str) -> Tuple[pd.DataFrame, str]:
        """Execute SQL — automatically loads referenced tables into DuckDB first.

        Large-table routing: if any table referenced in `sql` was flagged as
        too big to pull into DuckDB, the query is sent to the remote DB instead.
        The result is then registered as a temporary view in DuckDB so that
        subsequent analysis tables can JOIN against it.
        """
        # Identify which known tables this SQL references
        try:
            referenced = self._assert_analysis_scope(sql)
        except PermissionError as exc:
            return pd.DataFrame(), str(exc)

        # Separate large tables (remote-only) from small ones (pull into DuckDB)
        large_refs = [t for t in referenced if t in self._large_tables]
        for t in referenced:
            if t not in self._large_tables and t not in self._loaded and t not in self._cache_tables:
                self._ensure_loaded(t)

        # If any large table is involved, run the whole query on the remote DB
        if large_refs:
            log.info(
                "[SQLDataSource] large table(s) %s in query — routing to remote DB",
                large_refs,
            )
            try:
                from sqlalchemy import text as _text
                with self._engine.connect() as conn:
                    df = pd.read_sql(_text(sql), conn)
                # Register result as a temporary view so downstream analysis can use it
                _view_name = "_large_query_result_"
                self._duck.register(_view_name, df)
                return df, ""
            except Exception as exc:
                return pd.DataFrame(), str(exc)

        # All referenced tables are in DuckDB — run locally
        df, err = _query(self._duck, sql)
        if not err:
            return df, ""

        # DuckDB failed — fall back to remote DB for safety
        log.warning("[SQLDataSource] DuckDB query failed (%s), trying remote DB", err)
        try:
            from sqlalchemy import text as _text
            with self._engine.connect() as conn:
                df = pd.read_sql(_text(sql), conn)
            return df, ""
        except Exception as exc:
            return pd.DataFrame(), str(exc)

    def create_analysis_table(self, sql: str, table_name: str = "analysis_data",
                              _df=None) -> str:
        """Create a derived/analysis table in DuckDB."""
        if _df is not None:
            _register(self._duck, table_name, _df)
            rows = len(_df)
        else:
            # Ensure source tables in this SQL are loaded first
            try:
                self._assert_analysis_scope(sql)
            except PermissionError as exc:
                return f"Error building analysis table: {exc}"
            self._ensure_sql_tables_loaded(sql)
            try:
                self._duck.execute(
                    f'CREATE OR REPLACE TABLE "{table_name}" AS ({sql})'
                )
                rows = self._duck.execute(
                    f'SELECT COUNT(*) FROM "{table_name}"'
                ).fetchone()[0]
            except Exception as exc:
                return f"Error building analysis table: {exc}"

        self._cache_tables.add(table_name)
        return _table_schema_str(self._duck, table_name, rows)

    def list_tables(self) -> List[str]:
        """Agent-visible selected source tables + analysis cache tables."""
        tables = self.get_analysis_tables()
        for t in sorted(self._cache_tables):
            if t not in tables:
                tables.append(t)
        return tables

    def get_preview(self) -> List[dict]:
        result = []

        # Analysis cache tables (DuckDB)
        for t in sorted(self._cache_tables):
            try:
                cols  = [r[0] for r in self._duck.execute(f'DESCRIBE "{t}"').fetchall()]
                total = self._duck.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                result.append({"name": f"[分析表] {t}", "columns": cols, "total_rows": total})
            except Exception:
                continue

        # Source tables — metadata only.  Opening the preview must stay cheap:
        # do not run COUNT(*) for every remote table and do not pull row data.
        for t in self._all_table_names():
            if t in self._loaded:
                try:
                    cols  = [r[0] for r in self._duck.execute(f'DESCRIBE "{t}"').fetchall()]
                    total = self._duck.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    result.append({"name": t, "columns": cols, "total_rows": total})
                except Exception:
                    result.append({"name": t, "columns": [], "total_rows": None})
            else:
                try:
                    cols = [c["name"] for c in self._inspect.get_columns(t)]
                    result.append({"name": t, "columns": cols, "total_rows": None})
                except Exception:
                    result.append({"name": t, "columns": [], "total_rows": None})

        return result

    def get_preview_table(self, table_name: str, max_rows: int = 100) -> dict:
        # Analysis cache
        if table_name.startswith("[分析表] "):
            real = table_name[len("[分析表] "):]
            return _preview_table_dict(self._duck, real, table_name, max_rows)

        # Source-table preview is always a bounded remote query.  In particular,
        # it must not call _ensure_loaded(): asking to see 100 rows should never
        # download a complete remote table into DuckDB as a side effect.
        try:
            from sqlalchemy import MetaData, Table, select
            remote_table = Table(table_name, MetaData(), autoload_with=self._engine)
            stmt = select(remote_table).limit(max_rows)
            with self._engine.connect() as conn:
                df = pd.read_sql(stmt, conn)
        except Exception as exc:
            return {"name": table_name, "columns": [], "rows": [], "total_rows": None,
                    "error": str(exc)}

        rows = [["" if v is None else str(v) for v in row] for row in df.itertuples(index=False)]
        return {"name": table_name, "columns": list(df.columns),
                "rows": rows, "total_rows": None}

    # ── Cache inspection ──────────────────────────────────────────────────────

    def cache_status(self) -> dict:
        """Return info about what's currently cached in DuckDB."""
        return {
            "loaded_tables": sorted(self._loaded),
            "large_tables": sorted(self._large_tables),
            "analysis_tables": sorted(self._cache_tables),
            "total_cached": len(self._loaded) + len(self._cache_tables),
            "row_limit": _LAZY_LOAD_ROW_LIMIT,
        }
