#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MergedDataSource — 多数据源虚拟合并层。

当多个活跃数据源存在同名表（列名相同、表名相同）时，单一数据源无法
支持跨源 JOIN / UNION。本模块在一个新的 DuckDB 连接中，将所有活跃数
据源的表以 ``src{N}__<tablename>`` 的带前缀格式注册，使得 LLM 可以写出
跨源 SQL，且所有执行都在同一个 DuckDB 引擎内完成。

使用方式
--------
由 ``data/session.py`` 的 ``ChatSession.get_merged_source()`` 按需创建
并缓存；``agent/tools_data.py`` 中的 ``_route_query`` 检测到 SQL 含
``src{N}__`` 前缀时自动切换到该对象执行。

生命周期
--------
- 每次数据源列表变化（add / remove / toggle）都应调用 ``invalidate()``，
  下次查询时会重建合并连接。
- 单数据源时永远不需要此对象（``session.py`` 保证不创建）。
"""
import logging
import threading
import tempfile
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .sources._utils import (
    _new_conn,
    _table_schema_str,
    _query,
    _register,
)
from .sources.base import DataSource

log = logging.getLogger(__name__)


class MergedDataSource(DataSource):
    """虚拟合并数据源：将 N 个 DataSource 的表以 src{N}__ 前缀注册到同一
    个 DuckDB 连接，支持跨源 JOIN / UNION。

    Parameters
    ----------
    sources : list of DataSource
        已激活的数据源列表，顺序决定前缀编号（1-based）。
    """

    def __init__(self, sources: List[DataSource]):
        if len(sources) < 2:
            raise ValueError("MergedDataSource 至少需要 2 个数据源")

        self.name = "merged"
        self._sources = sources
        self._conn = _new_conn()
        self._lock = threading.RLock()
        self._registered: set[str] = set()
        self._conn.execute("SET memory_limit='512MiB'")
        self._conn.execute("SET threads=2")
        self._spill_directory = tempfile.TemporaryDirectory(prefix="baa-merged-")
        self._conn.execute("SET temp_directory = ?", [self._spill_directory.name])
        self._spill_limit_enforced = False
        try:
            self._conn.execute("SET max_temp_directory_size='8GiB'")
            self._spill_limit_enforced = True
        except Exception:
            log.info("[MergedDS] temporary storage limit is unavailable in this engine")

        # 已注册的带前缀表名 → (src_index, bare_table_name)
        self._prefix_map: Dict[str, Tuple[int, str]] = {}
        # 分析/派生表（写入合并连接）
        self._analysis_tables: List[str] = []

        self._build()

    # ── 内部构建 ──────────────────────────────────────────────────────────────

    def _build(self) -> None:
        """Index metadata only. Materialize only tables a query actually uses."""
        for index, source in enumerate(self._sources, 1):
            try:
                tables = source.list_tables()
            except Exception as exc:
                log.warning("[MergedDS] source catalog unavailable: %s", type(exc).__name__)
                tables = []
            for table in tables:
                self._prefix_map[f"src{index}__{table}"] = (index, table)

    def _prepare_query(self, sql: str) -> None:
        import sqlglot
        from sqlglot import expressions as exp
        from pathlib import Path

        parsed = sqlglot.parse_one(sql, dialect="duckdb", error_level=sqlglot.ErrorLevel.RAISE)
        if parsed is None:
            raise ValueError("SQL is required")
        for table in parsed.find_all(exp.Table):
            name = table.name
            if name not in self._prefix_map or name in self._registered:
                continue
            index, bare = self._prefix_map[name]
            source = self._sources[index - 1]
            handle = getattr(source, "handle", None)
            if handle is not None:
                if not self._spill_limit_enforced:
                    raise ValueError("A supported DuckDB engine with bounded spill storage is required for dataset joins")
                root = Path(handle.root).resolve(strict=True)
                files = handle.manifest.get("files", [])
                paths = []
                for item in files:
                    if item.get("table") and item["table"] != bare:
                        continue
                    relative = item.get("relative_path")
                    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
                        raise ValueError("Invalid immutable dataset file")
                    candidate = root / relative
                    if candidate.is_symlink() or not candidate.resolve(strict=True).is_relative_to(root) or not candidate.is_file():
                        raise ValueError("Dataset file is unavailable or changed")
                    if candidate.suffix.lower() != ".parquet":
                        raise ValueError("Merged datasets require immutable Parquet snapshots")
                    paths.append(str(candidate))
                if not paths:
                    raise ValueError("Dataset has no committed Parquet files for this table")
                self._conn.read_parquet(paths).create_view(name, replace=True)
            else:
                loaded = getattr(source, "_loaded", None)
                if loaded is not None and bare not in loaded and bare not in getattr(source, "_cache_tables", set()):
                    raise ValueError("Materialize a bounded remote query as a dataset before a cross-source JOIN")
                capabilities = source.get_capabilities() if callable(getattr(source, "get_capabilities", None)) else {}
                if capabilities.get("native_query") or capabilities.get("cluster"):
                    raise ValueError("Materialize a bounded cluster result before a cross-source JOIN")
                quoted = '"' + bare.replace('"', '""') + '"'
                frame, error = source.execute_query(f"SELECT * FROM {quoted}")
                if error:
                    raise ValueError(error)
                if len(frame) > 100_000 or int(frame.memory_usage(index=True, deep=True).sum()) > 64 * 1024 * 1024:
                    raise ValueError("budget_exceeded: create an immutable dataset before merging large sources")
                _register(self._conn, name, frame)
            self._registered.add(name)

    def get_capabilities(self) -> dict:
        return {"batch_reader": True, "large": True, "immutable": False,
                "query_dialect": "duckdb", "bounded_result": True}

    # ── DataSource interface ──────────────────────────────────────────────────

    def get_schema(self) -> str:
        """返回合并连接中所有带前缀表的 schema，按数据源分组展示。"""
        parts: List[str] = []

        # 按数据源分组
        src_sections: Dict[int, List[str]] = {i: [] for i in range(1, len(self._sources) + 1)}
        for prefixed, (idx, _) in self._prefix_map.items():
            src_sections[idx].append(prefixed)
        # 分析表单独一组
        analysis_section: List[str] = list(self._analysis_tables)

        for idx, src in enumerate(self._sources, start=1):
            table_parts: List[str] = []
            for prefixed in sorted(src_sections.get(idx, [])):
                try:
                    _index, bare = self._prefix_map[prefixed]
                    metadata = src.get_table_schema(bare)
                    columns = metadata.get("columns") or []
                    table_parts.append(f"Table: {prefixed}\n  " + ", ".join(str(column) for column in columns))
                except Exception:
                    table_parts.append(f"Table: {prefixed}  (metadata unavailable)")
            if table_parts:
                header = (
                    f"=== 数据源 {idx}: {getattr(src, 'name', '未命名')} ===\n"
                    f"  [NOTE: use prefix src{idx}__ for all table names, "
                    f"e.g. SELECT * FROM \"src{idx}__<table_name>\"]"
                )
                parts.append(header + "\n" + "\n\n".join(table_parts))

        for tbl in analysis_section:
            try:
                rows = self._conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
                parts.append(_table_schema_str(self._conn, tbl, rows))
            except Exception:
                pass

        return "\n\n".join(parts) if parts else "No tables in merged source."

    def execute_query(self, sql: str) -> Tuple[pd.DataFrame, str]:
        """在合并连接上执行 SQL。SQL 中的表名应使用 src{N}__ 前缀。"""
        try:
            import pyarrow as pa
            batches, count, size = [], 0, 0
            for batch in self.execute_query_batches(sql):
                count += batch.num_rows
                size += batch.nbytes
                if count > 100_000 or size > 64 * 1024 * 1024:
                    return pd.DataFrame(), "budget_exceeded: aggregate or materialize the merged result"
                batches.append(batch)
            if batches:
                return pa.Table.from_batches(batches).to_pandas(), ""
            return pd.DataFrame(), ""
        except Exception as exc:
            return pd.DataFrame(), str(exc)

    def execute_query_batches(self, sql: str, *, budget=None, cancel=None):
        with self._lock:
            self._prepare_query(sql)
            reader = self._conn.execute(sql).fetch_record_batch(rows_per_batch=4096)
            try:
                for batch in reader:
                    if cancel is not None and cancel():
                        self._conn.interrupt()
                        raise RuntimeError("Query canceled")
                    yield batch
            finally:
                reader.close()

    def create_analysis_table(self, sql: str, table_name: str = "analysis_data",
                              _df=None) -> str:
        """在合并连接中创建派生/分析表，供后续查询使用。"""
        with self._lock:
            if _df is not None:
                _register(self._conn, table_name, _df)
                rows = len(_df)
            else:
                try:
                    self._prepare_query(sql)
                    self._conn.execute(
                        f'CREATE OR REPLACE TABLE "{table_name}" AS ({sql})'
                    )
                    rows = self._conn.execute(
                        f'SELECT COUNT(*) FROM "{table_name}"'
                    ).fetchone()[0]
                except Exception as exc:
                    return f"Error building analysis table: {exc}"

            if table_name not in self._analysis_tables:
                self._analysis_tables.append(table_name)
            return _table_schema_str(self._conn, table_name, rows)

    def list_tables(self) -> List[str]:
        """返回合并连接中所有可查询的表名（含前缀 + 分析表）。"""
        return list(self._prefix_map.keys()) + list(self._analysis_tables)

    def get_preview(self) -> List[dict]:
        result = []
        for prefixed, (index, bare) in sorted(self._prefix_map.items()):
            try:
                metadata = self._sources[index - 1].get_table_schema(bare)
                result.append({"name": prefixed, "columns": metadata.get("columns") or [], "total_rows": None})
            except Exception:
                result.append({"name": prefixed, "columns": [], "total_rows": None})
        return result

    def get_preview_table(self, table_name: str, max_rows: int = 100) -> dict:
        from .sources._utils import _preview_table_dict
        if table_name not in self._prefix_map and table_name not in self._analysis_tables:
            return {"name": table_name, "columns": [], "rows": [], "error": "Table is unavailable"}
        quoted = '"' + table_name.replace('"', '""') + '"'
        with self._lock:
            self._prepare_query(f"SELECT * FROM {quoted} LIMIT 0")
            return _preview_table_dict(self._conn, table_name, table_name, min(100, max(1, max_rows)))

    # ── 辅助方法 ──────────────────────────────────────────────────────────────

    def has_table(self, prefixed_name: str) -> bool:
        """判断合并连接中是否存在某个带前缀的表。"""
        return prefixed_name in self._prefix_map or prefixed_name in self._analysis_tables

    def src_count(self) -> int:
        return len(self._sources)

    def invalidate(self) -> None:
        """关闭合并连接（数据源变化时调用），下次使用时由 session 重建。"""
        try:
            self._conn.close()
        finally:
            self._spill_directory.cleanup()
        log.info("[MergedDS] invalidated")

    close = invalidate
