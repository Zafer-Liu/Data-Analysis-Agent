---
name: regression
description: 执行线性回归并解释关键变量影响
icon: 📐
allowedTools: [get_schema, query_data, run_analysis, generate_chart]
---
# 线性回归

确认连续目标、解释变量和分析口径，检查缺失、异常、共线性与残差假设。报告拟合与验证指标、系数方向和不确定性；明确相关关系不等于因果关系。

## Tool routing

1. Use `get_schema` to identify the continuous target, candidate predictors, and source table.
2. Use `query_data` to verify field names, missingness, outliers, and whether the target is numeric.
3. Use `run_analysis` with `analysis_name="Regression"` for the actual regression computation.
4. Use `generate_chart` on coefficient, residual, or prediction result tables after `run_analysis` succeeds.

## Implementation reference

- Tool entry: `agent/tools/business/data.py::_tool_run_analysis`
- Analysis registry: `Function/Analyze/registry.py`
- Analysis implementation: `Function/Analyze/Regression/analyze.py`
- Chart implementation: `Function/Charts_generation/chart_generate.py`
