#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Data_Decile_Analysis
====================
把数值型指标按大小分成 N 个等频桶（十分位 / 百分位等），计算每个桶的
count / sum / mean / median / min / max / pct_of_total / cumulative_pct。

常用场景：
  - 客户价值分层（RFM decile）
  - 销售额 80/20 Pareto 验证
  - ESG 评分分布分析
  - 任何数值指标的分布剖析
"""
import pandas as pd
import numpy as np
from typing import Optional, Tuple


# ── 主入口 ─────────────────────────────────────────────────────────────────

def run(
    df: pd.DataFrame,
    target_column: str,
    groupby_column: Optional[str] = None,
    n_deciles: int = 10,
) -> Tuple[pd.DataFrame, pd.DataFrame, str]:
    """
    对 df[target_column] 执行十分位分析。

    Parameters
    ----------
    df             : 原始数据 DataFrame
    target_column  : 要分析的数值列
    groupby_column : 可选，额外的分组维度（会追加到分析结果）
    n_deciles      : 分桶数，默认 10（十分位）

    Returns
    -------
    result_df      : 每个桶的聚合统计（可直接 to_sql 写入分析表）
    breakdown_df   : 若指定 groupby_column，则返回各桶 × 各分类的交叉表；否则空 DataFrame
    markdown       : Markdown 格式的洞察文本，供 LLM 直接引用
    """
    if target_column not in df.columns:
        avail = ", ".join(df.columns[:20])
        raise ValueError(
            f"列 '{target_column}' 不存在。可用列：{avail}"
        )

    # ── 1. 数据清洗 ────────────────────────────────────────────────────────
    work = df.copy()
    work[target_column] = pd.to_numeric(work[target_column], errors="coerce")
    n_before = len(work)
    work = work.dropna(subset=[target_column])
    n_dropped = n_before - len(work)
    n_valid = len(work)

    if n_valid < n_deciles:
        raise ValueError(
            f"有效数据行数（{n_valid}）少于分桶数（{n_deciles}），无法完成分析。"
        )

    # ── 2. 分桶 ───────────────────────────────────────────────────────────
    labels = [str(i) for i in range(1, n_deciles + 1)]
    work["_decile"] = pd.qcut(
        work[target_column], q=n_deciles, labels=labels, duplicates="drop"
    )
    # 补全可能因 duplicates='drop' 导致的缺失桶号
    actual_n = work["_decile"].nunique()

    total_sum = work[target_column].sum()
    total_cnt = len(work)

    # ── 3. 按桶聚合 ────────────────────────────────────────────────────────
    agg = (
        work.groupby("_decile", observed=True)[target_column]
        .agg(
            count="count",
            sum="sum",
            mean="mean",
            median="median",
            std="std",
            min="min",
            max="max",
        )
        .reset_index()
    )
    agg.columns = ["decile", "count", "sum", "mean", "median", "std", "min", "max"]
    agg["decile"] = agg["decile"].astype(int)
    agg = agg.sort_values("decile").reset_index(drop=True)

    agg["pct_of_total"] = (agg["sum"] / total_sum * 100).round(2)
    agg["cumulative_pct"] = agg["pct_of_total"].cumsum().round(2)

    # 数值列保留 2 位小数
    for col in ["sum", "mean", "median", "std", "min", "max"]:
        agg[col] = agg[col].round(2)

    # ── 4. 可选：groupby 交叉分布 ──────────────────────────────────────────
    breakdown_df = pd.DataFrame()
    if groupby_column and groupby_column in df.columns:
        work[groupby_column] = work[groupby_column].astype(str)
        breakdown_df = (
            work.groupby(["_decile", groupby_column], observed=True)[target_column]
            .agg(count="count", sum="sum")
            .reset_index()
        )
        breakdown_df.columns = ["decile", groupby_column, "count", "sum"]
        breakdown_df["decile"] = breakdown_df["decile"].astype(int)
        breakdown_df["sum"] = breakdown_df["sum"].round(2)
        breakdown_df = breakdown_df.sort_values(["decile", groupby_column]).reset_index(drop=True)

    # ── 5. Markdown 洞察 ───────────────────────────────────────────────────
    markdown = _build_markdown(
        agg=agg,
        breakdown_df=breakdown_df,
        target_column=target_column,
        groupby_column=groupby_column,
        total_sum=total_sum,
        total_cnt=total_cnt,
        n_dropped=n_dropped,
        n_deciles=actual_n,
    )

    return agg, breakdown_df, markdown


# ── 内部工具函数 ───────────────────────────────────────────────────────────

def _build_markdown(
    agg: pd.DataFrame,
    breakdown_df: pd.DataFrame,
    target_column: str,
    groupby_column: Optional[str],
    total_sum: float,
    total_cnt: int,
    n_dropped: int,
    n_deciles: int,
) -> str:
    lines = []

    # 标题
    lines.append(f"## 📊 十分位分析（Decile Analysis）— `{target_column}`\n")

    # 数据概况
    lines.append("### 数据概况")
    lines.append(f"| 项目 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 有效数据行 | {total_cnt:,} |")
    lines.append(f"| 总计（{target_column}） | {total_sum:,.2f} |")
    lines.append(f"| 均值 | {agg['mean'].mean():,.2f} |")
    lines.append(f"| 分桶数 | {n_deciles} |")
    if n_dropped > 0:
        lines.append(f"| 跳过空值 | {n_dropped:,} 行 |")
    lines.append("")

    # 各桶汇总
    lines.append("### 分桶统计")
    lines.append("| 分位 | 样本量 | 总计 | 均值 | 中位数 | 最小 | 最大 | 占比% | 累计% |")
    lines.append("|:----:|-------:|-----:|-----:|-------:|-----:|-----:|------:|------:|")
    for _, row in agg.iterrows():
        lines.append(
            f"| **D{int(row['decile'])}** "
            f"| {int(row['count']):,} "
            f"| {row['sum']:,.2f} "
            f"| {row['mean']:,.2f} "
            f"| {row['median']:,.2f} "
            f"| {row['min']:,.2f} "
            f"| {row['max']:,.2f} "
            f"| {row['pct_of_total']:.2f}% "
            f"| {row['cumulative_pct']:.2f}% |"
        )
    lines.append("")

    # Pareto 洞察
    n = len(agg)
    top_n = max(1, round(n * 0.2))   # 顶部 20% 分位
    top_rows = agg.tail(top_n)
    top_pct = top_rows["pct_of_total"].sum()
    top_cnt_pct = top_rows["count"].sum() / total_cnt * 100

    bottom_n = max(1, round(n * 0.2))
    bottom_rows = agg.head(bottom_n)
    bottom_pct = bottom_rows["pct_of_total"].sum()

    lines.append("### 💡 核心洞察")
    lines.append(
        f"- **Pareto 效应**：排名最高的 {top_cnt_pct:.0f}%（D{n-top_n+1}–D{n}）"
        f"贡献了总 {target_column} 的 **{top_pct:.1f}%**"
    )
    lines.append(
        f"- **底部分位**：排名最低的 {bottom_n} 个分位（D1–D{bottom_n}）"
        f"仅贡献 **{bottom_pct:.1f}%**"
    )

    # 标准差最大的桶（最分散）
    max_std_row = agg.loc[agg["std"].idxmax()]
    lines.append(
        f"- **最分散分位**：D{int(max_std_row['decile'])}（标准差 = {max_std_row['std']:,.2f}），"
        f"内部数据差异最大"
    )
    lines.append("")

    # groupby 维度提示
    if groupby_column and not breakdown_df.empty:
        lines.append(f"### 分组维度（`{groupby_column}`）")
        lines.append(
            f"交叉分析结果已存储于 `decile_breakdown` 表，"
            f"可用于生成堆叠图等可视化。"
        )
        lines.append("")

    return "\n".join(lines)


# ── 描述信息（供 registry / agent 读取）──────────────────────────────────

ANALYSIS_ID   = "Data_Decile_Analysis"
ANALYSIS_NAME = "十分位分析（Decile Analysis）"
ANALYSIS_DESC = (
    "将数值指标按大小等频分桶，计算每桶的 count/sum/mean/median/min/max/"
    "占比/累计占比，验证 Pareto 效应，适用于客户价值分层、销售分布剖析、ESG 评分分布等场景。"
)
REQUIRED_PARAMS = ["target_column"]
OPTIONAL_PARAMS = ["groupby_column", "n_deciles"]
OUTPUT_TABLES   = ["decile_result", "decile_breakdown"]
