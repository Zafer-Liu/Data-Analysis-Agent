"""
饼图 Pie Chart - 占比图表
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

_DATA_FMT = "names列 + values列"
_DESC = "展示各部分占整体的比例，适合数量≤5的分类。"


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
    title: str = "饼图",
    color_scheme: str = "mckinsey",
    **kwargs
) -> ChartResult:
    warnings: list = []
    options = options or {}
    mapping = mapping or {}

    # 从 options 中获取参数
    if 'color_scheme' in options:
        color_scheme = options['color_scheme']

    # 获取配色方案
    scheme = get_color_scheme(color_scheme)
    colors = scheme.get('colors', ['#003D7A', '#0055CC', '#0077FF', '#33A1FF'])

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
    # None-safe 读取：整数 0 列名在 or 链中是 falsy 会被吞。
    names_col = mapping.get("label")
    if names_col is None:
        names_col = mapping.get("names")
    if names_col is None:
        names_col = mapping.get("name")
    if names_col is None:
        names_col = names
    values_col = mapping.get("value")
    if values_col is None:
        values_col = mapping.get("values")
    if values_col is None:
        values_col = values
    title = options.get("title", title)

    _names = _auto_col(df, names_col, "label", "name", "names", "产品", "类别", "category")
    # 查找 values 列时排除已选中的 names 列，避免两者映射到同一列
    _df_for_values = df.drop(columns=[_names]) if _names and _names in df.columns else df
    _values = _auto_col(_df_for_values, values_col, "value", "销售额", "销量", "amount", "num", "count")

    for role, col_ in [("names", _names), ("values", _values)]:
        if col_ is None or col_ not in df.columns:
            warnings.append(f"找不到必填字段 [{role}]")

    if _names not in df.columns or _values not in df.columns:
        return ChartResult(warnings=warnings)

    # ── 数据验证 ──────────────────────────────────────
    # 1. 检查数据点数量
    n_rows = len(df)
    if n_rows < 2:
        warnings.append(f"⚠️ 数据点过少（{n_rows}个）。建议至少2个数据点。")
    
    # 2. 检查类别数量
    if n_rows > 7:
        warnings.append(f"⚠️ 类别过多（{n_rows}个）。建议≤7个类别，否则会导致视觉混乱。建议用柱状图。")
    elif n_rows > 5:
        warnings.append(f"⚠️ 类别较多（{n_rows}个）。建议≤5个类别以获得最佳视觉效果。")
    
    # 3. 检查负数 / NaN（先转数值再判断，避免把 NaN 误报成“负数”）
    try:
        values_numeric = pd.to_numeric(df[_values], errors='coerce')
        neg_mask = values_numeric < 0
        nan_mask = values_numeric.isna()
        if neg_mask.any():
            warnings.append("⚠️ 数据中包含负数。饼图不支持负数，请过滤或转换数据。")
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

    # 按数值大小降序排列
    df = df.sort_values(by=_values, ascending=False)

    fig = go.Figure(data=[go.Pie(
        labels=df[_names].astype(str).tolist(),
        values=df[_values].astype(float).tolist(),
        marker=dict(colors=colors),
        hovertemplate=f'<b>%{{label}}</b><br>{_values}: %{{value}}<br>占比: %{{percent}}<extra></extra>',
    )])
    
    fig.update_layout(
        title=dict(text=title, font=dict(color='#333')),
        font=dict(family="Heiti SC, Microsoft YaHei, sans-serif", color='#333'),
        margin=dict(l=40, r=40, t=60, b=40)
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "pie", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "pie",
        "n_rows": len(df),
        "names_col": _names,
        "values_col": _values,
        "color_scheme": color_scheme,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
