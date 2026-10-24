"""
迷你图 Sparkline - 趋势图表
图表分类: 趋势 Trend | 书章节: Ch6
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult
"""
import logging
log = logging.getLogger(__name__)
import html as _html
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

_DATA_FMT = "宽格式：首列为行标签，其余列为数值序列"
_DESC = "为每一行数据生成一个极简趋势迷你图，适合嵌入表格。"

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


def _build_sparkline_html(label: str, y_vals: list, trend_up: bool, up_color: str = None, down_color: str = None) -> str:
    """为单行数据生成迷你图 HTML"""
    line_color = (up_color or _MCKINSEY_COLORS[3]) if trend_up else (down_color or _MCKINSEY_COLORS[6])
    
    # 转换 hex 颜色为 rgba
    r = int(line_color[1:3], 16)
    g = int(line_color[3:5], 16)
    b = int(line_color[5:7], 16)
    fill_color = f"rgba({r},{g},{b},0.15)"
    
    fig = go.Figure(go.Scatter(
        y=y_vals,
        mode="lines+markers",
        line=dict(color=line_color, width=2.5),
        marker=dict(size=4, color=line_color),
        fill="tozeroy",
        fillcolor=fill_color,
        hovertemplate="<b>值: %{y:.2f}</b><extra></extra>",
        showlegend=False
    ))
    
    fig.update_layout(
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=4, r=4, t=4, b=4),
        xaxis=dict(showticklabels=False, showgrid=False, zeroline=False),
        yaxis=dict(showticklabels=False, showgrid=False, zeroline=False),
        height=60,
        width=200,
        showlegend=False,
        hovermode="x unified"
    )
    
    return pio.to_html(fig, full_html=False, include_plotlyjs=False)


def _build_html(title: str, chart_name: str, library: str,
                data_fmt: str, desc: str, table_html: str) -> str:
    esc = _html.escape
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>{esc(title)}</title>
<style>
body{{font-family:"Heiti SC","Microsoft YaHei",sans-serif;margin:40px;background:#fafafa}}
.chart-wrap{{background:white;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08);padding:24px;margin-bottom:32px}}
h1{{color:#222;font-size:22px;margin-bottom:6px}}
.subtitle{{color:#888;font-size:13px;margin-bottom:24px}}
table{{width:100%;border-collapse:collapse;margin:20px 0}}
th,td{{padding:12px;text-align:left;border-bottom:1px solid #eee}}
th{{background:#f5f5f5;font-weight:600;color:#333}}
td{{color:#666}}
.sparkline-cell{{width:220px}}
.desc{{color:#555;font-size:14px;line-height:1.7;margin-top:20px}}
</style></head>
<body><div class="chart-wrap">
<h1>{esc(title)}</h1><div class="subtitle">{esc(chart_name)} | {esc(library)}</div>
{table_html}
</div><div class="desc">
<strong>数据格式：</strong>{esc(data_fmt)}<br>
<strong>说明：</strong>{esc(desc)}
</div></body></html>"""


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    # ── 向后兼容旧接口 ──────────────────────────────
    excel_path: str = None,
    label: str = None,
    title: str = "迷你图表",
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

    # 配色方案：优先用 options.color_scheme
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _palette = _scheme.get("colors") or _MCKINSEY_COLORS
    _up_color = _scheme.get("positive", _palette[3 % len(_palette)] if len(_palette) > 3 else "#7FBA00")
    _down_color = _scheme.get("negative", _palette[6 % len(_palette)] if len(_palette) > 6 else "#DA3B01")

    # 自动检测标签列（首列通常是标签）
    label_col = mapping.get("label")
    label_col = label_col if label_col is not None else (label if label is not None else df.columns[0])
    if label_col not in df.columns:
        label_col = df.columns[0]
    
    # 获取数值列（除了标签列的所有列）
    numeric_cols = [c for c in df.columns if c != label_col and pd.api.types.is_numeric_dtype(df[c])]
    
    if not numeric_cols:
        warnings.append("找不到数值列")
        return ChartResult(warnings=warnings)
    
    # 数值列 coerce（处理对象 dtype 的字符串数字/缺失值），含 NaN 的行跳过
    work = df.copy()
    for c in numeric_cols:
        work[c] = pd.to_numeric(work[c], errors="coerce")
    n_nan = int(work[numeric_cols].isna().any(axis=1).sum())
    if n_nan:
        warnings.append(f"检测到 {n_nan} 行含非数值/缺失单元格，已跳过")
        work = work.dropna(subset=numeric_cols)
    if work.empty:
        return ChartResult(warnings=warnings + ["无有效数据行"])

    # 生成表格 HTML（列名/标签值均转义，防注入）
    table_html = "<table><thead><tr><th>标签</th>"
    for col in numeric_cols:
        table_html += f"<th>{_html.escape(str(col))}</th>"
    table_html += "<th class='sparkline-cell'>趋势</th></tr></thead><tbody>"

    sparklines = []
    for idx, row in work.iterrows():
        label_val = str(row[label_col])
        y_vals = [float(row[col]) for col in numeric_cols]
        
        # 判断趋势
        trend_up = y_vals[-1] >= y_vals[0]
        change = y_vals[-1] - y_vals[0]
        
        # 生成迷你图
        sparkline_html = _build_sparkline_html(label_val, y_vals, trend_up, _up_color, _down_color)
        sparklines.append({
            "label": label_val,
            "html": sparkline_html,
            "trend": "up" if trend_up else "down",
            "change": change,
            "start": y_vals[0],
            "end": y_vals[-1]
        })
        
        # 添加表格行
        esc_label = _html.escape(label_val)
        table_html += f"<tr><td><strong>{esc_label}</strong></td>"
        for col in numeric_cols:
            table_html += f"<td>{row[col]:.1f}</td>"
        table_html += f"<td class='sparkline-cell'>{sparkline_html}</td></tr>"
    
    table_html += "</tbody></table>"
    
    html = _build_html(title, "sparkline", "plotly", _DATA_FMT, _DESC, table_html)

    meta = {
        "chart_id": "sparkline",
        "n_rows": len(work),
        "label_col": label_col,
        "numeric_cols": numeric_cols,
        "sparklines": sparklines,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
