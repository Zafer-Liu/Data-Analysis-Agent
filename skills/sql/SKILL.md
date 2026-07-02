---
name: sql
description: 使用 SQL 安全查询当前已选择的数据表
icon: 🗄️
allowedTools: [get_schema, get_table_detail, query_data]
---
# SQL 查询

先读取已授权分析表的 schema，确认真实表名与字段名，再编写只读 SQL。优先聚合并限制返回行数，不猜测字段；SQL 数据源只能查询用户已选择的分析表。解释查询口径并总结结果。

## Tool routing

1. Use `get_schema` before writing SQL to confirm real table and field names.
2. Use `get_table_detail` when a specific table needs deeper metadata.
3. Use `query_data` for read-only SQL. Prefer aggregate queries and explicit `LIMIT` for previews.
4. Do not use write, export, cleanup, or analysis tools from this skill.

## Implementation reference

- Schema/query tool entries: `agent/tools/business/data.py::_tool_get_schema`, `agent/tools/business/data.py::_tool_get_table_detail`, `agent/tools/business/data.py::_tool_query_data`
- SQL validation: `agent/validate.py`
