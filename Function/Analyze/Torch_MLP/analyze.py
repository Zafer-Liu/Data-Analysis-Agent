#!/usr/bin/env python3
"""Optional versioned PyTorch MLP classification/regression with bounded batches."""
from __future__ import annotations

from infrastructure.training_router import select_training_target, submit_approved_analysis

ANALYSIS_ID = "Torch_MLP"
ANALYSIS_NAME = "深度学习建模（PyTorch MLP）"
ANALYSIS_DESC = "分批训练 MLP 分类/回归；本机 CUDA 或 CPU，远程执行须先批准快照计划。"
REQUIRED_PARAMS = ["target_column"]
OPTIONAL_PARAMS = ["groupby_column (mlp_cls / mlp_reg)", "n_deciles (epochs, default 60)"]
OUTPUT_TABLES = ["analysis_result", "analysis_breakdown", "analysis_metrics"]


def _require_torch():
    try:
        import torch
        from torch import nn
        return torch, nn
    except ImportError as exc:
        raise ImportError("PyTorch 未安装，请先执行：pip install -r requirements/dl.txt") from exc


def run(df, target_column, groupby_column=None, n_deciles=0, progress_callback=None, **kwargs):
    mode = (groupby_column or "mlp_cls").strip().lower()
    mode = "regression" if mode in {"mlp_reg", "reg", "regression"} else mode
    params = {"target_column": target_column, "mode": mode, "epochs": int(n_deciles or 60),
              **(kwargs.get("training_params") or {})}
    target = select_training_target(execution_target=kwargs.get("execution_target", "auto"),
                                    approved_plan_id=kwargs.get("approved_plan_id", ""), grant_id=kwargs.get("grant_id", ""))
    if target["kind"] == "remote":
        return submit_approved_analysis("Torch_MLP", params, service=kwargs.get("training_service"),
                                       connection_id=target["connection_id"],
                                       **{k: kwargs[k] for k in ("approved_plan_id", "grant_id", "idempotency_key", "owner_id",
                                                               "session_id", "workspace_id", "job_runner") if k in kwargs})
    _require_torch()
    from Function.Analyze.Torch_MLP.kernel import train_mlp
    result = train_mlp(df, params, device=target["device"], budget=kwargs.get("budget"),
                       progress=progress_callback, cancel=kwargs.get("cancel"),
                       output_root=kwargs.get("output_root"), scratch=kwargs.get("scratch"))
    return result.legacy()
