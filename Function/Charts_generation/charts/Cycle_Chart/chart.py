"""
周期图 Cycle Chart - 周期模式
图表分类: 周期 Cycle
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult
"""
import logging
log = logging.getLogger(__name__)
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional

import pandas as pd
import plotly.graph_objects as go
import plotly.subplots as sp
import plotly.io as pio
import numpy as np

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult

try:
    from charts.color_schemes import get_color_scheme
except ImportError as e:  # pragma: no cover - 兜底，保证独立调用可用
    log.debug("[chart] 配色模块加载失败: %s", e)
    def get_color_scheme(name: str = "mckinsey"):
        return {"colors": [
            "#005B9A", "#2F7EBB", "#4C9FD6", "#7FB8E6", "#A9CDEB",
            "#5B6770", "#7D8790", "#9AA3AA", "#B8BFC5", "#D0D5D9",
        ]}

__all__ = ["generate"]

_DATA_FMT = "宽格式: 行=周期(年份), 列=时间相位(月份) | 长格式: time列 + value列 + group列"
_DESC = "展示多个周期的数据模式，支持宽格式(多年月度)和长格式(小倍数)两种布局。"


def _build_html(title: str, chart_name: str, library: str, data_fmt: str, desc: str, embed: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>{title}</title>
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

def _clean_numeric_like(df: pd.DataFrame) -> pd.DataFrame:
    """清洗 object/string 列：仅对“去逗号后能解析为数值”的列做千分位逗号剔除，
    避免误伤含合法逗号的文本/标签列（如 "1,000 units"、"北京,上海"）。"""
    d = df.copy()
    for c in d.columns:
        # 兼容 object 与 StringDtype（新版 pandas 全字符串列会是 string dtype）
        if not (d[c].dtype == "object" or pd.api.types.is_string_dtype(d[c])):
            continue
        s = d[c].astype(str).str.strip()
        stripped = s.str.replace(",", "", regex=False)
        # 去逗号后若多数能解析为数值，才认为是千分位格式数值列
        coerced = pd.to_numeric(stripped, errors="coerce")
        if coerced.notna().mean() > 0.5:
            d[c] = stripped
        else:
            # 保留原始文本，仅去首尾空白
            d[c] = s
    return d

def _detect_format(df: pd.DataFrame) -> str:
    # 宽表：第一列像年份/时间，后续列大多可数值化（>=2列即可）
    if df.shape[1] >= 3:
        first = df.iloc[:, 0].astype(str).str.strip()
        first_year_like = first.str.match(r"^\d{4}$").mean() > 0.6 or pd.to_datetime(first, errors="coerce").notna().mean() > 0.6

        numeric_ratio = []
        for c in df.columns[1:]:
            s = pd.to_numeric(df[c], errors="coerce")
            numeric_ratio.append(s.notna().mean())

        if first_year_like and np.mean(numeric_ratio) > 0.7:
            return "wide"

    return "long"


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    time: str = "time",
    value: str = "value",
    group: Optional[str] = None,
    title: str = "周期图",
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

    if df.empty:
        return ChartResult(warnings=["输入数据为空"])

    df = _clean_numeric_like(df)  # 放到这里，确保所有非空数据都清洗

    title = options.get("title", title)
    fmt = _detect_format(df)

    # 配色方案：优先用 options.color_scheme
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _palette = _scheme.get("colors") or [
        "#005B9A", "#2F7EBB", "#4C9FD6", "#7FB8E6", "#A9CDEB",
        "#5B6770", "#7D8790", "#9AA3AA", "#B8BFC5", "#D0D5D9",
    ]

    if fmt == "wide":
        return _generate_wide_format(df, title, warnings, _palette)
    else:
        return _generate_long_format(df, mapping, title, warnings, _palette)


def _generate_wide_format(df: pd.DataFrame, title: str, warnings: list, palette: list = None) -> ChartResult:
    years = df.iloc[:, 0].astype(str).tolist()
    phases = df.columns[1:].tolist()

    # 强制数值化
    data = df.iloc[:, 1:].apply(pd.to_numeric, errors="coerce").values
    avg_values = np.nanmean(data, axis=0)

    # 自动识别比例数据(0~1)
    finite_vals = data[np.isfinite(data)]
    is_ratio = (
        len(finite_vals) > 0
        and np.nanmax(finite_vals) <= 1.0
        and np.nanmin(finite_vals) >= -1.0
    )

    y_hover_fmt = ".1%" if is_ratio else ",.2f"
    y_tick_fmt = ".0%" if is_ratio else ",.2f"
    y_axis_title = "占比" if is_ratio else "数值"

    fig = go.Figure()

    # ===== 麦肯锡风配色（主蓝 + 辅助蓝 + 中性灰）=====
    # 说明：
    # - 首条线用 McKinsey 蓝，突出主序列
    # - 其他线用不同深浅蓝 + 灰，保证专业克制
    # - 均值线用强调橙（常见咨询报告的重点色）
    mck_palette = palette or [
        "#005B9A",  # McKinsey Blue（主色）
        "#2F7EBB",
        "#4C9FD6",
        "#7FB8E6",
        "#A9CDEB",
        "#5B6770",  # slate gray
        "#7D8790",
        "#9AA3AA",
        "#B8BFC5",
        "#D0D5D9",
    ]
    avg_color = "#E67E22"  # 强调橙（用于均值/基准）

    for idx, year in enumerate(years):
        color = mck_palette[idx % len(mck_palette)]
        row_data = data[idx]

        fig.add_trace(go.Scatter(
            x=phases,
            y=row_data,
            mode='lines',
            name=year,
            line=dict(color=color, width=2),
            hovertemplate=f"<b>%{{x}}</b><br>{year}: %{{y:{y_hover_fmt}}}<extra></extra>"
        ))

        # 末端点
        valid_idx = np.where(~np.isnan(row_data))[0]
        if len(valid_idx) > 0:
            last_i = valid_idx[-1]
            fig.add_trace(go.Scatter(
                x=[phases[last_i]],
                y=[row_data[last_i]],
                mode='markers',
                name=f"{year}_end",
                marker=dict(color=color, size=7),
                showlegend=False,
                hovertemplate=f"<b>{phases[last_i]}</b><br>{year} 末端: %{{y:{y_hover_fmt}}}<extra></extra>"
            ))

    # 均值线（强调橙 + 虚线）
    fig.add_trace(go.Scatter(
        x=phases,
        y=avg_values,
        mode='lines',
        name='平均值',
        line=dict(color=avg_color, width=3, dash='dash'),
        hovertemplate=f"<b>%{{x}}</b><br>平均: %{{y:{y_hover_fmt}}}<extra></extra>"
    ))

    # McKinsey 风格版式：白底、浅灰网格、深灰文字、克制边框
    fig.update_layout(
        title=title,
        xaxis_title='月份/时间相位',
        yaxis_title=y_axis_title,
        hovermode='x unified',
        plot_bgcolor='white',
        paper_bgcolor='white',
        font=dict(
            family="Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif",
            color="#1F2A33",
            size=13
        ),
        margin=dict(l=68, r=36, t=64, b=56),
        height=600,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            bgcolor="rgba(255,255,255,0.85)"
        )
    )

    fig.update_xaxes(
        showline=True,
        linewidth=1,
        linecolor="#D9DEE3",
        showgrid=False,
        tickfont=dict(color="#3A4650")
    )
    fig.update_yaxes(
        tickformat=y_tick_fmt,
        showline=True,
        linewidth=1,
        linecolor="#D9DEE3",
        gridcolor="#EEF2F5",
        zeroline=False,
        tickfont=dict(color="#3A4650")
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(
        title,
        "cycle_chart",
        "plotly",
        _DATA_FMT,
        "宽格式：多周期数据（麦肯锡风配色，自动识别比例/绝对值）",
        chart_html
    )

    meta = {
        "chart_id": "cycle_chart",
        "format": "wide",
        "n_years": len(years),
        "n_phases": len(phases),
        "is_ratio": is_ratio,
        "theme": "mckinsey"
    }
    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)




def _generate_long_format(df: pd.DataFrame, mapping: Dict[str, str], title: str, warnings: list, palette: list = None) -> ChartResult:
    # 优先用 mapping
    time_col = mapping.get("time")
    value_col = mapping.get("value")
    group_col = mapping.get("group")

    # fallback
    cols = list(df.columns)
    if time_col is None and len(cols) > 0: time_col = cols[0]
    if value_col is None and len(cols) > 1: value_col = cols[1]
    if group_col is None and len(cols) > 2: group_col = cols[2]

    if value_col is None:
        return ChartResult(warnings=["无法找到数值列"])

    d = df.copy()
    d[value_col] = pd.to_numeric(d[value_col], errors="coerce")
    d = d[d[value_col].notna()].copy()

    # ---- 关键：若是 year-month-value 这种 long，自动 pivot 成经典 cycle ----
    # 识别 time 是否日期
    dt = pd.to_datetime(d[time_col], errors="coerce")
    if dt.notna().mean() > 0.7:
        d["_year"] = dt.dt.year.astype(str)
        d["_phase"] = dt.dt.month
        month_order = list(range(1, 13))
        pivot = d.pivot_table(index="_year", columns="_phase", values=value_col, aggfunc="mean")
        pivot = pivot.reindex(columns=month_order)
        pivot = pivot.reset_index().rename(columns={"_year": "year"})
        pivot.columns = ["year"] + [f"{m}月" for m in month_order]
        return _generate_wide_format(pivot, title, warnings, palette)

    # 若 time 本身是月份，group 是年份，也可 pivot
    if group_col in d.columns:
        # 尝试当作 year=group, phase=time
        tmp = d[[group_col, time_col, value_col]].copy()
        pivot = tmp.pivot_table(index=group_col, columns=time_col, values=value_col, aggfunc="mean")
        if pivot.shape[1] >= 6:  # 至少有较完整相位，认为是周期图
            pivot = pivot.reset_index()
            return _generate_wide_format(pivot, title, warnings, palette)

    # 兜底：单线图（不再默认小倍数，避免碎图）
    _fallback_palette = palette or ["#005B9A"]
    d_sorted = d.sort_values(time_col)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=d_sorted[time_col].astype(str),
        y=d_sorted[value_col],
        mode='lines+markers',
        line=dict(color=_fallback_palette[0], width=2),
        marker=dict(size=6)
    ))
    fig.update_layout(title=title, xaxis_title='时间', yaxis_title='数值', height=500)

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "cycle_chart", "plotly", _DATA_FMT, "长格式：自动识别失败，回退单线图", chart_html)
    return ChartResult(html=html, spec={}, warnings=warnings + ["未识别出周期结构，已回退单线图"], meta={"format": "long_fallback"})
