"""Deterministic workflow-stage routing for model-visible tool schemas."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .activation import ActivationContext
from .prompts import message_needs_knowledge


STAGES = frozenset({
    "general",
    "understand",
    "inspect",
    "analyze",
    "visualize",
    "propose_output",
    "generate_output",
    "verify",
})

_ALWAYS_AVAILABLE = frozenset({
    "ask_user",
    "workspace_status",
    "workspace_glob",
    "workspace_grep",
    "workspace_read_file",
    "read_tool_result",
    "search_mcp_tools",
    "browse_webpage",
    "configure_hooks",
    "team_create",
    "team_delete",
    "team_list",
    "team_status",
    "send_message",
    "agent_delegate",
    "team_delegate",
    "task_create",
    "task_get",
    "task_list",
    "task_update",
    "plan_complete",
})

_INSPECT_TOOLS = frozenset({
    "query_knowledge",
    "get_schema",
    "get_table_detail",
    # Keep query_data available so providers that emit schema + query in one
    # valid batch do not pay an avoidable extra round trip.
    "query_data",
    "profile_data",
})

_ANALYZE_TOOLS = frozenset({
    "get_schema",
    "get_table_detail",
    "query_data",
    "create_analysis_table",
    "delete_analysis_tables",
    "run_analysis",
    "profile_data",
    "clean_data",
})

_CHART_TOOLS = frozenset({"select_chart", "generate_chart"})

_PROPOSAL_TOOLS = frozenset({
    "propose_ppt_outline",
    "propose_report_outline",
    "propose_excel_export",
    "propose_dashboard_outline",
    "set_ppt_color_scheme",
})

_GENERATION_TOOLS = frozenset({
    "generate_ppt",
    "export_report",
    "export_excel",
    "generate_dashboard",
})


def _tool_name(schema: dict) -> str:
    return str(((schema.get("function") or {}).get("name") or "")).strip()


def completed_tool_names(messages: Iterable[dict]) -> frozenset[str]:
    """Extract tool names from assistant calls without reading result bodies."""
    names: set[str] = set()
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or ():
            name = str(((call.get("function") or {}).get("name") or "")).strip()
            if name:
                names.add(name)
    return frozenset(names)


@dataclass(frozen=True)
class WorkflowStageContext:
    stage: str
    needs_chart: bool = False
    needs_output: bool = False


def infer_workflow_stage(
    *,
    activation: ActivationContext,
    user_message: str,
    has_data_source: bool,
    needs_chart: bool,
    needs_output: bool,
    completed_tools: Iterable[str] = (),
) -> WorkflowStageContext:
    """Infer a conservative stage from immutable turn evidence."""
    done = frozenset(str(name) for name in completed_tools)
    action = activation.internal_action
    if action.endswith("_confirm"):
        return WorkflowStageContext("generate_output", needs_chart, True)
    if done & _GENERATION_TOOLS:
        return WorkflowStageContext("verify", needs_chart, needs_output)
    if done & _PROPOSAL_TOOLS:
        return WorkflowStageContext("propose_output", needs_chart, True)
    if needs_chart and done & {"query_data", "run_analysis", "create_analysis_table"}:
        return WorkflowStageContext("visualize", True, needs_output)
    if done & {"get_schema", "get_table_detail", "query_data", "run_analysis"}:
        return WorkflowStageContext("analyze", needs_chart, needs_output)
    if has_data_source and (
        message_needs_knowledge(user_message)
        or activation.kind in {"skill", "command"}
        or needs_chart
        or needs_output
    ):
        return WorkflowStageContext("inspect", needs_chart, needs_output)
    if needs_output:
        return WorkflowStageContext("propose_output", needs_chart, True)
    return WorkflowStageContext("general", needs_chart, needs_output)


def filter_tools_for_stage(
    tools: list[dict],
    context: WorkflowStageContext,
) -> list[dict]:
    """Remove schemas that cannot be useful in the current workflow stage."""
    if context.stage in {"general", "verify"}:
        return list(tools)

    allowed = set(_ALWAYS_AVAILABLE)
    if context.stage in {"understand", "inspect"}:
        allowed.update(_INSPECT_TOOLS)
    elif context.stage == "analyze":
        allowed.update(_ANALYZE_TOOLS)
        if context.needs_chart:
            allowed.update(_CHART_TOOLS)
        if context.needs_output:
            allowed.update(_PROPOSAL_TOOLS)
    elif context.stage == "visualize":
        allowed.update(_ANALYZE_TOOLS)
        allowed.update(_CHART_TOOLS)
        if context.needs_output:
            allowed.update(_PROPOSAL_TOOLS)
    elif context.stage == "propose_output":
        allowed.update(_ANALYZE_TOOLS)
        allowed.update(_CHART_TOOLS)
        allowed.update(_PROPOSAL_TOOLS)
    elif context.stage == "generate_output":
        allowed.update(_GENERATION_TOOLS)

    if context.needs_chart:
        # A chart request is already served by the built-in chart registry and
        # renderer. Keep those tools visible from the first inspection call so
        # the model never mistakes a temporarily hidden built-in for a missing
        # external capability.
        allowed.update(_CHART_TOOLS)
        allowed.discard("search_mcp_tools")

    return [
        schema for schema in tools
        if _tool_name(schema).startswith("mcp__") or _tool_name(schema) in allowed
    ]
