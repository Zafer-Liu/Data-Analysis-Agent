#!/usr/bin/env python3
"""Optional versioned GRU/LSTM forecasting; routing precedes local Torch import."""
from __future__ import annotations

import pandas as pd

from infrastructure.training_router import select_training_target, submit_approved_analysis

ANALYSIS_ID = "Torch_RNN"
ANALYSIS_NAME = "深度学习时序建模（PyTorch GRU/LSTM）"
ANALYSIS_DESC = "分批训练 GRU/LSTM，按序列排序与惰性窗口；远程执行须先批准快照计划。"
REQUIRED_PARAMS = ["target_column"]
OPTIONAL_PARAMS = ["groupby_column (时间列或 lstm)", "n_deciles (预测步数, default 12)"]
OUTPUT_TABLES = ["analysis_result", "analysis_breakdown", "analysis_metrics"]
_TIME_KEYWORDS = ["date", "time", "month", "year", "week", "day", "period", "ds", "日期", "时间"]


def _require_torch():
    try:
        import torch
        from torch import nn
        return torch, nn
    except ImportError as exc:
        raise ImportError("PyTorch 未安装，请先执行：pip install -r requirements/dl.txt") from exc


def _detect_time_col(df, hint=""):
    if hint and hint in df.columns:
        return hint
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            return col
    for col in df.columns:
        if any(k in col.lower() for k in _TIME_KEYWORDS):
            try:
                pd.to_datetime(df[col], errors="raise")
                return col
            except (ValueError, TypeError):
                pass
    return ""


def run(df, target_column, groupby_column=None, n_deciles=0, progress_callback=None, **kwargs):
    hint = (groupby_column or "").strip()
    # Snapshot/batch callers specify time_column in training_params. Detection is
    # a compatibility convenience for already-small local DataFrames only.
    time_col = _detect_time_col(df, hint) if isinstance(df, pd.DataFrame) else (hint if hint.lower() != "lstm" else "")
    params = {"target_column": target_column, "cell": "lstm" if hint.lower() == "lstm" and (not hasattr(df, "columns") or hint not in df.columns) else "gru",
              "time_column": time_col, "steps": int(n_deciles or 12), **(kwargs.get("training_params") or {})}
    target = select_training_target(execution_target=kwargs.get("execution_target", "auto"),
                                    approved_plan_id=kwargs.get("approved_plan_id", ""), grant_id=kwargs.get("grant_id", ""))
    if target["kind"] == "remote":
        return submit_approved_analysis("Torch_RNN", params, service=kwargs.get("training_service"),
                                       connection_id=target["connection_id"],
                                       **{k: kwargs[k] for k in ("approved_plan_id", "grant_id", "idempotency_key", "owner_id",
                                                               "session_id", "workspace_id", "job_runner") if k in kwargs})
    _require_torch()
    if isinstance(df, pd.DataFrame) and target_column in df and not pd.api.types.is_numeric_dtype(df[target_column]):
        raise ValueError("Torch_RNN 目标列必须是数值类型")
    from Function.Analyze.Torch_RNN.kernel import train_rnn
    result = train_rnn(df, params, device=target["device"], budget=kwargs.get("budget"),
                       progress=progress_callback, cancel=kwargs.get("cancel"),
                       output_root=kwargs.get("output_root"), scratch=kwargs.get("scratch"))
    return result.legacy()
