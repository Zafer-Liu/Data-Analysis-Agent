---
name: screening
description: 进行单变量筛选并形成候选解释变量清单
icon: 🔎
allowedTools: [get_schema, query_data, run_analysis, generate_chart]
---
# 单变量筛选

确认目标变量与候选字段，对每个候选变量执行适当的单变量检验或回归。报告效应方向、效应量、显著性、缺失率和样本量；多重比较时提示假阳性风险，不直接宣称因果。

## Tool routing

1. Use `get_schema` to identify the target variable, candidate predictors, and source table.
2. Use `query_data` to verify field names, missingness, and candidate variable types.
3. Use `run_analysis` with `analysis_name="Univariate_Screening"` for the screening computation.
4. Use `generate_chart` on screening rankings or effect result tables after `run_analysis` succeeds.

## Implementation reference

- Tool entry: `agent/tools/business/data.py::_tool_run_analysis`
- Analysis registry: `Function/Analyze/registry.py`
- Analysis implementation: `Function/Analyze/Univariate_Screening/analyze.py`
- Chart implementation: `Function/Charts_generation/chart_generate.py`
