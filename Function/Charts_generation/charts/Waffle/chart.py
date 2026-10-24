"""
华夫格 Waffle Chart - 占比
图表分类: 占比 Proportion | 书章节: Ch4
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

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult
from charts.color_schemes import get_color_scheme

__all__ = ["generate"]

_DATA_FMT = "category列 + value列"
_DESC = "用单元格拼成比例方格图，适合直观展示各分类占整体比例。"

# 麦肯锡配色方案（默认）
MCKINSEY_COLORS = [
    "#003D7A",  # 深蓝 - 主色
    "#0084D1",  # 中蓝 - 次色
    "#00A4EF",  # 浅蓝 - 辅色
    "#7FBA00",  # 绿色 - 正向/增长
    "#FFB81C",  # 金色 - 中性/警示
    "#F7630C",  # 橙色 - 警示
    "#DA3B01",  # 红色 - 负向/下降
    "#A4373A",  # 深红 - 强调/危险
    "#6B2C91",  # 紫色 - 特殊/创新
    "#00B4EF",  # 青色 - 补充
]


def _auto_col(df: pd.DataFrame, *hints: str) -> Optional[str]:
    """根据提示自动查找匹配的列名。
    
    策略：
    1. 精确匹配 hints 中的任何一个
    2. 模糊匹配（包含关系）
    3. 类型匹配：根据 hint 的语义推断类型
    4. 自动推断：无 hints 时返回第一个合适的列
    """
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
    
    # 3. 类型匹配：根据 hint 的语义推断应该是什么类型
    if hints:
        hint = str(hints[0]).lower()
        # 字符串类型的 hints
        if any(kw in hint for kw in ["source", "target", "label", "name", "group", "category", "phase", "row", "col", "path", "text", "word", "location", "geo"]):
            if strs:
                return strs[0]
            if nums:
                return nums[0]
        # 数值类型的 hints
        elif any(kw in hint for kw in ["value", "size", "amount", "count", "frequency", "score", "rank", "actual", "target", "range"]):
            if nums:
                return nums[0]
            if strs:
                return strs[0]
        # 通用的 x/y：x 通常是类别（字符串），y 通常是数值
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
<h1>{title}</h1><div class="subtitle">{chart_name} | plotly</div>
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
    label: str = "label",
    value: str = "value",
    title: str = "华夫格",
    n_cells: int = 10,
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

    label_col = mapping.get("label")
    label_col = label_col if label_col is not None else label
    value_col = mapping.get("value")
    value_col = value_col if value_col is not None else value
    title = options.get("title", title)
    n_cells = options.get("n_cells", n_cells)
    color_scheme_name = options.get("color_scheme", "mckinsey")

    _label = _auto_col(df, label_col, "label", "类别", "产品", "category", "name", "item")
    _value = _auto_col(df, value_col, "value", "销售额", "销量", "amount", "num")

    for role, col_ in [("label", _label), ("value", _value)]:
        if col_ is None or col_ not in df.columns:
            warnings.append(f"找不到必填字段 [{role}]")

    if _label not in df.columns or _value not in df.columns:
        return ChartResult(warnings=warnings)

    # 获取配色方案
    try:
        color_scheme = get_color_scheme(color_scheme_name)
        colors = color_scheme.get("colors", MCKINSEY_COLORS)
    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        warnings.append(f"配色方案加载失败: {e}，使用默认配色")
        colors = MCKINSEY_COLORS

    # 类别数检查（1-6 为建议范围）
    if len(df) > 6:
        warnings.append(f"类别数 {len(df)} > 6，可能影响可读性，建议使用柱状图")

    # 数值强制转换并过滤 NaN：原实现直接 float(row[_value])，遇到 NaN 时
    # int(round(nan)) 抛 ValueError: cannot convert float NaN to integer。
    df_plot = df[[_label, _value]].copy()
    df_plot[_value] = pd.to_numeric(df_plot[_value], errors="coerce")
    before = len(df_plot)
    df_plot = df_plot.dropna(subset=[_value])
    if before != len(df_plot):
        warnings.append(f"已忽略 {before - len(df_plot)} 行无法解析为数值的数据")
    if df_plot.empty:
        return ChartResult(warnings=warnings + ["数值列不含可用数据"])

    total = float(df_plot[_value].sum())
    if total <= 0:
        return ChartResult(warnings=["数值总和为零或负数，无法生成华夫图"])

    n_total_cells = n_cells * n_cells  # 10x10 = 100 个单元格
    fig = go.Figure()

    # 计算每个类别占用的单元格数
    cell_idx = 0
    legend_traces = []  # 用于生成图例

    for ri, (_, row) in enumerate(df_plot.iterrows()):
        category = str(row[_label])
        value = float(row[_value])
        percentage = value / total * 100
        n_filled = int(round(value / total * n_total_cells))
        color = colors[ri % len(colors)]
        
        # 为该类别添加所有单元格
        for _ in range(n_filled):
            r, c = divmod(cell_idx, n_cells)
            fig.add_shape(
                type="rect",
                x0=c, x1=c + 1, y0=-r, y1=-r - 1,
                fillcolor=color,
                line=dict(color="white", width=2),
            )
            cell_idx += 1
        
        # 为图例添加一个不可见的 trace
        fig.add_trace(go.Scatter(
            x=[None], y=[None],
            mode="markers",
            marker=dict(size=12, color=color, symbol="square"),
            name=f"{category} ({percentage:.1f}%)",
            showlegend=True,
            hoverinfo="skip",
        ))
    
    n_rows = int(np.ceil(n_total_cells / n_cells))
    
    fig.update_layout(
        title=dict(
            text=title,
            font=dict(size=16, family="Heiti SC, Microsoft YaHei, sans-serif", color="#222"),
            x=0.5,
            xanchor="center"
        ),
        font=dict(family="Heiti SC, Microsoft YaHei, sans-serif", size=12),
        margin=dict(l=50, r=50, t=70, b=50),
        xaxis=dict(visible=False, range=[-0.5, n_cells + 0.5], scaleanchor="y", scaleratio=1),
        yaxis=dict(visible=False, range=[-n_rows - 0.5, 0.5]),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.05,
            xanchor="center",
            x=0.5,
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor="rgba(0,0,0,0.1)",
            borderwidth=1,
            font=dict(size=12)
        ),
        plot_bgcolor="white",
        paper_bgcolor="white",
        height=max(450, n_rows * 35 + 200),
        hovermode="closest"
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "waffle", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "waffle",
        "n_rows": len(df_plot),
        "label_col": _label,
        "value_col": _value,
        "color_scheme": color_scheme_name,
        "n_cells": n_cells,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
