"""Stable WF0 contracts for deterministic workflow execution."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from agent.workflows.features import workflow_feature_enabled


class WorkflowErrorCode(str, Enum):
    GRAPH_INVALID = "workflow_graph_invalid"
    INVALID_TRANSITION = "workflow_invalid_transition"
    RESOURCE_NOT_FOUND = "workflow_resource_not_found"
    WORKSPACE_MISMATCH = "workflow_workspace_mismatch"
    VERSION_CONFLICT = "workflow_version_conflict"
    IDEMPOTENCY_CONFLICT = "workflow_idempotency_conflict"
    PERMISSION_DENIED = "workflow_permission_denied"
    RUN_NOT_RECOVERABLE = "workflow_run_not_recoverable"
    ITERATION_LIMIT_REACHED = "workflow_iteration_limit_reached"
    CONCURRENCY_LIMIT_REACHED = "workflow_concurrency_limit_reached"
    OUTPUT_CONTRACT_VIOLATION = "workflow_output_contract_violation"
    APPROVAL_ALREADY_DECIDED = "workflow_approval_already_decided"


class WorkflowContractError(ValueError):
    """A stable machine-readable workflow contract failure."""

    def __init__(self, code: WorkflowErrorCode, message: str):
        super().__init__(message)
        self.code = code


class RunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    CANCELING = "canceling"
    CANCELED = "canceled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class NodeRunStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    QUEUED = "queued"
    RUNNING = "running"
    OUTPUT_READY = "output_ready"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELED = "canceled"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    DECIDED = "decided"
    CANCELED = "canceled"


class EdgeType(str, Enum):
    AUTO = "auto"
    CONDITIONAL = "conditional"
    APPROVAL = "approval"
    RETRY_LOOP = "retry_loop"


class WorkflowRunMode(str, Enum):
    FULL_AUTO = "full_auto"
    KEY_APPROVAL = "key_approval"
    EXCEPTION_REVIEW = "exception_review"


RUN_TERMINAL_STATUSES = frozenset({
    RunStatus.CANCELED,
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
})

NODE_RUN_TERMINAL_STATUSES = frozenset({
    NodeRunStatus.SUCCEEDED,
    NodeRunStatus.REJECTED,
    NodeRunStatus.SKIPPED,
    NodeRunStatus.FAILED,
    NodeRunStatus.CANCELED,
})

APPROVAL_TERMINAL_STATUSES = frozenset({
    ApprovalStatus.DECIDED,
    ApprovalStatus.CANCELED,
})

RUN_TRANSITIONS = {
    RunStatus.CREATED: frozenset({
        RunStatus.RUNNING,
        RunStatus.CANCELING,
        RunStatus.CANCELED,
        RunStatus.FAILED,
    }),
    RunStatus.RUNNING: frozenset({
        RunStatus.WAITING_APPROVAL,
        RunStatus.PAUSED,
        RunStatus.CANCELING,
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
    }),
    RunStatus.WAITING_APPROVAL: frozenset({
        RunStatus.RUNNING,
        RunStatus.PAUSED,
        RunStatus.CANCELING,
        RunStatus.FAILED,
    }),
    RunStatus.PAUSED: frozenset({
        RunStatus.RUNNING,
        RunStatus.CANCELING,
        RunStatus.CANCELED,
        RunStatus.FAILED,
    }),
    RunStatus.CANCELING: frozenset({
        RunStatus.CANCELED,
        RunStatus.FAILED,
    }),
    RunStatus.CANCELED: frozenset(),
    RunStatus.SUCCEEDED: frozenset(),
    # A failed Run may only be reopened by the explicit manual retry path.
    # The scheduler validates that a latest failed NodeRun is selected first.
    RunStatus.FAILED: frozenset({RunStatus.RUNNING}),
}

NODE_RUN_TRANSITIONS = {
    NodeRunStatus.PENDING: frozenset({
        NodeRunStatus.READY,
        NodeRunStatus.SKIPPED,
        NodeRunStatus.CANCELED,
    }),
    NodeRunStatus.READY: frozenset({
        NodeRunStatus.QUEUED,
        NodeRunStatus.SKIPPED,
        NodeRunStatus.FAILED,
        NodeRunStatus.SKIPPED,
        NodeRunStatus.CANCELED,
    }),
    NodeRunStatus.QUEUED: frozenset({
        NodeRunStatus.RUNNING,
        NodeRunStatus.FAILED,
        NodeRunStatus.CANCELED,
    }),
    NodeRunStatus.RUNNING: frozenset({
        NodeRunStatus.OUTPUT_READY,
        NodeRunStatus.FAILED,
        NodeRunStatus.CANCELED,
    }),
    NodeRunStatus.OUTPUT_READY: frozenset({
        NodeRunStatus.WAITING_APPROVAL,
        NodeRunStatus.SUCCEEDED,
        NodeRunStatus.FAILED,
        NodeRunStatus.CANCELED,
    }),
    NodeRunStatus.WAITING_APPROVAL: frozenset({
        NodeRunStatus.SUCCEEDED,
        NodeRunStatus.REJECTED,
        NodeRunStatus.FAILED,
        NodeRunStatus.SKIPPED,
        NodeRunStatus.CANCELED,
    }),
    NodeRunStatus.SUCCEEDED: frozenset(),
    NodeRunStatus.REJECTED: frozenset(),
    NodeRunStatus.SKIPPED: frozenset(),
    NodeRunStatus.FAILED: frozenset(),
    NodeRunStatus.CANCELED: frozenset(),
}

APPROVAL_TRANSITIONS = {
    ApprovalStatus.PENDING: frozenset({
        ApprovalStatus.DECIDED,
        ApprovalStatus.CANCELED,
    }),
    ApprovalStatus.DECIDED: frozenset(),
    ApprovalStatus.CANCELED: frozenset(),
}

ALLOWED_JOIN_POLICIES = frozenset({"all_success", "all_terminal"})
ALLOWED_REJECT_POLICIES = frozenset({"fail_run", "close_branch"})
ALLOWED_NODE_TYPES = frozenset({"agent", "validation", "router", "sql", "export", "verifier"})
ALLOWED_ARTIFACT_TYPES = frozenset({"dataset", "chart", "insight", "validation", "report", "sql"})
ALLOWED_SIDE_EFFECTS = frozenset({"read_data", "write_data", "export_file", "network"})
NODE_LIMIT_RANGES = {
    # Modern configured models expose up to a 384K completion window. This is
    # a ceiling, not a reservation: generation still ends once the node emits
    # its contracted output.
    "max_tokens": (400, 384000),
    "max_run_seconds": (10, 900),
    # Delegated execution is capped at 50 rounds × 4 calls per round.
    "max_tool_calls": (0, 200),
}


def _coerce_status(value: Any, enum_type: type[Enum], label: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowContractError(
            WorkflowErrorCode.INVALID_TRANSITION,
            f"unknown {label} status: {value}",
        ) from exc


def can_transition_run(current: RunStatus | str, target: RunStatus | str) -> bool:
    source = _coerce_status(current, RunStatus, "run")
    destination = _coerce_status(target, RunStatus, "run")
    return destination in RUN_TRANSITIONS[source]


def can_transition_node_run(
    current: NodeRunStatus | str,
    target: NodeRunStatus | str,
) -> bool:
    source = _coerce_status(current, NodeRunStatus, "node run")
    destination = _coerce_status(target, NodeRunStatus, "node run")
    return destination in NODE_RUN_TRANSITIONS[source]


def can_transition_approval(
    current: ApprovalStatus | str,
    target: ApprovalStatus | str,
) -> bool:
    source = _coerce_status(current, ApprovalStatus, "approval")
    destination = _coerce_status(target, ApprovalStatus, "approval")
    return destination in APPROVAL_TRANSITIONS[source]


@dataclass(frozen=True)
class AgentProfile:
    """An immutable workspace-scoped agent capability revision."""

    id: str
    workspace_id: str
    key: str
    revision: int
    name: str
    role: str
    instructions: str
    allowed_tools: tuple[str, ...]
    model_policy: str = "inherit"

    def __post_init__(self) -> None:
        for field_name in ("id", "workspace_id", "key", "name", "role"):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"agent profile {field_name} is required")
        if self.revision < 1:
            raise ValueError("agent profile revision must be positive")
        normalized = tuple(dict.fromkeys(
            str(tool).strip() for tool in self.allowed_tools if str(tool).strip()
        ))
        if normalized != self.allowed_tools:
            object.__setattr__(self, "allowed_tools", normalized)


def _graph_error(message: str) -> WorkflowContractError:
    return WorkflowContractError(WorkflowErrorCode.GRAPH_INVALID, message)


def _required_text(item: Mapping[str, Any], key: str, label: str) -> str:
    value = str(item.get(key) or "").strip()
    if not value:
        raise _graph_error(f"{label} requires {key}")
    return value


def _validate_retry_edge(edge: Mapping[str, Any], edge_id: str) -> None:
    raw_limit = edge.get("max_iterations")
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or raw_limit < 1:
        raise _graph_error(
            f"retry_loop edge {edge_id} requires a positive max_iterations"
        )


def _validate_acyclic_forward_graph(
    node_ids: set[str],
    edges: list[Mapping[str, Any]],
) -> None:
    outgoing = {node_id: [] for node_id in node_ids}
    indegree = {node_id: 0 for node_id in node_ids}
    for edge in edges:
        if edge.get("type") == EdgeType.RETRY_LOOP.value:
            continue
        source = str(edge["from_node"])
        target = str(edge["to_node"])
        outgoing[source].append(target)
        indegree[target] += 1

    ready = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        current = ready.pop()
        visited += 1
        for target in outgoing[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(node_ids):
        raise _graph_error("auto and approval edges must form an acyclic graph")


def _validate_reachable(
    node_ids: set[str],
    entries: list[str],
    edges: list[Mapping[str, Any]],
) -> None:
    outgoing = {node_id: [] for node_id in node_ids}
    for edge in edges:
        outgoing[str(edge["from_node"])].append(str(edge["to_node"]))
    reachable = set(entries)
    queue = list(entries)
    while queue:
        current = queue.pop()
        for target in outgoing[current]:
            if target not in reachable:
                reachable.add(target)
                queue.append(target)
    missing = sorted(node_ids - reachable)
    if missing:
        raise _graph_error(f"unreachable nodes: {', '.join(missing)}")


def validate_workflow_graph(graph: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return the graph without mutating caller-owned data."""
    if not isinstance(graph, Mapping):
        raise _graph_error("workflow graph must be an object")

    raw_nodes = graph.get("nodes")
    raw_edges = graph.get("edges")
    raw_entries = graph.get("entry_node_ids")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise _graph_error("workflow graph requires at least one node")
    if not isinstance(raw_edges, list):
        raise _graph_error("workflow graph edges must be an array")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise _graph_error("workflow graph requires at least one entry node")

    nodes: list[Mapping[str, Any]] = []
    node_ids: set[str] = set()
    for raw_node in raw_nodes:
        if not isinstance(raw_node, Mapping):
            raise _graph_error("each workflow node must be an object")
        node_id = _required_text(raw_node, "node_id", "workflow node")
        if node_id in node_ids:
            raise _graph_error(f"duplicate node_id: {node_id}")
        node_type = str(raw_node.get("type") or "agent")
        if node_type not in ALLOWED_NODE_TYPES:
            raise _graph_error(f"unsupported node type for {node_id}: {node_type}")
        if node_type in {"validation", "router", "sql", "export"} and not workflow_feature_enabled(
            "deterministic_nodes"
        ):
            raise _graph_error(
                f"deterministic node {node_id} is disabled by workflow feature flag"
            )
        if node_type == "verifier" and not workflow_feature_enabled("verifier_nodes"):
            raise _graph_error(
                f"verifier node {node_id} is disabled by workflow feature flag"
            )
        if node_type in {"agent", "verifier"}:
            _required_text(raw_node, "agent_profile_id", f"agent node {node_id}")
        elif "agent_profile_id" in raw_node:
            raise _graph_error(
                f"deterministic node {node_id} must not declare agent_profile_id"
            )
        if node_type == "validation":
            validation = raw_node.get("validation")
            if not isinstance(validation, Mapping):
                raise _graph_error(f"validation node {node_id} requires validation")
            for key in ("required", "non_empty"):
                values = validation.get(key, [])
                if not isinstance(values, list) or any(
                    not isinstance(item, str) or not item.strip() for item in values
                ):
                    raise _graph_error(
                        f"validation node {node_id} {key} must be a string array"
                    )
            for key in ("field_types", "min_items", "max_items", "equals"):
                values = validation.get(key, {})
                if not isinstance(values, Mapping) or any(
                    not isinstance(name, str) or not name.strip()
                    for name in values
                ):
                    raise _graph_error(
                        f"validation node {node_id} {key} must be an object"
                    )
            for key in ("min_items", "max_items"):
                if any(
                    isinstance(value, bool) or not isinstance(value, int) or value < 0
                    for value in dict(validation.get(key, {})).values()
                ):
                    raise _graph_error(
                        f"validation node {node_id} {key} values must be non-negative integers"
                    )
        if node_type == "router":
            router = raw_node.get("router")
            if not isinstance(router, Mapping):
                raise _graph_error(f"router node {node_id} requires router")
            _required_text(router, "input", f"router node {node_id}")
        if node_type == "sql":
            _required_text(raw_node, "sql", f"sql node {node_id}")
        if node_type == "export":
            export = raw_node.get("export")
            if not isinstance(export, Mapping):
                raise _graph_error(f"export node {node_id} requires export")
            _required_text(export, "source", f"export node {node_id}")
            if str(export.get("format") or "markdown") not in {"markdown", "json", "text"}:
                raise _graph_error(
                    f"export node {node_id} format must be markdown, json, or text"
                )
        if node_type == "verifier":
            verifier = raw_node.get("verifier")
            if not isinstance(verifier, Mapping):
                raise _graph_error(f"verifier node {node_id} requires verifier")
            standards = verifier.get("standards")
            if not isinstance(standards, list) or not standards or any(
                not isinstance(item, str) or not item.strip() for item in standards
            ):
                raise _graph_error(
                    f"verifier node {node_id} standards must be a non-empty string array"
                )
        join_policy = str(raw_node.get("join_policy") or "all_success")
        if join_policy not in ALLOWED_JOIN_POLICIES:
            raise _graph_error(f"unsupported join_policy for {node_id}: {join_policy}")
        on_reject = str(raw_node.get("on_reject") or "fail_run")
        if on_reject not in ALLOWED_REJECT_POLICIES:
            raise _graph_error(f"unsupported on_reject for {node_id}: {on_reject}")
        for contract_key in ("input_contract", "output_contract"):
            contract = raw_node.get(contract_key, [])
            if not isinstance(contract, list) or any(
                not isinstance(item, str) or not item.strip() for item in contract
            ):
                raise _graph_error(f"{node_id} {contract_key} must be a string array")
        output_validation = raw_node.get("output_validation", {})
        if not isinstance(output_validation, Mapping):
            raise _graph_error(f"{node_id} output_validation must be an object")
        forbidden = output_validation.get("forbidden_substrings", [])
        if not isinstance(forbidden, list) or any(
            not isinstance(item, str) or not item.strip() for item in forbidden
        ):
            raise _graph_error(
                f"{node_id} output_validation.forbidden_substrings must be a string array"
            )
        artifact_types = raw_node.get("output_artifacts", {})
        if not isinstance(artifact_types, Mapping) or any(
            key not in raw_node.get("output_contract", [])
            or value not in ALLOWED_ARTIFACT_TYPES
            for key, value in artifact_types.items()
        ):
            raise _graph_error(
                f"{node_id} output_artifacts must map declared outputs to known artifact types"
            )
        side_effects = raw_node.get("side_effects", [])
        if not isinstance(side_effects, list) or any(
            not isinstance(item, str) or item not in ALLOWED_SIDE_EFFECTS
            for item in side_effects
        ):
            raise _graph_error(
                f"{node_id} side_effects must be known capability names"
            )
        if node_type in {"validation", "router", "sql"} and any(
            item in {"write_data", "export_file", "network"} for item in side_effects
        ):
            raise _graph_error(
                f"deterministic node {node_id} cannot declare external side effects"
            )
        if node_type == "export" and "export_file" not in side_effects:
            raise _graph_error(
                f"export node {node_id} must declare export_file side effect"
            )
        node_limits = raw_node.get("limits", {})
        if not isinstance(node_limits, Mapping):
            raise _graph_error(f"{node_id} limits must be an object")
        for key, (minimum, maximum) in NODE_LIMIT_RANGES.items():
            value = node_limits.get(key)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not minimum <= value <= maximum
            ):
                raise _graph_error(
                    f"{node_id} {key} must be an integer between {minimum} and {maximum}"
                )
        node_ids.add(node_id)
        nodes.append(raw_node)

    entries: list[str] = []
    for value in raw_entries:
        node_id = str(value or "").strip()
        if not node_id or node_id not in node_ids:
            raise _graph_error(f"unknown entry node: {value}")
        if node_id not in entries:
            entries.append(node_id)

    edges: list[Mapping[str, Any]] = []
    edge_ids: set[str] = set()
    for raw_edge in raw_edges:
        if not isinstance(raw_edge, Mapping):
            raise _graph_error("each workflow edge must be an object")
        edge_id = _required_text(raw_edge, "edge_id", "workflow edge")
        if edge_id in edge_ids:
            raise _graph_error(f"duplicate edge_id: {edge_id}")
        source = _required_text(raw_edge, "from_node", f"edge {edge_id}")
        target = _required_text(raw_edge, "to_node", f"edge {edge_id}")
        if source not in node_ids or target not in node_ids:
            raise _graph_error(f"edge {edge_id} references an unknown node")
        try:
            edge_type = EdgeType(str(raw_edge.get("type") or EdgeType.AUTO.value))
        except ValueError as exc:
            raise _graph_error(f"unsupported edge type for {edge_id}") from exc
        if source == target:
            raise _graph_error(f"self edge is not allowed: {edge_id}")
        if edge_type is EdgeType.RETRY_LOOP:
            _validate_retry_edge(raw_edge, edge_id)
        if edge_type is EdgeType.CONDITIONAL:
            if not workflow_feature_enabled("conditional_edges"):
                raise _graph_error(
                    f"conditional edge {edge_id} is disabled by workflow feature flag"
                )
            condition = raw_edge.get("condition")
            if not isinstance(condition, Mapping):
                raise _graph_error(f"conditional edge {edge_id} requires condition")
            _required_text(condition, "field", f"conditional edge {edge_id}")
            if "equals" not in condition or isinstance(condition["equals"], (Mapping, list)):
                raise _graph_error(f"conditional edge {edge_id} requires scalar equals")
        edge_ids.add(edge_id)
        edges.append(raw_edge)

    _validate_acyclic_forward_graph(node_ids, edges)
    _validate_reachable(node_ids, entries, edges)

    nodes_by_id = {str(item["node_id"]): item for item in nodes}
    # External writes are irreversible enough that a graph must make the
    # independent verification and human authorization explicit.  This is a
    # schema invariant, rather than a template convention, so custom graphs
    # cannot accidentally bypass the same safety gate.
    high_risk_nodes = [
        str(item["node_id"])
        for item in nodes
        if set(item.get("side_effects") or []) & {"write_data", "export_file", "network"}
    ]
    for node_id in high_risk_nodes:
        verifier_approvals = [
            edge for edge in edges
            if edge.get("type") == EdgeType.APPROVAL.value
            and str(edge.get("to_node")) == node_id
            and str(nodes_by_id[str(edge.get("from_node"))].get("type")) == "verifier"
        ]
        if not verifier_approvals:
            raise _graph_error(
                f"high-risk side-effect node {node_id} requires an incoming approval edge from a verifier"
            )

    for raw_node in nodes:
        max_attempts = raw_node.get("max_attempts")
        if max_attempts is not None and (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or max_attempts < 1
        ):
            raise _graph_error(
                f"{raw_node['node_id']} max_attempts must be a positive integer"
            )

    run_policy = graph.get("run_policy", {})
    if not isinstance(run_policy, Mapping):
        raise _graph_error("workflow run_policy must be an object")
    raw_mode = run_policy.get("mode")
    if raw_mode is not None:
        try:
            WorkflowRunMode(str(raw_mode))
        except ValueError as exc:
            raise _graph_error(
                "run_policy.mode must be full_auto, key_approval, or exception_review"
            ) from exc
    if high_risk_nodes and raw_mode != WorkflowRunMode.KEY_APPROVAL.value:
        raise _graph_error(
            "graphs with high-risk side effects require run_policy.mode=key_approval"
        )

    limits = graph.get("limits", {})
    if not isinstance(limits, Mapping):
        raise _graph_error("workflow limits must be an object")
    for key in ("max_run_minutes", "max_total_node_runs", "max_total_tokens", "max_concurrent_node_runs"):
        value = limits.get(key)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1
        ):
            raise _graph_error(f"{key} must be a positive integer")

    return dict(graph)
