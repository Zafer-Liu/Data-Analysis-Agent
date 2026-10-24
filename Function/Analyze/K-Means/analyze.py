#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K-Means Clustering Analysis
============================
从零实现 K-Means 聚类（仅依赖 pandas + numpy，无需 scikit-learn）：
  - K-Means++ 初始化：概率加权选取初始质心，避免随机初始化的不稳定性
  - 多次初始化（n_init=10）取最优，保证结果稳定
  - 肘部法则（Elbow Method）：计算 K=1..K_max 的 SSE，自动检测拐点
  - 轮廓系数（Silhouette Score）：衡量簇间分离度与簇内紧凑度
  - Z-score 标准化：消除量纲差异对聚类结果的影响

输出三张结果表：
  analysis_result    — 各簇摘要统计（质心 / 样本量 / 占比）
  analysis_breakdown — 每条样本 + 簇标签（散点图着色）
  analysis_elbow     — 肘部曲线数据（k / inertia / silhouette）

参数说明：
  target_column  — 聚类使用的主要数值列（SQL 中可同时选多列，全部参与聚类）
  groupby_column — 可选标签列（用于计算各簇的主导标签，不参与聚类）
  n_deciles      — K，簇的数量（默认 3）
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple


# ── 模块元数据（供 registry 读取）─────────────────────────────────────────

ANALYSIS_ID   = "K_Means"
ANALYSIS_NAME = "K-Means 聚类分析"
ANALYSIS_DESC = (
    "对数值型特征执行 K-Means 聚类（K-Means++ 初始化，Z-score 标准化）。"
    "自动计算肘部曲线（SSE）和轮廓系数，辅助选择最优 K。"
    "通过 n_deciles 参数指定簇数 K（默认 3）；"
    "SQL 中选取所有聚类所需数值列；"
    "通过 groupby_column 可选填标签列（用于查看各簇的主导类别，不参与聚类）。"
)
REQUIRED_PARAMS = ["target_column"]
OPTIONAL_PARAMS = [
    "groupby_column (optional label column for cluster purity)",
    "n_deciles (K: number of clusters, default 3)",
]
OUTPUT_TABLES = ["analysis_result", "analysis_breakdown", "analysis_elbow"]

_N_INIT       = 10   # 主聚类随机初始化次数
_N_INIT_ELB   = 3    # 肘部曲线每个 K 的初始化次数（轻量）
_MAX_ITER     = 300  # 最大迭代次数
_MAX_ITER_ELB = 100  # 肘部曲线最大迭代次数
_ELB_SAMPLE   = 1000 # 肘部曲线最大样本量（大数据集采样）
_SIL_SAMPLE   = 500  # 轮廓系数最大样本量（O(n²) 计算，须限制）


def _select_cuda_device() -> Optional[str]:
    """Return an enabled local CUDA device, or ``None`` for the NumPy path.

    Importing PyTorch is intentionally deferred.  K-Means remains usable in
    core installations without Torch/CUDA and follows the same GPU switch and
    VRAM guard used by the Torch analysis modules.
    """
    try:
        from infrastructure.training_device import select_training_device

        choice = select_training_device()
        device = str(choice.get("device") or "")
        return device if device.startswith("cuda") else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  1. 数据预处理
# ═══════════════════════════════════════════════════════════════════════════

def _detect_feature_cols(df: pd.DataFrame, exclude: List[str]) -> List[str]:
    """返回所有可用数值型特征列（排除 exclude 中的列，且唯一值 > 1）。"""
    return [
        c for c in df.columns
        if c not in exclude
        and pd.api.types.is_numeric_dtype(df[c])
        and df[c].nunique() > 1
    ]


def _fill_numeric(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    """将指定列转为数值并用中位数填充缺失值。"""
    df = df.copy()
    for col in cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        if df[col].isna().any():
            df[col] = df[col].fillna(df[col].median())
    return df


def _normalize(X: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Z-score 标准化。返回 (X_norm, mean, std)。常数列 std 设为 1 以避免除零。"""
    mean = X.mean(axis=0)
    std  = X.std(axis=0)
    std[std < 1e-12] = 1.0
    return (X - mean) / std, mean, std


# ═══════════════════════════════════════════════════════════════════════════
#  2. 距离计算（向量化）
# ═══════════════════════════════════════════════════════════════════════════

def _dist_matrix(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """
    计算欧氏距离矩阵。A:(n,d), B:(k,d) -> (n,k)。
    利用 ||a-b||^2 = ||a||^2 + ||b||^2 - 2*a^T*b 向量化，避免显式循环。
    """
    A2 = (A ** 2).sum(axis=1, keepdims=True)   # (n, 1)
    B2 = (B ** 2).sum(axis=1)                   # (k,)
    AB = A @ B.T                                # (n, k)
    return np.sqrt(np.maximum(A2 + B2 - 2 * AB, 0.0))


# ═══════════════════════════════════════════════════════════════════════════
#  3. K-Means 算法
# ═══════════════════════════════════════════════════════════════════════════

def _kmeanspp_init(X: np.ndarray, k: int,
                   rng: np.random.RandomState) -> np.ndarray:
    """
    K-Means++ 初始化：以与已选质心距离平方为概率，依次选取 K 个初始质心。
    比随机初始化更快收敛，结果更稳定。
    """
    n = len(X)
    centers = [X[rng.randint(n)]]
    for _ in range(k - 1):
        dists = np.array([
            min(float(np.sum((x - c) ** 2)) for c in centers)
            for x in X
        ])
        total = float(dists.sum())
        # Duplicate rows can make every remaining distance zero.  A random
        # fallback keeps the requested K valid instead of producing NaN
        # probabilities; the normal empty-cluster handling then applies.
        if total <= 1e-12:
            centers.append(X[rng.randint(n)])
        else:
            probs = dists / total
            centers.append(X[rng.choice(n, p=probs)])
    return np.array(centers)


def _kmeans_once(
    X: np.ndarray, k: int, seed: int,
    max_iter: int = _MAX_ITER,
) -> Tuple[np.ndarray, np.ndarray, float]:
    """
    单次 K-Means 运行（K-Means++ 初始化）。
    返回 (labels, centroids, inertia)，惯性在标准化空间中计算。
    """
    rng       = np.random.RandomState(seed)
    centroids = _kmeanspp_init(X, k, rng)
    labels    = np.zeros(len(X), dtype=int)

    for _ in range(max_iter):
        new_labels = _dist_matrix(X, centroids).argmin(axis=1)
        if np.all(new_labels == labels):
            break
        labels = new_labels
        for j in range(k):
            mask = labels == j
            if mask.any():
                centroids[j] = X[mask].mean(axis=0)

    inertia = sum(
        float(np.sum((X[labels == j] - centroids[j]) ** 2))
        for j in range(k) if (labels == j).any()
    )
    return labels, centroids, inertia


def _kmeans_once_cuda(
    X: np.ndarray, k: int, seed: int, device_name: str,
    max_iter: int = _MAX_ITER,
) -> Tuple[np.ndarray, np.ndarray, float]:
    """Run one K-Means initialization on CUDA, returning NumPy-compatible data.

    K-Means++ seeding is kept deterministic on CPU.  The repeated assignment
    and centroid-update steps, which dominate large inputs, execute as Torch
    tensor operations on the selected local CUDA device.
    """
    import torch

    rng = np.random.RandomState(seed)
    centroids_np = _kmeanspp_init(X, k, rng).astype(np.float32, copy=False)
    device = torch.device(device_name)
    values = torch.as_tensor(X, dtype=torch.float32, device=device)
    centroids = torch.as_tensor(centroids_np, dtype=torch.float32, device=device)
    labels = torch.full((len(X),), -1, dtype=torch.long, device=device)

    for _ in range(max_iter):
        # Squared distance avoids an unnecessary square-root before argmin.
        distances = (values[:, None, :] - centroids[None, :, :]).square().sum(dim=2)
        next_labels = distances.argmin(dim=1)
        if torch.equal(next_labels, labels):
            labels = next_labels
            break
        labels = next_labels

        counts = torch.bincount(labels, minlength=k)
        sums = torch.zeros_like(centroids)
        sums.index_add_(0, labels, values)
        nonempty = counts > 0
        next_centroids = centroids.clone()
        next_centroids[nonempty] = sums[nonempty] / counts[nonempty, None]
        centroids = next_centroids

    inertia = float((values - centroids[labels]).square().sum().item())
    return (
        labels.detach().cpu().numpy().astype(int, copy=False),
        centroids.detach().cpu().numpy().astype(float, copy=False),
        inertia,
    )


def _kmeans(
    X: np.ndarray, k: int,
    n_init: int = _N_INIT,
    max_iter: int = _MAX_ITER,
    cuda_device: Optional[str] = None,
) -> Tuple[np.ndarray, np.ndarray, float]:
    """运行 K-Means n_init 次，取惯性最小的结果。"""
    best = None
    for seed in range(n_init):
        result = (
            _kmeans_once_cuda(X, k, seed, cuda_device, max_iter)
            if cuda_device else _kmeans_once(X, k, seed, max_iter)
        )
        if best is None or result[2] < best[2]:
            best = result
    return best  # (labels, centroids, inertia)


# ═══════════════════════════════════════════════════════════════════════════
#  4. 轮廓系数（Silhouette Score）
# ═══════════════════════════════════════════════════════════════════════════

def _silhouette_score(X: np.ndarray, labels: np.ndarray,
                      cap: int = _SIL_SAMPLE) -> float:
    """
    均值轮廓系数（Mean Silhouette Coefficient），范围 [-1, 1]。
      s(i) = (b - a) / max(a, b)
      a = 同簇内其他样本的平均距离（内聚度）
      b = 最近他簇所有样本的平均距离（分离度）
    计算复杂度 O(n^2)，超过 cap 时随机采样。
    """
    unique = np.unique(labels)
    if len(unique) < 2:
        return 0.0

    n = len(X)
    if n > cap:
        rng = np.random.RandomState(42)
        idx = rng.choice(n, cap, replace=False)
        X_s, L_s = X[idx], labels[idx]
    else:
        X_s, L_s = X, labels

    scores = []
    for i in range(len(X_s)):
        xi, li = X_s[i], L_s[i]

        # a: 同簇内距离（排除自身）
        same_idx = np.where(L_s == li)[0]
        same_idx = same_idx[same_idx != i]
        a = (float(np.mean(np.sqrt(np.sum((X_s[same_idx] - xi) ** 2, axis=1))))
             if len(same_idx) > 0 else 0.0)

        # b: 最近他簇的均距
        b_vals = [
            float(np.mean(np.sqrt(np.sum((X_s[L_s == lj] - xi) ** 2, axis=1))))
            for lj in unique if lj != li
        ]
        b = min(b_vals) if b_vals else 0.0

        denom = max(a, b)
        scores.append((b - a) / denom if denom > 1e-12 else 0.0)

    return float(np.mean(scores)) if scores else 0.0


# ═══════════════════════════════════════════════════════════════════════════
#  5. 肘部法则
# ═══════════════════════════════════════════════════════════════════════════

def _run_elbow(X: np.ndarray, k_max: int) -> pd.DataFrame:
    """
    对 K=1..k_max 运行轻量 K-Means，返回 DataFrame(k, inertia, silhouette)。
    大数据集采样至 _ELB_SAMPLE，保证速度。
    """
    n = len(X)
    if n > _ELB_SAMPLE:
        rng = np.random.RandomState(0)
        idx = rng.choice(n, _ELB_SAMPLE, replace=False)
        X_e = X[idx]
    else:
        X_e = X

    rows = []
    for k in range(1, k_max + 1):
        labels, _, inertia = _kmeans(X_e, k, n_init=_N_INIT_ELB,
                                     max_iter=_MAX_ITER_ELB)
        sil = _silhouette_score(X_e, labels) if k >= 2 else None
        rows.append({
            "k":          k,
            "inertia":    round(float(inertia), 4),
            "silhouette": round(float(sil), 4) if sil is not None else None,
        })
    return pd.DataFrame(rows)


def _detect_best_k(elbow_df: pd.DataFrame, fallback_k: int) -> int:
    """
    从肘部曲线中检测最优 K：
    1. 轮廓系数最大的 K。
    2. inertia 二阶差分（曲率最大处）作为参考。
    两者相差 <= 1 时返回轮廓系数推荐值；否则仍优先轮廓系数。
    """
    sil_df = elbow_df.dropna(subset=["silhouette"])
    sil_k  = (int(sil_df.loc[sil_df["silhouette"].idxmax(), "k"])
              if not sil_df.empty else fallback_k)

    inertias = elbow_df["inertia"].values
    if len(inertias) >= 3:
        d2      = np.diff(np.diff(inertias))
        elb_idx = int(np.argmax(np.abs(d2))) + 2
        elb_k   = int(elbow_df["k"].iloc[min(elb_idx, len(elbow_df) - 1)])
    else:
        elb_k = fallback_k

    # 两者一致时优先肘部；否则返回轮廓系数推荐
    return sil_k if abs(sil_k - elb_k) > 1 else elb_k


# ═══════════════════════════════════════════════════════════════════════════
#  6. 输出表构建
# ═══════════════════════════════════════════════════════════════════════════

def _build_result_df(
    df:           pd.DataFrame,
    feature_cols: List[str],
    labels:       np.ndarray,
    centroids_n:  np.ndarray,
    mean:         np.ndarray,
    std:          np.ndarray,
    label_col:    Optional[str],
) -> pd.DataFrame:
    """
    各簇摘要统计（analysis_result）。
    质心值反变换回原始量纲；label_col 有效时追加主导标签与纯度列。
    """
    n              = len(df)
    centroids_orig = centroids_n * std + mean
    rows: List[Dict] = []

    for j, c_orig in enumerate(centroids_orig):
        mask  = labels == j
        count = int(mask.sum())
        row: Dict = {
            "cluster": j,
            "count":   count,
            "pct":     round(count / n * 100, 2),
        }
        for i, col in enumerate(feature_cols):
            row[f"avg_{col}"] = round(float(c_orig[i]), 4)

        if label_col and label_col in df.columns:
            sub      = df[label_col].astype(str).values[mask]
            sub_list = sub.tolist()
            if sub_list:
                dominant = max(set(sub_list), key=sub_list.count)
                purity   = round(sub_list.count(dominant) / count * 100, 1)
            else:
                dominant, purity = "—", 0.0
            row["dominant_label"] = dominant
            row["purity_pct"]     = purity

        rows.append(row)

    return (pd.DataFrame(rows)
            .sort_values("cluster")
            .reset_index(drop=True))


def _build_breakdown_df(df: pd.DataFrame, labels: np.ndarray) -> pd.DataFrame:
    """每条样本附加 cluster 标签（analysis_breakdown），用于散点图着色。"""
    out = df.copy().reset_index(drop=True)
    out["cluster"] = labels
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  7. Markdown 报告
# ═══════════════════════════════════════════════════════════════════════════

def _build_md(
    k:            int,
    n:            int,
    feature_cols: List[str],
    result_df:    pd.DataFrame,
    elbow_df:     pd.DataFrame,
    best_k:       int,
    sil_score:    float,
    inertia:      float,
    label_col:    Optional[str],
    compute_device: str,
) -> str:
    disp_feats    = feature_cols[:5]
    feat_ellipsis = len(feature_cols) > 5
    feat_str      = ", ".join(feature_cols[:8]) + ("..." if len(feature_cols) > 8 else "")

    L = [
        f"## K-Means 聚类分析 — K={k}，特征：{feat_str}\n",
        "### 模型概况",
        "| 指标 | 值 |", "|------|-----|",
        f"| 样本数 | {n:,} |",
        f"| 特征数 | {len(feature_cols)} |",
        f"| 计算设备 | {compute_device} |",
        f"| 簇数 K | {k} |",
        f"| 总惯性（SSE，标准化空间） | {inertia:,.4f} |",
        f"| 轮廓系数 | **{sil_score:.4f}** |",
        f"| 推荐 K（肘部法则+轮廓系数） | **{best_k}** |",
        "",
    ]

    # ── 各簇摘要 ──────────────────────────────────────────────────────────
    L.append("### 各簇摘要")
    avg_hdr  = " | ".join(f"avg_{c}" for c in disp_feats)
    avg_sep  = " | ".join(["-----:"] * len(disp_feats))
    has_pur  = "dominant_label" in result_df.columns
    if feat_ellipsis:
        avg_hdr += " | ..."
        avg_sep += " | ---"

    header = f"| 簇 | 样本量 | 占比 | {avg_hdr}"
    sep    = f"|:--:|------:|-----:|{avg_sep}"
    if has_pur:
        header += " | 主标签 | 纯度% |"
        sep    += " | --- | ---: |"
    else:
        header += " |"
        sep    += " |"
    L += [header, sep]

    for _, row in result_df.iterrows():
        avg_vals = " | ".join(f"{row[f'avg_{c}']:,.3f}" for c in disp_feats)
        if feat_ellipsis:
            avg_vals += " | ..."
        line = (f"| **C{int(row['cluster'])}** "
                f"| {int(row['count']):,} "
                f"| {row['pct']:.1f}% "
                f"| {avg_vals}")
        if has_pur:
            line += f" | {row['dominant_label']} | {row['purity_pct']:.0f}% |"
        else:
            line += " |"
        L.append(line)
    L.append("")

    # ── 肘部法则表 ────────────────────────────────────────────────────────
    L += [
        "### 肘部法则 & 轮廓系数",
        "| K | 惯性（SSE） | 轮廓系数 | 备注 |",
        "|:--:|----------:|--------:|------|",
    ]
    for _, row in elbow_df.iterrows():
        sil_str = f"{row['silhouette']:.4f}" if row["silhouette"] is not None else "—"
        note = ""
        if int(row["k"]) == k and int(row["k"]) == best_k:
            note = "当前 K = 推荐 K"
        elif int(row["k"]) == k:
            note = "当前 K"
        elif int(row["k"]) == best_k:
            note = "推荐 K"
        L.append(f"| {int(row['k'])} | {row['inertia']:,.4f} | {sil_str} | {note} |")
    L.append("")

    # ── 核心洞察 ──────────────────────────────────────────────────────────
    biggest  = result_df.loc[result_df["count"].idxmax()]
    smallest = result_df.loc[result_df["count"].idxmin()]

    L += [
        "### 核心洞察",
        f"- **最大簇**：C{int(biggest['cluster'])}，占 **{biggest['pct']:.1f}%**（{int(biggest['count']):,} 条）",
        f"- **最小簇**：C{int(smallest['cluster'])}，占 {smallest['pct']:.1f}%（{int(smallest['count']):,} 条）",
    ]
    if sil_score >= 0.70:
        L.append(f"- 轮廓系数 **{sil_score:.4f}**：簇间分离优秀，当前 K={k} 是良好选择。")
    elif sil_score >= 0.50:
        L.append(f"- 轮廓系数 **{sil_score:.4f}**：簇间分离合理。")
    elif sil_score >= 0.25:
        L.append(f"- 轮廓系数 **{sil_score:.4f}**：簇间存在一定重叠，可尝试 K={best_k}。")
    else:
        L.append(
            f"- 轮廓系数 **{sil_score:.4f}**：簇间重叠较多，"
            f"建议改用 K={best_k} 或重新筛选聚类特征。"
        )
    if best_k != k:
        L.append(
            f"- 肘部/轮廓系数推荐 **K={best_k}**，"
            f"可将 n_deciles={best_k} 重新运行。"
        )

    # 各簇特征摘要（前 3 特征）
    if len(feature_cols) >= 2:
        f1, f2 = feature_cols[0], feature_cols[1]
        for _, row in result_df.iterrows():
            L.append(
                f"- C{int(row['cluster'])}：{f1}={row[f'avg_{f1}']:.2f}"
                f"，{f2}={row[f'avg_{f2}']:.2f}"
                + (f"，{feature_cols[2]}={row[f'avg_{feature_cols[2]}']:.2f}"
                   if len(feature_cols) >= 3 else "")
            )

    L += [
        "",
        "### 可视化建议",
        "> 以下三张表已写入数据源，可直接用于 generate_chart：",
        "",
        "| 图表 | 数据源 | 字段映射 |",
        "|------|--------|---------|",
        "| Bar_Chart（簇大小） | analysis_result | x=cluster, y=count |",
        (f"| Scatter_Plot（簇分布） | analysis_breakdown "
         f"| x={feature_cols[0]}, y={feature_cols[1] if len(feature_cols) > 1 else feature_cols[0]}, color=cluster |"),
        "| Line_Chart（肘部曲线） | analysis_elbow | x=k, y=inertia |",
        "| Line_Chart（轮廓系数） | analysis_elbow | x=k, y=silhouette |",
        "",
    ]

    return "\n".join(L)


# ═══════════════════════════════════════════════════════════════════════════
#  8. 主入口
# ═══════════════════════════════════════════════════════════════════════════

def run(
    df:             pd.DataFrame,
    target_column:  str,
    groupby_column: Optional[str] = None,
    n_deciles:      int = 3,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    """
    运行 K-Means 聚类分析。

    Parameters
    ----------
    df             : 原始数据（SQL 应只选取聚类所需数值列）
    target_column  : 主要数值特征列（与 SQL 中其他数值列一起参与聚类）
    groupby_column : 可选标签列（不参与聚类，仅用于簇纯度计算）
    n_deciles      : K — 簇的数量（默认 3；< 2 时重置为 3）

    Returns
    -------
    result_df    : 各簇摘要统计          → analysis_result
    breakdown_df : 每条样本 + 簇标签     → analysis_breakdown
    elbow_df     : 肘部曲线数据          → analysis_elbow
    markdown     : Markdown 分析报告
    """
    # 参数解析
    K = max(2, int(n_deciles)) if int(n_deciles) >= 2 else 3

    label_col = (
        groupby_column.strip()
        if groupby_column and groupby_column.strip() in df.columns
        else None
    )
    exclude      = [label_col] if label_col else []
    feature_cols = _detect_feature_cols(df, exclude)

    if not feature_cols:
        raise ValueError(
            "没有找到可用的数值型特征列。"
            "请在 SQL 中选取数值列（如 age, income, spending）后再运行 K-Means。"
        )

    n = len(df)
    K = min(K, n - 1, 20)
    if K < 2:
        raise ValueError(f"有效样本数 {n} 过少，无法完成聚类（至少需要 3 行）。")

    # 预处理 & 标准化
    df            = _fill_numeric(df, feature_cols)
    X_raw         = df[feature_cols].values.astype(float)
    X, mean, std  = _normalize(X_raw)

    # 主 K-Means（n_init=10，取最优）。CUDA 不可用时完整回退到 NumPy/CPU。
    cuda_device = _select_cuda_device()
    if cuda_device:
        try:
            labels, centroids_n, inertia = _kmeans(X, K, cuda_device=cuda_device)
            compute_device = cuda_device
        except (ImportError, RuntimeError):
            # Driver resets and VRAM allocation failures must not make an
            # otherwise valid analysis fail; NumPy remains the safe baseline.
            labels, centroids_n, inertia = _kmeans(X, K)
            compute_device = "CPU（CUDA 执行失败后回退 NumPy）"
    else:
        labels, centroids_n, inertia = _kmeans(X, K)
        compute_device = "CPU（NumPy 回退）"

    # 轮廓系数（在最终结果上评估）
    sil = _silhouette_score(X, labels)

    # 肘部法则（采样加速）
    k_max    = min(10, max(K + 2, 5), n - 1)
    elbow_df = _run_elbow(X, k_max)
    best_k   = _detect_best_k(elbow_df, fallback_k=K)

    # 构建输出表
    result_df    = _build_result_df(df, feature_cols, labels,
                                    centroids_n, mean, std, label_col)
    breakdown_df = _build_breakdown_df(df, labels)

    markdown = _build_md(
        k=K, n=n,
        feature_cols=feature_cols,
        result_df=result_df,
        elbow_df=elbow_df,
        best_k=best_k,
        sil_score=sil,
        inertia=inertia,
        label_col=label_col,
        compute_device=compute_device,
    )

    return result_df, breakdown_df, elbow_df, markdown
