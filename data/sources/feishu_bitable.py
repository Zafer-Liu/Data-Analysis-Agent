#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""In-memory DataSource backed by records explicitly read from Feishu Bitable."""
from __future__ import annotations

from typing import List, Tuple

import pandas as pd

from ._utils import (
    _clean_identifier, _dedup_columns, _list_tables, _new_conn,
    _preview_table_dict, _query, _register, _table_schema_str,
)
from .base import DataSource


class FeishuBitableDataSource(DataSource):
    """Analyze a bounded Bitable snapshot through the normal DuckDB toolchain.

    The source intentionally contains only records returned by the app bot for
    the current load operation.  It does not keep or expose application
    credentials, and writing back to Feishu is handled by dedicated tools.
    """

    def __init__(self, records: list[dict[str, object]], name: str, table_name: str = "数据表") -> None:
        self.name = str(name or "飞书多维表格")
        self._conn = _new_conn()
        self._table = _clean_identifier(table_name) or "bitable"
        frame = pd.DataFrame(records)
        if frame.empty and not list(frame.columns):
            frame = pd.DataFrame({"_feishu_record_id": pd.Series(dtype="string")})
        # Bitable text fields are intentionally the safe creation default, but
        # test/business tables often store measures as numeric strings. Promote
        # only columns whose non-empty values are all numeric so the existing
        # SQL analysis and chart tools receive real numeric columns.
        for column in frame.columns:
            if str(column) == "_feishu_record_id" or pd.api.types.is_numeric_dtype(frame[column]):
                continue
            raw = frame[column].dropna().astype(str).str.strip()
            non_empty = raw[raw.ne("")]
            if non_empty.empty:
                continue
            numeric = pd.to_numeric(non_empty, errors="coerce")
            if numeric.notna().all():
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame.columns = _dedup_columns([_clean_identifier(str(column)) for column in frame.columns])
        _register(self._conn, self._table, frame)

    def get_schema(self) -> str:
        parts: List[str] = []
        for table in self.list_tables() or [self._table]:
            rows = self._conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            parts.append(_table_schema_str(self._conn, table, rows))
        return "\n\n".join(parts)

    def list_tables(self) -> List[str]:
        return _list_tables(self._conn)

    def execute_query(self, sql: str) -> Tuple[pd.DataFrame, str]:
        return _query(self._conn, sql)

    def create_analysis_table(self, sql: str, table_name: str = "analysis_data", _df=None) -> str:
        if _df is not None:
            _register(self._conn, table_name, _df)
            rows = len(_df)
        else:
            try:
                self._conn.execute(f'CREATE OR REPLACE TABLE "{table_name}" AS ({sql})')
                rows = self._conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            except Exception as exc:
                return f"Error building analysis table: {exc}"
        return _table_schema_str(self._conn, table_name, rows)

    def get_preview(self) -> List[dict]:
        try:
            cols = [row[0] for row in self._conn.execute(f'DESCRIBE "{self._table}"').fetchall()]
            total = self._conn.execute(f'SELECT COUNT(*) FROM "{self._table}"').fetchone()[0]
            return [{"name": self._table, "columns": cols, "total_rows": total}]
        except Exception:
            return []

    def get_preview_table(self, table_name: str, max_rows: int = 100) -> dict:
        return _preview_table_dict(self._conn, table_name, table_name, max_rows)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
