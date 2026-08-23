#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sklearn_Model Analysis
=======================
通用机器学习建模模块（可选依赖 scikit-learn，M1 阶段）。

支持三种任务（自动/显式判定）：
  - 分类   — RandomForestClassifier / LogisticRegression
  - 回归   — RandomForestRegressor / LinearRegression / Ridge
  - 聚类   — KMeans（无监督，无目标列）

依赖策略（红线：核心包零 sklearn）：
  - 顶层仅导入 numpy/pandas；sklearn 在 run() 内延迟导入
  - 未安装时抛明确错误，提示 `pip install -r requirements-ml.txt`
  - registry 加载不受影响（模块可加载，调用时才要求 sklearn）

输出三张结果表：
  analysis_result    — 模型与主要指标（metric / value）
  analysis_breakdown — 按任务而异：特征重要性 或 聚类样本分布 / 中心
  analysis_metrics   — 详细评估（分类混淆矩阵长格式 / 回归逐样本残差 / 聚类中心表）
"""

import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional, Tuple


# ── 模块元数据（供 registry 读取）─────────────────────────────────────────

ANALYSIS_ID   = "Sklearn_Model"
ANALYSIS_NAME = "机器学习建模（Scikit-learn）"
ANALYSIS_DESC = (
    "使用 scikit-learn 进行通用机器学习建模：分类（随机森林/逻辑回归）、"
    "回归（随机森林/线性/岭回归）、聚类（KMeans），自动预处理缺失值与类别特征，"
    "输出评估指标、特征重要性或聚类结果及 Markdown 报告。"
    "通过 groupby_column 参数传入模型类型（rf / lr / lm / ridge / kmeans，默认 rf）；"
    "通过 n_deciles 参数传入聚类数 k 或随机种子（默认 42）。"
    "需要安装可选依赖：pip install -r requirements-ml.txt"
)
REQUIRED_PARAMS = ["target_column"]
OPTIONAL_PARAMS = [
    "groupby_column (model: rf / lr / lm / ridge / kmeans, default rf)",
    "n_deciles (kmeans 聚类数 或 随机种子, default 42)",
]
OUTPUT_TABLES = ["analysis_result", "analysis_breakdown", "analysis_metrics"]

_MODEL_ALIASES = {
    "rf": "random_forest", "random_forest": "random_forest", "forest": "random_forest",
    "lr": "logistic", "logistic": "logistic", "logreg": "logistic",
    "lm": "linear", "linear": "linear", "ols": "linear",
    "ridge": "ridge",
    "kmeans": "kmeans", "k-means": "kmeans", "k_means": "kmeans", "cluster": "kmeans",
}
_DEFAULT_SEED = 42
_N_ESTIMATORS = 100


# ═══════════════════════════════════════════════════════════════════════════
#  1. 预处理
# ═══════════════════════════════════════════════════════════════════════════

def _fill_missing(df: pd.DataFrame, target_col: Optional[str]) -> pd.DataFrame:
    df = df.copy()
    for col in df.columns:
        if col == target_col:
            continue
        if df[col].isna().any():
            if pd.api.types.is_numeric_dtype(df[col]):
                df[col] = df[col].fillna(df[col].median())
            else:
                mode = df[col].mode()
                df[col] = df[col].fillna(mode.iloc[0] if not mode.empty else "unknown")
    return df


def _encode_X(X: pd.DataFrame, seed: int = _DEFAULT_SEED) -> Tuple[np.ndarray, List[str]]:
    """数值列原样（Z-score 可选，保持可解释），类别列 LabelEncoder → 数值。"""
    from sklearn.preprocessing import LabelEncoder

    parts: List[np.ndarray] = []
    names: List[str] = []
    for col in X.columns:
        if pd.api.types.is_numeric_dtype(X[col]):
            parts.append(X[col].astype(float).to_numpy().reshape(-1, 1))
            names.append(col)
        else:
            le = LabelEncoder()
            parts.append(le.fit_transform(X[col].astype(str)).reshape(-1, 1))
            names.append(f"{col}(encoded)")
    if not parts:
        raise ValueError("预处理后无有效特征列，请检查输入数据。")
    return np.hstack(parts).astype(np.float64), names


def _encode_y(y: pd.Series) -> Tuple[np.ndarray, Optional[List[str]]]:
    from sklearn.preprocessing import LabelEncoder

    le = LabelEncoder()
    y_arr = le.fit_transform(y.astype(str))
    return y_arr, list(le.classes_)


# ═══════════════════════════════════════════════════════════════════════════
#  2. 建模与评估
# ═══════════════════════════════════════════════════════════════════════════

def _pick_model(task: str, model: str, seed: int):
    from sklearn.ensemble import (
        RandomForestClassifier, RandomForestRegressor,
    )
    from sklearn.linear_model import (
        LogisticRegression, LinearRegression, Ridge,
    )

    if task == "classification":
        if model == "logistic":
            return LogisticRegression(max_iter=2000, random_state=seed)
        return RandomForestClassifier(n_estimators=_N_ESTIMATORS, random_state=seed)
    if task == "regression":
        if model == "linear":
            return LinearRegression()
        if model == "ridge":
            return Ridge(random_state=seed)
        return RandomForestRegressor(n_estimators=_N_ESTIMATORS, random_state=seed)
    # clustering
    from sklearn.cluster import KMeans
    return KMeans(n_clusters=max(2, int(seed)), n_init=10, random_state=42)


def _task_from_model(model: str) -> str:
    if model == "kmeans":
        return "clustering"
    if model in ("linear", "ridge"):
        return "regression"
    return "classification"


# ── 分类评估 ───────────────────────────────────────────────────────────────

def _classification_metrics(y_true, y_pred, classes: List[str]) -> pd.DataFrame:
    from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

    rows = [
        {"metric": "accuracy", "value": round(float(accuracy_score(y_true, y_pred)), 4)},
    ]
    try:
        rows.append({"metric": "precision_macro", "value": round(float(precision_score(y_true, y_pred, average="macro")), 4)})
        rows.append({"metric": "recall_macro", "value": round(float(recall_score(y_true, y_pred, average="macro")), 4)})
        rows.append({"metric": "f1_macro", "value": round(float(f1_score(y_true, y_pred, average="macro")), 4)})
    except Exception:
        pass
    return pd.DataFrame(rows)


def _confusion_long(y_true, y_pred, classes: List[str]) -> pd.DataFrame:
    from sklearn.metrics import confusion_matrix

    cm = confusion_matrix(y_true, y_pred)
    rows = []
    for i, actual in enumerate(classes):
        for j, predicted in enumerate(classes):
            rows.append({
                "actual": actual, "predicted": predicted,
                "count": int(cm[i][j]) if i < cm.shape[0] and j < cm.shape[1] else 0,
            })
    return pd.DataFrame(rows)


def _feature_importance(model, names: List[str]) -> pd.DataFrame:
    imp = getattr(model, "feature_importances_", None)
    if imp is None:
        coef = getattr(model, "coef_", None)
        if coef is None:
            return pd.DataFrame(columns=["rank", "feature", "importance_pct"])
        coef = np.asarray(coef)
        if coef.ndim == 1:          # 单输出模型（LinearRegression 等）coef_ 为 1D
            imp = np.abs(coef)
        else:
            imp = np.abs(coef).mean(axis=0)
    total = float(imp.sum()) or 1.0
    rows = sorted(
        [
            {"feature": fn, "importance_pct": round(float(v / total * 100), 2)}
            for fn, v in zip(names, imp)
        ],
        key=lambda x: x["importance_pct"], reverse=True,
    )
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return pd.DataFrame(rows)[["rank", "feature", "importance_pct"]]


# ── 回归评估 ───────────────────────────────────────────────────────────────

def _regression_metrics(y_true, y_pred) -> pd.DataFrame:
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

    return pd.DataFrame([
        {"metric": "r2", "value": round(float(r2_score(y_true, y_pred)), 4)},
        {"metric": "mae", "value": round(float(mean_absolute_error(y_true, y_pred)), 4)},
        {"metric": "rmse", "value": round(float(np.sqrt(mean_squared_error(y_true, y_pred))), 4)},
    ])


def _residual_long(y_true, y_pred) -> pd.DataFrame:
    return pd.DataFrame({
        "actual": y_true,
        "predicted": y_pred,
        "residual": (np.asarray(y_pred) - np.asarray(y_true)).round(4),
    }).head(200)


# ── 聚类评估 ───────────────────────────────────────────────────────────────

def _cluster_tables(X: np.ndarray, labels, model, names: List[str]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    centers = np.asarray(getattr(model, "cluster_centers_", None))
    center_df = pd.DataFrame(columns=["cluster", *names])
    if centers is not None and centers.size:
        center_df = pd.DataFrame(centers, columns=names)
        center_df.insert(0, "cluster", [f"cluster_{i}" for i in range(center_df.shape[0])])
    dist = pd.Series(labels).value_counts().sort_index().reset_index()
    dist.columns = ["cluster", "count"]
    dist["cluster"] = dist["cluster"].astype(str)
    dist["pct"] = (dist["count"] / len(labels) * 100).round(2)
    return center_df, dist


# ═══════════════════════════════════════════════════════════════════════════
#  3. Markdown 报告
# ═══════════════════════════════════════════════════════════════════════════

def _build_md(
    task: str, model_label: str, target_col: str,
    n_train: int, n_test: int, n_features: int,
    metrics_df: pd.DataFrame, extra: Dict[str, Any],
) -> str:
    task_cn = {"classification": "分类", "regression": "回归", "clustering": "聚类"}[task]
    L = [
        f"## 机器学习建模 - `{target_col or '(无监督聚类)'}`\n",
        "### 模型概况",
        "| 指标 | 值 |", "|------|-----|",
        f"| 任务类型 | {task_cn} |",
        f"| 模型 | {model_label} |",
        f"| 训练样本 | {n_train} |",
        f"| 测试样本 | {n_test} |",
        f"| 特征数量 | {n_features} |",
        "",
        "### 评估指标",
        "| 指标 | 值 |", "|------|-----|",
    ]
    for _, row in metrics_df.iterrows():
        L.append(f"| {row['metric']} | **{row['value']}** |")
    L.append("")
    if task in ("classification", "regression") and extra.get("importance") is not None:
        imp = extra["importance"]
        if not imp.empty:
            L += ["### 特征重要性", "| 排名 | 特征 | 重要性占比 |", "|:----:|------|----------:|"]
            for _, row in imp.iterrows():
                bar = "|" * max(1, int(row["importance_pct"] / 5))
                L.append(f"| {int(row['rank'])} | `{row['feature']}` | {row['importance_pct']:.1f}% {bar} |")
            L.append("")
    if task == "clustering" and extra.get("dist") is not None:
        dist = extra["dist"]
        L += ["### 聚类样本分布", "| 簇 | 样本数 | 占比 |", "|---:|------:|-----:|"]
        for _, row in dist.iterrows():
            L.append(f"| {row['cluster']} | {row['count']} | {row['pct']}% |")
        L.append("")
    if task == "regression":
        L.append("> 逐样本预测与残差见 `analysis_metrics` 表（actual / predicted / residual）。")
    L.append("")
    return "\n".join(L)


# ═══════════════════════════════════════════════════════════════════════════
#  4. 主入口
# ═══════════════════════════════════════════════════════════════════════════

def run(
    df: pd.DataFrame,
    target_column: str,
    groupby_column: Optional[str] = None,
    n_deciles: int = 0,
    **kwargs: Any,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    """运行 scikit-learn 建模。

    Parameters
    ----------
    df             : 原始数据 DataFrame
    target_column  : 目标列；聚类（kmeans）时可为空字符串/None
    groupby_column : 模型类型（rf / lr / lm / ridge / kmeans）
    n_deciles      : 聚类数 k（kmeans）或随机种子（其余任务，默认 42）

    Returns
    -------
    result_df  : 模型与主要指标        → analysis_result
    breakdown  : 特征重要性 / 聚类分布 → analysis_breakdown
    metrics_df : 详细评估             → analysis_metrics
    markdown   : Markdown 分析报告
    """
    try:
        import sklearn  # noqa: F401  延迟导入：核心包不携带 sklearn
    except ImportError:
        raise ImportError(
            "scikit-learn 未安装。机器学习建模需要可选依赖，请先执行："
            "pip install -r requirements-ml.txt"
        )

    from sklearn.model_selection import train_test_split

    model_name = (groupby_column or "rf").strip().lower()
    model = _MODEL_ALIASES.get(model_name, "rf")
    seed = int(n_deciles) if int(n_deciles) > 0 else _DEFAULT_SEED
    task = _task_from_model(model)

    df = _fill_missing(df, target_column if task != "clustering" else None)

    if task == "clustering":
        X = df if target_column not in df.columns else df.drop(columns=[target_column])
        if X.shape[1] < 1:
            raise ValueError("聚类需要至少一个特征列。")
        X_arr, names = _encode_X(X)
        clf = _pick_model("clustering", model, seed)
        labels = clf.fit_predict(X_arr)
        center_df, dist = _cluster_tables(X_arr, labels, clf, names)
        n_clusters = dist.shape[0]
        result_df = pd.DataFrame([
            {"metric": "n_clusters", "value": n_clusters},
            {"metric": "n_samples", "value": len(labels)},
            {"metric": "n_features", "value": len(names)},
        ])
        markdown = _build_md(
            task, "KMeans", target_column or "(无监督聚类)",
            len(df), 0, len(names), result_df, {"dist": dist},
        )
        return result_df, dist, center_df, markdown

    if target_column not in df.columns:
        raise ValueError(
            f"目标列 '{target_column}' 不存在。可用列：{', '.join(df.columns[:20])}"
        )

    y_series = df[target_column]
    X = df.drop(columns=[target_column])
    if X.shape[1] < 1:
        raise ValueError("至少需要一个特征列。")

    X_arr, names = _encode_X(X)
    y_arr, classes = _encode_y(y_series)

    train_test = task == "classification" or task == "regression"
    if train_test and len(df) >= 6:
        X_train, X_test, y_train, y_test = train_test_split(
            X_arr, y_arr, test_size=0.3, random_state=_DEFAULT_SEED,
        )
    else:
        X_train = X_test = X_arr
        y_train = y_test = y_arr

    clf = _pick_model(task, model, seed)
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)

    if task == "classification":
        metrics_df = _classification_metrics(y_test, y_pred, classes or [])
        cm_long = _confusion_long(y_test, y_pred, classes or [])
        imp = _feature_importance(clf, names)
        result_df = metrics_df.copy()
        result_df.loc[len(result_df)] = {"metric": "n_train", "value": len(X_train)}
        result_df.loc[len(result_df)] = {"metric": "n_test", "value": len(X_test)}
        markdown = _build_md(
            task, clf.__class__.__name__, target_column,
            len(X_train), len(X_test), len(names), metrics_df, {"importance": imp},
        )
        return result_df, imp, cm_long, markdown

    # regression
    metrics_df = _regression_metrics(y_test, y_pred)
    resid = _residual_long(y_test, y_pred)
    imp = _feature_importance(clf, names)
    result_df = metrics_df.copy()
    result_df.loc[len(result_df)] = {"metric": "n_train", "value": len(X_train)}
    result_df.loc[len(result_df)] = {"metric": "n_test", "value": len(X_test)}
    markdown = _build_md(
        task, clf.__class__.__name__, target_column,
        len(X_train), len(X_test), len(names), metrics_df, {"importance": imp},
    )
    return result_df, imp, resid, markdown
