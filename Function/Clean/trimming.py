"""Trim rows by keeping only values within [min_val, max_val] on a target column."""
import pandas as pd
from typing import Tuple


def trim(
    df: pd.DataFrame,
    column: str,
    min_val: float,
    max_val: float,
) -> Tuple[pd.DataFrame, str]:
    """
    Remove rows where *column* is outside [min_val, max_val].

    Returns
    -------
    (cleaned_df, markdown_summary)
    """
    if column not in df.columns:
        return df.copy(), f"❌ 列 '{column}' 不存在。"
    if min_val >= max_val:
        return df.copy(), f"❌ 参数无效：min_val ({min_val}) 须小于 max_val ({max_val})。"

    before = len(df)
    cleaned = df[(df[column] >= min_val) & (df[column] <= max_val)].copy().reset_index(drop=True)
    removed = before - len(cleaned)

    summary = (
        f"## 截尾处理完成\n"
        f"- 列：**{column}**\n"
        f"- 保留范围：**[{min_val}, {max_val}]**\n"
        f"- 原始行数：{before:,}\n"
        f"- 移除行数：**{removed:,}**（{removed / before * 100:.1f}%）\n"
        f"- 保留行数：**{len(cleaned):,}**\n"
    )
    return cleaned, summary
