#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared DuckDB / DataFrame helpers used by every data source.

These were originally module-level helpers in `data.connector`. They are
intentionally underscore-prefixed and not re-exported — concrete sources
import them directly.
"""
import datetime
import logging
import re
from typing import List, Optional, Tuple

import duckdb
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ── Identifier / column helpers ──────────────────────────────────────────────

def _clean_identifier(raw: str) -> str:
    """Turn an arbitrary string into a safe DuckDB/SQL identifier."""
    if isinstance(raw, (tuple, list)):
        raw = "_".join(str(x) for x in raw)
    s = str(raw).strip()
    s = re.sub(r"[^\w]+", "_", s, flags=re.UNICODE)
    s = s.strip("_")
    if s and s[0].isdigit():
        s = "_" + s
    return s or "col"


def _dedup_columns(cols: List[str]) -> List[str]:
    """Append _2, _3 … to duplicate column names."""
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


def _detect_header_row(rows: list, scan: int = 10) -> int:
    """Return the index of the most likely header row within the first `scan` rows.

    Strategy (conservative-first):
    1. Row 0 is the default — most sheets have headers at the top.
    2. Only look further if row 0 is "obviously bad": all cells are empty,
       all cells are numeric, or more than half the cells are empty.
    3. Among candidate rows, prefer the first one that has the most non-empty,
       non-numeric cells AND whose non-empty cell count is notably better
       than row 0 (require at least 2× improvement to avoid false positives).

    This avoids mis-detecting data rows as headers when the actual header is
    row 0 but contains short strings or numbers mixed with text.
    """
    if not rows:
        return 0

    def _score(row):
        non_empty = [str(c).strip() for c in row if str(c).strip()]
        text_cells = [
            c for c in non_empty
            if not c.replace(".", "").replace("-", "").replace("%", "").replace(",", "").isdigit()
        ]
        return len(text_cells), len(non_empty)

    text0, nonempty0 = _score(rows[0])
    total_cols = max(len(rows[0]), 1)

    # Row 0 is clearly good → use it immediately
    if nonempty0 >= total_cols * 0.5 and text0 >= 2:
        return 0

    # Row 0 is mostly empty or all-numeric → scan for better header
    best_idx, best_text = 0, text0
    for i, row in enumerate(rows[1:scan], start=1):
        text_i, nonempty_i = _score(row)
        # Require a clear improvement over row 0 to switch
        if text_i > best_text and text_i >= 2:
            best_text = text_i
            best_idx = i

    return best_idx


# ── DuckDB connection / registration ─────────────────────────────────────────

def _new_conn() -> duckdb.DuckDBPyConnection:
    """Open a fresh DuckDB in-memory connection with sane thread settings.

    Security model (A4 起):
      - 不再禁用 LocalFileSystem 也不再禁用 enable_external_access —— 这两个
        设置会连带禁用 read_csv / read_parquet 等 file-read 函数，与"工作目录
        直读"目标冲突。DuckDB 的 enable_external_access 是全有或全无的，无法
        只禁网络而留本地。
      - 改由 `agent/validate.py` 的 SQL AST 路径白名单做精细控制：
          * 文件读取函数路径必须是字面量，resolve 后在工作目录/uploads/Information 白名单内
          * 网络 URL（http/https/s3/gs/azure/hdfs 等）一律拒绝（SSRF 防护）
          * 非字面量参数（@var / 列引用）一律拒绝
          * install/load/attach/copy_to/pragma/http_get/http_post 仍然禁止

    Layered defense (A4):
      Layer 1 (primary): agent/validate.py 的 sqlglot AST 校验
        - SELECT/WITH only，禁写操作
        - 文件读取函数路径白名单 + URL 黑名单
        - 非字面量路径参数一律拒绝
      Layer 2 (workspace): data/workspace.py 的 is_path_allowed()
        - Python 工具层文件读写鉴权（A5 接入）
      Layer 3 (engine, removed in A4): 原本依赖 enable_external_access=false
        做引擎级兜底，但该设置会禁用所有 file-read 函数，与工作目录直读冲突，
        A4 起移除，完全依赖 Layer 1 的 AST 校验。

    Risk: 如果 sqlglot 解析失败走 heuristic fallback，heuristic 会保守地拒绝
    所有 file-read 函数（无法做路径白名单），所以安全降级而非放行。
    """
    conn = duckdb.connect(":memory:")
    # Allow connections to be used from multiple threads (Flask worker threads)
    conn.execute("PRAGMA threads=4")

    # A4 起：不再 SET enable_external_access=false —— 它会禁用 read_csv 等
    # file-read 函数，与工作目录直读冲突。网络 SSRF 防护交给 validate.py 的
    # URL 黑名单（_BLOCKED_URL_SCHEMES）。

    return conn


def _sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    修复 DuckDB 无法自动 cast 的列类型，避免 "Failed to cast value: DOUBLE -> TIMESTAMP" 等错误。

    主要场景：
    1. pandas 把 Excel 数值型日期序列号（如 44927.0）读成 float64，
       但 DuckDB 试图将其 cast 为 TIMESTAMP 导致崩溃。
    2. object 列混合了 datetime / Timestamp 值，DuckDB 同样可能出错。
    3. pandas ExtensionArray 类型（Int64Dtype, StringDtype 等）偶发不兼容。
    """
    df = df.copy()
    for col in df.columns:
        s = df[col]
        dtype = s.dtype

        # ── 1. pandas nullable 整型 / 布尔 → 普通 numpy 类型 ──────────────
        if hasattr(dtype, "numpy_dtype"):
            try:
                df[col] = s.astype(dtype.numpy_dtype)
                s = df[col]
                dtype = s.dtype
            except Exception:
                df[col] = s.astype(object)
                continue

        # ── 2. object 列：处理含有 datetime / Timestamp 的混合类型列 ──────────
        if dtype == object:
            non_null = s.dropna()
            if len(non_null) == 0:
                continue
            has_dt = any(isinstance(v, (pd.Timestamp, datetime.datetime)) for v in non_null)
            if has_dt:
                all_dt = all(isinstance(v, (pd.Timestamp, datetime.datetime)) for v in non_null)
                if all_dt:
                    try:
                        df[col] = pd.to_datetime(s, errors="coerce")
                    except Exception:
                        df[col] = s.apply(lambda v: v.isoformat() if hasattr(v, 'isoformat') else (str(v) if pd.notna(v) else None))
                else:
                    def _to_str(v):
                        if v is None or (isinstance(v, float) and np.isnan(v)):
                            return None
                        if hasattr(v, 'strftime'):
                            return v.strftime('%Y-%m-%d')
                        return str(v)
                    df[col] = s.apply(_to_str)
            continue

        # ── 3. float64 列：若值全在 Excel 日期序号范围内，转 datetime ──────────
        if dtype == "float64":
            non_null = s.dropna()
            if len(non_null) == 0:
                continue
            looks_like_date = (
                non_null.between(1, 2958465).all()
                and (non_null == non_null.round()).all()
            )
            if looks_like_date:
                try:
                    df[col] = pd.to_datetime(
                        non_null.astype(int), unit="D", origin="1899-12-30"
                    ).reindex(s.index)
                except Exception:
                    pass
            continue

    return df


def _coerce_problem_columns(df: pd.DataFrame) -> pd.DataFrame:
    """将 object 列中含有空白字符的数字串统一转为纯字符串。

    DuckDB 在推断 schema 时，如果某列值形如 '11  002142  7.44'（数字+空格），
    会误判为 DOUBLE 并尝试 cast，导致 InvalidInputException。
    通过提前将所有 object 列强制转为 str/None，让 DuckDB 将其识别为 VARCHAR。
    """
    df = df.copy()
    for col in df.columns:
        if df[col].dtype != object:
            continue
        non_null = df[col].dropna()
        if len(non_null) == 0:
            continue
        # 只转换"非 datetime"的 object 列（datetime 列已在 _sanitize_df 处理）
        has_dt = any(isinstance(v, (pd.Timestamp, datetime.datetime)) for v in non_null)
        if has_dt:
            continue
        df[col] = df[col].apply(
            lambda v: None if (v is None or (isinstance(v, float) and np.isnan(v))) else str(v)
        )
    return df


def _register(conn: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame):
    """Zero-copy register a DataFrame as a DuckDB table (no INSERT at all)."""
    df = _sanitize_df(df)
    # 将所有 object 列强制转为 str/None，防止 DuckDB 把含空格的数字串
    # 误判为 DOUBLE 并尝试 cast，导致 InvalidInputException。
    df = _coerce_problem_columns(df)
    conn.register("_tmp_reg_", df)
    try:
        conn.execute(f'CREATE OR REPLACE TABLE "{table}" AS SELECT * FROM _tmp_reg_')
    except Exception as exc:
        # 兜底：若仍然失败（如非 object 列的 cast 问题），逐列探测并修复后重试。
        log.warning("[_register] table=%r cast failed (%s), probing columns for bad types…", table, exc)
        conn.unregister("_tmp_reg_")
        df = df.copy()
        for col in df.columns:
            if df[col].dtype == object:
                continue  # 已由 _coerce_problem_columns 处理
            tmp_df = df[[col]].copy()
            try:
                conn.register("_probe_", tmp_df)
                conn.execute('CREATE OR REPLACE TABLE "_probe_tbl_" AS SELECT * FROM _probe_')
                conn.execute('DROP TABLE IF EXISTS "_probe_tbl_"')
                conn.unregister("_probe_")
            except Exception:
                try:
                    conn.unregister("_probe_")
                except Exception:
                    pass
                log.warning("[_register] column %r dtype=%s cannot cast, coercing to VARCHAR", col, df[col].dtype)
                df[col] = df[col].apply(
                    lambda v: None if (v is None or (isinstance(v, float) and np.isnan(v))) else str(v)
                )
        conn.register("_tmp_reg_", df)
        conn.execute(f'CREATE OR REPLACE TABLE "{table}" AS SELECT * FROM _tmp_reg_')
    finally:
        try:
            conn.unregister("_tmp_reg_")
        except Exception:
            pass


# ── Write-SQL detection (A6: writes allowed, but only into memory) ────────────

# Fallback keyword heuristic when sqlglot is unavailable or fails to parse.
_WRITE_KEYWORD_RE = re.compile(
    r"\b(create|insert|update|delete|drop|alter|truncate|merge)\b",
    re.IGNORECASE,
)

_write_sql_cache: "dict[str, bool]" = {}


def is_write_sql(sql: str) -> bool:
    """True when *sql* mutates data (CREATE/INSERT/UPDATE/DELETE/DROP/…).

    Uses sqlglot's AST when available (SELECT/WITH → False, write nodes →
    True, PRAGMA/INSTALL/ATTACH/… → True conservatively: they are not
    read-only). Falls back to a keyword heuristic. Results are cached —
    the LLM tends to re-send identical statements across turns.
    """
    key = str(sql or "")
    if not key.strip():
        return False
    cached = _write_sql_cache.get(key)
    if cached is not None:
        return cached

    result = _detect_write_sql(key)
    # Bounded cache: never let a long session grow it without limit.
    if len(_write_sql_cache) >= 512:
        _write_sql_cache.clear()
    _write_sql_cache[key] = result
    return result


def _detect_write_sql(sql: str) -> bool:
    try:
        import sqlglot
        import sqlglot.expressions as exp
    except ImportError:
        return bool(_WRITE_KEYWORD_RE.search(sql))

    try:
        statements = sqlglot.parse(sql, dialect="duckdb")
    except Exception:
        # Unparseable → treat any write keyword as a write (conservative).
        return bool(_WRITE_KEYWORD_RE.search(sql))

    for stmt in statements:
        if stmt is None:
            continue
        if isinstance(stmt, exp.Query):
            continue  # read-only branch
        # Any other statement type — CREATE/INSERT/UPDATE/… as well as
        # PRAGMA/INSTALL/ATTACH/COPY/SET/Transaction — is mutating or
        # session-altering, so route through the shadow decision path.
        # (Validation blocks the dangerous ones before execution.)
        return True
    return False


def snapshot_tables_to_memory(conn: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """Copy every base table of *conn* into a fresh ``:memory:`` connection.

    This is the "副 sheet" (secondary sheet): the caller keeps its on-disk
    connection untouched and redirects all subsequent reads/writes to the
    returned in-memory snapshot. Uses pandas round-trips (pyarrow is not a
    dependency); DECIMAL→float64 narrowing is acceptable for analysis work.
    """
    shadow = _new_conn()
    for table in _list_tables(conn):
        try:
            df = conn.execute(f'SELECT * FROM "{table}"').df()
        except Exception as exc:
            log.warning("[shadow] snapshot of table %r failed: %s", table, exc)
            continue
        if df is None or df.empty:
            # Preserve empty tables so DDL referencing them still binds.
            try:
                cols = conn.execute(f'DESCRIBE "{table}"').fetchall()
                shadow.execute(
                    f'CREATE TABLE "{table}" ('
                    + ", ".join(f'"{c[0]}" {c[1]}' for c in cols)
                    + ")"
                )
            except Exception as exc:
                log.warning("[shadow] empty-table DDL for %r failed: %s", table, exc)
            continue
        _register(shadow, table, df)
    return shadow


# ── DuckDB query / introspection ─────────────────────────────────────────────

def _table_schema_str(conn: duckdb.DuckDBPyConnection, table: str, row_count: int) -> str:
    desc_rows = conn.execute(f'DESCRIBE "{table}"').fetchall()
    col_names = [r[0] for r in desc_rows]

    # Detect columns with uninformative auto-generated names (col, col_2, …)
    _AUTO_PAT = re.compile(r'^col(_\d+)?$', re.IGNORECASE)
    has_auto_cols = any(_AUTO_PAT.match(c) for c in col_names)

    col_lines = [f"  {r[0]}  {r[1]}" for r in desc_rows]
    header = f"Table: {table}  ({row_count} rows)"

    # Always append 2 sample rows so the LLM can see actual values and
    # distinguish tables that share identical column names / types across
    # multiple data sources (e.g. monthly MON vs weekly week_5_4).
    # Auto-named columns (col, col_2 …) already had this; now universal.
    if row_count > 0:
        try:
            sample = conn.execute(f'SELECT * FROM "{table}" LIMIT 2').fetchall()
            if sample:
                col_lines.append("  -- sample data (first 2 rows) --")
                for row in sample:
                    row_str = "  | " + " | ".join(
                        str(v)[:30] if v is not None else "NULL" for v in row
                    )
                    col_lines.append(row_str)
        except Exception:
            pass

    return header + "\n" + "\n".join(col_lines)


def _preview_table_dict(conn: duckdb.DuckDBPyConnection, table: str,
                        display_name: str, max_rows: int) -> dict:
    """Fast preview fetch from a DuckDB connection — avoids pandas fillna/astype overhead."""
    try:
        rel = conn.execute(f'SELECT * FROM "{table}" LIMIT {max_rows}')
        cols = [d[0] for d in rel.description]
        rows_raw = rel.fetchall()
        total = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        rows = [["" if v is None else str(v) for v in row] for row in rows_raw]
        return {"name": display_name, "columns": cols, "rows": rows, "total_rows": total}
    except Exception as e:
        return {"name": display_name, "columns": [], "rows": [], "total_rows": 0, "error": str(e)}


def _query(conn: duckdb.DuckDBPyConnection, sql: str) -> Tuple[pd.DataFrame, str]:
    try:
        return conn.execute(sql).df(), ""
    except Exception as exc:
        return pd.DataFrame(), str(exc)


def _list_tables(conn: duckdb.DuckDBPyConnection) -> List[str]:
    """List every base table in a DuckDB connection — including analysis/derived
    tables created at runtime. Uses information_schema (DuckDB-native)."""
    try:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        ).fetchall()
        return [r[0] for r in rows]
    except Exception as exc:
        log.warning("[_list_tables] failed: %s", exc)
        return []
