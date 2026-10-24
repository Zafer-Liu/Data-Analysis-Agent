"""
柱状图 Bar Chart - 比较图表
图表分类: 比较 Comparisons | 书章节: Ch4
感知排名: ★★★★★

统一接口:
    generate(df, mapping, options) -> ChartResult

使用示例:
    from charts.bar_chart import generate
    from charts import ChartResult

    result = generate(
        df=df,
        mapping={"x": "国家", "y": "GDP"},
        options={"title": "柱状图", "orientation": "v"}
    )
    print(result.html)
    print(result.warnings)
    print(result.meta)
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

FONT_PATH = os.environ.get("CHARTS_FONT_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "AlibabaPuHuiTi-3-55-Regular.ttf"
)

_DATA_FMT = "x列(类别) + y列(数值)"
_DESC = "通过矩形高度编码数值，最常用的比较图表。建议按值降序，y轴从0开始。"


def _auto_col(df: pd.DataFrame, *hints: str) -> Optional[str]:
    """根据提示自动查找匹配的列名。
    
    策略：
    1. 精确匹配 hints 中的任何一个
    2. 模糊匹配（包含关系）
    3. 类型匹配：根据 hint 的语义推断类型
    4. 自动推断：无 hints 时返回第一个合适的列
    """
    # 改进：处理 StringDtype、object 和其他字符串类型
    strs = [c for c in df.columns if 
            df[c].dtype == object or 
            str(df[c].dtype).startswith('string') or
            pd.api.types.is_string_dtype(df[c])]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    col_lower = {str(c).lower(): c for c in df.columns}
    
    # 1. 精确匹配 hints
    for h in hints:
        if h is None:
            continue
        h_lower = str(h).lower()
        if h_lower in col_lower:
            return col_lower[h_lower]
    
    # 2. 模糊匹配（包含关系）
    for h in hints:
        if h is None:
            continue
        h_lower = str(h).lower()
        for col in df.columns:
            col_lower_name = str(col).lower()
            if h_lower in col_lower_name or col_lower_name in h_lower:
                return col
    
    # 3. 类型匹配：根据 hint 的语义推断应该是什么类型
    if hints and hints[0] is not None:
        hint = str(hints[0]).lower()
        # 字符串类型的 hints
        if any(kw in hint for kw in ["source", "target", "label", "name", "group", "category", "phase", "row", "col", "path", "text", "word", "location", "geo", "x"]):
            if strs:
                return strs[0]
            if nums:
                return nums[0]
        # 数值类型的 hints
        elif any(kw in hint for kw in ["value", "size", "amount", "count", "frequency", "score", "rank", "actual", "target", "range", "y"]):
            if nums:
                return nums[0]
            if strs:
                return strs[0]
    
    # 4. 无 hints 时自动推断
    if not hints or all(h is None for h in hints):
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
    y: str = "y",
    orientation: str = "v",
    title: str = "柱状图",
    color: str = "#4C78A8",
    sort: bool = True,
    top_n: int = None,
    **kwargs
) -> ChartResult:
    warnings: list = []
    options = options or {}
    mapping = mapping or {}

    # ── 数据加载 ────────────────────────────────────────
    if df is None:
        if excel_path:
            try:
                df = pd.read_excel(excel_path)
            except Exception as e:
                log.warning("[chart] 图表生成异常: %s", e)
                return ChartResult(warnings=[f"读取Excel失败: {e}"])
        else:
            return ChartResult(warnings=["请提供 df 或 excel_path"])

    # ── 字段解析 ────────────────────────────────────────
    x_col = mapping.get("x")
    x_col = x_col if x_col is not None else x
    y_col = mapping.get("y")
    y_col = y_col if y_col is not None else y
    title = options.get("title", title)
    orientation = options.get("orientation", orientation)
    sort = options.get("sort", sort)
    top_n = options.get("top_n", top_n)
    
    # ── 配色方案 ────────────────────────────────────────
    color_scheme_name = options.get("color_scheme", "mckinsey")
    color_scheme = get_color_scheme(color_scheme_name)
    color = color_scheme.get("primary", color)

    _x = _auto_col(df, x_col, "x", "类别", "category", "label", "name")
    _y = _auto_col(df, y_col, "y", "数值", "value", "amount", "num", "count")

    if _x is None or _x not in df.columns:
        warnings.append("找不到必填字段 [x]")
        return ChartResult(warnings=warnings)
    if _y is None or _y not in df.columns:
        warnings.append("找不到必填字段 [y]")
        return ChartResult(warnings=warnings)

    # 数据处理
    df_plot = df[[_x, _y]].copy()
    df_plot[_y] = pd.to_numeric(df_plot[_y], errors='coerce')
    df_plot = df_plot.dropna()
    
    if df_plot.empty:
        warnings.append("数据为空")
        return ChartResult(warnings=warnings)
    
    if sort:
        df_plot = df_plot.sort_values(_y, ascending=(orientation == "horizontal"))
    if top_n:
        df_plot = df_plot.sort_values(_y, ascending=False).head(top_n)

    fig = px.bar(df_plot, x=_x, y=_y, orientation=orientation,
                 title=title, color_discrete_sequence=[color],
                 text_auto=True, **kwargs)
    
    # ── 应用麦肯锐风格 ────────────────────────────────────────
    fig.update_layout(
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=50, r=50, t=70, b=50),
        title_font_size=16,
        xaxis_title_font_size=12,
        yaxis_title_font_size=12,
        hovermode="x unified"
    )
    fig.update_traces(marker_line_width=0)
    fig.update_layout(font_family="Heiti SC, Microsoft YaHei, sans-serif",
                      plot_bgcolor="white", paper_bgcolor="white",
                      margin=dict(l=40, r=40, t=60, b=40))

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "bar_chart", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "bar_chart",
        "n_rows": len(df_plot),
        "x_col": _x,
        "y_col": _y,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
