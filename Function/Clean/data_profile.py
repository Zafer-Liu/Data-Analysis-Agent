"""Data profiling: missing values, numeric stats, distribution charts."""
import logging
log = logging.getLogger(__name__)
import numpy as np
import pandas as pd
from typing import List, Optional, Tuple


_PSEUDO_NULL_VALUES = {"", "null", "none", "nan", "n/a", "na", "-"}
_DATE_NAME_RE = ("date", "time", "日期", "时间", "dt")


def _pseudo_null_count(series: pd.Series) -> int:
    values = series.dropna().astype(str).str.strip().str.lower()
    if values.empty:
        return 0
    return int(values.isin(_PSEUDO_NULL_VALUES).sum())


def _looks_date_like(name: str, series: pd.Series) -> bool:
    lowered = str(name or "").lower()
    if any(token in lowered for token in _DATE_NAME_RE):
        return True
    sample = series.dropna().astype(str).str.strip().head(50)
    if sample.empty:
        return False
    return bool(sample.str.fullmatch(r"\d{8}").mean() >= 0.8)


def _date_parse_rate(series: pd.Series) -> float | None:
    sample = series.dropna().astype(str).str.strip()
    sample = sample[~sample.str.lower().isin(_PSEUDO_NULL_VALUES)]
    if sample.empty:
        return None
    compact = sample.str.replace(r"\.0$", "", regex=True)
    if compact.str.fullmatch(r"\d{8}").mean() >= 0.8:
        parsed = pd.to_datetime(compact, format="%Y%m%d", errors="coerce")
    else:
        parsed = pd.to_datetime(sample, errors="coerce")
    return round(float(parsed.notna().mean() * 100), 2)


def profile(
    df: pd.DataFrame,
    columns: Optional[List[str]] = None,
) -> Tuple[str, List[str]]:
    """
    Profile a DataFrame.

    Returns
    -------
    (markdown_text, chart_html_list)
      markdown_text   : stats table in markdown
      chart_html_list : list of full Plotly HTML strings (one combined histogram figure)
    """
    if columns:
        df = df[[c for c in columns if c in df.columns]]

    n_rows, n_cols = len(df), len(df.columns)
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    missing = df.isnull().sum()
    missing_pct = (missing / n_rows * 100).round(2) if n_rows > 0 else missing * 0
    pseudo_missing = {col: _pseudo_null_count(df[col]) for col in df.columns}
    pseudo_missing_cols = [col for col, count in pseudo_missing.items() if count > 0]
    # Zero values in numeric columns are a distinct quality signal: they may be
    # real zeros or silently-failed readings. Profiling reports them separately
    # so "0.0% missing" columns cannot masquerade as complete.
    zero_counts = {col: int((df[col] == 0).sum()) for col in numeric_cols}
    zero_cols = [col for col in numeric_cols if zero_counts[col] > 0]
    date_parse_rates = {
        col: _date_parse_rate(df[col])
        for col in df.columns
        if _looks_date_like(col, df[col])
    }

    # ── Overview ───────────────────────────────────────────────────
    lines = [
        "## 数据概况",
        f"- 总行数：**{n_rows:,}**",
        f"- 总列数：**{n_cols}**",
        f"- 数值列：**{len(numeric_cols)}** 个",
        f"- 含缺失值的列：**{int((missing > 0).sum())}** 个",
        f"- 含伪缺失字符串的列：**{len(pseudo_missing_cols)}** 个",
        f"- 含零值的数值列：**{len(zero_cols)}** 个",
        "",
    ]

    quality_warnings = []
    if pseudo_missing_cols:
        quality_warnings.append(
            "检测到字符串型伪缺失值，建分析样本前必须先清洗或确认导入规则。"
        )
    for col in zero_cols:
        if n_rows > 0 and zero_counts[col] / n_rows > 0.5:
            quality_warnings.append(
                f"数值字段 `{col}` 零值占比超过 50%，需先确认零值语义（真实零还是缺失）。"
            )
    for col, rate in date_parse_rates.items():
        if rate is not None and rate < 95:
            quality_warnings.append(f"日期字段 `{col}` 可解析率仅 {rate}%，需先检查格式。")
    if quality_warnings:
        lines += [
            "## 数据质量闸口",
            "| 风险 | 处理要求 |",
            "|------|----------|",
        ]
        for warning in quality_warnings:
            lines.append(f"| {warning} | 修复后再计算关键指标 |")
        lines.append("")

    # ── Missing value table ────────────────────────────────────────
    lines += [
        "## 缺失值统计",
        "| 列名 | 类型 | 缺失数 | 缺失占比 |",
        "|------|------|--------|----------|",
    ]
    for col in df.columns:
        dtype = str(df[col].dtype)
        lines.append(f"| {col} | {dtype} | {missing[col]} | {missing_pct[col]}% |")

    if pseudo_missing_cols:
        lines += [
            "",
            "## 伪缺失字符串统计",
            "| 列名 | 伪缺失数 | 伪缺失占比 |",
            "|------|----------|------------|",
        ]
        for col in pseudo_missing_cols:
            pct = round(pseudo_missing[col] / n_rows * 100, 2) if n_rows > 0 else 0
            lines.append(f"| {col} | {pseudo_missing[col]} | {pct}% |")

    if zero_cols:
        lines += [
            "",
            "## 数值列零值统计",
            "| 列名 | 零值数 | 零值占比 |",
            "|------|--------|----------|",
        ]
        for col in zero_cols:
            pct = round(zero_counts[col] / n_rows * 100, 2) if n_rows > 0 else 0
            lines.append(f"| {col} | {zero_counts[col]} | {pct}% |")

    if date_parse_rates:
        lines += [
            "",
            "## 日期字段可解析率",
            "| 列名 | 可解析率 |",
            "|------|----------|",
        ]
        for col, rate in date_parse_rates.items():
            shown = "—" if rate is None else f"{rate}%"
            lines.append(f"| {col} | {shown} |")

    # ── Numeric stats ──────────────────────────────────────────────
    if numeric_cols:
        lines += [
            "",
            "## 数值列统计",
            "| 列名 | 均值 | 标准差 | 最小值 | Q1 (25%) | 中位数 | Q3 (75%) | 最大值 |",
            "|------|------|--------|--------|----------|--------|----------|--------|",
        ]
        for col in numeric_cols:
            s = df[col].dropna()
            if len(s) == 0:
                lines.append(f"| {col} | — | — | — | — | — | — | — |")
                continue
            lines.append(
                f"| {col}"
                f" | {s.mean():.4g}"
                f" | {s.std():.4g}"
                f" | {s.min():.4g}"
                f" | {s.quantile(0.25):.4g}"
                f" | {s.median():.4g}"
                f" | {s.quantile(0.75):.4g}"
                f" | {s.max():.4g} |"
            )

    # ── Generate combined histogram figure ─────────────────────────
    charts: List[str] = []
    plot_cols = [c for c in numeric_cols if df[c].dropna().shape[0] >= 2]
    if plot_cols:
        try:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots

            n = len(plot_cols)
            ncols = min(n, 3)
            nrows = (n + ncols - 1) // ncols
            height = max(280, nrows * 230)

            fig = make_subplots(
                rows=nrows,
                cols=ncols,
                subplot_titles=plot_cols,
            )
            colors = [
                "#3b82f6", "#f59e0b", "#10b981",
                "#ef4444", "#8b5cf6", "#06b6d4",
                "#f97316", "#84cc16", "#ec4899",
            ]
            for i, col in enumerate(plot_cols):
                r, c = divmod(i, ncols)
                s = df[col].dropna()
                fig.add_trace(
                    go.Histogram(
                        x=s,
                        nbinsx=min(30, max(10, len(s) // 20)),
                        name=col,
                        marker_color=colors[i % len(colors)],
                        showlegend=False,
                    ),
                    row=r + 1,
                    col=c + 1,
                )

            fig.update_layout(
                title_text="数值列分布图",
                template="plotly_white",
                height=height,
                margin=dict(l=40, r=20, t=60, b=40),
            )
            charts.append(
                fig.to_html(
                    full_html=True,
                    include_plotlyjs="/static/vendor/plotly.min.js",
                )
            )
        except Exception as e:
            log.warning("[data_profile] 分布图生成异常: %s", e)

    return "\n".join(lines), charts
