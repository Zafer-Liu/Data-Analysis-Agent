"""
点图 Dot Plot - 范围/差异比较
图表分类: 比较 Comparison | 书章节: Ch4
感知排名: ★★★★★

统一接口:
    generate(df, mapping, options) -> ChartResult
"""
import logging
log = logging.getLogger(__name__)
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List

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

_DATA_FMT = "category列 + 两个数值列（起点、终点）"
_DESC = "用圆点和箭头展示两个数据点之间的范围或差异，箭头清晰指向终点，起点和终点用不同蓝色区分。"

# 麦肯锡配色方案
MCKINSEY_COLORS = [
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

# 颜色配置
COLOR_START = "#003D7A"  # 起点 - 深蓝
COLOR_END = "#0084D1"    # 终点 - 中蓝


def _auto_col(df: pd.DataFrame, *hints: str) -> Optional[str]:
    """根据提示自动查找匹配的列名。"""
    strs = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    col_lower = {str(c).lower(): c for c in df.columns}
    
    # 过滤 None 值
    hints = [h for h in hints if h is not None]
    
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


def _get_numeric_cols(df: pd.DataFrame, exclude: set = None) -> List[str]:
    """获取所有数值列（排除指定列）"""
    exclude = exclude or set()
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c not in exclude]


def _build_html(title: str, chart_name: str, library: str,
                data_fmt: str, desc: str, embed: str) -> str:
    import html as _html
    esc = _html.escape
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>{esc(title)}</title>
<style>
body{{font-family:"Heiti SC","Microsoft YaHei",sans-serif;margin:40px;background:#fafafa}}
.chart-wrap{{background:white;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08);padding:24px;margin-bottom:32px}}
h1{{color:#222;font-size:22px;margin-bottom:6px}}
.subtitle{{color:#888;font-size:13px;margin-bottom:24px}}
.desc{{color:#555;font-size:14px;line-height:1.7;margin-top:20px}}
</style></head>
<body><div class="chart-wrap">
<h1>{esc(title)}</h1><div class="subtitle">{esc(chart_name)} | {esc(library)}</div>
{embed}
</div><div class="desc">
<strong>数据格式：</strong>{esc(data_fmt)}<br>
<strong>说明：</strong>{esc(desc)}
</div></body></html>"""


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    x: str = None,
    y: str = None,
    start: str = None,
    end: str = None,
    title: str = "点图",
    color: str = None,
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

    # 参数提取
    title = options.get("title", title)
    # 配色方案：优先用 options.color_scheme
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _palette = _scheme.get("colors") or MCKINSEY_COLORS
    _color_start = _scheme.get("primary", COLOR_START)
    _color_end = _scheme.get("secondary", COLOR_END)
    sort_by = options.get("sort_by", None)
    marker_size = options.get("marker_size", 16)  # 默认改为 14
    arrow_width = options.get("arrow_width", 2)
    show_values = options.get("show_values", False)
    show_legend = options.get("show_legend", True)
    max_categories = options.get("max_categories", 50)

    # 自动检测列。注意：本图的 y 是类别轴（哑铃图横排），不能把 "y" 作为
    # 列提示 —— _auto_col 对 "y" 的类型回退是 nums[0]，会把数值列选成类别，
    # 导致"需要至少2个数值列"误判
    _y = mapping.get("y")
    _y = _y if _y is not None else y
    _y = _auto_col(df, _y, "category", "类别", "产品", "国家", "name", "label")
    
    if _y is None or _y not in df.columns:
        warnings.append("找不到必填字段 [y/category]")
        return ChartResult(warnings=warnings)
    
    # 获取数值列
    num_cols = _get_numeric_cols(df, exclude={_y})
    
    if len(num_cols) < 2:
        warnings.append(f"需要至少2个数值列，当前仅有 {len(num_cols)} 个")
        return ChartResult(warnings=warnings)
    
    # 自动检测起点和终点列
    _start = mapping.get("start")
    _start = _start if _start is not None else (start if start is not None else num_cols[0])
    _end = mapping.get("end")
    _end = _end if _end is not None else (end if end is not None else num_cols[1])
    
    if _start not in df.columns:
        warnings.append(f"找不到起点列 [{_start}]")
        return ChartResult(warnings=warnings)
    if _end not in df.columns:
        warnings.append(f"找不到终点列 [{_end}]")
        return ChartResult(warnings=warnings)
    
    # 数据验证
    n_categories = df[_y].nunique()
    if n_categories > max_categories:
        warnings.append(f"类别数 {n_categories} 超过上限 {max_categories}，建议分组展示")
    
    # 数据准备
    plot_df = df[[_y, _start, _end]].copy()
    plot_df[_start] = pd.to_numeric(plot_df[_start], errors="coerce")
    plot_df[_end] = pd.to_numeric(plot_df[_end], errors="coerce")
    plot_df[_y] = plot_df[_y].astype(str)
    plot_df = plot_df.dropna(subset=[_start, _end])
    
    # 计算变化方向和幅度
    plot_df["_change"] = plot_df[_end] - plot_df[_start]
    plot_df["_mid"] = (plot_df[_start] + plot_df[_end]) / 2
    
    # 排序
    if sort_by == "asc":
        plot_df = plot_df.sort_values(by="_mid", ascending=True)
    elif sort_by == "desc":
        plot_df = plot_df.sort_values(by="_mid", ascending=False)
    
    # 构建图表
    fig = go.Figure()

    # 添加连接线（黑色实线，细）——单 trace + None 分段，避免每行一个 trace
    seg_x: List = []
    seg_y: List = []
    for _, row in plot_df.iterrows():
        seg_x += [row[_start], row[_end], None]
        seg_y += [row[_y], row[_y], None]
    fig.add_trace(go.Scatter(
        x=seg_x,
        y=seg_y,
        mode="lines",
        line=dict(color="#000000", width=1),
        connectgaps=False,
        hoverinfo="skip",
        showlegend=False,
        name=""
    ))
    
    # 添加起点圆点（深蓝）
    fig.add_trace(go.Scatter(
        x=plot_df[_start],
        y=plot_df[_y],
        mode="markers",
        marker=dict(
            size=marker_size,
            color=_color_start,
            opacity=1.0,
            line=dict(width=1.5, color="white")
        ),
        text=[f"<b>{row[_y]}</b><br>{_start}: {row[_start]:.2f}" for _, row in plot_df.iterrows()],
        hovertemplate="%{text}<extra></extra>",
        name=_start,
        showlegend=show_legend
    ))
    
    # 添加终点圆点（中蓝）
    fig.add_trace(go.Scatter(
        x=plot_df[_end],
        y=plot_df[_y],
        mode="markers",
        marker=dict(
            size=marker_size,
            color=_color_end,
            opacity=1.0,
            line=dict(width=1.5, color="white")
        ),
        text=[f"<b>{row[_y]}</b><br>{_end}: {row[_end]:.2f}<br>变化: {row['_change']:+.2f}" 
              for _, row in plot_df.iterrows()],
        hovertemplate="%{text}<extra></extra>",
        name=_end,
        showlegend=show_legend
    ))

    # 将 marker 像素大小近似换算到 x 轴数据单位，用于让箭头落在圆边缘
    x_min = min(plot_df[_start].min(), plot_df[_end].min())
    x_max = max(plot_df[_start].max(), plot_df[_end].max())
    x_span = x_max - x_min if x_max > x_min else 1
    r = x_span * (marker_size / 900)  # 系数可微调：700~1200

    # 添加箭头注解（从起点指向终点）
    for _, row in plot_df.iterrows():
        s = float(row[_start])
        e = float(row[_end])
        d = e - s
        ad = abs(d)

        # 方向
        direction = 1 if d >= 0 else -1

        # 全局半径（你前面算的 r）
        r_global = r

        # 关键：单行半径不超过线段的 25%，避免短差值时偏移过大
        r_row = min(r_global, ad * 0.25)

        # 如果差值极小，直接不画箭头（可选）
        if ad <= 1e-12:
            continue

        x_start_edge = s + direction * r_row
        x_end_edge = e - direction * r_row

        fig.add_annotation(
            x=x_end_edge,
            y=row[_y],
            ax=x_start_edge,
            ay=row[_y],
            xref="x",
            yref="y",
            axref="x",
            ayref="y",
            arrowhead=2,
            arrowsize=1.5,
            arrowwidth=1.5,
            arrowcolor="#000000",
            showarrow=True
        )
    
    # 添加数值标签（可选）
    if show_values:
        # 起点标签
        fig.add_trace(go.Scatter(
            x=plot_df[_start],
            y=plot_df[_y],
            mode="text",
            text=[f"{v:.0f}" for v in plot_df[_start]],
            textposition="middle left",
            textfont=dict(size=9, color="#666666"),
            hoverinfo="skip",
            showlegend=False
        ))
        # 终点标签
        fig.add_trace(go.Scatter(
            x=plot_df[_end],
            y=plot_df[_y],
            mode="text",
            text=[f"{v:.0f}" for v in plot_df[_end]],
            textposition="middle right",
            textfont=dict(size=9, color="#666666"),
            hoverinfo="skip",
            showlegend=False
        ))
    
    # 布局优化
    fig.update_layout(
        title=dict(
            text=title,
            font=dict(size=16, color="#222222"),
            x=0.5,
            xanchor="center"
        ),
        xaxis=dict(
            title="数值",
            title_font=dict(size=12, color="#555555"),
            tickfont=dict(size=11, color="#666666"),
            showgrid=True,
            gridwidth=1,
            gridcolor="#E8E8E8",
            zeroline=False
        ),
        yaxis=dict(
            title=_y,
            title_font=dict(size=12, color="#555555"),
            tickfont=dict(size=11, color="#666666"),
            showgrid=False,
            zeroline=False
        ),
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=150, r=50, t=80, b=60),
        hovermode="closest",
        height=max(400, 50 + n_categories * 30),
        showlegend=show_legend,
        legend=dict(
            x=1.01,
            y=0.99,
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor="rgba(0,0,0,0.1)",
            borderwidth=1
        )
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "dot_plot", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "dot_plot",
        "n_rows": len(plot_df),
        "n_categories": n_categories,
        "y_col": _y,
        "start_col": _start,
        "end_col": _end,
        "sorted": sort_by is not None,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
