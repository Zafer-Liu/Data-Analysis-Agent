"""
堆叠柱状图 Stacked Bar - 比较图表
图表分类: 比较 Comparison
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

try:
    from charts.color_schemes import get_color_scheme
except ImportError as e:  # pragma: no cover - 兜底，保证独立调用可用
    log.debug("[chart] 配色模块加载失败: %s", e)
    def get_color_scheme(name: str = "mckinsey"):
        return {"colors": [
            "#0B1F3A", "#005B9A", "#00A3E0", "#6E7B8B", "#B9C2CF",
            "#7D8998", "#4A90E2", "#2E3A46",
        ]}

__all__ = ["generate"]

_DATA_FMT = "x列(类别) + 分组列 + y列(数值) 或 宽格式(行标签 + 多个数值列)"
_DESC = "多个分组堆叠在同一柱内，适合比较各部分对整体的贡献。支持宽格式数据自动转换。"

# 配色：蓝系 + 中性色 + 强调色
_MCKINSEY_COLORS = [
    "#0B1F3A",  # 深海军蓝
    "#005B9A",  # 麦肯锡蓝
    "#00A3E0",  # 亮蓝
    "#6E7B8B",  # 蓝灰
    "#B9C2CF",  # 浅灰蓝
    "#7D8998",  # 中性灰蓝
    "#4A90E2",  # 强调蓝
    "#2E3A46",  # 深灰
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
    hints = tuple(h for h in hints if isinstance(h, str) and h)
    
    # 1. 精确匹配 hints
    for h in hints:
        h_lower = str(h).lower()
        if h_lower in col_lower:
            return col_lower[h_lower]
    
    # 2. 模糊匹配（包含关系）
    for h in hints:
        h_lower = str(h).lower()
        if len(h_lower) < 2:
            continue
        for col in df.columns:
            col_lower_name = str(col).lower()
            if len(col_lower_name) >= 2 and (
                h_lower in col_lower_name or col_lower_name in h_lower
            ):
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


def _detect_wide_format(df: pd.DataFrame, mapping: dict) -> bool:
    """检测是否为宽格式数据（1个字符串列 + 多个数值列）"""
    if mapping.get("value_cols") or isinstance(mapping.get("y"), list) \
            or isinstance(mapping.get("series"), list):
        return True

    series_hint = mapping.get("series")
    series_hint = series_hint if series_hint is not None else mapping.get("color")
    if isinstance(series_hint, str) and series_hint in df.columns:
        return False

    strs = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    
    # 宽格式特征：恰好1个字符串列，2个以上数值列
    return len(strs) == 1 and len(nums) >= 2


def _convert_wide_to_long(df: pd.DataFrame, id_col: str,
                          value_cols: list = None) -> pd.DataFrame:
    """将宽格式转换为长格式
    
    输入：
        行标签  15    16    17    18    19
        标签二  36.39 31.97 29.03 28.11 25.46
        标签三  49.72 44.48 35.79 35.32 30.22
    
    输出：
        行标签  周期  数值
        标签二  15   36.39
        标签二  16   31.97
        ...
    """
    # 获取所有数值列（按原始顺序）
    if value_cols is None:
        value_cols = [c for c in df.columns
                      if c != id_col and pd.api.types.is_numeric_dtype(df[c])]
    
    # melt：将宽格式转为长格式
    df_long = df.melt(
        id_vars=[id_col],
        value_vars=value_cols,
        var_name="分类",
        value_name="数值"
    )
    
    return df_long


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
    color: str = "color",
    title: str = "堆叠柱状图",
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
    color_col = mapping.get("color")
    color_col = color_col if color_col is not None else (mapping.get("series") if mapping.get("series") is not None else color)
    title = options.get("title", title)

    # ── 检测并转换宽格式数据 ──────────────────────────────
    if _detect_wide_format(df, mapping):
        # 找到字符串列作为 id_col
        x_hint = mapping.get("x")
        x_hint = x_hint if x_hint is not None else x_col
        string_cols = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
        id_col = x_hint if isinstance(x_hint, str) and x_hint in df.columns else (
            string_cols[0] if string_cols else df.columns[0]
        )
        value_cols = mapping.get("value_cols")
        if not value_cols and isinstance(mapping.get("y"), list):
            value_cols = mapping["y"]
        if not value_cols and isinstance(mapping.get("series"), list):
            value_cols = mapping["series"]
        if value_cols:
            value_cols = [c for c in value_cols if c in df.columns and c != id_col]
        df = _convert_wide_to_long(df, id_col, value_cols or None)
        
        # 转换后的长格式：id_col, 分类, 数值
        _x = id_col  # 行标签作为 x
        _y = "数值"  # 数值列
        _color = "分类"  # 分类作为堆叠
        
        warnings.append(f"自动转换宽格式数据：{id_col} (x) × 分类 (stack) × 数值 (y)")
    else:
        # 长格式数据：正常处理
        _x = _auto_col(df, x_col, "x", "季度", "时间", "类别", "行标签")
        _y = _auto_col(df, y_col, "y", "销售额", "销量", "value", "amount", "数值")
        if isinstance(color_col, str) and color_col in df.columns:
            _color = color_col
        elif "series" in df.columns:
            _color = "series"
        elif "color" in df.columns:
            _color = "color"
        else:
            _color = _auto_col(
                df, color_col, "color", "产品", "区域", "group", "category", "分类"
            )

    for role, col_ in [("x", _x), ("y", _y)]:
        if col_ is None or col_ not in df.columns:
            warnings.append(f"找不到必填字段 [{role}]")

    if _x is None or _x not in df.columns:
        warnings.append("找不到必填字段 [x]")
        return ChartResult(warnings=warnings)
    if _y is None or _y not in df.columns:
        warnings.append("找不到必填字段 [y]")
        return ChartResult(warnings=warnings)

    if _color and _color not in df.columns:
        _color = None

    # 数值强制转换：长格式 _y 可能是字符串编码的数字（如 "1,234"），
    # px.bar 对 object dtype 的字符串可能失败或错误渲染。
    df = df.copy()
    df[_y] = pd.to_numeric(df[_y], errors="coerce")
    before = len(df)
    df = df.dropna(subset=[_y])
    if before != len(df):
        warnings.append(f"已忽略 {before - len(df)} 行无法解析为数值的 y 数据")
    if df.empty:
        return ChartResult(warnings=warnings + ["y 列不含可用数值"])

    # 配色方案：优先用 options.color_scheme，回退到 _MCKINSEY_COLORS
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _palette = _scheme.get("colors") or _MCKINSEY_COLORS

    fig = px.bar(
        df,
        x=_x,
        y=_y,
        color=_color,
        title=title,
        barmode="stack",
        text_auto=".2f",
        color_discrete_sequence=_palette,
        **kwargs
    )

    fig.update_layout(
        font_family="Arial, Helvetica, sans-serif",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=40, r=40, t=60, b=40),
        xaxis_title=_x,
        yaxis_title=_y,
        legend_title=_color if _color else "",
    )
    fig.update_xaxes(showgrid=False, linecolor="#D9D9D9")
    fig.update_yaxes(showgrid=True, gridcolor="#E6E9EF", zeroline=False)

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "stacked_bar", "plotly", _DATA_FMT, _DESC, chart_html)


    meta = {
        "chart_id": "stacked_bar",
        "n_rows": len(df),
        "x_col": _x,
        "y_col": _y,
        "color_col": _color,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
