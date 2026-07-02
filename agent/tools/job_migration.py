# -*- coding: utf-8 -*-
"""D3 JobRunner migration inventory for built-in tools.

The registry remains the source of truth for runtime policy.  This module adds
D3-specific planning notes that are intentionally reviewed for every built-in
tool so long-running work can be migrated in small, auditable batches.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from .registry import BUILTIN_TOOL_REGISTRY, ToolSpec

MigrationStatus = Literal[
    "job_ready",
    "candidate_batch_1",
    "candidate_batch_2",
    "keep_sync",
    "defer",
]


@dataclass(frozen=True)
class ToolJobMigration:
    status: MigrationStatus
    typical_cost: str
    threshold_plan: str
    cancellation_points: str
    result_risk: str
    next_action: str


@dataclass(frozen=True)
class ToolJobRegression:
    test_targets: tuple[str, ...]
    benchmark_baseline: str
    cancellation_behavior: str
    rollback_boundary: str
    isolation_notes: str


_KEEP_SYNC_READ = ToolJobMigration(
    status="keep_sync",
    typical_cost="bounded metadata/read operation",
    threshold_plan="none",
    cancellation_points="not needed",
    result_risk="bounded by tool result budget",
    next_action="keep synchronous unless measurements show user-visible latency",
)

_KEEP_SYNC_INTERACTION = ToolJobMigration(
    status="keep_sync",
    typical_cost="prompt/control operation",
    threshold_plan="none",
    cancellation_points="not needed",
    result_risk="small structured payload",
    next_action="keep synchronous",
)

_KEEP_SYNC_WORKSPACE_WRITE = ToolJobMigration(
    status="keep_sync",
    typical_cost="single workspace file/task mutation",
    threshold_plan="none",
    cancellation_points="mutation is one bounded operation",
    result_risk="small status payload; FileHistory handles rollback snapshots",
    next_action="keep synchronous and preserve confirmation/read-before-write guards",
)


TOOL_JOB_MIGRATION_PLAN: dict[str, ToolJobMigration] = {
    "workspace_status": _KEEP_SYNC_READ,
    "browse_webpage": _KEEP_SYNC_READ,
    "configure_hooks": _KEEP_SYNC_WORKSPACE_WRITE,
    "read_tool_result": _KEEP_SYNC_READ,
    "search_mcp_tools": _KEEP_SYNC_READ,
    "query_knowledge": ToolJobMigration(
        "defer",
        "knowledge retrieval can be remote or embedding-backed",
        "knowledge_latency_gt_3s once observability is available",
        "between retrieval/search phases",
        "answer text may exceed model budget and is already budgeted downstream",
        "measure latency first; migrate only if local retrieval blocks turns",
    ),
    "get_schema": _KEEP_SYNC_READ,
    "get_table_detail": _KEEP_SYNC_READ,
    "create_analysis_table": ToolJobMigration(
        "job_ready",
        "DuckDB CTAS over user query can scan large tables",
        "workspace_persistent_ctas",
        "before query, after table creation before registry update",
        "creates persistent table; must preserve registry and workspace lease semantics",
        "jobs only sources with explicit _db_lock; other connectors remain synchronous",
    ),
    "delete_analysis_tables": ToolJobMigration(
        "keep_sync",
        "bounded metadata mutation over explicit table names",
        "none",
        "mutation is one bounded DROP per table",
        "small cleanup summary; source tables are protected",
        "keep synchronous and require confirm=true",
    ),
    "query_data": ToolJobMigration(
        "job_ready",
        "SQL can scan large local or remote tables",
        "workspace_query_rows_gte_100000",
        "before execution and before result materialization",
        "large result is already persisted by tool result budget",
        "jobs only persistent Workspace DuckDB queries using a fresh DuckDB connection under _db_lock; other connectors remain synchronous",
    ),
    "run_analysis": ToolJobMigration(
        "job_ready",
        "time-series analysis can be CPU heavy",
        "time_series_rows_gte_1000",
        "analysis progress callback",
        "analysis outputs are finalized after job success",
        "use as the reference implementation for later compute migrations",
    ),
    "select_chart": _KEEP_SYNC_READ,
    "generate_chart": ToolJobMigration(
        "job_ready",
        "chart rendering can fetch data and generate sizable HTML",
        "chart_rows_gte_50000",
        "after SQL fetch and before render/save",
        "chart HTML is stored as artifact/session chart state",
        "queries on the request thread, then renders large DataFrame snapshots in JobRunner",
    ),
    "profile_data": ToolJobMigration(
        "job_ready",
        "profiling scans whole tables",
        "profile_rows_gte_50000_or_columns_gte_50",
        "between column/profile phases",
        "profile dictionary can grow with wide tables",
        "queries on the request thread, then profiles DataFrame snapshots in JobRunner",
    ),
    "clean_data": ToolJobMigration(
        "job_ready",
        "cleaning scans and writes derived tables",
        "clean_rows_gte_50000",
        "after read, after transform, before table publish",
        "writes analysis table and must keep registry/source boundaries intact",
        "queries on the request thread, computes cleaning in JobRunner, then writes result on request thread",
    ),
    "export_excel": ToolJobMigration(
        "job_ready",
        "Excel export can serialize many rows/sheets",
        "excel_bytes_gt_5mb",
        "between table exports and before file publish",
        "artifact path/download URL must remain stable",
        "wire existing auto metadata into a full worker path if not already routed",
    ),
    "export_report": ToolJobMigration(
        "job_ready",
        "Word report generation can render charts and docx assets",
        "report_sections_gt_6_or_chart_count_gt_3",
        "between section/chart rendering and before file publish",
        "docx artifact path/download URL must remain stable",
        "uses progress stages: collect charts, render document, publish artifact",
    ),
    "propose_excel_export": _KEEP_SYNC_INTERACTION,
    "propose_report_outline": _KEEP_SYNC_INTERACTION,
    "propose_ppt_outline": _KEEP_SYNC_INTERACTION,
    "generate_ppt": ToolJobMigration(
        "job_ready",
        "PPT rendering can be slow for many slides/charts",
        "ppt_slides_gt_5",
        "per-slide render loop and before file save",
        "pptx artifact path/download URL must remain stable",
        "use as output-tool migration template together with export_report",
    ),
    "set_ppt_color_scheme": _KEEP_SYNC_INTERACTION,
    "propose_dashboard_outline": _KEEP_SYNC_INTERACTION,
    "generate_dashboard": ToolJobMigration(
        "job_ready",
        "dashboard build renders multiple widgets and persists dashboard metadata",
        "dashboard_widgets_gt_4_or_total_widget_rows_gte_50000",
        "between widget builds and before dashboard publish",
        "dashboard id, saved spec, and chart artifacts must be published atomically",
        "queries on the request thread, then renders prefetched widget snapshots in JobRunner",
    ),
    "ask_user": _KEEP_SYNC_INTERACTION,
    "workspace_glob": _KEEP_SYNC_READ,
    "workspace_grep": ToolJobMigration(
        "defer",
        "bounded grep scans at most configured candidate files",
        "grep_candidates_hit_limit repeatedly",
        "between file scans if ever migrated",
        "bounded match list",
        "keep synchronous while MAX_SEARCH_FILES remains low",
    ),
    "workspace_read_file": _KEEP_SYNC_READ,
    "workspace_write_file": _KEEP_SYNC_WORKSPACE_WRITE,
    "workspace_edit_file": _KEEP_SYNC_WORKSPACE_WRITE,
    "workspace_delete_file": _KEEP_SYNC_WORKSPACE_WRITE,
    "workspace_move_file": _KEEP_SYNC_WORKSPACE_WRITE,
    "workspace_bash": ToolJobMigration(
        "defer",
        "restricted command can run up to its timeout",
        "timeout_gt_10s",
        "process boundary only; cancellation needs process termination semantics",
        "stdout/stderr are bounded by command output cap",
        "revisit after D3 output/compute tools because cancellation semantics differ",
    ),
    "workspace_command": _KEEP_SYNC_READ,
    "structured_output": _KEEP_SYNC_INTERACTION,
    "load_analysis_skill": _KEEP_SYNC_READ,
    "task_create": _KEEP_SYNC_WORKSPACE_WRITE,
    "task_get": _KEEP_SYNC_READ,
    "task_list": _KEEP_SYNC_READ,
    "task_update": _KEEP_SYNC_WORKSPACE_WRITE,
    "team_create": _KEEP_SYNC_WORKSPACE_WRITE,
    "team_delete": _KEEP_SYNC_WORKSPACE_WRITE,
    "team_list": _KEEP_SYNC_READ,
    "team_status": _KEEP_SYNC_READ,
    "send_message": _KEEP_SYNC_WORKSPACE_WRITE,
    "agent_delegate": ToolJobMigration(
        "defer",
        "bounded model subtask without tools/filesystem",
        "delegated_prompt_tokens_gt_8000",
        "before delegated call only",
        "text result is bounded by normal tool result budget",
        "leave synchronous until coordinator workflows need visible child progress",
    ),
    "team_delegate": ToolJobMigration(
        "defer",
        "bounded parallel model subtasks without tools/filesystem",
        "assignment_count_gt_6 or delegated_prompt_tokens_gt_8000",
        "before launching each delegated call; completed members are already persisted",
        "aggregate result is bounded by normal tool result budget and large output persistence",
        "keep synchronous for v1 because it already has internal parallelism and per-member timeout",
    ),
    "plan_complete": _KEEP_SYNC_INTERACTION,
}


D3_JOB_REGRESSION_MATRIX: dict[str, ToolJobRegression] = {
    "clean_data": ToolJobRegression(
        ("Test.test_tool_contract.TestToolContract.test_clean_data_auto_job_threshold_writes_after_job",),
        "small DataFrame stays sync; 50k rows enter JobRunner",
        "cancel after transform returns a canceled message and does not publish output table",
        "revert clean_data registry threshold and _tool_clean_data_with_jobs routing",
        "worker receives a DataFrame snapshot; result table write stays on request thread",
    ),
    "create_analysis_table": ToolJobRegression(
        ("Test.test_tool_contract.TestToolContract.test_create_analysis_table_jobs_only_locked_sources",),
        "sources without _db_lock stay sync; locked Workspace source enters JobRunner",
        "cancel before publish returns a canceled message; registry/schema cache updates only after success",
        "revert create_analysis_table registry threshold and _tool_create_analysis_table_with_jobs routing",
        "only sources with explicit _db_lock are eligible; shared connectors remain sync",
    ),
    "export_excel": ToolJobRegression(
        ("Test.test_excel_jobs",),
        "Excel work over 5MB uses job-backed parse/export paths where implemented",
        "sheet/file boundary cancellation removes incomplete database or unpublished artifact",
        "revert export_excel threshold metadata and worker handoff",
        "artifact is published only after the target file is complete",
    ),
    "export_report": ToolJobRegression(
        ("Test.test_tool_contract.TestToolContract.test_export_report_auto_job_threshold",),
        "reports with >6 sections or >3 charts enter JobRunner",
        "cancel between chart collection/render/publish returns report-generation canceled",
        "revert export_report registry threshold and _tool_export_report_with_jobs routing",
        "document artifact path/download URL is created only after successful publish",
    ),
    "generate_chart": ToolJobRegression(
        ("Test.test_tool_contract.TestToolContract.test_generate_chart_auto_job_threshold",),
        "query result below 50k rows stays sync; 50k rows render in JobRunner",
        "cancel before render/save returns chart-generation canceled",
        "revert generate_chart registry threshold and _tool_generate_chart_with_jobs routing",
        "SQL runs on request thread; worker receives a DataFrame snapshot",
    ),
    "generate_dashboard": ToolJobRegression(
        ("Test.test_tool_contract.TestToolContract.test_generate_dashboard_auto_job_threshold",),
        "dashboard with >4 widgets or >=50k total rows enters JobRunner",
        "cancel between widget renders avoids publishing dashboard manifest",
        "revert generate_dashboard threshold and _tool_generate_dashboard_with_jobs routing",
        "widget SQL is prefetched; worker renders snapshots and publishes atomically",
    ),
    "generate_ppt": ToolJobRegression(
        ("Test.test_tool_contract.TestToolContract.test_registry_drives_workspace_command_and_job_policy",),
        "PPT outlines above 5 slides are job-eligible",
        "cancel at slide boundary should avoid publishing partial pptx",
        "revert generate_ppt threshold metadata and PPT worker route",
        "pptx artifact path/download URL is stable after successful publish only",
    ),
    "profile_data": ToolJobRegression(
        ("Test.test_tool_contract.TestToolContract.test_profile_data_auto_job_threshold",),
        "tables below 50k rows/50 columns stay sync; large or wide tables enter JobRunner",
        "cancel between profile phases returns profile-generation canceled",
        "revert profile_data registry threshold and _tool_profile_data_with_jobs routing",
        "worker receives a DataFrame snapshot; generated charts are attached after success",
    ),
    "query_data": ToolJobRegression(
        (
            "Test.test_tool_contract.TestToolContract.test_query_data_auto_job_for_large_workspace_duckdb_only",
            "Test.test_d3_job_regression.TestD3JobRegression",
        ),
        "persistent Workspace DuckDB result estimates below 100k rows stay sync; >=100k rows enter JobRunner",
        "cancel after job submission returns query canceled and does not reuse partial result",
        "revert query_data registry threshold and _tool_query_data_with_jobs routing",
        "only routed Workspace _db_path is opened under _db_lock; shared/remote connectors stay sync",
    ),
    "run_analysis": ToolJobRegression(
        ("Test.test_time_series_jobs",),
        "time-series inputs below 1000 rows stay sync; >=1000 rows enter JobRunner",
        "progress callback cancellation does not publish partial analysis tables",
        "revert run_analysis threshold metadata and _tool_run_analysis_with_jobs routing",
        "worker receives query result DataFrame and pure parameters; table publication happens after success",
    ),
}


def validate_job_migration_plan(specs: Iterable[ToolSpec] | None = None) -> None:
    specs = tuple(BUILTIN_TOOL_REGISTRY.all() if specs is None else specs)
    spec_names = {spec.name for spec in specs}
    plan_names = set(TOOL_JOB_MIGRATION_PLAN)
    missing = sorted(spec_names - plan_names)
    extra = sorted(plan_names - spec_names)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing={missing}")
        if extra:
            details.append(f"extra={extra}")
        raise ValueError("D3 job migration plan mismatch: " + "; ".join(details))
    for spec in specs:
        plan = TOOL_JOB_MIGRATION_PLAN[spec.name]
        if spec.execution_mode in {"auto", "job"}:
            if plan.status not in {"job_ready", "candidate_batch_1", "candidate_batch_2"}:
                raise ValueError(f"job-eligible tool has non-job migration status: {spec.name}")
            if spec.job_threshold and spec.job_threshold not in plan.threshold_plan:
                raise ValueError(f"job threshold not reflected in D3 plan: {spec.name}")


def validate_job_regression_matrix(specs: Iterable[ToolSpec] | None = None) -> None:
    specs = tuple(BUILTIN_TOOL_REGISTRY.all() if specs is None else specs)
    job_ready = {
        spec.name
        for spec in specs
        if spec.execution_mode in {"auto", "job"}
    }
    matrix_names = set(D3_JOB_REGRESSION_MATRIX)
    missing = sorted(job_ready - matrix_names)
    extra = sorted(matrix_names - {spec.name for spec in specs})
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing={missing}")
        if extra:
            details.append(f"extra={extra}")
        raise ValueError("D3 job regression matrix mismatch: " + "; ".join(details))
    for tool in sorted(job_ready):
        row = D3_JOB_REGRESSION_MATRIX[tool]
        if not row.test_targets:
            raise ValueError(f"D3 regression row has no test targets: {tool}")
        for field in (
            row.benchmark_baseline,
            row.cancellation_behavior,
            row.rollback_boundary,
            row.isolation_notes,
        ):
            if not field:
                raise ValueError(f"D3 regression row has empty field: {tool}")


def build_job_migration_inventory() -> list[dict[str, str]]:
    validate_job_migration_plan()
    rows: list[dict[str, str]] = []
    for spec in BUILTIN_TOOL_REGISTRY.all():
        plan = TOOL_JOB_MIGRATION_PLAN[spec.name]
        rows.append({
            "tool": spec.name,
            "category": spec.category,
            "execution_mode": spec.execution_mode,
            "registry_threshold": spec.job_threshold or "none",
            "requires_data_source": "yes" if spec.requires_data_source else "no",
            "requires_runtime": "yes" if spec.requires_runtime else "no",
            "requires_workspace": "yes" if spec.requires_workspace else "no",
            "status": plan.status,
            "typical_cost": plan.typical_cost,
            "threshold_plan": plan.threshold_plan,
            "cancellation_points": plan.cancellation_points,
            "result_risk": plan.result_risk,
            "next_action": plan.next_action,
        })
    return rows


def build_job_regression_matrix() -> list[dict[str, str]]:
    validate_job_migration_plan()
    validate_job_regression_matrix()
    rows: list[dict[str, str]] = []
    for spec in BUILTIN_TOOL_REGISTRY.all():
        if spec.execution_mode not in {"auto", "job"}:
            continue
        regression = D3_JOB_REGRESSION_MATRIX[spec.name]
        rows.append({
            "tool": spec.name,
            "registry_threshold": spec.job_threshold or "none",
            "test_targets": ", ".join(regression.test_targets),
            "benchmark_baseline": regression.benchmark_baseline,
            "cancellation_behavior": regression.cancellation_behavior,
            "rollback_boundary": regression.rollback_boundary,
            "isolation_notes": regression.isolation_notes,
        })
    return rows
