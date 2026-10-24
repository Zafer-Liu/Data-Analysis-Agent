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
    title: str = "马赛克图_绝对数值版",
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
    # 宽格式：首列是x，其余列全数值（如 年份 + 各品牌）。
    # 至少 2 个数值列才可能是宽格式；2 列长格式 [类别, 值] 首列字符串 +
    # 单个数值列，melt 后只有单一 series，不是有意义的马赛克宽表。
    is_wide_format = False
    if len(df.columns) > 2:
        first_col = df.columns[0]
        rest_cols = df.columns[1:]
        if len(rest_cols) >= 2 and all(pd.api.types.is_numeric_dtype(df[c]) for c in rest_cols):
            is_wide_format = True

    if is_wide_format:
        # 宽转长：x=首列，group=原列名，y=单元格值
        x_col_actual = df.columns[0]
        data = df.melt(id_vars=[x_col_actual], var_name="_group", value_name="_value")
        data = data.dropna(subset=[x_col_actual, "_value"])
        data = data[data["_value"] >= 0]

        y_col_actual = "_value"
        group_col_actual = "_group"
    else:
        # 长格式：x + y (+ group可选)
        x_col = mapping.get("x")
        x_col = x_col if x_col is not None else x
        y_col = mapping.get("y")
        y_col = y_col if y_col is not None else y
        group_col = mapping.get("group")
        group_col = group_col if group_col is not None else group

        if x_col not in df.columns:
            x_col = df.columns[0]

        if y_col not in df.columns:
            y_col = None
            for col in df.columns:
                if col != x_col and pd.api.types.is_numeric_dtype(df[col]):
                    y_col = col
                    break

        if group_col and group_col not in df.columns:
            group_col = None

        if x_col not in df.columns:
            return ChartResult(warnings=[f"找不到列 [{x_col}]"])
        if not y_col or y_col not in df.columns:
            return ChartResult(warnings=[f"找不到数值列 [{y_col}]"])
        if not pd.api.types.is_numeric_dtype(df[y_col]):
            return ChartResult(warnings=[f"列 [{y_col}] 不是数值类型"])

        use_cols = [x_col, y_col] + ([group_col] if group_col else [])
        data = df[use_cols].copy()
        data = data.dropna(subset=[x_col, y_col])
        data = data[data[y_col] >= 0]

        x_col_actual = x_col
        y_col_actual = y_col
        group_col_actual = group_col

    if data.empty:
        return ChartResult(warnings=["有效数据为空"])

    # -------- 标签系统（仅显示，不影响计算）--------
    label_x = x_label_user or str(x_col_actual) or "x"
    if y_label_user:
        label_y = y_label_user
    else:
        # 宽表内部列名一般是 "_value"，展示成通用 value
        label_y = "value" if str(y_col_actual).startswith("_") else str(y_col_actual)
    label_group = group_label_user or (str(group_col_actual) if group_col_actual else "group")

    # -------- 2) 计算柱宽：按每个x总值占比 --------
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

    # -------- 3) 绘图 --------
    if group_col_actual:
        # 堆积柱 + 变宽（每段高度用绝对值，不做归一化）
        gdf = (
            data.groupby([x_col_actual, group_col_actual], sort=False)[y_col_actual]
            .sum()
            .reset_index()
        )
        group_order = list(dict.fromkeys(gdf[group_col_actual].tolist()))
        color_map = {g: _palette[i % len(_palette)] for i, g in enumerate(group_order)}

        for g in group_order:
            g_map = gdf[gdf[group_col_actual] == g].set_index(x_col_actual)[y_col_actual].to_dict()

            ys, widths, centers, customdata = [], [], [], []
            for xv in x_order:
                val = float(g_map.get(xv, 0.0))
                total_x = float(x_totals[xv])

                ys.append(val)  # 绝对值
                widths.append(float(x_widths[xv]))
                centers.append(float(x_centers[xv]))
                customdata.append([
                    str(xv),            # 0 x
                    str(g),             # 1 group
                    val,                # 2 当前段值
                    total_x,            # 3 该x总值
                    float(x_widths[xv]) # 4 柱宽占比
                ])

            fig.add_trace(go.Bar(
                x=centers,
                y=ys,
                width=widths,
                name=str(g),
                marker=dict(color=color_map[g]),
                customdata=customdata,
                hovertemplate=(
                    f"<b>{label_x}</b>: %{{customdata[0]}}<br>"
                    f"<b>{label_group}</b>: %{{customdata[1]}}<br>"
                    f"<b>{label_y}</b>: %{{customdata[2]:,.0f}}<br>"
                    f"<b>{label_x} total {label_y}</b>: %{{customdata[3]:,.0f}}<br>"
                    f"<b>柱宽(总占比)</b>: %{{customdata[4]:.1%}}<extra></extra>"
                ),
                showlegend=True
            ))
    else:
        # 单系列柱状图 + 变宽
        ys = [float(x_totals[xv]) for xv in x_order]
        widths = [float(x_widths[xv]) for xv in x_order]
        centers = [float(x_centers[xv]) for xv in x_order]
        customdata = [[str(xv), float(x_widths[xv])] for xv in x_order]

        fig.add_trace(go.Bar(
            x=centers,
            y=ys,
            width=widths,
            marker=dict(color=_palette[0]),
            customdata=customdata,
            hovertemplate=(
                f"<b>{label_x}</b>: %{{customdata[0]}}<br>"
                f"{label_y}: %{{y:,.0f}}<br>"
                "柱宽(总占比): %{customdata[1]:.1%}<extra></extra>"
            ),
            showlegend=False
        ))

    # -------- 4) 统一布局（y轴绝对值）--------
    tickvals = [float(x_centers[xv]) for xv in x_order]
    ticktext = [str(xv) for xv in x_order]

    fig.update_layout(
        title=dict(text=title, font=dict(size=16, color="#222"), x=0.5, xanchor="center"),
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=50, r=50, t=70, b=70),
        xaxis=dict(
            title=f"{label_x}（柱宽={label_y}占比）",
            range=[0, 1],
            tickmode="array",
            tickvals=tickvals,
            ticktext=ticktext,
            showgrid=False,
            zeroline=False
        ),
        yaxis=dict(
            title=f"{label_y}（绝对值）",
            tickformat=",.0f",
            showgrid=True,
            gridcolor="#f0f0f0",
            zeroline=False
        ),
        barmode="stack" if group_col_actual else "group",
        hovermode="closest",
        height=520,
        bargap=0
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "marimekko_ABS", "plotly", _DATA_FMT, _DESC, chart_html)

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
