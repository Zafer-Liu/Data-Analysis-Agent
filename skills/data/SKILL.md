---
name: data
description: 查看数据结构、质量和可分析性
icon: 🔍
allowedTools: [get_schema, get_table_detail, query_data, profile_data, generate_chart]
---
# 数据诊断

先获取 schema 和必要样本，报告表规模、字段类型、缺失、重复、异常、时间范围和关键分布。区分事实、风险与建议，指出可直接分析的字段及需要用户确认的口径。

## Tool routing

1. Use `get_schema` first to inspect tables, fields, row counts, and source structure.
2. Use `get_table_detail` when one table needs deeper field-level metadata.
3. Use `profile_data` for data quality, missingness, type, and distribution diagnostics.
4. Use `query_data` for small verification samples or targeted aggregates.
5. Use `generate_chart` only when a compact diagnostic chart materially helps explain the data.

## Implementation reference

- Data tool entries: `agent/tools/business/data.py`
- Profiling implementation: `Function/Clean/data_profile.py`
- Chart implementation: `Function/Charts_generation/chart_generate.py`
