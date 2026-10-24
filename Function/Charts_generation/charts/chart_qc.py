# -*- coding: utf-8 -*-
"""
图表质检（post-render QC）与复杂度预算

参考 diagram-design 的 self_check 思路：渲染产出后做自动化检查，把问题以
warnings 形式回传。本项目更进一步 —— warnings 会进入 agent 工具结果，
LLM 在同一轮里就能自动修正（top-N 聚合、过滤 NULL、重命名重复系列后重新
generate_chart），形成自愈闭环。

设计约束：
- 只做"建议级"检查，绝不把本来能渲染的图变成 error（渲染失败由各模块
  自己的 warnings/返回值负责）。
- 检查基于 theme.postprocess_plotly_html 解析出的 figure dict，不重复解析。
- 复杂度预算（CHART_BUDGETS）对齐 chart_selector 注册表 constraints 与
  agent 侧 CHART_RULES 的口径，超限提示 LLM 聚合或拆分，而不是后端偷偷改数。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

__all__ = ["CHART_BUDGETS", "DEFAULT_BUDGETS", "qc_figures"]


# 超过即告警的默认预算（图表未单独配置时适用）
DEFAULT_BUDGETS = {"categories": 40, "series": 8, "points": 500}

# 按图表类型的复杂度预算（key 为 chart_id 小写形式）。
# 类别数 = x/label 去重后的取值数；系列数 = 命名 trace 数。
# 口径与 diagram-design 的 complexity budget 一致：超限不是错误，
# 而是提示"聚合或拆分"。
CHART_BUDGETS: Dict[str, Dict[str, int]] = {
    # 占比类：类别多时饼图不可读
    "pie_chart": {"categories": 8},
    "pie": {"categories": 8},
    "donut_chart": {"categories": 8},
    "donut": {"categories": 8},
    "waffle": {"categories": 8},
    "nightingale_chart": {"categories": 12},
    # 柱状类
    "bar_chart": {"categories": 20},
    "bar": {"categories": 20},
    "diverging_bar_chart": {"categories": 20},
    "grouped_bar_chart": {"categories": 10, "series": 4},
    "stacked_bar_chart": {"categories": 12, "series": 6},
    "marimekko_abs": {"categories": 12, "series": 6},
    "marimekko_pct": {"categories": 12, "series": 6},
    "bullet_chart": {"categories": 8},
    "dot_plot": {"categories": 50},
    # 折线/面积类
    "line_chart": {"series": 6},
    "circular_line_chart": {"series": 6},
    "area_chart": {"series": 4},
    "stacked_area_chart": {"series": 5},
    "horizon_chart": {"series": 12},
    "bump_chart": {"series": 12},
    "slope_chart": {"series": 8},
    "cycle_chart": {"series": 6},
    # 平行坐标：按分组着色时一个组一条图例项，行数=数据量而非系列数，
    # 预算放宽到只在极端分组数时提示
    "parallel_coordinates_plot": {"series": 24},
    # 关系/网络类
    "sankey_chart": {"series": 24},       # 节点数
    "chord_diagram": {"series": 12},
    "network_diagram": {"series": 30},
    "arc_chart": {"series": 16},
    # 层级类
    "treemap": {"categories": 30},
    "sunburst_diagram": {"categories": 30},
    "pyramid_chart": {"categories": 12},
    # 散点类：模块会自动按字符串列分色（一个类别一个 trace），系列预算
    # 放宽到类别量级，只保留散点数预算作为有效约束
    "scatter_plot": {"points": 500, "series": 40},
    "bubble_plot": {"points": 300, "series": 40},
    "connected_scatter": {"points": 300, "series": 40},
}

# 视为"类别轴"的 trace 类型（用 x/labels 统计类别数）
_CATEGORICAL_TYPES = {"bar", "pie", "funnel", "funnelarea"}
# NULL 类别的字符串形态
_BAD_LABELS = {"", "nan", "none", "null"}


def _budget_for(chart_type: str) -> Dict[str, int]:
    key = str(chart_type or "").strip().lower()
    budget = dict(DEFAULT_BUDGETS)
    budget.update(CHART_BUDGETS.get(key, {}))
    return budget


def _distinct_categories(trace: Dict[str, Any]) -> int:
    cats = trace.get("labels") if trace.get("type") == "pie" else trace.get("x")
    if not isinstance(cats, list):
        return 0
    seen = set()
    for c in cats:
        s = str(c).strip().lower()
        if s not in _BAD_LABELS:
            seen.add(str(c))
    return len(seen)


def qc_figures(
    chart_type: str,
    figures: List[Dict[str, Any]],
    html: Optional[str] = None,
    prior_warnings: Optional[List[str]] = None,
) -> List[str]:
    """对解析出的 plotly figure 做质检，返回中文 warnings（可能为空）。

    检查项：无数据系列、缺标题、空/NULL 类别标签、重复系列名、类别/系列/
    散点数超复杂度预算、单点折线。``prior_warnings`` 是模块自己产出的警告，
    已覆盖同一问题的（如 Pie 模块的"类别过多"）不再重复提示。任何内部异常
    都吞掉 —— QC 绝不制造新错误。
    """
    warnings: List[str] = []
    try:
        prior = " ".join(str(w) for w in (prior_warnings or []))
        module_flagged_categories = "类别过多" in prior or "类别数" in prior
        if not figures:
            # 有 newPlot 却没解析出 figure，说明 JSON 形态异常
            if html and "Plotly.newPlot" in html:
                warnings.append("plotly figure 解析失败，未能执行自动质检（不影响渲染）")
            return warnings

        budget = _budget_for(chart_type)
        chart_key = str(chart_type or "").strip().lower()

        for fig in figures:
            traces = [t for t in (fig.get("data") or []) if isinstance(t, dict)]
            if not traces:
                warnings.append("图表没有任何数据系列（trace），渲染结果可能为空白")
                continue

            # ── 标题 ──
            layout = fig.get("layout") or {}
            title = layout.get("title")
            if isinstance(title, str):
                title_text = title.strip()
            elif isinstance(title, dict):
                title_text = str(title.get("text") or "").strip()
            else:
                title_text = ""
            if not title_text and "<h1" not in (html or ""):
                warnings.append("图表缺少标题，请通过 generate_chart 的 title 参数补充")

            # ── 重复系列名 / 系列计数 ──
            # 只看图例可见的 trace：showlegend=False 的 trace 是结构组件
            # （Ridgeline 的轮廓线、Horizon 的分层填充、Parallel 的组内
            # 非首条线等），不是独立系列，计入会造成"重复系列名/系列超
            # 预算"误报。图例隐藏的命名对（密度填充+轮廓同名的另一半）
            # 也不再算重复。
            legend_traces = [t for t in traces if t.get("showlegend") is not False]
            names = [str(t.get("name")).strip() for t in legend_traces if t.get("name")]
            dupes = sorted({n for n in names if names.count(n) > 1})
            if dupes:
                warnings.append(
                    f"存在重复系列名: {', '.join(dupes)}，图例无法区分，建议在 SQL 中重命名后重新生成"
                )

            # ── 空 / NULL 类别标签 ──
            # 只有含字符串的轴才按类别轴检查：全 None 的幽灵 trace
            # （Waffle/Horizon 的图例代理 x=[None]）没有类别轴；哑铃/折线
            # 的连接线 x 是数值轴且用 None 作分隔符，都不是 NULL 类别
            bad_labels = 0
            for t in traces:
                cats = t.get("labels") if t.get("type") == "pie" else t.get("x")
                if not isinstance(cats, list) or not any(isinstance(c, str) for c in cats):
                    continue
                bad_labels += sum(
                    1 for c in cats if str(c).strip().lower() in _BAD_LABELS
                )
            if bad_labels:
                warnings.append(
                    f"存在 {bad_labels} 个空/NULL 类别标签，建议 SQL 加 WHERE 过滤后重新生成"
                )

            # ── 复杂度预算 ──
            # 类别预算只对类别型 trace 生效：折线/散点的 x 是连续时间/数值轴，
            # 365 个日期点是正常形态，不算类别超限
            n_categories = max(
                (_distinct_categories(t) for t in traces
                 if str(t.get("type") or "").lower() in _CATEGORICAL_TYPES),
                default=0,
            )
            n_series = len(names) if names else (len(legend_traces) if len(legend_traces) > 1 else 1)
            limit_categories = budget["categories"]
            limit_series = budget["series"]
            if n_categories > limit_categories and not module_flagged_categories:
                warnings.append(
                    f"类别数 {n_categories} 超过 {chart_key} 的复杂度预算 {limit_categories}，"
                    f"建议 SQL 聚合为 Top {max(limit_categories - 1, 1)} + '其他' 后重新生成"
                )
            if n_series > limit_series:
                warnings.append(
                    f"系列数 {n_series} 超过 {chart_key} 的复杂度预算 {limit_series}，"
                    "建议只保留关键系列或拆分为多张图"
                )
            if chart_key in {"scatter_plot", "bubble_plot", "connected_scatter"}:
                n_points = max(
                    (len(t.get("x") or []) for t in traces if isinstance(t.get("x"), list)),
                    default=0,
                )
                if n_points > budget["points"]:
                    warnings.append(
                        f"散点数 {n_points} 过多（建议 ≤{budget['points']}），建议采样或聚合后重新生成"
                    )

            # ── 单点折线 ──
            for t in traces:
                if str(t.get("type") or "") == "scatter" and "lines" in str(t.get("mode") or ""):
                    xs = t.get("x")
                    if isinstance(xs, list) and len(xs) == 1:
                        warnings.append("折线图只有 1 个数据点，无法展示趋势，建议扩大时间范围")
                        break
    except Exception as exc:  # pragma: no cover —— QC 兜底
        log.debug(f"chart QC skipped due to internal error: {exc}")
    return warnings
