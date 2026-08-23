#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic two-arm A/B experiment analysis for unit-level data."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

ANALYSIS_ID = "AB_Test_Analysis"
ANALYSIS_NAME = "A/B 实验分析（AB Test Analysis）"
ANALYSIS_DESC = "比较两组单位级实验数据，输出 SRM、显著性检验、95% 置信区间与提升幅度。"
REQUIRED_PARAMS = ["target_column", "groupby_column"]
OPTIONAL_PARAMS = ["analysis_options.control_group", "analysis_options.metric_type", "analysis_options.expected_allocation"]
OUTPUT_TABLES = ["analysis_result", "analysis_breakdown", "analysis_metrics"]
_Z_975 = 1.959963984540054


def _normal_p(value: float) -> float:
    return math.erfc(abs(value) / math.sqrt(2))


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction used by the regularised incomplete beta function."""
    tiny, eps = 1e-300, 3e-14
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / max(d, tiny)
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 / max(1.0 + aa * d, tiny)
        c = max(1.0 + aa / c, tiny)
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 / max(1.0 + aa * d, tiny)
        c = max(1.0 + aa / c, tiny)
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(a * math.log(x) + b * math.log1p(-x) - math.lgamma(a) - math.lgamma(b) + math.lgamma(a + b))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _welch_two_sided_p(statistic: float, degrees_freedom: float) -> float:
    if not math.isfinite(statistic) or degrees_freedom <= 0:
        return math.nan
    x = degrees_freedom / (degrees_freedom + statistic ** 2)
    return _regularized_beta(x, degrees_freedom / 2, 0.5)


def _t_975(degrees_freedom: float) -> float:
    """Accurate t(0.975) approximation; converges to the normal critical value."""
    v, z = max(float(degrees_freedom), 1.0), _Z_975
    return z + (z**3 + z) / (4 * v) + (5*z**5 + 16*z**3 + 3*z) / (96 * v**2) + (3*z**7 + 19*z**5 + 17*z**3 - 15*z) / (384 * v**3)


def _groups(values: pd.Series, options: dict[str, Any]) -> tuple[str, str, str]:
    groups = sorted(values.dropna().astype(str).unique().tolist())
    if len(groups) != 2:
        raise ValueError(f"A/B 分析要求恰好两组，当前得到 {len(groups)} 组：{', '.join(groups)}")
    control = str(options.get("control_group", "")).strip()
    if control:
        if control not in groups:
            raise ValueError(f"对照组 '{control}' 不在分组中：{', '.join(groups)}")
        return control, next(item for item in groups if item != control), "specified"
    for candidate in ("control", "Control", "对照组", "对照", "A"):
        if candidate in groups:
            return candidate, next(item for item in groups if item != candidate), "inferred"
    return groups[0], groups[1], "alphabetical"


def _srm(counts: np.ndarray, group_names: list[str], options: dict[str, Any]) -> tuple[float, float]:
    allocation = options.get("expected_allocation")
    if allocation is None:
        expected = np.full(2, counts.sum() / 2)
    elif isinstance(allocation, dict):
        try:
            weights = np.array([float(allocation[name]) for name in group_names])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("expected_allocation 必须包含两个分组的正数权重") from exc
        if not np.isfinite(weights).all() or np.any(weights <= 0):
            raise ValueError("expected_allocation 的权重必须为有限正数")
        expected = counts.sum() * weights / weights.sum()
    else:
        raise ValueError("expected_allocation 必须是对象，例如 {'control': 1, 'treatment': 1}")
    chi2 = float(np.sum((counts - expected) ** 2 / expected))
    return chi2, math.erfc(math.sqrt(chi2 / 2))


def run(
    df: pd.DataFrame,
    target_column: str,
    groupby_column: str | None = None,
    n_deciles: int = 10,
    analysis_options: dict[str, Any] | None = None,
):
    """Analyse one numeric metric from exactly one row per randomisation unit."""
    del n_deciles
    options = analysis_options or {}
    if not isinstance(options, dict):
        raise ValueError("analysis_options 必须是对象。")
    if not groupby_column or groupby_column not in df or target_column not in df:
        raise ValueError("A/B 分析需要有效的 target_column 和 groupby_column（实验分组字段）。")

    work = df[[groupby_column, target_column]].copy()
    input_rows = len(work)
    work[groupby_column] = work[groupby_column].astype("string").str.strip()
    work[target_column] = pd.to_numeric(work[target_column], errors="coerce")
    work = work.loc[work[groupby_column].notna() & work[groupby_column].ne("")].dropna(subset=[target_column])
    if len(work) < 4:
        raise ValueError("有效单位少于 4，无法进行可靠的 A/B 分析。")

    control, treatment, control_source = _groups(work[groupby_column], options)
    work[groupby_column] = work[groupby_column].astype(str)
    series = work.groupby(groupby_column, observed=True)[target_column]
    result = series.agg(n="count", mean="mean", std="std", total="sum").reindex([control, treatment]).reset_index()
    result = result.rename(columns={groupby_column: "group"})
    result.insert(1, "role", ["control", "treatment"])
    n0, n1 = result["n"].astype(int).tolist()
    if min(n0, n1) < 2:
        raise ValueError("每个实验组至少需要 2 个有效单位。")
    counts = np.array([n0, n1], dtype=float)
    srm_chi2, srm_p = _srm(counts, [control, treatment], options)

    values = set(work[target_column].unique().tolist())
    requested_type = str(options.get("metric_type", "auto")).strip().lower()
    if requested_type not in {"auto", "binary", "continuous"}:
        raise ValueError("metric_type 只支持 auto、binary 或 continuous。")
    metric_type = "binary" if requested_type == "binary" or (requested_type == "auto" and values.issubset({0.0, 1.0})) else "continuous"
    if metric_type == "binary" and not values.issubset({0.0, 1.0}):
        raise ValueError("二元指标只能包含 0 和 1；请先按随机化单位聚合成功条件。")

    mean0, mean1 = result["mean"].astype(float).tolist()
    diff = mean1 - mean0
    relative = diff / mean0 if mean0 else math.nan
    if metric_type == "binary":
        success0, success1 = result["total"].astype(int).tolist()
        pooled = (success0 + success1) / (n0 + n1)
        se_pooled = math.sqrt(pooled * (1 - pooled) * (1 / n0 + 1 / n1))
        statistic = diff / se_pooled if se_pooled else 0.0
        p_value = _normal_p(statistic) if se_pooled else 1.0
        se_ci = math.sqrt(mean0 * (1 - mean0) / n0 + mean1 * (1 - mean1) / n1)
        ci_low, ci_high = diff - _Z_975 * se_ci, diff + _Z_975 * se_ci
        result["successes"] = result["total"].astype(int)
        result["conversion_rate"] = result["mean"]
        statistic_name, effect_size = "z_statistic", statistic
    else:
        std0, std1 = result["std"].astype(float).tolist()
        se_sq = std0 ** 2 / n0 + std1 ** 2 / n1
        se = math.sqrt(se_sq)
        denominator = (std0 ** 2 / n0) ** 2 / (n0 - 1) + (std1 ** 2 / n1) ** 2 / (n1 - 1)
        dof = se_sq ** 2 / denominator if denominator else n0 + n1 - 2
        critical = _t_975(dof)
        if se:
            statistic = diff / se
            p_value = _welch_two_sided_p(statistic, dof)
            ci_low, ci_high = diff - critical * se, diff + critical * se
        else:
            statistic = math.copysign(math.inf, diff) if diff else 0.0
            p_value = 0.0 if diff else 1.0
            ci_low = ci_high = diff
        pooled_sd = math.sqrt(((n0 - 1) * std0 ** 2 + (n1 - 1) * std1 ** 2) / (n0 + n1 - 2))
        statistic_name, effect_size = "welch_t_statistic", diff / pooled_sd if pooled_sd else math.nan

    significant = p_value < 0.05 and not ci_low <= 0 <= ci_high
    verdict = (
        "实验组在主指标上有统计证据优于对照组" if significant and diff > 0 else
        "实验组在主指标上有统计证据低于对照组" if significant and diff < 0 else
        "尚无足够证据证明两组主指标存在差异"
    )
    quality = pd.DataFrame([
        {"check": "input_rows", "value": input_rows, "status": "info", "detail": "输入应为每个随机化单位一行。"},
        {"check": "valid_units", "value": len(work), "status": "pass", "detail": "移除缺失分组或指标后的单位数。"},
        {"check": "srm_chi_square", "value": srm_chi2, "status": "warning" if srm_p < 0.01 else "pass", "detail": "样本比例失衡检验。"},
        {"check": "srm_p_value", "value": srm_p, "status": "warning" if srm_p < 0.01 else "pass", "detail": "p < 0.01 时优先排查分流或日志。"},
        {"check": "control_selection", "value": control, "status": "warning" if control_source == "alphabetical" else "pass", "detail": f"对照组选择方式：{control_source}。"},
    ])
    metrics = pd.DataFrame([
        {"metric": "metric_type", "value": metric_type}, {"metric": "control_group", "value": control},
        {"metric": "treatment_group", "value": treatment}, {"metric": "absolute_lift", "value": diff},
        {"metric": "relative_lift", "value": relative}, {"metric": statistic_name, "value": statistic},
        {"metric": "p_value", "value": p_value}, {"metric": "ci_95_low", "value": ci_low},
        {"metric": "ci_95_high", "value": ci_high}, {"metric": "effect_size", "value": effect_size},
        {"metric": "verdict", "value": verdict},
    ])
    value_label = "转化率" if metric_type == "binary" else "均值"
    markdown = "\n".join([
        "## 🧪 A/B 实验分析", f"- 指标类型：**{metric_type}**；对照组：`{control}`；实验组：`{treatment}`。",
        f"- {value_label}：对照组 **{mean0:.4%}**，实验组 **{mean1:.4%}**。" if metric_type == "binary" else f"- {value_label}：对照组 **{mean0:.4f}**，实验组 **{mean1:.4f}**。",
        f"- 绝对提升：**{diff:.4%}**；相对提升：**{relative:.2%}**。" if metric_type == "binary" else f"- 均值差：**{diff:.4f}**；相对提升：**{relative:.2%}**。",
        f"- p 值：**{p_value:.4g}**；95% CI：**[{ci_low:.4f}, {ci_high:.4f}]**。", f"- 结论：**{verdict}**。",
        "- ⚠️ SRM 检查异常，先排查分流/曝光日志。" if srm_p < 0.01 else "- SRM 检查未发现显著样本比例失衡。",
        "- 结果不替代护栏指标、长期指标、多重比较和最小业务效果评估。",
    ])
    return result, quality, metrics, markdown
