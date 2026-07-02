# -*- coding: utf-8 -*-
"""System prompt, command hints, and guide builders.

This module is imported first (no deps on other agent sub-modules) so that
tools/schemas.py can import _ANALYZE_GUIDE and _CHART_IDS from here.
"""
import logging
log = logging.getLogger(__name__)
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict
from infrastructure.paths import resource_root

_PROJ_ROOT  = str(resource_root())
_CHARTS_GEN = os.path.join(_PROJ_ROOT, "Function", "Charts_generation")
_PPT_PATH   = os.path.join(_PROJ_ROOT, "Function", "Output")

# Ensure runtime paths are available for every module that imports from agent/
sys.path.insert(0, _PROJ_ROOT)
sys.path.insert(0, _CHARTS_GEN)
if _PPT_PATH not in sys.path:
    sys.path.insert(0, _PPT_PATH)


# ── Guide builders ────────────────────────────────────────────────────────────
def _build_analyze_guide() -> str:
    try:
        from Function.Analyze.registry import build_agent_desc
        return build_agent_desc()
    except Exception as e:
        log.warning("[prompts] analyze guide build failed: %s", e)
        return "  Data_Decile_Analysis — 十分位分析（Decile Analysis）"


def _build_chart_ids() -> str:
    """Return a comma-separated list of all chart_ids from the embedded selector registry."""
    try:
        from LLM.chart_selector import _CHARTS
        return ", ".join(c["chart_id"] for c in _CHARTS)
    except Exception as e:
        log.warning("[prompts] chart ids build failed: %s", e)
        return (
            "Bar_Chart, Line_Chart, Pie_Chart, Scatter_Plot, Area_Chart, "
            "Heatmap, Waterfall, Treemap, Sunburst_Diagram, Nightingale_Chart"
        )


_ANALYZE_GUIDE = _build_analyze_guide()
_CHART_IDS = _build_chart_ids()

# ── Slash-command → system-hint mapping ──────────────────────────────────────

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
    "decile": (
        "The user issued the /decile command for Data_Decile_Analysis (十分位分析).\n"
        "Workflow:\n"
        "1. Call get_schema ONCE to understand the data.\n"
        "2. Choose the most relevant numeric target_column "
        "(revenue / amount / score — whatever the user mentioned, or the most business-relevant).\n"
        "3. Optionally set groupby_column if the user wants a category breakdown.\n"
        "4. Call run_analysis(analysis_name='Data_Decile_Analysis', sql=..., target_column=...).\n"
        "   SQL: SELECT <target_col>[, <groupby_col>] FROM <table>\n"
        "5. Generate BOTH charts from analysis_result:\n"
        "   a) Bar_Chart: x=decile, y=sum  — value distribution by bucket\n"
        "   b) Line_Chart: x=decile, y=cumulative_pct  — Pareto cumulative curve\n"
        "6. Conclude with a 2-4 sentence business interpretation."
    ),
    "tree": (
        "The user issued the /tree command for Decision_Tree analysis.\n"
        "Workflow:\n"
        "1. Call get_schema ONCE.\n"
        "2. target_column = the classification label column.\n"
        "3. groupby_column = algorithm choice: 'ID3' | 'C4.5' | 'CART' "
        "(default 'C4.5'; infer from user message if mentioned).\n"
        "4. n_deciles = max_depth (0 = unlimited; default 0).\n"
        "5. Call run_analysis(analysis_name='Decision_Tree', sql=..., target_column=..., "
        "groupby_column=<algorithm>).\n"
        "   SQL: SELECT <feature_cols>, <target_col> FROM <table>\n"
        "6. Generate ALL THREE charts:\n"
        "   a) Bar_Chart(analysis_result): x=feature, y=importance_pct  — feature importance\n"
        "   b) Heatmap(analysis_breakdown): x=predicted, y=actual, z=count  — confusion matrix\n"
        "   c) Line_Chart(analysis_roc): x=fpr, y=tpr, series=class  — ROC curve\n"
        "      Include AUC values in the chart title.\n"
        "7. Conclude with a 2-4 sentence business interpretation."
    ),
    "kmeans": (
        "The user issued the /kmeans command for K-Means clustering.\n"
        "Workflow:\n"
        "1. Call get_schema ONCE.\n"
        "2. SELECT the numeric feature columns to cluster on.\n"
        "3. n_deciles = K (number of clusters; default 3, or as specified by the user).\n"
        "4. groupby_column = optional categorical label column for cluster purity analysis.\n"
        "5. Call run_analysis(analysis_name='K_Means', sql=..., target_column=<main_numeric_col>, "
        "n_deciles=<K>).\n"
        "   SQL: SELECT <numeric_feature_cols>[, <label_col>] FROM <table>\n"
        "6. Generate ALL THREE charts:\n"
        "   a) Bar_Chart(analysis_result): x=cluster, y=count  — cluster sizes\n"
        "   b) Scatter_Plot(analysis_breakdown): x=<feat1>, y=<feat2>, color=cluster\n"
        "      — pick the 2 most business-relevant numeric columns for x/y\n"
        "   c) Line_Chart(analysis_elbow): x=k, y=inertia  — elbow curve\n"
        "7. A bonus table 'cluster_labels' (all original columns + cluster) is auto-created:\n"
        "   SELECT cluster, AVG(revenue) FROM cluster_labels GROUP BY cluster\n"
        "8. Conclude with a 2-4 sentence business interpretation."
    ),
    "screening": (
        "The user issued the /screening command for Univariate Screening Regression.\n"
        "Workflow:\n"
        "1. Call get_schema ONCE to identify the target column and all numeric candidates.\n"
        "2. target_column = the dependent variable the user wants to explain (e.g. 'rd1').\n"
        "3. groupby_column = significance threshold as a string (e.g. '0.05'; default '0.05').\n"
        "4. Call run_analysis(analysis_name='Univariate_Screening', sql=..., target_column=...).\n"
        "   SQL: SELECT <target_col>, <all_candidate_cols> FROM <table>\n"
        "   Include ALL numeric columns the user wants screened — the model will skip non-numeric.\n"
        "5. Generate TWO charts from analysis_result (all variables, sorted by p-value):\n"
        "   a) Bar_Chart: x=变量, y=R²   — explained variance ranking\n"
        "   b) Bar_Chart: x=变量, y=系数  — coefficient direction & magnitude\n"
        "   Use analysis_breakdown (significant only) as the SQL source for cleaner charts.\n"
        "6. Display analysis_metrics as a formatted table.\n"
        "7. Conclude with: which variables are significant, direction of effect, "
        "and recommendations for which to include in a multivariate model.\n"
        "⚠️ NEVER output regression numbers before run_analysis returns — "
        "all coefficients and p-values MUST come from actual tool results."
    ),
    "regression": (
        "The user issued the /regression command for Linear Regression analysis.\n"
        "Workflow:\n"
        "1. Call get_schema ONCE.\n"
        "2. target_column = the continuous numeric column to predict.\n"
        "3. groupby_column = ridge regularization lambda as a string (e.g. '0' for plain OLS, '0.1' for ridge; default '0').\n"
        "4. n_deciles = polynomial degree (1=linear, 2=quadratic, etc.; default 1; pass 0 for default).\n"
        "5. Call run_analysis(analysis_name='Regression', sql=..., target_column=..., "
        "groupby_column=<lambda_str>, n_deciles=<degree>).\n"
        "   SQL: SELECT <feature_cols>, <target_col> FROM <table>\n"
        "   Include numeric and/or categorical feature columns; preprocessing is automatic.\n"
        "6. Generate ALL THREE charts:\n"
        "   a) Bar_Chart(analysis_result): x=feature, y=coefficient  — regression coefficients\n"
        "      Exclude the (intercept) row for cleaner visuals: WHERE feature != '(intercept)'\n"
        "   b) Scatter_Plot(analysis_breakdown): x=y_pred, y=std_residual  — residual diagnostics\n"
        "      A good model has residuals randomly scattered around 0; patterns suggest non-linearity.\n"
        "   c) Display analysis_metrics table (R²/RMSE/MAE for train vs test) as a formatted table.\n"
        "7. Conclude with a 2-4 sentence business interpretation covering model fit (R²), "
        "top significant predictors (p<0.05), and any multicollinearity warnings (VIF>10)."
    ),
    "arima": (
        "The user issued the /arima command for ARIMA time series forecasting.\n"
        "⚠️ CRITICAL: You MUST call run_analysis with analysis_name='Time_Series_ARIMA'. "
        "Do NOT use Prophet, SARIMA, or any other model. ARIMA only.\n"
        "Workflow:\n"
        "1. Call get_schema ONCE. Identify the time column and the numeric target column.\n"
        "2. groupby_column = the time column name (e.g. 'date'), OR a manual order string 'p,d,q' (e.g. '2,1,1').\n"
        "   If not sure, leave groupby_column empty — the model will auto-detect the time column and select orders via AIC.\n"
        "3. n_deciles = forecast horizon (number of future steps; default 12).\n"
        "4. Call run_analysis(analysis_name='Time_Series_ARIMA', sql=..., target_column=..., "
        "groupby_column=<time_col_or_order>, n_deciles=<steps>).\n"
        "   SQL: SELECT <time_col>, <value_col> FROM <table> ORDER BY <time_col>\n"
        "5. Generate ALL THREE outputs:\n"
        "   a) Line_Chart(analysis_result): x=ds — TWO y-series in ONE chart:\n"
        "      SQL: SELECT ds, y_actual, y_pred FROM analysis_result\n"
        "      field_mapping: {\"x\":\"ds\",\"y\":[\"y_actual\",\"y_pred\"]}\n"
        "      ⚠️ Do NOT filter by segment. Do NOT split into two separate queries.\n"
        "      The chart will show historical actuals (y_actual) and the full forecast line (y_pred) together.\n"
        "   b) Scatter_Plot(analysis_breakdown): x=row_num, y=std_residual — residual diagnostics\n"
        "      analysis_breakdown has columns: ds, row_num (integer), residual, std_residual.\n"
        "      Use row_num as x — Scatter_Plot requires a numeric x column; ds is a string and will fail.\n"
        "      Randomly scattered residuals around 0 indicate a good fit.\n"
        "   c) Display analysis_metrics table (AIC/BIC/MAE/RMSE) as a formatted table.\n"
        "6. Conclude with a 2-4 sentence interpretation: trend direction, forecast confidence, and model order chosen."
    ),
    "sarima": (
        "The user issued the /sarima command for SARIMA seasonal time series forecasting.\n"
        "⚠️ CRITICAL: You MUST call run_analysis with analysis_name='Time_Series_SARIMA'. "
        "Do NOT use ARIMA, Prophet, or any other model. SARIMA only.\n"
        "Workflow:\n"
        "1. Call get_schema ONCE. Identify the time column and numeric target column.\n"
        "2. groupby_column = time column name, OR a numeric string for the seasonal period (e.g. '12' for monthly, '4' for quarterly, '7' for daily-weekly).\n"
        "   Leave empty for automatic detection.\n"
        "3. n_deciles = forecast horizon (default 12).\n"
        "4. Call run_analysis(analysis_name='Time_Series_SARIMA', sql=..., target_column=..., "
        "groupby_column=<time_col_or_period>, n_deciles=<steps>).\n"
        "   SQL: SELECT <time_col>, <value_col> FROM <table> ORDER BY <time_col>\n"
        "5. Generate ALL THREE outputs:\n"
        "   a) Line_Chart(analysis_result): x=ds — TWO y-series in ONE chart:\n"
        "      SQL: SELECT ds, y_actual, y_pred FROM analysis_result\n"
        "      field_mapping: {\"x\":\"ds\",\"y\":[\"y_actual\",\"y_pred\"]}\n"
        "      ⚠️ Do NOT filter by segment. Do NOT split into two separate queries.\n"
        "   b) Line_Chart(analysis_breakdown): x=ds, y=trend — trend component\n"
        "      Also plot seasonal column to visualize seasonality.\n"
        "   c) Display analysis_metrics table (AIC/BIC/MAE/RMSE/seasonal period) as a table.\n"
        "6. Conclude with trend direction, detected seasonality pattern, and forecast summary."
    ),
    "var": (
        "The user issued the /var command for VAR (Vector Autoregression) multivariate forecasting.\n"
        "Workflow:\n"
        "1. Call get_schema ONCE. Identify the time column and at least 2 numeric columns.\n"
        "2. target_column = the primary variable to forecast.\n"
        "3. groupby_column = time column name; OR comma-separated variable names to include (e.g. 'sales,cost,profit').\n"
        "   If groupby_column contains commas, those columns are used as the VAR variables.\n"
        "4. n_deciles = forecast horizon (default 6).\n"
        "5. Call run_analysis(analysis_name='Time_Series_VAR', sql=..., target_column=..., "
        "groupby_column=<time_col_or_cols>, n_deciles=<steps>).\n"
        "   SQL: SELECT <time_col>, <col1>, <col2>[, <col3>...] FROM <table> ORDER BY <time_col>\n"
        "6. Generate ALL THREE outputs:\n"
        "   a) Line_Chart(analysis_result): x=ds, y=<target>_pred — primary variable forecast\n"
        "   b) Heatmap(analysis_breakdown): x=effect, y=cause, z=f_stat — Granger causality heatmap\n"
        "      Highlight significant cells (p_value < 0.05).\n"
        "   c) Display analysis_metrics table (VAR lag, AIC/BIC, per-variable MAE) as a table.\n"
        "7. Conclude with key Granger causal relationships and forecast direction for the target variable."
    ),
    "prophet": (
        "The user issued the /prophet command for Prophet-style additive time series decomposition.\n"
        "⚠️ CRITICAL: You MUST call run_analysis with analysis_name='Time_Series_Prophet'. "
        "Do NOT use ARIMA, SARIMA, or any other model. Prophet only.\n"
        "Workflow:\n"
        "1. Call get_schema ONCE. Identify the time column and numeric target column.\n"
        "2. groupby_column = time column name (auto-detected if empty).\n"
        "3. n_deciles = forecast horizon (default 30, suitable for daily data).\n"
        "4. Call run_analysis(analysis_name='Time_Series_Prophet', sql=..., target_column=..., "
        "groupby_column=<time_col>, n_deciles=<steps>).\n"
        "   SQL: SELECT <time_col>, <value_col> FROM <table> ORDER BY <time_col>\n"
        "5. Generate ALL THREE outputs:\n"
        "   a) Line_Chart(analysis_result): x=ds — TWO y-series in ONE chart:\n"
        "      SQL: SELECT ds, y_actual, y_pred FROM analysis_result\n"
        "      field_mapping: {\"x\":\"ds\",\"y\":[\"y_actual\",\"y_pred\"]}\n"
        "      ⚠️ Do NOT filter by segment. Do NOT split into two separate queries.\n"
        "      The chart shows historical actuals (y_actual) overlaid with the full forecast line (y_pred).\n"
        "   b) Line_Chart(analysis_breakdown): x=ds, y=trend — pure trend line\n"
        "      If yearly column is non-zero, also plot yearly seasonality.\n"
        "   c) Display analysis_metrics table (R²/MAE/RMSE, active changepoints) as a table.\n"
        "6. Conclude with trend direction, seasonal pattern strength, and changepoint highlights."
    ),
    "gru": (
        "The user issued the /gru command for GRU (Gated Recurrent Unit) deep learning time series forecasting.\n"
        "⚠️ CRITICAL: You MUST call run_analysis with analysis_name='Time_Series_GRU'. "
        "Do NOT use Prophet, ARIMA, SARIMA, or any other model under ANY circumstances. GRU only.\n"
        "⚠️ CRITICAL: Do NOT do manual analysis, do NOT call query_data for EDA, do NOT generate charts "
        "before run_analysis. Your ONLY job is to call run_analysis immediately after get_schema.\n"
        "⚠️ CRITICAL: If the data has fewer than 14 rows, still call run_analysis — let the model "
        "return an error message. Do NOT fall back to manual analysis or a different model.\n"
        "Note: GRU is implemented from scratch in pure numpy — no keras/tensorflow required.\n"
        "Workflow:\n"
        "1. Call get_schema ONCE. Identify the time column and numeric target column.\n"
        "2. groupby_column = time column name (auto-detected if empty).\n"
        "3. n_deciles = forecast horizon (default 12).\n"
        "4. Call run_analysis(analysis_name='Time_Series_GRU', sql=..., target_column=..., "
        "groupby_column=<time_col>, n_deciles=<steps>) — do this immediately, no EDA first.\n"
        "   SQL: SELECT <time_col>, <value_col> FROM <table> ORDER BY <time_col>\n"
        "   NOTE: GRU training may take 10-30 seconds for large datasets — this is expected.\n"
        "5. Generate ALL THREE outputs:\n"
        "   a) Line_Chart(analysis_result): x=ds — TWO y-series in ONE chart:\n"
        "      SQL: SELECT ds, y_actual, y_pred FROM analysis_result\n"
        "      field_mapping: {\"x\":\"ds\",\"y\":[\"y_actual\",\"y_pred\"]}\n"
        "      ⚠️ Do NOT filter by segment. Do NOT split into two separate queries.\n"
        "   b) Line_Chart(analysis_breakdown): x=epoch, y=train_loss — training loss curve\n"
        "      A smoothly decreasing curve indicates successful training.\n"
        "   c) Display analysis_metrics table (R²/MAE/RMSE/final loss) as a table.\n"
        "6. Conclude with forecast trend, model convergence quality, and uncertainty interpretation."
    ),
    "logistic": (
        "The user issued the /logistic command for Logistic Regression analysis.\n"
        "Workflow:\n"
        "1. Call get_schema ONCE.\n"
        "2. target_column = the classification label column (binary or multi-class).\n"
        "3. groupby_column = L2 regularization lambda as a string (e.g. '0.01'; default '0.01').\n"
        "4. n_deciles = max training iterations (default 1000; pass 0 for default).\n"
        "5. Call run_analysis(analysis_name='Logistic_Regression', sql=..., target_column=..., "
        "groupby_column=<lambda_str>, n_deciles=<max_iter>).\n"
        "   SQL: SELECT <feature_cols>, <target_col> FROM <table>\n"
        "   Include both numeric and categorical feature columns; preprocessing is automatic.\n"
        "6. Generate ALL THREE charts:\n"
        "   a) Bar_Chart(analysis_result): x=feature, y=importance_pct  — feature importance\n"
        "   b) Heatmap(analysis_breakdown): x=predicted, y=actual, z=count  — confusion matrix\n"
        "   c) Line_Chart(analysis_roc): x=fpr, y=tpr, series=class  — ROC curve\n"
        "      Include AUC values in the chart title.\n"
        "7. Conclude with a 2-4 sentence business interpretation covering top predictors and model fit."
    ),
    "data": (
        "The user issued the /data command to profile their data.\n"
        "Call profile_data immediately as your FIRST and ONLY tool call.\n"
        "Pass table_name if the user specified one; otherwise leave it empty.\n"
        "Do NOT call get_schema, query_data, or any other tool first.\n"
        "After profile_data returns, present the stats summary to the user — "
        "the distribution charts are automatically included."
    ),
    "inset": (
        "The user issued the /inset command to handle missing values.\n"
        "Call clean_data(operation='fill_na', fill_method=<method>) immediately.\n"
        "Determine fill_method from the user's message:\n"
        "  • '0' / 'zero' / '补0' → fill_method='zero'\n"
        "  • 'mean' / '均值' → fill_method='mean'\n"
        "  • 'median' / '中位数' → fill_method='median'\n"
        "  Default to 'mean' if the user did not specify.\n"
        "Pass table_name if mentioned; otherwise leave empty (auto-detects first table).\n"
        "Do NOT call any other data tools before clean_data.\n"
        "After the call, tell the user the cleaned table is saved as 'cleaned_data'."
    ),
    "winsorize": (
        "The user issued the /winsorize command to cap extreme values.\n"
        "Call clean_data(operation='winsorize', lower_pct=<N>, upper_pct=<M>) immediately.\n"
        "Extract lower_pct and upper_pct from the user's message (e.g. '1 99' → lower=1, upper=99).\n"
        "Default: lower_pct=1, upper_pct=99 if not specified.\n"
        "Do NOT call any other data tools before clean_data.\n"
        "After the call, tell the user the result is saved as 'cleaned_data'."
    ),
    "trimming": (
        "The user issued the /trimming command to remove rows outside a value range.\n"
        "Call clean_data(operation='trimming', trim_column=<col>, min_val=<N>, max_val=<M>) immediately.\n"
        "Extract trim_column, min_val, and max_val from the user's message.\n"
        "If trim_column is unclear, call get_schema ONCE first to see numeric columns, "
        "then immediately call clean_data.\n"
        "Do NOT call query_data or any analysis tool.\n"
        "After the call, tell the user the result is saved as 'cleaned_data'."
    ),
    "export": (
        "The user issued the /export command to export data to Excel.\n"
        "Goal: call propose_excel_export — NEVER export_excel this turn.\n\n"
        "STEP 1 — Check what the user wants to export:\n"
        "  • If they just want the raw/current data → skip to STEP 3.\n"
        "  • If they ask for LABELS, analysis results, derived/cross-tab tables, or any\n"
        "    table that does NOT yet exist in the data source (e.g. '带标签', '十分位标签',\n"
        "    '聚类结果', '分组汇总', 'with labels') → you MUST create those tables FIRST.\n\n"
        "STEP 2 — Generate the missing tables (only if STEP 1 requires it):\n"
        "  • Call get_schema to see existing tables.\n"
        "  • For label tables: run the relevant run_analysis (Data_Decile_Analysis writes\n"
        "    'decile_labels'; K_Means writes 'cluster_labels'), OR use create_analysis_table\n"
        "    with SQL that joins/derives the labelled columns.\n"
        "  • Verify the new table exists before continuing. Do this in the SAME turn.\n\n"
        "STEP 3 — Propose the export:\n"
        "  Call propose_excel_export(tables=[\"*\"], summary=<one-line description>).\n"
        "  tables=[\"*\"] exports EVERY table currently in the data source — so any label\n"
        "  table you created in STEP 2 will be included automatically.\n"
        "  Only pass specific table names if the user explicitly named the tables.\n"
        "  Output NOTHING after propose_excel_export — the UI handles confirmation."
    ),
    "excel_revise": (
        "The user wants to revise the Excel export plan. "
        "Current tables/filename are embedded in the user message as [CURRENT_EXCEL_JSON]. "
        "Apply the requested changes and call propose_excel_export with the updated params. "
        "Output NOTHING after the tool call."
    ),
    "report": (
        "The user issued the /report command to generate a Word document report.\n"
        "Goal: call propose_report_outline — NEVER export_report this turn.\n\n"
        "Step 1 — Charts (only if user asked for charts / 带图):\n"
        "  If the user wants charts, generate them with generate_chart using data already\n"
        "  in the conversation or by running 1-2 targeted queries.\n"
        "  Charts are automatically bundled into the ZIP when the report is confirmed.\n"
        "  If the user did NOT ask for charts, skip this step entirely.\n\n"
        "Step 2 — Compose the report outline from the conversation history:\n"
        "  title: a concise, descriptive title\n"
        "  sections: Executive Summary → Key Findings → Detailed Analysis → Recommendations\n"
        "  Each section has heading + content (plain text summary from the conversation).\n"
        "  Do NOT re-query or re-analyse data for the text content.\n\n"
        "Step 3 — Call propose_report_outline(title=..., sections=[...]).\n"
        "  Output NOTHING after the tool call — the UI handles confirmation."
    ),
    "report_revise": (
        "The user wants to revise the report outline. "
        "Current title/sections are embedded as [CURRENT_REPORT_JSON] in the user message. "
        "Apply the requested changes and call propose_report_outline with the updated params. "
        "Output NOTHING after the tool call."
    ),
    "ppt": (
        "The user issued /ppt. Goal: call propose_ppt_outline — NEVER generate_ppt this turn.\n\n"
        "IMPORTANT: This MUST be done in TWO SEPARATE turns. Do NOT call propose_ppt_outline "
        "in the same turn as data queries — you need the query results first!\n\n"
        "Turn 1 — Gather data:\n"
        "  Call get_schema ONCE to understand tables. Run 2–5 queries to retrieve the key\n"
        "  metrics, breakdowns, and time-series that the PPT will visualise.\n"
        "  STOP after issuing these tool calls. Do NOT call propose_ppt_outline yet.\n\n"
        "Turn 1b — Color scheme (optional): if the user specifies a firm style "
        "(BCG/Bain/EY/McKinsey), call set_ppt_color_scheme first. Default: mckinsey.\n\n"
        "Turn 2 — After you receive the query results, design 8–15 slides using ONLY "
        "real data from those results.\n"
        "  NEVER fabricate numbers, labels, or percentages — use exact values from tool results.\n"
        "  Structure: cover → toc → [section_divider + content] × N → closing.\n"
        "  Include at least 2 chart slides with actual data rows:\n"
        "    donut  : segments list [[value_fraction, 'COLOR', 'Label'], ...] — fractions sum to 1.0\n"
        "    grouped_bar / stacked_bar: categories, series, and values from query results\n"
        "    timeline: milestones list from real data\n"
        "  Allowed layouts: cover, toc, section_divider, big_number, two_stat, metric_cards,\n"
        "    data_table, table_insight, executive_summary, two_column_text, action_items,\n"
        "    donut, grouped_bar, stacked_bar, timeline, closing.\n"
        "  Color strings ONLY: NAVY, ACCENT_BLUE, ACCENT_GREEN, ACCENT_ORANGE, ACCENT_RED.\n\n"
        "  Then call propose_ppt_outline(title=..., slides=[...]).\n"
        "  Output NOTHING after the tool call — the UI handles user interaction."
    ),
    "ppt_revise": (
        "The user wants to revise a PPT outline. The current slides JSON is embedded in\n"
        "the user message as [CURRENT_SLIDES_JSON]. Parse it, apply the requested changes,\n"
        "then call propose_ppt_outline with the updated complete slides list.\n"
        "Do NOT call generate_ppt. Do NOT call data tools unless the user asks for new data.\n"
        "Output NOTHING after the tool call."
    ),
    "dashboard": (
        "The user issued /dashboard. Goal: call propose_dashboard_outline — NEVER call generate_dashboard this turn.\n\n"
        "IMPORTANT: This MUST be done in TWO SEPARATE turns. Do NOT call propose_dashboard_outline\n"
        "in the same turn as data queries — you need the query results first!\n\n"
        "Turn 1 — Gather data:\n"
        "  Call get_schema ONCE to understand tables and column names.\n"
        "  Run 2–5 exploratory queries to understand data shape, key metrics, and distributions.\n"
        "  STOP after issuing these tool calls. Do NOT call propose_dashboard_outline yet.\n\n"
        "Turn 2 — After receiving query results, design 2–6 dashboard widgets.\n"
        "  Each widget MUST have a valid SQL query using ONLY real table/column names from the schema.\n"
        "  NEVER fabricate column names or table names — only use what get_schema returned.\n"
        "  Choose appropriate chart types:\n"
        "    KPI_Card: for a single headline metric (SQL returns 1 row; col1=value, col2=subtitle, col3=trend%).\n"
        "      Use 1–4 KPI cards at the top for key business numbers. Recommended grid: w=3, h=2.\n"
        "      Example SQL: SELECT COUNT(*) AS 总订单数 FROM orders\n"
        "    Bar_Chart / Line_Chart: for comparisons or trends (field_mapping: x, y)\n"
        "    Grouped_Bar_Chart: for multi-series comparisons (field_mapping: x, value_cols=[col1,col2,...])\n"
        "    Stacked_Bar_Chart: for part-to-whole comparisons (field_mapping: x, y=[col1,col2,...])\n"
        "    Pie_Chart: for proportions (field_mapping: label, value)\n"
        "    Scatter_Plot: for correlations (field_mapping: x, y, [color])\n"
        "    Area_Chart: for cumulative trends (field_mapping: x, y)\n"
        "    Heatmap: for matrix/correlation data (field_mapping: x, y, value)\n"
        "  Layout recommendation: place KPI cards in a row at y=0 (w=3,h=2 each), then charts below (y=2+).\n"
        "  Assign grid positions so widgets tile neatly (total width = 12 units):\n"
        "    e.g. two charts side-by-side: {x:0,y:2,w:6,h:4} and {x:6,y:2,w:6,h:4}\n"
        "  Then call propose_dashboard_outline(name=..., widgets=[...]).\n"
        "  Output NOTHING after the tool call — the UI handles user confirmation."
    ),
    "dashboard_revise": (
        "The user wants to revise the dashboard outline. "
        "The current widgets JSON is embedded as [CURRENT_DASHBOARD_JSON] in the user message. "
        "Apply the requested changes and call propose_dashboard_outline with the updated params. "
        "Do NOT call generate_dashboard. Do NOT call data tools unless the user asks for new data. "
        "Output NOTHING after the tool call."
    ),
}


@dataclass(frozen=True)
class PromptContext:
    """Deterministic capability context used to assemble the system prompt."""

    has_data_source: bool = False
    source_count: int = 0
    has_workspace: bool = False
    needs_workspace: bool = False
    teams_enabled: bool = False
    activation_kind: str = ""
    activation_name: str = ""
    needs_chart: bool = False
    needs_output: bool = False
    needs_hooks: bool = False
    has_knowledge: bool = False
    has_unnamed_columns: bool = False


CORE_RULES = """Your name is ZHIXI/智析. You are a professional business analyst assistant embedded in a data analytics platform.
Help users understand business data through concise, evidence-backed conversation.

## Core rules

1. NEVER fabricate or estimate data values, column names, table contents, row counts,
statistical results, rankings, trends, percentages, or findings. Every data-derived
number in an answer must come from a tool result in the current turn. If evidence is
missing, say what is unverified and use an available tool.
2. Respond in the user's language. Use standard Markdown only; never use box-drawing
or ASCII tree art. Format numbers with separators and units when the tool evidence
supports them.
3. Do not expose unexecuted SQL as an answer. Show SQL only when explicitly requested,
and execute it first whenever a data source is available.
4. When a request has two or more materially different interpretations, call ask_user
with one focused question and 2-6 short options. Do not present a plain-text choice menu.
A trivial assumption is allowed only when all reasonable interpretations lead to the
same action.
5. Application permissions and tool availability are authoritative. Never claim an
operation succeeded without a successful tool result."""


DATA_RULES = """## Data analysis rules

1. Call get_schema before writing SQL unless the current-turn evidence already contains
the exact table structure. For a table omitted from a compact schema, call
get_table_detail before querying it. Use exact identifiers; never guess.
2. Execute SQL through query_data. Report empty results and errors honestly. Do not
replace them with inferred values.
3. Regression, correlation, significance tests, clustering, forecasting and other
statistical computation must use run_analysis or query_data; never calculate results
in-context.
4. Use create_analysis_table only for useful multi-step joins, aggregations or reshaping.
Avoid extra round trips for simple queries.
5. After raw results, add a concise business interpretation grounded in those results.
For open-ended requests with no metric, dimension or analysis direction, inspect schema
if needed and then use ask_user.
6. run_analysis outputs may include analysis_result, analysis_breakdown,
analysis_metrics, analysis_roc and analysis_elbow. Treat them as result tables only
after the tool confirms creation."""


WORKSPACE_RULES = """## Workspace and local-file rules

The logical Workspace exposes allowlisted system roots (uploads, outputs, mcp) and may
expose a mounted user root. When the user asks about local files, directories, on-disk
data or whether files can be read, call workspace_status before claiming they are
unavailable. Search narrowly with workspace_glob/workspace_grep and read only relevant
files; never request a recursive dump of every root.

If workspace_status shows user data files, they are already registered as data-source
tables: use get_schema then query_data. Do not ask for another upload and do not use
read_csv, read_csv_auto or CREATE TABLE. If a mounted user root has no recognizable data
files, state that and ask which file should be used."""


KNOWLEDGE_RULES = """## Business knowledge availability

An isolated business knowledge base exists and is available through query_knowledge. Never
assume or describe its contents before retrieval. Use query_knowledge only for a
business-analysis request, with the user's original business keywords. Apply only the
returned relevant metric definitions, SQL templates, rules and document fragments. If
knowledge is used, end with a short 引用来源/source section. For identity, small-talk,
general help, pure file navigation or unrelated requests, do not access the knowledge
base."""


CHART_RULES = """## Chart rules

Before generating a new chart type, call select_chart with the visualization intent and
available columns, then verify the required columns with query_data and call
generate_chart using the returned chart_id and exact required role keys. You may skip
selection only when regenerating an already confirmed chart type in the same turn.
field_mapping values must be SQL result column names, not raw arrays or display objects;
multi-series y and parallel-coordinate dimensions are lists. Ensure every required role
is selected by the SQL."""


OUTPUT_RULES = """## Output artifact rules

PPT, Word report, Excel export and Dashboard tools are available only through an active
trusted Skill or confirmation flow. Follow the active Skill instructions. Proposal tools
create a reviewable plan; generation/export happens only in the confirmation flow.
Never invent data, identifiers or chart values for an artifact, and output nothing after
a proposal tool when the UI owns confirmation."""


TEAMS_RULES = """## Teams rules

Teams are enabled. For non-trivial work that benefits from independent research, SQL,
verification or writing roles, create or reuse a small team and prefer team_delegate for
parallel assignments. Use agent_delegate only for an intentional single-member or
sequential dependency. Trivial one-step requests should run directly.

Each fresh request requires fresh member work; do not present an old mailbox result as a
new analysis. Teammates are bounded read-only model calls: they may inspect schema,
query data, search knowledge and read relevant Workspace files, but cannot mutate data,
create nested teams or ask the user questions. Include the business goal in assignments
and use a verifier for important metrics, SQL or conclusions."""


HOOKS_RULES = """## Hooks configuration rules

When the user supplies a URL or documentation and asks to configure Hooks, webhooks,
callbacks or automation, browse the page first, derive the smallest supported config,
then call configure_hooks with merge=true. Never invent undocumented endpoint fields.
Use command hooks only when the user explicitly requests a local Python script hook and
confirms command hooks; never generate a shell snippet. Preserve existing hooks unless
replacement was explicitly requested."""


MULTI_SOURCE_RULES = """## Multi-source SQL rules

When schema identifiers use src1__, src2__ or similar prefixes, preserve the full exact
identifier in SQL. Cross-source JOIN and UNION are supported in the shared query engine.
Never strip, swap or guess a source prefix."""


UNNAMED_COLUMN_RULES = """## Unnamed-column rules

Columns such as col, col_2 and col_3 represent real data with blank source headers.
Inspect supplied samples before assigning meaning, retain these columns in analysis, and
ask for confirmation when their semantics remain ambiguous. Recommend renaming blank
headers in the source file."""


_WORKSPACE_INTENT_RE = re.compile(
    r"(workspace|workdir|local\s+file|folder|directory|on\s+disk|"
    r"工作目录|工作区|本地文件|目录|文件夹|磁盘|读取文件|打开文件|查找文件)",
    re.IGNORECASE,
)
_HOOKS_INTENT_RE = re.compile(
    r"(hooks?|webhooks?|callback|automation|回调|自动化|钩子|配置这个.{0,20}(?:链接|url))",
    re.IGNORECASE,
)
_CHART_INTENT_RE = re.compile(
    r"(chart|plot|visuali[sz]|dashboard|图表|可视化|画图|绘图|看板)",
    re.IGNORECASE,
)
_NON_BUSINESS_KNOWLEDGE_RE = re.compile(
    r"^\s*(?:你是谁|你叫什么|介绍(?:一下)?你自己|who\s+are\s+you|what(?:'s| is)\s+your\s+name|"
    r"你好|您好|hello|hi|谢谢|thanks?|help|帮助)\s*[？?!！。.]*\s*$",
    re.IGNORECASE,
)
_BUSINESS_ANALYSIS_RE = re.compile(
    r"(分析|统计|查询|计算|汇总|总结|对比|比较|趋势|变化|增长|下降|占比|分布|排名|预测|回归|"
    r"相关性|异常|诊断|洞察|指标|口径|规则|成本|收入|利润|订单|客户|用户|销售|"
    r"运营|转化|留存|流失|效率|绩效|预算|业务|报表|数据|schema|sql|metric|kpi|"
    r"analy[sz]|trend|forecast|revenue|profit|cost|sales|customer|retention|churn)",
    re.IGNORECASE,
)
_FILE_NAVIGATION_RE = re.compile(
    r"(打开|读取|查找|列出|浏览|上传|删除|移动|重命名|open|read|find|list|browse|upload|delete|move|rename)"
    r".{0,20}(文件|目录|文件夹|file|folder|directory)",
    re.IGNORECASE,
)
_ANALYSIS_ACTION_RE = re.compile(
    r"(分析|统计|查询|计算|汇总|总结|对比|比较|趋势|变化|增长|下降|占比|分布|排名|预测|"
    r"回归|相关性|异常|诊断|洞察|analy[sz]|trend|forecast|compare|calculate)",
    re.IGNORECASE,
)


def message_needs_workspace_rules(message: str, *, has_workspace: bool = False) -> bool:
    text = str(message or "")
    return bool(_WORKSPACE_INTENT_RE.search(text)) or (
        has_workspace and bool(re.search(r"\.(?:csv|xlsx?|xlsm|docx|pdf)\b", text, re.IGNORECASE))
    )


def message_needs_hooks_rules(message: str) -> bool:
    return bool(_HOOKS_INTENT_RE.search(str(message or "")))


def message_needs_chart_rules(message: str) -> bool:
    return bool(_CHART_INTENT_RE.search(str(message or "")))


def message_needs_knowledge(message: str) -> bool:
    """Conservative privacy gate for knowledge retrieval."""
    text = str(message or "").strip()
    if not text or _NON_BUSINESS_KNOWLEDGE_RE.fullmatch(text):
        return False
    if _FILE_NAVIGATION_RE.search(text) and not _ANALYSIS_ACTION_RE.search(text):
        return False
    return bool(_BUSINESS_ANALYSIS_RE.search(text))


def schema_has_unnamed_columns(schema: str) -> bool:
    return bool(re.search(r"(?<![A-Za-z0-9_])col(?:_\d+)?(?![A-Za-z0-9_])", str(schema or "")))


def get_system_prompt(
    context: PromptContext | None = None,
    *,
    knowledge_summary: str | None = None,
) -> str:
    """Assemble stable prompt blocks in a fixed order for Prompt Cache reuse."""
    context = context or PromptContext()
    blocks = [CORE_RULES]
    if context.has_data_source:
        blocks.append(DATA_RULES)
    if context.needs_workspace:
        blocks.append(WORKSPACE_RULES)
    if context.has_knowledge:
        # ``knowledge_summary`` remains a compatibility argument for callers,
        # but content is intentionally ignored: the prompt only advertises the
        # isolated retrieval capability. Actual content arrives via Top-K RAG.
        blocks.append(KNOWLEDGE_RULES)
    if context.needs_chart:
        blocks.append(CHART_RULES)
    if context.needs_output:
        blocks.append(OUTPUT_RULES)
    if context.teams_enabled:
        blocks.append(TEAMS_RULES)
    if context.needs_hooks:
        blocks.append(HOOKS_RULES)
    if context.source_count > 1:
        blocks.append(MULTI_SOURCE_RULES)
    if context.has_unnamed_columns:
        blocks.append(UNNAMED_COLUMN_RULES)
    return "\n\n".join(block.strip() for block in blocks if block and block.strip())


# ── Temporary per-session prompt ──────────────────────────────────────────────

# Hard cap on the injected temp-prompt length so a single conversation-scoped
# instruction can't blow up the context window. ~4000 chars ≈ 1100 tokens.
TEMP_PROMPT_MAX_CHARS = 4000


def strip_temp_prompt_thinking(temp_prompt: str) -> str:
    """Remove reasoning blocks accidentally emitted by thinking models."""
    text = (temp_prompt or "").strip()
    if not text:
        return ""

    # Most OpenAI-compatible reasoning models wrap hidden reasoning this way.
    text = re.sub(r"<think\b[^>]*>[\s\S]*?</think\s*>", "", text,
                  flags=re.IGNORECASE)

    # If a provider emits an opening tag without closing it, everything after
    # that tag is reasoning rather than a usable instruction.
    unclosed = re.search(r"<think\b[^>]*>", text, flags=re.IGNORECASE)
    if unclosed:
        text = text[:unclosed.start()]

    # Remove orphan closing tags left by malformed provider output.
    text = re.sub(r"</think\s*>", "", text, flags=re.IGNORECASE)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def build_temp_prompt_section(temp_prompt: str) -> str:
    """Wrap a user's per-session temporary instruction for system-prompt injection.

    Returns an empty string when there is nothing to inject. The wrapper makes
    the priority explicit: the temp instruction overrides ordinary default
    behaviour, but must never override the ABSOLUTE no-fabrication rule or the
    strict output-format rules above it in the system prompt.
    """
    text = strip_temp_prompt_thinking(temp_prompt)
    if not text:
        return ""
    if len(text) > TEMP_PROMPT_MAX_CHARS:
        text = text[:TEMP_PROMPT_MAX_CHARS] + "\n…[临时指令已截断]"
    return (
        "\n\n## 本次会话临时指令（用户为当前对话设定）\n"
        "以下指令由用户为这次对话临时设定，优先级高于一般默认行为；"
        "但绝对不可违反上文的「ABSOLUTE RULE — NO FABRICATION」与输出格式规则。\n"
        f"{text}"
    )
