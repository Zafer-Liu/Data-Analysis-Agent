"""Conversation-facing tools for published Workflows and durable Runs."""
from __future__ import annotations

import re
import time
from typing import Any, Mapping

from agent.workflows.models import WorkflowContractError, WorkflowErrorCode
from agent.workflows.features import workflow_feature_flags
from agent.workflows.runtime import workflow_runtime_manager
from agent.workflows.service import WorkflowService


_CREATE_VERBS = ("创建", "新建", "生成", "搭建", "create", "build")
_WORKFLOW_COMPLEXITY_SIGNALS = (
    "多数据源", "多个数据源", "交叉", "对比", "长报告", "报告", "审批", "复核",
    "风险", "异常", "清洗", "workflow", "工作流", "multi-source", "approval",
    "review", "audit", "report",
)
_TEMPLATE_ALIASES = {
    "analysis": "analysis",
    "经营": "analysis",
    "分析": "analysis",
    "insight": "insight",
    "洞察": "insight",
    "查询": "insight",
    "report": "report",
    "报告": "report",
    "cleaning_approval": "cleaning_approval",
    "清洗": "cleaning_approval",
    "数据治理": "cleaning_approval",
}
_CUSTOM_WORKFLOW_SIGNALS = ("自定义", "agent", "角色", "节点", "步骤", "流程", "template", "模板")
_CUSTOM_ALLOWED_TOOLS = frozenset({"get_schema", "query_data"})


def classify_workflow_request(message: str) -> dict[str, Any]:
    """Classify a request without auto-promoting ordinary chat into a DAG.

    The result is deliberately advisory. A Workflow is created only after an
    explicit create request, which keeps simple Q&A on the single-agent Loop.
    """
    text = str(message or "").strip()
    lowered = text.lower()
    reasons = [signal for signal in _WORKFLOW_COMPLEXITY_SIGNALS if signal in lowered or signal in text]
    explicit = "workflow" in lowered or "工作流" in text
    recommended = explicit or len(reasons) >= 2
    return {
        "route": "workflow" if recommended else "loop",
        "explicit_workflow": explicit,
        "reasons": reasons[:5],
        "auto_create": False,
    }


def _normalize_template(value: str) -> str:
    text = str(value or "analysis").strip().lower()
    if text in {"analysis", "insight", "report", "cleaning_approval"}:
        return text
    for alias, template in _TEMPLATE_ALIASES.items():
        if alias in text:
            return template
    raise WorkflowContractError(
        WorkflowErrorCode.GRAPH_INVALID,
        "workflow template must be analysis, insight, report, or cleaning_approval",
    )


def _template_from_request(text: str) -> str | None:
    """Infer a template only when the wording identifies one.

    Keeping this optional preserves the existing deterministic chat-create
    contract; absent a template, ``workflow_create`` uses ``analysis``.
    """
    lowered = str(text or "").lower()
    for alias, template in _TEMPLATE_ALIASES.items():
        if alias in lowered or alias in text:
            return template
    return None


def parse_workflow_create_request(message: str) -> dict[str, str] | None:
    """Return deterministic create arguments for an explicit chat request."""
    text = str(message or "").strip()
    lowered = text.lower()
    if not text or ("workflow" not in lowered and "工作流" not in text):
        return None
    if any(token in text for token in ("不要创建", "别创建", "无需创建")):
        return None
    if any(token in text for token in ("如何创建", "怎么创建", "怎样创建", "为什么创建")):
        return None
    # A detailed workflow definition must reach the model and the custom
    # workflow tool. Do not silently replace it with the analysis template.
    if any(token in lowered or token in text for token in _CUSTOM_WORKFLOW_SIGNALS):
        return None
    verb = next((item for item in _CREATE_VERBS if item in lowered), "")
    if not verb:
        return None

    mode = "full_auto"
    if any(token in lowered for token in ("key_approval", "关键审批", "审批模式")):
        mode = "key_approval"
    elif any(token in lowered for token in ("exception_review", "异常复核", "异常审核")):
        mode = "exception_review"

    workflow_pos = lowered.find("workflow")
    if workflow_pos < 0:
        workflow_pos = text.find("工作流")
    verb_pos = lowered.find(verb)
    candidate = text[verb_pos + len(verb):workflow_pos].strip(" ：:，,。")
    candidate = re.sub(r"^(?:一个|一套|新的?)\s*", "", candidate).strip()
    name = f"{candidate} Workflow" if candidate else "经营分析 Workflow"
    result = {
        "name": name,
        "mode": mode,
        "source_key": "source_snapshot",
    }
    template = _template_from_request(text)
    if template is not None and template != "analysis":
        result["template"] = template
    return result


def _workflow_mode(value: str) -> str:
    mode = str(value or "full_auto").strip() or "full_auto"
    aliases = {
        "auto": "full_auto",
        "自动": "full_auto",
        "全自动": "full_auto",
        "approval": "key_approval",
        "审批": "key_approval",
        "关键审批": "key_approval",
        "exception": "exception_review",
        "异常": "exception_review",
        "异常复核": "exception_review",
    }
    mode = aliases.get(mode, mode)
    if mode not in {"full_auto", "key_approval", "exception_review"}:
        raise WorkflowContractError(
            WorkflowErrorCode.GRAPH_INVALID,
            "workflow mode must be full_auto, key_approval, or exception_review",
        )
    return mode


def _create_template_graph(
    profile_ids: Mapping[str, str], *, mode: str, source_key: str, template: str,
) -> tuple[dict[str, Any], str]:
    """Build small, auditable graphs for the supported high-value scenarios."""
    approval_edge_type = "approval" if mode == "key_approval" else "auto"
    inspect_node = {
        "node_id": "inspect_data",
        "type": "agent",
        "agent_profile_id": profile_ids["inspect"],
        "input_contract": [source_key, "business_context"],
        "output_contract": ["data_quality_report"],
        "output_artifacts": {"data_quality_report": "validation"},
        "side_effects": ["read_data"],
        "limits": {"max_tokens": 384000, "max_run_seconds": 900, "max_tool_calls": 200},
    }
    if template == "insight":
        return ({
            "run_policy": {"mode": mode}, "entry_node_ids": ["inspect_data"],
            "nodes": [inspect_node, {
                "node_id": "analyze_metrics", "type": "agent",
                "agent_profile_id": profile_ids["metrics"],
                "input_contract": ["data_quality_report"], "output_contract": ["metric_analysis"],
                "output_artifacts": {"metric_analysis": "insight"},
                "side_effects": ["read_data"],
                "limits": {"max_tokens": 384000, "max_run_seconds": 900, "max_tool_calls": 200},
            }],
            "edges": [{"edge_id": "inspect-to-metrics", "from_node": "inspect_data", "to_node": "analyze_metrics", "type": "auto"}],
            "limits": {"max_run_minutes": 30, "max_total_node_runs": 8},
        }, "metric_analysis")
    if template == "report":
        return ({
            "run_policy": {"mode": mode}, "entry_node_ids": ["inspect_data"],
            "nodes": [inspect_node, {
                "node_id": "generate_report", "type": "agent",
                "agent_profile_id": profile_ids["reporter"],
                "input_contract": ["data_quality_report"],
                "output_contract": ["operating_report"],
                "output_artifacts": {"operating_report": "report"},
                "output_validation": {"forbidden_substrings": ["empty / not provided", "no upstream findings", "no verification report content"]},
                "limits": {"max_tokens": 384000, "max_run_seconds": 900, "max_tool_calls": 0},
            }],
            "edges": [{"edge_id": "inspect-to-report", "from_node": "inspect_data", "to_node": "generate_report", "type": approval_edge_type}],
            "limits": {"max_run_minutes": 45, "max_total_node_runs": 8},
        }, "operating_report")
    if template == "cleaning_approval":
        return ({
            "run_policy": {"mode": "key_approval" if mode == "full_auto" else mode},
            "entry_node_ids": ["validate_input"],
            "nodes": [{
                "node_id": "validate_input", "type": "validation",
                "input_contract": [source_key], "output_contract": ["validated_input"],
                "output_artifacts": {"validated_input": "validation"},
                "validation": {"required": [source_key], "non_empty": [source_key]},
            }, {
                "node_id": "propose_cleaning", "type": "agent",
                "agent_profile_id": profile_ids["cleaning"],
                "input_contract": ["validated_input", "business_context", "revision_request"], "output_contract": ["cleaning_plan"],
                "output_artifacts": {"cleaning_plan": "insight"},
                "side_effects": ["read_data"],
                "max_iterations": 3,
                "limits": {"max_tokens": 384000, "max_run_seconds": 900, "max_tool_calls": 200},
            }, {
                "node_id": "verify_cleaning", "type": "verifier",
                "agent_profile_id": profile_ids["cleaning_verifier"],
                "input_contract": ["cleaning_plan"], "output_contract": ["decision", "issues", "evidence"],
                "output_artifacts": {"decision": "validation", "issues": "validation", "evidence": "validation"},
                "verifier": {"standards": [
                    "The plan names exactly one existing source table and never overwrites it",
                    "Every proposed operation is fill_na, winsorize, or trimming with explicit fields and parameters",
                    "The plan includes expected impact and a rollback path",
                ]},
                "limits": {"max_tokens": 12000, "max_run_seconds": 300, "max_tool_calls": 0},
            }, {
                "node_id": "apply_cleaning", "type": "agent",
                "agent_profile_id": profile_ids["cleaning_executor"],
                "input_contract": ["cleaning_plan"], "output_contract": ["cleaning_execution"],
                "output_artifacts": {"cleaning_execution": "dataset"},
                "side_effects": ["write_data"],
                "limits": {"max_tokens": 12000, "max_run_seconds": 900, "max_tool_calls": 3},
            }, {
                "node_id": "generate_report", "type": "agent",
                "agent_profile_id": profile_ids["cleaning_reporter"],
                "input_contract": ["cleaning_plan", "cleaning_execution"], "output_contract": ["operating_report"],
                "output_artifacts": {"operating_report": "report"},
                "output_validation": {"forbidden_substrings": ["empty / not provided", "no upstream findings"]},
                "limits": {"max_tokens": 384000, "max_run_seconds": 900, "max_tool_calls": 0},
            }],
            "edges": [
                {"edge_id": "validate-to-plan", "from_node": "validate_input", "to_node": "propose_cleaning", "type": "auto"},
                {"edge_id": "plan-to-verify", "from_node": "propose_cleaning", "to_node": "verify_cleaning", "type": "auto"},
                {"edge_id": "plan-to-clean", "from_node": "propose_cleaning", "to_node": "apply_cleaning", "type": "auto"},
                {"edge_id": "verify-to-clean-approval", "from_node": "verify_cleaning", "to_node": "apply_cleaning", "type": "approval"},
                {"edge_id": "clean-to-report", "from_node": "apply_cleaning", "to_node": "generate_report", "type": "auto"},
                {"edge_id": "plan-to-report", "from_node": "propose_cleaning", "to_node": "generate_report", "type": "auto"},
                {"edge_id": "verify-rework", "from_node": "verify_cleaning", "to_node": "propose_cleaning", "type": "retry_loop", "max_iterations": 3},
            ], "limits": {"max_run_minutes": 60, "max_total_node_runs": 14},
        }, "operating_report")
    return {
        "run_policy": {"mode": mode},
        "entry_node_ids": ["inspect_data"],
        "nodes": [
            inspect_node,
            {
                "node_id": "analyze_metrics",
                "type": "agent",
                "agent_profile_id": profile_ids["metrics"],
                "input_contract": ["data_quality_report"],
                "output_contract": ["metric_analysis"],
                "output_artifacts": {"metric_analysis": "insight"},
                "side_effects": ["read_data"],
                "limits": {"max_tokens": 384000, "max_run_seconds": 900, "max_tool_calls": 200},
            },
            {
                "node_id": "analyze_anomalies",
                "type": "agent",
                "agent_profile_id": profile_ids["anomalies"],
                "input_contract": ["data_quality_report"],
                "output_contract": ["anomaly_analysis"],
                "output_artifacts": {"anomaly_analysis": "insight"},
                "side_effects": ["read_data"],
                "limits": {"max_tokens": 384000, "max_run_seconds": 900, "max_tool_calls": 200},
            },
            {
                "node_id": "verify_findings",
                "type": "agent",
                "agent_profile_id": profile_ids["reviewer"],
                "join_policy": "all_success",
                "input_contract": ["metric_analysis", "anomaly_analysis"],
                "output_contract": ["verification_report"],
                "output_artifacts": {"verification_report": "validation"},
                "limits": {"max_tokens": 384000, "max_run_seconds": 900, "max_tool_calls": 0},
            },
            {
                "node_id": "generate_report",
                "type": "agent",
                "agent_profile_id": profile_ids["reporter"],
                "input_contract": ["metric_analysis", "anomaly_analysis", "verification_report"],
                "output_contract": ["operating_report"],
                "output_artifacts": {"operating_report": "report"},
                "output_validation": {"forbidden_substrings": ["empty / not provided", "no verification report content", "no upstream findings"]},
                "limits": {"max_tokens": 384000, "max_run_seconds": 900, "max_tool_calls": 0},
            },
        ],
        "edges": [
            {"edge_id": "inspect-to-metrics", "from_node": "inspect_data", "to_node": "analyze_metrics", "type": "auto"},
            {"edge_id": "inspect-to-anomalies", "from_node": "inspect_data", "to_node": "analyze_anomalies", "type": "auto"},
            {"edge_id": "metrics-to-verify", "from_node": "analyze_metrics", "to_node": "verify_findings", "type": "auto"},
            {"edge_id": "anomalies-to-verify", "from_node": "analyze_anomalies", "to_node": "verify_findings", "type": "auto"},
            {"edge_id": "verify-to-report", "from_node": "verify_findings", "to_node": "generate_report", "type": approval_edge_type},
            {"edge_id": "metrics-to-report", "from_node": "analyze_metrics", "to_node": "generate_report", "type": "auto"},
            {"edge_id": "anomalies-to-report", "from_node": "analyze_anomalies", "to_node": "generate_report", "type": "auto"},
            {"edge_id": "verify-retry", "from_node": "verify_findings", "to_node": "analyze_metrics", "type": "retry_loop", "max_iterations": 2},
        ],
        "limits": {
            "max_run_minutes": 120,
            "max_total_node_runs": 30,
        },
    }, "operating_report"


def workflow_create(
    session_id: str,
    *,
    name: str = "经营分析 Workflow",
    description: str = "",
    mode: str = "full_auto",
    source_key: str = "source_snapshot",
    template: str = "analysis",
) -> dict[str, Any]:
    mode = _workflow_mode(mode)
    template = _normalize_template(template)
    source_key = str(source_key or "source_snapshot").strip() or "source_snapshot"
    workflow_name = str(name or "经营分析 Workflow").strip() or "经营分析 Workflow"
    workflow_description = str(description or "").strip() or f"{mode} 模式的 {template} Workflow 模板"
    suffix = f"{int(time.time() * 1000):x}"
    specs = [
        ("inspect", "数据检查员", "data_inspector", "识别数据表、字段质量和可用指标范围，直接输出一份非空的 data_quality_report。报告必须包含“数据质量”和“可用指标范围”两节；可使用 Markdown。", ["get_schema", "query_data"]),
        ("metrics", "指标分析师", "metric_analyst", "围绕业务目标执行 SQL/指标分析，输出 metric_analysis。", ["get_schema", "query_data"]),
        ("anomalies", "异常分析师", "anomaly_analyst", "发现波动、异常与可解释原因，输出 anomaly_analysis。", ["get_schema", "query_data"]),
        ("reviewer", "结论复核员", "finding_reviewer", "只使用上游 metric_analysis 与 anomaly_analysis 交叉复核。必须直接输出非空的 verification_report：先写“复核结论”，再列出已证实证据、待验证项和风险。材料不足时也必须明确写出“材料不足，不能确认”的非空复核结论；不得只输出思考过程、工具调用或空白。", []),
        ("reporter", "报告编辑", "report_editor", "输出完整、可供管理层使用的经营分析报告：管理摘要、经营表现与关键指标、结构表现与业务驱动、风险与数据可信度、管理建议与行动计划。把技术复核压缩为一小节，直接解释业务影响；不得输出 artifact ID、预览截断说明、表清单、原始计算、SQL 或字段级审计细节。直接返回 Markdown，不要用代码围栏。", []),
        ("cleaning", "数据清洗方案员", "data_cleaning_planner", "只提出待审批的可审计数据清洗方案，不得写入数据。方案必须列出源表、仅可执行的操作（fill_na、winsorize 或 trimming）、精确字段与参数、预期影响和回滚方式。若提供 revision_request，必须针对该要求重新制定方案。", ["get_schema", "query_data"]),
        ("cleaning_verifier", "清洗方案复核员", "data_cleaning_verifier", "独立复核清洗方案，不得修改方案或写入数据。只返回 JSON：decision（pass、rework 或 escalate）、issues（字符串数组）和 evidence（字符串数组）。只有方案满足全部安全标准时才返回 pass。", []),
        ("cleaning_executor", "数据清洗执行员", "data_cleaning_executor", "仅在上游 cleaning_plan 已获审批后执行。必须调用 clean_data 恰好一次，并且只执行获批方案中一个支持的操作：fill_na、winsorize 或 trimming。不得覆盖源表，output_table 固定为 cleaned_data；工具返回后如实输出 cleaning_execution，包含源表、操作、参数、结果表、实际影响与失败信息。", ["clean_data"]),
        ("cleaning_reporter", "清洗执行报告员", "data_cleaning_reporter", "仅依据获批方案和 cleaning_execution 写执行报告。必须清楚区分审批的计划与实际执行结果；只可陈述工具返回证实的写入。说明源表未覆盖、清洗结果保存在 cleaned_data，以及任何未执行或失败项。", []),
    ]
    with WorkflowService.for_session(session_id) as service:
        profile_ids: dict[str, str] = {}
        for key, profile_name, role, instructions, allowed_tools in specs:
            profile = service.create_agent_profile(
                key=f"conversation_workflow_{key}_{suffix}",
                name=profile_name,
                role=role,
                instructions=instructions,
                allowed_tools=allowed_tools,
                model_policy="inherit",
                created_by="conversation",
            )
            profile_ids[key] = str(profile["id"])
        graph, output_key = _create_template_graph(
            profile_ids, mode=mode, source_key=source_key, template=template,
        )
        workflow = service.create_workflow(
            name=workflow_name,
            description=workflow_description,
            graph=graph,
            input_schema={
                "type": "object",
                "properties": {
                    source_key: {"type": "string", "title": "数据来源或范围", "description": "例如：当前工作区数据、2026 年 7 月销售表"},
                    "business_context": {"type": "string", "title": "业务补充说明", "description": "字段单位、口径、业务目标或需重点核查的问题"},
                    "revision_request": {"type": "string", "title": "补充清洗要求", "description": "仅在审批要求重做时自动带入新的方案节点"},
                },
                "required": [source_key],
            },
            output_schema={
                "type": "object",
                "properties": {output_key: {"type": "string"}},
                "required": [output_key],
            },
            created_by="conversation",
        )
        validation = service.validate_draft(workflow["id"])
        published = service.publish(workflow["id"], published_by="conversation")
    return {
        "workflow": {
            "id": workflow["id"],
            "name": workflow["name"],
            "description": workflow["description"],
            "mode": mode,
            "template": template,
            "source_key": source_key,
            "output_key": output_key,
        },
        "version": published["version"],
        "validation": validation,
        "profile_ids": profile_ids,
        "feature_flags": workflow_feature_flags(),
        "next_actions": [
            "Use workflow_start with workflow_id or version_id to run it.",
            "Open the Teams panel Workflow tab to inspect the DAG, approvals, and materials.",
        ],
    }


def _normalize_custom_agents(agents: Any) -> list[dict[str, Any]]:
    if not isinstance(agents, list) or not 1 <= len(agents) <= 8:
        raise WorkflowContractError(WorkflowErrorCode.GRAPH_INVALID, "custom workflow requires 1 to 8 agents")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(agents):
        if not isinstance(raw, Mapping):
            raise WorkflowContractError(WorkflowErrorCode.GRAPH_INVALID, f"agent {index + 1} must be an object")
        name = str(raw.get("name") or f"Agent {index + 1}").strip()
        role = str(raw.get("role") or "workflow_specialist").strip()
        instructions = str(raw.get("instructions") or "").strip()
        tools = raw.get("allowed_tools", raw.get("tools", []))
        if isinstance(tools, str):
            tools = [item.strip() for item in tools.split(",") if item.strip()]
        if not isinstance(tools, list) or any(not isinstance(item, str) for item in tools):
            raise WorkflowContractError(WorkflowErrorCode.GRAPH_INVALID, f"{name} tools must be a string array")
        invalid = sorted(set(tools) - _CUSTOM_ALLOWED_TOOLS)
        if not instructions:
            raise WorkflowContractError(WorkflowErrorCode.GRAPH_INVALID, f"{name} instructions are required")
        if invalid:
            raise WorkflowContractError(WorkflowErrorCode.GRAPH_INVALID, f"{name} has unsupported tools: {', '.join(invalid)}")
        dependencies = raw.get("depends_on", raw.get("dependsOn", []))
        if isinstance(dependencies, str):
            dependencies = [item.strip() for item in dependencies.split(",") if item.strip()]
        if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies):
            raise WorkflowContractError(WorkflowErrorCode.GRAPH_INVALID, f"{name} depends_on must be a string array")
        normalized.append({"name": name, "role": role, "instructions": instructions, "allowed_tools": list(dict.fromkeys(tools)), "depends_on": list(dict.fromkeys(item.strip() for item in dependencies if item.strip()))})
    names: set[str] = set()
    for agent in normalized:
        if agent["name"] in names:
            raise WorkflowContractError(WorkflowErrorCode.GRAPH_INVALID, f"duplicate custom agent name: {agent['name']}")
        invalid_dependencies = sorted(set(agent["depends_on"]) - names)
        if invalid_dependencies:
            raise WorkflowContractError(WorkflowErrorCode.GRAPH_INVALID, f"{agent['name']} dependencies must reference earlier agents: {', '.join(invalid_dependencies)}")
        names.add(agent["name"])
    return normalized


def workflow_create_custom(
    session_id: str, *, name: str, description: str = "", mode: str = "full_auto",
    source_key: str = "source_snapshot", agents: Any = None,
) -> dict[str, Any]:
    """Create and publish an auditable, user-described serial Agent Workflow."""
    mode = _workflow_mode(mode)
    source_key = str(source_key or "source_snapshot").strip() or "source_snapshot"
    workflow_name = str(name or "自定义 Agent Workflow").strip() or "自定义 Agent Workflow"
    normalized_agents = _normalize_custom_agents(agents)
    agent_index_by_name = {agent["name"]: index for index, agent in enumerate(normalized_agents)}
    suffix = f"{int(time.time() * 1000):x}"
    with WorkflowService.for_session(session_id) as service:
        profile_ids: dict[str, str] = {}
        nodes: list[dict[str, Any]] = []
        for index, agent in enumerate(normalized_agents):
            key = f"custom_{index}"
            profile = service.create_agent_profile(
                key=f"conversation_workflow_{key}_{suffix}", name=agent["name"], role=agent["role"],
                instructions=agent["instructions"], allowed_tools=agent["allowed_tools"],
                model_policy="inherit", created_by="conversation",
            )
            profile_ids[key] = str(profile["id"])
            is_last = index == len(normalized_agents) - 1
            output_key = "workflow_result" if is_last else f"custom_agent_{index + 1}_output"
            dependency_indexes = [agent_index_by_name[item] for item in agent["depends_on"]]
            nodes.append({
                "node_id": f"custom_agent_{index + 1}", "type": "agent", "agent_profile_id": profile_ids[key],
                "input_contract": [source_key, "business_context"] if not dependency_indexes else [f"custom_agent_{item + 1}_output" for item in dependency_indexes],
                "output_contract": [output_key],
                "output_artifacts": {output_key: "report" if is_last else "insight"},
                "side_effects": ["read_data"] if agent["allowed_tools"] else [],
                "limits": {"max_tokens": 384000, "max_run_seconds": 900, "max_tool_calls": 200 if agent["allowed_tools"] else 0},
            })
        graph = {
            "run_policy": {"mode": mode}, "entry_node_ids": [f"custom_agent_{index + 1}" for index, agent in enumerate(normalized_agents) if not agent["depends_on"]], "nodes": nodes,
            "edges": [{"edge_id": f"custom-{agent_index_by_name[source] + 1}-to-{index + 1}", "from_node": f"custom_agent_{agent_index_by_name[source] + 1}", "to_node": f"custom_agent_{index + 1}", "type": "auto"} for index, agent in enumerate(normalized_agents) for source in agent["depends_on"]],
            "limits": {"max_run_minutes": 120, "max_total_node_runs": max(8, len(nodes) * 3)},
        }
        workflow = service.create_workflow(
            name=workflow_name,
            description=str(description or "").strip() or "通过对话定义的自定义 Agent Workflow",
            graph=graph,
            input_schema={"type": "object", "properties": {
                source_key: {"type": "string", "title": "数据来源或范围", "description": "例如：当前工作区数据、2026 年 7 月销售表"},
                "business_context": {"type": "string", "title": "业务补充说明", "description": "字段单位、口径、业务目标或需重点核查的问题"},
            }, "required": [source_key]},
            output_schema={"type": "object", "properties": {"workflow_result": {"type": "string"}}, "required": ["workflow_result"]},
            created_by="conversation",
        )
        validation = service.validate_draft(workflow["id"])
        published = service.publish(workflow["id"], published_by="conversation")
    return {"workflow": {"id": workflow["id"], "name": workflow["name"], "description": workflow["description"], "mode": mode, "template": "custom", "source_key": source_key, "output_key": "workflow_result"}, "version": published["version"], "validation": validation, "profile_ids": profile_ids, "next_actions": ["Use workflow_start with workflow_id or version_id to run it.", "Open the Teams panel Workflow tab to inspect the generated Agent DAG."]}


def workflow_list(session_id: str) -> dict[str, Any]:
    workflows = []
    with WorkflowService.for_session(session_id) as service:
        for workflow in service.list_workflows():
            version_id = str(workflow.get("current_version_id") or "")
            if not version_id:
                continue
            version = service.store.get_version(version_id)
            workflows.append({
                "id": workflow["id"],
                "name": workflow["name"],
                "description": workflow["description"],
                "version_id": version_id,
                "version_number": version.get("version_number") if version else None,
            })
    return {"workflows": workflows, "count": len(workflows)}


def _resolve_version(runtime, workflow_ref: str) -> str:
    reference = str(workflow_ref or "").strip()
    if not reference:
        raise WorkflowContractError(
            WorkflowErrorCode.RESOURCE_NOT_FOUND,
            "workflow id, name, or version id is required",
        )
    version = runtime.workflow_store.get_version(reference)
    if version is not None:
        return version["id"]
    matches = [
        workflow
        for workflow in runtime.workflow_store.list_workflows()
        if workflow["id"] == reference or workflow["name"] == reference
    ]
    if len(matches) != 1 or not matches[0].get("current_version_id"):
        raise WorkflowContractError(
            WorkflowErrorCode.RESOURCE_NOT_FOUND,
            f"published workflow not found or ambiguous: {reference}",
        )
    return str(matches[0]["current_version_id"])


def workflow_start(
    session_id: str,
    workflow_ref: str,
    inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    runtime = workflow_runtime_manager.get(session_id)
    return runtime.scheduler.start(
        workflow_version_id=_resolve_version(runtime, workflow_ref),
        session_id=session_id,
        inputs=inputs or {},
        started_by="conversation",
    )


def workflow_status(session_id: str, run_id: str) -> dict[str, Any]:
    runtime = workflow_runtime_manager.get(session_id)
    return runtime.scheduler.advance(str(run_id or "").strip())


def execute_workflow_tool(
    name: str,
    session_id: str,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    if name == "workflow_create":
        return workflow_create(
            session_id,
            name=str(args.get("name") or "经营分析 Workflow"),
            description=str(args.get("description") or ""),
            mode=str(args.get("mode") or "full_auto"),
            source_key=str(args.get("source_key") or args.get("sourceKey") or "source_snapshot"),
            template=str(args.get("template") or "analysis"),
        )
    if name == "workflow_create_custom":
        return workflow_create_custom(
            session_id,
            name=str(args.get("name") or "自定义 Agent Workflow"),
            description=str(args.get("description") or ""),
            mode=str(args.get("mode") or "full_auto"),
            source_key=str(args.get("source_key") or args.get("sourceKey") or "source_snapshot"),
            agents=args.get("agents"),
        )
    if name == "workflow_list":
        return workflow_list(session_id)
    if name == "workflow_start":
        inputs = args.get("inputs")
        return workflow_start(
            session_id,
            str(
                args.get("workflow_version_id")
                or args.get("workflow_id")
                or args.get("name")
                or ""
            ),
            inputs if isinstance(inputs, Mapping) else {},
        )
    if name == "workflow_status":
        return workflow_status(session_id, str(args.get("run_id") or ""))
    raise ValueError(f"unknown workflow tool: {name}")
