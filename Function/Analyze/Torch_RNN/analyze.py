#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional PyTorch GRU/LSTM time-series forecasting (M2).

Same result-table contract as Time_Series_GRU (analysis_result / analysis_breakdown
/ analysis_metrics) so downstream charts work unchanged, but the model itself is
torch-powered: hidden cell selectable between GRU (default) and LSTM, training
device routed by infrastructure.training_router (local CUDA -> CPU in-process).
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

import numpy as np
import pandas as pd

from infrastructure.training_router import select_training_target


ANALYSIS_ID = "Torch_RNN"
ANALYSIS_NAME = "深度学习时序建模（PyTorch GRU/LSTM）"
ANALYSIS_DESC = (
    "使用可选 PyTorch 训练 GRU 或 LSTM 时间序列预测模型，自动选择可用 CUDA 或 CPU，"
    "支持多步滚动预测与 MC Dropout 置信区间估计。"
    "groupby_column 为时间列名（默认自动探测）；值不是列名且为 lstm 时使用 LSTM 单元（默认 GRU）；"
    "n_deciles 为预测步数（默认 12）。"
    "需要安装：pip install -r requirements-dl.txt"
)
REQUIRED_PARAMS = ["target_column"]
OPTIONAL_PARAMS = [
    "groupby_column (时间列名，默认自动探测；传 lstm 切换 LSTM 单元)",
    "n_deciles (预测步数, default 12)",
]
OUTPUT_TABLES = ["analysis_result", "analysis_breakdown", "analysis_metrics"]

_DEFAULT_STEPS = 12
_HIDDEN_SIZE = 32       # RNN 隐层维度
_WINDOW = 10            # 滑动窗口（lookback）
_EPOCHS = 120           # 训练轮数
_LR = 1e-3              # Adam 学习率
_BATCH_SIZE = 16        # mini-batch 大小
_DROPOUT_RATE = 0.1     # MC Dropout（预测不确定性）
_MC_SAMPLES = 30        # 蒙特卡洛采样次数（置信区间）
_VAL_SPLIT = 0.15       # 验证集比例
_SEED = 42
_MIN_POINTS = _WINDOW + 4

_TIME_KEYWORDS = ["date", "time", "month", "year", "week", "day", "period", "ds", "日期", "时间"]


def _require_torch():
    try:
        import torch
        from torch import nn
    except ImportError as exc:
        raise ImportError("PyTorch 未安装，请先执行：pip install -r requirements-dl.txt") from exc
    return torch, nn


# ── 数据准备（与 Time_Series_GRU 保持一致的语义） ───────────────────────────

def _detect_time_col(df: pd.DataFrame, hint: str = "") -> str:
    if hint and hint in df.columns:
        return hint
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            return col
    for col in df.columns:
        if any(k in col.lower() for k in _TIME_KEYWORDS):
            try:
                pd.to_datetime(df[col])
                return col
            except Exception:
                pass
    return ""


def _prepare_series(df: pd.DataFrame, time_col: str, value_col: str) -> pd.Series:
    work = df[[time_col, value_col]].copy() if time_col else df[[value_col]].copy()
    if time_col:
        work[time_col] = pd.to_datetime(work[time_col])
        work = work.sort_values(time_col).drop_duplicates(time_col)
        work = work.set_index(time_col)
    return work[value_col].astype(float).dropna()


def _make_windows(data: np.ndarray, window: int) -> Tuple[np.ndarray, np.ndarray]:
    """滑动窗口构建 (X, y)，X shape=(n, window, 1)，y shape=(n,)。"""
    X, y = [], []
    for i in range(len(data) - window):
        X.append(data[i:i + window])
        y.append(data[i + window])
    return np.array(X)[..., np.newaxis], np.array(y)


# ── torch 模型 ──────────────────────────────────────────────────────────────

def _build_model(nn, cell: str, hidden: int, dropout: float):
    rnn_cls = nn.GRU if cell == "gru" else nn.LSTM

    class _SeqModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.rnn = rnn_cls(input_size=1, hidden_size=hidden, batch_first=True)
            self.dropout = nn.Dropout(dropout)
            self.head = nn.Linear(hidden, 1)

        def forward(self, x):
            out, _ = self.rnn(x)                     # (batch, seq, hidden)，GRU/LSTM 同签名
            return self.head(self.dropout(out[:, -1, :])).squeeze(-1)

    return _SeqModel()


def _rolling_forecast(
    model, torch, last_window: np.ndarray, steps: int, mu: float, std: float, device,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """多步滚动预测 + MC Dropout 置信区间，返回原始尺度 (y_pred, lower, upper)。"""
    model.train()                                    # 仅 Dropout 层生效，用于 MC 采样
    all_preds = []
    with torch.no_grad():
        for _ in range(_MC_SAMPLES):
            window = last_window.copy()
            preds = []
            for _ in range(steps):
                x_in = torch.tensor(window[np.newaxis, :, np.newaxis], dtype=torch.float32, device=device)
                y_hat = float(model(x_in).detach().cpu().numpy()[0])
                preds.append(y_hat)
                window = np.append(window, y_hat)[1:]
            all_preds.append(preds)
    model.eval()
    all_preds = np.array(all_preds)                  # (mc_samples, steps)
    return (
        all_preds.mean(axis=0) * std + mu,
        np.percentile(all_preds, 2.5, axis=0) * std + mu,
        np.percentile(all_preds, 97.5, axis=0) * std + mu,
    )


# ── 结果表构建 ──────────────────────────────────────────────────────────────

def _build_future_index(series: pd.Series, steps: int):
    if hasattr(series.index, "freq") and series.index.freq is not None:
        try:
            return pd.date_range(series.index[-1], periods=steps + 1, freq=series.index.freq)[1:]
        except Exception:
            pass
    if hasattr(series.index, "dtype") and pd.api.types.is_datetime64_any_dtype(series.index):
        try:
            delta = series.index[-1] - series.index[-2]
            return pd.date_range(series.index[-1] + delta, periods=steps, freq=delta)
        except Exception:
            pass
    return pd.RangeIndex(start=len(series), stop=len(series) + steps)


def _build_result_df(
    series: pd.Series, fitted_norm: np.ndarray, mu: float, std: float, window: int,
    y_pred: np.ndarray, lower: np.ndarray, upper: np.ndarray, steps: int,
) -> pd.DataFrame:
    fitted = fitted_norm * std + mu
    hist_ds, hist_actual = series.index[window:].astype(str), series.values[window:]
    n_fit = min(len(hist_ds), len(fitted))
    hist = pd.DataFrame({
        "ds": hist_ds[:n_fit], "y_actual": hist_actual[:n_fit].round(4), "y_pred": fitted[:n_fit].round(4),
        "lower_ci": np.nan, "upper_ci": np.nan, "segment": "historical",
    })
    future_idx = _build_future_index(series, steps)
    fcast = pd.DataFrame({
        "ds": future_idx.astype(str) if hasattr(future_idx, "astype") else [str(i) for i in future_idx],
        "y_actual": np.nan, "y_pred": y_pred.round(4), "lower_ci": lower.round(4),
        "upper_ci": upper.round(4), "segment": "forecast",
    })
    return pd.concat([hist, fcast], ignore_index=True)


def _build_metrics_df(
    series: pd.Series, fitted_norm: np.ndarray, mu: float, std: float, window: int,
    train_losses: list, val_losses: list, cell: str, device_str: str, torch_version: str,
) -> pd.DataFrame:
    fitted = fitted_norm * std + mu
    n_fit = min(len(series) - window, len(fitted))
    y_true = series.values[window:window + n_fit]
    y_pred_h = fitted[:n_fit]
    mae = float(np.mean(np.abs(y_true - y_pred_h)))
    rmse = float(np.sqrt(np.mean((y_true - y_pred_h) ** 2)))
    nz = y_true != 0
    mape = float(np.mean(np.abs((y_true[nz] - y_pred_h[nz]) / y_true[nz])) * 100) if nz.any() else float("nan")
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
    r2 = 1.0 - float(np.sum((y_true - y_pred_h) ** 2)) / ss_tot if ss_tot > 0 else 0.0
    rows = [
        {"metric": "模型类型", "value": f"{cell.upper()}（隐层 {_HIDDEN_SIZE} 维，PyTorch {torch_version}）"},
        {"metric": "训练设备", "value": device_str},
        {"metric": "窗口大小（lookback）", "value": window},
        {"metric": "训练轮数（epochs）", "value": _EPOCHS},
        {"metric": "训练样本数", "value": len(series)},
        {"metric": "最终训练 MSE Loss", "value": round(train_losses[-1], 6)},
        {"metric": "最终验证 MSE Loss", "value": round(val_losses[-1], 6) if not np.isnan(val_losses[-1]) else "N/A"},
        {"metric": "R²（训练集）", "value": round(r2, 4)},
        {"metric": "MAE（训练集）", "value": round(mae, 4)},
        {"metric": "RMSE（训练集）", "value": round(rmse, 4)},
        {"metric": "MAPE %（训练集）", "value": round(mape, 2) if not np.isnan(mape) else "N/A"},
    ]
    return pd.DataFrame(rows)


def _build_md(
    target_col: str, time_col: str, steps: int, series: pd.Series,
    metrics_df: pd.DataFrame, result_df: pd.DataFrame, train_losses: list,
    device_reason: str,
) -> str:
    forecast_rows = result_df[result_df["segment"] == "forecast"].head(10)
    cell_label = str(metrics_df.iloc[0]["value"]).split("（")[0]
    L = [
        f"## {cell_label} 时间序列预测（PyTorch） — `{target_col}`\n",
        "### 模型概况",
        "| 指标 | 值 |", "|------|-----|",
        f"| 时间列 | `{time_col or '（行序号）'}` |",
        f"| 训练样本数 | {len(series)} |",
        f"| 预测步数 | {steps} |",
        f"| 置信区间 | 95%（{_MC_SAMPLES} 次 MC Dropout 采样） |",
        f"| 设备选择 | {device_reason} |",
        "",
        "### 模型评估",
        "| 指标 | 值 |", "|------|-----|",
    ]
    for _, row in metrics_df.iterrows():
        L.append(f"| {row['metric']} | {row['value']} |")
    L += ["", "### 预测摘要（未来数据点）",
          "| 时间点 | 预测值 | 95% 置信区间 |", "|--------|-------:|-------------|"]
    for _, row in forecast_rows.iterrows():
        lo = f"{row['lower_ci']:.4f}" if not pd.isna(row["lower_ci"]) else "—"
        hi = f"{row['upper_ci']:.4f}" if not pd.isna(row["upper_ci"]) else "—"
        L.append(f"| {row['ds']} | {row['y_pred']:.4f} | [{lo}, {hi}] |")
    L.append("")
    last_actual = float(series.iloc[-1])
    last_pred = float(result_df[result_df["segment"] == "forecast"].iloc[-1]["y_pred"])
    direction = "上升" if last_pred > last_actual else "下降"
    chg_pct = abs(last_pred - last_actual) / abs(last_actual) * 100 if last_actual != 0 else 0
    loss_drop = (train_losses[0] - train_losses[-1]) / (train_losses[0] + 1e-9) * 100
    L += [
        "### 核心洞察",
        f"- **预测趋势**：预测期末值（{last_pred:.4f}）较历史末值（{last_actual:.4f}）{direction}，幅度约 **{chg_pct:.1f}%**。",
        f"- **训练收敛**：Loss 从 {train_losses[0]:.6f} 降至 {train_losses[-1]:.6f}（下降 {loss_drop:.1f}%）。",
        f"- **不确定性**：置信区间由 {_MC_SAMPLES} 次 MC Dropout 近似，宽区间表示该时间点预测不确定性较高。",
        "",
        "> **图表建议**",
        "> - Line_Chart(analysis_result)：x=ds，y=y_pred + y_actual，按 segment 着色",
        "> - Line_Chart(analysis_breakdown)：x=epoch，y=train_loss + val_loss — 训练曲线",
        "> - analysis_metrics 表直接展示 R²/MAE/RMSE 与训练设备",
    ]
    return "\n".join(L)


# ── 主入口 ──────────────────────────────────────────────────────────────────

def run(
    df: pd.DataFrame, target_column: str, groupby_column: Optional[str] = None,
    n_deciles: int = 0, progress_callback: Optional[Callable[[int, str], None]] = None,
    **kwargs: Any,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    """PyTorch GRU/LSTM 时间序列预测入口。

    groupby_column：时间列名（自动探测）；值不是列名且为 "lstm" 时切换 LSTM 单元。
    n_deciles：预测步数（默认 12）。
    """
    torch, nn = _require_torch()
    progress = progress_callback or (lambda _pct, _message="": None)
    progress(5, "正在校验 Torch_RNN 输入")
    if target_column not in df.columns:
        raise ValueError(f"目标列 '{target_column}' 不存在。可用列：{', '.join(df.columns[:20])}")
    if not pd.api.types.is_numeric_dtype(df[target_column]):
        raise ValueError(f"目标列 '{target_column}' 不是数值类型。")

    hint = (groupby_column or "").strip()
    cell = "lstm" if (hint.lower() == "lstm" and hint not in df.columns) else "gru"
    steps = int(n_deciles) if int(n_deciles) > 0 else _DEFAULT_STEPS
    time_col = _detect_time_col(df, hint)
    series = _prepare_series(df, time_col, target_column)
    if len(series) < _MIN_POINTS:
        raise ValueError(f"有效数据点不足（{len(series)} 个），Torch_RNN 至少需要 {_MIN_POINTS} 个数据点。")

    progress(15, "正在构建滑动窗口样本")
    values = series.to_numpy(dtype=np.float64)
    mu, std = values.mean(), (values.std() or 1.0)
    norm = (values - mu) / std
    X_all, y_all = _make_windows(norm, _WINDOW)

    n_val = max(1, int(len(X_all) * _VAL_SPLIT))
    n_train = len(X_all) - n_val
    X_train, y_train = X_all[:n_train], y_all[:n_train]
    X_val, y_val = X_all[n_train:], y_all[n_train:]

    target = select_training_target()
    # 远程 runner 属 M3 受控通道；进程内分析只能在本地张量上执行。
    device_choice = target if target["kind"] != "remote" else {"device": "cpu", "reason": target["reason"]}
    device = torch.device(device_choice["device"])
    torch.manual_seed(_SEED)

    model = _build_model(nn, cell, _HIDDEN_SIZE, _DROPOUT_RATE).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=_LR)
    loss_fn = nn.MSELoss()
    x_train = torch.tensor(X_train, dtype=torch.float32, device=device)
    y_t = torch.tensor(y_train, dtype=torch.float32, device=device)
    has_val = len(X_val) > 0
    x_val = torch.tensor(X_val, dtype=torch.float32, device=device) if has_val else None
    y_v = torch.tensor(y_val, dtype=torch.float32, device=device) if has_val else None

    rng = np.random.RandomState(_SEED)
    n = len(x_train)
    train_losses, val_losses, loss_rows = [], [], []
    progress(20, f"正在训练 {cell.upper()} 模型（{device}）")
    for epoch in range(1, _EPOCHS + 1):
        if epoch == 1 or epoch == _EPOCHS or epoch % max(1, _EPOCHS // 20) == 0:
            progress(20 + int(epoch / _EPOCHS * 60), f"{cell.upper()} 训练中：{epoch}/{_EPOCHS} epoch")
        model.train()
        idx = rng.permutation(n)
        epoch_loss = []
        for start in range(0, n, _BATCH_SIZE):
            batch = idx[start:start + _BATCH_SIZE]
            optimizer.zero_grad()
            loss = loss_fn(model(x_train[batch]), y_t[batch])
            loss.backward()
            optimizer.step()
            epoch_loss.append(float(loss.detach().cpu()))
        train_losses.append(float(np.mean(epoch_loss)))
        val_loss = float("nan")
        if has_val:
            model.eval()
            with torch.no_grad():
                val_loss = float(loss_fn(model(x_val), y_v).detach().cpu())
        val_losses.append(val_loss)
        loss_rows.append({
            "epoch": epoch, "train_loss": round(train_losses[-1], 6),
            "val_loss": round(val_loss, 6) if not np.isnan(val_loss) else None,
        })

    progress(85, "正在生成多步滚动预测")
    model.eval()
    with torch.no_grad():
        fitted_norm = model(torch.tensor(X_all, dtype=torch.float32, device=device)).detach().cpu().numpy()
    y_pred, lower, upper = _rolling_forecast(model, torch, norm[-_WINDOW:], steps, mu, std, device)

    progress(92, "正在整理结果")
    result_df = _build_result_df(series, fitted_norm, mu, std, _WINDOW, y_pred, lower, upper, steps)
    loss_df = pd.DataFrame(loss_rows)
    metrics_df = _build_metrics_df(
        series, fitted_norm, mu, std, _WINDOW, train_losses, val_losses,
        cell, str(device), torch.__version__,
    )
    markdown = _build_md(
        target_col=target_column, time_col=time_col, steps=steps, series=series,
        metrics_df=metrics_df, result_df=result_df, train_losses=train_losses,
        device_reason=device_choice["reason"],
    )
    progress(98, "Torch_RNN 分析计算完成")
    return result_df, loss_df, metrics_df, markdown
