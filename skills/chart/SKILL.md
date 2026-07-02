---
name: chart
description: 根据当前数据选择并生成合适的业务图表
icon: 📊
allowedTools: [get_schema, query_data, select_chart, generate_chart]
---
# 图表分析

先检查字段、口径和数据量，再查询绘图所需的最小数据集。根据比较、趋势、分布、关系或构成目标选择图形；标题、坐标轴和单位必须明确。若用户未指定图形，说明选择理由并生成最合适的一种。

## Tool routing

1. Use `get_schema` to inspect available tables and fields.
2. Use `query_data` to verify the minimum data needed for the chart and avoid guessing column names.
3. Use `select_chart` when the user intent does not already determine a chart type.
4. Use `generate_chart` with the selected chart id and explicit field mapping.

## Implementation reference

- Tool entries: `agent/tools/business/data.py::_tool_select_chart`, `agent/tools/business/data.py::_tool_generate_chart`
- Chart implementation: `Function/Charts_generation/chart_generate.py`
- Chart catalog: `Function/Charts_generation/charts/`
