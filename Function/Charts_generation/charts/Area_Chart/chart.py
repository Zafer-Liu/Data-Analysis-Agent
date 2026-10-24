"""
面积图 Area Chart - 趋势图表
图表分类: 趋势 Trend | 书章节: Ch6
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult

使用示例:
    from charts.area_chart import generate
    from charts import ChartResult

    result = generate(
        df=df,
        mapping={"x": "月份", "y": ["销售额", "成本"]},
        options={"title": "累积趋势"}
    )
"""
import logging
log = logging.getLogger(__name__)
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List, Union

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult
from charts.color_schemes import get_color_scheme

__all__ = ["generate"]

_DATA_FMT = "x列(时间/类别) + y列(数值，支持多列) + 可选series列(分组)"
_DESC = "在折线图基础上用填充区域展示数值，适合累积趋势。支持多列展示和麦肯锡配色。"

# 麦肯锡配色方案
MCKINSEY_COLORS = [
    "#003D7A",  # 深蓝
    "#0084D1",  # 中蓝
    "#00A4EF",  # 浅蓝
    "#7FBA00",  # 绿色
    "#FFB81C",  # 金色
    "#F7630C",  # 橙色
    "#DA3B01",  # 红色
    "#A4373A",  # 深红
    "#6B2C91",  # 紫色
    "#00B4EF",  # 青色
]


def _get_colors_for_scheme(color_scheme_name: str, count: int = 10) -> List[str]:
    """获取指定配色方案的颜色列表"""
    scheme = get_color_scheme(color_scheme_name)
    colors = scheme.get("colors", MCKINSEY_COLORS)
    result = []
    for i in range(count):
        result.append(colors[i % len(colors)])
    return result


def _auto_col(df: pd.DataFrame, role: str, exclude: set = None) -> Optional[str]:
    """根据角色自动查找匹配的列名。
    
    参数：
        role: 'x' (时间/类别) 或 'y' (数值)
        exclude: 已使用的列名集合，避免重复
    
    策略：
    1. 精确匹配常见列名
    2. 类型推断（x优先字符串，y优先数值）
    3. 回退到第一个合适的列
    """
    exclude = exclude or set()
    strs = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c])) and c not in exclude]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c not in exclude]
    col_lower = {str(c).lower(): c for c in df.columns if c not in exclude}
    
    if role == "x":
        # x 优先查找时间/类别列
        time_hints = ["date","time","month","year","week","day","period","Week_Num","时间","日期","月份","年份","周","周数"]
        for hint in time_hints:
            if str(hint).lower() in col_lower:
                return col_lower[str(hint).lower()]
        # 回退到第一个字符串列
        if strs:
            return strs[0]
        # 最后回退到第一个数值列
        if nums:
            return nums[0]
    
    elif role == "y":
        # y 优先查找数值列
        value_hints = ["value", "amount", "sales", "count", "frequency", "数值", "销售额", "销量", "金额","线上","线下"]
        for hint in value_hints:
            if str(hint).lower() in col_lower:
                return col_lower[str(hint).lower()]
        # 回退到第一个数值列
        if nums:
            return nums[0]
        # 最后回退到第一个字符串列
        if strs:
            return strs[0]
    
    return None


def _get_numeric_cols(df: pd.DataFrame, exclude: set = None) -> List[str]:
    """获取所有数值列"""
    exclude = exclude or set()
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c not in exclude]


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
    mapping: Dict[str, Union[str, List[str]]] = None,
    options: Dict[str, Any] = None,
    # ── 向后兼容旧接口 ──────────────────────────────
    excel_path: str = None,
    x: str = "x",
    y: Union[str, List[str]] = "y",
    title: str = "面积图",
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
    y_cols = mapping.get("y")
    y_cols = y_cols if y_cols is not None else y
    title = options.get("title", title)
    color_scheme_name = options.get("color_scheme", "mckinsey")

    # 规范化 y_cols：默认值 "y" 代表未指定
    if y_cols == "y" or y_cols is None:
        y_cols = []
    elif isinstance(y_cols, str):
        y_cols = [y_cols]
    elif not isinstance(y_cols, list):
        y_cols = [str(y_cols)]

    # 自动检测 x 列（优先用户指定，再自动推断）
    if x_col and x_col != "x":
        _x = x_col if x_col in df.columns else _auto_col(df, "x")
    else:
        _x = _auto_col(df, "x")
    
    # 检查 x 列
    if _x is None:
        warnings.append("找不到x列（时间/类别）")
        return ChartResult(warnings=warnings)
    if _x not in df.columns:
        warnings.append(f"x列 '{_x}' 不存在")
        return ChartResult(warnings=warnings)

    # 处理 y 列：支持多列或自动检测
    _y_cols = []
    exclude_for_y = {_x}
    
    for y_col in y_cols:
        if y_col and y_col != "y":
            # 用户指定的列
            if y_col in df.columns:
                _y_cols.append(y_col)
            else:
                warnings.append(f"y列 '{y_col}' 不存在")
        else:
            # 自动检测数值列
            auto_y = _auto_col(df, "y", exclude_for_y)
            if auto_y:
                _y_cols.append(auto_y)
                exclude_for_y.add(auto_y)
    
    # 如果没有找到任何 y 列，尝试获取所有数值列
    if not _y_cols:
        all_nums = _get_numeric_cols(df, exclude_for_y)
        if all_nums:
            _y_cols = all_nums[:5]  # 最多 5 列
        else:
            warnings.append("找不到任何数值列")
            return ChartResult(warnings=warnings)
    
    if len(_y_cols) > 5:
        warnings.append(f"数值列过多({len(_y_cols)}个)，只显示前5列")
        _y_cols = _y_cols[:5]

    try:
        _x = str(_x)
        
        # 检查是否有 series 列（分组）
        series_col = mapping.get("series")
        series_col = series_col if series_col is not None else options.get("series")
        
        # 准备绘图数据
        cols_to_use = [_x] + _y_cols
        if series_col and series_col in df.columns:
            cols_to_use.append(series_col)
        
        df_plot = df[cols_to_use].copy()
        
        # 转换所有 y 列为数值
        for y_col in _y_cols:
            df_plot[y_col] = pd.to_numeric(df_plot[y_col], errors='coerce')
        
        df_plot = df_plot.dropna()
        if df_plot.empty:
            warnings.append("无有效数据")
            return ChartResult(warnings=warnings)
        
        # 创建图表
        if series_col and series_col in df_plot.columns:
            # 分组模式：每个 series 值对应一个子图或颜色
            n_series = df_plot[series_col].nunique()
            if n_series > 3:
                warnings.append(f"分组数过多({n_series}个)，建议 ≤3")
            
            # 使用 facet_col 创建分组面积图
            fig = px.area(
                df_plot, 
                x=_x, 
                y=_y_cols[0] if len(_y_cols) == 1 else None,
                color=series_col,
                title=title,
                **kwargs
            )
        else:
            # 多列模式：堆叠面积图
            if len(_y_cols) == 1:
                fig = px.area(df_plot, x=_x, y=_y_cols[0], title=title, **kwargs)
            else:
                # 多列堆叠 - 获取配色方案
                colors = _get_colors_for_scheme(color_scheme_name, len(_y_cols))
                fig = go.Figure()
                for idx, y_col in enumerate(_y_cols):
                    color = colors[idx % len(colors)]
                    fig.add_trace(go.Scatter(
                        x=df_plot[_x],
                        y=df_plot[y_col],
                        fill='tonexty' if idx > 0 else 'tozeroy',
                        name=y_col,
                        line=dict(width=0.5, color=color),
                        fillcolor=color,
                        opacity=0.7
                    ))
        
        # 应用配色方案
        colors = _get_colors_for_scheme(color_scheme_name, len(_y_cols) if _y_cols else 10)
        if not series_col or series_col not in df_plot.columns:
            # 为单色图表应用配色
            if hasattr(fig, 'data') and fig.data:
                for idx, trace in enumerate(fig.data):
                    color = colors[idx % len(colors)]
                    trace.line.color = color
                    if hasattr(trace, 'fillcolor'):
                        trace.fillcolor = color
        
        # 优化布局
        fig.update_layout(
            font_family="Heiti SC, Microsoft YaHei, sans-serif",
            plot_bgcolor="white",
            paper_bgcolor="white",
            margin=dict(l=50, r=50, t=70, b=50),
            hovermode="x unified",
            xaxis_title=_x,
            yaxis_title="数值" if len(_y_cols) > 1 else _y_cols[0],
            title_font_size=16,
            showlegend=True,
            legend=dict(
                orientation="v",
                yanchor="top",
                y=0.99,
                xanchor="left",
                x=0.01,
                bgcolor="rgba(255,255,255,0.8)",
                bordercolor="rgba(0,0,0,0.1)",
                borderwidth=1
            )
        )
        
        chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
        if not chart_html or len(chart_html) < 100:
            fig_empty = go.Figure()
            fig_empty.add_annotation(text="无有效数据", showarrow=False)
            chart_html = pio.to_html(fig_empty, full_html=False, include_plotlyjs=False)
            warnings.append("图表数据为空")
    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        warnings.append(f"图表生成失败: {e}")
        return ChartResult(warnings=warnings)
    
    html = _build_html(title, "area_chart", "plotly", _DATA_FMT, _DESC, chart_html)


    meta = {
        "chart_id": "area_chart",
        "n_rows": len(df),
        "x_col": _x,
        "y_cols": _y_cols,
        "n_y_cols": len(_y_cols),
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
