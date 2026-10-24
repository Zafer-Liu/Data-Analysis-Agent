# -*- coding: utf-8 -*-
"""Dynamic tool exposure for each conversation turn."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

from copy import deepcopy
import re

from agent.activation import ActivationContext

from .registry import BUILTIN_TOOL_REGISTRY


MAX_VISIBLE_TOOLS = 20


def _tool_name(schema: dict) -> str:
    return ((schema.get("function") or {}).get("name") or "").strip()


def _normalize_discovery_text(text: str) -> str:
    return " ".join(str(text or "").lower().replace("_", " ").split())


_READ_ACTION_RE = re.compile(r"(查看|读取|打开|读一下|看一下|看下|看看|read|open|view)", re.IGNORECASE)
_FILE_OBJECT_RE = re.compile(
    r"(说明(?:文件|文档)?|文件|文档|资料|正文|内容|file|document|doc(?:x)?|txt|md)",
    re.IGNORECASE,
)
_DOCUMENT_SUFFIX_RE = re.compile(r"\.(?:docx?|txt|md)\b", re.IGNORECASE)
_WEB_CONTEXT_RE = re.compile(
    r"(https?://|网页|网站|浏览器|website|web\s*page|browser)", re.IGNORECASE,
)
_WORKSPACE_CONTEXT_RE = re.compile(
    r"(工作区|工作目录|本地|文件|目录|file|folder|workspace|workdir|grep)",
    re.IGNORECASE,
)
_STRONG_DIAGRAM_RE = re.compile(
    r"(商业模式画布|价值主张画布|bcg\s*矩阵|swot|四象限|draw\.?io|diagram|"
    r"画布|框架图|战略图|形状库|shape\s*library)",
    re.IGNORECASE,
)

_TEAM_TOOLS = {
    "team_create", "team_delete", "team_list", "team_status", "send_message",
    "agent_delegate", "team_plan_create", "team_delegate",
}
_TASK_TOOLS = {"task_create", "task_get", "task_list", "task_update"}
_WORKFLOW_TOOLS = {
    "workflow_create", "workflow_create_custom", "workflow_list",
    "workflow_start", "workflow_status",
}
_FEISHU_TOOLS = {
    "create_feishu_bitable", "list_feishu_bitable_tables",
    "load_feishu_bitable", "append_feishu_bitable_records",
    "update_feishu_bitable_record",
}


def _looks_like_workspace_file_read_request(query: str) -> bool:
    text = str(query or "")
    if not text.strip():
        return False
    if _DOCUMENT_SUFFIX_RE.search(text):
        return True
    return bool(_READ_ACTION_RE.search(text) and _FILE_OBJECT_RE.search(text))


def discover_tool_names_for_query(query: str) -> set[str]:
    """Return discoverable built-in tools mentioned by the user query."""
    haystack = _normalize_discovery_text(query)
    if not haystack:
        return set()
    discovered: set[str] = set()
    if _looks_like_workspace_file_read_request(query):
        discovered.add("workspace_read_file")
    raw = str(query or "").lower()
    for spec in BUILTIN_TOOL_REGISTRY.all():
        if not spec.discoverable:
            continue
        for term in spec.discovery_terms:
            term_text = str(term or "").strip()
            if not term_text:
                continue
            normalized_term = _normalize_discovery_text(term_text)
            if normalized_term and normalized_term in haystack:
                discovered.add(spec.name)
                break
            if term_text.lower() in raw:
                discovered.add(spec.name)
                break

    # Resolve broad lexical collisions after candidate discovery. Capability
    # context wins over generic words such as "搜索", "打开", and "图表".
    if _WORKSPACE_CONTEXT_RE.search(raw) and not _WEB_CONTEXT_RE.search(raw):
        discovered.discard("browser_search")
        discovered.discard("browser_navigate")
        if re.search(r"(修改|编辑|替换|edit|patch)", raw, re.IGNORECASE):
            discovered.add("workspace_edit_file")
        if re.search(r"(删除|移除|delete|remove|\brm\b)", raw, re.IGNORECASE):
            discovered.add("workspace_delete_file")
        if re.search(r"(移动|重命名|move|rename)", raw, re.IGNORECASE):
            discovered.add("workspace_move_file")
        if re.search(r"(新建|创建|写入|保存|create|write|save)", raw, re.IGNORECASE):
            discovered.add("workspace_write_file")
    if _WEB_CONTEXT_RE.search(raw) and not _WORKSPACE_CONTEXT_RE.search(raw):
        discovered.discard("workspace_glob")
        discovered.discard("workspace_grep")
        discovered.discard("workspace_read_file")
    if {"browse_webpage", "browser_navigate"}.issubset(discovered):
        if re.search(
            r"(读取|分析|总结|配置|文档|接口|read|analy[sz]|summari[sz]|config|docs?)",
            raw, re.IGNORECASE,
        ):
            discovered.discard("browser_navigate")
        elif re.search(r"(打开|访问|跳转|navigate|go\s+to|浏览器)", raw, re.IGNORECASE):
            discovered.discard("browse_webpage")
        else:
            discovered.discard("browser_navigate")

    diagram_tools = {"display_diagram", "edit_diagram", "get_diagram"}
    if not _STRONG_DIAGRAM_RE.search(raw):
        discovered -= diagram_tools
    elif discovered & diagram_tools:
        discovered -= diagram_tools
        if re.search(r"(修改|编辑|调整|edit|update)", raw, re.IGNORECASE):
            discovered.update({"get_diagram", "edit_diagram"})
        elif re.search(r"(查看|读取|获取|view|get|read)", raw, re.IGNORECASE):
            discovered.add("get_diagram")
        else:
            discovered.add("display_diagram")

    # CRUD families should expose the requested action, not every tool sharing
    # the family noun. Teams mode remains an authorization gate in
    # ``filter_tools_for_turn``.
    team_hits = discovered & _TEAM_TOOLS
    if team_hits:
        if re.search(r"(删除|移除|delete|remove).{0,12}(团队|team)", raw, re.IGNORECASE):
            discovered -= _TEAM_TOOLS
            discovered.update({"team_delete", "team_status"})
        elif re.search(r"(查看|查询|状态|status).{0,12}(团队|team)|(团队|team).{0,12}(状态|status)", raw, re.IGNORECASE):
            discovered -= _TEAM_TOOLS
            discovered.add("team_status")
        elif re.search(r"(列出|列表|有哪些|list).{0,12}(团队|team)|(团队|team).{0,12}(列表|list)", raw, re.IGNORECASE):
            discovered -= _TEAM_TOOLS
            discovered.add("team_list")
        elif re.search(r"(发送|发消息|send|message|mailbox|收件箱)", raw, re.IGNORECASE):
            discovered -= _TEAM_TOOLS
            discovered.add("send_message")
        elif re.search(r"(并行|并发|批量|parallel|team\s*delegate)", raw, re.IGNORECASE):
            discovered -= _TEAM_TOOLS
            discovered.add("team_delegate")
        elif re.search(r"(委派|子任务|delegate)", raw, re.IGNORECASE):
            discovered -= _TEAM_TOOLS
            discovered.add("agent_delegate")
        elif re.search(r"(创建|新建|create).{0,12}(团队|team)|(团队|team).{0,12}(创建|新建|create)", raw, re.IGNORECASE):
            discovered -= _TEAM_TOOLS
            discovered.add("team_create")

    task_hits = discovered & _TASK_TOOLS
    if task_hits:
        discovered -= _TASK_TOOLS
        if re.search(r"(创建|新建|添加|create|add)", raw, re.IGNORECASE):
            discovered.add("task_create")
        elif re.search(r"(更新|完成|修改|update|complete)", raw, re.IGNORECASE):
            discovered.add("task_update")
        elif re.search(r"(详情|查看.{0,8}任务|task\s*detail)", raw, re.IGNORECASE):
            discovered.add("task_get")
        else:
            discovered.add("task_list")

    workflow_hits = discovered & _WORKFLOW_TOOLS
    if workflow_hits:
        discovered -= _WORKFLOW_TOOLS
        if re.search(r"(状态|进度|status|progress)", raw, re.IGNORECASE):
            discovered.add("workflow_status")
        elif re.search(r"(运行|启动|执行|run|start)", raw, re.IGNORECASE):
            discovered.add("workflow_start")
        elif re.search(r"(创建|新建|create)", raw, re.IGNORECASE):
            discovered.add(
                "workflow_create_custom"
                if re.search(r"(自定义|custom|agent\s*workflow)", raw, re.IGNORECASE)
                else "workflow_create"
            )
        else:
            discovered.add("workflow_list")

    feishu_hits = discovered & _FEISHU_TOOLS
    if feishu_hits:
        discovered -= _FEISHU_TOOLS
        if re.search(r"(创建|新建|create)", raw, re.IGNORECASE):
            discovered.add("create_feishu_bitable")
        elif re.search(r"(追加|写入|回写|append)", raw, re.IGNORECASE):
            discovered.add("append_feishu_bitable_records")
        elif re.search(r"(更新|编辑|修改|update)", raw, re.IGNORECASE):
            discovered.add("update_feishu_bitable_record")
        elif re.search(r"(表格列表|数据表|tables?|list)", raw, re.IGNORECASE):
            discovered.add("list_feishu_bitable_tables")
        else:
            discovered.add("load_feishu_bitable")
    return discovered


def filter_tools_for_turn(
    tools: list[dict],
    *,
    command: str = "",
    activation: ActivationContext | None = None,
    skill_allowed_tools: frozenset[str] | None = None,
    trusted_skill: str = "",
    user_message: str = "",
    discovered_tools: list[str] | tuple[str, ...] | frozenset[str] | set[str] | None = None,
    has_data_source: bool = False,
    has_workspace: bool = False,
    teams_enabled: bool = False,
    include_mcp: bool = True,
    allowed_mcp_tools: frozenset[str] | set[str] | None = None,
) -> list[dict]:
    """Return only tools useful for the current turn.

    Output-generation tools stay hidden unless their slash command is active.
    Data tools are hidden when no data source is connected, while knowledge,
    clarification, and confirm tools can still work.
    """
    policy_command = command or ""
    if activation is not None:
        # Skills never unlock command/action-gated tools. Internal actions keep
        # the existing confirm/revise guards while using a separate namespace.
        policy_command = activation.command_name or activation.internal_action
    discovered = set(discovered_tools or ())
    if not policy_command and not trusted_skill:
        discovered |= discover_tool_names_for_query(user_message)
    allowed = BUILTIN_TOOL_REGISTRY.exposed_names(
        command=policy_command,
        has_data_source=has_data_source,
        has_workspace=has_workspace,
        skill=trusted_skill,
        discovered_tools=discovered,
    )
    if not teams_enabled:
        allowed -= _TEAM_TOOLS
    elif has_workspace:
        # Teams mode authorizes coordination but does not expose every CRUD
        # action on every turn. Creation/deletion/mailbox tools remain driven
        # by current or recent explicit intent.
        allowed |= {
            "team_status", "agent_delegate", "team_delegate",
        }
    if skill_allowed_tools:
        # Intersection happens after normal source/workspace/command policy, so
        # a Skill can only reduce an already authorized set.
        allowed &= set(skill_allowed_tools)
    deferred_count = sum(
        1
        for spec in BUILTIN_TOOL_REGISTRY.all()
        if spec.discoverable and spec.name not in allowed
    )
    if discovered:
        log.debug(
            "[tools] discovery  discovered=%s exposed=%d deferred=%d",
            sorted(discovered), len(allowed), deferred_count,
        )
    else:
        log.debug("[tools] discovery  exposed=%d deferred=%d", len(allowed), deferred_count)

    filtered: list[dict] = []
    selected_mcp = set(allowed_mcp_tools or ())
    for schema in tools:
        name = _tool_name(schema)
        if not name:
            continue
        if name.startswith("mcp__"):
            if (
                include_mcp
                and name in selected_mcp
                and (
                    not skill_allowed_tools
                    or name in set(skill_allowed_tools)
                )
            ):
                filtered.append(schema)
            continue
        if name in allowed:
            filtered.append(schema)

    # Copy so callers can safely tweak descriptions later without mutating the
    # global AGENT_TOOLS list.
    return deepcopy(filtered)


_ESSENTIAL_TOOL_NAMES = {
    "ask_user", "workspace_status", "read_tool_result", "search_mcp_tools",
    "query_knowledge", "get_schema", "query_data", "load_analysis_skill",
}


def limit_tools_for_turn(
    tools: list[dict],
    *,
    user_message: str = "",
    recent_discovered_tools: list[str] | tuple[str, ...] | None = None,
    command: str = "",
    trusted_skill: str = "",
    required_tools: set[str] | frozenset[str] | None = None,
    max_tools: int = MAX_VISIBLE_TOOLS,
) -> list[dict]:
    """Apply a deterministic hard cap after all exposure/stage gates.

    Explicit command/Skill requirements, MCP selections and current-turn
    intent outrank older discoveries. Returned schemas retain their canonical
    input order for stable prompt caching.
    """
    bounded = max(1, int(max_tools or MAX_VISIBLE_TOOLS))
    if len(tools) <= bounded:
        return list(tools)

    current = discover_tool_names_for_query(user_message)
    recent = list(recent_discovered_tools or ())
    recent_rank = {name: index for index, name in enumerate(recent)}
    required = set(required_tools or ())

    ranked: list[tuple[int, int, str]] = []
    for index, schema in enumerate(tools):
        name = _tool_name(schema)
        spec = BUILTIN_TOOL_REGISTRY.get(name)
        score = 0
        if name in required:
            score = 1_000
        elif name.startswith("mcp__"):
            score = 900
        elif spec and (command in spec.commands or trusted_skill in spec.skills):
            score = 850
        elif name in current:
            score = 800
        elif name in _ESSENTIAL_TOOL_NAMES:
            score = 750
        elif name in recent_rank:
            score = 700 + recent_rank[name]
        elif spec and spec.default_exposed:
            score = 500
        else:
            score = 100
        ranked.append((score, -index, name))

    selected = {
        name for _score, _neg_index, name
        in sorted(ranked, reverse=True)[:bounded]
    }
    limited = [schema for schema in tools if _tool_name(schema) in selected]
    log.info(
        "[tools] visibility cap applied before=%d after=%d dropped=%s",
        len(tools), len(limited),
        [_tool_name(schema) for schema in tools if _tool_name(schema) not in selected],
    )
    return limited
