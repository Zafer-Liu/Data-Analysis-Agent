---
name: kmeans
description: 使用 K-Means 对业务对象进行聚类画像
icon: 🔵
allowedTools: [get_schema, query_data, run_analysis, generate_chart]
---
# K-Means 聚类

确认聚类实体与特征，处理缺失值并标准化数值变量。比较合理的 K 值，报告聚类质量、各簇规模、中心特征和业务画像，并说明异常点及稳定性限制。

## Tool routing

1. Use `get_schema` to identify the entity key, candidate numeric features, and source table.
2. Use `query_data` to verify feature availability, missingness, and scale before modeling.
3. Use `run_analysis` with `analysis_name="K_Means"` for the clustering computation.
4. Use `generate_chart` on cluster profiles, elbow output, or label result tables after `run_analysis` succeeds.

## Implementation reference

- Tool entry: `agent/tools/business/data.py::_tool_run_analysis`
- Analysis registry: `Function/Analyze/registry.py`
- Analysis implementation: `Function/Analyze/K-Means/analyze.py`
- Chart implementation: `Function/Charts_generation/chart_generate.py`
