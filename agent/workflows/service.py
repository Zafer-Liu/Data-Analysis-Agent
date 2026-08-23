"""WF1 workflow draft validation, profile registration, and publication."""
from __future__ import annotations

import hashlib
import json
from contextlib import AbstractContextManager
from typing import Any, Mapping

from agent.tools.registry import BUILTIN_TOOL_REGISTRY
from data.workflow_store import WorkflowStore, WorkflowStoreError
from data.workspace import workspace_manager

from .models import (
    AgentProfile,
    EdgeType,
    WorkflowContractError,
    WorkflowErrorCode,
    validate_workflow_graph,
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _schema_properties(schema: Mapping[str, Any], label: str) -> set[str]:
    if not isinstance(schema, Mapping):
        raise WorkflowContractError(
            WorkflowErrorCode.GRAPH_INVALID,
            f"{label} schema must be an object",
        )
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise WorkflowContractError(
            WorkflowErrorCode.GRAPH_INVALID,
            f"{label} schema properties must be an object",
        )
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise WorkflowContractError(
            WorkflowErrorCode.GRAPH_INVALID,
            f"{label} schema required must be a string array",
        )
    unknown = sorted(set(required) - set(properties))
    if unknown:
        raise WorkflowContractError(
            WorkflowErrorCode.GRAPH_INVALID,
            f"{label} schema required keys are missing properties: {', '.join(unknown)}",
        )
    return set(properties)


class WorkflowService(AbstractContextManager["WorkflowService"]):
    """Workspace-fixed service boundary for all WF1 operations."""

    def __init__(self, runtime):
        if runtime is None:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                "no workspace is mounted for this session",
            )
        self.runtime = runtime
        self.workspace_id = str(runtime.workspace_id)
        self.store = WorkflowStore(
            runtime.meta_dir / "workflows.sqlite3",
            self.workspace_id,
        )

    @classmethod
    def for_session(cls, session_id: str) -> "WorkflowService":
        return cls(workspace_manager.workflow_runtime_for_session(session_id))

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        self.store.close()

    def _require_write(self) -> None:
        if self.runtime.permission != "read_write":
            raise WorkflowContractError(
                WorkflowErrorCode.PERMISSION_DENIED,
                "workspace is mounted read-only",
            )

    @staticmethod
    def _required_text(value: Any, label: str, *, max_length: int) -> str:
        normalized = " ".join(str(value or "").split())
        if not normalized:
            raise WorkflowContractError(
                WorkflowErrorCode.GRAPH_INVALID,
                f"{label} is required",
            )
        if len(normalized) > max_length:
            raise WorkflowContractError(
                WorkflowErrorCode.GRAPH_INVALID,
                f"{label} exceeds {max_length} characters",
            )
        return normalized

    def create_agent_profile(
        self,
        *,
        key: str,
        name: str,
        role: str,
        instructions: str = "",
        allowed_tools: list[str] | tuple[str, ...] = (),
        model_policy: str = "inherit",
        created_by: str = "",
    ) -> dict[str, Any]:
        self._require_write()
        profile_key = self._required_text(key, "profile key", max_length=80)
        profile_name = self._required_text(name, "profile name", max_length=120)
        profile_role = self._required_text(role, "profile role", max_length=120)
        tools = tuple(dict.fromkeys(
            str(tool).strip() for tool in allowed_tools if str(tool).strip()
        ))
        unknown = sorted(set(tools) - BUILTIN_TOOL_REGISTRY.names())
        if unknown:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                f"unknown profile tools: {', '.join(unknown)}",
            )
        profile = AgentProfile(
            id="pending",
            workspace_id=self.workspace_id,
            key=profile_key,
            revision=1,
            name=profile_name,
            role=profile_role,
            instructions=str(instructions or "")[:8000],
            allowed_tools=tools,
            model_policy=str(model_policy or "inherit")[:80],
        )
        return self.store.create_agent_profile(
            key=profile.key,
            name=profile.name,
            role=profile.role,
            instructions=profile.instructions,
            allowed_tools=profile.allowed_tools,
            model_policy=profile.model_policy,
            created_by=str(created_by or "")[:120],
        )

    def list_agent_profiles(self) -> list[dict[str, Any]]:
        return self.store.list_agent_profiles()

    def create_workflow(
        self,
        *,
        name: str,
        description: str = "",
        graph: Mapping[str, Any],
        input_schema: Mapping[str, Any] | None = None,
        output_schema: Mapping[str, Any] | None = None,
        created_by: str = "",
    ) -> dict[str, Any]:
        self._require_write()
        if not isinstance(graph, Mapping):
            raise WorkflowContractError(
                WorkflowErrorCode.GRAPH_INVALID,
                "workflow graph must be an object",
            )
        return self.store.create_workflow(
            name=self._required_text(name, "workflow name", max_length=120),
            description=str(description or "")[:2000],
            graph=graph,
            input_schema=input_schema or {},
            output_schema=output_schema or {},
            created_by=str(created_by or "")[:120],
        )

    def list_workflows(self) -> list[dict[str, Any]]:
        return self.store.list_workflows()

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        workflow = self.store.get_workflow(workflow_id)
        if workflow is None:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                f"workflow not found: {workflow_id}",
            )
        workflow["versions"] = self.store.list_versions(workflow_id)
        return workflow

    def update_draft(
        self,
        workflow_id: str,
        *,
        graph: Mapping[str, Any],
        input_schema: Mapping[str, Any] | None = None,
        output_schema: Mapping[str, Any] | None = None,
        name: str | None = None,
        description: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        self._require_write()
        if not isinstance(graph, Mapping):
            raise WorkflowContractError(
                WorkflowErrorCode.GRAPH_INVALID,
                "workflow graph must be an object",
            )
        current = self.get_workflow(workflow_id)
        try:
            updated = self.store.update_workflow_draft(
                workflow_id,
                graph=graph,
                input_schema=(
                    input_schema
                    if input_schema is not None
                    else current["draft_input_schema"]
                ),
                output_schema=(
                    output_schema
                    if output_schema is not None
                    else current["draft_output_schema"]
                ),
                name=(
                    self._required_text(name, "workflow name", max_length=120)
                    if name is not None
                    else None
                ),
                description=(
                    str(description or "")[:2000]
                    if description is not None
                    else None
                ),
                expected_revision=expected_revision,
            )
        except WorkflowStoreError as exc:
            raise WorkflowContractError(
                WorkflowErrorCode.VERSION_CONFLICT,
                str(exc),
            ) from exc
        if updated is None:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                f"workflow not found: {workflow_id}",
            )
        return updated

    def validate_draft(self, workflow_id: str) -> dict[str, Any]:
        workflow = self.get_workflow(workflow_id)
        graph = workflow["draft_graph"]
        validate_workflow_graph(graph)
        self._validate_profile_references(graph)
        self._validate_material_contracts(
            graph,
            workflow["draft_input_schema"],
            workflow["draft_output_schema"],
        )
        return {
            "valid": True,
            "workflow_id": workflow_id,
            "draft_revision": workflow["draft_revision"],
            "node_count": len(graph["nodes"]),
            "edge_count": len(graph["edges"]),
        }

    def _validate_profile_references(self, graph: Mapping[str, Any]) -> None:
        missing = sorted({
            str(node.get("agent_profile_id") or "")
            for node in graph.get("nodes", [])
            if str(node.get("type") or "agent") in {"agent", "verifier"}
            if self.store.get_agent_profile(
                str(node.get("agent_profile_id") or "")
            ) is None
        })
        if missing:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                f"agent profiles not found in workspace: {', '.join(missing)}",
            )

    @staticmethod
    def _validate_material_contracts(
        graph: Mapping[str, Any],
        input_schema: Mapping[str, Any],
        output_schema: Mapping[str, Any],
    ) -> None:
        external_inputs = _schema_properties(input_schema, "workflow input")
        declared_outputs = _schema_properties(output_schema, "workflow output")
        nodes = {
            str(node["node_id"]): node
            for node in graph.get("nodes", [])
        }
        incoming: dict[str, set[str]] = {node_id: set() for node_id in nodes}
        for edge in graph.get("edges", []):
            if edge.get("type") == EdgeType.RETRY_LOOP.value:
                continue
            source = nodes[str(edge["from_node"])]
            incoming[str(edge["to_node"])].update(source.get("output_contract", []))

        for node_id, node in nodes.items():
            available = external_inputs | incoming[node_id]
            missing = sorted(set(node.get("input_contract", [])) - available)
            if missing:
                raise WorkflowContractError(
                    WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                    f"node {node_id} inputs have no declared source: {', '.join(missing)}",
                )

        produced = {
            output
            for node in nodes.values()
            for output in node.get("output_contract", [])
        }
        missing_outputs = sorted(declared_outputs - produced)
        if missing_outputs:
            raise WorkflowContractError(
                WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                "workflow outputs are not produced: " + ", ".join(missing_outputs),
            )

    def publish(self, workflow_id: str, *, published_by: str = "") -> dict[str, Any]:
        self._require_write()
        validation = self.validate_draft(workflow_id)
        workflow = self.get_workflow(workflow_id)
        graph_hash = hashlib.sha256(
            _canonical_json(workflow["draft_graph"]).encode("utf-8")
        ).hexdigest()
        result = self.store.publish_workflow(
            workflow_id,
            graph_hash=graph_hash,
            published_by=str(published_by or "")[:120],
        )
        if result is None:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                f"workflow not found: {workflow_id}",
            )
        version, reused = result
        return {
            "workflow_id": workflow_id,
            "version": version,
            "reused": reused,
            "validation": validation,
        }
