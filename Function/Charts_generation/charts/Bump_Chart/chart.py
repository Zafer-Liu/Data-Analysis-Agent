"""
凹凸图 Bump Chart - 排名变化图表
图表分类: 排名 Ranking
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult

使用示例:
    from charts.bump_chart import generate
    from charts import ChartResult

    result = generate(
        df=df,
        mapping={"x": "年份", "y": "排名", "group": "产品"},
        options={"title": "产品排名变化"}
    )
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

_DATA_FMT = "长格式：x列(时间) + y列(排名/分数) + group列(实体名称) | 宽格式：首列为时间，其余列为实体数据"
_DESC = "展示多个实体的排名随时间的变化，通过连接线展示排名变化趋势。适合展示相对排名而非绝对值。支持长格式和宽格式数据。"

# 麦肯锡配色方案
MCKINSEY_COLORS = [
    "#003D7A",  # 深蓝
    "#0084D1",  # 中蓝
    "#00A4EF",  # 浅蓝
    "#7FBA00",  # 绿色
    "#FFB81C",  # 金色
    "#F7630C",  # 橙色
    "#DA3B01",  # 红色
    "#A4373A",  # 深红
    "#6B2C91",  # 紫色
    "#00B4EF",  # 青色
]


def _auto_col(df: pd.DataFrame, role: str, exclude: set = None) -> Optional[str]:
    """根据角色自动查找匹配的列名。
    
    参数：
        role: 'x' (时间), 'y' (排名/分数), 'group' (实体)
        exclude: 已使用的列名集合
    """
    exclude = exclude or set()
    strs = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c])) and c not in exclude]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c not in exclude]
    col_lower = {str(c).lower(): c for c in df.columns if c not in exclude}
    
    if role == "x":
        # x 优先查找时间列
        time_hints = ["date", "time", "month", "year", "week", "day", "period", "时间", "日期", "月份", "年份", "年度"]
        for hint in time_hints:
            if str(hint).lower() in col_lower:
                return col_lower[str(hint).lower()]
        # 回退到第一个字符串列
        if strs:
            return strs[0]
        if nums:
            return nums[0]
    
    elif role == "y":
        # y 优先查找排名/分数列
        rank_hints = ["rank", "ranking", "score", "value", "排名", "分数", "评分", "数值"]
        for hint in rank_hints:
            if str(hint).lower() in col_lower:
                return col_lower[str(hint).lower()]
        # 回退到第一个数值列
        if nums:
            return nums[0]
        if strs:
            return strs[0]
    
    elif role == "group":
        # group 优先查找实体/分组列
        group_hints = ["group", "entity", "name", "category", "country", "brand", "team", "分组", "实体", "名称", "国家", "品牌", "球队"]
        for hint in group_hints:
            if str(hint).lower() in col_lower:
                return col_lower[str(hint).lower()]
        # 回退到第一个字符串列
        if strs:
            return strs[0]
        if nums:
            return nums[0]
    
    return None


def _is_wide_format(df: pd.DataFrame, x_col: str) -> bool:
    """判断是否为宽格式数据。
    
    宽格式特征：
    - 首列为时间/类别（x_col）
    - 其余列都是数值列
    """
    if x_col not in df.columns:
        return False
    
    other_cols = [c for c in df.columns if c != x_col]
    if not other_cols:
        return False
    
    # 检查其余列是否都是数值
    all_numeric = all(pd.api.types.is_numeric_dtype(df[c]) for c in other_cols)
    return all_numeric


def _wide_to_long(df: pd.DataFrame, x_col: str) -> pd.DataFrame:
    """将宽格式数据转换为长格式。
    
    宽格式：
    | 年份 | 产品A | 产品B | 产品C |
    | 2018 | 1 | 2 | 3 |
    
    长格式：
    | 年份 | 产品 | 排名 |
    | 2018 | 产品A | 1 |
    | 2018 | 产品B | 2 |
    """
    other_cols = [c for c in df.columns if c != x_col]
    
    # 使用 melt 转换
    df_long = df.melt(
        id_vars=[x_col],
        value_vars=other_cols,
        var_name='group',
        value_name='value'
    )
    
    return df_long


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
    # ── 向后兼容旧接口 ──────────────────────────────
    excel_path: str = None,
    x: str = "x",
    y: str = "y",
    group: str = "group",
    title: str = "凹凸图",
    highlight: List[str] = None,
    **kwargs
) -> ChartResult:
    warnings: list = []
    options = options or {}
    mapping = mapping or {}
    highlight = highlight or options.get("highlight", [])

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
    group_col = mapping.get("group")
    group_col = group_col if group_col is not None else group
    title = options.get("title", title)

    # 新增：模式参数
    mode = options.get("mode", "full")  # "full" | "topn_anchor"
    anchor_x = options.get("anchor_x", None)
    top_n = int(options.get("top_n", 10))
    ascending = bool(options.get("ascending", False))   # False: 值大排名靠前
    rank_method = options.get("rank_method", "first")   # first|min|dense

    # 自动检测 x 列
    exclude_set = set()
    _x = x_col if x_col and x_col != "x" and x_col in df.columns else _auto_col(df, "x", exclude_set)

    if _x is None or _x not in df.columns:
        warnings.append("找不到x列（时间）")
        return ChartResult(warnings=warnings)

    exclude_set.add(_x)

    # 检查是否为宽格式
    if _is_wide_format(df, _x):
        warnings.append("检测到宽格式数据，自动转换为长格式")
        df = _wide_to_long(df, _x)
        y_col = "value"
        group_col = "group"

    # 自动检测 y 和 group 列
    _y = y_col if y_col and y_col != "y" and y_col in df.columns else _auto_col(df, "y", exclude_set)
    if _y:
        exclude_set.add(_y)

    _group = group_col if group_col and group_col != "group" and group_col in df.columns else _auto_col(df, "group", exclude_set)

    if _y is None or _y not in df.columns:
        warnings.append("找不到y列（排名/分数）")
        return ChartResult(warnings=warnings)
    if _group is None or _group not in df.columns:
        warnings.append("找不到group列（实体名称）")
        return ChartResult(warnings=warnings)

    try:
        # 准备数据
        df_plot = df[[_x, _y, _group]].copy()
        df_plot[_y] = pd.to_numeric(df_plot[_y], errors='coerce')
        df_plot = df_plot.dropna(subset=[_x, _y, _group])

        if df_plot.empty:
            return ChartResult(warnings=["无有效数据"])

        # 判断 y 是否已是“排名列”
        y_name = str(_y).lower()
        is_rank_input = any(k in y_name for k in ["rank", "ranking", "排名", "名次", "位次"])

        if is_rank_input:
            df_plot["rank"] = pd.to_numeric(df_plot[_y], errors="coerce")
        else:
            df_plot["rank"] = df_plot.groupby(_x)[_y].rank(
                method=rank_method,
                ascending=ascending
            )

        df_plot = df_plot.dropna(subset=["rank"])
        if df_plot.empty:
            return ChartResult(warnings=["排名计算后无有效数据"])

        # 不强制 int，避免 dense/min 以外策略时丢精度
        df_plot["rank"] = pd.to_numeric(df_plot["rank"], errors="coerce")

        # x 顺序：优先 datetime；否则保持原出现顺序（比 sorted 更稳）
        x_order = pd.Series(df_plot[_x].drop_duplicates())
        try:
            x_order_dt = pd.to_datetime(x_order, errors="raise")
            ordered_x = list(x_order.iloc[x_order_dt.argsort()])
        except Exception as e:
            log.warning("[chart] 图表生成异常: %s", e)
            ordered_x = list(x_order)

        # topn_anchor 模式：只保留 anchor_x 时点 top_n 实体
        if mode == "topn_anchor":
            if anchor_x is None:
                anchor_x = ordered_x[-1] if ordered_x else None

            anchor_df = df_plot[df_plot[_x] == anchor_x].copy()
            if anchor_df.empty:
                warnings.append(f"anchor_x={anchor_x} 不存在，回退 full 模式")
            else:
                keep_groups = (
                    anchor_df.sort_values("rank", ascending=True)
                    .head(top_n)[_group]
                    .tolist()
                )
                df_plot = df_plot[df_plot[_group].isin(keep_groups)].copy()
                warnings.append(f"topn_anchor: 追踪 {anchor_x} Top{top_n}，共 {len(keep_groups)} 个实体")

        if df_plot.empty:
            return ChartResult(warnings=["过滤后无数据"])

        n_groups = df_plot[_group].nunique()
        if n_groups > 15:
            warnings.append(f"实体数过多({n_groups}个)，建议 ≤15")

        # 绘图
        # 配色方案：优先用 options.color_scheme，回退到 MCKINSEY_COLORS
        _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
        _palette = _scheme.get("colors") or MCKINSEY_COLORS

        fig = go.Figure()
        groups = sorted(df_plot[_group].astype(str).unique())

        for idx, group_val in enumerate(groups):
            subset = df_plot[df_plot[_group].astype(str) == group_val].copy()
            subset["_x_order"] = pd.Categorical(subset[_x], categories=ordered_x, ordered=True)
            subset = subset.sort_values("_x_order")

            if subset.empty:
                continue

            if highlight and group_val in [str(h) for h in highlight]:
                color = _palette[0]
                width = 4.5
                opacity = 1.0
            else:
                color = _palette[(idx + 1) % len(_palette)]
                width = 2.8
                opacity = 0.9

            fig.add_trace(go.Scatter(
                x=subset[_x],
                y=subset["rank"],
                mode='lines+markers',
                name=str(group_val),
                line=dict(color=color, width=width),
                marker=dict(size=8, color=color, line=dict(width=1.2, color="white")),
                opacity=opacity,
                hovertemplate=(
                    f"<b>{group_val}</b><br>"
                    f"{_x}: %{{x}}<br>"
                    f"排名: %{{y}}<br>"
                    f"{_y}: %{{customdata}}<extra></extra>"
                ),
                customdata=subset[_y]
            ))

        max_rank = int(pd.to_numeric(df_plot["rank"], errors="coerce").max())

        fig.update_layout(
            title=title,
            xaxis_title=_x,
            yaxis_title="排名",
            font_family="Heiti SC, Microsoft YaHei, sans-serif",
            plot_bgcolor="white",
            paper_bgcolor="white",
            margin=dict(l=50, r=50, t=70, b=50),
            hovermode="x unified",
            showlegend=True,
            legend=dict(
                orientation="v",
                yanchor="top",
                y=0.99,
                xanchor="left",
                x=1.01,
                bgcolor="rgba(255,255,255,0.8)",
                bordercolor="rgba(0,0,0,0.1)",
                borderwidth=1
            ),
            title_font_size=16
        )

        fig.update_yaxes(
            autorange="reversed",
            dtick=1,
            tick0=1,
            range=[max_rank + 0.5, 0.5]
        )

        chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
        if not chart_html or len(chart_html) < 100:
            return ChartResult(warnings=["图表生成失败"])

    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        return ChartResult(warnings=[f"图表生成失败: {e}"])

    html = _build_html(title, "bump_chart", "plotly", _DATA_FMT, _DESC, chart_html)


    meta = {
        "chart_id": "bump_chart",
        "n_rows": len(df),
        "x_col": _x,
        "y_col": _y,
        "group_col": _group,
        "n_groups": n_groups,
        "mode": mode,
        "anchor_x": anchor_x,
        "top_n": top_n,
        "is_rank_input": is_rank_input,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)