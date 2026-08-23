"""Safe outbound delivery through a Feishu application bot."""
from __future__ import annotations

import json
from dataclasses import dataclass

import requests

from data.feishu_bot_store import load_config
from infrastructure.credential_store import CredentialStoreError, load_secret


_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
_CHATS_URL = "https://open.feishu.cn/open-apis/im/v1/chats"
_REQUEST_TIMEOUT = (3.05, 10)
_RECEIVE_ID_TYPES = {"chat_id", "open_id"}
_CHAT_LIST_ERROR_HINTS = {
    99991672: (
        "飞书返回 99991672：缺少群组读取权限。请在开放平台的权限管理中申请 "
        "“获取群组信息”（im:chat:readonly）或“获取与更新群组信息”（im:chat），然后发布新版本。"
    ),
    232001: "飞书返回 232001：群列表请求参数无效，请刷新后重试。",
    232004: "飞书返回 232004：App ID 不存在，请检查应用凭据。",
    232025: "飞书返回 232025：请在飞书开放平台为应用开启“机器人”能力，并发布新版本。",
    232034: "飞书返回 232034：应用在当前企业未启用，请在管理后台安装或启用该应用。",
}


class FeishuBotError(RuntimeError):
    """A user-facing Feishu robot configuration or delivery error."""


@dataclass(frozen=True)
class FeishuBotStatus:
    enabled: bool
    configured: bool
    app_id: str
    app_id_masked: str
    app_secret_configured: bool
    event_verification_token_configured: bool
    inbound_transport: str
    receive_id_type: str
    receive_id: str
    receive_id_masked: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "app_id": self.app_id,
            "app_id_masked": self.app_id_masked,
            "app_secret_configured": self.app_secret_configured,
            "event_verification_token_configured": self.event_verification_token_configured,
            "inbound_transport": self.inbound_transport,
            "receive_id_type": self.receive_id_type,
            "receive_id": self.receive_id,
            "receive_id_masked": self.receive_id_masked,
            "updated_at": self.updated_at,
        }


def validate_app_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("App ID 必须是文本")
    app_id = value.strip()
    if not app_id or len(app_id) > 128 or any(char.isspace() for char in app_id):
        raise ValueError("请填写有效的飞书 App ID")
    return app_id


def validate_receive_target(receive_id_type: object, receive_id: object) -> tuple[str, str]:
    if receive_id_type not in _RECEIVE_ID_TYPES:
        raise ValueError("接收对象类型仅支持群聊 chat_id 或用户 open_id")
    if not isinstance(receive_id, str):
        raise ValueError("接收对象 ID 必须是文本")
    value = receive_id.strip()
    if not value or len(value) > 256 or any(char.isspace() for char in value):
        raise ValueError("请填写有效的目标群 chat_id 或用户 open_id")
    return str(receive_id_type), value


def _masked_value(value: str) -> str:
    suffix = value[-6:] if len(value) > 6 else value
    return f"…{suffix}" if suffix else ""


def get_status() -> FeishuBotStatus:
    config = load_config()
    has_secret = bool(config["app_secret_ref"])
    configured = bool(config["app_id"] and has_secret and config["receive_id"])
    return FeishuBotStatus(
        enabled=config["enabled"],
        configured=configured,
        app_id=config["app_id"],
        app_id_masked=_masked_value(config["app_id"]),
        app_secret_configured=has_secret,
        event_verification_token_configured=bool(config["event_verification_token_ref"]),
        inbound_transport=config["inbound_transport"],
        receive_id_type=config["receive_id_type"],
        receive_id=config["receive_id"],
        receive_id_masked=_masked_value(config["receive_id"]),
        updated_at=config["updated_at"],
    )


def _configured_application() -> tuple[str, str, str, str]:
    config = load_config()
    app_id, app_secret = _configured_credentials(config)
    try:
        receive_id_type, receive_id = validate_receive_target(config["receive_id_type"], config["receive_id"])
    except ValueError as exc:
        raise FeishuBotError("请先从群列表中选择一个目标群") from exc
    return app_id, app_secret, receive_id_type, receive_id


def _configured_credentials(config: dict | None = None) -> tuple[str, str]:
    config = config or load_config()
    try:
        app_id = validate_app_id(config["app_id"])
    except ValueError as exc:
        raise FeishuBotError("请先保存飞书 App ID") from exc
    if not config["app_secret_ref"]:
        raise FeishuBotError("请先保存飞书 App Secret")
    try:
        app_secret = load_secret(config["app_secret_ref"])
    except (CredentialStoreError, ValueError) as exc:
        raise FeishuBotError(f"无法读取飞书 App Secret：{exc}") from exc
    return app_id, app_secret


def event_verification_token() -> str:
    """Load the event callback verifier only when the public endpoint needs it."""
    config = load_config()
    reference = config["event_verification_token_ref"]
    if not reference:
        raise FeishuBotError("请先在飞书机器人设置中保存事件校验 Token")
    try:
        return load_secret(reference)
    except (CredentialStoreError, ValueError) as exc:
        raise FeishuBotError(f"无法读取飞书事件校验 Token：{exc}") from exc


def _tenant_access_token(app_id: str, app_secret: str) -> str:
    try:
        response = requests.post(_TOKEN_URL, json={"app_id": app_id, "app_secret": app_secret}, timeout=_REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise FeishuBotError("无法连接飞书开放平台，请检查网络") from exc
    if not response.ok:
        raise FeishuBotError(f"飞书凭证验证返回 HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise FeishuBotError("飞书凭证验证返回了无效响应") from exc
    token = body.get("tenant_access_token") if isinstance(body, dict) else ""
    if not token:
        raise FeishuBotError("飞书未接受 App ID 或 App Secret，请检查应用状态和凭据")
    return str(token)


def list_joined_chats() -> list[dict[str, str]]:
    """List groups visible to the configured application bot for UI selection."""
    app_id, app_secret = _configured_credentials()
    access_token = _tenant_access_token(app_id, app_secret)
    page_token = ""
    chats: list[dict[str, str]] = []
    # The picker needs a bounded snapshot, not an unbounded tenant crawl.
    for _ in range(3):
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        try:
            response = requests.get(
                _CHATS_URL,
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise FeishuBotError("无法读取飞书群列表，请检查网络") from exc
        if not response.ok:
            try:
                error_body = response.json()
            except ValueError:
                error_body = {}
            code = error_body.get("code") if isinstance(error_body, dict) else None
            if isinstance(code, int) and code in _CHAT_LIST_ERROR_HINTS:
                raise FeishuBotError(_CHAT_LIST_ERROR_HINTS[code])
            if isinstance(code, int):
                raise FeishuBotError(f"飞书群列表请求被拒绝（错误码 {code}）；请检查机器人能力、群组信息权限和应用发布状态。")
            raise FeishuBotError(f"飞书群列表接口返回 HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise FeishuBotError("飞书群列表返回了无效响应") from exc
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise FeishuBotError("飞书拒绝读取群列表，请开通获取群组信息权限并发布应用")
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            chat_id = str(item.get("chat_id") or "").strip()
            if chat_id:
                chats.append({"chat_id": chat_id, "name": str(item.get("name") or "未命名群")})
        if not data.get("has_more"):
            break
        page_token = str(data.get("page_token") or "")
        if not page_token:
            break
    return chats


def send_text(
    text: str,
    *,
    receive_id: str | None = None,
    receive_id_type: str | None = None,
) -> None:
    """Post a plain-text message without exposing secrets or response bodies.

    A linked Web conversation supplies its own previously-selected ``chat_id``;
    the settings-page test continues to use the configured default target.
    """
    message = str(text or "").strip()
    if not message:
        raise FeishuBotError("发送内容不能为空")
    if len(message) > 4000:
        raise FeishuBotError("飞书机器人单条测试消息不能超过 4000 个字符")
    if receive_id is None:
        app_id, app_secret, target_type, target_id = _configured_application()
    else:
        app_id, app_secret = _configured_credentials()
        try:
            target_type, target_id = validate_receive_target(
                receive_id_type or "chat_id", receive_id,
            )
        except ValueError as exc:
            raise FeishuBotError("飞书会话目标无效，请重新执行 /robot 连接") from exc
    access_token = _tenant_access_token(app_id, app_secret)
    payload = {
        "receive_id": target_id,
        "msg_type": "text",
        "content": json.dumps({"text": message}, ensure_ascii=False),
    }
    try:
        response = requests.post(
            _MESSAGE_URL,
            params={"receive_id_type": target_type},
            json=payload,
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"},
            timeout=_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise FeishuBotError("无法发送飞书机器人消息，请检查网络") from exc
    if not response.ok:
        raise FeishuBotError(f"飞书机器人消息接口返回 HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        body = {}
    if isinstance(body, dict) and body.get("code") not in (None, 0):
        raise FeishuBotError("飞书拒绝了该消息，请检查机器人是否已加入目标群以及应用权限")


def send_conversation_turn(*, chat_id: str, user_message: str, assistant_message: str) -> None:
    """Mirror one completed Web turn in order, splitting long replies safely."""
    parts = [
        ("🧑 Web 对话", str(user_message or "").strip()),
        ("🤖 智析 Agent", str(assistant_message or "").strip()),
    ]
    for label, content in parts:
        if not content:
            continue
        prefix = f"{label}\n"
        limit = 4000 - len(prefix)
        while content:
            chunk, content = content[:limit], content[limit:]
            send_text(prefix + chunk, receive_id=chat_id, receive_id_type="chat_id")


def send_test_message() -> None:
    send_text("智析 Agent 已成功连接飞书应用机器人。后续分析结果将可同步至此群。")
