"""API endpoints for user-configurable hooks."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent.hooks.events import DISPATCHED_EVENTS, EVENT_ALIASES, SUPPORTED_EVENTS
from agent.hooks.loader import ACTION_TYPES, ONCE_SCOPES, HookConfigError, load_settings, serialize_settings
from agent.hooks.models import HookContext
from data.hooks_store import clear_hook_history, list_hook_history, load_raw_settings, save_raw_settings

bp = Blueprint("hooks", __name__)


@bp.get("/api/hooks/history")
def hook_history():
    return jsonify({"ok": True, "items": list_hook_history(request.args.get("limit", 50))})


@bp.delete("/api/hooks/history")
def clear_history():
    return jsonify({"ok": True, "cleared": clear_hook_history()})


@bp.get("/api/hooks")
def get_hooks():
    raw = load_raw_settings()
    try:
        settings = load_settings(raw)
    except HookConfigError as exc:
        return jsonify({"ok": False, "settings": raw, "error": str(exc)})
    normalized = serialize_settings(settings)
    configured_hooks = [
        {
            "id": hook.id,
            "name": hook.name,
            "enabled": hook.enabled,
            "event": hook.event,
            "action_type": hook.action.type,
            "condition": hook.condition,
            "async": hook.async_exec,
            "once": hook.once,
            "once_scope": hook.once_scope,
            "event_dispatched": hook.event in DISPATCHED_EVENTS,
            "internal_endpoint": hook.internal_endpoint,
        }
        for hook in settings.hooks
    ]
    enabled_hooks = [hook for hook in configured_hooks if hook["enabled"]]
    user_hooks = [hook for hook in enabled_hooks if not hook["internal_endpoint"]]
    configured_user_hooks = [hook for hook in configured_hooks if not hook["internal_endpoint"]]
    internal_endpoints = [hook for hook in enabled_hooks if hook["internal_endpoint"]]
    runnable_hooks = [hook for hook in user_hooks if hook["event_dispatched"]]
    return jsonify({
        "ok": True,
        "settings": normalized,
        "runtime": {
            "enabled": settings.enabled,
            "enabled_count": len(user_hooks),
            "runnable_count": len(runnable_hooks) if settings.enabled else 0,
            "pending_count": len(user_hooks) - len(runnable_hooks),
            "configured_count": len(configured_user_hooks),
            "active_hooks": user_hooks,
            "configured_hooks": configured_user_hooks,
            "internal_endpoints": internal_endpoints,
        },
    })


@bp.put("/api/hooks")
def put_hooks():
    raw = request.json or {}
    try:
        settings = save_raw_settings(raw)
    except HookConfigError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "settings": settings})


@bp.post("/api/hooks/validate")
def validate_hooks():
    raw = request.json or {}
    try:
        settings = load_settings(raw)
    except HookConfigError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "settings": serialize_settings(settings)})


@bp.post("/api/hooks/test")
def test_hooks():
    raw = request.json or {}
    event = str(raw.get("event") or "turn_start")
    context = raw.get("context") if isinstance(raw.get("context"), dict) else {}
    settings_raw = raw.get("settings") if isinstance(raw.get("settings"), dict) else load_raw_settings()
    try:
        settings = load_settings(settings_raw)
    except HookConfigError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    side_effect_hooks = [hook.id for hook in settings.hooks if hook.action.type in {"http", "command"}]
    if side_effect_hooks:
        return jsonify({
            "ok": False,
            "error": "测试运行仅支持 prompt Hook；HTTP 与 command 动作不会在此接口执行。",
            "hook_ids": side_effect_hooks,
        }), 400
    from agent.hooks.engine import HookEngine

    engine = HookEngine(
        settings.hooks,
        enabled=settings.enabled,
        allow_command_hooks=settings.allow_command_hooks,
    )
    ctx = HookContext(
        event_name=event,
        session_id=str(context.get("session_id") or "test-session"),
        turn_id=str(context.get("turn_id") or "test-turn"),
        tool_name=str(context.get("tool_name") or "query_data"),
        tool_args=context.get("tool_args") if isinstance(context.get("tool_args"), dict) else {"sql": "SELECT 1"},
        message=str(context.get("message") or "测试 hook"),
        final_answer=str(context.get("final_answer") or ""),
        error=str(context.get("error") or ""),
        extra={"test_mode": True},
    )
    if event == "pre_tool_use":
        rejected = engine.run_pre_tool_hooks(ctx)
        notifications = engine.drain_notifications()
        return jsonify({
            "ok": True,
            "rejected": bool(rejected),
            "reason": rejected.reason if rejected else "",
            "notifications": [item.to_event() for item in notifications],
            "prompt_messages": engine.drain_prompt_messages(),
        })
    notifications = engine.run_hooks(event, ctx)
    return jsonify({
        "ok": True,
        "notifications": [item.to_event() for item in notifications],
        "prompt_messages": engine.drain_prompt_messages(),
    })


@bp.get("/api/hooks/metadata")
def hooks_metadata():
    return jsonify({
        "ok": True,
        "events": sorted(SUPPORTED_EVENTS),
        "dispatched_events": sorted(DISPATCHED_EVENTS),
        "aliases": {
            "SessionStart": "session_start",
            "UserPromptSubmit": "user_prompt_submit",
            "PreToolUse": "pre_tool_use",
            "PostToolUse": "post_tool_use",
            "PermissionRequest": "permission_request",
            "SubagentStart": "subagent_start",
            "SubagentStop": "subagent_stop",
            "PreCompact": "pre_compact",
            "PostCompact": "post_compact",
            "Stop": "stop",
            "turn_begin": "turn_start",
            "tool_call": "tool_call",
        },
        "accepted_event_names": sorted(set(SUPPORTED_EVENTS) | set(EVENT_ALIASES)),
        "actions": sorted(ACTION_TYPES),
        "once_scopes": sorted(ONCE_SCOPES),
        "variables": [
            "$EVENT",
            "$SESSION_ID",
            "$TURN_ID",
            "$TOOL_NAME",
            "$TOOL_ARGS.sql",
            "$MESSAGE",
            "$FINAL_ANSWER",
            "$ERROR",
            "$WORKSPACE_ID",
            "$WORKSPACE_PATH",
        ],
    })
