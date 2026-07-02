---
name: logistic
description: 执行二分类或多分类逻辑回归分析
icon: 📈
allowedTools: [get_schema, query_data, run_analysis, generate_chart]
---
# 逻辑回归

确认目标编码、正类定义和特征，检查泄漏、共线性及类别不平衡。报告验证集指标、系数或优势比及不确定性，并把模型结论翻译成业务含义。

## Tool routing

1. Use `get_schema` to identify the target, positive-class definition, features, and source table.
2. Use `query_data` to verify class balance, coding, missingness, and candidate feature fields.
3. Use `run_analysis` with `analysis_name="Logistic_Regression"` for the actual model computation.
4. Use `generate_chart` on ROC, feature importance, or result tables after `run_analysis` succeeds.

## Implementation reference

- Tool entry: `agent/tools/business/data.py::_tool_run_analysis`
- Analysis registry: `Function/Analyze/registry.py`
- Analysis implementation: `Function/Analyze/Logistic_Regression/analyze.py`
- Chart implementation: `Function/Charts_generation/chart_generate.py`
