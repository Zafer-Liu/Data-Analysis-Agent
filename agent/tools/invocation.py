"""Shared execution authorization, independent of prompt visibility/ranking."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from agent.activation import ActivationContext
from .registry import BUILTIN_TOOL_REGISTRY, ToolRegistry, ToolSpec


class ToolAuthorizationError(PermissionError):
    def __init__(self, tool: str, code: str, detail: str) -> None:
        self.tool, self.code = tool, code
        super().__init__(f"Unauthorized tool {tool}: {detail}")


@dataclass(frozen=True)
class ToolInvocationPolicy:
    activation: ActivationContext | None = None
    command: str = ""
    trusted_skill: str = ""
    skill_allowed_tools: frozenset[str] | None = None
    discovered_tools: frozenset[str] = frozenset()
    has_data_source: bool = False
    has_workspace: bool = False
    has_runtime: bool = True
    teams_enabled: bool = False
    allowed_tools: frozenset[str] | None = None
    allowed_mcp_tools: frozenset[str] = frozenset()
    # Selected MCP names alone do not establish their side-effect safety.
    mcp_specs: Mapping[str, ToolSpec] | None = None
    read_only: bool = False
    action_restrictions: Mapping[str, frozenset[str]] | None = None
    explicit_request_tools: frozenset[str] = frozenset()
    workflow_approved_tools: frozenset[str] = frozenset()
    parent: "ToolInvocationPolicy | None" = None

    def child(self, **restrictions) -> "ToolInvocationPolicy":
        """Derive narrower capability policy; every parent is still checked."""
        if "parent" in restrictions:
            raise ValueError("child policy cannot replace its parent")
        return replace(self, parent=self, **restrictions)

    def authorize(self, name: str, args: Mapping | None = None, *, registry=BUILTIN_TOOL_REGISTRY) -> ToolSpec:
        return authorize_tool_invocation(name, args, policy=self, registry=registry)


def _read_only_query(name: str, args: Mapping) -> None:
    if name != "query_data":
        return
    try:
        import sqlglot
        from sqlglot import expressions as exp
        statements = sqlglot.parse(str(args.get("sql") or ""), dialect="duckdb", error_level=sqlglot.ErrorLevel.RAISE)
        if (len(statements) != 1 or not isinstance(statements[0], exp.Query)
                or any(isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Merge, exp.Into))
                       for node in statements[0].walk())):
            raise ValueError("read-only policy requires one SELECT query")
    except Exception as exc:
        raise ToolAuthorizationError(name, "read_only", "only a single read-only query is authorized") from exc


def authorize_tool_invocation(
    name: str, args: Mapping | None = None, *, policy: ToolInvocationPolicy,
    registry: ToolRegistry = BUILTIN_TOOL_REGISTRY,
) -> ToolSpec:
    """Return the authorized spec or raise a stable, transport-neutral denial.

    ``allowed_tools`` is an optional capability ceiling, never the ranked list
    shown to a model. Confirmation grants are server-resolved, per-tool facts;
    a model's own argument cannot claim an explicit user/workflow approval.
    """
    name = str(name or "").strip()
    if args is not None and not isinstance(args, Mapping):
        raise ToolAuthorizationError(name, "invalid_arguments", "arguments must be an object")
    arguments = args if args is not None else {}
    if policy.parent is not None:
        authorize_tool_invocation(name, arguments, policy=policy.parent, registry=registry)
    spec = registry.get(name)
    action = str(arguments.get("action") or ("get" if name == "agent_task" else "")).strip().lower()
    if spec is not None and name == "agent_task":
        # Registry metadata describes the coarse dispatcher. Its read actions
        # must remain usable by read-only delegates; cancel is an explicit write.
        if action in {"get", "list"}:
            spec = replace(spec, category="read", side_effect="none", confirmation="none")
        elif action == "cancel":
            spec = replace(spec, confirmation="explicit_request")
        else:
            raise ToolAuthorizationError(name, "action_denied", "unknown task action")
    mcp = name.startswith("mcp__")
    if mcp:
        if name not in policy.allowed_mcp_tools:
            raise ToolAuthorizationError(name, "capability_denied", "MCP capability is not selected")
        spec = (policy.mcp_specs or {}).get(name) or spec
        if spec is None:
            # Unknown side effects cannot enter a read-only delegate.
            spec = ToolSpec(name, "interaction", side_effect="external", confirmation="explicit_request")
    if spec is None:
        raise ToolAuthorizationError(name, "unknown_tool", "tool has no registered execution policy")
    for ceiling, code in ((policy.allowed_tools, "capability_denied"), (policy.skill_allowed_tools, "skill_denied")):
        if ceiling is not None and name not in ceiling:
            raise ToolAuthorizationError(name, code, "tool exceeds the inherited capability set")
    if spec.requires_data_source and not policy.has_data_source:
        raise ToolAuthorizationError(name, "data_source_required", "no data source is connected")
    if spec.requires_workspace and not policy.has_workspace:
        raise ToolAuthorizationError(name, "workspace_required", "no workspace is mounted")
    if spec.requires_runtime and not policy.has_runtime:
        raise ToolAuthorizationError(name, "runtime_required", "no execution runtime is available")
    command = policy.command or ""
    if policy.activation is not None:
        command = policy.activation.command_name or policy.activation.internal_action
    trusted_skill = policy.trusted_skill or (policy.activation.skill_name if policy.activation else "")
    coordination = policy.teams_enabled and name in {"team_status", "team_delegate"}
    server_grant = name in policy.explicit_request_tools or name in policy.workflow_approved_tools
    if not mcp and not coordination and not server_grant and not spec.is_exposed(
        command, policy.has_data_source, policy.has_workspace,
        trusted_skill, name in policy.discovered_tools,
    ):
        raise ToolAuthorizationError(name, "activation_required", "tool is not activated for this request")
    # Discovery and model arguments never mint confirmation authority. Commands
    # and these grant sets are resolved by trusted request/workflow code.
    if spec.confirmation == "workflow" and name not in policy.workflow_approved_tools:
        if not command or command not in spec.commands:
            raise ToolAuthorizationError(name, "workflow_approval_required", "confirmed workflow action is required")
    if spec.confirmation == "explicit_request" and not server_grant:
        raise ToolAuthorizationError(name, "explicit_request_required", "an explicit user request is required")
    if spec.confirmation == "argument":
        if not server_grant:
            raise ToolAuthorizationError(name, "explicit_request_required", "server-confirmed authorization is required")
        if arguments.get("confirm") is not True:
            raise ToolAuthorizationError(name, "confirmation_required", "confirm must be true")
    restriction = (policy.action_restrictions or {}).get(name)
    if restriction is not None and action not in restriction:
        raise ToolAuthorizationError(name, "action_denied", "action exceeds the inherited action set")
    if policy.read_only:
        if spec.category not in {"read", "analysis"} or spec.side_effect != "none":
            raise ToolAuthorizationError(name, "read_only", "tool has side effects under a read-only policy")
        _read_only_query(name, arguments)
    return spec
