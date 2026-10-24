"""
散点图 Scatter Plot - 关系图表
图表分类: 关系 Relationship
感知排名: ⭐⭐⭐⭐⭐

统一接口:
    generate(df, mapping, options) -> ChartResult

使用示例:
    from charts.scatter_plot import generate
    from charts import ChartResult

    result = generate(
        df=df,
        mapping={"x": "面积", "y": "价格", "size": "房间数", "color": "区域"},
        options={"title": "房价关系", "trendline": True}
    )
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
from scipy import stats

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult
from charts.color_schemes import get_color_scheme

__all__ = ["generate"]

_DATA_FMT = "x列(数值) + y列(数值) + 可选size列(气泡大小) + 可选color列(分组)"
_DESC = "展示两个或多个数值变量之间的关系，适合发现相关性、聚类和异常值。"


def _norm(s: str) -> str:
    return str(s).strip().lower().replace("_", "").replace(" ", "")


def _auto_col(df: pd.DataFrame, role: str, exclude: set = None) -> Optional[str]:
    """根据角色自动查找匹配的列名。"""
    exclude = exclude or set()
    cols = [c for c in df.columns if c not in exclude]
    nums = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    objs = [c for c in cols if not pd.api.types.is_numeric_dtype(df[c])]

    hints_map = {
        "x": ["x", "value", "amount", "sales", "数值", "销售额", "金额", "面积", "GDP"],
        "y": ["y", "profit", "利润", "value", "amount", "数值", "价格", "寿命", "排放"],
        "size": ["size", "value", "amount", "大小", "数值", "权重", "volume", "房间数", "人口"],
        "color": ["color", "group", "category", "region", "颜色", "分组", "类别", "区域"],
    }
    hints = [_norm(h) for h in hints_map.get(role, [])]

    # 1) 完全匹配
    norm_to_col = {_norm(c): c for c in cols}
    for h in hints:
        if h in norm_to_col:
            return norm_to_col[h]

    # 2) 包含匹配
    for c in cols:
        nc = _norm(c)
        if any(h in nc or nc in h for h in hints):
            return c

    # 3) 回退策略
    if role in ("x", "y", "size"):
        if nums:
            if role == "y" and len(nums) > 1:
                return nums[1]
            return nums[0]
        if role != "size" and objs:   # size 必须是数值列，不回退到字符串列
            return objs[0]
    elif role == "color":
        if objs:
            return objs[0]
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


def _calculate_trendline(x_data: np.ndarray, y_data: np.ndarray) -> tuple:
    """计算趋势线（线性回归）。
    
    返回: (x_line, y_line, r_squared, correlation)
    """
    # 移除NaN
    mask = ~(np.isnan(x_data) | np.isnan(y_data))
    x_clean = x_data[mask]
    y_clean = y_data[mask]
    
    if len(x_clean) < 2:
        return None, None, None, None
    
    # 线性回归
    slope, intercept, r_value, p_value, std_err = stats.linregress(x_clean, y_clean)
    
    # 生成趋势线数据
    x_line = np.array([x_clean.min(), x_clean.max()])
    y_line = slope * x_line + intercept
    
    r_squared = r_value ** 2
    correlation = r_value
    
    return x_line, y_line, r_squared, correlation


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    x: str = "x",
    y: str = "y",
    size: str = None,
    color: str = None,
    title: str = "散点图",
    **kwargs
) -> ChartResult:
    """
    生成散点图。
    
    参数:
        df: DataFrame
        mapping: {"x": "列名", "y": "列名", "size": "气泡大小列", "color": "分组列"}
        options: {
            "title": "标题",
            "color_scheme": "配色方案",
            "trendline": True/False,  # 是否显示趋势线
            "opacity": 0.7,  # 透明度（0-1）
            "marker_size": 10  # 标记大小
        }
        excel_path: Excel文件路径
        x, y, size, color: 列名（如果mapping中没有指定）
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

    # 获取列名
    x_col = mapping.get("x")
    x_col = x_col if x_col is not None else x
    y_col = mapping.get("y")
    y_col = y_col if y_col is not None else y
    size_col = mapping.get("size")
    size_col = size_col if size_col is not None else size
    color_col = mapping.get("color")
    color_col = color_col if color_col is not None else color
    title = options.get("title", title)
    color_scheme_name = options.get("color_scheme", "mckinsey")
    show_trendline = bool(options.get("trendline", False))
    opacity = float(options.get("opacity", 0.7))
    marker_size = int(options.get("marker_size", 15))

    # 自动检测列
    exclude_set = set()
    _x = x_col if x_col and x_col != "x" and x_col in df.columns else _auto_col(df, "x", exclude_set)
    if _x:
        exclude_set.add(_x)
    else:
        return ChartResult(warnings=["无法找到x列"])

    _y = y_col if y_col and y_col != "y" and y_col in df.columns else _auto_col(df, "y", exclude_set)
    if _y:
        exclude_set.add(_y)
    else:
        return ChartResult(warnings=["无法找到y列"])

    _size = size_col if size_col and size_col in df.columns else _auto_col(df, "size", exclude_set)
    if _size:
        exclude_set.add(_size)

    _color = color_col if color_col and color_col in df.columns else _auto_col(df, "color", exclude_set)
    if _color:
        exclude_set.add(_color)

    # 数据验证 — 先直接转，失败后去除百分号/货币符号再转
    def _to_numeric_col(series):
        converted = pd.to_numeric(series, errors="coerce")
        if converted.isna().all():
            cleaned = series.astype(str).str.strip().str.replace(r'[%￥$,，]', '', regex=True)
            converted = pd.to_numeric(cleaned, errors="coerce")
        return converted

    try:
        df[_x] = _to_numeric_col(df[_x])
        df[_y] = _to_numeric_col(df[_y])
        if df[_x].isna().all() or df[_y].isna().all():
            return ChartResult(warnings=["x或y列包含非数值数据"])
    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        return ChartResult(warnings=[f"数据转换失败: {e}"])

    # 删除NaN行
    df_plot = df.dropna(subset=[_x, _y])
    if len(df_plot) < 2:
        return ChartResult(warnings=["有效数据点少于2个"])

    # 处理气泡大小
    marker_sizes = [marker_size] * len(df_plot)
    if _size:
        try:
            size_vals = pd.to_numeric(df_plot[_size], errors="coerce")
            if size_vals.notna().sum() > 0:
                size_min, size_max = size_vals.min(), size_vals.max()
                if size_max > size_min:
                    # 映射到 10-50 范围
                    marker_sizes = 10 + (size_vals - size_min) / (size_max - size_min) * 40
                    marker_sizes = marker_sizes.fillna(marker_size).tolist()
                else:
                    marker_sizes = [marker_size] * len(df_plot)
        except Exception as e:
            log.warning("[chart] 图表生成异常: %s", e)
            warnings.append(f"气泡大小处理失败: {e}")

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
        fig = go.Figure()

        x_data = df_plot[_x].values
        y_data = df_plot[_y].values

        # 如果有color列，按分组绘制
        if _color:
            groups = df_plot[_color].unique()
            colors = [
                "#003D7A", "#0084D1", "#00A4EF", "#7FBA00", "#FFB81C",
                "#F7630C", "#DA3B01", "#A4373A", "#6B2C91", "#00B4EF"
            ]
            
            for idx, group in enumerate(groups):
                mask = df_plot[_color] == group
                group_x = x_data[mask]
                group_y = y_data[mask]
                group_sizes = [marker_sizes[i] for i in range(len(marker_sizes)) if mask.iloc[i]]
                
                color = colors[idx % len(colors)]
                
                # 构建悬停文本
                hover_texts = []
                for i, (gx, gy) in enumerate(zip(group_x, group_y)):
                    hover_text = f"<b>{_x}</b>: {float(gx):.2f}<br>"
                    hover_text += f"<b>{_y}</b>: {float(gy):.2f}<br>"
                    if _size:
                        hover_text += f"<b>{_size}</b>: {float(df_plot[_size].values[mask][i]):.2f}<br>"
                    hover_text += f"<b>{_color}</b>: {group}"
                    hover_texts.append(hover_text)
                
                fig.add_trace(go.Scatter(
                    x=group_x,
                    y=group_y,
                    mode="markers",
                    name=str(group),
                    marker=dict(
                        size=group_sizes,
                        color=color,
                        line=dict(color="white", width=1),
                        opacity=opacity
                    ),
                    text=hover_texts,
                    hovertemplate="%{text}<extra></extra>",
                    showlegend=True
                ))
        else:
            # 无分组，单色散点
            hover_texts = []
            for i, (gx, gy) in enumerate(zip(x_data, y_data)):
                hover_text = f"<b>{_x}</b>: {float(gx):.2f}<br>"
                hover_text += f"<b>{_y}</b>: {float(gy):.2f}<br>"
                if _size:
                    hover_text += f"<b>{_size}</b>: {float(df_plot[_size].values[i]):.2f}"
                hover_texts.append(hover_text)
            
            fig.add_trace(go.Scatter(
                x=x_data,
                y=y_data,
                mode="markers",
                marker=dict(
                    size=marker_sizes,
                    color=primary_color,
                    line=dict(color="white", width=1),
                    opacity=opacity
                ),
                text=hover_texts,
                hovertemplate="%{text}<extra></extra>",
                showlegend=False
            ))

        # 添加趋势线
        if show_trendline:
            x_line, y_line, r_squared, correlation = _calculate_trendline(x_data, y_data)
            if x_line is not None:
                fig.add_trace(go.Scatter(
                    x=x_line,
                    y=y_line,
                    mode="lines",
                    name=f"趋势线 (R²={r_squared:.3f})",
                    line=dict(color="rgba(200,0,0,0.5)", width=2, dash="dash"),
                    hovertemplate="趋势线<br>x: %{x:.2f}<br>y: %{y:.2f}<extra></extra>",
                    showlegend=True
                ))

        # 更新布局
        fig.update_layout(
            title=dict(text=title, font=dict(size=16)),
            xaxis=dict(
                title=_x,
                showgrid=True,
                gridwidth=1,
                gridcolor="#E8E8E8",
                zeroline=False
            ),
            yaxis=dict(
                title=_y,
                showgrid=True,
                gridwidth=1,
                gridcolor="#E8E8E8",
                zeroline=False
            ),
            plot_bgcolor="white",
            paper_bgcolor="white",
            font=dict(family="Heiti SC, Microsoft YaHei, sans-serif", size=12),
            margin=dict(l=60, r=60, t=80, b=60),
            hovermode="closest",
            height=500,
            showlegend=bool(_color or show_trendline)
        )

        chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
        if not chart_html or len(chart_html) < 100:
            return ChartResult(warnings=["图表生成失败"])

    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        return ChartResult(warnings=[f"图表生成失败: {e}"])

    html = _build_html(title, "scatter_plot", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "scatter_plot",
        "n_rows": len(df_plot),
        "x_col": _x,
        "y_col": _y,
        "size_col": _size,
        "color_col": _color,
        "has_trendline": show_trendline,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
