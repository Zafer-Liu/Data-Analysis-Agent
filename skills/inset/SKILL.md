---
name: inset
description: 诊断并处理缺失值
icon: 🩹
allowedTools: [get_schema, profile_data, clean_data, query_data]
---
# 缺失值处理

先量化字段和行级缺失，判断缺失机制及业务含义，再选择删除、常数、统计量或分组插补。修改前说明影响，保留可追溯结果，并在处理后验证缺失率和分布变化。

## Tool routing

1. Use `get_schema` to identify tables, nullable fields, and candidate columns.
2. Use `profile_data` to quantify missingness before any modification.
3. Use `clean_data` with the appropriate missing-value operation only when the user intent is clear.
4. Use `query_data` after cleaning to verify row counts, remaining nulls, and distribution changes.

## Implementation reference

- Tool entries: `agent/tools/business/data.py::_tool_profile_data`, `agent/tools/business/data.py::_tool_clean_data`
- Profiling implementation: `Function/Clean/data_profile.py`
- Missing-value implementation: `Function/Clean/missing_handler.py`
