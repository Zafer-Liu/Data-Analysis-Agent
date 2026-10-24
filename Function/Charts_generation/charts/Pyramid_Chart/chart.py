"""
金字塔图 Population Pyramid - 分布
图表分类: 分布 Distribution | 书章节: Ch5
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult
"""
import logging
log = logging.getLogger(__name__)
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

_DATA_FMT = "age_group列 + male列 + female列"
_DESC = "人口金字塔图，左右分别展示男性和女性数据，适合人口结构分析。"


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
    age: str = "age",
    male: str = "male",
    female: str = "female",
    title: str = "人口金字塔图",
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

    age_col = mapping.get("age")
    age_col = age_col if age_col is not None else age
    male_col = mapping.get("male")
    male_col = male_col if male_col is not None else male
    female_col = mapping.get("female")
    female_col = female_col if female_col is not None else female
    title = options.get("title", title)

    # 获取配色方案
    color_scheme_name = options.get("color_scheme", "mckinsey")
    try:
        color_scheme = get_color_scheme(color_scheme_name)
        male_color = color_scheme.get("primary", "#003D7A")
        female_color = color_scheme.get("negative", "#DA3B01")
    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        warnings.append(f"配色方案加载失败: {e}，使用默认配色")
        male_color = "#003D7A"
        female_color = "#DA3B01"

    _age = _auto_col(df, age_col, "age", "年龄段", "年龄", "x", "label")
    _male = _auto_col(df, male_col, "male", "男性", "男", "male")
    _female = _auto_col(df, female_col, "female", "女性", "女", "female")

    for role, col_ in [("age", _age), ("male", _male), ("female", _female)]:
        if col_ is None or col_ not in df.columns:
            warnings.append(f"找不到必填字段 [{role}]")

    if _age not in df.columns or _male not in df.columns or _female not in df.columns:
        return ChartResult(warnings=warnings)

    # 数值强制转换：男/女列可能是字符串编码的数字（如 "1,234"），用
    # pd.to_numeric 而非 .astype(float) 以安全处理非数字（避免 ValueError 崩溃）。
    df_plot = df[[_age, _male, _female]].copy()
    df_plot[_male] = pd.to_numeric(df_plot[_male], errors="coerce")
    df_plot[_female] = pd.to_numeric(df_plot[_female], errors="coerce")
    before = len(df_plot)
    df_plot = df_plot.dropna(subset=[_male, _female])
    if before != len(df_plot):
        warnings.append(f"已忽略 {before - len(df_plot)} 行无法解析为数值的男/女人口数据")
    if df_plot.empty:
        return ChartResult(warnings=warnings + ["男/女列不含可用数值"])

    fig = go.Figure()

    # 添加男性柱子（左侧，负值）
    fig.add_trace(go.Bar(
        y=df_plot[_age],
        x=-df_plot[_male],
        orientation="h",
        name="男性",
        marker=dict(color=male_color),
        customdata=df_plot[_male],  # 存储绝对值
        hovertemplate="<b>%{y}</b><br>男性: %{customdata:,.0f}<extra></extra>",
        **kwargs
    ))

    # 添加女性柱子（右侧，正值）
    fig.add_trace(go.Bar(
        y=df_plot[_age],
        x=df_plot[_female],
        orientation="h",
        name="女性",
        marker=dict(color=female_color),
        customdata=df_plot[_female],  # 存储绝对值
        hovertemplate="<b>%{y}</b><br>女性: %{customdata:,.0f}<extra></extra>",
        **kwargs
    ))
    
    # 应用麦肯锡视觉规范
    fig.update_layout(
        title=title,
        barmode="overlay",
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=60, r=60, t=80, b=60),
        title_font_size=16,
        xaxis=dict(
            title=dict(text="男性  ←  人数  →  女性", font=dict(size=12)),
            tickfont=dict(size=11),
        ),
        yaxis=dict(
            title=dict(text="年龄段", font=dict(size=12)),
            tickfont=dict(size=11)
        ),
        hovermode="closest",
        legend=dict(
            x=0.5,
            y=-0.15,
            orientation="h",
            xanchor="center",
            yanchor="top",
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor="rgba(0,0,0,0.1)",
            borderwidth=1
        )
    )
    
    # 自定义 x 轴刻度，显示为绝对值
    x_max = max(float(df_plot[_male].max()), float(df_plot[_female].max()))
    if not (x_max > 0):  # 全 0 或负值时退化为占位刻度，避免 NaN/0 比例破坏坐标轴
        x_max = 1.0
    tick_range = [0, x_max * 0.25, x_max * 0.5, x_max * 0.75, x_max]
    tick_labels = [f"{int(v):,}" for v in tick_range]
    
    # 设置两个方向的刻度（负值和正值）
    fig.update_xaxes(
        tickvals=[-v for v in tick_range[::-1]] + tick_range[1:],
        ticktext=tick_labels[::-1] + tick_labels[1:]
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "pyramid_chart", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "pyramid_chart",
        "n_rows": len(df),
        "age_col": _age,
        "male_col": _male,
        "female_col": _female,
        "color_scheme": color_scheme_name,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
