---
name: sarima
description: 使用 SARIMA 建模季节性时间序列
icon: 🌊
allowedTools: [get_schema, query_data, run_analysis, generate_chart]
---
# SARIMA 预测

确认时间频率和季节周期，检查数据长度能否覆盖足够周期。执行季节模型与时间验证，报告参数、误差、预测区间和季节模式；数据不足时不要强行拟合。

## Tool routing

1. Use `get_schema` to identify the time column, target column, seasonal frequency, and source table.
2. Use `query_data` only to verify sorted frequency, missing periods, and enough seasonal cycles.
3. Use `run_analysis` with `analysis_name="Time_Series_SARIMA"` for the actual forecast computation.
4. Use `generate_chart` on forecast or seasonal diagnostic result tables after `run_analysis` succeeds.

## Implementation reference

- Tool entry: `agent/tools/business/data.py::_tool_run_analysis`
- Analysis registry: `Function/Analyze/registry.py`
- Analysis implementation: `Function/Analyze/Time_Series_SARIMA/analyze.py`
- Chart implementation: `Function/Charts_generation/chart_generate.py`
