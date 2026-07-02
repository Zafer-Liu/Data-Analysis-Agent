---
name: var
description: 使用 VAR 分析多个时间序列的动态关系
icon: 🔗
allowedTools: [get_schema, query_data, run_analysis, generate_chart]
---
# VAR 分析

确认多个同步时间序列、频率和滞后范围，检查平稳性与样本长度。报告滞后选择、预测表现和动态响应；将格兰杰关系表述为预测信息而非因果证明。

## Tool routing

1. Use `get_schema` to identify multiple time-aligned target series, the time column, and source table.
2. Use `query_data` only to verify frequency alignment, missing periods, stationarity preparation needs, and sample length.
3. Use `run_analysis` with `analysis_name="Time_Series_VAR"` for the actual VAR computation.
4. Use `generate_chart` on forecast, lag, or response result tables after `run_analysis` succeeds.

## Implementation reference

- Tool entry: `agent/tools/business/data.py::_tool_run_analysis`
- Analysis registry: `Function/Analyze/registry.py`
- Analysis implementation: `Function/Analyze/Time_Series_VAR/analyze.py`
- Chart implementation: `Function/Charts_generation/chart_generate.py`
