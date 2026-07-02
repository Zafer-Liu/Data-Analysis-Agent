# -*- coding: utf-8 -*-
"""Single source of truth for built-in tool runtime policy.

JSON schemas remain in ``tools/schemas.py`` because they are prompt-facing and
comparatively large. Everything that controls *when and how* a tool runs lives
here: exposure, data requirements, concurrency, and future JobRunner policy.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

from dataclasses import dataclass
from typing import Iterable, Literal

ToolCategory = Literal["read", "analysis", "write", "output", "interaction"]
ExecutionMode = Literal["sync", "auto", "job"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    category: ToolCategory
    default_exposed: bool = True
    discoverable: bool = False
    discovery_keywords: frozenset[str] = frozenset()
    discovery_summary: str = ""
    commands: frozenset[str] = frozenset()
    skills: frozenset[str] = frozenset()
    requires_data_source: bool = False
    concurrency_safe: bool = False
    execution_mode: ExecutionMode = "sync"
    job_threshold: str = ""
    requires_runtime: bool = False
    requires_workspace: bool = False

    @property
    def discovery_terms(self) -> frozenset[str]:
        return frozenset({self.name, *self.discovery_keywords})

    def is_exposed(
        self, command: str, has_data_source: bool, has_workspace: bool = False,
        skill: str = "", discovered: bool = False,
    ) -> bool:
        if self.requires_data_source and not has_data_source:
            return False
        if self.requires_workspace and not has_workspace:
            return False
        return (
            self.default_exposed
            or command in self.commands
            or skill in self.skills
            or (self.discoverable and discovered)
        )


class ToolRegistry:
    def __init__(self, specs: Iterable[ToolSpec]) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            if not spec.name:
                raise ValueError("tool name cannot be empty")
            if spec.name in self._specs:
                raise ValueError(f"duplicate tool spec: {spec.name}")
            if spec.execution_mode == "auto" and not spec.job_threshold:
                raise ValueError(f"auto tool requires job_threshold: {spec.name}")
            if spec.discoverable and (not spec.discovery_keywords or not spec.discovery_summary):
                raise ValueError(
                    f"discoverable tool requires keywords and summary: {spec.name}"
                )
            self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def all(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs.values())

    def names(self) -> frozenset[str]:
        return frozenset(self._specs)

    def exposed_names(
        self, command: str = "", has_data_source: bool = False, has_workspace: bool = False,
        skill: str = "", discovered_tools: Iterable[str] | None = None,
    ) -> set[str]:
        discovered = set(discovered_tools or ())
        return {
            spec.name
            for spec in self._specs.values()
            if spec.is_exposed(
                command or "", has_data_source, has_workspace, skill or "",
                spec.name in discovered,
            )
        }

    def validate_schema_names(self, schemas: Iterable[dict]) -> None:
        schema_names = {
            ((schema.get("function") or {}).get("name") or "").strip()
            for schema in schemas
        }
        schema_names.discard("")
        missing_specs = schema_names - self.names()
        missing_schemas = self.names() - schema_names
        if missing_specs or missing_schemas:
            details = []
            if missing_specs:
                details.append(f"missing specs={sorted(missing_specs)}")
            if missing_schemas:
                details.append(f"missing schemas={sorted(missing_schemas)}")
            raise ValueError("tool registry/schema mismatch: " + "; ".join(details))


def _spec(
    name: str,
    category: ToolCategory,
    *,
    commands: tuple[str, ...] = (),
    skills: tuple[str, ...] = (),
    default_exposed: bool = True,
    discoverable: bool = False,
    discovery_keywords: tuple[str, ...] = (),
    discovery_summary: str = "",
    requires_data_source: bool = False,
    concurrency_safe: bool = False,
    execution_mode: ExecutionMode = "sync",
    job_threshold: str = "",
    requires_runtime: bool = False,
    requires_workspace: bool = False,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        category=category,
        default_exposed=default_exposed,
        discoverable=discoverable,
        discovery_keywords=frozenset(discovery_keywords),
        discovery_summary=discovery_summary,
        commands=frozenset(commands),
        skills=frozenset(skills),
        requires_data_source=requires_data_source,
        concurrency_safe=concurrency_safe,
        execution_mode=execution_mode,
        job_threshold=job_threshold,
        requires_runtime=requires_runtime,
        requires_workspace=requires_workspace,
    )


BUILTIN_TOOL_REGISTRY = ToolRegistry([
    _spec("workspace_status", "read", requires_runtime=True),
    _spec(
        "browse_webpage", "read", default_exposed=False, discoverable=True,
        discovery_keywords=(
            "http://", "https://", "url", "网页", "链接", "website", "web page",
            "api 文档", "接口文档", "webhook", "docs", "documentation",
        ),
        discovery_summary="Fetch a public HTTP(S) webpage and return bounded readable text.",
    ),
    _spec(
        "configure_hooks", "write", default_exposed=False, discoverable=True,
        discovery_keywords=(
            "hooks", "hook", "webhook", "自动配置", "帮我配置", "配置这个",
            "配置hook", "配置 hooks", "hook配置", "hook config",
        ),
        discovery_summary="Validate and save user Hooks configuration proposed by the model.",
    ),
    _spec("read_tool_result", "read"),
    _spec("search_mcp_tools", "read"),
    _spec("query_knowledge", "read", concurrency_safe=True),
    _spec("get_schema", "read", requires_data_source=True),
    _spec("get_table_detail", "read", requires_data_source=True, concurrency_safe=True),
    _spec(
        "create_analysis_table", "write", requires_data_source=True,
        execution_mode="auto", job_threshold="workspace_persistent_ctas",
        requires_runtime=True,
    ),
    _spec(
        "delete_analysis_tables", "write", default_exposed=False,
        discoverable=True,
        discovery_keywords=("删除表", "清理表", "drop table", "cleanup table", "analysis table cleanup"),
        discovery_summary="Delete confirmed derived analysis tables while protecting source tables.",
        requires_data_source=True, requires_runtime=True,
    ),
    _spec(
        "query_data", "read", requires_data_source=True,
        execution_mode="auto", job_threshold="workspace_query_rows_gte_100000",
        requires_runtime=True,
    ),
    _spec(
        "run_analysis", "analysis", requires_data_source=True,
        execution_mode="auto", job_threshold="time_series_rows_gte_1000", requires_runtime=True,
    ),
    _spec("select_chart", "analysis", requires_data_source=True, concurrency_safe=True),
    _spec(
        "generate_chart", "output", requires_data_source=True,
        execution_mode="auto", job_threshold="chart_rows_gte_50000",
        requires_runtime=True,
    ),
    _spec(
        "profile_data", "analysis", requires_data_source=True,
        default_exposed=False, discoverable=True,
        discovery_keywords=("profile", "profile_data", "数据概况", "数据画像", "字段分布", "数据质量"),
        discovery_summary="Profile a table and produce distribution/quality summaries.",
        execution_mode="auto", job_threshold="profile_rows_gte_50000_or_columns_gte_50",
    ),
    _spec(
        "clean_data", "write", requires_data_source=True,
        default_exposed=False, discoverable=True,
        discovery_keywords=("clean", "clean_data", "清洗", "缺失值", "异常值", "winsorize", "trim"),
        discovery_summary="Clean missing values, outliers, and related table-quality issues.",
        execution_mode="auto", job_threshold="clean_rows_gte_50000",
        requires_runtime=True,
    ),
    _spec(
        "export_excel", "output", default_exposed=False,
        commands=("excel_confirm",), execution_mode="auto",
        job_threshold="excel_bytes_gt_5mb", requires_runtime=True,
    ),
    _spec(
        "export_report", "output", default_exposed=False,
        commands=("report_confirm",), execution_mode="auto",
        job_threshold="report_sections_gt_6_or_chart_count_gt_3",
        requires_runtime=True,
    ),
    _spec(
        "propose_excel_export", "interaction", default_exposed=False,
        commands=("export", "excel_revise"), skills=("export",),
    ),
    _spec(
        "propose_report_outline", "interaction", default_exposed=False,
        commands=("report", "report_revise"), skills=("report",),
    ),
    _spec(
        "propose_ppt_outline", "interaction", default_exposed=False,
        commands=("ppt", "ppt_revise"), skills=("ppt",),
    ),
    _spec(
        "generate_ppt", "output", default_exposed=False,
        commands=("ppt_confirm",), execution_mode="auto",
        job_threshold="ppt_slides_gt_5", requires_runtime=True,
    ),
    _spec(
        "set_ppt_color_scheme", "write", default_exposed=False,
        discoverable=True,
        discovery_keywords=("ppt color", "ppt颜色", "配色", "主题色", "color scheme"),
        discovery_summary="Set the PPT color scheme preference.",
    ),
    _spec(
        "propose_dashboard_outline", "interaction", default_exposed=False,
        commands=("dashboard", "dashboard_revise"), skills=("dashboard",),
    ),
    _spec(
        "generate_dashboard", "output", default_exposed=False,
        commands=("dashboard_confirm",), execution_mode="auto",
        job_threshold="dashboard_widgets_gt_4_or_total_widget_rows_gte_50000",
        requires_runtime=True,
    ),
    _spec("ask_user", "interaction"),
    _spec(
        "workspace_glob", "read", default_exposed=False, discoverable=True,
        discovery_keywords=("workspace", "文件", "目录", "列文件", "list files", "glob", "查找文件"),
        discovery_summary="List files in mounted/system workspace roots.",
        requires_runtime=True,
    ),
    _spec(
        "workspace_grep", "read", default_exposed=False, discoverable=True,
        discovery_keywords=("grep", "搜索", "搜索文件", "查找文本", "全文搜索", "search files", "regex"),
        discovery_summary="Search text files in workspace roots.",
        requires_runtime=True,
    ),
    _spec(
        "workspace_read_file", "read", default_exposed=False, discoverable=True,
        discovery_keywords=("读取文件", "打开文件", "read file", "查看文件", "文件内容"),
        discovery_summary="Read a bounded workspace file.",
        requires_runtime=True,
    ),
    _spec(
        "workspace_write_file", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("写文件", "新建文件", "保存文件", "write file", "create file"),
        discovery_summary="Write a new file under writable workspace roots.",
        requires_runtime=True,
    ),
    _spec(
        "workspace_edit_file", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("修改文件", "编辑文件", "替换文本", "edit file", "patch file"),
        discovery_summary="Edit a previously read workspace file.",
        requires_runtime=True,
    ),
    _spec(
        "workspace_delete_file", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("删除文件", "移除文件", "delete file", "rm file"),
        discovery_summary="Delete one confirmed file from writable workspace roots.",
        requires_runtime=True,
    ),
    _spec(
        "workspace_move_file", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("移动文件", "重命名文件", "move file", "rename file"),
        discovery_summary="Move or rename one workspace file.",
        requires_runtime=True,
    ),
    _spec(
        "workspace_bash", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("bash", "shell", "命令行", "运行命令", "git diff", "git status", "python compile"),
        discovery_summary="Run a restricted shell-like command in the mounted workspace.",
        requires_runtime=True, requires_workspace=True,
    ),
    _spec(
        "workspace_command", "read", default_exposed=False, discoverable=True,
        discovery_keywords=("checksum", "json validate", "git status", "git diff", "git log", "python compile"),
        discovery_summary="Run a fixed, shell-free workspace command.",
        requires_runtime=True,
    ),
    _spec("structured_output", "interaction", default_exposed=False),
    _spec(
        "load_analysis_skill", "read", default_exposed=False, discoverable=True,
        discovery_keywords=("分析技能", "load skill", "analysis skill", "sop", "流程"),
        discovery_summary="Load a project analysis skill SOP body.",
    ),
    _spec(
        "task_create", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("任务", "待办", "task", "todo", "创建任务"),
        discovery_summary="Create a persistent workspace task.",
        requires_runtime=True, requires_workspace=True,
    ),
    _spec(
        "task_get", "read", default_exposed=False, discoverable=True,
        discovery_keywords=("任务", "task", "查看任务", "任务详情"),
        discovery_summary="Read one workspace task.",
        requires_runtime=True, requires_workspace=True,
    ),
    _spec(
        "task_list", "read", default_exposed=False, discoverable=True,
        discovery_keywords=("任务", "待办", "task", "todo", "任务列表"),
        discovery_summary="List persistent workspace tasks.",
        requires_runtime=True, requires_workspace=True,
    ),
    _spec(
        "task_update", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("任务", "待办", "task", "更新任务", "完成任务"),
        discovery_summary="Update a workspace task.",
        requires_runtime=True, requires_workspace=True,
    ),
    _spec(
        "team_create", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("团队", "team", "agent team", "创建团队"),
        discovery_summary="Create a persistent workspace analyst team.",
        requires_runtime=True, requires_workspace=True,
    ),
    _spec(
        "team_delete", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("团队", "team", "删除团队"),
        discovery_summary="Delete a workspace analyst team.",
        requires_runtime=True, requires_workspace=True,
    ),
    _spec(
        "team_list", "read", default_exposed=False, discoverable=True,
        discovery_keywords=("团队", "team", "团队列表", "成员状态"),
        discovery_summary="List workspace analyst teams and member statuses.",
        requires_runtime=True, requires_workspace=True,
    ),
    _spec(
        "team_status", "read", default_exposed=False, discoverable=True,
        discovery_keywords=("团队", "team", "团队状态", "成员状态", "mailbox"),
        discovery_summary="Read one workspace analyst team status and recent mailbox.",
        requires_runtime=True, requires_workspace=True,
    ),
    _spec(
        "send_message", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("团队消息", "发消息", "send message", "mailbox", "收件箱"),
        discovery_summary="Send a message to a workspace team member mailbox.",
        requires_runtime=True, requires_workspace=True,
    ),
    _spec(
        "agent_delegate", "analysis", default_exposed=False, discoverable=True,
        discovery_keywords=("delegate", "委派", "子任务", "团队分析", "agent delegate"),
        discovery_summary="Delegate a bounded reasoning task to a workspace team member.",
        requires_runtime=True, requires_workspace=True,
    ),
    _spec(
        "team_delegate", "analysis", default_exposed=False, discoverable=True,
        discovery_keywords=("parallel team", "并行团队", "并发团队", "批量委派", "team delegate"),
        discovery_summary="Delegate multiple bounded teammate tasks in parallel.",
        requires_runtime=True, requires_workspace=True,
    ),
    _spec("plan_complete", "interaction", default_exposed=False),
])


def get_tool_spec(name: str) -> ToolSpec | None:
    return BUILTIN_TOOL_REGISTRY.get(name)


def is_job_eligible(name: str) -> bool:
    spec = get_tool_spec(name)
    return bool(spec and spec.execution_mode in {"auto", "job"})
