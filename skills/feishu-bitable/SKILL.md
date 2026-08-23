---
name: feishu-bitable
description: 将飞书多维表格接入为 SQL 数据源，完成分析、报告并在明确授权后回写或生成结果表。
icon: 📊
allowedTools: [list_feishu_bitable_tables, load_feishu_bitable, get_schema, get_table_detail, query_data, run_analysis, select_chart, generate_chart, propose_report_outline, export_report, create_feishu_bitable, append_feishu_bitable_records, update_feishu_bitable_record]
---

# 飞书多维表格分析与回写

目标：通过已配置的飞书应用机器人读取用户明确指定的多维表格，将读取结果接入当前会话的 DuckDB/SQL 数据源，再用项目现有分析、图表和报告能力完成任务；仅在用户明确提出写入时回写或创建飞书表格。

## 工作流程

1. 先确认用户给出了飞书多维表格链接或 `app_token`。不得猜测企业中的表格、目录、`table_id` 或 `record_id`。
2. 若链接不含 `?table=...`，先调用 `list_feishu_bitable_tables`，展示可访问的数据表并请用户选择；只有在用户已经明确指定某张表时，才继续读取。
3. 调用 `load_feishu_bitable`。它会把最多 500 条记录作为本会话的 SQL 数据源接入；读取结果中 `_feishu_record_id` 仅用于之后明确的逐条更新，不是业务指标。
4. 读取后先调用 `get_schema`，再用 `query_data`、`run_analysis` 和图表工具基于实际列名与实际结果分析。不得编造数值、字段或结论。
5. 在对话中先给出简洁的证据、结论与限制。用户要求正式报告时，走既有 `propose_report_outline` → 用户确认 → `export_report` 流程。

## 写回规则

- `append_feishu_bitable_records`：仅在用户明确要求把哪些结果写入一张已有表时调用。必须使用目标表已存在的字段名，并在调用后交付返回的链接。
- `update_feishu_bitable_record`：仅在用户明确要求修改指定记录时调用。`record_id` 必须来自本次或之前的真实读取结果，绝不猜测。
- `create_feishu_bitable`：仅在用户明确要求创建新表时调用。先说明拟创建的表名、列和示例记录；字段保持简短明确。一次最多写入 500 条初始记录，并按 100 条安全分批提交；成功后交付真实返回的链接。
- 不要因为完成分析就自动写回；读取和分析默认只读。若用户说“把结果写回去”但没有目标表或字段映射，先提出最小必要澄清。

## 结果表建议

需要输出分析好的新表时，优先建立一张清晰的结果表，例如：`指标`、`结果`、`同比/环比`、`结论`、`证据/口径`。所有数据必须来自实际工具结果；无法验证的项应标注为待确认。

## 权限与错误

应用机器人必须同时拥有访问目标多维表格的权限以及所需的读取/写入权限。遇到飞书返回的权限或资源错误时，如实返回错误码与下一步配置建议；不得伪造表格链接、表 ID 或“已创建/已写入”的结果。
