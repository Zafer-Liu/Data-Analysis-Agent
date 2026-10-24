"""
南丁格尔玫瑰图 Nightingale Rose - 占比图表
图表分类: 占比 Proportion | 书章节: Ch4
感知排名: ★★★☆☆

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
import plotly.io as pio

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult
from charts.color_schemes import get_color_scheme

__all__ = ["generate"]

_DATA_FMT = "category列 + value列 [+ time列]"
_DESC = "极坐标扇形面积表示数值，适合周期性数据。"


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
    names: str = "name",
    values: str = "value",
    title: str = "南丁格尔玫瑰图",
    color_scheme: str = "mckinsey",
    **kwargs
) -> ChartResult:
    warnings: list = []
    options = options or {}
    mapping = mapping or {}
    
    # 从 options 中获取 color_scheme，如果没有则使用参数值
    if 'color_scheme' in options:
        color_scheme = options['color_scheme']
    
    # 获取配色方案
    scheme = get_color_scheme(color_scheme)
    primary_color = scheme.get('primary', '#003D7A')

    if df is None:
        if excel_path:
            try:
                df = pd.read_excel(excel_path)
            except Exception as e:
                log.warning("[chart] 图表生成异常: %s", e)
                return ChartResult(warnings=[f"读取Excel失败: {e}"])
        else:
            return ChartResult(warnings=["请提供 df 或 excel_path"])

    # 调度器把 aliases 归一化：labels→label、values→value（chart_generate.py:76-80）。
    # 同时保留旧别名 names/name/values 以兼容直接调用与旧 payload。
    names_col = (mapping.get("label") or mapping.get("names")
                 or mapping.get("name") or names)
    values_col = mapping.get("value")
    values_col = values_col if values_col is not None else (mapping.get("values") if mapping.get("values") is not None else values)
    title = options.get("title", title)

    _names = _auto_col(df, names_col, "label", "name", "names", "月份", "类别", "category")
    _values = _auto_col(df, values_col, "value", "销售额", "销量", "amount", "num")

    for role, col_ in [("names", _names), ("values", _values)]:
        if col_ is None or col_ not in df.columns:
            warnings.append(f"找不到必填字段 [{role}]")

    if _names not in df.columns or _values not in df.columns:
        return ChartResult(warnings=warnings)

    # ── 数据验证 ──────────────────────────────────────
    # 1. 检查数据点数量
    n_rows = len(df)
    if n_rows < 4:
        warnings.append(f"⚠️ 数据点过少（{n_rows}个）。建议至少4个数据点，否则建议用柱状图或饼图。")
    
    # 2. 检查类别数量
    if n_rows > 12:
        warnings.append(f"⚠️ 类别过多（{n_rows}个）。建议≤12个类别，否则会导致视觉混乱。建议用柱状图。")
    
    # 3. 检查负数 / NaN（先转数值再判断，避免把 NaN 误报成“负数”）
    try:
        values_numeric = pd.to_numeric(df[_values], errors='coerce')
        neg_mask = values_numeric < 0
        nan_mask = values_numeric.isna()
        if neg_mask.any():
            warnings.append("⚠️ 数据中包含负数。南丁格尔玫瑰图不支持负数，请过滤或转换数据。")
        if nan_mask.any() and not neg_mask.any():
            warnings.append(f"⚠️ 数据中包含 {int(nan_mask.sum())} 个无法解析为数值的空值，已自动删除。")
        # 过滤负数与 NaN
        keep = values_numeric.notna() & (values_numeric >= 0)
        if (~keep).any():
            df = df[keep].copy()
            if len(df) == 0:
                return ChartResult(warnings=warnings + ["❌ 过滤后无有效数据"])
    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        warnings.append(f"⚠️ 数值转换失败: {e}")
    
    # 4. 检查空值
    if df[_names].isna().any() or df[_values].isna().any():
        warnings.append("⚠️ 数据中包含空值，已自动删除。")
        df = df.dropna(subset=[_names, _values])
        if len(df) == 0:
            return ChartResult(warnings=warnings + ["❌ 删除空值后无有效数据"])

    values_list = df[_values].astype(float).tolist()
    names_list = [str(v) for v in df[_names]]

    # 创建主图（条形）
    fig = go.Figure(data=[go.Barpolar(
        r=values_list,
        theta=names_list,
        marker=dict(color=primary_color),
        hovertemplate=f'<b>%{{theta}}</b><br>{_values}: %{{r:.1f}}<extra></extra>',
        **kwargs)]
    )

    fig.update_layout(
        title=dict(text=title, font=dict(color='#333')),
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        font=dict(color='#333'),
        margin=dict(l=40, r=40, t=60, b=40),
        polar=dict(
            radialaxis=dict(visible=True, gridcolor='#e0e0e0', tickcolor='#333'),
            angularaxis=dict(gridcolor='#e0e0e0', tickcolor='#333')
        )
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "nightingale", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "nightingale",
        "n_rows": len(df),
        "names_col": _names,
        "values_col": _values,
        "color_scheme": color_scheme,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
