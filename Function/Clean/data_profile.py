"""Data profiling: missing values, numeric stats, distribution charts."""
import numpy as np
import pandas as pd
from typing import List, Optional, Tuple


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

    # ── Overview ───────────────────────────────────────────────────
    lines = [
        "## 数据概况",
        f"- 总行数：**{n_rows:,}**",
        f"- 总列数：**{n_cols}**",
        f"- 数值列：**{len(numeric_cols)}** 个",
        f"- 含缺失值的列：**{int((missing > 0).sum())}** 个",
        "",
    ]

    # ── Missing value table ────────────────────────────────────────
    lines += [
        "## 缺失值统计",
        "| 列名 | 类型 | 缺失数 | 缺失占比 |",
        "|------|------|--------|----------|",
    ]
    for col in df.columns:
        dtype = str(df[col].dtype)
        lines.append(f"| {col} | {dtype} | {missing[col]} | {missing_pct[col]}% |")

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
            charts.append(fig.to_html(full_html=True, include_plotlyjs="cdn"))
        except Exception:
            pass  # chart is optional; don't break profiling

    return "\n".join(lines), charts
