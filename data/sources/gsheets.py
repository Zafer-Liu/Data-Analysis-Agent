#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GoogleSheetsDataSource — service-account auth → all worksheets in parallel."""
import logging
from typing import List, Optional, Tuple

import pandas as pd

from ._utils import (
    _clean_identifier, _dedup_columns, _detect_header_row,
    _list_tables, _new_conn, _preview_table_dict, _query, _register,
    _table_schema_str,
)
from .base import DataSource

log = logging.getLogger(__name__)


class GoogleSheetsDataSource(DataSource):
    """Load worksheets from a Google Spreadsheet via a service-account JSON dict."""

    _CONNECT_TIMEOUT  = 20   # seconds for OAuth token + spreadsheet open
    _FETCH_TIMEOUT    = 120  # seconds per individual sheet fetch (large sheets can take 60-90s)
    _TOTAL_TIMEOUT    = 300  # seconds total budget for fetching all sheets
    _MAX_WORKERS      = 6    # concurrent fetch threads (reduced to avoid rate-limiting)

    def __init__(self, creds_dict: dict, spreadsheet_url_or_id: str, display_name: str = ""):
        import gspread
        from google.oauth2.service_account import Credentials

        self._creds_dict = creds_dict
        self._spreadsheet_ref = spreadsheet_url_or_id

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        log.info("[GSheets] building credentials …")
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)

        log.info("[GSheets] authorizing …")
        gc = gspread.authorize(creds)
        gc.set_timeout(self._CONNECT_TIMEOUT)

        log.info("[GSheets] opening spreadsheet …")
        if spreadsheet_url_or_id.startswith("http"):
            spreadsheet = gc.open_by_url(spreadsheet_url_or_id)
        else:
            spreadsheet = gc.open_by_key(spreadsheet_url_or_id)

        self.name = display_name or spreadsheet.title
        log.info("[GSheets] opened %r", self.name)
        self._conn = _new_conn()
        self._tables: List[str] = []
        self._load(spreadsheet)

    @staticmethod
    def _fetch_sheet_once(ws) -> Optional[pd.DataFrame]:
        """Fetch one worksheet as a DataFrame. Returns None if empty or failed."""
        rows = ws.get_all_values()
        if len(rows) < 2:
            return None
        header_idx = _detect_header_row(rows)
        header = rows[header_idx]
        data = rows[header_idx + 1:]
        if not data:
            return None
        log.info("[GSheets] sheet %r: header at row %d", ws.title, header_idx)
        df = pd.DataFrame(data, columns=header)
        df.columns = _dedup_columns([_clean_identifier(c) for c in df.columns])
        df.replace("", pd.NA, inplace=True)
        df = df.dropna(how="all")
        if df.empty or len(df.columns) == 0:
            return None
        log.info("[GSheets] sheet %r → %d rows", ws.title, len(df))
        return df

    @staticmethod
    def _fetch_sheet(ws) -> Optional[pd.DataFrame]:
        """Fetch with one automatic retry on transient network errors."""
        last_exc = None
        for attempt in range(2):
            try:
                return GoogleSheetsDataSource._fetch_sheet_once(ws)
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    import time as _time
                    log.warning(
                        "[GSheets] sheet %r fetch failed (attempt 1), retrying: %s",
                        ws.title, exc,
                    )
                    _time.sleep(2)   # brief back-off before retry
        log.warning("[GSheets] sheet %r fetch failed after 2 attempts: %s", ws.title, last_exc)
        return None

    def _load(self, spreadsheet):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import time as _time

        worksheets = spreadsheet.worksheets()
        log.info("[GSheets] fetching %d sheet(s) concurrently …", len(worksheets))

        sheet_dfs = {}   # ws.title → DataFrame
        n_workers = min(self._MAX_WORKERS, len(worksheets))

        deadline = _time.monotonic() + self._TOTAL_TIMEOUT

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            future_to_ws = {pool.submit(self._fetch_sheet, ws): ws for ws in worksheets}

            # Collect results one by one; give each future its own per-sheet timeout
            # so a single slow sheet never blocks the rest.
            for future in as_completed(future_to_ws):
                ws = future_to_ws[future]
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    log.warning(
                        "[GSheets] total timeout reached — %d sheet(s) still pending, skipping",
                        sum(1 for f in future_to_ws if not f.done()),
                    )
                    break
                try:
                    df = future.result(timeout=min(self._FETCH_TIMEOUT, max(remaining, 1)))
                except TimeoutError:
                    log.warning("[GSheets] sheet %r timed out, skipping", ws.title)
                    df = None
                except Exception as exc:
                    log.warning("[GSheets] sheet %r error: %s", ws.title, exc)
                    df = None
                if df is not None:
                    sheet_dfs[ws.title] = df

            # Cancel futures that are still pending after timeout or break
            for f in future_to_ws:
                if not f.done():
                    f.cancel()

        skipped = [ws.title for ws in worksheets if ws.title not in sheet_dfs]
        if skipped:
            log.warning("[GSheets] %d sheet(s) skipped: %s", len(skipped), skipped)

        # Register in original worksheet order (preserve user's sheet ordering)
        for ws in worksheets:
            if ws.title not in sheet_dfs:
                continue
            df = sheet_dfs[ws.title]
            table = _clean_identifier(ws.title) or f"sheet{len(self._tables) + 1}"
            _register(self._conn, table, df)
            self._tables.append(table)

        log.info("[GSheets] loaded %d/%d sheet(s) into DuckDB", len(self._tables), len(worksheets))
        if not self._tables:
            raise ValueError("Google Spreadsheet 中未发现有效工作表。")

    def get_schema(self) -> str:
        n = len(self._tables)
        MAX_FULL = 20
        parts: List[str] = []

        if n > MAX_FULL:
            parts.append(
                f"[Spreadsheet schema — {n} sheets total. "
                f"Full column details shown for first {MAX_FULL} sheets. "
                f"Use get_table_detail(table_name) for any other sheet.]\n"
                f"All sheets: {', '.join(self._tables)}"
            )
            detail_tables = self._tables[:MAX_FULL]
        else:
            detail_tables = self._tables

        for table in detail_tables:
            try:
                rows = self._conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                cols = self._conn.execute(f'DESCRIBE "{table}"').fetchall()
                col_str = ", ".join(r[0] for r in cols)
                parts.append(f"Table: {table}  ({rows} rows)  [{col_str}]")
            except Exception:
                parts.append(f"Table: {table}  (unavailable)")

        # Always show analysis tables in full
        analysis = [t for t in _list_tables(self._conn) if t not in self._tables]
        for table in analysis:
            try:
                rows = self._conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                parts.append(_table_schema_str(self._conn, table, rows))
            except Exception:
                pass

        return "\n\n".join(parts) if parts else "No sheets found."

    def get_table_detail(self, table_name: str) -> str:
        """Return full column list + row count for a single sheet."""
        try:
            rows = self._conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            return _table_schema_str(self._conn, table_name, rows)
        except Exception as exc:
            return f"Sheet '{table_name}' — error: {exc}"

    def execute_query(self, sql: str) -> Tuple[pd.DataFrame, str]:
        return _query(self._conn, sql)

    def create_analysis_table(self, sql: str, table_name: str = "analysis_data", _df=None) -> str:
        if _df is not None:
            _register(self._conn, table_name, _df)
            rows = len(_df)
        else:
            try:
                self._conn.execute(
                    f'CREATE OR REPLACE TABLE "{table_name}" AS ({sql})'
                )
                rows = self._conn.execute(
                    f'SELECT COUNT(*) FROM "{table_name}"'
                ).fetchone()[0]
            except Exception as exc:
                return f"Error building analysis table: {exc}"
        if table_name not in self._tables:
            self._tables.append(table_name)
        return _table_schema_str(self._conn, table_name, rows)

    def list_tables(self) -> List[str]:
        return _list_tables(self._conn)

    def get_preview(self) -> List[dict]:
        tables = self._conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main' ORDER BY table_name"
        ).fetchall()
        result = []
        for (t,) in tables:
            try:
                cols = [r[0] for r in self._conn.execute(f'DESCRIBE "{t}"').fetchall()]
                total = self._conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                result.append({"name": t, "columns": cols, "total_rows": total})
            except Exception:
                continue
        return result

    def get_preview_table(self, table_name: str, max_rows: int = 100) -> dict:
        return _preview_table_dict(self._conn, table_name, table_name, max_rows)
