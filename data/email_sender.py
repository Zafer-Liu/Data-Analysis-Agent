"""Email sender for cloud verification codes — QQ Mail SMTP (SSL).

Env vars:
    BAA_SMTP_USER  — QQ email address, e.g. "xxx@qq.com"
    BAA_SMTP_PASS  — QQ authorization code (授权码, not the login password)

If either is missing, the module logs a warning and ``send_code`` returns
False — callers should handle this gracefully.
"""
from __future__ import annotations

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

log = logging.getLogger(__name__)

_SMTP_HOST = "smtp.qq.com"
_SMTP_PORT = 465  # SSL

_USER = os.environ.get("BAA_SMTP_USER", "")
_PASS = os.environ.get("BAA_SMTP_PASS", "")


def is_configured() -> bool:
    return bool(_USER and _PASS)


def send_code(to_email: str, code: str) -> bool:
    """Send a 6-digit verification code via QQ Mail SMTP.

    Returns True on success, False on failure (logs the error).
    """
    if not is_configured():
        log.warning("[email] SMTP not configured — set BAA_SMTP_USER / BAA_SMTP_PASS")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = _USER
    msg["To"] = to_email
    msg["Subject"] = "智析Agent — 登录验证码"

    html = (
        '<div style="font-family:system-ui,sans-serif;max-width:400px;margin:0 auto;padding:32px">'
        '<h2 style="color:#6366f1;margin:0 0 16px">智析Agent 验证码</h2>'
        '<p style="color:#475569;font-size:14px">您的验证码是：</p>'
        f'<div style="font-size:32px;font-weight:700;letter-spacing:8px;color:#6366f1;'
        f'text-align:center;padding:24px 0;border-radius:12px;background:#f1f5f9;margin:16px 0">{code}</div>'
        '<p style="color:#94a3b8;font-size:12px">验证码 5 分钟内有效，请尽快使用。</p>'
        '<p style="color:#94a3b8;font-size:12px">如非本人操作，请忽略此邮件。</p>'
        '</div>'
    )
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT, timeout=15) as server:
            server.login(_USER, _PASS)
            server.sendmail(_USER, to_email, msg.as_string())
        log.info("[email] verification code sent to %s", to_email)
        return True
    except Exception as e:
        log.error("[email] failed to send code to %s: %s", to_email, e)
        return False
