#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DataSource abstract base class.

All concrete sources implement this contract. Frontend code (api/, agent/)
never imports a concrete class directly — it always programs to this base.
"""
from typing import List, Tuple

import pandas as pd

MAX_DISPLAY_ROWS = 200   # max rows shown to the LLM in query results


class DataSource:
    name: str = ""

    def writable_conn(self):
        """The connection that write SQL (A6) should execute against.

        Memory-backed sources (``:memory:`` DuckDB) return ``self._conn``
        directly — writes are naturally ephemeral. File-backed sources
        override this to lazily build an in-memory snapshot (副 sheet) so
        the on-disk database is never touched by agent SQL.
        """
        return getattr(self, "_conn", None)

    def list_tables(self) -> List[str]:
        """Return ALL table names currently in the data source, including
        analysis/derived tables created at runtime via create_analysis_table.
        Subclasses backed by DuckDB should query information_schema."""
        raise NotImplementedError

    def execute_query(self, sql: str) -> Tuple[pd.DataFrame, str]:
        """Returns (dataframe, error_string). error_string is empty on success."""
        raise NotImplementedError

    def get_capabilities(self) -> dict:
        """Declare execution behavior; callers must not infer it from names."""
        return {"batch_reader": False, "immutable": False, "large": False,
                "requires_table_selection": False, "query_dialect": "duckdb"}

    def execute_query_batches(self, sql: str, *, budget=None, cancel=None):
        """Compatibility fallback for small sources, not a bounded reader claim."""
        if cancel is not None and cancel():
            raise RuntimeError("Query canceled")
        frame, error = self.execute_query(sql)
        if error:
            raise ValueError(error)
        import pyarrow as pa
        batch_rows = 4096
        for start in range(0, len(frame), batch_rows):
            if cancel is not None and cancel():
                raise RuntimeError("Query canceled")
            yield pa.RecordBatch.from_pandas(frame.iloc[start:start + batch_rows], preserve_index=False)

    def get_preview(self) -> List[dict]:
        """Return table metadata list (name / columns / total_rows). No row data."""
        return []

    def get_table_schema(self, table_name: str) -> dict:
        """Return metadata only; callers must not infer columns from sample rows."""
        for table in self.get_preview():
            if str(table.get("name") or "") == table_name:
                columns = [
                    column.get("name") if isinstance(column, dict) else str(column)
                    for column in table.get("columns", [])
                ]
                columns = [column for column in columns if column]
                return {"name": table_name, "columns": columns, "schema_available": bool(columns)}
        return {"name": table_name, "columns": [], "schema_available": False}

    def get_preview_table(self, table_name: str, max_rows: int = 100) -> dict:
        """Return row data for a single table. Called on demand by the frontend."""
        return {"name": table_name, "columns": [], "rows": [], "total_rows": 0}

    def create_analysis_table(
        self, sql: str, table_name: str = "analysis_data", _df=None
    ) -> str:
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
