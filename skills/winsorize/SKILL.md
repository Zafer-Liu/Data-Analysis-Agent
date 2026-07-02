---
name: winsorize
description: 对极端值执行缩尾处理并验证影响
icon: ✂️
allowedTools: [get_schema, profile_data, clean_data, query_data]
---
# 缩尾处理

识别目标数值字段和异常分布，说明上下界规则后再缩尾。不得默认覆盖原始数据；比较处理前后的分布、均值和关键指标，并记录边界与受影响行数。

## Tool routing

1. Use `get_schema` to identify the target table and candidate numeric columns.
2. Use `profile_data` to quantify distribution tails before modification.
3. Use `clean_data` with the winsorize operation only after the percentile bounds are clear.
4. Use `query_data` after cleaning to verify clipped values, row counts, and key metric changes.

## Implementation reference

- Tool entries: `agent/tools/business/data.py::_tool_profile_data`, `agent/tools/business/data.py::_tool_clean_data`
- Profiling implementation: `Function/Clean/data_profile.py`
- Winsorization implementation: `Function/Clean/winsorize.py`
