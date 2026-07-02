# -*- coding: utf-8 -*-
"""LLM tool JSON schemas (the ``tools`` list passed to the API).

Depends on module-level globals from prompts.py — import order matters:
  prompts.py  →  tools/schemas.py  →  agent.py
"""
import logging

log = logging.getLogger(__name__)
from ..prompts import _ANALYZE_GUIDE, _CHART_IDS  # _CHART_IDS built from chart_selector._CHARTS

TOOL_SCHEMA_VERSION = "1.1"

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "workspace_status",
            "description": (
                "Return a bounded metadata summary for the always-available system "
                "Workspace roots uploads, outputs and mcp, plus the optional mounted "
                "user workspace. The summary contains counts and at most five recent "
                "files per root; it never reads file contents. System-root files are "
                "metadata only: page with workspace_glob and inspect only relevant "
                "files. Files in a mounted user workspace are already registered as "
                "data-source tables and should be queried through get_schema/query_data."
                "\n\nCall this FIRST whenever the user mentions local files, a project "
                "folder, or asks you to read/analyse files they have on disk. "
                "NEVER reply 'I cannot browse' or 'please upload' without calling this tool. "
                "The system roots remain available even when no user folder is mounted."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_knowledge",
            "description": (
                "Search the business knowledge base for metric definitions, business rules, "
                "context notes, and retrieved document chunks from the local RAG index. "
                "ALWAYS call this at the start of any data analysis request BEFORE "
                "writing SQL — the knowledge base may contain canonical definitions, "
                "pre-built SQL templates, business rules, or source-document context "
                "that must be followed. Search by the user's exact keywords (Chinese "
                "or English). If results are returned, follow the sql_template and "
                "definition exactly and use document chunks as grounded context. "
                "Only skip this call for schema exploration (get_schema) or "
                "purely structural questions that have no business metric involved."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": (
                            "The user's question, metric name, or business concept to look up. "
                            "Use the original user wording — e.g. '日活', 'DAU', '次日留存率', "
                            "'客户流失', 'LTV', '复购率'. Both Chinese and English are supported."
                        ),
                    }
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_schema",
            "description": (
                "Get the schema of the connected data source — table names and columns. "
                "For large databases (>20 tables) only the table inventory and the first "
                "20 tables' columns are returned in full; use get_table_detail for any "
                "specific table you need to query. Always call this first when the user "
                "asks about data you haven't seen yet. The schema is always fetched fresh "
                "from the database — never cached between turns."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_table_detail",
            "description": (
                "Get the full column list and row count for a single table. "
                "Use this when get_schema returned only a summary for a table "
                "(i.e. the database has more than 20 tables) and you need to know "
                "the exact column names before writing a SQL query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "Exact table name as returned by get_schema.",
                    }
                },
                "required": ["table_name"],
            },
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
            "name": "delete_analysis_tables",
            "description": (
                "Delete one or more derived/analysis tables from the connected data source. "
                "Use this only after the user explicitly confirms the exact table names. "
                "The tool refuses to delete raw/source tables or tables that cannot be "
                "proven to be analysis/derived objects."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "table_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Exact analysis table names to delete.",
                        "minItems": 1,
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "Must be true after confirming the exact table names.",
                    },
                },
                "required": ["table_names", "confirm"],
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
            "name": "select_chart",
            "description": (
                "Look up the chart registry to find the best-matching chart type for the user's request. "
                "ALWAYS call this BEFORE generate_chart whenever the user asks for a visualization "
                "and you are not 100% certain which chart_id and field_mapping keys to use. "
                "Returns the top matching charts with their exact required_roles, data_format, "
                "and constraints — use this information to construct the correct field_mapping for generate_chart. "
                "Do NOT skip this step and guess the chart type directly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "user_intent": {
                        "type": "string",
                        "description": (
                            "A concise description of what the user wants to visualize. "
                            "Include chart type hints if the user mentioned one, the business question, "
                            "and any dimension/metric keywords. "
                            "Examples: '各月销售额趋势', '产品类别占比饼图', '两个时间点的排名变化', "
                            "'地区销售热力图', 'KPI达成率对比'."
                        ),
                    },
                    "available_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Column names available in the data (from get_schema or query_data). "
                            "Helps the selector match required_roles to actual columns. "
                            "Leave empty if schema is unknown."
                        ),
                    },
                },
                "required": ["user_intent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_chart",
            "description": (
                "Create a data visualization chart displayed to the user. "
                "IMPORTANT: Call select_chart first to confirm the correct chart_id and field_mapping keys. "
                "Use the exact required_roles returned by select_chart as your field_mapping keys."
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
                            '{"label": "product", "value": "sales"}. '
                            "Values must be SQL result column names (or a list of column names). "
                            "Do not put colors, display-name objects, or raw data arrays in field_mapping."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": (
                            "Required human-readable chart title shown in the tool "
                            "timeline and above the visualization."
                        ),
                    },
                },
                "required": ["chart_type", "sql", "field_mapping", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "profile_data",
            "description": (
                "Profile a data table: per-column missing value %, dtype, "
                "and for numeric columns: mean/std/min/max/quartiles. "
                "Also generates distribution histogram charts automatically. "
                "Call this for the /data command."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "Table to profile. Leave empty to use the first available table.",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of columns to limit profiling to.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clean_data",
            "description": (
                "Clean data in-place and store result as 'cleaned_data' table. "
                "Supports three operations:\n"
                "  fill_na   — fill NaN with zero / mean / median\n"
                "  winsorize — cap values at lower/upper percentiles\n"
                "  trimming  — remove rows outside [min_val, max_val] on one column"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "description": "One of: fill_na | winsorize | trimming",
                    },
                    "table_name": {
                        "type": "string",
                        "description": "Source table name. Leave empty to use the first available raw table.",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Columns to process (fill_na / winsorize). None = all numeric columns.",
                    },
                    "fill_method": {
                        "type": "string",
                        "description": "For fill_na: 'zero' | 'mean' | 'median'",
                    },
                    "lower_pct": {
                        "type": "number",
                        "description": "For winsorize: lower percentile, 0–100 (e.g. 1 for 1st percentile)",
                    },
                    "upper_pct": {
                        "type": "number",
                        "description": "For winsorize: upper percentile, 0–100 (e.g. 99 for 99th percentile)",
                    },
                    "trim_column": {
                        "type": "string",
                        "description": "For trimming: the column to filter on",
                    },
                    "min_val": {
                        "type": "number",
                        "description": "For trimming: minimum value to keep (inclusive)",
                    },
                    "max_val": {
                        "type": "number",
                        "description": "For trimming: maximum value to keep (inclusive)",
                    },
                    "output_table": {
                        "type": "string",
                        "description": "Name for the result table (default: 'cleaned_data')",
                    },
                },
                "required": ["operation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_excel",
            "description": (
                "Export data tables to an Excel (.xlsx) file. "
                "Call this ONLY when the user explicitly asked to export data. "
                "Each table becomes a separate sheet. "
                "Pass tables=[\"*\"] to export ALL available tables automatically — "
                "this is the default behaviour unless the user asks for specific tables."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tables": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Table names to export. "
                            "Use [\"*\"] to auto-export every table in the data source "
                            "(raw data + analysis results). "
                            "Only specify exact names if the user asked for specific tables."
                        ),
                    },
                    "filename": {
                        "type": "string",
                        "description": "Base filename without extension (optional, auto-generated if omitted).",
                    },
                },
                "required": ["tables"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_report",
            "description": (
                "Generate a Word document (.docx) analysis report. "
                "Call this ONLY when the user explicitly asked to export a report. "
                "Query the data first, then compose sections."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Report title.",
                    },
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "content": {
                                    "type": "string",
                                    "description": "Section body text (plain text or markdown-style).",
                                },
                            },
                            "required": ["heading", "content"],
                        },
                        "description": (
                            "Ordered list of report sections. Typical structure: "
                            "Executive Summary → Key Findings → Data Analysis → Recommendations."
                        ),
                    },
                },
                "required": ["title", "sections"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_excel_export",
            "description": (
                "Show the user a preview of which tables will be exported BEFORE generating the Excel file.\n"
                "ONLY call this when the /export slash command is active. NEVER call proactively.\n"
                "The frontend renders a confirmation card with Confirm / Edit / Cancel buttons."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tables": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Table names to export. Use [\"*\"] to export all available tables.",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Base filename without extension (optional).",
                    },
                    "summary": {
                        "type": "string",
                        "description": "One-sentence description of what will be exported.",
                    },
                },
                "required": ["tables"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_report_outline",
            "description": (
                "Show the user a report outline for review BEFORE generating the Word file.\n"
                "ONLY call this when the /report slash command is active. NEVER call proactively.\n"
                "The frontend renders a confirmation card with Confirm / Edit / Cancel buttons."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Report title.",
                    },
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["heading", "content"],
                        },
                        "description": "Ordered list of report sections.",
                    },
                },
                "required": ["title", "sections"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_ppt_outline",
            "description": (
                "Show the user a slide-by-slide PPT outline for review BEFORE generating the file.\n"
                "ONLY call this when the /ppt slash command is active. NEVER call proactively.\n"
                "The frontend will render an editable card with Confirm / Edit / Cancel buttons.\n"
                "Use exactly the same parameters as generate_ppt."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Overall deck title.",
                    },
                    "slides": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "layout": {"type": "string"},
                                "params": {"type": "object"},
                            },
                            "required": ["layout", "params"],
                        },
                        "description": "Ordered list of slides, each {layout, params}.",
                    },
                },
                "required": ["title", "slides"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_ppt",
            "description": (
                "Generate a professional McKinsey-style PowerPoint (.pptx) presentation.\n"
                "ONLY called automatically by the system after user confirms a propose_ppt_outline. NEVER call directly.\n\n"
                "SLIDE LAYOUTS and their params:\n"
                "  cover            title, subtitle?(str), author?(str), date?(str)\n"
                "  toc              title?='目录', items(list of [num_str, title, desc])\n"
                "  section_divider  section_label(str), title(str), subtitle?(str)\n"
                "  big_number       title, number(str), unit?(str), description?(str),\n"
                "                   detail_items?(list[str])\n"
                "  two_stat         title, stats(list of [number_str, label, is_navy_bool])\n"
                "  metric_cards     title, cards(list of [letter, card_title, desc])\n"
                "  data_table       title, headers(list[str]), rows(list[list[str]])\n"
                "  table_insight    title, headers(list[str]), rows(list[list[str]]),\n"
                "                   insights(list[str])\n"
                "  executive_summary title, headline(str),\n"
                "                   items(list of [num_str, item_title, desc])\n"
                "  two_column_text  title, columns(list of [letter, col_title,\n"
                "                   points(list[str])])\n"
                "  action_items     title, actions(list of [action_title, timeline, desc, owner])\n"
                "  donut            title, segments(list of [pct_float, color_str, label]),\n"
                "                   center_label?(str), center_sub?(str)\n"
                "  grouped_bar      title, categories(list[str]),\n"
                "                   series(list of [name, color_str]),\n"
                "                   data(list[list[num]]), max_val?(num)\n"
                "  stacked_bar      title, periods(list[str]),\n"
                "                   series(list of [name, color_str]),\n"
                "                   data(list[list[num]] — percentages 0-100)\n"
                "  timeline         title, milestones(list of [label, description])\n"
                "  closing          title, message?(str)\n\n"
                "Color string constants for color params:\n"
                "  NAVY (primary dark), ACCENT_BLUE, ACCENT_GREEN, ACCENT_ORANGE, ACCENT_RED,\n"
                "  BG_GRAY, LIGHT_BLUE, LIGHT_GREEN, LIGHT_ORANGE, LIGHT_RED, MED_GRAY"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Overall deck title (used for the filename).",
                    },
                    "slides": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "layout": {
                                    "type": "string",
                                    "description": "Slide layout name, e.g. 'cover', 'donut', 'timeline'.",
                                },
                                "params": {
                                    "type": "object",
                                    "description": "Parameters matching the chosen layout's signature.",
                                },
                            },
                            "required": ["layout", "params"],
                        },
                        "description": "Ordered list of slides. Each item: {layout, params}.",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional base filename without extension.",
                    },
                },
                "required": ["title", "slides"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_ppt_color_scheme",
            "description": (
                "Set the color scheme for ALL visuals in this session — both Plotly charts "
                "and PPT slides. Default is 'mckinsey'. "
                "Available schemes: mckinsey, bcg, bain, ey. "
                "Call this IMMEDIATELY whenever the user mentions a color preference, firm style, "
                "or brand (e.g. 'BCG green', 'Bain style', 'EY yellow', '麦肯锡蓝'). "
                "If the user never specifies, do NOT call this tool — mckinsey is already active."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scheme": {
                        "type": "string",
                        "enum": ["mckinsey", "bcg", "bain", "ey"],
                        "description": "Color scheme name.",
                    },
                },
                "required": ["scheme"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_dashboard_outline",
            "description": (
                "Show the user a dashboard outline for review BEFORE generating the dashboard.\n"
                "ONLY call this when the /dashboard slash command is active. NEVER call proactively.\n"
                "The frontend renders a confirmation card with Confirm / Edit / Cancel buttons.\n"
                "Do NOT call generate_dashboard in the same turn."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Dashboard name."},
                    "widgets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "chart_type": {
                                    "type": "string",
                                    "enum": [
                                        "Bar_Chart", "Line_Chart", "Pie_Chart",
                                        "Scatter_Plot", "Area_Chart", "Grouped_Bar_Chart",
                                        "Heatmap", "Stacked_Bar_Chart",
                                    ],
                                },
                                "sql": {"type": "string", "description": "Valid SQL against real tables."},
                                "field_mapping": {
                                    "type": "object",
                                    "description": "Maps chart axes/roles to column names.",
                                },
                                "options": {"type": "object"},
                                "grid": {
                                    "type": "object",
                                    "description": "{x, y, w, h} grid position (w/h in grid units).",
                                },
                            },
                            "required": ["title", "chart_type", "sql", "field_mapping"],
                        },
                        "description": "List of widget specs (2–6 widgets recommended).",
                    },
                },
                "required": ["name", "widgets"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_dashboard",
            "description": (
                "Generate and save an interactive dashboard with multiple chart widgets.\n"
                "Only call this after the user confirms a proposed outline via the UI button,\n"
                "or when the dashboard_confirm command is active.\n"
                "Each widget executes SQL against the connected data source and renders a chart."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Dashboard name."},
                    "widgets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "chart_type": {
                                    "type": "string",
                                    "enum": [
                                        "Bar_Chart", "Line_Chart", "Pie_Chart",
                                        "Scatter_Plot", "Area_Chart", "Grouped_Bar_Chart",
                                        "Heatmap", "Stacked_Bar_Chart", "KPI_Card",
                                    ],
                                    "description": (
                                        "Chart type. Use KPI_Card for scalar metric display "
                                        "(SQL must return 1 row: col1=value, col2=subtitle (opt), col3=trend% (opt))."
                                    ),
                                },
                                "sql": {"type": "string"},
                                "field_mapping": {"type": "object"},
                                "options": {"type": "object"},
                                "grid": {
                                    "type": "object",
                                    "description": "{x, y, w, h} grid position. KPI_Card recommended: w=3, h=2.",
                                },
                            },
                            "required": ["title", "chart_type", "sql", "field_mapping"],
                        },
                    },
                    "color_scheme": {
                        "type": "string",
                        "enum": ["mckinsey", "bcg", "bain", "ey"],
                        "description": "Color scheme (defaults to current session scheme).",
                    },
                },
                "required": ["name", "widgets"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "Ask the user a clarifying question when their request is ambiguous or "
                "underspecified, especially open-ended requests like '帮我分析一下' / "
                "'analyse this' / '看看数据'. Present a short question and 2-6 predefined "
                "options the user can click as chips — this is the ONLY sanctioned way to "
                "offer analysis direction choices to the user. Always include a catch-all "
                "option so the user can type a custom answer. NEVER enumerate 2+ analysis "
                "directions as plain text bullets for the user to pick from — convert them "
                "into an ask_user call instead. Example: user uploads data and says '帮我"
                "分析一下' -> call ask_user with options like 盈利分析 / 结构分析 / 趋势"
                "分析 / 用户分析 rather than replying with a text menu."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The clarifying question to show the user (one sentence, ≤120 chars).",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "2–6 predefined answer options. Keep each option short (≤40 chars). "
                            "Do NOT include an 'Other' option — it is added automatically."
                        ),
                        "minItems": 2,
                        "maxItems": 6,
                    },
                    "multi_select": {
                        "type": "boolean",
                        "description": (
                            "If true, the user may select multiple options before submitting. "
                            "Use for questions like 'which dimensions to include'. "
                            "Defaults to false (single-select)."
                        ),
                    },
                },
                "required": ["question", "options"],
            },
        },
    },
]


HOOKS_AUTOMATION_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "browse_webpage",
            "description": (
                "Read a public HTTP(S) webpage and return bounded text for configuration tasks. "
                "Use this when the user sends a URL/link/API documentation/webhook documentation "
                "and asks you to configure something from it. Local/private network URLs are blocked. "
                "For Hooks auto-configuration, first call browse_webpage on the user's link, then "
                "derive a minimal Hooks JSON config, then call configure_hooks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Absolute public http:// or https:// URL to read.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "minimum": 1000,
                        "maximum": 30000,
                        "description": "Maximum readable characters to return. Default 12000.",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_hooks",
            "description": (
                "Validate and save a Hooks configuration. Use this after reading user-provided "
                "documentation and deriving the hook JSON. The settings object must match the "
                "Hooks API shape: {enabled, allow_command_hooks, hooks:[{id,event,if,reject,once,async,action}]}."
                " prompt/http actions are always supported. command actions are supported only when "
                "the user explicitly asks for a local Python script hook and confirm_command_hooks=true; "
                "the command must be a simple Python .py script invocation, not arbitrary shell. "
                "Prefer merge=true to preserve existing hooks. "
                "After calling this tool, summarize what was configured and mention that it applies from "
                "the next turn/tool call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "settings": {
                        "type": "object",
                        "description": (
                            "Full or partial Hooks settings JSON. Example hook: "
                            "{id:'notify_done', event:'turn_end', async:true, "
                            "action:{type:'http', method:'POST', url:'https://...', "
                            "body:{event:'$EVENT', message:'$MESSAGE', answer:'$FINAL_ANSWER'}}}"
                        ),
                    },
                    "merge": {
                        "type": "boolean",
                        "description": "If true, update/add hooks by id while preserving existing hooks. Default true.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short user-facing reason for the configuration.",
                    },
                    "confirm_command_hooks": {
                        "type": "boolean",
                        "description": (
                            "Set true only when the user explicitly asked to configure a command hook. "
                            "This enables allow_command_hooks and permits simple Python script commands "
                            "such as python path/to/hook.py. Never set true for shell snippets."
                        ),
                    },
                },
                "required": ["settings"],
            },
        },
    },
]

AGENT_TOOLS.extend(HOOKS_AUTOMATION_TOOL_SCHEMAS)


WORKSPACE_TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "workspace_glob", "description": "Page through file metadata. When a user workspace is mounted, omit path to search that mounted directory first; returned user/... paths can be passed unchanged to read, move, or delete tools. Use explicit workspace://uploads, workspace://outputs, or workspace://mcp only for system roots. Contents are never read by this tool.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string", "description": "Optional root/base. Omit for the mounted user workspace; otherwise use workspace://user, workspace://uploads, workspace://outputs, or workspace://mcp."}, "max_results": {"type": "integer", "minimum": 1, "maximum": 100}, "cursor": {"type": "integer", "minimum": 0}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "workspace_grep", "description": "Regex-search allowlisted UTF-8 text files on demand. At most 50 matches and 200 candidate files are examined; dependency, cache and build directories are skipped.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}, "include": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 50}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "workspace_read_file", "description": "Read one allowlisted UTF-8 text, DOCX, or spreadsheet file. Excel/XLS/XLSX/ODS files return a bounded worksheet preview (up to 256 MiB file size); use sheet_name, offset, and next_offset to page through rows. Text/DOCX files allow up to 20 MiB. Output remains capped at 400 lines and 12000 characters. Existing files must be read before write/edit.", "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 400}, "sheet_name": {"type": "string", "description": "Optional worksheet name for spreadsheet files; defaults to the first sheet."}}, "required": ["file_path"]}}},
    {"type": "function", "function": {"name": "workspace_write_file", "description": "Write UTF-8 content up to 20 MiB to workspace://outputs or an explicitly mounted user workspace. Existing files must be read first; uploads, mcp and sensitive/internal paths are read-only.", "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}, "content": {"type": "string"}}, "required": ["file_path", "content"]}}},
    {"type": "function", "function": {"name": "workspace_edit_file", "description": "Replace one unique exact string in a previously-read file under outputs or the mounted user workspace. System uploads and mcp roots are read-only.", "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["file_path", "old_string", "new_string"]}}},
    {"type": "function", "function": {"name": "workspace_delete_file", "description": "Delete exactly one file from workspace://outputs or a writable mounted user workspace. Recursive/directory deletion is forbidden and confirm=true is always required.", "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}, "confirm": {"type": "boolean", "description": "Must be true after confirming the exact file path to delete."}}, "required": ["file_path", "confirm"]}}},
    {"type": "function", "function": {"name": "workspace_move_file", "description": "Move or rename exactly one file within writable workspace roots. Directory moves are forbidden; replacing an existing file requires confirm_overwrite=true.", "parameters": {"type": "object", "properties": {"source_path": {"type": "string"}, "destination_path": {"type": "string"}, "confirm_overwrite": {"type": "boolean"}}, "required": ["source_path", "destination_path"]}}},
    {"type": "function", "function": {"name": "workspace_bash", "description": "Run a restricted Bash-like command in the mounted workspace. Allowed commands: pwd, ls/dir, cat, rg, sha256sum, read-only git status/log/diff, python -m compileall, rm/del, and mv/move/ren. No pipes, redirection, chaining, substitutions, recursive deletion, arbitrary executables, or shell=True. rm and overwrite moves require confirm=true.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer", "minimum": 1, "maximum": 120}, "confirm": {"type": "boolean", "description": "Required for rm/del and for overwriting an existing mv destination."}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "workspace_command", "description": "Run a fixed shell-free operation: checksum, json_validate, git_status, git_diff, git_log, or python_compile. Arbitrary commands are impossible.", "parameters": {"type": "object", "properties": {"operation": {"type": "string", "enum": ["checksum", "json_validate", "git_status", "git_diff", "git_log", "python_compile"]}, "path": {"type": "string"}, "timeout": {"type": "integer", "minimum": 1, "maximum": 120}}, "required": ["operation"]}}},
    {"type": "function", "function": {"name": "structured_output", "description": "Return machine-readable output and optionally require object fields. Reserved for structured/coordinator workflows.", "parameters": {"type": "object", "properties": {"output": {}, "required_fields": {"type": "array", "items": {"type": "string"}}}, "required": ["output"]}}},
    {"type": "function", "function": {"name": "load_analysis_skill", "description": "Load a named project analysis skill and return its full SOP body.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
]

AGENT_TOOLS.extend(WORKSPACE_TOOL_SCHEMAS)

TASK_TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "task_create", "description": "Create a persistent task in the mounted workspace task board, optionally with dependencies.", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "description": {"type": "string"}, "assignee": {"type": "string"}, "blocks": {"type": "array", "items": {"type": "string"}}, "blocked_by": {"type": "array", "items": {"type": "string"}}}, "required": ["title"]}}},
    {"type": "function", "function": {"name": "task_get", "description": "Get one task from the mounted workspace task board.", "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}}},
    {"type": "function", "function": {"name": "task_list", "description": "List persistent tasks in the mounted workspace, optionally filtered by status or assignee.", "parameters": {"type": "object", "properties": {"status": {"type": "string", "enum": ["pending", "in_progress", "completed", "blocked"]}, "assignee": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "task_update", "description": "Update a workspace task status, assignee, description, or dependencies.", "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "blocked"]}, "assignee": {"type": "string"}, "description": {"type": "string"}, "add_blocks": {"type": "array", "items": {"type": "string"}}, "add_blocked_by": {"type": "array", "items": {"type": "string"}}}, "required": ["task_id"]}}},
]

AGENT_TOOLS.extend(TASK_TOOL_SCHEMAS)

TEAM_TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "team_create", "description": "Create a persistent analyst team definition in the mounted workspace. Members are roles, not unrestricted OS processes. Team and member names may be Chinese or English. Pass members as an array of objects, not a string.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "description": {"type": "string"}, "members": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}, "instructions": {"type": "string"}}, "required": ["name"]}}}, "required": ["name", "members"]}}},
    {"type": "function", "function": {"name": "team_delete", "description": "Delete a team definition and mailbox from the mounted workspace.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "team_list", "description": "List persistent analyst teams in the mounted workspace with member statuses.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "team_status", "description": "Get one workspace team status, member inbox counts, and recent mailbox messages.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "send_message", "description": "Store a message for a named member in a mounted-workspace team mailbox. Use recipient='*' to broadcast to all members.", "parameters": {"type": "object", "properties": {"team_name": {"type": "string"}, "recipient": {"type": "string"}, "message": {"type": "string"}}, "required": ["team_name", "recipient", "message"]}}},
    {"type": "function", "function": {"name": "agent_delegate", "description": "Delegate a bounded reasoning task to a named workspace team member. The delegated member has a limited read-only toolset for schema inspection, data queries, knowledge search, and workspace reading; it consumes unread mailbox messages and writes the result back to the team leader mailbox.", "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}, "description": {"type": "string"}, "team_name": {"type": "string"}, "member_name": {"type": "string"}}, "required": ["prompt"]}}},
    {"type": "function", "function": {"name": "team_delegate", "description": "Delegate multiple independent bounded reasoning tasks to named team members in parallel. Prefer this over repeated agent_delegate calls when Teams are enabled. Keep each member prompt focused and concise. Each teammate has a limited read-only toolset for schema inspection, data queries, knowledge search, and workspace reading; it consumes unread mailbox messages and writes the result back to the leader mailbox.", "parameters": {"type": "object", "properties": {"team_name": {"type": "string"}, "assignments": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "object", "properties": {"member_name": {"type": "string"}, "prompt": {"type": "string"}, "description": {"type": "string"}}, "required": ["member_name", "prompt"]}}, "timeout_seconds": {"type": "integer", "minimum": 10, "maximum": 300, "description": "Per-member timeout. Default 300."}, "max_concurrency": {"type": "integer", "minimum": 1, "maximum": 8, "description": "Parallel worker count. Default is assignments count capped at 6."}, "result_max_tokens": {"type": "integer", "minimum": 400, "maximum": 2500, "description": "Per-member output cap. Default 1200 for speed."}}, "required": ["team_name", "assignments"]}}},
]

AGENT_TOOLS.extend(TEAM_TOOL_SCHEMAS)

CONTROL_TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "read_tool_result", "description": "Read a recoverable tool-result Artifact that belongs to this session. Use query for matching snippets or offset/limit for bounded character pagination. Never guess missing Artifact content.", "parameters": {"type": "object", "properties": {"artifact_id": {"type": "string", "description": "Opaque tr_... id from a prior tool result."}, "offset": {"type": "integer", "minimum": 0, "default": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 4000, "default": 4000}, "query": {"type": "string", "description": "Optional case-insensitive text to find with bounded context."}}, "required": ["artifact_id"]}}},
    {"type": "function", "function": {"name": "search_mcp_tools", "description": "Search the compact catalog of currently connected MCP tools. Returns up to 5 names and summaries without loading every full parameter schema. Use when the user needs an external capability that is not already exposed.", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Capability, service, or exact MCP tool name to find."}, "server": {"type": "string", "description": "Optional exact MCP server id."}, "limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 5}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "plan_complete", "description": "Return a completed structured plan in coordinator workflows.", "parameters": {"type": "object", "properties": {"summary": {"type": "string"}, "steps": {"type": "array", "items": {"type": "object"}}}, "required": ["summary", "steps"]}}},
]

AGENT_TOOLS.extend(CONTROL_TOOL_SCHEMAS)


TOOL_SCHEMA_VERSIONS = {
    (tool.get("function") or {}).get("name"): TOOL_SCHEMA_VERSION
    for tool in AGENT_TOOLS
    if (tool.get("function") or {}).get("name")
}


def get_tool_schema_version(tool_name: str) -> str:
    return TOOL_SCHEMA_VERSIONS.get(tool_name, TOOL_SCHEMA_VERSION)


def get_tools_with_mcp(mcp_manager=None, selected_mcp_tools=None) -> list:
    if mcp_manager is None:
        return AGENT_TOOLS
    try:
        mcp_schemas = mcp_manager.get_all_openai_schemas()
    except Exception as e:
        log.warning("[schemas] MCP schema fetch failed: %s", e)
        mcp_schemas = []
    if selected_mcp_tools is not None:
        from agent.mcp_discovery import select_mcp_schemas
        mcp_schemas = select_mcp_schemas(
            mcp_schemas, selected_mcp_tools, limit=5,
        )
    return AGENT_TOOLS + mcp_schemas


# Fail fast during startup/tests when a schema is added without runtime policy
# metadata (or vice versa). MCP tools are intentionally validated separately.
from .registry import BUILTIN_TOOL_REGISTRY

BUILTIN_TOOL_REGISTRY.validate_schema_names(AGENT_TOOLS)
