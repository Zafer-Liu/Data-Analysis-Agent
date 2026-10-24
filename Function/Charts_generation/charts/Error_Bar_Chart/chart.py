"""
误差条形图 Error Bar Chart - 分布
图表分类: 分布 Distribution | 书章节: Ch5
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult

数据格式：
  - x列：分组类别
  - y列：原始数值（系统自动计算Q25、Q50、Q75）
  
系统会自动：
  1. 按 x 分组
  2. 计算每组的 Q25（下界）、Q50（中位数）、Q75（上界）
  3. 绘制误差条（Q25-Q75）和中位数柱子
"""
import logging
log = logging.getLogger(__name__)
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

_DATA_FMT = "x列(分组类别) + y列(原始数值) - 系统自动计算Q25、Q50、Q75"
_DESC = "展示分组数据的中位数及其四分位数范围。误差条自动计算为Q25-Q75区间。"


def _auto_col(df: pd.DataFrame, *hints: str) -> Optional[str]:
    """根据提示自动查找匹配的列名。"""
    strs = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    col_lower = {str(c).lower(): c for c in df.columns}
    
    # 1. 精确匹配 hints
    for h in hints:
        if h is None:
            continue
        h_lower = str(h).lower()
        if h_lower in col_lower:
            return col_lower[h_lower]
    
    # 2. 模糊匹配（包含关系）
    for h in hints:
        if h is None:
            continue
        h_lower = str(h).lower()
        for col in df.columns:
            col_lower_name = str(col).lower()
            if h_lower in col_lower_name or col_lower_name in h_lower:
                return col
    
    # 3. 类型匹配
    if hints and hints[0] is not None:
        hint = str(hints[0]).lower()
        if any(kw in hint for kw in ["source", "target", "label", "name", "group", "category", "phase", "row", "col", "path", "text", "word", "location", "geo", "x"]):
            if strs:
                return strs[0]
            if nums:
                return nums[0]
        elif any(kw in hint for kw in ["value", "size", "amount", "count", "frequency", "score", "rank", "actual", "target", "range", "y", "mean", "avg"]):
            if nums:
                return nums[0]
            if strs:
                return strs[0]
    
    # 4. 无 hints 时自动推断
    if not hints or all(h is None for h in hints):
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


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    x: str = "x",
    y: str = "y",
    title: str = "误差条形图",
    orientation: str = "h",   # 默认改为横向，更像示例
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

    x_col = mapping.get("x")
    x_col = x_col if x_col is not None else x
    y_col = mapping.get("y")
    y_col = y_col if y_col is not None else y
    title = options.get("title", title)
    orientation = options.get("orientation", orientation)

    color_scheme_name = options.get("color_scheme", "mckinsey")
    try:
        color_scheme = get_color_scheme(color_scheme_name)
        bar_color = color_scheme.get("primary", "#B58F78")  # 更接近示例棕色
    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        warnings.append(f"配色方案加载失败: {e}，使用默认颜色")
        bar_color = "#B58F78"

    # 先按用户 mapping 显式指定
    if mapping.get("x") in df.columns:
        _x = mapping["x"]
    else:
        # x 优先选字符串列
        str_cols = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
        _x = str_cols[0] if str_cols else None

    if mapping.get("y") in df.columns:
        _y = mapping["y"]
    else:
        # y 优先选数值列，且不与 x 重复
        num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        _y = next((c for c in num_cols if c != _x), None)

    # 兜底（兼容中文列名）
    if _x is None:
        _x = _auto_col(df, "行业", "类别", "category", "group", "label", "name")
    if _y is None:
        _y = _auto_col(df, "收入", "值", "数值", "value", "amount", "y")

    if _x is None or _x not in df.columns:
        return ChartResult(warnings=warnings + ["找不到必填字段 [x]"])
    if _y is None or _y not in df.columns:
        return ChartResult(warnings=warnings + ["找不到必填字段 [y]"])

    df_plot = df[[_x, _y]].copy()
    df_plot.columns = ["group", "value"]
    df_plot["value"] = pd.to_numeric(df_plot["value"], errors="coerce")
    df_plot = df_plot.dropna()

    if df_plot.empty:
        return ChartResult(warnings=warnings + ["数据为空"])

    grouped_stats = (
        df_plot.groupby("group", as_index=False)["value"]
        .agg(
            q25=lambda s: s.quantile(0.25),
            q50=lambda s: s.quantile(0.50),
            q75=lambda s: s.quantile(0.75),
            count="size"
        )
    )
    if (grouped_stats["count"] < 2).all():
        warnings.append("每个分组仅1条记录，Q25=Q50=Q75，误差线长度为0（这是正常现象）。")
    grouped_stats["error_minus"] = grouped_stats["q50"] - grouped_stats["q25"]
    grouped_stats["error_plus"] = grouped_stats["q75"] - grouped_stats["q50"]

    # 关键：按中位数排序（高->低）
    grouped_stats = grouped_stats.sort_values("q50", ascending=False).reset_index(drop=True)

    fig = go.Figure()

    if orientation == "v":
        fig.add_trace(go.Bar(
            x=grouped_stats["group"],
            y=grouped_stats["q50"],
            error_y=dict(
                type="data",
                symmetric=False,
                array=grouped_stats["error_plus"],
                arrayminus=grouped_stats["error_minus"],
                color="#222",
                thickness=2.2,
                width=6
            ),
            marker=dict(color=bar_color),
            name="中位数",
            customdata=grouped_stats[["q25", "q75", "count"]],
            hovertemplate="<b>%{x}</b><br>Q50(中位数): %{y:,.0f}<br>Q25: %{customdata[0]:,.0f}<br>Q75: %{customdata[1]:,.0f}<br>样本数: %{customdata[2]}<extra></extra>",
            **kwargs
        ))
    else:
        fig.add_trace(go.Bar(
            y=grouped_stats["group"],
            x=grouped_stats["q50"],
            orientation="h",
            error_x=dict(
                type="data",
                symmetric=False,
                array=grouped_stats["error_plus"],
                arrayminus=grouped_stats["error_minus"],
                color="#222",
                thickness=2.2,
                width=6
            ),
            marker=dict(color=bar_color),
            name="中位数",
            customdata=grouped_stats[["q25", "q75", "count"]],
            hovertemplate="<b>%{y}</b><br>Q50(中位数): %{x:,.0f}<br>Q25: %{customdata[0]:,.0f}<br>Q75: %{customdata[1]:,.0f}<br>样本数: %{customdata[2]}<extra></extra>",
            **kwargs
        ))

    # 轴范围与刻度：数据驱动，不再硬编码 $20K 美元刻度。
    # 原实现固定按 20000 取整并标 $XXK，对百分比/计数/非美元数据产生荒谬坐标轴。
    max_q75 = float(grouped_stats["q75"].max())
    if not (max_q75 > 0):  # 全 0/NaN 退化为占位范围
        max_q75 = 1.0
    # 计算“美观”刻度步长（1/2/5 × 10^n），约 5 个主刻度
    def _nice_step(value):
        if value <= 0:
            return 1.0
        raw = value / 5.0
        mag = 10 ** np.floor(np.log10(raw))
        norm = raw / mag
        step = mag * (1 if norm < 1.5 else (2 if norm < 3 else (5 if norm < 7 else 10)))
        return float(step)

    step = _nice_step(max_q75)
    axis_max = float(np.ceil(max_q75 / step) * step + step)  # 留一格右侧余量
    tickvals = np.arange(0, axis_max + step / 2, step)

    fig.update_layout(
        title=title,
        font_family="Arial, Heiti SC, Microsoft YaHei, sans-serif",
        plot_bgcolor="#f3f3f3",
        paper_bgcolor="#f3f3f3",
        margin=dict(l=220, r=60, t=80, b=60),
        title_font_size=20,
        showlegend=False,
        bargap=0.55,   # 条更细，接近示例
        hovermode="closest"
    )

    if orientation == "h":
        fig.update_xaxes(
            range=[0, axis_max],
            tickvals=tickvals,
            gridcolor="#d9d9d9",
            zeroline=False
        )
        fig.update_yaxes(
            autorange="reversed",  # 高值在上
            gridcolor="rgba(0,0,0,0)"
        )
    else:
        fig.update_yaxes(
            range=[0, axis_max],
            tickvals=tickvals,
            gridcolor="#d9d9d9",
            zeroline=False
        )
        fig.update_xaxes(gridcolor="rgba(0,0,0,0)")

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "error_bar_chart", "plotly", _DATA_FMT, _DESC, chart_html)

    # 可选：导出静态图片（需要 kaleido）
    image_bytes = None
    if options.get("export_png", False):
        try:
            image_bytes = fig.to_image(format="png", scale=2)
        except Exception as e:
            log.warning("[chart] 图表生成异常: %s", e)
            warnings.append(f"PNG导出失败(需安装kaleido): {e}")

    meta = {
        "chart_id": "error_bar_chart",
        "n_groups": len(grouped_stats),
        "n_rows": len(df_plot),
        "x_col": _x,
        "y_col": _y,
        "color_scheme": color_scheme_name,
        "orientation": orientation
    }

    # 如果 ChartResult 暂不支持 image_bytes，可先不传
    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
