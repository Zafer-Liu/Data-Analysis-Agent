---
name: trimming
description: 对异常样本执行截尾处理并评估偏差
icon: 🔪
allowedTools: [get_schema, profile_data, clean_data, query_data]
---
# 截尾处理

先定义异常判据和业务合理范围，量化拟删除样本及其特征。仅在用户意图明确时执行，保留原始数据和可追溯输出；处理后报告样本损失及潜在选择偏差。

## Tool routing

1. Use `get_schema` to identify the target table and candidate numeric columns.
2. Use `profile_data` to quantify outliers and candidate trim boundaries before modification.
3. Use `clean_data` with the trimming operation only when the user has confirmed the rule or bounds.
4. Use `query_data` after cleaning to verify row loss, boundary effects, and key metric changes.

## Implementation reference

- Tool entries: `agent/tools/business/data.py::_tool_profile_data`, `agent/tools/business/data.py::_tool_clean_data`
- Profiling implementation: `Function/Clean/data_profile.py`
- Trimming implementation: `Function/Clean/trimming.py`
