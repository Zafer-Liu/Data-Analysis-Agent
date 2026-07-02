---
name: export
description: 规划并确认 Excel 数据导出
icon: 📥
allowedTools: [get_schema, query_data, propose_excel_export]
---
# Excel 导出

检查数据范围、字段、筛选条件和工作表组织，生成可确认的导出方案。先提案，不直接生成文件；用户确认后由受控导出流程写入 outputs。

## Tool routing

1. Use `get_schema` to identify exportable tables and fields.
2. Use `query_data` only to validate row counts, filters, or preview values needed for the export plan.
3. Use `propose_excel_export` to create a confirmation-ready export plan. Do not write files in this skill turn.
4. After user confirmation, the controlled export command flow writes the final workbook.

## Implementation reference

- Proposal tool entry: `agent/tools/business/export.py::_tool_propose_excel_export`
- Export tool entry: `agent/tools/business/export.py::_tool_export_excel`
- Export implementation: `Function/Output/excel_export.py`
