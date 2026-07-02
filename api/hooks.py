"""API endpoints for user-configurable hooks."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent.hooks.events import EVENT_ALIASES, SUPPORTED_EVENTS
from agent.hooks.loader import ACTION_TYPES, HookConfigError, load_settings, serialize_settings
from agent.hooks.models import HookContext
from data.hooks_store import load_raw_settings, save_raw_settings

bp = Blueprint("hooks", __name__)


@bp.get("/api/hooks")
def get_hooks():
    raw = load_raw_settings()
    try:
        settings = load_settings(raw)
    except HookConfigError as exc:
        return jsonify({"ok": False, "settings": raw, "error": str(exc)})
    return jsonify({"ok": True, "settings": serialize_settings(settings)})


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
