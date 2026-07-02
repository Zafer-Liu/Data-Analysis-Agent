---
name: gru
description: 使用 GRU 对足量时间序列进行预测
icon: 🧠
allowedTools: [get_schema, query_data, run_analysis, generate_chart]
---
# GRU 预测

仅在样本量和序列长度足够时使用。确认窗口、特征和预测期，严格按时间划分训练验证，避免泄漏，报告基线对比、误差和不确定性；小数据优先建议传统时序模型。

## Tool routing

1. Use `get_schema` to identify the time column, target column, feature columns, and source table.
2. Use `query_data` only to verify sorted sequence length, missingness, and whether the sample size is sufficient.
3. Use `run_analysis` with `analysis_name="Time_Series_GRU"` for the actual model computation.
4. Use `generate_chart` on forecast or validation result tables after `run_analysis` succeeds.

## Implementation reference

- Tool entry: `agent/tools/business/data.py::_tool_run_analysis`
- Analysis registry: `Function/Analyze/registry.py`
- Analysis implementation: `Function/Analyze/Time_Series_GRU/analyze.py`
- Chart implementation: `Function/Charts_generation/chart_generate.py`
