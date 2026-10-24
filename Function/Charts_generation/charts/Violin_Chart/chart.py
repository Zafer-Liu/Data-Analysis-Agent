"""
小提琴图 Violin Chart - 分布图表
图表分类: 分布 Distribution | 书章节: Ch5
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
import plotly.express as px
import plotly.io as pio

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult
from charts.color_schemes import get_color_scheme

__all__ = ["generate"]

_DATA_FMT = "y列(数值) + [可选: x列(分类)]"
_DESC = "结合箱线图和密度估计，展示分布形状，比箱线图更丰富。"


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


def _is_wide_format(df: pd.DataFrame) -> bool:
    """检测是否为宽格式数据（第一列为分类，其余为数值，且列名不是标准长格式列名）。"""
    if len(df.columns) < 3:  # 宽格式至少需要3列（分类+2个数值列）
        return False
    
    first_col = df.columns[0]
    rest_cols = df.columns[1:]
    
    # 第一列是字符串类型，其余列是数值类型
    is_first_str = pd.api.types.is_string_dtype(df[first_col])
    rest_numeric = all(pd.api.types.is_numeric_dtype(df[c]) for c in rest_cols)
    
    if not (is_first_str and rest_numeric):
        return False
    
    # 排除已经是长格式的情况（如 group/value, x/y 等标准列名）
    standard_long_cols = {'group', 'value', 'x', 'y', 'category', 'measure', 'variable', 'sample'}
    rest_cols_lower = {str(c).lower() for c in rest_cols}
    
    # 如果其余列中有标准长格式列名，则不认为是宽格式
    if rest_cols_lower & standard_long_cols:
        return False
    
    return True


def _wide_to_long(df: pd.DataFrame) -> pd.DataFrame:
    """将宽格式数据转换为长格式。"""
    id_col = df.columns[0]
    value_cols = df.columns[1:]
    df_long = df.melt(id_vars=[id_col], value_vars=value_cols,
                      var_name="sample", value_name="value")
    df_long = df_long.rename(columns={id_col: "group"})
    return df_long


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    # ── 向后兼容旧接口 ──────────────────────────────
    excel_path: str = None,
    x: str = None,
    y: str = "y",
    title: str = "小提琴图",
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

    # 检测并转换宽格式数据
    is_wide = _is_wide_format(df)
    if is_wide:
        df = _wide_to_long(df)
        x_col = "group"
        y_col = "value"
        warnings.append("检测到宽格式数据，已自动转换为长格式")
    else:
        x_col = mapping.get("x")
        x_col = x_col if x_col is not None else x
        y_col = mapping.get("y")
        y_col = y_col if y_col is not None else y

    title = options.get("title", title)

    _x = _auto_col(df, x_col, "x", "区域", "类别", "group") if x_col else None
    _y = _auto_col(df, y_col, "y", "价格", "销售额", "value", "num")

    if _y is None or _y not in df.columns:
        warnings.append("找不到必填字段 [y]")
        return ChartResult(warnings=warnings)

    if _x and _x not in df.columns:
        _x = None

    # 获取配色方案
    color_scheme_name = options.get("color_scheme", "mckinsey")
    color_scheme = get_color_scheme(color_scheme_name)
    primary_color = color_scheme.get("primary", "#003D7A")

    fig = px.violin(df, x=_x, y=_y, title=title,
                    box=True, points="outliers", **kwargs)
    
    # 应用配色方案
    fig.update_traces(marker=dict(color=primary_color))
    
    fig.update_layout(
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=40, r=40, t=60, b=40),
        xaxis=dict(tickfont=dict(size=11)),
        yaxis=dict(tickfont=dict(size=11))
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "violin_chart", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "violin_chart",
        "n_rows": len(df),
        "x_col": _x,
        "y_col": _y,
        "is_wide_format": is_wide,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
