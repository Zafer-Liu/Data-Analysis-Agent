"""Winsorize numeric columns by capping at specified quantiles."""
import numpy as np
import pandas as pd
from typing import List, Optional, Tuple


def winsorize(
    df: pd.DataFrame,
    lower_pct: float,
    upper_pct: float,
    columns: Optional[List[str]] = None,
) -> Tuple[pd.DataFrame, str]:
    """
    Cap values at the *lower_pct*-th and *upper_pct*-th percentiles.

    Parameters
    ----------
    lower_pct / upper_pct : percentile values in 0–100 range
    columns               : columns to process; None = all numeric columns

    Returns
    -------
    (cleaned_df, markdown_summary)
    """
    if not (0 <= lower_pct < upper_pct <= 100):
        return df.copy(), f"❌ 分位数范围无效：lower={lower_pct}, upper={upper_pct}（须 0 ≤ lower < upper ≤ 100）"

    numeric_all = df.select_dtypes(include=[np.number]).columns.tolist()
    target = [c for c in columns if c in numeric_all] if columns else numeric_all

    if not target:
        return df.copy(), "⚠️ 未找到可处理的数值列。"

    cleaned = df.copy()
    rows = ["| 列名 | 下限值 | 上限值 | 下截行数 | 上截行数 |",
            "|------|--------|--------|----------|----------|"]

    for col in target:
        lo = float(df[col].quantile(lower_pct / 100))
        hi = float(df[col].quantile(upper_pct / 100))
        n_lo = int((df[col] < lo).sum())
        n_hi = int((df[col] > hi).sum())
        cleaned[col] = cleaned[col].clip(lower=lo, upper=hi)
        rows.append(f"| {col} | {lo:.4g} | {hi:.4g} | {n_lo} | {n_hi} |")

    summary = (
        f"## 缩尾处理完成（分位数 {lower_pct}% ~ {upper_pct}%）\n\n"
        + "\n".join(rows)
    )
    return cleaned, summary
