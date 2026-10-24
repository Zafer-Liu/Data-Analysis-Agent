---
name: timesfm
description: 通过本地 API 或 Docker 服务调用 TimesFM-3，进行单变量与多变量时序预测（forecast sales time series）
tags: [mode-ml, forecasting, modeling]
allowedTools: [get_schema, query_data, run_analysis, generate_chart]
---
# TimesFM-3 预测

使用已在设置 → 本地服务中启用的服务。不得安装模型、下载权重或自动转向收费云服务。服务未配置时引导用户配置，不生成虚构的预测。

1. 使用 `get_schema` 确认日期、数值目标与可选商品/门店标识。查询检查重复时间、缺失值、等间隔及各目标日期对齐；不把缺失或缺货静默填零。
2. 调用 `run_analysis`，`analysis_name="Time_Series_TimesFM3"`；SQL 只读取已观测历史，`target_column` 指定数值列。
3. `analysis_options` 示例：`{"time_column":"date","series_column":"series_id","horizon":7,"frequency":"D","backtest_steps":7}`。联合预测限 1–8 个商品；只把相关商品分成一组，不把编号当作预测目标。宽表可用 `target_columns`，与 `series_column` 二选一。
4. `horizon` 明确控制预测期，默认 7；`n_deciles` 不用于本分析。`backtest_steps` 默认 0，无标签不能声称准确率。历史回测只作对照，不当作独立无偏的最终测试。
5. 基于 `analysis_result` 绘图：`ds`、`y_actual`、`y_pred`、`series_id`。`analysis_breakdown` 保存历史回测指标；`analysis_run_info` 保存版本、数据哈希、设备、耗时与单位。

目前应用入口只支持目标序列，不接受协变量；不要传入未来真实销量。需要协变量时明确说明该能力尚未接入 Agent。TimesFM-3 权重仅限非商业、非生产研究；不把缩放销量当作真实件数，不直接据此下采购单。
