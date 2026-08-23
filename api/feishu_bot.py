"""Settings and connectivity endpoints for a Feishu application bot."""
from __future__ import annotations

import hmac
import json
import logging
import re
import threading

from flask import Blueprint, current_app, jsonify, request

from data.feishu_bot_service import (
    FeishuBotError,
    event_verification_token,
    get_status,
    list_joined_chats,
    send_test_message,
    validate_app_id,
    validate_receive_target,
)
from data.feishu_bot_store import save_config
from infrastructure.credential_store import CredentialStoreError
from .state import require_session_ownership, session_manager


bp = Blueprint("feishu_bot", __name__)
log = logging.getLogger(__name__)
_EVENT_LOCK = threading.RLock()
_RECENT_EVENT_IDS: set[str] = set()
_MAX_RECENT_EVENT_IDS = 2_000


@bp.get("/api/feishu-bot")
def get_feishu_bot():
    return jsonify({"ok": True, "connection": get_status().to_dict()})


@bp.put("/api/feishu-bot")
def put_feishu_bot():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求体必须是 JSON 对象"}), 400
    current = get_status()
    enabled = payload.get("enabled", current.enabled)
    if not isinstance(enabled, bool):
        return jsonify({"ok": False, "error": "enabled 必须是布尔值"}), 400
    app_secret = payload.get("app_secret")
    if app_secret is not None:
        if not isinstance(app_secret, str):
            return jsonify({"ok": False, "error": "App Secret 必须是文本"}), 400
        app_secret = app_secret.strip() or None
    verification_token = payload.get("event_verification_token")
    if verification_token is not None:
        if not isinstance(verification_token, str):
            return jsonify({"ok": False, "error": "事件校验 Token 必须是文本"}), 400
        verification_token = verification_token.strip() or None
    inbound_transport = payload.get("inbound_transport", current.inbound_transport)
    if inbound_transport not in {"long_connection", "webhook"}:
        return jsonify({"ok": False, "error": "入站方式仅支持长连接或 Webhook"}), 400
    receive_id = payload.get("receive_id", "")
    try:
        app_id = validate_app_id(payload.get("app_id", ""))
        receive_id_type = payload.get("receive_id_type", "chat_id")
        if str(receive_id or "").strip():
            receive_id_type, receive_id = validate_receive_target(receive_id_type, receive_id)
        elif receive_id_type != "chat_id":
            raise ValueError("未选择目标群时，接收对象类型必须为 chat_id")
        else:
            receive_id = ""
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    try:
        save_config(
            enabled=enabled,
            app_id=app_id,
            app_secret=app_secret,
            event_verification_token=verification_token,
            inbound_transport=inbound_transport,
            receive_id_type=receive_id_type,
            receive_id=receive_id,
        )
    except (CredentialStoreError, ValueError) as exc:
        return jsonify({"ok": False, "error": f"无法保存飞书机器人配置：{exc}"}), 503
    if inbound_transport == "long_connection":
        try:
            from infrastructure.feishu_long_connection import start_long_connection

            start_long_connection(current_app._get_current_object())
        except Exception:
            log.exception("[feishu] long connection startup request failed")
    return jsonify({"ok": True, "connection": get_status().to_dict()})


@bp.get("/api/feishu-bot/chats")
def list_feishu_bot_chats():
    try:
        chats = list_joined_chats()
    except FeishuBotError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "chats": chats})


@bp.post("/api/feishu-bot/test")
def test_feishu_bot():
    try:
        send_test_message()
    except FeishuBotError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "message": "测试消息已发送到飞书群。"})


def _conversation_payload(sid: str) -> dict:
    session = session_manager.get_or_create(sid)
    connection = get_status()
    return {
        "ok": True,
        "configured": connection.configured,
        "application_enabled": connection.enabled,
        "connected": bool(session.feishu_bot_enabled and session.feishu_chat_id),
        "chat_id": session.feishu_chat_id,
        "chat_name": session.feishu_chat_name,
    }


@bp.get("/api/session/<sid>/feishu-bot")
@require_session_ownership
def get_conversation_feishu_bot(sid: str):
    return jsonify(_conversation_payload(sid))


@bp.put("/api/session/<sid>/feishu-bot")
@require_session_ownership
def put_conversation_feishu_bot(sid: str):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("enabled"), bool):
        return jsonify({"ok": False, "error": "enabled 必须是布尔值"}), 400
    session = session_manager.get_or_create(sid)
    enabled = payload["enabled"]
    if not enabled:
        session.feishu_bot_enabled = False
        session.feishu_chat_id = ""
        session.feishu_chat_name = ""
        return jsonify(_conversation_payload(sid))

    connection = get_status()
    if not (connection.enabled and connection.configured):
        return jsonify({
            "ok": False,
            "error": "请先在“应用设置 → 飞书机器人”启用发送并选择目标群。",
        }), 409
    try:
        available_chats = list_joined_chats()
    except FeishuBotError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    requested_chat_id = str(payload.get("chat_id") or connection.receive_id).strip()
    selected_chat = next(
        (item for item in available_chats if item["chat_id"] == requested_chat_id),
        None,
    )
    if selected_chat is None:
        return jsonify({
            "ok": False,
            "error": "目标群不在当前机器人可见范围内，请刷新群列表后重新选择。",
        }), 400
    session.feishu_bot_enabled = True
    session.feishu_chat_id = selected_chat["chat_id"]
    session.feishu_chat_name = selected_chat["name"][:120]
    return jsonify(_conversation_payload(sid))


@bp.get("/api/session/<sid>/feishu-bot/events")
@require_session_ownership
def get_conversation_feishu_events(sid: str):
    """Return only completed Feishu-originated messages for Web live sync."""
    session = session_manager.get(sid)
    if session is None:
        return jsonify({"ok": False, "error": "会话不存在"}), 404
    try:
        after = max(0, int(request.args.get("after", "0")))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "after 必须是非负整数"}), 400
    revision, events = session.feishu_inbound_events_after(after)
    return jsonify({"ok": True, "revision": revision, "events": events})


def _event_token_matches(payload: dict) -> bool:
    try:
        expected = event_verification_token()
    except FeishuBotError:
        return False
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    supplied = str(header.get("token") or payload.get("token") or "")
    return bool(supplied) and hmac.compare_digest(expected, supplied)


def _remember_event(event_id: str) -> bool:
    """Return False for a retried Feishu event without retaining message text."""
    if not event_id:
        return True
    with _EVENT_LOCK:
        if event_id in _RECENT_EVENT_IDS:
            return False
        if len(_RECENT_EVENT_IDS) >= _MAX_RECENT_EVENT_IDS:
            _RECENT_EVENT_IDS.clear()
        _RECENT_EVENT_IDS.add(event_id)
    return True


def _event_prompt(event: dict) -> tuple[str, str] | None:
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    if sender.get("sender_type") != "user" or message.get("chat_type") != "group":
        return None
    # The app requests only @机器人 message events.  Requiring a mention here
    # prevents ordinary group traffic from being silently injected into a Web
    # analysis session if broader events are enabled later.
    if not message.get("mentions"):
        return None
    if message.get("message_type") != "text":
        return None
    try:
        content = json.loads(str(message.get("content") or "{}"))
    except ValueError:
        return None
    text = str(content.get("text") or "").strip() if isinstance(content, dict) else ""
    text = re.sub(r"@_user_\d+\s*", "", text).strip()
    chat_id = str(message.get("chat_id") or "").strip()
    return (chat_id, text) if chat_id and text else None


def _run_feishu_turn(app, sid: str, message: str) -> None:
    """Run the existing chat path in a private request context for a bot event."""
    try:
        from .chat import chat_stream

        session = session_manager.get(sid)
        if session is None:
            return

        # A group conversation is one shared Agent context.  Serialize inbound
        # turns so two quick mobile @mentions cannot interleave model history.
        with session._feishu_turn_lock:
            with app.test_request_context(
                f"/api/session/{sid}/chat",
                method="POST",
                json={"message": message, "memory_enabled": True},
            ):
                from flask import g

                g.feishu_inbound = True
                response = chat_stream(sid)
                for _chunk in response.response:
                    pass
                response.close()
    except Exception:
        log.exception("[feishu] inbound turn failed sid=%s", sid)


def dispatch_inbound_event(app, event: dict, event_id: str = "") -> bool:
    """Enqueue a verified event from either Webhook or long connection."""
    if not _remember_event(event_id):
        return False
    parsed = _event_prompt(event)
    if parsed is None:
        return False
    chat_id, message = parsed
    session = session_manager.find_feishu_chat(chat_id)
    if session is None:
        # A group is intentionally inert until a Web user explicitly links a
        # session with /robot; do not create unowned sessions from group chat.
        return False
    thread = threading.Thread(
        target=_run_feishu_turn,
        args=(app, session.session_id, message),
        daemon=True,
        name="feishu-inbound-turn",
    )
    thread.start()
    return True


@bp.post("/api/feishu-bot/events")
def receive_feishu_event():
    """Webhook fallback: verify and enqueue @robot group messages."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"code": 400, "message": "invalid JSON"}), 400
    if not _event_token_matches(payload):
        return jsonify({"code": 401, "message": "invalid event token"}), 401
    if payload.get("type") == "url_verification":
        return jsonify({"challenge": str(payload.get("challenge") or "")})

    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    if header.get("event_type") != "im.message.receive_v1":
        return jsonify({"ok": True})
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    accepted = dispatch_inbound_event(
        current_app._get_current_object(), event, str(header.get("event_id") or ""),
    )
    return jsonify({"ok": True, "accepted": accepted})
