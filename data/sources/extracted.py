#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""In-memory multi-table DataSource built from extracted web/PDF/JSON grids."""
from __future__ import annotations

from typing import List, Tuple

import pandas as pd

from ._utils import (
    _clean_identifier, _dedup_columns, _list_tables, _new_conn,
    _preview_table_dict, _query, _register, _table_schema_str,
)
from .base import DataSource


class ExtractedTableDataSource(DataSource):
    """Analyze tables extracted from a web page / PDF / JSON payload through
    the normal DuckDB toolchain.

    The source is a bounded snapshot taken at extraction time.  Refreshing
    the data means re-running the extraction tool; nothing here talks to
    the network.
    """

    def __init__(self, name: str, tables: dict[str, pd.DataFrame]) -> None:
        self.name = str(name or "网页表格").strip() or "网页表格"
        self._conn = _new_conn()
        self._tables: List[str] = []
        for raw_name, frame in tables.items():
            table = _clean_identifier(raw_name) or f"table_{len(self._tables) + 1}"
            base, attempt = table, 1
            while table in self._tables:
                attempt += 1
                table = f"{base}_{attempt}"
            frame = frame.copy()
            frame.columns = _dedup_columns(
                [_clean_identifier(str(column)) for column in frame.columns]
            )
            _register(self._conn, table, frame)
            self._tables.append(table)

    def get_schema(self) -> str:
        parts: List[str] = []
        for table in self.list_tables():
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
                rows = self._conn.execute(
                    f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            except Exception as exc:
                return f"Error building analysis table: {exc}"
        return _table_schema_str(self._conn, table_name, rows)

    def get_preview(self) -> List[dict]:
        previews: List[dict] = []
        for table in self.list_tables():
            try:
                cols = [row[0] for row in self._conn.execute(
                    f'DESCRIBE "{table}"').fetchall()]
                total = self._conn.execute(
                    f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                previews.append({"name": table, "columns": cols, "total_rows": total})
            except Exception:
                continue
        return previews

    def get_preview_table(self, table_name: str, max_rows: int = 100) -> dict:
        return _preview_table_dict(self._conn, table_name, table_name, max_rows)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
