---
name: tree
description: 使用决策树识别影响目标变量的关键规则
icon: 🌳
allowedTools: [get_schema, query_data, run_analysis, generate_chart]
---
# 决策树分析

确认目标变量和任务类型，排除泄漏字段，合理处理缺失值与类别变量。执行训练验证并报告性能、重要特征和可解释规则；避免把相关性表述为因果。

## Tool routing

1. Use `get_schema` to identify the target, candidate features, task type, and source table.
2. Use `query_data` to verify field names, target distribution, missingness, and leakage risks.
3. Use `run_analysis` with `analysis_name="Decision_Tree"` for the actual tree computation.
4. Use `generate_chart` on tree, feature importance, or validation result tables after `run_analysis` succeeds.

## Implementation reference

- Tool entry: `agent/tools/business/data.py::_tool_run_analysis`
- Analysis registry: `Function/Analyze/registry.py`
- Analysis implementation: `Function/Analyze/Decision_Tree/analyze.py`
- Chart implementation: `Function/Charts_generation/chart_generate.py`
