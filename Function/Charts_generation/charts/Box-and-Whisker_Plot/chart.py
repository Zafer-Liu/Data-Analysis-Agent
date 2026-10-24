"""
箱线图 Box Plot - 分布图表
图表分类: 分布 Distribution | 书章节: Ch5
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
import plotly.express as px
import plotly.io as pio

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult
from charts.color_schemes import get_color_scheme

__all__ = ["generate"]

_DATA_FMT = "y列(数值) + [可选: x列(分类)]"
_DESC = "展示中位数、四分位数和异常值，适合比较多个组别的分布。"

# 麦肯锡配色（默认）
_MCKINSEY_COLORS = [
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
    x: str = None,
    y: str = "y",
    title: str = "箱线图",
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

    x_col = mapping.get("x")
    x_col = x_col if x_col is not None else x
    y_col = mapping.get("y")
    y_col = y_col if y_col is not None else y
    title = options.get("title", title)
    color_scheme_name = options.get("color_scheme", "mckinsey")

    # 检测宽格式数据（仅在用户未显式指定 x/y 映射时才自动检测，
    # 避免 melt 覆盖用户意图）
    # 宽格式：第一列为分类，其余列为数值
    is_wide_format = False
    first_col = df.columns[0]
    numeric_cols = [c for c in df.columns[1:] if pd.api.types.is_numeric_dtype(df[c])]
    user_mapping = any(v is not None for v in (x_col, y_col)) or bool(mapping)

    if (
        not user_mapping
        and len(numeric_cols) > 1
        and pd.api.types.is_string_dtype(df[first_col])
    ):
        # 这是宽格式数据
        is_wide_format = True
        _x = first_col
        # 将宽格式转换为长格式
        df_melted = df.melt(id_vars=[first_col], value_vars=numeric_cols,
                            var_name="sample", value_name="value")
        df_melted = df_melted.dropna(subset=["value"])
        _y = "value"
        plot_df = df_melted
    else:
        # 长格式数据
        _x = _auto_col(df, x_col, "x", "区域", "类别", "group")
        _y = _auto_col(df, y_col, "y", "价格", "销售额", "value", "num")
        plot_df = df

    if _y is None or _y not in plot_df.columns:
        warnings.append("找不到必填字段 [y]")
        return ChartResult(warnings=warnings)

    if _x and _x not in plot_df.columns:
        _x = None

    # 获取配色方案
    try:
        color_scheme = get_color_scheme(color_scheme_name)
        primary_color = color_scheme.get("primary", "#003D7A")
    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        warnings.append(f"配色方案加载失败: {e}，使用默认配色")
        primary_color = "#003D7A"

    # 生成箱线图
    fig = px.box(plot_df, x=_x, y=_y, title=title, **kwargs)
    
    # 应用配色方案到箱线图
    fig.update_traces(
        marker=dict(color=primary_color),
        line=dict(color=primary_color),
        boxmean=False,  # 不显示均值线
        boxpoints='outliers'  # 显示离群点
    )
    
    # 应用麦肯锡风格
    fig.update_layout(
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=50, r=50, t=70, b=50),
        title=dict(font=dict(size=16)),
        xaxis=dict(
            title=dict(font=dict(size=12)),
            tickfont=dict(size=11),
            showgrid=False,
            zeroline=False
        ),
        yaxis=dict(
            title=dict(font=dict(size=12)),
            tickfont=dict(size=11),
            showgrid=True,
            gridwidth=1,
            gridcolor="rgba(0,0,0,0.05)",
            zeroline=False
        ),
        hovermode="closest"
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "boxplot_chart", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "boxplot_chart",
        "n_rows": len(df),
        "x_col": _x,
        "y_col": _y,
        "is_wide_format": is_wide_format,
        "color_scheme": color_scheme_name,
    }

    result = ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
    
    # 验证结果有效性
    if not result.is_valid():
        warnings.append("图表生成可能不完整")
    
    return result
