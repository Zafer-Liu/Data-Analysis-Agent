---
name: dashboard
description: 规划业务数据仪表盘/看板（dashboard）
icon: 📊
tags: [mode-business, reporting, visualization]
allowedTools: [get_schema, query_data, ask_user, propose_dashboard_outline]
---
# 仪表盘规划

通过 ask_user 单轮询问收集用户需求，再动态生成 HTML 看板文件。

## 三阶段工作流

### Phase 1 — 理解数据
  Call get_schema ONCE. Run 1-3 lightweight queries to understand tables and columns.
  After data exploration, IMMEDIATELY proceed to Phase 2 in the SAME turn. Do NOT stop or wait.

### Phase 2 — Collect requirements via ask_user (SINGLE question)
  Call ask_user ONCE with a single multi-select question asking what the user wants on the dashboard.
  Derive 4-6 options from actual data columns. Use real column names. Ask in user language.
  After ask_user returns, IMMEDIATELY proceed to Phase 3 in the SAME turn. Do NOT ask again.

### Phase 3 — Design and propose (SAME turn as Phase 2 answer)
  Build 2-6 widgets using ONLY metrics the user selected.
  Each widget needs valid SQL, ONLY real table/column names. NEVER fabricate names.
  KPI_Card: SQL returns 1 row; col1=value, col2=subtitle, col3=trend_pct. Grid: w=3,h=2.
  Bar_Chart / Line_Chart: field_mapping: x, y
  Grouped_Bar_Chart: field_mapping: x, value_cols=[col1,col2,...]
  Stacked_Bar_Chart: field_mapping: x, y=[col1,col2,...]
  Pie_Chart: field_mapping: label, value
  Scatter_Plot: field_mapping: x, y, color(opt)
  Area_Chart: field_mapping: x, y
  Heatmap: field_mapping: x, y, value
  Layout: KPI cards at y=0 (w=3,h=2 each), charts below at y=2+. Total width=12 units.
  Call propose_dashboard_outline(name=..., widgets=[...]). Output NOTHING after the tool call.

## CRITICAL RULES
  - Call ask_user EXACTLY ONCE. Never call it twice.
  - After the user answers ask_user, you MUST call propose_dashboard_outline in the same turn.
  - Do NOT output text after ask_user or propose_dashboard_outline.
  - Do NOT manually call generate_chart — the dashboard system handles chart creation.

## HTML export note
  After generate_dashboard succeeds, the result includes a download-HTML link.
  Mention it naturally: You can also download the HTML file to view offline.

## Implementation reference
- Proposal tool entry: `agent/tools/business/export.py::_tool_propose_dashboard_outline`
- Generation tool entry: `agent/tools/business/export.py::_tool_generate_dashboard`
