---
name: funnel-analysis
description: 诊断业务漏斗各环节转化率，定位主要流失点并给出优先行动建议
icon: 🔻
allowedTools: [get_schema, query_data, generate_chart]
---
# Funnel analysis workflow

Apply this workflow to the user's request:

> $ARGUMENTS

1. Confirm the funnel stages, entity identifier, time field, and analysis period. Ask one concise question if a critical definition is missing.
2. Inspect the schema before querying. Never invent stage names or event definitions.
3. Calculate the entity count at each stage, step conversion rate, cumulative conversion rate, and loss from the preceding stage.
4. Segment the largest loss by relevant dimensions such as channel, region, product, or customer cohort when the data supports it.
5. Separate observed facts from hypotheses. Quantify every important claim and cite the source table or analysis result.
6. End with the top three prioritized actions, their expected mechanism, and the metric that should be monitored.

Prefer a funnel chart or a compact stage-by-stage table. If stage ordering is ambiguous, do not guess—ask the user.

## Tool routing

1. Use `get_schema` to identify candidate event, stage, entity, time, and segmentation fields.
2. Use `query_data` to compute stage counts, conversion rates, cumulative conversion, and loss by segment.
3. Use `generate_chart` only after the funnel table is available and the stage order is explicit.
4. If stage definitions or ordering are missing, ask one concise clarification before querying.

## Implementation reference

- Query tool entry: `agent/tools/business/data.py::_tool_query_data`
- Chart tool entry: `agent/tools/business/data.py::_tool_generate_chart`
- Chart implementation: `Function/Charts_generation/chart_generate.py`
