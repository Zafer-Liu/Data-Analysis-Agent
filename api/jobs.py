#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blueprint: job management and durable event replay.

路由：
  GET   /api/session/<sid>/jobs              — 列出会话内的任务（?active=true 只看运行中）
  GET   /api/session/<sid>/jobs/<jid>        — 查询单个任务状态
  POST  /api/session/<sid>/jobs/<jid>/cancel — 请求取消任务
  GET   /api/session/<sid>/jobs/events       — 按 sequence 增量读取持久化事件
前端通过 SSE 或轮询 /jobs 接口获取进度；任务创建只由真实业务入口触发。
"""
import logging
from flask import Blueprint, request, jsonify

from .state import session_manager, require_session_ownership

log = logging.getLogger(__name__)

bp = Blueprint("jobs", __name__)


def _job_to_dict(job) -> dict:
    """把 JobsStore 返回的 row dict 标准化为 JSON 响应。"""
    workspace_id = str(job.get("workspace_id") or "")
    workspace = None
    if workspace_id:
        from data.workspace import workspace_manager
        root = workspace_manager.root_for_workspace(workspace_id)
        workspace = {
            "id": workspace_id,
            "name": root.name if root else workspace_id[:8],
            "path": str(root) if root else "",
        }
    return {
        "id": job["id"],
        "session_id": job["session_id"],
        "workspace_id": workspace_id,
        "workspace": workspace,
        "type": job["type"],
        "label": job.get("label", ""),
        "parent_id": job.get("parent_id", ""),
        "status": job["status"],
        "progress": job.get("progress", 0),
        "message": job.get("message", ""),
        "result": job.get("result"),
        "error": job.get("error"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
    }


@bp.get("/api/session/<sid>/jobs")
@require_session_ownership
def list_jobs(sid: str):
    """列出会话内的任务。?active=true 只返回未完成的。"""
    sess = session_manager.get_or_create(sid)
    active_only = request.args.get("active", "").lower() in ("1", "true", "yes")
    try:
        limit = max(1, min(int(request.args.get("limit", "100")), 500))
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400
    jobs = sess.job_runner.list_jobs(
        active_only=active_only, limit=limit, top_level_only=True,
    )
    artifacts = sess.job_runner.list_artifacts([job["id"] for job in jobs])
    details = sess.job_runner.list_detail_events([job["id"] for job in jobs])
    # Response-time compatibility for conversation jobs created before the
    # universal schema-snapshot rule was introduced. New jobs persist this
    # artifact during /chat; old jobs receive the same content-deduplicated
    # reference when history is refreshed.
    schema_artifact = None
    schema_text = str(getattr(sess, "_combined_schema_cache", "") or "")
    if schema_text and any(job.get("type") == "conversation_analysis" for job in jobs):
        from agent.tools.results import persist_large_tool_result
        from data.workspace import workspace_manager
        _preview, schema_artifact, _debug = persist_large_tool_result(
            sid, "get_schema", schema_text,
            runtime=workspace_manager.get(sid), threshold=1, deduplicate=True,
        )
        if schema_artifact:
            schema_artifact["name"] = "get_schema 数据结构"
            sess.record_tool_audit({"recovery": {}, "artifacts": [schema_artifact]})
    payload = []
    activations = {
        str(item.get("job_id") or ""): item
        for item in getattr(sess, "turn_activations", [])
        if isinstance(item, dict) and item.get("job_id")
    }
    for job in jobs:
        item = _job_to_dict(job)
        item["activation"] = activations.get(job["id"]) or (
            (job.get("result") or {}).get("activation")
            if isinstance(job.get("result"), dict) else None
        )
        item["artifacts"] = artifacts.get(job["id"], [])
        if job.get("type") == "conversation_analysis" and schema_artifact:
            known = {artifact.get("artifact_id") for artifact in item["artifacts"]}
            if schema_artifact.get("artifact_id") not in known:
                item["artifacts"].append(schema_artifact)
        item["steps"] = details.get(job["id"], [])
        payload.append(item)
    return jsonify({"jobs": payload})


@bp.delete("/api/session/<sid>/jobs")
@require_session_ownership
def clear_completed_jobs(sid: str):
    """Clear terminal history while preserving queued/running jobs."""
    sess = session_manager.get_or_create(sid)
    deleted = sess.job_runner.clear_terminal()
    return jsonify({
        "ok": True,
        "deleted": deleted,
        "latest_sequence": sess.job_runner.last_sequence,
    })


@bp.get("/api/session/<sid>/jobs/events")
@require_session_ownership
def list_job_events(sid: str):
    """Return replayable events after an exclusive per-session sequence."""
    sess = session_manager.get_or_create(sid)
    try:
        after_sequence = max(0, int(request.args.get("after_sequence", "0")))
        limit = max(1, min(int(request.args.get("limit", "200")), 1000))
    except (TypeError, ValueError):
        return jsonify({"error": "after_sequence and limit must be integers"}), 400
    job_id = (request.args.get("job_id") or "").strip() or None
    events = sess.job_runner.list_events(
        after_sequence=after_sequence,
        limit=limit,
        job_id=job_id,
    )
    next_sequence = events[-1]["sequence"] if events else after_sequence
    oldest_sequence = sess.job_runner.oldest_sequence
    return jsonify({
        "events": events,
        "next_sequence": next_sequence,
        "latest_sequence": sess.job_runner.last_sequence,
        "oldest_sequence": oldest_sequence,
        "replay_truncated": after_sequence < max(0, oldest_sequence - 1),
    })


@bp.get("/api/session/<sid>/jobs/<jid>")
@require_session_ownership
def get_job(sid: str, jid: str):
    """查询单个任务状态。"""
    sess = session_manager.get_or_create(sid)
    job = sess.job_runner.get_status(jid)
    if job is None:
        return jsonify({"error": "job not found", "id": jid}), 404
    item = _job_to_dict(job)
    item["activation"] = next((
        activation for activation in reversed(getattr(sess, "turn_activations", []))
        if isinstance(activation, dict) and activation.get("job_id") == jid
    ), None) or (
        (job.get("result") or {}).get("activation")
        if isinstance(job.get("result"), dict) else None
    )
    item["artifacts"] = sess.job_runner.list_artifacts([jid]).get(jid, [])
    item["steps"] = sess.job_runner.list_detail_events([jid]).get(jid, [])
    return jsonify({"job": item})


@bp.post("/api/session/<sid>/jobs/<jid>/cancel")
@require_session_ownership
def cancel_job(sid: str, jid: str):
    """请求取消任务。返回 accepted=true 表示请求已受理。

    注意：Python 无法强行中断运行中的线程，job 函数需协作式检查 ctx.check_canceled()。
    若 job 已是终态（succeeded/failed/canceled），返回 409。
    """
    sess = session_manager.get_or_create(sid)
    job = sess.job_runner.get_status(jid)
    if job is None:
        return jsonify({"error": "job not found", "id": jid}), 404
    if job.get("type") == "filehistory_rewind" and job.get("status") == "running":
        return jsonify({
            "error": "文件历史正在执行回退，当前阶段不能取消。",
            "id": jid,
            "status": job["status"],
        }), 409
    if job.get("type") == "memory_extraction":
        # Extraction runs on its own worker without cooperative cancel checks;
        # accepting would strand the job in "canceling" forever.
        return jsonify({
            "error": "记忆提取为后台短任务，不支持取消。",
            "id": jid,
            "status": job["status"],
        }), 409

    if job.get("type") == "conversation_analysis":
        sess.cancel_requested = True
    accepted = sess.job_runner.cancel(jid)
    if not accepted:
        return jsonify({
            "error": "cannot cancel terminal job",
            "id": jid,
            "status": job["status"],
        }), 409

    current = sess.job_runner.get_status(jid)
    return jsonify({
        "id": jid,
        "accepted": True,
        "status": current["status"] if current else "canceling",
    })
