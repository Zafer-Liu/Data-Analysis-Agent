"""Fill missing values in a DataFrame."""
import numpy as np
import pandas as pd
from typing import List, Optional, Tuple


def fill_missing(
    df: pd.DataFrame,
    method: str,
    columns: Optional[List[str]] = None,
) -> Tuple[pd.DataFrame, str]:
    """
    Fill NaN values in numeric columns.

    Parameters
    ----------
    method  : 'zero' | 'mean' | 'median'
    columns : list of column names to process; None = all numeric columns

    Returns
    -------
    (cleaned_df, markdown_summary)
    """
    numeric_all = df.select_dtypes(include=[np.number]).columns.tolist()
    if columns:
        target = [c for c in columns if c in numeric_all]
    else:
        target = numeric_all

    if not target:
        return df.copy(), "⚠️ 未找到可处理的数值列。"

    before = df[target].isnull().sum()
    cleaned = df.copy()

    if method == "zero":
        cleaned[target] = cleaned[target].fillna(0)
        method_cn = "填 0"
    elif method == "mean":
        for col in target:
            cleaned[col] = cleaned[col].fillna(cleaned[col].mean())
        method_cn = "填均值"
    elif method == "median":
        for col in target:
            cleaned[col] = cleaned[col].fillna(cleaned[col].median())
        method_cn = "填中位数"
    else:
        return df.copy(), f"❌ 未知方法 '{method}'，支持：zero / mean / median"

    after = cleaned[target].isnull().sum()
    total_filled = int((before - after).sum())

    rows = ["| 列名 | 处理前缺失 | 处理后缺失 | 填充数 |",
            "|------|-----------|-----------|--------|"]
    for col in target:
        diff = int(before[col] - after[col])
        if diff > 0:
            rows.append(f"| {col} | {int(before[col])} | {int(after[col])} | {diff} |")

    summary = (
        f"## 缺失值处理完成（{method_cn}）\n"
        f"- 处理列数：{len(target)}\n"
        f"- 共填充 {total_filled} 个单元格\n\n"
        + "\n".join(rows)
    )
    return cleaned, summary
