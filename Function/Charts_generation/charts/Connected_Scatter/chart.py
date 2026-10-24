"""
连线散点图 Connected Scatter - 演变过程图表
图表分类: 演变 Evolution
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult

使用示例:
    from charts.connected_scatter import generate
    from charts import ChartResult

    result = generate(
        df=df,
        mapping={"x": "销售额", "y": "利润", "order": "年份", "size": "人均GDP"},
        options={"title": "销售与利润演变"}
    )
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
import numpy as np

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult
from charts.color_schemes import get_color_scheme

__all__ = ["generate"]

_DATA_FMT = "x列(数值) + y列(数值) + order列(排序/时间) + 可选size列(气泡大小)"
_DESC = "在散点基础上用线段连接各点，展示数据的演变过程或轨迹。适合展示有序路径、时间序列或因果关系。"


def _norm(s: str) -> str:
    return str(s).strip().lower().replace("_", "").replace(" ", "")


def _auto_col(df: pd.DataFrame, role: str, exclude: set = None) -> Optional[str]:
    """根据角色自动查找匹配的列名（支持大小写/中英文/包含匹配）。"""
    exclude = exclude or set()
    cols = [c for c in df.columns if c not in exclude]
    nums = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    objs = [c for c in cols if not pd.api.types.is_numeric_dtype(df[c])]

    hints_map = {
        "x": ["x", "value", "amount", "sales", "数值", "销售额", "金额", "预期寿命"],
        "y": ["y", "profit", "利润", "value", "amount", "数值", "预期寿命"],
        "order": ["order", "time", "date", "year", "month", "sequence", "排序", "时间", "日期", "年份"],
        "size": ["size", "value", "amount", "大小", "数值", "权重", "volume", "人均GDP"],
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
        if objs:
            return objs[0]
    elif role == "order":
        # 优先可解析为 datetime 的列
        best_dt_col = None
        best_dt_ratio = 0.0
        for c in cols:
            s = df[c].astype(str).str.strip()
            dt = pd.to_datetime(s, errors="coerce")
            ratio = dt.notna().mean()
            if ratio > best_dt_ratio:
                best_dt_ratio = ratio
                best_dt_col = c
        if best_dt_col is not None and best_dt_ratio >= 0.8:
            return best_dt_col

        # 再选"像年份"的数值列（1900~2100）
        year_like = []
        for c in nums:
            s = pd.to_numeric(df[c], errors="coerce")
            ratio = s.between(1900, 2100).mean()
            year_like.append((ratio, c))
        year_like.sort(reverse=True)
        if year_like and year_like[0][0] >= 0.8:
            return year_like[0][1]

        # 最后才兜底
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


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    x: str = "x",
    y: str = "y",
    order: str = None,
    size: str = None,
    title: str = "连线散点图",
    **kwargs
) -> ChartResult:
    """
    生成连线散点图。
    
    参数:
        df: DataFrame
        mapping: {"x": "列名", "y": "列名", "order": "排序列", "size": "气泡大小列"}
        options: {"title": "标题", "color_scheme": "配色方案"}
        excel_path: Excel文件路径
        x, y, order, size: 列名（如果mapping中没有指定）
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
    order_col = mapping.get("order")
    order_col = order_col if order_col is not None else order
    size_col = mapping.get("size")
    size_col = size_col if size_col is not None else size
    title = options.get("title", title)
    color_scheme_name = options.get("color_scheme", "mckinsey")

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

    _order = order_col if order_col and order_col in df.columns else _auto_col(df, "order", exclude_set)
    if _order:
        exclude_set.add(_order)

    _size = size_col if size_col and size_col in df.columns else _auto_col(df, "size", exclude_set)
    if _size:
        exclude_set.add(_size)

    # 数据验证
    try:
        df[_x] = pd.to_numeric(df[_x], errors="coerce")
        df[_y] = pd.to_numeric(df[_y], errors="coerce")
        if df[_x].isna().all() or df[_y].isna().all():
            return ChartResult(warnings=["x或y列包含非数值数据"])
    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        return ChartResult(warnings=[f"数据转换失败: {e}"])

    # 按order排序
    if _order:
        try:
            # 尝试转换为数值排序
            df_sort = df.copy()
            df_sort["_order_val"] = pd.to_numeric(df_sort[_order], errors="coerce")
            if df_sort["_order_val"].notna().sum() > 0:
                df_plot = df_sort.sort_values("_order_val").drop("_order_val", axis=1)
            else:
                # 按字符串排序
                df_plot = df.sort_values(_order)
        except Exception as e:
            log.warning("[chart] 图表生成异常: %s", e)
            warnings.append(f"排序失败: {e}")
            df_plot = df
    else:
        df_plot = df

    # 删除NaN行
    df_plot = df_plot.dropna(subset=[_x, _y])
    if len(df_plot) < 2:
        return ChartResult(warnings=["有效数据点少于2个"])

    # 处理气泡大小
    marker_sizes = [15] * len(df_plot)  # 默认大小
    if _size:
        try:
            size_vals = pd.to_numeric(df_plot[_size], errors="coerce")
            if size_vals.notna().sum() > 0:
                size_min, size_max = size_vals.min(), size_vals.max()
                if size_max > size_min:
                    # 映射到 8-40 范围
                    marker_sizes = 8 + (size_vals - size_min) / (size_max - size_min) * 32
                    marker_sizes = marker_sizes.fillna(15).tolist()
                else:
                    marker_sizes = [15] * len(df_plot)
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

        # 提取数据
        x_data = df_plot[_x].values
        y_data = df_plot[_y].values
        order_data = df_plot[_order].values if _order else list(range(len(df_plot)))
        size_data = df_plot[_size].values if _size else None

        # 构建悬停文本
        hover_texts = []
        for i in range(len(df_plot)):
            hover_text = f"<b>{_x}</b>: {x_data[i]:.2f}<br>"
            hover_text += f"<b>{_y}</b>: {y_data[i]:.2f}<br>"
            if _order:
                hover_text += f"<b>{_order}</b>: {order_data[i]}<br>"
            if _size:
                hover_text += f"<b>{_size}</b>: {size_data[i]:.2f}"
            hover_texts.append(hover_text)

        # 添加连线 (lines)
        fig.add_trace(go.Scatter(
            x=x_data,
            y=y_data,
            mode="lines",
            line=dict(color=primary_color, width=2),
            hoverinfo="skip",
            showlegend=False
        ))

        # 添加散点 (markers)
        fig.add_trace(go.Scatter(
            x=x_data,
            y=y_data,
            mode="markers",
            marker=dict(
                size=marker_sizes,
                color=primary_color,
                line=dict(color="white", width=2),
                opacity=0.8
            ),
            text=hover_texts,
            hovertemplate="%{text}<extra></extra>",
            showlegend=False
        ))

        # 起点和终点标记
        if len(df_plot) > 1:
            # 起点
            fig.add_trace(go.Scatter(
                x=[x_data[0]],
                y=[y_data[0]],
                mode="markers+text",
                marker=dict(size=20, color="#00B050", line=dict(color="white", width=2)),
                text=["START"],
                textposition="bottom right",
                textfont=dict(size=10, color="#00B050"),
                hoverinfo="skip",
                showlegend=False
            ))

            # 终点
            fig.add_trace(go.Scatter(
                x=[x_data[-1]],
                y=[y_data[-1]],
                mode="markers+text",
                marker=dict(size=20, color="#FF0000", line=dict(color="white", width=2)),
                text=["END"],
                textposition="top left",
                textfont=dict(size=10, color="#FF0000"),
                hoverinfo="skip",
                showlegend=False
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
            height=500
        )

        chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
        if not chart_html or len(chart_html) < 100:
            return ChartResult(warnings=["图表生成失败"])

    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        return ChartResult(warnings=[f"图表生成失败: {e}"])

    html = _build_html(title, "connected_scatter", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "connected_scatter",
        "n_rows": len(df_plot),
        "x_col": _x,
        "y_col": _y,
        "order_col": _order,
        "size_col": _size,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
