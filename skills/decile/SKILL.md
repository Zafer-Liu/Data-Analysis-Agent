---
name: decile
description: 执行十分位分层分析并识别高低价值群体
icon: 📉
allowedTools: [get_schema, query_data, run_analysis, generate_chart]
---
# 十分位分析

确认排序指标、分析实体和方向，将有效样本等频划分为十组，报告每组样本量、指标范围、核心结果及累计贡献。标记空值和并列值处理方式，并给出可执行的分层策略。

## Tool routing

1. Use `get_schema` to identify the entity key, ranking metric, target metric, and source table.
2. Use `query_data` to verify field names, null handling needs, and sample distribution.
3. Use `run_analysis` with `analysis_name="Data_Decile_Analysis"` for the decile computation.
4. Use `generate_chart` on returned decile result tables after `run_analysis` succeeds.

## Implementation reference

- Tool entry: `agent/tools/business/data.py::_tool_run_analysis`
- Analysis registry: `Function/Analyze/registry.py`
- Analysis implementation: `Function/Analyze/Data_Decile_Analysis/analyze.py`
- Chart implementation: `Function/Charts_generation/chart_generate.py`
