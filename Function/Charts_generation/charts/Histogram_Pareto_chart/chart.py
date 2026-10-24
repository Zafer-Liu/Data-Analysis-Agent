"""
直方图 Histogram - 分布图表
支持两种模式：
  1. 单列数据 → 频率分布直方图
  2. 双列数据 → 帕累托图（柱子 + 累积曲线）

图表分类: 分布 Distribution | 书章节: Ch5
感知排名: ★★★★★

统一接口:
    generate(df, mapping, options) -> ChartResult
"""
import logging
log = logging.getLogger(__name__)
import sys
from pathlib import Path
from typing import Dict, Any, Optional

import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult
from charts.color_schemes import get_color_scheme

__all__ = ["generate"]

_DATA_FMT = "单列数据（频率分布）或双列数据（类别 + 数值，自动生成帕累托图）"
_DESC = "展示数值分布。单列数据显示频率分布直方图；双列数据显示帕累托图（柱子 + 累积曲线）。"


def _auto_col(df: pd.DataFrame, *hints: str) -> Optional[str]:
    """根据提示自动查找匹配的列名。"""
    strs = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    col_lower = {str(c).lower(): c for c in df.columns}
    
    # 1. 精确匹配 hints
    for h in hints:
        h_lower = str(h).lower()
        if h_lower in col_lower:
            return col_lower[h_lower]
    
    # 2. 模糊匹配（包含关系）
    for h in hints:
        h_lower = str(h).lower()
        for col in df.columns:
            col_lower_name = str(col).lower()
            if h_lower in col_lower_name or col_lower_name in h_lower:
                return col
    
    # 3. 类型匹配
    if hints:
        hint = str(hints[0]).lower()
        if any(kw in hint for kw in ["source", "target", "label", "name", "group", "category", "phase", "row", "col", "path", "text", "word", "location", "geo"]):
            if strs:
                return strs[0]
            if nums:
                return nums[0]
        elif any(kw in hint for kw in ["value", "size", "amount", "count", "frequency", "score", "rank", "actual", "target", "range"]):
            if nums:
                return nums[0]
            if strs:
                return strs[0]
        elif hint == "x":
            if strs:
                return strs[0]
            if nums:
                return nums[0]
        elif hint == "y":
            if nums:
                return nums[0]
            if strs:
                return strs[0]
    
    # 4. 无 hints 时自动推断
    if not hints:
        if strs:
            return strs[0]
        if nums:
            return nums[0]
    
    return None


def _build_html(title: str, chart_name: str, library: str,
                data_fmt: str, desc: str, embed: str) -> str:
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


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    # ── 向后兼容旧接口 ──────────────────────────────
    excel_path: str = None,
    x: str = "x",
    title: str = "直方图",
    nbins: int = 30,
    color: str = None,
    **kwargs
) -> ChartResult:
    warnings: list = []
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

    title = options.get("title", title)
    nbins = options.get("nbins", nbins)

    # 获取配色方案
    color_scheme_name = options.get("color_scheme", "mckinsey")
    try:
        color_scheme = get_color_scheme(color_scheme_name)
        primary_color = color_scheme.get("primary", "#003D7A")
        secondary_color = color_scheme.get("secondary", "#0084D1")
    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        warnings.append(f"配色方案加载失败: {e}，使用默认麦肯锡蓝")
        primary_color = "#003D7A"
        secondary_color = "#0084D1"

    # ── 自动检测数据模式 ──────────────────────────────
    n_cols = len(df.columns)
    
    if n_cols == 1:
        # 模式 1：单列数据 → 频率分布直方图
        _x = df.columns[0]
        
        fig = px.histogram(df, x=_x, title=title, nbins=nbins,
                           color_discrete_sequence=[primary_color], **kwargs)
        
        # 应用麦肯锡视觉规范
        fig.update_layout(
            font_family="Heiti SC, Microsoft YaHei, sans-serif",
            plot_bgcolor="white",
            paper_bgcolor="white",
            bargap=0.05,
            margin=dict(l=50, r=50, t=70, b=50),
            title=dict(font=dict(size=16)),
            xaxis=dict(title=dict(font=dict(size=12))),
            yaxis=dict(title=dict(font=dict(size=12))),
            hovermode="x unified",
            showlegend=False
        )
        
        # 优化悬停提示
        fig.update_traces(
            hovertemplate="<b>%{x}</b><br>频次: %{y}<extra></extra>"
        )

        chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
        html = _build_html(title, "histogram_chart", "plotly", _DATA_FMT, _DESC, chart_html)

        meta = {
            "chart_id": "histogram_chart",
            "mode": "frequency_distribution",
            "n_rows": len(df),
            "x_col": _x,
            "nbins": nbins,
            "color_scheme": color_scheme_name,
        }

        return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
    
    elif n_cols >= 2:
        # 模式 2：双列数据 → 帕累托图（柱子 + 累积曲线）
        x_col = mapping.get("x")
        x_col = x_col if x_col is not None else df.columns[0]
        y_col = mapping.get("y")
        y_col = y_col if y_col is not None else df.columns[1]
        
        # 自动检测列
        _x = _auto_col(df, x_col, "类别", "分组", "group", "category")
        _y = _auto_col(df, y_col, "值", "数量", "频次", "value", "count")

        if _x is None or _x not in df.columns:
            _x = df.columns[0]
        if _y is None or _y not in df.columns:
            _y = df.columns[1] if len(df.columns) > 1 else df.columns[0]

        # 准备数据：按 y 值从高到低排序（帕累托原则）
        grouped = df[[_x, _y]].copy()
        grouped = grouped.sort_values(_y, ascending=False)
        
        # 计算累积百分比
        total = grouped[_y].sum()
        if total <= 0:
            return ChartResult(warnings=warnings + ["数值列总和为 0，无法计算帕累托累积百分比"])
        grouped['cumulative'] = grouped[_y].cumsum()
        grouped['cumulative_pct'] = (grouped['cumulative'] / total * 100).round(2)

        # 创建图表
        fig = go.Figure()

        # 添加柱状图（频次）
        fig.add_trace(go.Bar(
            x=grouped[_x],
            y=grouped[_y],
            name="频次",
            marker=dict(color=primary_color),
            yaxis="y1",
            hovertemplate="<b>%{x}</b><br>频次: %{y}<extra></extra>"
        ))

        # 添加累积曲线
        fig.add_trace(go.Scatter(
            x=grouped[_x],
            y=grouped['cumulative_pct'],
            name="累积百分比",
            mode='lines+markers',
            line=dict(color=secondary_color, width=2.5),
            marker=dict(size=6),
            yaxis="y2",
            hovertemplate="<b>%{x}</b><br>累积: %{y}%<extra></extra>"
        ))

        # 配置双 y 轴（坐标轴和文字颜色保持默认，不受配色影响）
        fig.update_layout(
            title=title,
            xaxis=dict(
                title="",
                tickangle=-45,
                tickfont=dict(size=11)
            ),
            yaxis=dict(
                title=dict(text="频次", font=dict(size=12)),
                tickfont=dict(size=11),
                side="left"
            ),
            yaxis2=dict(
                title=dict(text="累积百分比 (%)", font=dict(size=12)),
                tickfont=dict(size=11),
                overlaying="y",
                side="right",
                range=[0, 105]
            ),
            font_family="Heiti SC, Microsoft YaHei, sans-serif",
            plot_bgcolor="white",
            paper_bgcolor="white",
            margin=dict(l=60, r=60, t=80, b=80),
            title_font_size=16,
            hovermode="x unified",
            legend=dict(
                x=0.01,
                y=0.99,
                bgcolor="rgba(255,255,255,0.8)",
                bordercolor="rgba(0,0,0,0.1)",
                borderwidth=1
            )
        )

        chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
        html = _build_html(title, "histogram_chart", "plotly", _DATA_FMT, _DESC, chart_html)

        meta = {
            "chart_id": "histogram_chart",
            "mode": "pareto",
            "n_rows": len(df),
            "x_col": _x,
            "y_col": _y,
            "color_scheme": color_scheme_name,
        }

        return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
    
    else:
        return ChartResult(warnings=["数据列数不足"])
