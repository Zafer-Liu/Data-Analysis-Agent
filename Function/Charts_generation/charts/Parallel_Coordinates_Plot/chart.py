"""
平行坐标图 Parallel Coordinates Plot - 多变量关系图表
图表分类: 多变量 Multivariate
感知排名: ⭐⭐⭐⭐☆

统一接口:
    generate(df, mapping, options) -> ChartResult

使用示例:
    from charts.Parallel_Coordinates_Plot import generate
    from charts import ChartResult

    result = generate(
        df=df,
        mapping={"dimensions": ["教育程度", "就业率", "寿命", "投票率"]},
        options={"title": "国家发展指标", "color_col": "地区"}
    )
"""
import logging
log = logging.getLogger(__name__)
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List

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

_DATA_FMT = "多个数值列 + 可选color列(分组)"
_DESC = "展示多个变量之间的关系，用多条竖直轴表示不同变量，用线条连接各轴上的点。适合发现多变量间的相关性和模式。"

# 维度颜色（用于标记不同的维度）
DIM_COLORS = [
    "#003D7A", "#0084D1", "#00A4EF", "#7FBA00", "#FFB81C", "#F7630C"
]

# 分组颜色（用于区分不同的分组）
GROUP_COLORS = [
    "#003D7A", "#0084D1", "#00A4EF", "#7FBA00", "#FFB81C",
    "#F7630C", "#DA3B01", "#A4373A", "#6B2C91", "#00B4EF"
]


def _auto_detect_dimensions(df: pd.DataFrame, exclude: set = None) -> List[str]:
    """自动检测数值列作为维度。"""
    exclude = exclude or set()
    nums = [c for c in df.columns if c not in exclude and pd.api.types.is_numeric_dtype(df[c])]
    return nums[:6]  # 最多6个维度


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
    mapping: Dict[str, Any] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    dimensions: List[str] = None,
    color_col: str = None,
    title: str = "平行坐标图",
    **kwargs
) -> ChartResult:
    """
    生成平行坐标图。
    
    参数:
        df: DataFrame
        mapping: {
            "dimensions": ["列名1", "列名2", ...],  # 要展示的维度列
            "color": "分组列"  # 可选，用于颜色分组
        }
        options: {
            "title": "标题",
            "color_scheme": "配色方案",
            "normalize": True/False,  # 是否标准化所有轴到0-1范围
            "opacity": 0.5,  # 线条透明度
            "line_width": 3  # 线条宽度
        }
        excel_path: Excel文件路径
        dimensions: 维度列名列表
        color_col: 分组列名
        title: 图表标题
    """
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

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # 获取维度列
    dims = mapping.get("dimensions")
    dims = dims if dims is not None else dimensions
    if not dims:
        dims = _auto_detect_dimensions(df)
    
    if not dims or len(dims) < 2:
        return ChartResult(warnings=["至少需要2个数值维度"])

    # 验证维度列存在
    dims = [d for d in dims if d in df.columns]
    if len(dims) < 2:
        return ChartResult(warnings=["至少需要2个有效的数值维度"])

    # 获取分组列
    color_col_name = mapping.get("color")
    color_col_name = color_col_name if color_col_name is not None else color_col
    _color = None
    if color_col_name and color_col_name in df.columns:
        _color = color_col_name

    title = options.get("title", title)
    color_scheme_name = options.get("color_scheme", "mckinsey")
    normalize = bool(options.get("normalize", True))
    opacity = float(options.get("opacity", 0.6))
    line_width = float(options.get("line_width", 3))

    # 数据验证和转换
    try:
        for dim in dims:
            df[dim] = pd.to_numeric(df[dim], errors="coerce")
    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        return ChartResult(warnings=[f"数据转换失败: {e}"])

    # 删除包含NaN的行
    df_plot = df.dropna(subset=dims)
    if len(df_plot) < 1:
        return ChartResult(warnings=["没有有效数据"])

    # 获取配色
    try:
        color_scheme = get_color_scheme(color_scheme_name)
        primary_color = color_scheme.get("primary", "#0084D1")
    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        warnings.append(f"配色方案加载失败: {e}")
        primary_color = "#0084D1"

    # 生成图表
    try:
        # 准备数据：标准化或独立范围
        data_normalized = df_plot[dims].copy()
        
        if normalize:
            # 标准化到0-1范围
            for dim in dims:
                min_val = data_normalized[dim].min()
                max_val = data_normalized[dim].max()
                if max_val > min_val:
                    data_normalized[dim] = (data_normalized[dim] - min_val) / (max_val - min_val)
                else:
                    data_normalized[dim] = 0.5
        
        # 创建图表
        fig = go.Figure()

        # 如果有分组列，按分组绘制
        if _color:
            groups = sorted(df_plot[_color].unique())
            legend_added = set()  # 追踪已添加到图例的分组
            
            for idx, group in enumerate(groups):
                mask = df_plot[_color] == group
                group_data = data_normalized[mask]
                
                color = GROUP_COLORS[idx % len(GROUP_COLORS)]
                
                # 为每一行绘制一条线
                for row_idx in group_data.index:
                    row_values = group_data.loc[row_idx].values
                    
                    # 构建x坐标（维度索引）和y坐标（标准化值）
                    x_coords = list(range(len(dims)))
                    y_coords = row_values.tolist()
                    
                    # 构建悬停文本
                    hover_text = f"<b>{_color}</b>: {group}<br>"
                    for dim, val in zip(dims, df_plot.loc[row_idx, dims].values):
                        hover_text += f"<b>{dim}</b>: {val:.2f}<br>"
                    
                    # 只在第一条线时添加到图例
                    show_in_legend = group not in legend_added
                    if show_in_legend:
                        legend_added.add(group)
                    
                    fig.add_trace(go.Scatter(
                        x=x_coords,
                        y=y_coords,
                        mode="lines",
                        line=dict(color=color, width=line_width),
                        opacity=opacity,
                        hovertemplate=hover_text + "<extra></extra>",
                        showlegend=show_in_legend,
                        name=str(group),
                        legendgroup=str(group)
                    ))
        else:
            # 无分组，为每条线分配不同颜色
            for row_idx, data_row in enumerate(data_normalized.iterrows()):
                row_idx_actual = data_row[0]
                row_values = data_row[1].values
                
                x_coords = list(range(len(dims)))
                y_coords = row_values.tolist()
                
                # 为每行分配不同颜色
                color = DIM_COLORS[row_idx % len(DIM_COLORS)]
                
                # 构建悬停文本
                hover_text = ""
                for dim, val in zip(dims, df_plot.loc[row_idx_actual, dims].values):
                    hover_text += f"<b>{dim}</b>: {val:.2f}<br>"
                
                fig.add_trace(go.Scatter(
                    x=x_coords,
                    y=y_coords,
                    mode="lines",
                    line=dict(color=color, width=line_width),
                    opacity=opacity,
                    hovertemplate=hover_text + "<extra></extra>",
                    showlegend=False
                ))
        
        # 添加维度标签和颜色指示
        for dim_idx, dim in enumerate(dims):
            dim_color = DIM_COLORS[dim_idx % len(DIM_COLORS)]
            fig.add_annotation(
                x=dim_idx,
                y=1.08,
                text=f"<b>{dim}</b>",
                showarrow=False,
                xref="x",
                yref="paper",
                font=dict(size=13, color=dim_color)
            )

        # 更新布局
        fig.update_layout(
            title=dict(text=title, font=dict(size=16)),
            xaxis=dict(
                tickvals=list(range(len(dims))),
                ticktext=dims,
                showgrid=False,
                zeroline=False
            ),
            yaxis=dict(
                title="标准化值" if normalize else "值",
                showgrid=True,
                gridwidth=1,
                gridcolor="#E8E8E8",
                zeroline=False,
                range=[-0.1, 1.1] if normalize else None
            ),
            plot_bgcolor="white",
            paper_bgcolor="white",
            font=dict(family="Heiti SC, Microsoft YaHei, sans-serif", size=12),
            margin=dict(l=60, r=200, t=120, b=60),
            hovermode="closest",
            height=600,
            showlegend=bool(_color),
            legend=dict(
                x=1.02,
                y=1,
                xanchor="left",
                yanchor="top",
                bgcolor="rgba(255,255,255,0.8)",
                bordercolor="#CCCCCC",
                borderwidth=1
            )
        )

        chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
        if not chart_html or len(chart_html) < 100:
            return ChartResult(warnings=["图表生成失败"])

    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        return ChartResult(warnings=[f"图表生成失败: {e}"])

    html = _build_html(title, "parallel_coordinates_plot", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "parallel_coordinates_plot",
        "n_rows": len(df_plot),
        "n_dimensions": len(dims),
        "dimensions": dims,
        "color_col": _color,
        "normalized": normalize,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
