---
name: prophet
description: 使用 Prophet 分解趋势季节性并预测
icon: 🔮
allowedTools: [get_schema, query_data, run_analysis, generate_chart]
---
# Prophet 预测

确认时间列、目标、频率、预测期及已知节假日。用时间切分验证，展示趋势、季节性、预测区间和误差；异常点或结构突变应单独提示。

## Tool routing

1. Use `get_schema` to identify the time column, target column, known holiday or event fields, and source table.
2. Use `query_data` only to verify frequency, gaps, outliers, and enough history for trend and seasonality.
3. Use `run_analysis` with `analysis_name="Time_Series_Prophet"` for the actual forecast computation.
4. Use `generate_chart` on forecast, trend, or seasonality result tables after `run_analysis` succeeds.

## Implementation reference

- Tool entry: `agent/tools/business/data.py::_tool_run_analysis`
- Analysis registry: `Function/Analyze/registry.py`
- Analysis implementation: `Function/Analyze/Time_Series_Prophet/analyze.py`
- Chart implementation: `Function/Charts_generation/chart_generate.py`
