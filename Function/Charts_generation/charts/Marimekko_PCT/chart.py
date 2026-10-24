"""
马赛克图 Marimekko - 比较图表
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

_DATA_FMT = "x列(类别) + y列(数值) + Z列(数值)"
_DESC = "在柱状图的基础上，通过改变柱宽引入第二变量"

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
.info{{background:#f5f5f5;padding:12px;border-left:4px solid #003D7A;margin:16px 0;font-size:13px;color:#666}}
</style></head>
<body><div class="chart-wrap">
<h1>{title}</h1><div class="subtitle">{chart_name} | {library}</div>
{embed}
<div class="info">
<strong>数据格式：</strong>{data_fmt}<br>
<strong>说明：</strong>{desc}
</div>
</div></body></html>"""


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    x: str = "x",
    y: str = "y",
    group: str = None,
    title: str = "马赛克图_标准版",
    **kwargs
) -> ChartResult:
    warnings: list = []
    options = options or {}
    mapping = mapping or {}

    # ---- 用户可传显示标签（优先级：mapping > options）----
    x_label_user = mapping.get("x_label")
    x_label_user = x_label_user if x_label_user is not None else options.get("x_label")
    y_label_user = mapping.get("y_label")
    y_label_user = y_label_user if y_label_user is not None else options.get("y_label")
    group_label_user = mapping.get("group_label")
    group_label_user = group_label_user if group_label_user is not None else options.get("group_label")

    if df is None:
        if excel_path:
            try:
                df = pd.read_excel(excel_path)
            except Exception as e:
                log.warning("[chart] 图表生成异常: %s", e)
                return ChartResult(warnings=[f"读取Excel失败: {e}"])
        else:
            return ChartResult(warnings=["请提供 df 或 excel_path"])

    if df is None or df.empty:
        return ChartResult(warnings=["输入数据为空"])

    title = options.get("title", title)

    # -------- 1) 识别宽格式 / 长格式 --------
    # 宽格式判定：除首列外其余列都是数值列，且至少 2 个数值列。
    # 2 列长格式 [类别, 值] 首列字符串 + 单数值列会被误判为宽表，
    # melt 后只有单一 series，不是有意义的马赛克宽表。
    is_wide_format = False
    if len(df.columns) > 2:
        first_col = df.columns[0]
        rest_cols = df.columns[1:]
        if len(rest_cols) >= 2 and all(pd.api.types.is_numeric_dtype(df[c]) for c in rest_cols):
            is_wide_format = True

    if is_wide_format:
        # 宽格式：首列为 x，其余列为 group，值为 value
        x_col_actual = df.columns[0]
        data = df.melt(id_vars=[x_col_actual], var_name="_group", value_name="_value")
        data = data.dropna(subset=[x_col_actual, "_value"])
        # 数值列可能含字符串编码的数字，强制转换
        data["_value"] = pd.to_numeric(data["_value"], errors="coerce")
        data = data.dropna(subset=["_value"])
        # 负值在 Marimekko 中无法表达（柱宽/高度按占比计算，负值会破坏几何）
        neg_n = int((data["_value"] < 0).sum())
        if neg_n > 0:
            warnings.append(f"检测到 {neg_n} 个负值，已过滤（马赛克图不支持负值）")
        data = data[data["_value"] >= 0]

        y_col_actual = "_value"
        group_col_actual = "_group"
    else:
        # 长格式：x + y (+ group)
        x_col = mapping.get("x")
        x_col = x_col if x_col is not None else x
        y_col = mapping.get("y")
        y_col = y_col if y_col is not None else y
        group_col = mapping.get("group")
        group_col = group_col if group_col is not None else group

        # 自动兜底 x
        if x_col not in df.columns:
            x_col = df.columns[0]

        # 自动兜底 y：找第一个数值列（且不等于 x）
        if y_col not in df.columns:
            y_col = None
            for col in df.columns:
                if col != x_col and pd.api.types.is_numeric_dtype(df[col]):
                    y_col = col
                    break

        # 自动兜底 group：找第一个非 x 且非 y 的非数值列
        if (group_col not in df.columns) if group_col else True:
            group_col = None
            for col in df.columns:
                if col != x_col and col != y_col and not pd.api.types.is_numeric_dtype(df[col]):
                    group_col = col
                    break

        if x_col not in df.columns:
            return ChartResult(warnings=[f"找不到列 [{x_col}]"])
        if not y_col or y_col not in df.columns:
            return ChartResult(warnings=[f"找不到数值列 [{y_col}]"])
        if not pd.api.types.is_numeric_dtype(df[y_col]):
            return ChartResult(warnings=[f"列 [{y_col}] 不是数值类型"])

        use_cols = [x_col, y_col] + ([group_col] if group_col else [])
        data = df[use_cols].copy()
        data = data.dropna(subset=[x_col, y_col])
        # 负值在 Marimekko 中无法表达（柱宽/高度按占比计算，负值会破坏几何）
        neg_n = int((data[y_col] < 0).sum())
        if neg_n > 0:
            warnings.append(f"检测到 {neg_n} 个负值，已过滤（马赛克图不支持负值）")
        data = data[data[y_col] >= 0]

        x_col_actual = x_col
        y_col_actual = y_col
        group_col_actual = group_col

    if data.empty:
        return ChartResult(warnings=["有效数据为空"])

    # -------- 显示标签（通用）--------
    label_x = x_label_user or str(x_col_actual) or "x"
    if y_label_user:
        label_y = y_label_user
    else:
        label_y = "value" if str(y_col_actual).startswith("_") else str(y_col_actual)
    label_group = group_label_user or (str(group_col_actual) if group_col_actual else "group")

    # -------- 2) 计算 Marimekko 几何：柱宽、left、center --------
    x_totals = data.groupby(x_col_actual, sort=False)[y_col_actual].sum()
    grand_total = float(x_totals.sum())
    if grand_total <= 0:
        return ChartResult(warnings=["总值为0"])

    x_order = list(x_totals.index)
    x_widths = (x_totals / grand_total).to_dict()

    x_lefts = {}
    x_centers = {}
    cursor = 0.0
    for xv in x_order:
        w = float(x_widths[xv])
        x_lefts[xv] = cursor
        x_centers[xv] = cursor + w / 2.0
        cursor += w

    fig = go.Figure()

    # 配色方案：优先用 options.color_scheme，回退到 _MCKINSEY_COLORS
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _palette = _scheme.get("colors") or _MCKINSEY_COLORS

    # -------- 3) 绘制 --------
    if group_col_actual:
        # 有分组：Marimekko（柱宽=该x总占比，柱内高度=组内占比）
        gdf = (
            data.groupby([x_col_actual, group_col_actual], sort=False)[y_col_actual]
            .sum()
            .reset_index()
        )

        group_order = list(dict.fromkeys(gdf[group_col_actual].tolist()))
        color_map = {g: _palette[i % len(_palette)] for i, g in enumerate(group_order)}

        for g in group_order:
            g_data = gdf[gdf[group_col_actual] == g].set_index(x_col_actual)[y_col_actual].to_dict()

            heights = []
            widths = []
            centers = []
            customdata = []

            for xv in x_order:
                val = float(g_data.get(xv, 0.0))
                total_x = float(x_totals[xv])
                h = (val / total_x) if total_x > 0 else 0.0

                heights.append(h)
                widths.append(float(x_widths[xv]))
                centers.append(float(x_centers[xv]))
                customdata.append([
                    str(xv),             # 0 x值
                    str(g),              # 1 分组值
                    val,                 # 2 该组在该x下的值
                    total_x,             # 3 该x总值
                    float(x_widths[xv])  # 4 该x柱宽（总占比）
                ])

            fig.add_trace(go.Bar(
                x=centers,
                y=heights,
                width=widths,
                name=str(g),
                marker=dict(color=color_map[g]),
                customdata=customdata,
                hovertemplate=(
                    f"<b>{label_x}</b>: %{{customdata[0]}}<br>"
                    f"<b>{label_group}</b>: %{{customdata[1]}}<br>"
                    f"<b>{label_y}</b>: %{{customdata[2]:.0f}}<br>"
                    f"<b>{label_x} total {label_y}</b>: %{{customdata[3]:.0f}}<br>"
                    f"<b>宽度(总占比)</b>: %{{customdata[4]:.1%}}<br>"
                    f"<b>{label_x}内占比</b>: %{{y:.1%}}<extra></extra>"
                ),
                showlegend=True
            ))
    else:
        # 无分组：变宽柱图（宽=总占比，高=总值）
        heights = [float(x_totals[xv]) for xv in x_order]
        widths = [float(x_widths[xv]) for xv in x_order]
        centers = [float(x_centers[xv]) for xv in x_order]
        customdata = [[str(xv), float(x_widths[xv])] for xv in x_order]

        fig.add_trace(go.Bar(
            x=centers,
            y=heights,
            width=widths,
            marker=dict(color=_palette[0]),
            customdata=customdata,
            hovertemplate=(
                f"<b>{label_x}</b>: %{{customdata[0]}}<br>"
                f"{label_y}: %{{y:.0f}}<br>"
                "宽度(总占比): %{customdata[1]:.1%}<extra></extra>"
            ),
            showlegend=False
        ))

    # -------- 4) 统一布局 --------
    tickvals = [float(x_centers[xv]) for xv in x_order]
    ticktext = [str(xv) for xv in x_order]

    x_axis_title = f"{label_x}（柱宽={label_y}占比）"
    y_axis_title = f"{label_group}内占比" if group_col_actual else label_y

    fig.update_layout(
        title=dict(text=title, font=dict(size=16, color="#222"), x=0.5, xanchor="center"),
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=50, r=50, t=70, b=70),
        xaxis=dict(
            title=x_axis_title,
            range=[0, 1],
            tickmode="array",
            tickvals=tickvals,
            ticktext=ticktext,
            showgrid=False,
            zeroline=False
        ),
        yaxis=dict(
            title=y_axis_title,
            tickformat=".0%" if group_col_actual else ".0f",
            range=[0, 1] if group_col_actual else None,
            showgrid=True,
            gridcolor="#f0f0f0",
            zeroline=False
        ),
        barmode="stack",
        hovermode="closest",
        height=520,
        bargap=0
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "marimekko_PCT", "plotly", _DATA_FMT, _DESC, chart_html)

    if not html or len(html) < 500:
        return ChartResult(warnings=["生成的 HTML 无效或过短"])

    meta = {
        "chart_id": "marimekko",
        "n_rows": len(df),
        "is_wide_format": is_wide_format,
        "x_col": x_col_actual,
        "y_col": y_col_actual,
        "group_col": group_col_actual,
        "x_label": label_x,
        "y_label": label_y,
        "group_label": label_group,
        "x_widths": {str(k): float(v) for k, v in x_widths.items()},
        "x_lefts": {str(k): float(x_lefts[k]) for k in x_order},
        "x_centers": {str(k): float(x_centers[k]) for k in x_order},
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
