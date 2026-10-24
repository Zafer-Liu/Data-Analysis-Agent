"""
折线图 Line Chart - 趋势图表
图表分类: 趋势 Trend
感知排名: ★★★★★

统一接口:
    generate(df, mapping, options) -> ChartResult

使用示例:
    from charts.line_chart import generate

    result = generate(
        df=df,
        mapping={"x": "月份", "y": ["销售额", "成本"]},
        options={"title": "销售趋势"}
    )
"""
import logging
log = logging.getLogger(__name__)
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List, Union

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult
from charts.color_schemes import get_color_scheme

__all__ = ["generate"]

_DATA_FMT = "x列(时间/类别) + y列(数值，支持多列)；或宽格式(第一列维度 + 多个数值列)"
_DESC = "展示数据随时间或有序类别的变化趋势，支持多条折线对比，适合连续数据。"

MCKINSEY_COLORS = [
    "#003D7A", "#0084D1", "#00A4EF", "#7FBA00", "#FFB81C",
    "#F7630C", "#DA3B01", "#A4373A", "#6B2C91", "#00B4EF",
]


def _get_colors_for_scheme(color_scheme_name: str, count: int = 10) -> List[str]:
    """获取指定配色方案的颜色列表"""
    scheme = get_color_scheme(color_scheme_name)
    colors = scheme.get("colors", MCKINSEY_COLORS)
    result = []
    for i in range(count):
        result.append(colors[i % len(colors)])
    return result


def _auto_col(df: pd.DataFrame, *hints: str) -> Optional[str]:
    strs = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    col_lower = {str(c).lower(): c for c in df.columns}

    for h in hints:
        h_lower = str(h).lower()
        if h_lower in col_lower:
            return col_lower[h_lower]

    for h in hints:
        h_lower = str(h).lower()
        for col in df.columns:
            c_lower = str(col).lower()
            if h_lower in c_lower or c_lower in h_lower:
                return col

    if hints:
        hint = str(hints[0]).lower()
        if hint == "x":
            return strs[0] if strs else (nums[0] if nums else None)
        if hint == "y":
            return nums[0] if nums else (strs[0] if strs else None)

    return strs[0] if strs else (nums[0] if nums else None)


def _get_numeric_cols(df: pd.DataFrame, exclude: set = None) -> List[str]:
    exclude = exclude or set()
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c not in exclude]


def _detect_wide_format(df: pd.DataFrame):
    if df is None or len(df.columns) < 3:
        return False, None, []

    id_col = df.columns[0]
    rest_cols = list(df.columns[1:])

    first_ok = False
    if df[id_col].dtype == object or pd.api.types.is_string_dtype(df[id_col]):
        first_ok = True
    elif pd.api.types.is_datetime64_any_dtype(df[id_col]):
        first_ok = True
    elif pd.api.types.is_numeric_dtype(df[id_col]):
        s = pd.to_numeric(df[id_col], errors="coerce").dropna()
        if len(s) > 0:
            first_ok = (s.round().between(1900, 2100)).mean() >= 0.8

    if not first_ok:
        return False, None, []

    value_cols = []
    for c in rest_cols:
        s = pd.to_numeric(df[c], errors="coerce")
        if s.notna().sum() > 0:
            value_cols.append(c)

    if len(value_cols) >= 2:
        return True, id_col, value_cols
    return False, None, []


def _build_html(title: str, chart_name: str, library: str, data_fmt: str, desc: str, embed: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>{title}</title></head>
<body>
<h1>{title}</h1><div>{chart_name} | {library}</div>
{embed}
<div><strong>数据格式：</strong>{data_fmt}<br><strong>说明：</strong>{desc}</div>
</body></html>"""


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, Any] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    x: str = "x",
    y: Union[str, List[str]] = "y",
    title: str = "折线图",
    **kwargs
) -> ChartResult:
    warnings = []
    options = options or {}
    mapping = mapping or {}

    if df is None:
        if excel_path:
            try:
                df = pd.read_excel(excel_path)
            except Exception as e:
                log.warning("[chart] 图表生成异常: %s", e)
                return ChartResult(warnings=[f"读取Excel失败: {e}"])
        else:
            return ChartResult(warnings=["请提供 df 或 excel_path"])

    x_col = mapping.get("x")
    x_col = x_col if x_col is not None else x
    y_cols = mapping.get("y")
    y_cols = y_cols if y_cols is not None else y
    series_col = mapping.get("series")
    series_col = series_col if series_col is not None else (mapping.get("color") if mapping.get("color") is not None else None)
    title = options.get("title", title)
    color_scheme_name = options.get("color_scheme", "mckinsey")

    if y_cols == "y" or y_cols is None:
        y_cols = []
    elif isinstance(y_cols, str):
        y_cols = [y_cols]
    elif isinstance(y_cols, (tuple, set)):
        y_cols = list(y_cols)
    elif not isinstance(y_cols, list):
        y_cols = [str(y_cols)]

    # A list under series represents wide-format value columns, not a column
    # name. Accept it for callers that use series=["A", "B"] as shorthand.
    if isinstance(series_col, (list, tuple, set)):
        if not y_cols:
            y_cols = list(series_col)
        series_col = None

    # ── Series 分组模式（x=维度, y=数值, series=分组列）──────────────
    if isinstance(series_col, str) and series_col in df.columns:
        x_key = x_col if (x_col and x_col in df.columns) else _auto_col(df, x_col, "x")
        y_key = (y_cols[0] if isinstance(y_cols, list) and y_cols else y_cols) if y_cols else None
        if not y_key or y_key not in df.columns:
            y_key = _auto_col(df, y_key or "y", "y")

        if x_key and y_key and x_key in df.columns and y_key in df.columns:
            # 判断是否为 ROC 模式（x/y 均为 [0,1] 范围内的浮点数值列）
            x_numeric = pd.api.types.is_numeric_dtype(df[x_key])
            y_numeric = pd.api.types.is_numeric_dtype(df[y_key])
            is_roc_like = (
                x_numeric and y_numeric
                and df[x_key].dropna().between(0, 1).all()
                and df[y_key].dropna().between(0, 1).all()
                and "auc" in df.columns
            )

            colors = _get_colors_for_scheme(color_scheme_name, df[series_col].nunique() + 1)
            fig = go.Figure()
            for idx, (grp_val, grp_df) in enumerate(df.groupby(series_col, sort=True)):
                grp_df = grp_df.sort_values(x_key)
                color = colors[idx % len(colors)]
                auc_str = ""
                if is_roc_like and "auc" in grp_df.columns:
                    auc_val = grp_df["auc"].iloc[0]
                    auc_str = f" (AUC={auc_val:.4f})"
                hover = (
                    f"<b>{grp_val}{auc_str}</b><br>{x_key}: %{{x:.4f}}<br>{y_key}: %{{y:.4f}}<extra></extra>"
                    if is_roc_like
                    else f"<b>{grp_val}</b><br>{x_key}: %{{x}}<br>{y_key}: %{{y:,.2f}}<extra></extra>"
                )
                fig.add_trace(go.Scatter(
                    x=grp_df[x_key],
                    y=grp_df[y_key],
                    mode="lines+markers" if not is_roc_like else "lines",
                    name=f"{grp_val}{auc_str}",
                    line=dict(color=color, width=2.5),
                    marker=dict(size=5, color=color),
                    hovertemplate=hover,
                ))

            xaxis_cfg = dict(title=x_key, showgrid=False,
                             showline=True, linewidth=1.2, linecolor="#B0B0B0")
            yaxis_cfg = dict(title=y_key,
                             showgrid=True, gridcolor="#E6E6E6", gridwidth=1, showline=False)
            if is_roc_like:
                xaxis_cfg["range"] = [0, 1]
                yaxis_cfg["range"] = [0, 1]
                fig.add_shape(type="line", x0=0, y0=0, x1=1, y1=1,
                              line=dict(color="#aaa", width=1.2, dash="dash"))

            fig.update_layout(
                title=dict(text=title, x=0.02, xanchor="left", font=dict(size=20, color="#1F1F1F")),
                font=dict(family="Arial, Helvetica, sans-serif", size=12, color="#1F1F1F"),
                paper_bgcolor="white", plot_bgcolor="white",
                margin=dict(l=70, r=40, t=70, b=60),
                xaxis=xaxis_cfg,
                yaxis=yaxis_cfg,
                legend=dict(orientation="h", yanchor="bottom", y=1.02,
                            xanchor="left", x=0, bgcolor="rgba(0,0,0,0)",
                            borderwidth=0, font=dict(size=11)),
                hovermode="x unified" if not is_roc_like else "closest",
            )
            chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
            html = _build_html(title, "line_chart", "plotly", _DATA_FMT, _DESC, chart_html)
            return ChartResult(html=html, spec={}, warnings=warnings,
                               meta={"chart_id": "line_chart", "n_rows": len(df),
                                     "x_col": x_key, "y_cols": [y_key], "series_col": series_col})

    is_wide, id_col, value_cols = _detect_wide_format(df)
    strict_y = bool(options.get("strict_y", False))

    if is_wide and not strict_y:
        _x = id_col
        # 宽表默认全量，避免上游传单列导致只出“中国”
        _y_cols = value_cols
        warnings.append("检测到宽格式，已使用第一列为x、其余数值列为多条线")
    else:
        if x_col and x_col != "x" and x_col in df.columns:
            _x = x_col
        else:
            _x = _auto_col(df, x_col, "x", "时间", "date", "月份", "年份", "日期", "period", "year")

        if _x is None or _x not in df.columns:
            return ChartResult(warnings=["找不到 x 列（时间/类别）"])

        _y_cols = []
        if not y_cols:
            _y_cols = _get_numeric_cols(df, exclude={_x})[:10]
            if not _y_cols:
                for c in df.columns:
                    if c == _x:
                        continue
                    s = pd.to_numeric(df[c], errors="coerce")
                    if s.notna().sum() > 0:
                        _y_cols.append(c)
                _y_cols = _y_cols[:10]
        else:
            for yc in y_cols:
                if yc in df.columns:
                    _y_cols.append(yc)
                else:
                    auto_y = _auto_col(df, yc, "y", "value", "amount", "销售额", "销量", "数值")
                    if auto_y and auto_y in df.columns and auto_y not in _y_cols:
                        _y_cols.append(auto_y)

        _y_cols = [c for i, c in enumerate(_y_cols) if c != _x and c not in _y_cols[:i]]
        if not _y_cols:
            return ChartResult(warnings=["找不到任何可用的 y 列（数值列）"])

    if len(_y_cols) > 10:
        warnings.append(f"数值列过多({len(_y_cols)}个)，只显示前10列")
        _y_cols = _y_cols[:10]

    keep_cols = [_x] + [c for c in _y_cols if c in df.columns]
    df_plot = df[keep_cols].copy()

    valid_y = []
    for yc in _y_cols:
        df_plot[yc] = pd.to_numeric(df_plot[yc], errors="coerce")
        if df_plot[yc].notna().sum() > 0:
            valid_y.append(yc)

    _y_cols = valid_y
    if not _y_cols:
        return ChartResult(warnings=["所有 y 列都无法转换为数值"])

    df_plot = df_plot.dropna(subset=_y_cols, how="all")
    if df_plot.empty:
        return ChartResult(warnings=["无有效数据"])

    df_plot[_x] = df_plot[_x].astype(str)

    # 获取配色列表
    colors = _get_colors_for_scheme(color_scheme_name, len(_y_cols))

    fig = go.Figure()
    for idx, yc in enumerate(_y_cols):
        color = colors[idx % len(colors)]
        df_line = df_plot[[_x, yc]].dropna()
        fig.add_trace(go.Scatter(
            x=df_line[_x],
            y=df_line[yc],
            mode="lines+markers",
            name=yc,
            line=dict(color=color, width=3.2),
            marker=dict(size=7, color=color),
            hovertemplate=f"<b>{yc}</b><br>%{{x}}<br>值: %{{y:.2f}}<extra></extra>"
        ))

    fig.update_layout(
        title=dict(text=title, x=0.02, xanchor="left", font=dict(size=20, color="#1F1F1F")),
        font=dict(family="Arial, Helvetica, sans-serif", size=12, color="#1F1F1F"),
        paper_bgcolor="white",  # 整体背景白
        plot_bgcolor="white",  # 绘图区白
        margin=dict(l=70, r=40, t=70, b=60),
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="left", x=0,
            bgcolor="rgba(0,0,0,0)",  # 图例透明，无底纹
            borderwidth=0,
            font=dict(size=11, color="#1F1F1F")
        ),
        xaxis_title=_x,
        yaxis_title="值",
    )
    fig.update_xaxes(
        showgrid=False,  # x方向网格去掉
        showline=True, linewidth=1.2, linecolor="#B0B0B0",
        ticks="outside", tickcolor="#B0B0B0"
    )
    fig.update_yaxes(
        showgrid=True, gridcolor="#E6E6E6", gridwidth=1,  # 只留浅灰横向网格
        showline=False,
        zeroline=False
    )
    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "line_chart", "plotly", _DATA_FMT, _DESC, chart_html)

    return ChartResult(
        html=html,
        spec={},
        warnings=warnings,
        meta={"chart_id": "line_chart", "n_rows": len(df_plot), "x_col": _x, "y_cols": _y_cols, "is_wide_format": is_wide}
    )
