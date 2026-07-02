---
name: ppt
description: 规划数据分析演示文稿
icon: 🎯
allowedTools: [get_schema, query_data, propose_ppt_outline]
---
# PPT 规划

明确受众、场景、页数和核心叙事，用真实数据规划逐页标题、要点和图表。先提交大纲供确认，不直接生成文件；确认后由受控 PPT 流程执行。

## Tool routing

1. Use `get_schema` to identify available evidence tables, metrics, and chart candidates.
2. Use `query_data` only to validate key numbers that will appear in the deck outline.
3. Use `propose_ppt_outline` to create the slide plan. Do not generate PPT files in this skill turn.
4. After user confirmation, the controlled PPT command flow may call the generation tool.

## Implementation reference

- Proposal tool entry: `agent/tools/business/export.py::_tool_propose_ppt_outline`
- Generation tool entry: `agent/tools/business/export.py::_tool_generate_ppt`
- PPT implementation: `Function/Output/PPT/`
