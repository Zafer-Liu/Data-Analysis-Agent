"""
山脊线图 Ridgeline Plot - 分布图表
图表分类: 分布 Distribution | 书章节: Ch6
感知排名: ⭐⭐⭐⭐⭐

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
import numpy as np
import plotly.graph_objects as go
import plotly.io as pio
from scipy import stats

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult
from charts.color_schemes import get_color_scheme

__all__ = ["generate"]

_DATA_FMT = "group列(分类) + value列(数值)"
_DESC = "多个密度曲线沿垂直轴排列并略微重叠，展示分布形态对比和演变趋势。"


def _auto_col(df: pd.DataFrame, *hints: str) -> Optional[str]:
    """根据提示自动查找匹配的列名。"""
    strs = [c for c in df.columns if pd.api.types.is_string_dtype(df[c])]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    col_lower = {str(c).lower(): c for c in df.columns}

    # 1. 精确匹配 hints（跳过 None）
    for h in hints:
        if h is None:
            continue
        h_lower = str(h).lower()
        if h_lower in col_lower:
            return col_lower[h_lower]
    
    # 2. 模糊匹配（包含关系，跳过 None）
    for h in hints:
        if h is None:
            continue
        h_lower = str(h).lower()
        for col in df.columns:
            col_lower_name = str(col).lower()
            if h_lower in col_lower_name or col_lower_name in h_lower:
                return col

    # 3. 类型匹配
    non_none_hints = [h for h in hints if h is not None]
    if non_none_hints:
        hint = non_none_hints[0].lower()
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
    
    # 4. 无有效 hints 时自动推断
    if not non_none_hints:
        if strs:
            return strs[0]
        if nums:
            return nums[0]
    
    return None


def _is_wide_format(df: pd.DataFrame) -> bool:
    """检测是否为宽格式数据（第一列为分类，其余为数值）。"""
    if len(df.columns) < 3:
        return False
    
    first_col = df.columns[0]
    rest_cols = df.columns[1:]
    
    is_first_str = pd.api.types.is_string_dtype(df[first_col])
    rest_numeric = all(pd.api.types.is_numeric_dtype(df[c]) for c in rest_cols)
    
    if not (is_first_str and rest_numeric):
        return False
    
    # 排除已经是长格式的情况
    standard_long_cols = {'group', 'value', 'x', 'y', 'category', 'measure', 'variable', 'sample'}
    rest_cols_lower = {str(c).lower() for c in rest_cols}
    
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


def _build_html(title: str, chart_name: str, library: str,
                data_fmt: str, desc: str, embed: str) -> str:
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


def _compute_density(values: np.ndarray, x_range: np.ndarray) -> np.ndarray:
    """使用 KDE 计算密度曲线。"""
    if len(values) < 2:
        return np.zeros_like(x_range)

    try:
        kde = stats.gaussian_kde(values)
        return kde(x_range)
    except np.linalg.LinAlgError:
        # 奇异协方差（如所有值相同），KDE 不可估
        return np.zeros_like(x_range)


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    group: str = None,
    value: str = None,
    title: str = "山脊线图",
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
        group_col = "group"
        value_col = "value"
        warnings.append("检测到宽格式数据，已自动转换为长格式")
    else:
        # 兼容 required_roles 为 group/value 或 x/y 两种写法
        group_col = mapping.get("group")
        group_col = group_col if group_col is not None else (mapping.get("x") if mapping.get("x") is not None else group)
        value_col = mapping.get("value")
        value_col = value_col if value_col is not None else (mapping.get("y") if mapping.get("y") is not None else value)

    title = options.get("title", title)

    _group = _auto_col(df, group_col, "group", "分组", "类别", "行业", "地区")
    _value = _auto_col(df, value_col, "value", "数值", "销售额", "薪资", "价格")

    if _value is None or _value not in df.columns:
        warnings.append("找不到必填字段 [value]")
        return ChartResult(warnings=warnings)

    if _group is None or _group not in df.columns:
        warnings.append("找不到必填字段 [group]")
        return ChartResult(warnings=warnings)

    # 获取配色方案
    color_scheme_name = options.get("color_scheme", "mckinsey")
    color_scheme = get_color_scheme(color_scheme_name)
    colors = color_scheme.get("colors", ["#003D7A", "#0084D1", "#00A4EF", "#6E7B8B", "#B9C2CF"])

    # 按分组计算密度曲线
    groups = df[_group].dropna().unique().tolist()

    # 排序方式：默认按中位数排序，更适合分布比较
    sort_by = options.get("sort_by", "median")  # median / mean / name / none
    if sort_by == "median":
        groups = sorted(groups, key=lambda g: df[df[_group] == g][_value].median())
    elif sort_by == "mean":
        groups = sorted(groups, key=lambda g: df[df[_group] == g][_value].mean())
    elif sort_by == "name":
        groups = sorted(groups)
    elif sort_by == "none":
        groups = list(groups)

    # 确定 x 轴范围，适当留白
    values_all = df[_value].dropna().values
    x_min = np.min(values_all)
    x_max = np.max(values_all)
    x_pad = (x_max - x_min) * 0.05 if x_max > x_min else 1
    x_range = np.linspace(x_min - x_pad, x_max + x_pad, 300)

    # 先计算所有组密度，用于统一缩放
    density_map = {}
    max_density = 0.0

    valid_groups = []
    for grp in groups:
        grp_data = df[df[_group] == grp][_value].dropna().values
        if len(grp_data) < 2:
            warnings.append(f"分组 '{grp}' 数据点过少（<2），已跳过")
            continue

        density = _compute_density(grp_data, x_range)
        density_map[grp] = density
        max_density = max(max_density, float(np.max(density)))
        valid_groups.append(grp)

    groups = valid_groups

    if not groups:
        return ChartResult(warnings=warnings + ["没有足够的数据生成山脊线图"])

    fig = go.Figure()

    # ridgeline 参数
    overlap = float(options.get("overlap", 0.6))  # 0~1，越大重叠越明显
    ridge_height = float(options.get("ridge_height", 0.9))  # 单条山脊最大高度

    # 用统一缩放将密度映射到 ridge_height
    scale = ridge_height / max_density if max_density > 0 else 1.0

    for idx, grp in enumerate(groups):
        density = density_map[grp]
        scaled_density = density * scale

        # 基线间距小于 ridge_height，即产生重叠
        baseline = idx * (1 - overlap)

        color = colors[idx % len(colors)]

        # 闭合多边形：上边界 + 反向基线
        x_poly = np.concatenate([x_range, x_range[::-1]])
        y_poly = np.concatenate([
            baseline + scaled_density,
            np.full_like(x_range, baseline)[::-1]
        ])

        fig.add_trace(go.Scatter(
            x=x_poly,
            y=y_poly,
            fill="toself",
            mode="lines",
            line=dict(color=color, width=1.5),
            fillcolor=color,
            name=str(grp),
            opacity=0.75,
            hoverinfo="skip",
            showlegend=True
        ))

        # 再叠加一条真正的密度轮廓线，hover 显示正确密度
        fig.add_trace(go.Scatter(
            x=x_range,
            y=baseline + scaled_density,
            mode="lines",
            line=dict(color=color, width=2),
            name=str(grp),
            showlegend=False,
            customdata=np.column_stack([density, np.full_like(density, idx)]),
            hovertemplate=(
                f"<b>{grp}</b><br>"
                "值: %{x:.2f}<br>"
                "密度: %{customdata[0]:.4f}"
                "<extra></extra>"
            )
        ))

    tickvals = [i * (1 - overlap) for i in range(len(groups))]

    fig.update_layout(
        title=title,
        xaxis_title=_value,
        yaxis_title="分组",
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=80, r=40, t=60, b=40),
        hovermode="closest",
        showlegend=False,
        height=max(420, 120 + len(groups) * 45),
        yaxis=dict(
            ticktext=[str(g) for g in groups],
            tickvals=tickvals,
            tickfont=dict(size=11),
            range=[-0.2, tickvals[-1] + ridge_height + 0.2],
            showgrid=False,
            zeroline=False
        ),
        xaxis=dict(
            tickfont=dict(size=11),
            showgrid=True,
            gridcolor="rgba(0,0,0,0.08)",
            zeroline=False
        )
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "ridgeline_plot", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "ridgeline_plot",
        "n_rows": len(df),
        "n_groups": len(groups),
        "group_col": _group,
        "value_col": _value,
        "is_wide_format": is_wide,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
