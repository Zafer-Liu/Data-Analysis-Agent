"""
斜率图 Slope Chart - 变化图表
图表分类: 变化 Change | 书章节: Ch6
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
import plotly.graph_objects as go
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
            "#003D7A", "#0084D1", "#00A4EF", "#7FBA00", "#FFB81C",
            "#F7630C", "#DA3B01", "#A4373A", "#6B2C91", "#00B4EF",
        ]}

__all__ = ["generate"]

_DATA_FMT = "group列 + start列 + end列"
_DESC = "用连线斜率展示从起点到终点的变化，适合比较两个时间点间的差异。"


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
        if any(kw in hint for kw in ["source", "target", "label", "name", "group", "category", "phase", "row", "col", "path", "text", "word", "location", "geo", "item", "start", "end"]):
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


# 麦肯锡配色方案（全局强制）
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


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    # ── 向后兼容旧接口 ──────────────────────────────
    excel_path: str = None,
    group: str = "group",
    start: str = "start",
    end: str = "end",
    title: str = "斜率图",
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

    # Support x/y mapping (LLM typically sends x=group, y=[start,end])
    x_col = mapping.get("x")
    y_cols = mapping.get("y")
    y_cols = y_cols if y_cols is not None else mapping.get("y_cols")
    if isinstance(y_cols, str):
        y_cols = [y_cols]

    if x_col and y_cols and len(y_cols) >= 2:
        group_col = x_col
        start_col = y_cols[0]
        end_col = y_cols[1]
    else:
        group_col = mapping.get("group")
        group_col = group_col if group_col is not None else group
        start_col = mapping.get("start")
        start_col = start_col if start_col is not None else start
        end_col = mapping.get("end")
        end_col = end_col if end_col is not None else end

    def _resolve(col_name: str, role: str, exclude: set = None) -> Optional[str]:
        exclude = exclude or set()
        # None-safe：整数 0 列名是 falsy，不能用 truthiness 判断
        if col_name is not None and col_name in df.columns and col_name not in exclude:
            return col_name
        nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c not in exclude]
        strs = [c for c in df.columns if pd.api.types.is_string_dtype(df[c]) and c not in exclude]
        if role == "group":
            return strs[0] if strs else (nums[0] if nums else None)
        else:
            return nums[0] if nums else None

    _group = _resolve(group_col, "group")
    _start = _resolve(start_col, "start", exclude={_group} if _group else set())
    _end   = _resolve(end_col, "end", exclude={_group, _start} - {None})

    for role, col_ in [("group", _group), ("start", _start), ("end", _end)]:
        if col_ is None or col_ not in df.columns:
            warnings.append(f"找不到必填字段 [{role}]")

    if _group not in df.columns or _start not in df.columns or _end not in df.columns:
        return ChartResult(warnings=warnings)

    # 检查数据行数
    if len(df) > 30:
        warnings.append(f"实体数 {len(df)} 超过建议上限 30，图表可能过于拥挤")

    fig = go.Figure()

    # 配色方案：优先用 options.color_scheme
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _palette = _scheme.get("colors") or _MCKINSEY_COLORS
    _pos_color = _scheme.get("positive", _palette[3 % len(_palette)] if len(_palette) > 3 else "#7FBA00")
    _neg_color = _scheme.get("negative", _palette[6 % len(_palette)] if len(_palette) > 6 else "#DA3B01")

    # 按变化幅度排序，便于视觉对比
    df_sorted = df.copy()
    # 数值强制转换：start/end 可能是字符串编码的数字，直接相减会 TypeError。
    df_sorted[_start] = pd.to_numeric(df_sorted[_start], errors="coerce")
    df_sorted[_end] = pd.to_numeric(df_sorted[_end], errors="coerce")
    before = len(df_sorted)
    df_sorted = df_sorted.dropna(subset=[_start, _end])
    if before != len(df_sorted):
        warnings.append(f"已忽略 {before - len(df_sorted)} 行无法解析为数值的 start/end 数据")
    if df_sorted.empty:
        return ChartResult(warnings=warnings + ["start/end 列不含可用数值"])
    df_sorted["_change"] = df_sorted[_end] - df_sorted[_start]
    df_sorted = df_sorted.sort_values("_change", ascending=False)
    
    for idx, (_, row) in enumerate(df_sorted.iterrows()):
        change = row[_end] - row[_start]
        # 根据变化方向选择颜色：增长用绿色，下降用红色
        color = _pos_color if change >= 0 else _neg_color
        
        fig.add_trace(go.Scatter(
            x=[_start, _end],
            y=[row[_start], row[_end]],
            mode="lines+markers+text",
            text=[str(row[_group]), f"{change:+.1f}"],
            textposition=["middle left", "middle right"],
            textfont=dict(size=10, color="#333"),
            name=str(row[_group]),
            line=dict(color=color, width=3.5),
            marker=dict(size=9, color=color),
            hovertemplate=(
                f"<b>{row[_group]}</b><br>"
                "时间点: %{x}<br>"
                "数值: %{y:.2f}<br>"
                f"变化: {change:+.2f}<extra></extra>"
            ),
            showlegend=False,
        ))
    
    fig.update_xaxes(
        type="category",
        tickvals=[_start, _end],
        tickfont=dict(size=13, color="#333"),
        showgrid=False,
        showline=True,
        linewidth=2,
        linecolor="#333"
    )
    
    fig.update_yaxes(
        showgrid=True,
        gridwidth=1,
        gridcolor="#f0f0f0",
        showline=True,
        linewidth=1,
        linecolor="#ddd",
        tickfont=dict(size=11, color="#666")
    )
    
    fig.update_layout(
        title=dict(text=title, font=dict(size=16, color="#222"), x=0.5, xanchor="center"),
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=150, r=150, t=80, b=60),
        hovermode="closest",
        height=max(600, len(df) * 30 + 250),
        showlegend=False,
        xaxis=dict(scaleanchor="y", scaleratio=2)
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "slope_chart", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "slope_chart",
        "n_rows": len(df),
        "group_col": _group,
        "start_col": _start,
        "end_col": _end,
        "is_wide_format": False,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
