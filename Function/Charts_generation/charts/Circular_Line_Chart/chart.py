"""
圆形折线图 Circular Line Chart - 周期趋势
图表分类: 趋势 Trend / 周期 Cyclical
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult

使用示例:
    from charts.circular_line_chart import generate

    result = generate(
        df=df,
        mapping={"x": "周次", "y": ["2014", "2015", "2016", "2017"]},
        options={"title": "流感就诊率周期趋势"}
    )
"""
import logging
log = logging.getLogger(__name__)
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List, Union
import math

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import numpy as np

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult
from charts.color_schemes import get_color_scheme

__all__ = ["generate"]

_DATA_FMT = "x列(周期/类别) + y列(数值，支持多列)；或宽格式(第一列维度 + 多个数值列)"
_DESC = "将折线图卷成圆形，适合展示周期性数据（如一年52周、12个月）。相比标准折线图更紧凑，但精度略低。"

MCKINSEY_COLORS = [
    "#003D7A", "#0084D1", "#00A4EF", "#7FBA00", "#FFB81C",
    "#F7630C", "#DA3B01", "#A4373A", "#6B2C91", "#00B4EF",
]


def _get_colors_for_scheme(color_scheme_name: str, count: int = 10) -> List[str]:
    """获取指定配色方案的颜色列表。

    get_color_scheme 对未知方案名回退到 mckinsey，永不抛异常；因此这里
    不再 try/except 降级到硬编码列表（否则 bcg/bain/ey 请求会被静默降级
    为 McKinsey 蓝）。仅在配色模块缺失时才回退。
    """
    scheme = get_color_scheme(color_scheme_name)
    colors = scheme.get("colors", MCKINSEY_COLORS)
    return [colors[i % len(colors)] for i in range(count)]


def _auto_col(df: pd.DataFrame, *hints: str) -> Optional[str]:
    """自动检测列名"""
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
    """获取所有数值列"""
    exclude = exclude or set()
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c not in exclude]


def _detect_wide_format(df: pd.DataFrame):
    """检测宽格式数据"""
    if df is None or len(df.columns) < 3:
        return False, None, []

    id_col = df.columns[0]
    rest_cols = list(df.columns[1:])

    first_ok = False
    if df[id_col].dtype == object or pd.api.types.is_string_dtype(df[id_col]):
        first_ok = True
    elif pd.api.types.is_numeric_dtype(df[id_col]):
        s = pd.to_numeric(df[id_col], errors="coerce").dropna()
        if len(s) > 0:
            first_ok = (s.round().between(1, 365)).mean() >= 0.8

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
<html lang="zh-CN"><head><meta charset="UTF-8"><title>{title}</title>
<style>
body{{font-family:"Heiti SC","Microsoft YaHei",sans-serif;margin:40px;background:#fafafa}}
.chart-wrap{{background:white;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08);padding:24px;margin-bottom:32px}}
h1{{color:#222;font-size:22px;margin-bottom:6px}}
.subtitle{{color:#888;font-size:13px;margin-bottom:24px}}
.desc{{color:#555;font-size:14px;line-height:1.7;margin-top:20px}}
</style></head>
<body><div class="chart-wrap">
<h1>{title}</h1><div class="subtitle">{chart_name} | {library}</div>
{embed}
</div><div class="desc">
<strong>数据格式：</strong>{data_fmt}<br>
<strong>说明：</strong>{desc}
</div></body></html>"""


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, Any] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    x: str = "x",
    y: Union[str, List[str]] = "y",
    title: str = "圆形折线图",
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
    title = options.get("title", title)
    color_scheme_name = options.get("color_scheme", "mckinsey")

    # 规范化 y_cols
    if y_cols == "y" or y_cols is None:
        y_cols = []
    elif isinstance(y_cols, str):
        y_cols = [y_cols]
    elif isinstance(y_cols, (tuple, set)):
        y_cols = list(y_cols)
    elif not isinstance(y_cols, list):
        y_cols = [str(y_cols)]

    # 检测宽格式
    is_wide, id_col, value_cols = _detect_wide_format(df)
    strict_y = bool(options.get("strict_y", False))

    if is_wide and not strict_y:
        _x = id_col
        _y_cols = value_cols
        warnings.append("检测到宽格式，已使用第一列为周期、其余列为多条线")
    else:
        # 查找 x 列
        if x_col and x_col != "x" and x_col in df.columns:
            _x = x_col
        else:
            _x = _auto_col(df, x_col, "x", "周", "周次", "月", "月份", "week", "month", "period")

        if _x is None or _x not in df.columns:
            return ChartResult(warnings=["找不到 x 列（周期/类别）"])

        # 查找 y 列
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
                    auto_y = _auto_col(df, yc, "y", "value", "amount", "数值")
                    if auto_y and auto_y in df.columns and auto_y not in _y_cols:
                        _y_cols.append(auto_y)

        _y_cols = [c for i, c in enumerate(_y_cols) if c != _x and c not in _y_cols[:i]]
        if not _y_cols:
            return ChartResult(warnings=["找不到任何可用的 y 列（数值列）"])

    if len(_y_cols) > 10:
        warnings.append(f"数值列过多({len(_y_cols)}个)，只显示前10列")
        _y_cols = _y_cols[:10]

    # 准备数据
    keep_cols = [_x] + [c for c in _y_cols if c in df.columns]
    df_plot = df[keep_cols].copy()

    # 转换 y 为数值
    valid_y = []
    for yc in _y_cols:
        df_plot[yc] = pd.to_numeric(df_plot[yc], errors="coerce")
        if df_plot[yc].notna().sum() > 0:
            valid_y.append(yc)

    _y_cols = valid_y
    if not _y_cols:
        return ChartResult(warnings=["所有 y 列都无法转换为数值"])

    # 至少保留有一列 y 非空的行
    df_plot = df_plot.dropna(subset=_y_cols, how="all")
    if df_plot.empty:
        return ChartResult(warnings=["无有效数据"])

    # 排序：x 多数可转数字时按数字排序，否则按字符串排序
    month_order = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
    }
    x_lower = df_plot[_x].astype(str).str.strip().str[:3].str.lower()
    if x_lower.isin(month_order.keys()).mean() >= 0.8:
        df_plot = df_plot.assign(__m=x_lower.map(month_order)).sort_values("__m").drop(columns="__m")
    else:
        df_plot = df_plot.sort_values(by=_x)
    df_plot = df_plot.reset_index(drop=True)

    period_labels = df_plot[_x].astype(str).tolist()
    n_periods = len(period_labels)
    if n_periods < 3:
        return ChartResult(warnings=["数据点过少（< 3），无法绘制圆形折线图"])

    # 用数值角度严格控制起点位置（第一个周期在12点）
    theta_vals = np.linspace(0, 360, n_periods, endpoint=False).tolist()
    theta_closed = theta_vals + [theta_vals[0]]

    colors = _get_colors_for_scheme(color_scheme_name, len(_y_cols))
    fig = go.Figure()

    plotted_cols = []
    for idx, yc in enumerate(_y_cols):
        color = colors[idx % len(colors)]
        s = pd.Series(df_plot[yc].tolist(), dtype="float64").interpolate(limit_direction="both")

        if s.notna().sum() == 0:
            warnings.append(f"{yc} 全为空，已跳过")
            continue

        values = s.tolist()
        values_closed = values + [values[0]]

        fig.add_trace(go.Scatterpolar(
            r=values_closed,
            theta=theta_closed,  # 数值角度闭环
            mode="lines+markers",
            fill=None,
            name=str(yc),
            line=dict(color=color, width=3),
            marker=dict(size=5, color=color),
            customdata=period_labels + [period_labels[0]],
            hovertemplate=f"<b>{yc}</b><br>周期: %{{customdata}}<br>值: %{{r:.2f}}<extra></extra>"
        ))
        plotted_cols.append(yc)

    if not plotted_cols:
        return ChartResult(warnings=["没有可绘制的 y 列（可能全为空）"])

    fig.update_layout(
        title=dict(
            text=title,
            font=dict(size=20, color="#222"),
            x=0.5,
            xanchor="center"
        ),
        width=1000,
        height=780,
        font=dict(family="Heiti SC, Microsoft YaHei, sans-serif", size=12, color="#1F1F1F"),
        polar=dict(
            radialaxis=dict(
                visible=True,
                rangemode="tozero",
                tickfont=dict(size=11),
                gridcolor="#D9D9D9",
                gridwidth=1,
                showline=True,
                linecolor="#BFBFBF"
            ),
            angularaxis=dict(
                tickmode="array",
                tickvals=theta_vals,
                ticktext=period_labels,
                tickfont=dict(size=10),
                gridcolor="#E6E6E6",
                gridwidth=1,
                direction="clockwise",
                rotation=90
            ),
            bgcolor="rgba(245,247,250,0.55)"
        ),
        paper_bgcolor="white",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=-0.14,
            xanchor="center",
            x=0.5,
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="rgba(0,0,0,0.1)",
            borderwidth=1,
            font=dict(size=11)
        ),
        margin=dict(l=60, r=60, t=90, b=130),
        hovermode="closest"
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "circular_line_chart", "plotly", _DATA_FMT, _DESC, chart_html)

    return ChartResult(
        html=html,
        spec={},
        warnings=warnings,
        meta={
            "chart_id": "circular_line_chart",
            "n_rows": len(df_plot),
            "x_col": _x,
            "y_cols": plotted_cols,
            "n_periods": n_periods,
            "is_wide_format": is_wide
        }
    )
