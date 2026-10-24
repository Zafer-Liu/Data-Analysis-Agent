#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Excel data source plus the B2 cancellable, per-sheet parse worker."""
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Tuple

import duckdb
import pandas as pd

from ._utils import (
    _clean_identifier, _dedup_columns, _detect_header_row,
    _list_tables, _new_conn, _preview_table_dict, _query, _register,
    _table_schema_str, is_write_sql, snapshot_tables_to_memory,
)
from .base import DataSource

log = logging.getLogger(__name__)

# Files below this size keep the existing low-latency synchronous path.
EXCEL_JOB_THRESHOLD_BYTES = int(os.environ.get("BAA_EXCEL_JOB_THRESHOLD", 5_000_000))


def excel_requires_job(path: str) -> bool:
    """Return whether an Excel upload should use the background parser."""
    try:
        return Path(path).stat().st_size > EXCEL_JOB_THRESHOLD_BYTES
    except OSError:
        return False


def _excel_engine(path: str) -> Tuple[str, List[str]]:
    """Choose the fastest installed engine and return workbook sheet names."""
    try:
        import python_calamine  # noqa: F401
        engine = "calamine"
    except ImportError:
        engine = "openpyxl"
        log.warning("[ExcelDS] python_calamine unavailable; using openpyxl")
    meta = pd.ExcelFile(path, engine=engine)
    try:
        return engine, list(meta.sheet_names)
    finally:
        meta.close()


def parse_excel_job(ctx, file_path: str, db_path: str, filename: str) -> dict:
    """Parse an Excel workbook into a dedicated persistent DuckDB database.

    This function is intentionally self-contained for ``JobRunner`` workers:
    it opens and closes its own connection, reports progress after each sheet,
    and observes cancellation only at safe sheet boundaries.
    """
    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    complete = False
    conn = None
    try:
        ctx.set_progress(2, f"正在读取 {filename} 的工作表")
        ctx.check_canceled()
        engine, sheet_names = _excel_engine(file_path)
        if not sheet_names:
            raise ValueError("Excel 文件中未发现工作表。")

        total = len(sheet_names)
        completed = 0

        def _sheet_done(sheet, done, _total):
            nonlocal completed
            completed = done
            ctx.set_progress(
                5 + int(done * 78 / total),
                f"已解析工作表 {done}/{total}：{sheet}",
            )

        parsed = _parse_sheets_parallel(
            file_path,
            sheet_names,
            engine,
            check_canceled=ctx.check_canceled,
            on_complete=_sheet_done,
        )
        ctx.check_canceled()

        conn = duckdb.connect(str(target))
        conn.execute("PRAGMA threads=4")
        tables: List[str] = []
        for index, (sheet, df) in enumerate(parsed, start=1):
            if df is not None:
                table = _clean_identifier(sheet) or f"sheet{index}"
                # A workbook may contain sheet names that normalize identically.
                base, suffix = table, 2
                while table in tables:
                    table = f"{base}_{suffix}"
                    suffix += 1
                _register(conn, table, df)
                tables.append(table)
            ctx.set_progress(
                83 + int(index * 14 / total),
                f"正在注册工作表 {index}/{total}：{sheet}",
            )

        if not tables:
            raise ValueError("Excel 文件中未发现有效工作表。")
        complete = True
        ctx.set_progress(100, f"{filename} 解析完成，共 {len(tables)} 个工作表")
        return {
            "file_path": str(Path(file_path).resolve()),
            "db_path": str(target.resolve()),
            "filename": filename,
            "tables": tables,
            "sheet_count": total,
        }
    finally:
        if conn is not None:
            conn.close()
        if not complete:
            for partial in (target, Path(str(target) + ".wal")):
                try:
                    partial.unlink(missing_ok=True)
                except OSError:
                    log.warning("[ExcelDS] failed to clean incomplete job artifact: %s", partial)


def _is_wide_pivoted(raw: pd.DataFrame) -> bool:
    """Detect if this sheet is a wide/pivoted table where:
    - The first column contains metric/row labels (text)
    - The remaining columns contain time-series or category values (mostly numeric)
    - Most column headers are Unnamed or blank (pandas default)

    Heuristic: if >60% of column headers are Unnamed/blank AND the first column
    has text values in most rows → this is a wide pivoted table that needs
    to be read with the first column as the index and then transposed.
    """
    if raw.shape[1] < 5:
        return False

    # Count blank/Unnamed column headers (from pandas default header=0 read)
    # Use the raw values since we read with header=None; check the row that
    # pandas *would* use as header (row 0) for blanks.
    top_row = [str(v).strip() for v in raw.iloc[0]]
    blank_cols = sum(1 for v in top_row if not v or v == 'nan')
    blank_ratio = blank_cols / len(top_row)

    # Check if first column has text labels in most rows (metric names)
    first_col = raw.iloc[:, 0].dropna().astype(str)
    text_in_first = sum(
        1 for v in first_col
        if v.strip() and not v.strip().replace('.', '').replace('-', '').isdigit()
    )
    text_ratio = text_in_first / max(len(first_col), 1)

    # Wide pivoted: mostly blank column headers AND first col is mostly text labels
    return blank_ratio > 0.6 and text_ratio > 0.5


def _pivot_to_long(raw: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Convert a wide pivoted table (metrics × dates) to long format (dates × metrics).

    Strategy:
    1. Find the row that contains the most date-like values → use as column headers
    2. Find rows where the first cell is a metric name → use as data rows
    3. Transpose: rows become columns (metric names), columns become rows (dates/periods)
    """
    import re

    DATE_PAT = re.compile(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}')

    n_rows, n_cols = raw.shape

    # Step 1: Find the best "header row" — the one with the most date-like values
    best_header_row = 0
    best_date_count = 0
    for i in range(min(n_rows, 10)):
        row_vals = [str(v) for v in raw.iloc[i]]
        date_count = sum(1 for v in row_vals if DATE_PAT.search(v))
        if date_count > best_date_count:
            best_date_count = date_count
            best_header_row = i

    # Step 2: Find data rows — rows where the first cell is a non-blank text label
    header_row_vals = [str(v).strip() for v in raw.iloc[best_header_row]]

    data_rows = []
    row_labels = []
    for i in range(n_rows):
        if i == best_header_row:
            continue
        first_cell = str(raw.iloc[i, 0]).strip()
        if not first_cell or first_cell == 'nan':
            continue
        # Must have some numeric data in the row (at least 30% of non-blank cells)
        row_vals = raw.iloc[i, 1:]
        numeric = sum(1 for v in row_vals
                      if str(v).strip() and str(v).strip() != 'nan'
                      and str(v).strip().replace('.', '').replace('-', '').replace('%', '').lstrip('-').isdigit())
        non_blank = sum(1 for v in row_vals if str(v).strip() and str(v).strip() != 'nan')
        if non_blank > 0 and numeric / non_blank > 0.3:
            data_rows.append(i)
            row_labels.append(first_cell)

    if not data_rows or best_date_count < 3:
        return None  # Not recognizable as a wide pivoted table

    # Step 3: Build the transposed DataFrame
    # Columns = metric names (from first column of data rows)
    # Rows = dates/periods (from the header row we found)
    col_headers = header_row_vals[1:]   # skip the first cell (it's the row-label column)
    data_matrix = []
    for i in data_rows:
        data_matrix.append([str(v).strip() if str(v).strip() != 'nan' else None
                            for v in raw.iloc[i, 1:]])

    # Transpose: data_matrix[metric][date] → df[date][metric]
    df = pd.DataFrame(data_matrix, index=row_labels, columns=col_headers).T
    df.index.name = 'period'
    df = df.reset_index()

    # Clean column names
    df.columns = _dedup_columns([_clean_identifier(str(c)) for c in df.columns])

    # Drop all-null rows and columns
    df = df.dropna(how='all').dropna(axis=1, how='all')

    # Keep only rows where the period column looks like a real date/period value
    # (filter out metadata rows like "City", "DeltaDo7D", etc.)
    period_col = df.columns[0]
    DATE_PAT2 = re.compile(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[Ww]\d{2}|\w+\s+\d{4}')
    mask = df[period_col].astype(str).str.strip().apply(
        lambda v: bool(DATE_PAT2.search(v)) or v.replace('.', '').replace('-', '').isdigit()
    )
    df = df[mask].reset_index(drop=True)

    log.info(
        "[ExcelDS] wide-pivoted sheet: %d metric cols × %d period rows (header at row %d)",
        len(data_rows), len(df), best_header_row,
    )
    return df if not df.empty else None


def _parse_sheet(path: str, sheet: str, engine: str) -> Tuple[str, Optional[pd.DataFrame]]:
    """Parse a single Excel sheet, auto-detecting the header row.

    Also handles wide/pivoted tables (metrics × dates) by transposing them
    into a tidy long format (dates × metrics) that SQL can query cleanly.
    """
    try:
        # Read without header first to detect structure
        raw = pd.read_excel(path, sheet_name=sheet, engine=engine, header=None)
        if raw.empty:
            return sheet, None

        # Detect wide pivoted format before normal header detection
        if _is_wide_pivoted(raw):
            df = _pivot_to_long(raw)
            if df is not None:
                log.info("[ExcelDS] sheet %r: transposed wide-pivoted format → %d rows", sheet, len(df))
                return sheet, df
            # Fall through to normal parsing if pivot detection failed

        header_idx = _detect_header_row(raw.values.tolist())
        # ``raw`` already contains every cell.  Re-reading the same sheet with
        # ``header=...`` roughly doubled large-workbook parsing time.  Build the
        # framed table from that first read instead.
        header_values = []
        for col_index, value in enumerate(raw.iloc[header_idx].tolist()):
            if pd.isna(value) or not str(value).strip():
                header_values.append(f"Unnamed_{col_index}")
            else:
                header_values.append(value)
        df = raw.iloc[header_idx + 1:].copy().reset_index(drop=True)
        df.columns = header_values
        df = df.infer_objects()
        log.info("[ExcelDS] sheet %r: header at row %d", sheet, header_idx)
        df.columns = _dedup_columns([_clean_identifier(c) for c in df.columns])
        df = df.dropna(how="all")
        if df.empty or len(df.columns) == 0:
            return sheet, None
        return sheet, df
    except Exception as exc:
        log.warning("[ExcelDS] sheet %r parse failed: %s", sheet, exc)
        return sheet, None


def _parse_sheets_parallel(
    path: str,
    sheet_names: List[str],
    engine: str,
    *,
    check_canceled=None,
    on_complete=None,
) -> List[Tuple[str, Optional[pd.DataFrame]]]:
    """Parse sheets with a bounded pool and return them in workbook order."""
    if not sheet_names:
        return []

    def _work(sheet):
        if check_canceled:
            check_canceled()
        result = _parse_sheet(path, sheet, engine)
        if check_canceled:
            check_canceled()
        return result

    results = {}
    # Excel sheet parsing is IO + CPU mixed; calamine (Rust) releases the GIL,
    # so real parallelism is possible. 4 → 8 capped by CPU count for safety.
    with ThreadPoolExecutor(max_workers=min(8, len(sheet_names))) as pool:
        futures = {pool.submit(_work, sheet): sheet for sheet in sheet_names}
        done = 0
        for future in as_completed(futures):
            sheet = futures[future]
            if check_canceled:
                check_canceled()
            parsed_sheet, df = future.result()
            results[parsed_sheet] = df
            done += 1
            if on_complete:
                on_complete(sheet, done, len(sheet_names))
    return [(sheet, results.get(sheet)) for sheet in sheet_names]


class ExcelDataSource(DataSource):
    """Load one or more sheets from an Excel file into a DuckDB in-memory DB."""

    def __init__(self, file_path: str, filename: str):
        self.name = filename
        self.file_path = file_path
        self._conn = _new_conn()
        self._tables: List[str] = []
        # A6: only from_database instances (job-parsed .duckdb files) are
        # file-backed and need the in-memory shadow for agent writes.
        self._shadow_conn = None
        self._load(file_path)

    @classmethod
    def from_database(cls, file_path: str, filename: str, db_path: str):
        """Attach a completed job database using a fresh request-thread connection."""
        obj = cls.__new__(cls)
        obj.name = filename
        obj.file_path = file_path
        obj._db_path = Path(db_path)
        obj._conn = duckdb.connect(str(obj._db_path))
        obj._shadow_conn = None
        obj._tables = _list_tables(obj._conn)
        if not obj._tables:
            obj._conn.close()
            raise ValueError("解析数据库中没有可用工作表。")
        return obj

    # ── A6 shadow (副 sheet) support — only for from_database instances ─────

    def _file_backed(self) -> bool:
        return hasattr(self, "_db_path")

    def writable_conn(self):
        if not self._file_backed():
            return self._conn  # already :memory:
        if self._shadow_conn is None:
            self._shadow_conn = snapshot_tables_to_memory(self._conn)
            log.info("[ExcelDS] shadow snapshot built  tables=%d", len(self._tables))
        return self._shadow_conn

    def _active_conn(self):
        return self._shadow_conn if self._shadow_conn is not None else self._conn

    def _load(self, path: str):
        engine, sheet_names = _excel_engine(path)
        log.info("[ExcelDS] engine=%s  sheets=%s", engine, sheet_names)

        # Parse in parallel, then register in original sheet order.
        for sheet, df in _parse_sheets_parallel(path, sheet_names, engine):
            if df is None:
                log.info("[ExcelDS] sheet %r skipped (no data)", sheet)
                continue
            table = _clean_identifier(sheet) or f"sheet{len(self._tables) + 1}"
            log.info("[ExcelDS] register → table=%r  rows=%d", table, len(df))
            _register(self._conn, table, df)
            self._tables.append(table)

        if not self._tables:
            raise ValueError("Excel 文件中未发现有效工作表。")

    def get_schema(self) -> str:
        parts: List[str] = []
        conn = self._active_conn()
        for table in self.list_tables():
            rows = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            parts.append(_table_schema_str(conn, table, rows))
        return "\n\n".join(parts) if parts else "No tables."

    def execute_query(self, sql: str) -> Tuple[pd.DataFrame, str]:
        # A6: writes on file-backed (from_database) instances go to the
        # in-memory shadow; :memory: instances write directly.
        if self._file_backed() and is_write_sql(sql):
            return _query(self.writable_conn(), sql)
        return _query(self._active_conn(), sql)

    def create_analysis_table(self, sql: str, table_name: str = "analysis_data", _df=None) -> str:
        use_shadow = self._file_backed() and (
            self._shadow_conn is not None or is_write_sql(sql or "") or _df is not None
        )
        conn = self.writable_conn() if use_shadow else self._conn
        if _df is not None:
            _register(conn, table_name, _df)
            rows = len(_df)
        else:
            try:
                conn.execute(
                    f'CREATE OR REPLACE TABLE "{table_name}" AS ({sql})'
                )
                rows = conn.execute(
                    f'SELECT COUNT(*) FROM "{table_name}"'
                ).fetchone()[0]
            except Exception as exc:
                return f"Error building analysis table: {exc}"
        # Track the new table so get_schema / list_tables include it.
        if table_name not in self._tables:
            self._tables.append(table_name)
        return _table_schema_str(conn, table_name, rows)

    def close(self) -> None:
        for conn in (getattr(self, "_conn", None), getattr(self, "_shadow_conn", None)):
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass

    def list_tables(self) -> List[str]:
        return _list_tables(self._active_conn())

    def get_preview(self) -> List[dict]:
        """Return table metadata only — fast even for 50+ sheet workbooks."""
        conn = self._active_conn()
        result = []
        for t in self.list_tables():
            try:
                cols = [r[0] for r in conn.execute(f'DESCRIBE "{t}"').fetchall()]
                total = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                result.append({"name": t, "columns": cols, "total_rows": total})
            except Exception:
                continue
        return result

    def get_preview_table(self, table_name: str, max_rows: int = 100) -> dict:
        return _preview_table_dict(self._active_conn(), table_name, table_name, max_rows)
