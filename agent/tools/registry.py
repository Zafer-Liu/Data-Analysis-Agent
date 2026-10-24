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
SideEffectScope = Literal["none", "session", "workspace", "external"]
ConfirmationMode = Literal["none", "explicit_request", "argument", "workflow"]


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
    side_effect: SideEffectScope = "none"
    confirmation: ConfirmationMode = "none"

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
            if spec.concurrency_safe and spec.side_effect != "none":
                raise ValueError(
                    f"concurrency-safe tool cannot declare side effects: {spec.name}"
                )
            if spec.confirmation != "none" and spec.side_effect == "none":
                raise ValueError(
                    f"confirmation requires a side-effect scope: {spec.name}"
                )
            if spec.side_effect == "external" and spec.confirmation == "none":
                raise ValueError(
                    f"external side effect requires confirmation policy: {spec.name}"
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
    side_effect: SideEffectScope | None = None,
    confirmation: ConfirmationMode = "none",
) -> ToolSpec:
    resolved_side_effect: SideEffectScope = side_effect or (
        "workspace" if category in {"write", "output"} else "none"
    )
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
        side_effect=resolved_side_effect,
        confirmation=confirmation,
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
        "browser_search", "read", default_exposed=False, discoverable=True,
        discovery_keywords=(
            "搜索", "search", "调研", "research", "google", "百度", "bing",
            "查一下", "查找", "浏览器搜索",
        ),
        discovery_summary="Search the web in a visible browser and return results.",
        side_effect="session",
    ),
    _spec(
        "browser_navigate", "read", default_exposed=False, discoverable=True,
        discovery_keywords=(
            "打开网址", "navigate", "访问网页", "go to url", "打开网页",
        ),
        discovery_summary="Navigate the visible browser to a URL.",
        side_effect="session",
    ),
    _spec(
        "browser_click", "interaction", default_exposed=False, discoverable=True,
        discovery_keywords=(
            "网页点击", "点击网页", "点击按钮", "点击链接",
            "click button", "click link", "browser click",
        ),
        discovery_summary="Click an element in the visible browser.",
        side_effect="external", confirmation="explicit_request",
    ),
    _spec(
        "browser_fill", "interaction", default_exposed=False, discoverable=True,
        discovery_keywords=(
            "网页输入", "填写表单", "输入框", "browser fill", "type into",
        ),
        discovery_summary="Fill an input field in the visible browser.",
        side_effect="external", confirmation="explicit_request",
    ),
    _spec(
        "browser_extract", "read", default_exposed=False, discoverable=True,
        discovery_keywords=(
            "提取网页", "extract text", "网页内容", "page content",
        ),
        discovery_summary="Extract visible text from the current browser page.",
    ),
    _spec(
        "browser_screenshot", "read", default_exposed=False, discoverable=True,
        discovery_keywords=(
            "截图", "screenshot", "网页截图",
        ),
        discovery_summary="Take a screenshot of the current browser page.",
        side_effect="workspace",
    ),
    _spec(
        "browser_close", "interaction", default_exposed=False, discoverable=True,
        discovery_keywords=(
            "关闭浏览器", "close browser",
        ),
        discovery_summary="Close the visible browser window.",
        side_effect="session",
    ),
    _spec(
        "fetch_table", "read", default_exposed=False, discoverable=True,
        skills=("web-table",),
        discovery_keywords=(
            "网页表格", "抓取表格", "抓下来", "爬取表格", "网页数据", "导入网址",
            "在线表格", "解析pdf", "pdf 表格", "json 接口", "csv 链接",
            "fetch table", "scrape table", "web table", "web scrape", "pdf table",
        ),
        discovery_summary="Fetch a URL (HTML/JSON/CSV/PDF) and register its tables as a queryable data source.",
        side_effect="session",
    ),
    _spec(
        "browser_extract_tables", "read", default_exposed=False, discoverable=True,
        skills=("web-table",),
        discovery_keywords=(
            "动态网页表格", "动态表格", "js 渲染表格", "浏览器表格", "网页表格提取",
            "dynamic table", "javascript table", "browser tables", "rendered table",
        ),
        discovery_summary="Extract <table> elements from the live browser page and register them as a data source.",
        side_effect="session",
    ),
    _spec(
        "configure_hooks", "write", default_exposed=False, discoverable=True,
        discovery_keywords=(
            "hooks", "hook", "webhook", "自动配置", "帮我配置", "配置这个",
            "配置hook", "配置 hooks", "hook配置", "hook config",
        ),
        discovery_summary="Validate and save user Hooks configuration proposed by the model.",
        side_effect="workspace", confirmation="explicit_request",
    ),
    _spec(
        "create_feishu_bitable", "write", default_exposed=False, discoverable=True,
        skills=("feishu-bitable",),
        discovery_keywords=(
            "飞书多维表格", "多维表格", "bitable", "base 表格", "创建飞书表格",
            "新建飞书表格", "创建多维表", "新建多维表",
        ),
        discovery_summary="Create a Feishu Bitable with a named table, text fields, and optional initial records through the configured application bot.",
        side_effect="external", confirmation="explicit_request",
    ),
    _spec(
        "list_feishu_bitable_tables", "read", default_exposed=False, discoverable=True,
        skills=("feishu-bitable",),
        discovery_keywords=("飞书多维表格", "bitable", "多维表格数据表", "表格列表", "读取飞书表格"),
        discovery_summary="List data tables visible to the configured Feishu application bot in one Bitable.",
    ),
    _spec(
        "load_feishu_bitable", "read", default_exposed=False, discoverable=True,
        skills=("feishu-bitable",),
        discovery_keywords=("分析飞书表格", "读取飞书表格", "飞书多维表格", "bitable", "多维表格分析"),
        discovery_summary="Read a bounded Feishu Bitable table into the current conversation as an analyzable data source.",
        side_effect="session",
    ),
    _spec(
        "append_feishu_bitable_records", "write", default_exposed=False, discoverable=True,
        skills=("feishu-bitable",),
        discovery_keywords=("写入飞书表格", "回写飞书表格", "追加多维表格", "append bitable"),
        discovery_summary="Append explicitly requested records to an existing Feishu Bitable table.",
        side_effect="external", confirmation="explicit_request",
    ),
    _spec(
        "update_feishu_bitable_record", "write", default_exposed=False, discoverable=True,
        skills=("feishu-bitable",),
        discovery_keywords=("更新飞书表格", "编辑飞书表格", "修改多维表格", "update bitable"),
        discovery_summary="Update one explicitly identified Feishu Bitable record using a record ID returned by a read.",
        side_effect="external", confirmation="explicit_request",
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
        requires_data_source=True, requires_runtime=True, confirmation="argument",
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
        confirmation="workflow",
    ),
    _spec(
        "export_report", "output", default_exposed=False,
        commands=("report_confirm",), execution_mode="auto",
        job_threshold="report_sections_gt_6_or_chart_count_gt_3",
        requires_runtime=True, confirmation="workflow",
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
        confirmation="workflow",
    ),
    _spec(
        "set_ppt_color_scheme", "write", default_exposed=False,
        discoverable=True,
        discovery_keywords=("ppt color", "ppt颜色", "配色", "主题色", "color scheme"),
        discovery_summary="Set the PPT color scheme preference.",
        side_effect="session",
    ),
    _spec(
        "propose_dashboard_outline", "interaction", default_exposed=False,
        commands=("dashboard", "dashboard_revise"), skills=("dashboard",),
    ),
    _spec(
        "generate_dashboard", "output", default_exposed=False,
        commands=("dashboard_confirm",), execution_mode="auto",
        job_threshold="dashboard_widgets_gt_4_or_total_widget_rows_gte_50000",
        requires_runtime=True, confirmation="workflow",
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
        discovery_keywords=(
            "读取文件", "打开文件", "查看文件", "说明文件", "说明文档",
            "文档内容", "文件内容", "读一下", "看一下", "看下",
            "read", "read file", "open file", "view file", ".txt", ".doc", ".docx", "word 文档", "Word 文档",
        ),
        discovery_summary="Read a bounded workspace text, Word or spreadsheet file.",
        requires_runtime=True,
    ),
    _spec(
        "workspace_write_file", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("写文件", "新建文件", "保存文件", "write file", "create file"),
        discovery_summary="Write a new file under writable workspace roots.",
        requires_runtime=True, confirmation="explicit_request",
    ),
    _spec(
        "workspace_edit_file", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("修改文件", "编辑文件", "替换文本", "edit file", "patch file"),
        discovery_summary="Edit a previously read workspace file.",
        requires_runtime=True, confirmation="explicit_request",
    ),
    _spec(
        "workspace_delete_file", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("删除文件", "移除文件", "delete file", "rm file"),
        discovery_summary="Delete one confirmed file from writable workspace roots.",
        requires_runtime=True, confirmation="argument",
    ),
    _spec(
        "workspace_move_file", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("移动文件", "重命名文件", "move file", "rename file"),
        discovery_summary="Move or rename one workspace file.",
        requires_runtime=True, confirmation="explicit_request",
    ),
    _spec(
        "workspace_bash", "write", default_exposed=False, discoverable=True,
        discovery_keywords=(
            "bash", "shell", "命令行", "运行命令", "checksum", "json validate",
            "json_validate", "git diff", "git status", "git log", "python compile",
        ),
        discovery_summary="Run the strongest restricted command facade in the mounted workspace.",
        requires_runtime=True, requires_workspace=True, confirmation="explicit_request",
    ),
    _spec("structured_output", "interaction", default_exposed=False),
    _spec(
        "load_analysis_skill", "read", default_exposed=True,
        discovery_keywords=("分析技能", "load skill", "analysis skill", "sop", "流程"),
        discovery_summary="Load a project analysis skill SOP body.",
    ),
    _spec(
        "task_create", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("任务", "待办", "task", "todo", "创建任务"),
        discovery_summary="Create a persistent workspace task.",
        requires_runtime=True, requires_workspace=True, confirmation="explicit_request",
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
        requires_runtime=True, requires_workspace=True, confirmation="explicit_request",
    ),
    _spec(
        "team_create", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("团队", "team", "agent team", "创建团队", "团队协作"),
        discovery_summary="Create a persistent workspace analyst team.",
        requires_runtime=True, requires_workspace=True, confirmation="explicit_request",
    ),
    _spec(
        "team_delete", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("团队", "team", "删除团队"),
        discovery_summary="Delete a workspace analyst team.",
        requires_runtime=True, requires_workspace=True, confirmation="explicit_request",
    ),
    _spec(
        "team_list", "read", default_exposed=False, discoverable=True,
        discovery_keywords=("团队", "team", "团队列表", "成员状态"),
        discovery_summary="List workspace analyst teams and member statuses.",
        requires_runtime=True, requires_workspace=True,
    ),
    _spec(
        "team_status", "read", default_exposed=False, discoverable=True,
        discovery_keywords=("团队", "team", "团队状态", "成员状态", "团队里", "mailbox"),
        discovery_summary="Read one workspace analyst team status and recent mailbox.",
        requires_runtime=True, requires_workspace=True,
    ),
    _spec(
        "send_message", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("团队消息", "发消息", "send message", "mailbox", "收件箱"),
        discovery_summary="Send a message to a workspace team member mailbox.",
        requires_runtime=True, requires_workspace=True, confirmation="explicit_request",
    ),
    _spec(
        "agent_delegate", "analysis", default_exposed=False, discoverable=True,
        discovery_keywords=("delegate", "委派", "子任务", "团队分析", "团队协作", "agent delegate"),
        discovery_summary="Delegate a bounded reasoning task to a workspace team member.",
        requires_runtime=True, requires_workspace=True, side_effect="session",
    ),
    _spec(
        "team_plan_create", "write", default_exposed=False, discoverable=True,
        discovery_keywords=("团队计划", "动态计划", "创建团队计划", "创建动态计划", "先建计划", "预览计划", "暂不执行", "不要执行", "dynamic plan"),
        discovery_summary="Create a bounded dynamic team plan without executing members.",
        requires_runtime=True, requires_workspace=True, confirmation="explicit_request",
    ),
    _spec(
        "team_delegate", "analysis", default_exposed=False, discoverable=True,
        discovery_keywords=("parallel team", "并行团队", "并发团队", "批量委派", "切换到团队", "team delegate"),
        discovery_summary="Delegate multiple bounded teammate tasks in parallel.",
        requires_runtime=True, requires_workspace=True, side_effect="session",
    ),
    _spec("plan_complete", "interaction", default_exposed=False),
    _spec(
        "workflow_create", "write", default_exposed=False, discoverable=True,
        discovery_keywords=(
            "workflow", "工作流", "创建workflow", "创建 workflow", "创建工作流",
            "新建workflow", "新建 workflow", "新建工作流", "创建流程", "新建流程",
        ),
        discovery_summary="Create and publish a standard AI-team analysis Workflow template.",
        requires_runtime=True, requires_workspace=True, confirmation="explicit_request",
    ),
    _spec(
        "workflow_create_custom", "write", default_exposed=False, discoverable=True,
        discovery_keywords=(
            "自定义 workflow", "自定义工作流", "自定义 agent", "agent workflow", "创建模板", "创建流程",
        ),
        discovery_summary="Create and publish a custom Agent Workflow defined in conversation.",
        requires_runtime=True, requires_workspace=True, confirmation="explicit_request",
    ),
    _spec(
        "workflow_list", "read", default_exposed=False, discoverable=True,
        discovery_keywords=("workflow", "工作流", "流程", "场景", "可用流程"),
        discovery_summary="List published Workflows in the mounted workspace.",
        requires_runtime=True, requires_workspace=True,
    ),
    _spec(
        "workflow_start", "analysis", default_exposed=False, discoverable=True,
        discovery_keywords=(
            "run workflow", "start workflow", "运行工作流", "启动工作流",
            "运行流程", "启动流程", "执行场景",
        ),
        discovery_summary="Start a published Workflow and return its durable Run status.",
        skills=("workflow",),
        requires_runtime=True, requires_workspace=True,
        side_effect="workspace", confirmation="explicit_request",
    ),
    _spec(
        "workflow_status", "read", default_exposed=False, discoverable=True,
        discovery_keywords=(
            "workflow status", "run status", "工作流状态", "流程状态", "运行状态",
        ),
        discovery_summary="Read and reconcile a durable Workflow Run.",
        requires_runtime=True, requires_workspace=True,
    ),
    # ── Diagram / Canvas tools ──
    # Exposed via skills, but also discoverable so the agent can use them
    # when the user mentions canvas/diagram keywords without manually
    # selecting a skill.
    _spec(
        "display_diagram", "output", default_exposed=False, discoverable=True,
        skills=("business-model-canvas", "bcg-matrix", "swot-analysis", "value-proposition"),
        discovery_keywords=(
            "商业模式画布", "价值主张", "bcg", "swot", "画布", "canvas",
            "diagram", "四象限", "框架图", "战略图",
        ),
        discovery_summary="Render a draw.io diagram (business model canvas, BCG, SWOT, value proposition) to the canvas drawer.",
        concurrency_safe=False,
        confirmation="explicit_request",
    ),
    _spec(
        "edit_diagram", "write", default_exposed=False, discoverable=True,
        skills=("business-model-canvas", "bcg-matrix", "swot-analysis", "value-proposition"),
        discovery_keywords=(
            "商业模式画布", "价值主张", "bcg", "swot", "画布", "canvas",
            "diagram", "四象限", "框架图", "修改画布", "编辑图形",
        ),
        discovery_summary="Edit an existing draw.io diagram in the canvas drawer.",
        confirmation="explicit_request",
    ),
    _spec(
        "get_diagram", "read", default_exposed=False, discoverable=True,
        skills=("business-model-canvas", "bcg-matrix", "swot-analysis", "value-proposition"),
        discovery_keywords=(
            "商业模式画布", "价值主张", "bcg", "swot", "画布", "canvas",
            "diagram", "查看图",
        ),
        discovery_summary="Retrieve the current draw.io XML from the canvas drawer.",
    ),
    _spec(
        "memory_read", "read", default_exposed=False, discoverable=True,
        discovery_keywords=(
            "memory", "记忆", "长期记忆", "偏好", "反馈", "preference", "remember",
            "previous preference", "之前说的", "上次提到",
        ),
        discovery_summary="Read the full body of a named long-term memory record from the current scope.",
    ),
])


def get_tool_spec(name: str) -> ToolSpec | None:
    return BUILTIN_TOOL_REGISTRY.get(name)


def is_job_eligible(name: str) -> bool:
    spec = get_tool_spec(name)
    return bool(spec and spec.execution_mode in {"auto", "job"})
