"""
瀑布图 Waterfall Chart - 累积变化图表
"""
import logging
log = logging.getLogger(__name__)
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
            "#003D7A", "#0084D1", "#00A4EF", "#7FBA00",
            "#FFB81C", "#F7630C", "#DA3B01", "#A4373A",
            "#6B2C91", "#00B4EF",
        ]}

__all__ = ["generate"]

_DATA_FMT = "x列(阶段名称) + y列(数值) + 可选type列(initial/increase/decrease/total)"
_DESC = "展示数值从起点经过增减变化到终点的过程，通过柱子的堆叠展示累积效果。"

MCKINSEY_COLORS = {
    "increase": "#7FBA00",
    "decrease": "#DA3B01",
    "total":    "#003D7A",
}


def _auto_col(df: pd.DataFrame, role: str, exclude: set = None) -> Optional[str]:
    exclude = exclude or set()
    strs = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c])) and c not in exclude]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c not in exclude]
    col_lower = {str(c).lower(): c for c in df.columns if c not in exclude}

    if role == "x":
        for hint in ["x", "label", "stage", "category", "阶段", "类别", "名称", "标签"]:
            if hint in col_lower:
                return col_lower[hint]
        return strs[0] if strs else (nums[0] if nums else None)
    elif role == "y":
        for hint in ["y", "value", "amount", "数值", "金额", "变动", "人数", "数量"]:
            if hint in col_lower:
                return col_lower[hint]
        return nums[0] if nums else (strs[0] if strs else None)
    elif role == "type":
        for hint in ["type", "measure", "类型", "度量"]:
            if hint in col_lower:
                return col_lower[hint]
        return strs[0] if strs else None
    return None


def _build_html(title: str, embed: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>{title}</title>
<style>body{{font-family:"Heiti SC","Microsoft YaHei",sans-serif;margin:40px;background:#fafafa}}
.chart-wrap{{background:white;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08);padding:24px}}
h1{{color:#222;font-size:22px;margin-bottom:6px}}</style></head>
<body><div class="chart-wrap"><h1>{title}</h1>{embed}</div></body></html>"""


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    x: str = "x",
    y: str = "y",
    type_col: str = None,
    title: str = "瀑布图",
    **kwargs
) -> ChartResult:
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
    type_col_name = mapping.get("type")
    type_col_name = type_col_name if type_col_name is not None else type_col
    title = options.get("title", title)

    exclude_set = set()
    _x = (x_col if x_col != "x" and x_col in df.columns else _auto_col(df, "x", exclude_set))
    if _x:
        exclude_set.add(_x)

    _y = (y_col if y_col != "y" and y_col in df.columns else _auto_col(df, "y", exclude_set))
    if _y:
        exclude_set.add(_y)

    if not type_col_name:
        type_col_name = _auto_col(df, "type", {_x, _y})
    _type = type_col_name if type_col_name and type_col_name in df.columns and type_col_name not in {_x, _y} else None

    if not _x or _x not in df.columns:
        return ChartResult(warnings=["找不到x列"])
    if not _y or _y not in df.columns:
        return ChartResult(warnings=["找不到y列"])

    try:
        cols_to_use = [_x, _y]
        if _type:
            cols_to_use.append(_type)

        # 配色方案：优先用 options.color_scheme
        _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
        _increase_color = _scheme.get("positive", MCKINSEY_COLORS["increase"])
        _decrease_color = _scheme.get("negative", MCKINSEY_COLORS["decrease"])
        _total_color = _scheme.get("primary", MCKINSEY_COLORS["total"])

        df_plot = df[cols_to_use].copy()
        df_plot[_y] = pd.to_numeric(df_plot[_y], errors="coerce")
        df_plot = df_plot.dropna(subset=[_x, _y])

        if df_plot.empty:
            return ChartResult(warnings=["无有效数据"])

        x_vals = list(df_plot[_x].astype(str))
        y_vals = list(df_plot[_y].astype(float))
        n = len(x_vals)

        if n < 2:
            return ChartResult(warnings=["瀑布图至少需要2行数据（起点+终点）"])

        # 1) 构造 measure：首行 absolute，中间 relative，末行 total
        measures = ["relative"] * n
        measures[0] = "absolute"
        measures[-1] = "total"

        # 可选：若有 type 列，仅允许覆盖中间行；首尾强制不变
        if _type:
            for i in range(1, n - 1):
                t = str(df_plot.iloc[i][_type]).strip().lower()
                if t in {"absolute", "abs", "initial", "start", "起始", "期初"}:
                    measures[i] = "absolute"
                elif t in {"total", "subtotal", "汇总", "小计", "总计"}:
                    measures[i] = "total"
                else:
                    measures[i] = "relative"

        # 2) 计算每个柱子的“当前累计值”（用于 text 和 hover）
        running_totals = []
        acc = 0.0
        for i in range(n):
            m = measures[i]
            v = y_vals[i]
            if m == "absolute":
                acc = v
            elif m == "relative":
                acc += v
            elif m == "total":
                # 对 total 行，显示该行给定的最终值
                # 若你希望“严格按前面累计”可改成：acc = acc
                acc = v if pd.notna(v) else acc
            running_totals.append(acc)

        text_vals = [f"{t:,.0f}" for t in running_totals]
        customdata = list(zip(y_vals, running_totals))  # [变动值, 当前累计]

        fig = go.Figure(go.Waterfall(
            x=x_vals,
            y=y_vals,
            measure=measures,
            text=text_vals,
            textposition="outside",   # 显示当前累计值
            cliponaxis=False,
            increasing=dict(marker=dict(color=_increase_color, line=dict(color="white", width=1))),
            decreasing=dict(marker=dict(color=_decrease_color, line=dict(color="white", width=1))),
            totals=dict(marker=dict(color=_total_color, line=dict(color="white", width=1))),
            connector=dict(visible=True, line=dict(color="#cccccc", width=1)),
            customdata=customdata,
            hovertemplate="<b>%{x}</b><br>变动: %{customdata[0]:,.0f}<br>当前: %{customdata[1]:,.0f}<extra></extra>",
            **kwargs
        ))

        fig.update_layout(
            title=title,
            xaxis_title=_x,
            yaxis_title=_y,
            font_family="Heiti SC, Microsoft YaHei, sans-serif",
            plot_bgcolor="white",
            paper_bgcolor="white",
            margin=dict(l=50, r=50, t=70, b=80),
            hovermode="closest",
            title_font_size=16,
            waterfallgap=0.3,
            xaxis=dict(tickangle=-30),
        )
        fig.update_yaxes(zeroline=True, zerolinecolor="#dddddd")

        chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
        chart_html = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>' + chart_html

        if not chart_html or len(chart_html) < 100:
            return ChartResult(warnings=["图表生成失败"])

    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        return ChartResult(warnings=[f"图表生成失败: {e}"])

    html = _build_html(title, chart_html)
    meta = {
        "chart_id": "waterfall",
        "n_rows": len(df_plot),
        "x_col": _x,
        "y_col": _y,
        "type_col": _type,
        "measures": dict(zip(x_vals, measures)),
        "running_totals": dict(zip(x_vals, running_totals)),
        "data_format": _DATA_FMT,
        "description": _DESC,
    }
    return ChartResult(html=html, spec={}, warnings=[], meta=meta)