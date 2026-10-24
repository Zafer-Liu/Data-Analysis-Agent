import logging
log = logging.getLogger(__name__)
import os
import re
import pandas as pd
from typing import List


def _safe_sheet_name(name: str, used: set[str]) -> str:
    """Build an openpyxl-compliant unique sheet name (≤31 chars, no banned chars,
    not starting with `'`, not a reserved name). Uniquify against *used* by
    appending ` _2`, ` _3` … while staying within the 31-char limit.
    """
    raw = re.sub(r'[\/*?:\[\]\\]', '_', str(name or "Sheet"))
    if raw.startswith("'"):
        raw = "_" + raw[1:]
    base = raw[:31] or "Sheet"
    if base.lower() in {"history"}:
        base = "_" + base
        base = base[:31]
    candidate = base
    suffix_n = 2
    while candidate in used:
        suffix = f" _{suffix_n}"
        candidate = (base[: max(0, 31 - len(suffix))]) + suffix
        suffix_n += 1
    used.add(candidate)
    return candidate


def prefetch_excel_frames(datasource, tables: List[str]) -> List[tuple]:
    """Query each table on the *request thread* and return ``(table, df)``
    pairs for the non-empty ones. The DataSource connection must not cross
    a thread boundary; callers hand these snapshots to a worker for writing.
    """
    frames: List[tuple] = []
    for table in tables:
        try:
            df, err = datasource.execute_query(f'SELECT * FROM "{table}"')
            if err or df is None or df.empty:
                continue
            frames.append((table, df))
        except Exception as e:
            log.warning("[excel_export] 预取表 '%s' 失败: %s", table, e)
            continue
    return frames


def write_excel_from_frames(frames: List[tuple], filepath: str) -> tuple:
    """Write prefetched ``(table, df)`` snapshots to an Excel file at *filepath*.

    Thread-safe: takes no DataSource, only DataFrame snapshots.

    Returns
    -------
    (filepath, skipped_tables)
        filepath        : absolute path of the written file.
        skipped_tables  : list of (table_name, error_str) for frames that
                          failed to write. Empty when everything succeeded.
    Raises ValueError if nothing could be written at all.
    """
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

    used_sheet_names: set[str] = set()
    written = 0
    skipped: list = []
    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        for table, df in frames:
            try:
                sheet_name = _safe_sheet_name(table, used_sheet_names)
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                written += 1
            except Exception as e:
                log.warning("[excel_export] 写入表 '%s' 失败: %s", table, e)
                skipped.append((table, str(e)))
                continue

    if written == 0:
        raise ValueError("没有可导出的表格数据，请确认表名正确且数据不为空。")

    return filepath, skipped


def export_to_excel(datasource, tables: List[str], filepath: str) -> tuple:
    """
    Query *tables* from *datasource* and write each as a separate sheet
    in an Excel file at *filepath*.

    Returns
    -------
    (filepath, skipped_tables)
        See ``write_excel_from_frames`` for the return contract.
    Raises ValueError if no table could be exported.
    """
    if not tables:
        raise ValueError("没有可导出的表，请确认表名正确且数据不为空。")

    frames = prefetch_excel_frames(datasource, tables)
    return write_excel_from_frames(frames, filepath)
