"""Aggregate auditable Workflow health metrics without exposing model reasoning."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Mapping

from agent.workflows.models import WorkflowContractError, WorkflowErrorCode
from agent.workflows.pricing import estimate_model_cost
from agent.workflows.service import WorkflowService


def _seconds(start: Any, end: Any) -> float | None:
    if not start or not end:
        return None
    try:
        return max(
            0.0,
            (datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start))).total_seconds(),
        )
    except (TypeError, ValueError):
        return None


def _average(values: Iterable[float]) -> float:
    items = list(values)
    return round(sum(items) / len(items), 2) if items else 0.0


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _suggestion_id(version_id: str, kind: str, node_id: str = "") -> str:
    raw = f"{version_id}:{kind}:{node_id}".encode("utf-8")
    return "wos_" + hashlib.sha256(raw).hexdigest()[:16]


def workflow_metrics(runtime, workflow_version_id: str = "") -> dict[str, Any]:
    runs = runtime.run_store.list_runs(limit=10000)
    if workflow_version_id:
        runs = [
            run for run in runs
            if run["workflow_version_id"] == workflow_version_id
        ]
    versions: dict[str, dict[str, Any]] = {}
    nodes_by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
    approvals_by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
    manifests_by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
    consumptions_by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for run in runs:
        version_id = str(run["workflow_version_id"])
        nodes_by_version[version_id].extend(runtime.run_store.list_node_runs(run["id"]))
        approvals_by_version[version_id].extend(
            runtime.run_store.list_approvals(run["id"])
        )
        manifests_by_version[version_id].extend(
            runtime.run_store.list_manifests(run["id"])
        )
        consumptions_by_version[version_id].extend(
            runtime.run_store.list_consumptions(run["id"])
        )

    run_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        run_groups[str(run["workflow_version_id"])].append(run)

    for version_id, version_runs in run_groups.items():
        version = runtime.workflow_store.get_version(version_id)
        workflow = (
            runtime.workflow_store.get_workflow(version["workflow_id"])
            if version else None
        )
        terminal = [
            run for run in version_runs
            if run["status"] in {"succeeded", "failed", "canceled"}
        ]
        successes = [run for run in terminal if run["status"] == "succeeded"]
        durations = [
            value for value in (
                _seconds(run.get("started_at"), run.get("finished_at"))
                for run in terminal
            ) if value is not None
        ]
        node_runs = nodes_by_version[version_id]
        approval_rows = approvals_by_version[version_id]
        decided_waits = [
            value for value in (
                _seconds(item.get("requested_at"), item.get("decided_at"))
                for item in approval_rows if item.get("decided_at")
            ) if value is not None
        ]
        failed_nodes = [node for node in node_runs if node["status"] == "failed"]
        retried_nodes = [
            node for node in node_runs
            if int(node.get("attempt") or 1) > 1 or int(node.get("iteration") or 1) > 1
        ]
        rejected_approvals = [
            item for item in approval_rows
            if item.get("decision") in {"reject_and_retry", "reject_and_stop"}
        ]
        executed_nodes = [
            node for node in node_runs
            if node["status"] not in {"pending", "ready", "skipped"}
        ]
        input_tokens = sum(int(node.get("input_tokens") or 0) for node in node_runs)
        output_tokens = sum(int(node.get("output_tokens") or 0) for node in node_runs)
        cached_tokens = sum(
            int(node.get("cached_input_tokens") or 0) for node in node_runs
        )
        measured_nodes = sum(
            1 for node in executed_nodes
            if int(node.get("input_tokens") or 0) or int(node.get("output_tokens") or 0)
        )
        manifest_items = [
            item
            for manifest in manifests_by_version[version_id]
            for item in (manifest.get("items") or [])
        ]
        produced_ids = {
            str(item.get("artifact_id") or "")
            for item in manifest_items if item.get("artifact_id")
        }
        consumed_ids = {
            str(item.get("artifact_id") or "")
            for item in consumptions_by_version[version_id]
            if item.get("artifact_id")
        }
        candidates = [
            item for run in version_runs
            for item in runtime.run_store.list_knowledge_candidates(run_id=run["id"])
        ]
        accepted_candidates = [
            item for item in candidates if item["status"] == "accepted"
        ]

        node_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in node_runs:
            node_groups[str(node["node_id"])].append(node)
        node_metrics = []
        for node_id, rows in sorted(node_groups.items()):
            failures = sum(1 for row in rows if row["status"] == "failed")
            retries = sum(
                1 for row in rows
                if int(row.get("attempt") or 1) > 1
                or int(row.get("iteration") or 1) > 1
            )
            node_metrics.append({
                "node_id": node_id,
                "runs": len(rows),
                "failures": failures,
                "failure_rate": _rate(failures, len(rows)),
                "retries": retries,
                "retry_rate": _rate(retries, len(rows)),
                "avg_duration_seconds": _average(
                    value for value in (
                        _seconds(row.get("started_at"), row.get("finished_at"))
                        for row in rows
                    ) if value is not None
                ),
                "input_tokens": sum(int(row.get("input_tokens") or 0) for row in rows),
                "output_tokens": sum(int(row.get("output_tokens") or 0) for row in rows),
            })

        model_groups: dict[tuple[str, str], dict[str, Any]] = {}
        for node in node_runs:
            model = str(node.get("model_name") or "")
            provider = str(node.get("provider_name") or "")
            if not model and not provider:
                continue
            key = (provider, model)
            group = model_groups.setdefault(key, {
                "provider": provider,
                "model": model,
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": 0,
                "estimated_cost": None,
            })
            group["calls"] += 1
            group["input_tokens"] += int(node.get("input_tokens") or 0)
            group["output_tokens"] += int(node.get("output_tokens") or 0)
            group["cached_input_tokens"] += int(
                node.get("cached_input_tokens") or 0
            )

        for group in model_groups.values():
            group["estimated_cost"] = estimate_model_cost(
                group["provider"], group["model"], group["input_tokens"], group["output_tokens"],
            )

        model_costs = [item["estimated_cost"] for item in model_groups.values()]

        versions[version_id] = {
            "workflow_id": str((version or {}).get("workflow_id") or ""),
            "workflow_name": str((workflow or {}).get("name") or version_id),
            "workflow_version_id": version_id,
            "version_number": (version or {}).get("version_number"),
            "run_count": len(version_runs),
            "terminal_run_count": len(terminal),
            "success_count": len(successes),
            "success_rate": _rate(len(successes), len(terminal)),
            "avg_duration_seconds": _average(durations),
            "approval_count": len(approval_rows),
            "pending_approval_count": sum(
                1 for item in approval_rows if item["status"] == "pending"
            ),
            "avg_approval_wait_seconds": _average(decided_waits),
            "approval_rejection_rate": _rate(
                len(rejected_approvals), len(approval_rows)
            ),
            "node_run_count": len(node_runs),
            "node_failure_rate": _rate(len(failed_nodes), len(node_runs)),
            "node_retry_rate": _rate(len(retried_nodes), len(node_runs)),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_input_tokens": cached_tokens,
            "token_coverage": _rate(measured_nodes, len(executed_nodes)),
            "estimated_cost": (round(sum(model_costs), 8) if model_costs and all(value is not None for value in model_costs) else None),
            "artifact_count": len(produced_ids),
            "artifact_reuse_rate": _rate(
                len(consumed_ids & produced_ids), len(produced_ids)
            ),
            "knowledge_candidate_count": len(candidates),
            "knowledge_candidate_adoption_rate": _rate(
                len(accepted_candidates), len(candidates)
            ),
            "nodes": node_metrics,
            "models": sorted(
                model_groups.values(),
                key=lambda item: (item["provider"], item["model"]),
            ),
        }

    version_rows = sorted(
        versions.values(),
        key=lambda item: (item["workflow_name"], item.get("version_number") or 0),
    )
    total_terminal = sum(item["terminal_run_count"] for item in version_rows)
    total_success = sum(item["success_count"] for item in version_rows)
    return {
        "summary": {
            "workflow_version_count": len(version_rows),
            "run_count": len(runs),
            "terminal_run_count": total_terminal,
            "success_rate": _rate(total_success, total_terminal),
            "input_tokens": sum(item["input_tokens"] for item in version_rows),
            "output_tokens": sum(item["output_tokens"] for item in version_rows),
            "estimated_cost": (round(sum(item["estimated_cost"] for item in version_rows), 8) if version_rows and all(item["estimated_cost"] is not None for item in version_rows) else None),
        },
        "versions": version_rows,
    }


def workflow_optimization_suggestions(
    metrics: Mapping[str, Any],
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for version in metrics.get("versions") or []:
        version_id = str(version["workflow_version_id"])
        if version["terminal_run_count"] >= 3 and version["success_rate"] < 0.9:
            suggestions.append({
                "id": _suggestion_id(version_id, "reliability"),
                "workflow_version_id": version_id,
                "kind": "reliability",
                "severity": "high",
                "title": "检查失败节点并调整恢复策略",
                "rationale": (
                    f"成功率 {version['success_rate']:.0%}，"
                    f"低于建议门槛 90%。"
                ),
                "proposed_change": "复制当前发布版本为草稿，人工检查失败节点、超时和 max_attempts。",
            })
        if version["avg_approval_wait_seconds"] > 600:
            suggestions.append({
                "id": _suggestion_id(version_id, "approval-wait"),
                "workflow_version_id": version_id,
                "kind": "approval_wait",
                "severity": "medium",
                "title": "缩短关键审批等待",
                "rationale": (
                    f"平均审批等待 {version['avg_approval_wait_seconds']:.0f} 秒。"
                ),
                "proposed_change": "复制为草稿后，人工检查审批位置、负责人和输入摘要。",
            })
        for node in version.get("nodes") or []:
            if node["runs"] >= 3 and node["failure_rate"] >= 0.15:
                suggestions.append({
                    "id": _suggestion_id(
                        version_id, "node-failure", str(node["node_id"])
                    ),
                    "workflow_version_id": version_id,
                    "node_id": node["node_id"],
                    "kind": "node_failure",
                    "severity": "high",
                    "title": f"优化高失败节点：{node['node_id']}",
                    "rationale": (
                        f"节点失败率 {node['failure_rate']:.0%}，"
                        f"共 {node['failures']} 次失败。"
                    ),
                    "proposed_change": "复制为草稿后，人工检查输入契约、Agent Profile 和重试上限。",
                })
            elif node["runs"] >= 3 and node["retry_rate"] >= 0.2:
                suggestions.append({
                    "id": _suggestion_id(
                        version_id, "node-retry", str(node["node_id"])
                    ),
                    "workflow_version_id": version_id,
                    "node_id": node["node_id"],
                    "kind": "node_retry",
                    "severity": "medium",
                    "title": f"减少节点返工：{node['node_id']}",
                    "rationale": f"节点重试率 {node['retry_rate']:.0%}。",
                    "proposed_change": "复制为草稿后，人工完善输入材料和输出契约。",
                })
            average_tokens = (
                int(node["input_tokens"] or 0) + int(node["output_tokens"] or 0)
            ) / max(1, int(node["runs"] or 1))
            if node["runs"] >= 3 and average_tokens >= 4000:
                suggestions.append({
                    "id": _suggestion_id(
                        version_id, "node-token-budget", str(node["node_id"])
                    ),
                    "workflow_version_id": version_id,
                    "node_id": node["node_id"],
                    "kind": "node_token_budget",
                    "severity": "medium",
                    "title": f"压缩高 Token 节点：{node['node_id']}",
                    "rationale": (
                        f"该节点平均每次消耗约 {average_tokens:.0f} Token，"
                        "超过 4000 Token 观察阈值。"
                    ),
                    "proposed_change": "复制为草稿后，收紧输入材料、输出契约和 max_tokens；确认该节点的输出确实被下游使用。",
                })
        if (
            version["knowledge_candidate_count"] >= 3
            and version["knowledge_candidate_adoption_rate"] < 0.2
        ):
            suggestions.append({
                "id": _suggestion_id(version_id, "knowledge-adoption"),
                "workflow_version_id": version_id,
                "kind": "knowledge_adoption",
                "severity": "low",
                "title": "提高知识候选可采纳性",
                "rationale": (
                    f"知识候选采纳率 "
                    f"{version['knowledge_candidate_adoption_rate']:.0%}。"
                ),
                "proposed_change": "复制为草稿后，人工收紧报告与 SQL 输出契约。",
            })
    return suggestions


def create_suggestion_draft(
    runtime,
    suggestion_id: str,
    *,
    created_by: str = "",
) -> dict[str, Any]:
    metrics = workflow_metrics(runtime)
    suggestions = workflow_optimization_suggestions(metrics)
    suggestion = next(
        (item for item in suggestions if item["id"] == suggestion_id),
        None,
    )
    if suggestion is None:
        raise WorkflowContractError(
            WorkflowErrorCode.RESOURCE_NOT_FOUND,
            f"workflow optimization suggestion not found: {suggestion_id}",
        )
    version = runtime.workflow_store.get_version(
        suggestion["workflow_version_id"]
    )
    if version is None:
        raise WorkflowContractError(
            WorkflowErrorCode.RESOURCE_NOT_FOUND,
            "published workflow version is missing",
        )
    source = runtime.workflow_store.get_workflow(version["workflow_id"])
    if source is None:
        raise WorkflowContractError(
            WorkflowErrorCode.RESOURCE_NOT_FOUND,
            "workflow definition is missing",
        )
    with WorkflowService(runtime.workspace) as service:
        return service.create_workflow(
            name=f"{source['name']} · 优化草稿",
            description=(
                f"基于 {suggestion['id']} 从发布版本 "
                f"v{version['version_number']} 创建。{suggestion['rationale']} "
                f"{suggestion['proposed_change']}"
            ),
            graph=version["graph"],
            input_schema=version["input_schema"],
            output_schema=version["output_schema"],
            created_by=created_by or "workflow_metrics",
        )
