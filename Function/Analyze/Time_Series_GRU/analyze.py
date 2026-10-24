#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Time_Series_GRU
===============
纯 numpy + pandas 从零实现 GRU（门控循环单元）时间序列预测模块。
不依赖 keras / tensorflow / torch，可在仅有 numpy 的环境中运行。

GRU 核心公式（标准定义）：
    z_t = sigmoid(W_z x_t + U_z h_{t-1} + b_z)        # 更新门
    r_t = sigmoid(W_r x_t + U_r h_{t-1} + b_r)        # 重置门
    h̃_t = tanh(W_h x_t + U_h (r_t ⊙ h_{t-1}) + b_h)  # 候选隐藏状态
    h_t = (1 - z_t) ⊙ h_{t-1} + z_t ⊙ h̃_t            # 隐藏状态

训练：BPTT（截断反向传播）+ AdaGrad 自适应学习率

输出三张结果表：
    analysis_result    — 预测 + 历史拟合（ds / y_actual / y_pred / lower_ci / upper_ci / segment）
    analysis_breakdown — 训练损失曲线（epoch / train_loss / val_loss）
    analysis_metrics   — 模型评估（metric / value）
"""

import numpy as np
import pandas as pd
from typing import Callable, Optional, Tuple, List


def _try_cuda_torch_gru(
    df: pd.DataFrame,
    target_column: str,
    groupby_column: Optional[str],
    n_deciles: int,
    progress_callback: Optional[Callable[[int, str], None]],
):
    """Use the maintained Torch GRU only when a local CUDA device is usable.

    The built-in NumPy implementation remains the deterministic CPU baseline.
    This avoids making the ``gru`` Skill depend on the optional Torch package,
    while allowing the same public analysis contract to use CUDA automatically.
    """
    try:
        from infrastructure.training_device import select_training_device

        choice = select_training_device()
        if not str(choice.get("device") or "").startswith("cuda"):
            return None
        from Function.Analyze.Torch_RNN.analyze import run as run_torch_rnn

        if progress_callback:
            progress_callback(20, f"检测到 {choice['device']}，切换到 PyTorch GRU 加速")
        return run_torch_rnn(
            df, target_column, groupby_column, n_deciles, progress_callback,
        )
    except (ImportError, RuntimeError):
        # Optional Torch/CUDA failures must retain the original CPU capability.
        if progress_callback:
            progress_callback(20, "PyTorch CUDA 不可用，回退到 NumPy CPU GRU")
        return None

# ── 模块元数据 ──────────────────────────────────────────────────────────────
ANALYSIS_ID   = "Time_Series_GRU"
ANALYSIS_NAME = "GRU 时间序列预测（深度学习）"
ANALYSIS_DESC = (
    "使用门控循环单元（GRU）神经网络预测时间序列，纯 numpy 从零实现，无需 keras/tensorflow。"
    "支持多步滚动预测与置信区间估计（蒙特卡洛 dropout 近似）。"
    "通过 groupby_column 指定时间列名；"
    "通过 n_deciles 指定预测步数（默认 12）。"
)
REQUIRED_PARAMS = ["target_column"]
OPTIONAL_PARAMS = [
    "groupby_column (时间列名，默认自动探测)",
    "n_deciles (预测步数，默认 12)",
]
OUTPUT_TABLES = ["analysis_result", "analysis_breakdown", "analysis_metrics"]

_DEFAULT_STEPS   = 12
_HIDDEN_SIZE     = 32      # GRU 隐层维度
_WINDOW          = 10      # 滑动窗口（lookback）
_EPOCHS          = 80      # 训练轮数
_LR              = 0.01    # 初始学习率
_BATCH_SIZE      = 16      # mini-batch 大小
_DROPOUT_RATE    = 0.1     # dropout 近似（预测不确定性）
_MC_SAMPLES      = 50      # 蒙特卡洛采样次数（置信区间）
_VAL_SPLIT       = 0.15    # 验证集比例


# ═══════════════════════════════════════════════════════════════════════════
#  1. 工具函数
# ═══════════════════════════════════════════════════════════════════════════

def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))

def _tanh(x: np.ndarray) -> np.ndarray:
    return np.tanh(np.clip(x, -15, 15))

def _detect_time_col(df: pd.DataFrame, hint: str = "") -> str:
    if hint and hint in df.columns:
        return hint
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            return col
    keywords = ["date", "time", "month", "year", "week", "day", "period", "ds", "日期", "时间"]
    for col in df.columns:
        if any(k in col.lower() for k in keywords):
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


def _normalize(values: np.ndarray) -> Tuple[np.ndarray, float, float]:
    mu  = values.mean()
    std = values.std() or 1.0
    return (values - mu) / std, mu, std


def _make_windows(data: np.ndarray, window: int) -> Tuple[np.ndarray, np.ndarray]:
    """滑动窗口构建 (X, y)，X shape=(n, window, 1), y shape=(n,)。"""
    X, y = [], []
    for i in range(len(data) - window):
        X.append(data[i:i + window])
        y.append(data[i + window])
    return np.array(X)[..., np.newaxis], np.array(y)


# ═══════════════════════════════════════════════════════════════════════════
#  2. GRU 单元（向量化，支持 batch）
# ═══════════════════════════════════════════════════════════════════════════

class GRULayer:
    """单层 GRU，支持 mini-batch 前向 + BPTT 反向。"""

    def __init__(self, input_size: int, hidden_size: int, seed: int = 42):
        rng = np.random.RandomState(seed)
        scale = 0.1
        H = hidden_size
        D = input_size

        # 更新门 z
        self.W_z = rng.randn(D, H) * scale
        self.U_z = rng.randn(H, H) * scale
        self.b_z = np.zeros(H)
        # 重置门 r
        self.W_r = rng.randn(D, H) * scale
        self.U_r = rng.randn(H, H) * scale
        self.b_r = np.zeros(H)
        # 候选状态 h_tilde
        self.W_h = rng.randn(D, H) * scale
        self.U_h = rng.randn(H, H) * scale
        self.b_h = np.zeros(H)

        # AdaGrad 累积梯度
        self._init_adagrad()

    def _init_adagrad(self):
        self._ag = {k: np.ones_like(v) * 1e-8
                    for k, v in self._params().items()}

    def _params(self) -> dict:
        return {
            "W_z": self.W_z, "U_z": self.U_z, "b_z": self.b_z,
            "W_r": self.W_r, "U_r": self.U_r, "b_r": self.b_r,
            "W_h": self.W_h, "U_h": self.U_h, "b_h": self.b_h,
        }

    def forward(self, X: np.ndarray, h0: np.ndarray = None, dropout: float = 0.0) -> Tuple[np.ndarray, list]:
        """
        X: (batch, seq_len, input_size)
        Returns: h_last (batch, H), cache list
        """
        batch, seq_len, _ = X.shape
        H = self.W_z.shape[1]
        h = h0 if h0 is not None else np.zeros((batch, H))
        cache = []
        rng = np.random

        for t in range(seq_len):
            x_t = X[:, t, :]               # (batch, D)
            z_t = _sigmoid(x_t @ self.W_z + h @ self.U_z + self.b_z)
            r_t = _sigmoid(x_t @ self.W_r + h @ self.U_r + self.b_r)
            h_tilde = _tanh(x_t @ self.W_h + (r_t * h) @ self.U_h + self.b_h)
            h_new   = (1 - z_t) * h + z_t * h_tilde

            # dropout（推断时设 0）
            if dropout > 0:
                mask = (rng.rand(*h_new.shape) > dropout).astype(float) / (1 - dropout)
                h_new = h_new * mask

            cache.append((x_t, h, z_t, r_t, h_tilde, h_new))
            h = h_new

        return h, cache

    def backward(self, dh_last: np.ndarray, cache: list, lr: float) -> None:
        """BPTT，更新参数（AdaGrad）。"""
        grads = {k: np.zeros_like(v) for k, v in self._params().items()}
        dh = dh_last.copy()

        for x_t, h_prev, z_t, r_t, h_tilde, h_next in reversed(cache):
            # dL/dh_new -> dL/d各门
            dz = dh * (h_tilde - h_prev)
            dh_tilde = dh * z_t
            dh_prev_from_h = dh * (1 - z_t)

            # 通过 tanh
            dh_tilde_pre = dh_tilde * (1 - h_tilde ** 2)
            grads["W_h"] += x_t.T @ dh_tilde_pre
            grads["U_h"] += (r_t * h_prev).T @ dh_tilde_pre
            grads["b_h"] += dh_tilde_pre.sum(axis=0)

            dr = (dh_tilde_pre @ self.U_h.T) * h_prev
            dh_prev_from_htilde = (dh_tilde_pre @ self.U_h.T) * r_t

            # 通过 sigmoid(z)
            dz_pre = dz * z_t * (1 - z_t)
            grads["W_z"] += x_t.T @ dz_pre
            grads["U_z"] += h_prev.T @ dz_pre
            grads["b_z"] += dz_pre.sum(axis=0)
            dh_prev_from_z = dz_pre @ self.U_z.T

            # 通过 sigmoid(r)
            dr_pre = dr * r_t * (1 - r_t)
            grads["W_r"] += x_t.T @ dr_pre
            grads["U_r"] += h_prev.T @ dr_pre
            grads["b_r"] += dr_pre.sum(axis=0)
            dh_prev_from_r = dr_pre @ self.U_r.T

            dh = dh_prev_from_h + dh_prev_from_htilde + dh_prev_from_z + dh_prev_from_r

        # AdaGrad 更新
        eps = 1e-8
        for k, g in grads.items():
            g_clipped = np.clip(g, -5, 5)
            self._ag[k] += g_clipped ** 2
            setattr(self, k, getattr(self, k) - lr * g_clipped / (np.sqrt(self._ag[k]) + eps))


class GRUModel:
    """GRU + 线性输出层。"""

    def __init__(self, hidden_size: int, seed: int = 42):
        rng = np.random.RandomState(seed + 1)
        self.gru = GRULayer(input_size=1, hidden_size=hidden_size, seed=seed)
        self.W_out = rng.randn(hidden_size, 1) * 0.1
        self.b_out = np.zeros(1)
        self._ag_Wo = np.ones_like(self.W_out) * 1e-8
        self._ag_bo = np.ones_like(self.b_out) * 1e-8

    def predict_one(self, X: np.ndarray, dropout: float = 0.0) -> np.ndarray:
        """X: (batch, seq_len, 1)，返回 (batch,)"""
        h, _ = self.gru.forward(X, dropout=dropout)
        return (h @ self.W_out + self.b_out).squeeze(-1)

    def train_step(self, X: np.ndarray, y: np.ndarray, lr: float) -> float:
        """单 batch 前向 + 反向，返回 MSE loss。"""
        h, cache = self.gru.forward(X)
        y_pred = (h @ self.W_out + self.b_out).squeeze(-1)
        loss   = float(np.mean((y_pred - y) ** 2))

        # 输出层梯度
        dy     = 2 * (y_pred - y) / len(y)
        dh     = dy[:, np.newaxis] @ self.W_out.T   # (batch, H)

        dW_out = h.T @ dy[:, np.newaxis]
        db_out = np.array([dy.sum()])

        # AdaGrad 输出层
        eps = 1e-8
        self._ag_Wo += np.clip(dW_out, -5, 5) ** 2
        self._ag_bo += np.clip(db_out, -5, 5) ** 2
        self.W_out -= lr * np.clip(dW_out, -5, 5) / (np.sqrt(self._ag_Wo) + eps)
        self.b_out -= lr * np.clip(db_out, -5, 5) / (np.sqrt(self._ag_bo) + eps)

        # GRU 反向
        self.gru.backward(dh, cache, lr)
        return loss


# ═══════════════════════════════════════════════════════════════════════════
#  3. 训练
# ═══════════════════════════════════════════════════════════════════════════

def _train(
    X_train: np.ndarray, y_train: np.ndarray,
    X_val:   np.ndarray, y_val:   np.ndarray,
    hidden_size: int, epochs: int, lr: float, batch_size: int,
    seed: int = 42,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Tuple[GRUModel, list, list]:
    model = GRUModel(hidden_size=hidden_size, seed=seed)
    rng   = np.random.RandomState(seed)
    n     = len(X_train)
    train_losses, val_losses = [], []

    for epoch in range(epochs):
        if progress_callback is not None and (
            epoch == 0 or epoch == epochs - 1 or epoch % max(1, epochs // 10) == 0
        ):
            progress_callback(epoch, f"GRU 训练轮次 {epoch + 1}/{epochs}")
        # shuffle
        idx = rng.permutation(n)
        X_sh, y_sh = X_train[idx], y_train[idx]
        epoch_loss = []
        for start in range(0, n, batch_size):
            xb = X_sh[start:start + batch_size]
            yb = y_sh[start:start + batch_size]
            if len(xb) == 0:
                continue
            loss = model.train_step(xb, yb, lr)
            epoch_loss.append(loss)
        train_losses.append(float(np.mean(epoch_loss)))

        if len(X_val) > 0:
            y_val_pred = model.predict_one(X_val, dropout=0.0)
            val_loss   = float(np.mean((y_val_pred - y_val) ** 2))
        else:
            val_loss = float("nan")
        val_losses.append(val_loss)

    return model, train_losses, val_losses


# ═══════════════════════════════════════════════════════════════════════════
#  4. 多步滚动预测 + MC Dropout 置信区间
# ═══════════════════════════════════════════════════════════════════════════

def _rolling_forecast(
    model: GRUModel,
    last_window: np.ndarray,    # shape (window,) 归一化后
    steps: int,
    mc_samples: int,
    dropout_rate: float,
    mu: float, std: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Returns (y_pred, lower_ci, upper_ci) 全部为原始尺度。
    """
    all_preds = []
    for _ in range(mc_samples):
        window = last_window.copy()
        preds  = []
        for _ in range(steps):
            x_in  = window[-len(last_window):][np.newaxis, :, np.newaxis]  # (1, W, 1)
            y_hat = model.predict_one(x_in, dropout=dropout_rate)[0]
            preds.append(y_hat)
            window = np.append(window, y_hat)[1:]
        all_preds.append(preds)

    all_preds = np.array(all_preds)   # (mc_samples, steps)
    y_pred  = all_preds.mean(axis=0) * std + mu
    lower   = np.percentile(all_preds, 2.5,  axis=0) * std + mu
    upper   = np.percentile(all_preds, 97.5, axis=0) * std + mu
    return y_pred, lower, upper


# ═══════════════════════════════════════════════════════════════════════════
#  5. 结果表构建
# ═══════════════════════════════════════════════════════════════════════════

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
    series: pd.Series,
    fitted_norm: np.ndarray,
    mu: float, std: float,
    window: int,
    y_pred: np.ndarray,
    lower:  np.ndarray,
    upper:  np.ndarray,
    steps:  int,
) -> pd.DataFrame:
    fitted = fitted_norm * std + mu
    # 历史（window 个点之后才有拟合值）
    hist_ds      = series.index[window:].astype(str)
    hist_actual  = series.values[window:]
    n_fit = min(len(hist_ds), len(fitted))

    hist = pd.DataFrame({
        "ds":       hist_ds[:n_fit],
        "y_actual": hist_actual[:n_fit].round(4),
        "y_pred":   fitted[:n_fit].round(4),
        "lower_ci": np.nan,
        "upper_ci": np.nan,
        "segment":  "historical",
    })

    future_idx = _build_future_index(series, steps)
    fcast = pd.DataFrame({
        "ds":       future_idx.astype(str) if hasattr(future_idx, "astype") else [str(i) for i in future_idx],
        "y_actual": np.nan,
        "y_pred":   y_pred.round(4),
        "lower_ci": lower.round(4),
        "upper_ci": upper.round(4),
        "segment":  "forecast",
    })
    return pd.concat([hist, fcast], ignore_index=True)


def _build_loss_df(train_losses, val_losses) -> pd.DataFrame:
    rows = []
    for i, (tl, vl) in enumerate(zip(train_losses, val_losses)):
        rows.append({"epoch": i + 1, "train_loss": round(tl, 6),
                     "val_loss": round(vl, 6) if not np.isnan(vl) else None})
    return pd.DataFrame(rows)


def _build_metrics_df(
    series: pd.Series,
    fitted_norm: np.ndarray,
    mu: float, std: float,
    window: int,
    train_losses: list,
    val_losses:   list,
    hidden_size: int,
    epochs: int,
) -> pd.DataFrame:
    fitted   = fitted_norm * std + mu
    n_fit    = min(len(series) - window, len(fitted))
    y_true   = series.values[window:window + n_fit]
    y_pred_h = fitted[:n_fit]
    mae  = float(np.mean(np.abs(y_true - y_pred_h)))
    rmse = float(np.sqrt(np.mean((y_true - y_pred_h) ** 2)))
    nz   = y_true != 0
    mape = float(np.mean(np.abs((y_true[nz] - y_pred_h[nz]) / y_true[nz])) * 100) if nz.any() else float("nan")
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
    ss_res = float(np.sum((y_true - y_pred_h) ** 2))
    r2   = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    rows = [
        {"metric": "模型类型",         "value": f"GRU（隐层 {hidden_size} 维）"},
        {"metric": "窗口大小（lookback）", "value": window},
        {"metric": "训练轮数（epochs）",  "value": epochs},
        {"metric": "训练样本数",          "value": len(series)},
        {"metric": "最终训练 MSE Loss",   "value": round(train_losses[-1], 6)},
        {"metric": "最终验证 MSE Loss",   "value": round(val_losses[-1], 6) if not np.isnan(val_losses[-1]) else "N/A"},
        {"metric": "R²（训练集）",        "value": round(r2,   4)},
        {"metric": "MAE（训练集）",       "value": round(mae,  4)},
        {"metric": "RMSE（训练集）",      "value": round(rmse, 4)},
        {"metric": "MAPE %（训练集）",    "value": round(mape, 2) if not np.isnan(mape) else "N/A"},
    ]
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════════
#  6. Markdown 报告
# ═══════════════════════════════════════════════════════════════════════════

def _build_md(target_col, time_col, steps, series, metrics_df, result_df, train_losses) -> str:
    forecast_rows = result_df[result_df["segment"] == "forecast"].head(10)

    L = [
        f"## GRU 时间序列预测 — `{target_col}`\n",
        "### 模型概况",
        "| 指标 | 值 |", "|------|-----|",
        "| 模型类型 | GRU（门控循环单元，纯 numpy 实现） |",
        f"| 时间列 | `{time_col or '（行序号）'}` |",
        f"| 训练样本数 | {len(series)} |",
        f"| 预测步数 | {steps} |",
        f"| 置信区间 | 95%（{_MC_SAMPLES} 次 MC Dropout 采样） |",
        "",
        "### 模型评估",
        "| 指标 | 值 |", "|------|-----|",
    ]
    for _, row in metrics_df.iterrows():
        L.append(f"| {row['metric']} | {row['value']} |")
    L.append("")

    L += ["### 预测摘要（未来数据点）",
          "| 时间点 | 预测值 | 95% 置信区间 |",
          "|--------|-------:|-------------|"]
    for _, row in forecast_rows.iterrows():
        lo = f"{row['lower_ci']:.4f}" if not pd.isna(row["lower_ci"]) else "—"
        hi = f"{row['upper_ci']:.4f}" if not pd.isna(row["upper_ci"]) else "—"
        L.append(f"| {row['ds']} | {row['y_pred']:.4f} | [{lo}, {hi}] |")
    L.append("")

    last_actual = float(series.iloc[-1])
    last_pred   = float(result_df[result_df["segment"] == "forecast"].iloc[-1]["y_pred"])
    direction   = "上升" if last_pred > last_actual else "下降"
    chg_pct     = abs(last_pred - last_actual) / abs(last_actual) * 100 if last_actual != 0 else 0

    loss_drop = (train_losses[0] - train_losses[-1]) / (train_losses[0] + 1e-9) * 100

    L += [
        "### 核心洞察",
        f"- **预测趋势**：预测期末值（{last_pred:.4f}）"
        f"较历史末值（{last_actual:.4f}）{direction}，幅度约 **{chg_pct:.1f}%**。",
        f"- **训练收敛**：Loss 从 {train_losses[0]:.6f} 降至 {train_losses[-1]:.6f}"
        f"（下降 {loss_drop:.1f}%）。",
        f"- **不确定性**：置信区间由 {_MC_SAMPLES} 次 MC Dropout 近似，"
        f"宽区间表示该时间点预测不确定性较高。",
        "",
        "> **图表建议**",
        "> - Line_Chart(analysis_result)：x=ds，y=y_pred + y_actual，按 segment 着色",
        "> - Line_Chart(analysis_breakdown)：x=epoch，y=train_loss + val_loss — 训练曲线",
        "> - analysis_metrics 表直接展示 R²/MAE/RMSE",
    ]
    return "\n".join(L)


# ═══════════════════════════════════════════════════════════════════════════
#  7. 主入口
# ═══════════════════════════════════════════════════════════════════════════

def run(
    df:             pd.DataFrame,
    target_column:  str,
    groupby_column: Optional[str] = None,
    n_deciles:      int = 0,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    """
    Parameters
    ----------
    df             : 原始 DataFrame
    target_column  : 目标数值列
    groupby_column : 时间列名（默认自动探测）
    n_deciles      : 预测步数（默认 12）
    """
    progress = progress_callback or (lambda _pct, _message="": None)
    progress(5, "正在校验 GRU 输入")
    if target_column not in df.columns:
        raise ValueError(f"目标列 '{target_column}' 不存在。可用列：{', '.join(df.columns[:20])}")
    if not pd.api.types.is_numeric_dtype(df[target_column]):
        raise ValueError(f"目标列 '{target_column}' 不是数值类型。")

    steps    = int(n_deciles) if int(n_deciles) > 0 else _DEFAULT_STEPS
    time_col = _detect_time_col(df, groupby_column or "")
    series   = _prepare_series(df, time_col, target_column)

    if len(series) < _WINDOW + 4:
        raise ValueError(
            f"有效数据点不足（{len(series)} 个），GRU 至少需要 {_WINDOW + 4} 个数据点。"
        )

    accelerated_result = _try_cuda_torch_gru(
        df, target_column, groupby_column, n_deciles, progress_callback,
    )
    if accelerated_result is not None:
        return accelerated_result

    # ── 归一化 ────────────────────────────────────────────────────────────
    progress(18, "正在构建 GRU 训练样本")
    values_norm, mu, std = _normalize(series.values)

    # ── 构建窗口样本 ───────────────────────────────────────────────────────
    X_all, y_all = _make_windows(values_norm, _WINDOW)

    # 训练 / 验证分割
    n_val   = max(1, int(len(X_all) * _VAL_SPLIT))
    n_train = len(X_all) - n_val
    X_train, y_train = X_all[:n_train], y_all[:n_train]
    X_val,   y_val   = X_all[n_train:], y_all[n_train:]

    # ── 训练 ──────────────────────────────────────────────────────────────
    def _training_progress(epoch: int, message: str):
        pct = 30 + int(45 * epoch / max(1, _EPOCHS - 1))
        progress(pct, message)

    model, train_losses, val_losses = _train(
        X_train, y_train, X_val, y_val,
        hidden_size = _HIDDEN_SIZE,
        epochs      = _EPOCHS,
        lr          = _LR,
        batch_size  = _BATCH_SIZE,
        progress_callback = _training_progress,
    )

    # ── 历史拟合值 ────────────────────────────────────────────────────────
    fitted_norm = model.predict_one(X_all, dropout=0.0)   # (n_samples,)

    # ── 多步滚动预测 + MC Dropout 置信区间 ────────────────────────────────
    last_window = values_norm[-_WINDOW:]
    progress(80, "正在生成 GRU 滚动预测")
    y_pred, lower, upper = _rolling_forecast(
        model, last_window, steps, _MC_SAMPLES, _DROPOUT_RATE, mu, std
    )

    # ── 结果表 ────────────────────────────────────────────────────────────
    progress(90, "正在整理 GRU 结果")
    result_df   = _build_result_df(series, fitted_norm, mu, std, _WINDOW,
                                    y_pred, lower, upper, steps)
    loss_df     = _build_loss_df(train_losses, val_losses)
    metrics_df  = _build_metrics_df(series, fitted_norm, mu, std, _WINDOW,
                                     train_losses, val_losses, _HIDDEN_SIZE, _EPOCHS)

    markdown = _build_md(
        target_col   = target_column,
        time_col     = time_col,
        steps        = steps,
        series       = series,
        metrics_df   = metrics_df,
        result_df    = result_df,
        train_losses = train_losses,
    )

    progress(98, "GRU 分析计算完成")
    return result_df, loss_df, metrics_df, markdown
