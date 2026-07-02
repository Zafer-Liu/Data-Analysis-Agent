"""Public catalog and trusted deterministic Command endpoints."""
import logging
import math
import os
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from agent.commands import (
    CommandAvailabilityContext, CommandLoader, CommandType,
    availability_provider,
)
from .state import config_manager, session_manager

bp = Blueprint("commands", __name__)
log = logging.getLogger(__name__)


def _loader_for_session(sid: str = "") -> CommandLoader:
    workspace_dir = None
    if sid:
        from data.workspace import workspace_manager
        runtime = workspace_manager.get(sid)
        if runtime:
            workspace_dir = runtime.workdir / ".baa" / "commands"
    return CommandLoader(workspace_dir=workspace_dir)


def _availability_context(sid: str) -> CommandAvailabilityContext | None:
    if not sid:
        return None
    from data.workspace import workspace_manager

    sess = session_manager.get(sid)
    provider = ""
    history_length = 0
    if sess is not None:
        history_length = len(sess.history)
        provider = sess.model_provider
    try:
        if not provider:
            provider = config_manager.get_default_provider() or ""
        model_config = config_manager.get_config(provider) if provider else None
        model_available = bool(
            model_config
            and getattr(model_config, "enabled", True)
            and getattr(model_config, "model", "")
        )
    except Exception:
        log.exception("[commands] failed to inspect model availability sid=%s", sid)
        model_available = False
    return CommandAvailabilityContext(
        history_length=history_length,
        model_available=model_available,
        workspace_mounted=workspace_manager.get(sid) is not None,
    )


def _public_diagnostic(item) -> dict[str, str]:
    """Return useful diagnostics without exposing an absolute host path."""
    return {
        "path": Path(item.path).name,
        "source": item.source,
        "error": item.error,
    }


def _public_command(item, context) -> dict[str, object]:
    payload = item.to_public_dict(
        availability=availability_provider.evaluate(item, context),
    )
    if item.type is CommandType.PROMPT:
        prompt_chars = len(item.prompt)
        payload["prompt_chars"] = prompt_chars
        payload["prompt_tokens_est"] = int(math.ceil(prompt_chars / 3.5))
        payload["prompt_size_warning"] = prompt_chars > 28_000
    return payload


@bp.get("/api/commands")
def list_commands():
    sid = (request.args.get("sid") or "").strip()
    loader = _loader_for_session(sid)
    registry = loader.load()
    context = _availability_context(sid)
    diagnostics = [_public_diagnostic(item) for item in loader.diagnostics()]
    return jsonify({
        "commands": [
            _public_command(item, context)
            for item in registry.all()
        ],
        "diagnostics": diagnostics,
        "diagnostic_count": len(diagnostics),
    })


def _compact_command(sess, arguments: str = "") -> tuple[dict, int]:
    """Summarize older history without entering the Agent loop."""
    if len(sess.history) < 4:
        return {
            "ok": False,
            "code": "not_enough_context",
            "error": "当前对话内容较少，暂时不需要压缩。",
        }, 409

    provider = sess.model_provider or config_manager.get_default_provider()
    if not provider:
        return {
            "ok": False,
            "code": "model_required",
            "error": "请先配置并选择模型。",
        }, 400
    try:
        from LLM.llm_config_manager import get_llm_client
        from agent.compaction import (
            _estimate_history_tokens,
            compact_history,
            record_compaction_result,
        )
        from agent.token_metrics import finalize_prompt_breakdown

        cfg = config_manager.get_config(provider)
        client = get_llm_client(provider)
        before_messages = len(sess.history)
        before_tokens = _estimate_history_tokens(sess.history)
        usages = []
        compacted, changed = compact_history(
            sess.history,
            client,
            cfg.model,
            usage_callback=usages.append,
            focus=arguments,
        )
        usage = None
        if usages:
            usage = finalize_prompt_breakdown({
                "component": "manual_compaction",
                "provider": provider,
                "model": cfg.model,
            }, usages[-1])
            sess.record_usage(
                usage["actual_prompt_tokens"],
                usage["actual_completion_tokens"],
                breakdown=usage,
                cached_input_tokens=usage["cached_input_tokens"],
                cache_write_tokens=usage["cache_write_tokens"],
                update_last_prompt=False,
            )
        usage_payload = ({
            "input_tokens": usage["actual_prompt_tokens"],
            "output_tokens": usage["actual_completion_tokens"],
            "cached_tokens": usage["cached_input_tokens"],
            "cache_write_tokens": usage["cache_write_tokens"],
        } if usage else None)
        if not changed:
            record_compaction_result(
                getattr(sess, "compaction_state", None),
                success=False,
                error_type="manual_compaction_failed",
            )
            return {
                "ok": False,
                "code": "compaction_failed",
                "error": "上下文压缩未完成，请检查当前模型连接后重试。",
                "usage": usage_payload,
            }, 502
        after_tokens = _estimate_history_tokens(compacted)
        if after_tokens >= before_tokens:
            record_compaction_result(
                getattr(sess, "compaction_state", None),
                success=False,
                error_type="compaction_not_smaller",
            )
            return {
                "ok": False,
                "code": "compaction_not_smaller",
                "error": "本次摘要未缩短上下文，已保留原对话。",
                "before_messages": before_messages,
                "after_messages": len(compacted),
                "before_tokens": before_tokens,
                "after_tokens": after_tokens,
                "usage": usage_payload,
            }, 409
        record_compaction_result(
            getattr(sess, "compaction_state", None),
            success=True,
            error_type="",
        )
        sess.history = compacted
        sess.last_prompt_tokens = after_tokens
        return {
            "ok": True,
            "command": "compact",
            "before_messages": before_messages,
            "after_messages": len(compacted),
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "usage": usage_payload,
            "session_total_input": sess.total_input_tokens,
            "session_total_output": sess.total_output_tokens,
            "session_total_cached_input": sess.total_cached_input_tokens,
        }, 200
    except Exception as exc:
        try:
            from agent.compaction import record_compaction_result
            record_compaction_result(
                getattr(sess, "compaction_state", None),
                success=False,
                error_type=type(exc).__name__,
            )
        except Exception:
            pass
        log.exception(
            "[commands] manual compaction failed sid=%s: %s",
            getattr(sess, "session_id", ""),
            exc,
        )
        return {
            "ok": False,
            "code": "command_failed",
            "error": "上下文压缩失败，请检查模型连接后重试。",
        }, 502


_BACKEND_HANDLERS = {
    "server:compact": _compact_command,
}


def _execute_backend(sid: str, name: str, arguments: str = "") -> tuple[dict, int]:
    loader = _loader_for_session(sid)
    command = loader.load().get(str(name or "").strip().lower())
    if command is None:
        return {
            "ok": False,
            "code": "unknown_command",
            "error": f"未知斜杠命令：/{name}",
        }, 404
    if command.type is not CommandType.BACKEND:
        return {
            "ok": False,
            "code": "invalid_command_route",
            "error": f"/{command.name} 不是后端命令。",
        }, 400
    if command.arguments == "required" and not arguments.strip():
        return {
            "ok": False,
            "code": "command_arguments_required",
            "error": command.argument_hint or f"用法：{command.usage}",
        }, 400
    if command.arguments == "none" and arguments.strip():
        return {
            "ok": False,
            "code": "command_arguments_forbidden",
            "error": f"/{command.name} 不接受参数。用法：{command.usage}",
        }, 400
    sess = session_manager.get_or_create(sid)
    availability = availability_provider.evaluate(
        command,
        _availability_context(sid),
    )
    if not availability.available:
        sess.record_command_metric(
            command=command.name,
            command_type=command.type.value,
            outcome="rejected",
            error_code=availability.code or "command_unavailable",
        )
        return {
            "ok": False,
            "code": availability.code or "command_unavailable",
            "error": availability.reason or f"/{command.name} 当前不可用。",
            "command": command.name,
        }, 409
    handler = _BACKEND_HANDLERS.get(command.handler_key)
    if handler is None:
        sess.record_command_metric(
            command=command.name,
            command_type=command.type.value,
            outcome="error",
            error_code="command_handler_unavailable",
        )
        return {
            "ok": False,
            "code": "command_handler_unavailable",
            "error": f"/{command.name} 的后端处理器不可用。",
        }, 503
    started = time.monotonic()
    payload, status = handler(
        sess,
        arguments.strip(),
    )
    payload.setdefault("command", command.name)
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    before_tokens = int(payload.get("before_tokens") or 0)
    after_tokens = int(payload.get("after_tokens") or 0)
    ratio = (
        max(0.0, min(after_tokens / before_tokens, 1.0))
        if before_tokens else None
    )
    sess.record_command_metric(
        command=command.name,
        command_type=command.type.value,
        outcome="success" if status < 400 and payload.get("ok") is not False else "error",
        duration_ms=int((time.monotonic() - started) * 1000),
        error_code=str(payload.get("code") or ""),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cached_input_tokens=int(usage.get("cached_tokens") or 0),
        compression_ratio=ratio,
    )
    return payload, status


@bp.post("/api/session/<sid>/commands/<name>/execute")
def execute_backend_command(sid: str, name: str):
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return jsonify({
            "ok": False,
            "code": "invalid_request_body",
            "error": "请求正文必须是 JSON 对象。",
        }), 400
    arguments = body.get("arguments", "")
    if not isinstance(arguments, str):
        return jsonify({
            "ok": False,
            "code": "invalid_command_arguments",
            "error": "命令参数必须是字符串。",
        }), 400
    payload, status = _execute_backend(sid, name, arguments)
    return jsonify(payload), status


@bp.post("/api/session/<sid>/commands/compact")
def compact_conversation(sid: str):
    """Compatibility endpoint for clients predating the generic route."""
    enabled = os.getenv("BAA_ENABLE_LEGACY_COMPACT_ROUTE", "1").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return jsonify({
            "ok": False,
            "code": "legacy_command_route_disabled",
            "error": (
                "旧版 /commands/compact 路由已停用，请改用 "
                "/commands/compact/execute。"
            ),
        }), 410
    payload, status = _execute_backend(sid, "compact")
    response = jsonify(payload)
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = (
        f'</api/session/{sid}/commands/compact/execute>; rel="successor-version"'
    )
    return response, status


@bp.get("/api/session/<sid>/command-metrics")
def get_command_metrics(sid: str):
    sess = session_manager.get(sid)
    if sess is None:
        return jsonify({"error": "session not found"}), 404
    entries = list(getattr(sess, "command_metrics", []) or [])[-200:]
    summary: dict[str, dict[str, int]] = {}
    for item in entries:
        command_type = str(item.get("command_type") or "unknown")
        aggregate = summary.setdefault(command_type, {
            "count": 0,
            "success": 0,
            "error": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "duration_ms": 0,
        })
        aggregate["count"] += 1
        if item.get("outcome") == "success":
            aggregate["success"] += 1
        elif item.get("outcome") in {"error", "rejected"}:
            aggregate["error"] += 1
        for field in (
            "input_tokens", "output_tokens", "cached_input_tokens", "duration_ms",
        ):
            aggregate[field] += int(item.get(field) or 0)
    return jsonify({
        "ok": True,
        "retention_limit": 200,
        "entries": entries,
        "summary": summary,
    })


@bp.post("/api/session/<sid>/command-metrics")
def record_client_command_metric(sid: str):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "请求正文必须是 JSON 对象。"}), 400
    name = str(body.get("command") or "").strip().lower()
    command = _loader_for_session(sid).load().get(name)
    if command is None:
        return jsonify({"error": "未知斜杠命令。", "code": "unknown_command"}), 404
    if command.type not in {CommandType.LOCAL, CommandType.LOCAL_UI}:
        return jsonify({
            "error": "仅本地命令可由浏览器上报执行指标。",
            "code": "invalid_metric_source",
        }), 400
    outcome = str(body.get("outcome") or "success").strip().lower()
    if outcome not in {"success", "error", "rejected"}:
        return jsonify({"error": "无效的命令执行结果。"}), 400
    try:
        duration_ms = int(body.get("duration_ms") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "命令耗时必须是整数毫秒。"}), 400
    session_manager.get_or_create(sid).record_command_metric(
        command=command.name,
        command_type=command.type.value,
        outcome=outcome,
        duration_ms=duration_ms,
        error_code=str(body.get("error_code") or ""),
    )
    return jsonify({"ok": True})
