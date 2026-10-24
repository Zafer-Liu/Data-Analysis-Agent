"""Human-gated template and knowledge-candidate services for Workflow Runs."""
from __future__ import annotations

import json
from typing import Any, Mapping

from agent.workflows.models import WorkflowContractError, WorkflowErrorCode


_REPORT_TOKENS = ("report", "报告", "summary", "总结", "conclusion", "结论", "template", "模板")


def _require_succeeded(runtime, run_id: str) -> dict[str, Any]:
    run = runtime.run_store.get_run(str(run_id or ""))
    if run is None:
        raise WorkflowContractError(
            WorkflowErrorCode.RESOURCE_NOT_FOUND,
            f"workflow run not found: {run_id}",
        )
    if run["status"] != "succeeded":
        raise WorkflowContractError(
            WorkflowErrorCode.RUN_NOT_RECOVERABLE,
            "only a succeeded workflow run can be reused",
        )
    return run


def _source_manifest(detail: Mapping[str, Any]) -> str:
    manifests = detail.get("manifests") or []
    preferred = [
        item for item in manifests
        if item.get("kind") in {"node_output_revision", "node_output"}
    ]
    selected = (preferred or manifests)[-1:] if (preferred or manifests) else []
    return str(selected[0].get("id") or "") if selected else ""


def mark_run_template(
    runtime,
    run_id: str,
    *,
    name: str = "",
    description: str = "",
    created_by: str = "",
) -> dict[str, Any]:
    run = _require_succeeded(runtime, run_id)
    version = runtime.workflow_store.get_version(run["workflow_version_id"])
    workflow = (
        runtime.workflow_store.get_workflow(version["workflow_id"])
        if version else None
    )
    detail = runtime.scheduler.detail(run_id)
    template_name = str(name or "").strip() or (
        f"{workflow.get('name', 'Workflow')} 成功模板" if workflow else "Workflow 成功模板"
    )
    return runtime.run_store.create_run_template(
        run_id,
        name=template_name,
        description=str(description or "").strip(),
        source_manifest_id=_source_manifest(detail),
        created_by=created_by,
    )


def generate_knowledge_candidates(runtime, run_id: str) -> list[dict[str, Any]]:
    run = _require_succeeded(runtime, run_id)
    detail = runtime.scheduler.detail(run_id)
    source_manifest_id = _source_manifest(detail)
    version = runtime.workflow_store.get_version(run["workflow_version_id"])
    workflow = (
        runtime.workflow_store.get_workflow(version["workflow_id"])
        if version else None
    )
    workflow_name = str((workflow or {}).get("name") or "Workflow")
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for key, value in (detail.get("outputs") or {}).items():
        label = str(key or "")
        lowered = label.lower()
        if not any(token in lowered for token in _REPORT_TOKENS):
            continue
        content = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, indent=2, default=str
        )
        title = f"{workflow_name} · {label}"
        candidates.append({
            "candidate_type": "report_template",
            "title": title,
            "payload": {
                "topic": title,
                "content": str(content)[:12000],
                "tags": "workflow,report-template",
                "workflow_version_id": run["workflow_version_id"],
                "source_manifest_id": source_manifest_id,
            },
        })
        seen.add(("report_template", title))

    for manifest in detail.get("manifests") or []:
        for item in manifest.get("items") or []:
            sql = str(item.get("sql") or "").strip()
            if not sql:
                continue
            logical_name = str(
                item.get("logical_name") or item.get("name") or item.get("artifact_id") or "SQL"
            )
            title = f"{workflow_name} · {logical_name}"
            key = ("metric_sql", title)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "candidate_type": "metric_sql",
                "title": title,
                "payload": {
                    "name": title,
                    "definition": str(item.get("summary") or logical_name),
                    "sql_template": sql,
                    "notes": (
                        f"来源 Workflow Run {run_id}; Manifest {manifest.get('id', '')}; "
                        f"SQL hash {item.get('sql_hash', '')}"
                    ),
                    "workflow_version_id": run["workflow_version_id"],
                    "source_manifest_id": str(manifest.get("id") or source_manifest_id),
                },
            })

    return runtime.run_store.create_knowledge_candidates(
        run_id,
        candidates,
        source_manifest_id=source_manifest_id,
    )


def decide_knowledge_candidate(
    runtime,
    candidate_id: str,
    *,
    decision: str,
    user_id: str = "local-default",
    category_id: int | None = None,
    decided_by: str = "",
    comment: str = "",
) -> dict[str, Any]:
    normalized = str(decision or "").strip().lower()
    if normalized not in {"accept", "reject"}:
        raise WorkflowContractError(
            WorkflowErrorCode.GRAPH_INVALID,
            "knowledge candidate decision must be accept or reject",
        )
    candidate = runtime.run_store.get_knowledge_candidate(candidate_id)
    if candidate is None:
        raise WorkflowContractError(
            WorkflowErrorCode.RESOURCE_NOT_FOUND,
            f"knowledge candidate not found: {candidate_id}",
        )
    if candidate["status"] in {"accepted", "rejected"}:
        return candidate
    published_ref: dict[str, Any] = {}
    if normalized == "accept":
        from Function.Knowledge.knowledge_base import KnowledgeBase

        payload = candidate.get("payload") or {}
        with KnowledgeBase(workspace_id="", user_id=str(user_id or "local-default")) as kb:
            if candidate["candidate_type"] == "metric_sql":
                record = kb.add_metric(
                    name=str(payload.get("name") or candidate["title"]),
                    definition=str(payload.get("definition") or ""),
                    sql_template=str(payload.get("sql_template") or ""),
                    notes=str(payload.get("notes") or ""),
                    category_id=category_id,
                )
                published_ref = {"kind": "metric", "id": record.get("id")}
            elif candidate["candidate_type"] == "report_template":
                record = kb.add_note(
                    topic=str(payload.get("topic") or candidate["title"]),
                    content=str(payload.get("content") or ""),
                    tags=str(payload.get("tags") or "workflow"),
                    category_id=category_id,
                )
                published_ref = {"kind": "note", "id": record.get("id")}
            else:
                raise WorkflowContractError(
                    WorkflowErrorCode.GRAPH_INVALID,
                    f"unsupported knowledge candidate type: {candidate['candidate_type']}",
                )
    decided = runtime.run_store.decide_knowledge_candidate(
        candidate_id,
        decision=normalized,
        decided_by=decided_by,
        comment=comment,
        published_ref=published_ref,
    )
    if decided is None:
        raise WorkflowContractError(
            WorkflowErrorCode.RESOURCE_NOT_FOUND,
            f"knowledge candidate not found: {candidate_id}",
        )
    return decided