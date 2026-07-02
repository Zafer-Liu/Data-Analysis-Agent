---
name: arima
description: 使用 ARIMA 进行单变量时间序列预测
icon: 〰️
allowedTools: [get_schema, query_data, run_analysis, generate_chart]
---
# ARIMA 预测

确认时间列、频率、目标和预测区间，按时间排序并处理缺口。检查平稳性，选择合理参数，使用时间切分验证，输出预测值、区间和误差，并说明外部冲击限制。

## Tool routing

1. Use `get_schema` to identify the time column, target column, table name, and available covariates.
2. Use `query_data` only to verify ordering, missing timestamps, frequency, and enough rows for modeling.
3. Use `run_analysis` with `analysis_name="Time_Series_ARIMA"` for the actual forecast computation.
4. Use `generate_chart` on the returned forecast or diagnostic result tables after `run_analysis` succeeds.

## Implementation reference

- Tool entry: `agent/tools/business/data.py::_tool_run_analysis`
- Analysis registry: `Function/Analyze/registry.py`
- Analysis implementation: `Function/Analyze/Time_Series_ARIMA/analyze.py`
- Chart implementation: `Function/Charts_generation/chart_generate.py`
