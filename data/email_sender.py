"""Email sender for cloud verification codes — Resend HTTP API.

Uses Resend (api.resend.com) over HTTPS, which works on Railway
where SMTP ports are blocked.  Free tier: 100 emails/day.

Env vars:
    RESEND_API_KEY     — "re_xxxx..." from resend.com
    RESEND_FROM_EMAIL  — verified sender, e.g. "noreply@zaferliu.me"
"""
from __future__ import annotations

import os
import json
import urllib.request
import urllib.error
import logging

log = logging.getLogger(__name__)

_RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
_RESEND_FROM = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
_RESEND_URL = "https://api.resend.com/emails"


def is_configured() -> bool:
    return bool(_RESEND_API_KEY)


def _mail_copy(purpose: str) -> tuple[str, str, str]:
    if purpose == "password_reset":
        return (
            "智析Agent — 重置密码验证码",
            "重置密码",
            "你正在重置智析 Agent 的登录密码。",
        )
    return (
        "智析Agent — 注册验证码",
        "验证邮箱",
        "你正在注册智析 Agent。",
    )


def _build_html(code: str, purpose: str = "register") -> str:
    _, heading, description = _mail_copy(purpose)
    return (
        '<div style="font-family:system-ui,sans-serif;max-width:400px;margin:0 auto;padding:32px">'
        f'<h2 style="color:#6366f1;margin:0 0 16px">智析Agent · {heading}</h2>'
        f'<p style="color:#475569;font-size:14px">{description}</p>'
        '<p style="color:#475569;font-size:14px">验证码：</p>'
        f'<div style="font-size:32px;font-weight:700;letter-spacing:8px;color:#6366f1;'
        f'text-align:center;padding:24px 0;border-radius:12px;background:#f1f5f9;margin:16px 0">{code}</div>'
        '<p style="color:#94a3b8;font-size:12px">验证码 5 分钟内有效，请尽快使用。</p>'
        '<p style="color:#94a3b8;font-size:12px">如非本人操作，请忽略此邮件。</p>'
        '</div>'
    )


def send_code(to_email: str, code: str, purpose: str = "register") -> bool:
    """Send verification code via Resend HTTP API. Returns True on success."""
    if not is_configured():
        log.warning("[email] RESEND_API_KEY not configured")
        return False

    subject, _, _ = _mail_copy(purpose)
    payload = json.dumps({
        "from": _RESEND_FROM,
        "to": [to_email],
        "subject": subject,
        "html": _build_html(code, purpose),
    }).encode("utf-8")

    req = urllib.request.Request(
        _RESEND_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {_RESEND_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "BAA-Auth/1.0 (+https://zaferliu.me)",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
            log.info("[email] sent to %s via Resend (HTTP %d)", to_email, resp.status)
            return resp.status in (200, 201)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        log.warning("[email] Resend failed: HTTP %d %s | body=%s", e.code, e.reason, err_body[:500])
        return False
    except Exception as e:
        log.warning("[email] Resend failed: %s", e)
        return False
