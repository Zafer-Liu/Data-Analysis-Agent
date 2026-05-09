#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Business Analyst Agent — tool-calling loop via OpenAI-compatible SDK."""
import json
import sys
import os
from typing import Iterator, List, Dict, Any, Optional

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CHARTS_GEN = os.path.join(_PROJ_ROOT, "Function", "Charts_generation")
sys.path.insert(0, _PROJ_ROOT)
sys.path.insert(0, _CHARTS_GEN)

# ── Analyze registry (lazy-loaded, best-effort) ───────────────────────────
def _build_analyze_guide() -> str:
    try:
        from Function.Analyze.registry import build_agent_desc
        return build_agent_desc()
    except Exception:
        return "  Data_Decile_Analysis — 十分位分析（Decile Analysis）"

_ANALYZE_GUIDE = _build_analyze_guide()


def _build_chart_guide() -> tuple[str, str]:
    """Return (system_prompt_guide, tool_type_list) built from the registry.
    Falls back to a hardcoded minimal list if the registry cannot be imported."""
    try:
        from charts.registry import REGISTRY
        lines, ids = [], []
        current_cat = ""
        for c in REGISTRY:
            if "ongoing" in c.name.lower():
                continue
            ids.append(c.chart_id)
            if c.category != current_cat:
                current_cat = c.category
                lines.append(f"\n[{current_cat}]")
            lines.append(f"  {c.chart_id:<35} → {c.desc[:80]}")
        return "\n".join(lines), ", ".join(ids)
    except Exception:
        fallback = (
            "Bar_Chart, Line_Chart, Pie_Chart, Scatter_Plot, Area_Chart, "
            "Heatmap, Waterfall, Treemap, Sunburst_Diagram, Nightingale_Chart"
        )
        return fallback, fallback


_CHART_GUIDE, _CHART_IDS = _build_chart_guide()

# ── Tool schemas sent to the LLM ──────────────────────────────────────────

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_schema",
            "description": (
                "Get the full schema of the connected data source — tables, columns, "
                "types, and row counts. Always call this first when the user asks "
                "about data you haven't seen yet."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_analysis_table",
            "description": (
                "Extract specific fields from the raw data and materialise the result "
                "as a new queryable table. Use this to: (1) select only the columns "
                "needed for the current analysis, (2) pre-aggregate or filter large "
                "datasets before charting, (3) join / reshape data into the exact "
                "shape a chart requires. The resulting table is immediately available "
                "to query_data and generate_chart by its table_name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": (
                            "SQL SELECT that defines the analysis table — "
                            "select the exact columns needed, apply WHERE filters, "
                            "GROUP BY aggregations, JOINs, etc."
                        ),
                    },
                    "table_name": {
                        "type": "string",
                        "description": (
                            "Name for the new temp table (default: 'analysis_data'). "
                            "Use a descriptive name when creating multiple tables."
                        ),
                    },
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_data",
            "description": "Execute a SQL SELECT query and return the results as a table.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A valid SQL SELECT statement using actual column/table names from the schema.",
                    }
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_analysis",
            "description": (
                "Run a built-in statistical analysis template on the data.\n"
                "Steps: (1) call get_schema to know the tables/columns, "
                "(2) call run_analysis with the appropriate parameters, "
                "(3) the result is stored as queryable tables — call generate_chart on them.\n\n"
                "Available analyses:\n"
                f"{_ANALYZE_GUIDE}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "analysis_name": {
                        "type": "string",
                        "description": "Analysis ID, e.g. 'Data_Decile_Analysis'.",
                    },
                    "sql": {
                        "type": "string",
                        "description": (
                            "SQL SELECT to fetch the raw data for analysis. "
                            "Include the target column and any optional groupby column. "
                            "Example: SELECT revenue, region FROM sales_data"
                        ),
                    },
                    "target_column": {
                        "type": "string",
                        "description": "The numeric column to analyse (must exist in the SQL result).",
                    },
                    "groupby_column": {
                        "type": "string",
                        "description": "(Optional) A categorical column for additional breakdown.",
                    },
                    "n_deciles": {
                        "type": "integer",
                        "description": "Number of buckets (default 10). Use 5 for quintiles, 4 for quartiles.",
                    },
                },
                "required": ["analysis_name", "sql", "target_column"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_chart",
            "description": (
                "Create a data visualization chart displayed to the user. "
                "Use after querying to confirm the data shape. "
                "See the system prompt for the complete chart type list and their field_mapping requirements."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chart_type": {
                        "type": "string",
                        "description": (
                            f"Exact chart_id from the registry. Available: {_CHART_IDS}"
                        ),
                    },
                    "sql": {
                        "type": "string",
                        "description": "SQL query to retrieve data for the chart.",
                    },
                    "field_mapping": {
                        "type": "object",
                        "description": (
                            "Maps chart field roles to column names per the chart's data_format. "
                            'E.g. {"x": "month", "y": "revenue"} or '
                            '{"label": "product", "value": "sales"}.'
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "Chart title shown above the visualization.",
                    },
                },
                "required": ["chart_type", "sql", "field_mapping"],
            },
        },
    },
]

SYSTEM_PROMPT = f"""You are a professional business analyst assistant embedded in a data analytics platform.
Your job: help users understand and derive insights from their business data through conversation.

Behaviour rules:
1. Always call get_schema before writing SQL if you don't already know the table structure.
2. Use exact column and table names from the schema — never guess.
3. After showing raw data, add a concise business insight (1-3 sentences).
4. Proactively suggest a relevant chart after answering data questions.
5. Respond in the same language the user used (Chinese or English).
6. Format numbers with separators and units where possible (e.g. ¥1,234,567 or 38.5%).
7. Use create_analysis_table when it genuinely helps: multi-step aggregations, joining sheets,
   or reshaping data before charting. For simple single-table queries with few columns, write
   the SQL directly in generate_chart instead — avoid unnecessary extra round-trips.
8. When the user invokes /analyze <AnalysisName>, use run_analysis with the named template.
   After run_analysis succeeds, ALWAYS generate at least one chart from the result tables.
   Result tables (module-specific, check OUTPUT_TABLES in each module):
     analysis_result    — primary summary table (always written)
     analysis_breakdown — secondary per-sample or cross-tab table (if non-empty)
     analysis_roc       — Decision_Tree only: ROC curve points (fpr/tpr/auc/class)
     analysis_elbow     — K_Means only: elbow curve (k/inertia/silhouette)
   Recommended charts per analysis:
     - Data_Decile_Analysis:
         Bar_Chart(analysis_result, x=decile, y=sum) + Line_Chart(x=decile, y=cumulative_pct)
     - Decision_Tree:
         Bar_Chart(analysis_result, x=feature, y=importance_pct)       — feature importance
         Heatmap(analysis_breakdown, x=predicted, y=actual, z=count)   — confusion matrix
         Line_Chart(analysis_roc, x=fpr, y=tpr, series=class)          — ROC curve
     - K_Means:
         Bar_Chart(analysis_result, x=cluster, y=count)                — cluster sizes
         Scatter_Plot(analysis_breakdown, x=<feat1>, y=<feat2>, color=cluster) — cluster view
         Line_Chart(analysis_elbow, x=k, y=inertia)                    — elbow curve

Complete chart type list (use the exact chart_id shown):
{_CHART_GUIDE}

field_mapping key rules (use the required_roles from each chart's description):
- Most charts: x/y for axes, series for grouping
- Pie/Nightingale: label+value or names+values
- Treemap/Sunburst: labels+values (+ optional parents)
- Sankey/Chord/Arc: source+target+value (or x+y+z)
- Distribution charts (Box, Violin, Beeswarm, Ridgeline): y (+ optional x for grouping)
- Parallel coordinates: dimensions (list of column names) + optional color
- Geographic charts: label+value (+ optional category)
"""


class BusinessAgent:
    MAX_ITERATIONS = 100

    def __init__(self, client, model: str, data_source=None, enable_thinking: bool = False):
        self.client = client
        self.model = model
        self.data_source = data_source
        self.enable_thinking = enable_thinking
        self._schema_cache: Optional[str] = None

    def set_data_source(self, source):
        self.data_source = source
        self._schema_cache = None

    # ── Tool implementations ───────────────────────────────────────────────

    def _tool_get_schema(self) -> str:
        if not self.data_source:
            return "No data source connected."
        if not self._schema_cache:
            self._schema_cache = self.data_source.get_schema()
        return self._schema_cache

    def _tool_query_data(self, sql: str) -> str:
        if not self.data_source:
            return "No data source. Please connect a database or upload an Excel file first."
        df, error = self.data_source.execute_query(sql)
        if error:
            return f"SQL Error: {error}"
        return self.data_source.format_result(df)

    def _tool_create_analysis_table(self, sql: str, table_name: str = "analysis_data") -> str:
        if not self.data_source:
            return "No data source connected."
        result = self.data_source.create_analysis_table(sql, table_name)
        # Invalidate schema cache so the new table shows up in get_schema
        self._schema_cache = None
        return result

    # ── DataFrame → DataSource writer (backward-compatible) ───────────────

    def _write_analysis_df(self, df, table_name: str) -> None:
        """
        Write *df* into the connected data source as a queryable table.

        Strategy (two-tier for backward compatibility):
        1. Try the new connector API: create_analysis_table(sql=None, _df=df, ...)
        2. If the connector is an older version that lacks the *_df* parameter,
           fall back to writing directly to the underlying SQLite connection:
           - ExcelDataSource / CSVDataSource → self._conn  (in-memory SQLite)
           - SQLDataSource                  → self._cache_conn (local SQLite cache)
        This ensures the result tables are queryable even on older installs
        without requiring users to manually update connector.py.
        """
        import sqlite3

        ds = self.data_source

        # ── Attempt new API ────────────────────────────────────────────────
        try:
            ds.create_analysis_table(sql=None, table_name=table_name, _df=df)
            self._schema_cache = None
            return
        except TypeError:
            pass   # old connector — fall through to direct SQLite write

        # ── Fallback: direct SQLite write ──────────────────────────────────
        # ExcelDataSource / CSVDataSource store data in self._conn
        conn = getattr(ds, "_conn", None)

        if conn is None:
            # SQLDataSource: create/reuse local cache connection
            if getattr(ds, "_cache_conn", None) is None:
                ds._cache_conn = sqlite3.connect(":memory:", check_same_thread=False)
                ds._cache_tables = set()
            conn = ds._cache_conn
            ds._cache_tables.add(table_name)

        df.to_sql(table_name, conn, if_exists="replace", index=False)
        self._schema_cache = None

    # ── Analysis tool ──────────────────────────────────────────────────────

    def _tool_run_analysis(
        self,
        analysis_name: str,
        sql: str,
        target_column: str,
        groupby_column: str = "",
        n_deciles: int = 10,
    ) -> str:
        """
        Run a registered analysis template.
        Returns a markdown summary; also materialises result tables
        into the data source for subsequent generate_chart calls.
        """
        if not self.data_source:
            return "No data source connected."

        # 1. Fetch raw data
        df, error = self.data_source.execute_query(sql)
        if error:
            return f"SQL Error while fetching data: {error}"
        if df.empty:
            return "Query returned no rows — cannot run analysis."

        # 2. Dispatch to analysis module
        try:
            from Function.Analyze.registry import get as get_analysis
            entry = get_analysis(analysis_name)
        except KeyError as exc:
            return str(exc)
        except Exception as exc:
            return f"Failed to load analysis module '{analysis_name}': {exc}"

        run_fn = entry.get("run")
        if run_fn is None:
            return f"Analysis module '{analysis_name}' failed to load."

        try:
            ret = run_fn(
                df=df,
                target_column=target_column,
                groupby_column=groupby_column or None,
                n_deciles=n_deciles,
            )
        except Exception as exc:
            return f"Analysis error: {exc}"

        # 支持 3-tuple（旧模块）和 4-tuple（带 extra_df，如 analysis_roc）
        if len(ret) == 4:
            result_df, breakdown_df, extra_df, markdown = ret
        else:
            result_df, breakdown_df, markdown = ret
            extra_df = None

        # 3. Materialise result tables so LLM can query/chart them
        try:
            self._write_analysis_df(result_df, "analysis_result")

            if not breakdown_df.empty:
                self._write_analysis_df(breakdown_df, "analysis_breakdown")

            if extra_df is not None and not extra_df.empty:
                # 从模块 OUTPUT_TABLES[2] 读取第三张表的名称（各模块可自定义）
                _out_tbls = entry.get("output_tables", [])
                extra_table_name = _out_tbls[2] if len(_out_tbls) > 2 else "analysis_roc"
                self._write_analysis_df(extra_df, extra_table_name)

        except Exception as exc:
            return (
                markdown
                + f"\n\n⚠️ **结果表写入失败**：{exc}\n"
                "分析计算已完成，但结果无法存为可查询表格，请联系开发者。"
            )

        return markdown

    def _tool_generate_chart(
        self, chart_type: str, sql: str, field_mapping: dict, title: str = ""
    ) -> dict:
        if not self.data_source:
            return {"error": "No data source connected."}
        df, error = self.data_source.execute_query(sql)
        if error:
            return {"error": f"Data query failed: {error}"}
        if df.empty:
            return {"error": "Query returned no rows — cannot generate chart."}

        from chart_generate import generate_chart as _gen

        options = {"title": title} if title else {}
        result = _gen(df=df, chart_type=chart_type, mapping=field_mapping, options=options)

        if "error" in result:
            return {"error": result["error"]}
        return {"html": result.get("html", ""), "chart_type": chart_type}

    # ── Agent loop ─────────────────────────────────────────────────────────

    # ── Slash-command → system-hint mapping ────────────────────────────────
    COMMAND_HINTS: Dict[str, str] = {
        "chart": (
            "The user issued the /chart command. Your primary goal for this turn is to "
            "generate one or more data visualizations. Query the relevant data first, "
            "then call generate_chart. End with a brief interpretation of the chart."
        ),
        "sql": (
            "The user issued the /sql command. Execute the SQL they described and show "
            "the results clearly formatted as a table, then provide a short insight."
        ),
        "analyze": (
            "The user issued the /analyze command, optionally followed by an analysis name "
            "(e.g. 'Data_Decile_Analysis') and a description of what to analyse.\n"
            "Workflow:\n"
            "1. Call get_schema to understand the data structure.\n"
            "2. Identify the analysis name from the user message (default: Data_Decile_Analysis).\n"
            "3. Choose the most appropriate numeric target_column from the schema.\n"
            "   If the user mentioned a column or metric, use that; otherwise pick the most "
            "   business-relevant numeric column (e.g. revenue, amount, score).\n"
            "4. Optionally choose a groupby_column if the user asked for a breakdown by category.\n"
            "5. Call run_analysis with the chosen parameters.\n"
            "6. After run_analysis, generate charts from the result tables:\n"
            "   For Data_Decile_Analysis:\n"
            "     a) Bar_Chart(analysis_result): x=decile, y=sum  (value by bucket)\n"
            "     b) Line_Chart(analysis_result): x=decile, y=cumulative_pct  (Pareto curve)\n"
            "   For Decision_Tree — ALWAYS generate all three charts:\n"
            "     a) Bar_Chart(analysis_result): x=feature, y=importance_pct  (feature importance)\n"
            "     b) Heatmap(analysis_breakdown): x=predicted, y=actual, z=count  (confusion matrix)\n"
            "     c) Line_Chart(analysis_roc): x=fpr, y=tpr, series=class  (ROC curve, one line per class)\n"
            "        The Line_Chart title should include AUC values for each class.\n"
            "   For K_Means — ALWAYS generate all three charts:\n"
            "     a) Bar_Chart(analysis_result): x=cluster, y=count  (cluster sizes)\n"
            "     b) Scatter_Plot(analysis_breakdown): x=<feat1>, y=<feat2>, color=cluster\n"
            "        (pick the 2 most business-relevant numeric columns for x and y axes)\n"
            "     c) Line_Chart(analysis_elbow): x=k, y=inertia  (elbow curve)\n"
            "   K_Means SQL tip: SELECT the numeric features to cluster on directly,\n"
            "   e.g. SELECT age, income, spending FROM customers. Set n_deciles = K.\n"
            "   If analysis_breakdown is available for other analyses, generate a Heatmap.\n"
            "7. Conclude with a concise business interpretation (2-4 sentences)."
        ),
        "report": (
            "The user issued the /report command. Structure your response as a formal "
            "analysis report with: Executive Summary, Key Findings (with data), "
            "Visualizations, and Recommendations."
        ),
    }

    def run(self, user_message: str, history: List[Dict], command: str = "") -> Iterator[Dict]:
        """
        Yields event dicts consumed by the Flask SSE stream:
          {"type": "tool_start",  "tool": str, "display": str}
          {"type": "chart_ready", "chart_id": str}
          {"type": "text",        "content": str}
          {"type": "done"}
          {"type": "error",       "message": str}

        Charts are stored in the `pending_charts` list and passed back via
        the "chart_ready" event so the Flask layer can persist them.
        """
        if not self.data_source:
            yield {
                "type": "text",
                "content": (
                    "请先连接数据源（上传 Excel 文件或连接 SQL 数据库），然后再开始分析。\n\n"
                    "Please connect a data source (upload Excel or connect to a SQL database) first."
                ),
            }
            yield {"type": "done"}
            return

        system = SYSTEM_PROMPT
        if command and command in self.COMMAND_HINTS:
            system += f"\n\n[ACTIVE COMMAND: /{command}]\n{self.COMMAND_HINTS[command]}"

        messages: List[Dict] = [
            {"role": "system", "content": system},
            *history[-20:],
            {"role": "user", "content": user_message},
        ]

        pending_charts: List[dict] = []  # filled by generate_chart tool calls
        all_reasoning: List[str] = []   # accumulated across all loop iterations

        for _ in range(self.MAX_ITERATIONS):
            try:
                call_kwargs: Dict[str, Any] = dict(
                    model=self.model,
                    messages=messages,
                    tools=AGENT_TOOLS,
                    tool_choice="auto",
                    temperature=0.1,
                    max_tokens=2048,
                )
                if self.enable_thinking and self.model.startswith("claude"):
                    # Extended thinking requires temperature=1 and no tool_choice override
                    call_kwargs["temperature"] = 1
                    call_kwargs["extra_body"] = {
                        "thinking": {"type": "enabled", "budget_tokens": 8000}
                    }
                resp = self.client.chat.completions.create(**call_kwargs)
            except Exception as exc:
                yield {"type": "error", "message": f"LLM 调用失败: {exc}"}
                yield {"type": "done"}
                return

            # Emit token usage after every LLM call
            if resp.usage:
                yield {
                    "type": "usage",
                    "prompt_tokens": resp.usage.prompt_tokens,
                    "completion_tokens": resp.usage.completion_tokens,
                    "total_tokens": resp.usage.total_tokens,
                }

            choice = resp.choices[0]
            msg = choice.message

            if choice.finish_reason == "tool_calls" and msg.tool_calls:
                # Append assistant turn with tool calls.
                # DeepSeek thinking mode requires reasoning_content to be echoed back.
                asst_entry: Dict[str, Any] = {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
                reasoning = getattr(msg, "reasoning_content", None)
                if reasoning:
                    asst_entry["reasoning_content"] = reasoning
                    all_reasoning.append(reasoning)
                messages.append(asst_entry)

                for tc in msg.tool_calls:
                    name = tc.function.name
                    try:
                        args: Dict[str, Any] = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    display_map = {
                        "get_schema":            "读取数据结构...",
                        "create_analysis_table": f"提取字段 → {args.get('table_name', 'analysis_data')}...",
                        "query_data":            f"执行查询: {args.get('sql', '')[:60]}...",
                        "run_analysis":          f"运行分析: {args.get('analysis_name', '?')} · 目标列: {args.get('target_column', '?')}...",
                        "generate_chart":        f"生成 {args.get('chart_type', '?')} 图表...",
                    }
                    yield {
                        "type": "tool_start",
                        "tool": name,
                        "display": display_map.get(name, name),
                    }

                    try:
                        if name == "get_schema":
                            tool_result = self._tool_get_schema()
                        elif name == "create_analysis_table":
                            tool_result = self._tool_create_analysis_table(
                                sql=args.get("sql", ""),
                                table_name=args.get("table_name", "analysis_data"),
                            )
                        elif name == "query_data":
                            tool_result = self._tool_query_data(args.get("sql", ""))
                        elif name == "run_analysis":
                            tool_result = self._tool_run_analysis(
                                analysis_name=args.get("analysis_name", ""),
                                sql=args.get("sql", ""),
                                target_column=args.get("target_column", ""),
                                groupby_column=args.get("groupby_column", ""),
                                n_deciles=int(args.get("n_deciles", 10)),
                            )
                        elif name == "generate_chart":
                            chart = self._tool_generate_chart(
                                chart_type=args.get("chart_type", "Bar_Chart"),
                                sql=args.get("sql", ""),
                                field_mapping=args.get("field_mapping", {}),
                                title=args.get("title", ""),
                            )
                            if "html" in chart:
                                pending_charts.append(chart["html"])
                                # Signal that a chart placeholder should be inserted
                                yield {
                                    "type": "chart_placeholder",
                                    "index": len(pending_charts) - 1,
                                }
                                tool_result = (
                                    f"Chart generated ({args.get('chart_type')}). "
                                    "It is displayed to the user."
                                )
                            else:
                                tool_result = f"Chart failed: {chart.get('error', 'unknown')}"
                        else:
                            tool_result = f"Unknown tool: {name}"
                    except Exception as exc:
                        tool_result = f"工具执行错误 [{name}]: {exc}"

                    messages.append(
                        {"role": "tool", "tool_call_id": tc.id, "content": str(tool_result)}
                    )

            else:
                # Collect final-response reasoning and emit all accumulated reasoning
                reasoning = getattr(msg, "reasoning_content", None)
                if reasoning:
                    all_reasoning.append(reasoning)
                if all_reasoning:
                    yield {"type": "reasoning", "content": "\n\n---\n\n".join(all_reasoning)}

                # Final text response — emit any charts first
                for html in pending_charts:
                    yield {"type": "chart_html", "html": html}

                yield {"type": "text", "content": msg.content or ""}
                yield {"type": "done"}
                return

        yield {
            "type": "text",
            "content": "分析完成（已达到最大工具调用次数）。Analysis complete (max iterations reached).",
        }
        yield {"type": "done"}
