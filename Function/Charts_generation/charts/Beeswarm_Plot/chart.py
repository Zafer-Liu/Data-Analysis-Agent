"""
蜜蜂群图 Beeswarm Plot - 分布图表
图表分类: 分布 Distribution | 书章节: Ch7
感知排名: ⭐⭐⭐⭐☆

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
import numpy as np
import plotly.graph_objects as go
import plotly.io as pio

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult
from charts.color_schemes import get_color_scheme

__all__ = ["generate"]

_DATA_FMT = "group列(分类) + value列(数值)"
_DESC = "通过蜂群避让布局将个体数据点分散排列，展示分布形态和异常值。"


def _auto_col(df: pd.DataFrame, *hints: str) -> Optional[str]:
    strs = [c for c in df.columns if pd.api.types.is_string_dtype(df[c])]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    col_lower = {str(c).lower(): c for c in df.columns}

    hints = [h for h in hints if h is not None]

    for h in hints:
        h_lower = str(h).lower()
        if h_lower in col_lower:
            return col_lower[h_lower]

    for h in hints:
        h_lower = str(h).lower()
        for col in df.columns:
            col_lower_name = str(col).lower()
            if h_lower in col_lower_name or col_lower_name in h_lower:
                return col

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

    if not hints:
        if strs:
            return strs[0]
        if nums:
            return nums[0]

    return None


def _is_wide_format(df: pd.DataFrame) -> bool:
    if len(df.columns) < 3:
        return False

    first_col = df.columns[0]
    rest_cols = df.columns[1:]

    is_first_str = pd.api.types.is_string_dtype(df[first_col])
    rest_numeric = all(pd.api.types.is_numeric_dtype(df[c]) for c in rest_cols)

    if not (is_first_str and rest_numeric):
        return False

    standard_long_cols = {'group', 'value', 'x', 'y', 'category', 'measure', 'variable', 'sample'}
    rest_cols_lower = {str(c).lower() for c in rest_cols}

    if rest_cols_lower & standard_long_cols:
        return False

    return True


def _wide_to_long(df: pd.DataFrame) -> pd.DataFrame:
    id_col = df.columns[0]
    value_cols = df.columns[1:]
    df_long = df.melt(id_vars=[id_col], value_vars=value_cols,
                      var_name="sample", value_name="value")
    df_long = df_long.rename(columns={id_col: "group"})
    return df_long


def _swarm_offsets(
    y_values: np.ndarray,
    point_radius: float = 0.10,
    y_tolerance: Optional[float] = None,
    max_offset: Optional[float] = 0.42,
) -> np.ndarray:
    """
    计算 beeswarm 的横向偏移:
    保持 y 值真实，仅在 x 方向进行最小必要避让。
    """
    if len(y_values) == 0:
        return np.array([])

    y = np.asarray(y_values, dtype=float)
    order = np.argsort(y)
    y_sorted = y[order]
    offsets_sorted = np.zeros(len(y_sorted))

    if y_tolerance is None:
        y_range = np.ptp(y_sorted)
        y_tolerance = y_range * 0.015 if y_range > 0 else 1e-9

    placed = []
    collision_dist2 = (point_radius * 1.8) ** 2

    for i, yi in enumerate(y_sorted):
        neighbors = [(yj, xj) for yj, xj in placed if abs(yi - yj) <= y_tolerance]

        if not neighbors:
            xi = 0.0
        else:
            candidates = [0.0]
            for k in range(1, len(neighbors) + 3):
                candidates.extend([k * point_radius, -k * point_radius])

            xi = 0.0
            for cand in candidates:
                collision = False
                for yj, xj in neighbors:
                    dx = cand - xj
                    dy = yi - yj
                    if dx * dx + dy * dy < collision_dist2:
                        collision = True
                        break
                if not collision:
                    xi = cand
                    break

        if max_offset is not None:
            xi = np.clip(xi, -max_offset, max_offset)

        offsets_sorted[i] = xi
        placed.append((yi, xi))

    offsets = np.zeros(len(y))
    offsets[order] = offsets_sorted
    return offsets


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
    excel_path: str = None,
    group: str = None,
    value: str = None,
    title: str = "蜜蜂群图",
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

    # 宽格式检测仅在用户未显式提供映射时启用，避免 melt 覆盖用户意图
    user_mapping = any(v is not None for v in (group, value)) or bool(mapping)
    is_wide = not user_mapping and _is_wide_format(df)
    if is_wide:
        df = _wide_to_long(df)
        group_col = "group"
        value_col = "value"
        warnings.append("检测到宽格式数据，已自动转换为长格式")
    else:
        # chart_selector 声明角色为 y(必填)/x：调度器不做 x→group 转换，因此
        # 这里同时接受 selector 规范 (y=数值, x=分组) 与旧别名 (value/group)。
        value_col = mapping.get("y")
        value_col = value_col if value_col is not None else (mapping.get("value") if mapping.get("value") is not None else value)
        group_col = mapping.get("x")
        group_col = group_col if group_col is not None else (mapping.get("group") if mapping.get("group") is not None else group)

    title = options.get("title", title)

    _group = _auto_col(df, group_col, "x", "group", "分组", "类别", "行业", "地区")
    _value = _auto_col(df, value_col, "y", "value", "数值", "销售额", "薪资", "价格")

    if _value is None or _value not in df.columns:
        warnings.append("找不到必填字段 [value]")
        return ChartResult(warnings=warnings)

    if _group is None or _group not in df.columns:
        warnings.append("找不到必填字段 [group]")
        return ChartResult(warnings=warnings)

    plot_df = df[[_group, _value]].copy()
    plot_df = plot_df.dropna(subset=[_group, _value])

    if plot_df.empty:
        return ChartResult(warnings=["有效数据为空"])

    color_scheme_name = options.get("color_scheme", "mckinsey")
    color_scheme = get_color_scheme(color_scheme_name)
    colors = color_scheme.get("colors", ["#003D7A", "#0084D1", "#00A4EF", "#6E7B8B", "#B9C2CF"])

    point_radius = options.get("point_radius", 0.10)
    y_tolerance = options.get("y_tolerance")
    max_offset = options.get("max_offset", 0.42)
    marker_size = options.get("marker_size", 8)
    marker_opacity = options.get("marker_opacity", 0.72)

    groups = sorted(plot_df[_group].unique())
    fig = go.Figure()
    drawn_groups = []

    for idx, grp in enumerate(groups):
        grp_series = plot_df.loc[plot_df[_group] == grp, _value]
        grp_data = grp_series.to_numpy(dtype=float)

        if len(grp_data) == 0:
            warnings.append(f"分组 '{grp}' 无有效数据，已跳过")
            continue

        offsets = _swarm_offsets(
            grp_data,
            point_radius=point_radius,
            y_tolerance=y_tolerance,
            max_offset=max_offset,
        )
        x_positions = idx + offsets
        color = colors[idx % len(colors)]
        drawn_groups.append(grp)

        fig.add_trace(go.Scatter(
            x=x_positions,
            y=grp_data,
            mode='markers',
            name=str(grp),
            marker=dict(
                size=marker_size,
                color=color,
                opacity=marker_opacity,
                line=dict(width=0.6, color='white')
            ),
            hovertemplate=f"<b>{grp}</b><br>{_value}: %{{y:.2f}}<extra></extra>",
            text=[str(grp)] * len(grp_data)
        ))

    fig.update_layout(
        title=title,
        xaxis=dict(
            title=_group,
            ticktext=[str(g) for g in groups],
            tickvals=list(range(len(groups))),
            tickfont=dict(size=11),
            showgrid=False,
            zeroline=False
        ),
        yaxis=dict(
            title=_value,
            tickfont=dict(size=11),
            showgrid=True,
            gridwidth=1,
            gridcolor='rgba(200,200,200,0.25)',
            zeroline=False
        ),
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=60, r=40, t=60, b=60),
        hovermode="closest",
        showlegend=False,
        height=500
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "beeswarm_plot", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "beeswarm_plot",
        "n_rows": len(plot_df),
        "n_groups": len(drawn_groups),
        "n_points": len(plot_df),
        "group_col": _group,
        "value_col": _value,
        "is_wide_format": is_wide,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)