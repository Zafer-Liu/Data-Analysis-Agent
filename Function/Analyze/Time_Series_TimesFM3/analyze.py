"""TimesFM-3 analysis over an optional HTTP service, without local ML dependencies."""
from __future__ import annotations

import hashlib
import json
import time
import uuid

import numpy as np
import pandas as pd

ANALYSIS_ID = "Time_Series_TimesFM3"
ANALYSIS_NAME = "TimesFM-3 时序预测（本地 API / Docker）"
ANALYSIS_DESC = "通过已配置的本地预测服务进行单/多商品预测；默认不下载模型。analysis_options 指定 time_column、series_column、horizon。"
REQUIRED_PARAMS = ["target_column", "analysis_options.time_column"]
OPTIONAL_PARAMS = ["analysis_options.series_column", "analysis_options.target_columns", "analysis_options.horizon",
                   "analysis_options.frequency", "analysis_options.backtest_steps", "analysis_options.make_positive"]
OUTPUT_TABLES = ["analysis_result", "analysis_breakdown", "analysis_run_info"]
ACCEPTS_ANALYSIS_OPTIONS = True


def _integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} 必须为 {low}–{high} 的整数 / must be an integer within range")
    return value


def _prepare(df, target_column, groupby_column, options):
    time_col = options.get("time_column") or groupby_column
    if not time_col:
        time_col = next((c for c in ("date", "ds", "dt", "日期") if c in df.columns), None)
    if not time_col or time_col not in df.columns:
        raise ValueError("请通过 analysis_options.time_column 指定日期列 / Specify the date column")
    frame = df.copy(deep=True)
    frame[time_col] = pd.to_datetime(frame[time_col], errors="raise")
    if frame[time_col].isna().any():
        raise ValueError("日期不能缺失 / Dates cannot be missing")
    wide = options.get("target_columns")
    series_col = options.get("series_column") or ("series_id" if "series_id" in frame.columns and not wide else None)
    if wide is not None:
        if not isinstance(wide, list) or not 1 <= len(wide) <= 8 or any(not isinstance(c, str) for c in wide) or len(set(wide)) != len(wide):
            raise ValueError("target_columns 需要 1–8 个不重复列名 / Provide 1–8 unique target columns")
        if series_col:
            raise ValueError("宽表目标列和长表分组列不能同时使用 / Choose wide targets or a long-table series column")
        if time_col in wide or any(c not in frame.columns for c in wide):
            raise ValueError("目标列不存在或包含日期列 / Invalid target columns")
        groups = [(name, frame[[time_col, name]].rename(columns={name: "__value"})) for name in wide]
    else:
        if target_column not in frame.columns:
            raise ValueError("目标列不存在 / Target column does not exist")
        if series_col:
            if series_col not in frame.columns or frame[series_col].isna().any():
                raise ValueError("分组列不存在或包含空值 / Invalid series column")
            frame[series_col] = frame[series_col].map(str)
            groups = [(str(name), group[[time_col, target_column]].rename(columns={target_column: "__value"}))
                      for name, group in frame.groupby(series_col, sort=True)]
        else:
            groups = [(target_column, frame[[time_col, target_column]].rename(columns={target_column: "__value"}))]
    if not 1 <= len(groups) <= 8:
        raise ValueError("每次联合预测需要 1–8 个目标，请按门店/品类分组 / Group requests into 1–8 targets")
    contexts, dates, names = [], None, []
    for name, group in groups:
        group = group.sort_values(time_col)
        if group[time_col].duplicated().any():
            raise ValueError(f"{name} 存在重复日期，请先明确聚合方式 / Duplicate dates require explicit aggregation")
        index = pd.DatetimeIndex(group[time_col])
        if not 2 <= len(index) <= 2048:
            raise ValueError("每个目标需要 2–2048 个历史点 / Need 2–2048 historical points per target")
        if dates is not None and not dates.equals(index):
            raise ValueError("联合预测目标需要相同历史日期，不能自动补零 / Joint targets must have aligned dates")
        values = pd.to_numeric(group["__value"], errors="raise").to_numpy(dtype=float)
        if not np.isfinite(values).all() or np.max(np.abs(values)) > 1e15:
            raise ValueError("销量包含缺失或无效数值，请先清理 / Clean missing or invalid values first")
        contexts.append(values.tolist())
        names.append(name)
        dates = index
    freq = options.get("frequency") or (pd.infer_freq(dates) if len(dates) >= 3 else None)
    if not freq or not isinstance(freq, str):
        raise ValueError("无法确认等间隔频率，请指定 frequency 并检查缺失日期 / Specify frequency and check missing dates")
    expected = pd.date_range(dates[0], periods=len(dates), freq=freq)
    if not dates.equals(expected):
        raise ValueError("历史日期与指定频率不一致 / Historical dates do not match the requested frequency")
    return contexts, names, dates, freq


def run(df, target_column, groupby_column=None, n_deciles=10, analysis_options=None, progress_callback=None, **kwargs):
    from infrastructure.paths import is_cloud_managed_runtime
    if is_cloud_managed_runtime():
        raise ValueError("本地预测仅适用于本地或桌面运行 / Local prediction is unavailable on the hosted server")
    from infrastructure.forecast_client import ForecastClient
    opts = dict(analysis_options or {})
    allowed = {"time_column", "series_column", "target_columns", "horizon", "frequency", "backtest_steps",
               "make_positive", "use_symmetric_averaging", "language", "unit"}
    if set(opts) - allowed:
        raise ValueError("包含不支持的预测选项 / Unsupported forecast options: " + ", ".join(sorted(set(opts) - allowed)))
    en = opts.get("language") == "en"
    say = lambda zh, english: english if en else zh
    progress = progress_callback or (lambda *_: None)
    progress(3, say("校验预测数据与日期", "Validating series and dates"))
    horizon = _integer(opts.get("horizon", 7), "horizon", 1, 256)
    holdout = _integer(opts.get("backtest_steps", 0), "backtest_steps", 0, 256)
    for key in ("make_positive", "use_symmetric_averaging"):
        if key in opts and not isinstance(opts[key], bool):
            raise ValueError(f"{key} 必须为布尔值 / must be boolean")
    contexts, names, dates, freq = _prepare(df, target_column, groupby_column, opts)
    if holdout and len(dates) - holdout < 7:
        raise ValueError("回测需保留至少 7 个历史点 / Backtesting needs at least 7 earlier context points")
    snapshot = {"dates": [d.isoformat() for d in dates], "names": names, "context": contexts}
    fingerprint = hashlib.sha256(json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()).hexdigest()
    run_id = uuid.uuid4().hex
    client = ForecastClient()
    health = client.health()
    predict_opts = {"make_positive": opts.get("make_positive", True),
                    "use_symmetric_averaging": opts.get("use_symmetric_averaging", False)}
    metrics = []
    started = time.perf_counter()
    if holdout:
        progress(15, say("使用历史截断点进行留出回测", "Evaluating a historical holdout"))
        past = [series[:-holdout] for series in contexts]
        validation = client.predict(past, holdout, names, request_id=run_id + "-validation", **predict_opts)
        for k, name in enumerate(names):
            actual = np.asarray(contexts[k][-holdout:])
            predictions = {"timesfm3": validation["forecast"][k],
                           "recent7_mean": [float(np.mean(past[k][-7:]))] * holdout,
                           "seasonal_naive": [past[k][-7 + (j % 7)] for j in range(holdout)]}
            for method, pred in predictions.items():
                error = np.asarray(pred) - actual
                q = np.asarray(validation["quantiles"][k]) if method == "timesfm3" else None
                metrics.append({"series_id": name, "method": method, "MAE": float(np.abs(error).mean()),
                    "RMSE": float(np.sqrt((error ** 2).mean())),
                    "WAPE_pct": float(np.abs(error).sum() / np.abs(actual).sum() * 100) if np.abs(actual).sum() else None,
                    "coverage_pct": float(((q[:, 0] <= actual) & (actual <= q[:, 8])).mean() * 100) if q is not None else None,
                    "evaluated_points": holdout, "run_id": run_id})
    progress(50, say("调用已配置的 TimesFM 预测服务", "Calling the configured TimesFM service"))
    output = client.predict(contexts, horizon, names, request_id=run_id, **predict_opts)
    progress(90, say("整理预测结果与分位数", "Preparing forecast tables and quantiles"))
    future = pd.date_range(dates[-1], periods=horizon + 1, freq=freq)[1:]
    rows = []
    for k, name in enumerate(names):
        for ds, actual in zip(dates, contexts[k]):
            rows.append({"series_id": name, "ds": ds, "y_actual": actual, "y_pred": None,
                         "segment": "history", **{f"q{i}": None for i in range(10, 100, 10)}})
        for j, ds in enumerate(future):
            rows.append({"series_id": name, "ds": ds, "y_actual": None, "y_pred": output["forecast"][k][j],
                         "segment": "forecast", **{f"q{i*10}": output["quantiles"][k][j][i-1] for i in range(1, 10)}})
    result = pd.DataFrame(rows)
    result["run_id"] = run_id
    result["input_sha256"] = fingerprint
    revision = output.get("model_revision") or health.get("model_revision") or "not_reported_by_service"
    metadata = pd.DataFrame([{"run_id": run_id, "model": output["model"], "model_revision": revision,
        "input_sha256": fingerprint, "device": output.get("device", health.get("device", "unknown")),
        "targets": len(names), "context_points": len(dates), "horizon": horizon, "frequency": freq,
        "last_observed_at": dates[-1].isoformat(), "elapsed_seconds": time.perf_counter() - started,
        "server_inference_seconds": output.get("elapsed_seconds"), "backtest_steps": holdout,
        "evaluation_status": "historical_holdout" if holdout else "not_evaluated",
        "unit": opts.get("unit", "source_units"), "license": "research_only"}])
    breakdown = pd.DataFrame(metrics, columns=["series_id", "method", "MAE", "RMSE", "WAPE_pct", "coverage_pct", "evaluated_points", "run_id"])
    markdown = "\n".join([
        say("### TimesFM-3 预测完成", "### TimesFM-3 forecast completed"),
        say(f"{len(names)} 个目标，{len(dates)} 个历史点，预测 {horizon} 步。", f"{len(names)} targets, {len(dates)} historical points, {horizon} forecast steps."),
        f"Run: `{run_id}` · Device: `{metadata.iloc[0]['device']}`",
        say("结果：`analysis_result`；回测：`analysis_breakdown`；来源与耗时：`analysis_run_info`。", "Tables: `analysis_result`, `analysis_breakdown`, `analysis_run_info`."),
        say("图表建议：以 ds 为横轴、y_actual 与 y_pred 为纵轴，按 series_id 分组。", "Chart: ds on the x-axis, y_actual/y_pred on the y-axis, grouped by series_id."),
        say("已使用历史留出数据比较 TimesFM、近期均值与同期基线；不以该窗口自动选择最优方法。" if holdout else "未提供回测窗口，尚未评估准确率。", "Historical holdout compares TimesFM with recent-mean and seasonal baselines; no automatic winner selection." if holdout else "No holdout requested; forecast accuracy has not been evaluated."),
        say("数值保持来源单位；预测不直接等于补货量。模型仅用于非商业、非生产研究。", "Values retain source units; forecasts are not order quantities. Model is for non-commercial, non-production research."),
    ])
    progress(98, say("预测计算完成", "Forecast computation completed"))
    return result, breakdown, metadata, markdown
