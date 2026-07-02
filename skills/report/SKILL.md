---
name: report
description: 规划结构化业务分析报告
icon: 📄
allowedTools: [get_schema, query_data, propose_report_outline]
---
# 报告规划

基于真实数据形成目标、受众、结论、证据和建议结构，引用关键指标与表。先提交报告大纲供确认，不直接生成最终文件；确认后由受控输出流程执行。

## Tool routing

1. Use `get_schema` to identify available source tables, metrics, dimensions, and artifacts.
2. Use `query_data` only to validate the numbers and evidence needed for the outline.
3. Use `propose_report_outline` to create the report structure. Do not export the final report in this skill turn.
4. After user confirmation, the controlled report command flow may call the export tool.
5. Keep the proposal compact: 4–6 sections and no more than 120 Chinese characters
   per section body. Submit an outline, not the complete report正文, so the tool-call
   JSON remains bounded and valid.

## Implementation reference

- Proposal tool entry: `agent/tools/business/export.py::_tool_propose_report_outline`
- Export tool entry: `agent/tools/business/export.py::_tool_export_report`
- Report implementation: `Function/Output/report_export.py`
