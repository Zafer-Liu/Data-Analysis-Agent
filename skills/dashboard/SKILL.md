---
name: dashboard
description: 规划业务数据仪表盘
icon: 📊
allowedTools: [get_schema, query_data, propose_dashboard_outline]
---
# 仪表盘规划

确认使用者、决策问题、指标口径、筛选维度和刷新需求，设计信息层级与图表。先提交结构方案供确认，不直接生成仪表盘；确认后由受控输出流程执行。

## Tool routing

1. Use `get_schema` to identify candidate metrics, dimensions, date fields, and source tables.
2. Use `query_data` only to validate representative metrics or small samples needed for the outline.
3. Use `propose_dashboard_outline` to create the dashboard plan. Do not generate dashboard files in this skill turn.
4. After user confirmation, the controlled dashboard command flow may call the generation tool.

## Implementation reference

- Proposal tool entry: `agent/tools/business/export.py::_tool_propose_dashboard_outline`
- Generation tool entry: `agent/tools/business/export.py::_tool_generate_dashboard`
