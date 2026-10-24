#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn unstructured sources (HTML / JSON / CSV / PDF) into clean table grids.

Pure extraction and normalization, no agent imports.  The agent layer owns
URL safety (SSRF validation); this module only guards scheme, size and
content caps.  Every parse function returns a list of "grid" dicts
``{"name": str, "rows": list[list]}`` where the first row may or may not
be a header — :func:`normalize_grids` decides via header detection.
"""
from __future__ import annotations

import io
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .sources._utils import _clean_identifier, _dedup_columns, _detect_header_row


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


MAX_TABLES_PER_SOURCE = _env_int("BAA_INGEST_MAX_TABLES", 10)
MAX_ROWS_PER_TABLE = _env_int("BAA_INGEST_MAX_ROWS", 10_000)
MAX_TOTAL_CELLS = 500_000
MAX_FETCH_BYTES = _env_int("BAA_INGEST_MAX_BYTES", 10 * 1024 * 1024)
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = _env_int("BAA_INGEST_MAX_PDF_PAGES", 30)

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,text/csv,application/pdf,*/*",
}


class TableExtractError(Exception):
    """Extraction failure with a stable error code (FETCH-xxx / PDF-xxx)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


GridTable = Dict[str, Any]  # {"name": str, "rows": list[list[Any]]}


# ── cell / row helpers ───────────────────────────────────────────────────────


def _cell(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def _filter_junk(rows: List[List[Any]]) -> Optional[List[List[Any]]]:
    """Drop all-empty rows; reject grids with fewer than 2 rows or 2 columns."""
    cleaned = [
        row for row in rows
        if any(c is not None and str(c).strip() for c in row)
    ]
    if len(cleaned) < 2:
        return None
    if max(len(row) for row in cleaned) < 2:
        return None
    return cleaned


def _df_to_grid(frame: pd.DataFrame, name: str, include_header: bool = True) -> GridTable:
    rows: List[List[Any]] = []
    if include_header:
        rows.append([_cell(c) for c in frame.columns])
    for record in frame.to_dict(orient="records"):
        rows.append([_cell(record[column]) for column in frame.columns])
    return {"name": name, "rows": rows}


# ── parse functions (all return List[GridTable]) ────────────────────────────


def parse_html_tables(html: str) -> List[GridTable]:
    try:
        frames = pd.read_html(io.StringIO(html), header=None, flavor="lxml")
    except ValueError as exc:
        raise TableExtractError("FETCH-001", f"页面中未检测到 HTML 表格: {exc}") from exc
    except Exception as exc:  # lxml parse failures on malformed markup
        raise TableExtractError("FETCH-001", f"HTML 解析失败: {exc}") from exc
    grids: List[GridTable] = []
    for index, frame in enumerate(frames, 1):
        # read_html promotes <th> cells to DataFrame columns even with
        # header=None. Keep those labels in the grid or normalization will
        # incorrectly consume the first data record as the header.
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = [
                " / ".join(dict.fromkeys(str(part) for part in column))
                for column in frame.columns
            ]
        grid = _df_to_grid(
            frame, f"t{index}", include_header=not isinstance(frame.columns, pd.RangeIndex)
        )
        if _filter_junk(grid["rows"]) is None:
            continue
        grids.append(grid)
    if not grids:
        raise TableExtractError("FETCH-001", "页面中未检测到可用表格（至少 2 行 2 列）")
    return grids


def parse_json_tables(text: str) -> List[GridTable]:
    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise TableExtractError("FETCH-001", f"内容不是有效 JSON: {exc}") from exc
    grids: List[GridTable] = []
    if isinstance(payload, list):
        if not payload:
            raise TableExtractError("FETCH-001", "JSON 为空数组，无可导入数据")
        grids.append(_df_to_grid(pd.json_normalize(payload), "data"))
    elif isinstance(payload, dict):
        found_nested = False
        for key, value in payload.items():
            if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
                grids.append(_df_to_grid(pd.json_normalize(value), str(key)))
                found_nested = True
        if not found_nested:
            grids.append(_df_to_grid(pd.json_normalize(payload), "data"))
    else:
        raise TableExtractError("FETCH-001", "JSON 根节点不是对象或数组，无法转为表格")
    kept = [grid for grid in grids if _filter_junk(grid["rows"]) is not None]
    if not kept:
        raise TableExtractError("FETCH-001", "JSON 中未发现可用表格（至少 2 行 2 列）")
    return kept


def parse_csv_table(text: str) -> List[GridTable]:
    try:
        frame = pd.read_csv(io.StringIO(text))
    except Exception as exc:
        raise TableExtractError("FETCH-001", f"CSV 解析失败: {exc}") from exc
    if frame.empty:
        raise TableExtractError("FETCH-001", "CSV 内容为空")
    grid = _df_to_grid(frame, "data")
    if _filter_junk(grid["rows"]) is None:
        raise TableExtractError("FETCH-001", "CSV 少于 2 行或 2 列，无可导入内容")
    return [grid]


def parse_pdf_tables(data: bytes) -> List[GridTable]:
    """Extract tables from a text-based PDF.  Returns [] when no tables are
    found (that is a content outcome, not an error); raises PDF-00x codes
    for dependency / corruption / page-cap problems."""
    try:
        import pdfplumber
    except ImportError as exc:
        raise TableExtractError(
            "PDF-001",
            "PDF 解析依赖 pdfplumber 未安装；请执行 pip install pdfplumber 后重试",
        ) from exc
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = pdf.pages
            if len(pages) > MAX_PDF_PAGES:
                raise TableExtractError(
                    "PDF-003",
                    f"PDF 页数 {len(pages)} 超过上限 {MAX_PDF_PAGES}，请拆分后重试",
                )
            grids: List[GridTable] = []
            for page_index, page in enumerate(pages, 1):
                try:
                    tables = page.extract_tables() or []
                except Exception:
                    continue
                for table_index, rows in enumerate(tables, 1):
                    cleaned = [[_cell(c) for c in row] for row in rows if row]
                    if _filter_junk(cleaned) is None:
                        continue
                    grids.append({
                        "name": f"page{page_index}_table{table_index}",
                        "rows": cleaned,
                    })
            return grids
    except TableExtractError:
        raise
    except Exception as exc:
        raise TableExtractError("PDF-002", f"PDF 无法解析（可能已加密或损坏）: {exc}") from exc


def fetch_url_tables(url: str, timeout: int = 30) -> List[GridTable]:
    """Fetch an http/https URL and parse its body into table grids.

    Only scheme and size are guarded here; the agent layer performs full
    SSRF validation before calling this function.
    """
    import requests

    scheme = str(url or "").split(":", 1)[0].lower()
    if scheme not in ("http", "https"):
        raise TableExtractError("FETCH-001", "仅支持 http/https 链接")
    timeout = max(5, min(int(timeout or 30), 60))
    try:
        response = requests.get(
            url, headers=_FETCH_HEADERS, timeout=timeout, stream=True,
        )
    except requests.RequestException as exc:
        raise TableExtractError("FETCH-001", f"请求失败: {exc}") from exc
    with response:
        if response.status_code >= 400:
            raise TableExtractError(
                "FETCH-001", f"目标返回 HTTP {response.status_code}")
        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > MAX_FETCH_BYTES:
            raise TableExtractError(
                "FETCH-002", f"内容超过 {MAX_FETCH_BYTES} 字节上限")
        chunks: List[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_FETCH_BYTES:
                raise TableExtractError(
                    "FETCH-002", f"内容超过 {MAX_FETCH_BYTES} 字节上限")
            chunks.append(chunk)
        raw = b"".join(chunks)
        content_type = response.headers.get("Content-Type", "")
    return _parse_body(raw, url, content_type)


def _parse_body(raw: bytes, url: str, content_type: str) -> List[GridTable]:
    ct = (content_type or "").lower()
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    if raw[:1024].lstrip().startswith(b"%PDF-") or "pdf" in ct or path.endswith(".pdf"):
        return parse_pdf_tables(raw)
    text = raw.decode("utf-8", errors="replace")
    stripped = text.lstrip()
    if "json" in ct or path.endswith(".json") or stripped[:1] in ("{", "["):
        return parse_json_tables(text)
    if "csv" in ct or path.endswith(".csv"):
        return parse_csv_table(text)
    if ("html" in ct or path.endswith((".html", ".htm"))
            or b"<table" in raw[:20000].lower()):
        return parse_html_tables(text)
    try:
        return parse_html_tables(text)
    except TableExtractError:
        raise TableExtractError(
            "FETCH-001",
            "无法从该链接识别出表格（内容既不是 HTML/JSON/CSV/PDF）",
        ) from None


# ── normalization pipeline (shared by every entry point) ─────────────────────


def _promote_numeric(frame: pd.DataFrame) -> None:
    # Business pages often store measures as numeric strings ("1,234").
    # Promote only columns whose non-empty values are all numeric so the
    # SQL and chart tools receive real numeric columns.
    for column in frame.columns:
        if pd.api.types.is_numeric_dtype(frame[column]):
            continue
        raw = frame[column].dropna().astype(str).str.strip()
        non_empty = raw[raw.ne("")]
        if non_empty.empty:
            continue
        cleaned = non_empty.str.replace(",", "", regex=False)
        if pd.to_numeric(cleaned, errors="coerce").notna().all():
            frame[column] = pd.to_numeric(
                frame[column].astype(str).str.replace(",", "", regex=False),
                errors="coerce",
            )


def normalize_grids(
    grids: List[GridTable],
) -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    """Turn raw grids into clean DataFrames plus a metadata summary.

    Pipeline: junk filter -> table cap -> header detection/promotion ->
    ragged-row padding -> identifier cleaning/dedup -> numeric promotion ->
    row cap -> total-cell cap -> table-name dedup.
    """
    usable: List[GridTable] = []
    dropped = 0
    for grid in grids:
        rows = _filter_junk(grid.get("rows") or [])
        if rows is None:
            dropped += 1
        else:
            usable.append({"name": str(grid.get("name") or ""), "rows": rows})
    kept = usable[:MAX_TABLES_PER_SOURCE]
    dropped += len(usable) - len(kept)

    tables: Dict[str, pd.DataFrame] = {}
    summaries: List[Dict[str, Any]] = []
    total_cells = 0
    for grid in kept:
        rows = grid["rows"]
        width = max(len(row) for row in rows)
        padded = [list(row) + [None] * (width - len(row)) for row in rows]
        header_index = _detect_header_row(padded)
        header = padded[header_index]
        body = padded[header_index + 1:]
        if not body:
            dropped += 1
            continue
        labels: List[str] = []
        for cell_value in header:
            text = "" if cell_value is None else str(cell_value).strip()
            labels.append(_clean_identifier(text) if text else "")
        labels = [label or f"col_{i + 1}" for i, label in enumerate(labels)]
        columns = _dedup_columns(labels)
        frame = pd.DataFrame(body, columns=columns)
        frame = frame.dropna(how="all")
        if frame.empty:
            dropped += 1
            continue
        truncated = False
        if len(frame) > MAX_ROWS_PER_TABLE:
            frame = frame.iloc[:MAX_ROWS_PER_TABLE].copy()
            truncated = True
        total_cells += int(frame.size)
        if total_cells > MAX_TOTAL_CELLS:
            total_cells -= int(frame.size)
            dropped += 1
            continue
        _promote_numeric(frame)
        base = _clean_identifier(grid["name"]) or f"table_{len(tables) + 1}"
        final, attempt = base, 1
        while final in tables:
            attempt += 1
            final = f"{base}_{attempt}"
        tables[final] = frame
        summaries.append({
            "name": final,
            "rows": int(len(frame)),
            "cols": int(len(frame.columns)),
            "truncated": truncated,
        })
    return tables, {"kept": len(tables), "dropped": dropped, "tables": summaries}
