"""Email sender for cloud verification codes.

Strategy:
  1. If RESEND_API_KEY is set → use Resend HTTP API (works on Railway,
     no SMTP port restrictions, free 100 emails/day).
  2. Else if BAA_SMTP_USER/PASS set → try QQ Mail SMTP (SSL:465 then
     STARTTLS:587, forced IPv4).  Will likely fail on Railway but works
     on platforms that allow outbound SMTP.

Env vars:
  Resend (recommended for Railway):
    RESEND_API_KEY     — "re_xxxx..." from resend.com
    RESEND_FROM_EMAIL  — sender address (default: onboarding@resend.dev)

  SMTP (for platforms that allow outbound SMTP):
    BAA_SMTP_USER  — QQ email address
    BAA_SMTP_PASS  — QQ authorization code
"""
from __future__ import annotations

import os
import json
import socket
import ssl
import smtplib
import urllib.request
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

log = logging.getLogger(__name__)

# --- Resend (HTTP API) ---
_RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
_RESEND_FROM = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
_RESEND_URL = "https://api.resend.com/emails"

# --- SMTP fallback ---
_SMTP_HOST = "smtp.qq.com"
_SMTP_PORT_SSL = 465
_SMTP_PORT_STARTTLS = 587
_SMTP_USER = os.environ.get("BAA_SMTP_USER", "")
_SMTP_PASS = os.environ.get("BAA_SMTP_PASS", "")


def is_configured() -> bool:
    return bool(_RESEND_API_KEY) or bool(_SMTP_USER and _SMTP_PASS)


def _build_html(code: str) -> str:
    return (
        '<div style="font-family:system-ui,sans-serif;max-width:400px;margin:0 auto;padding:32px">'
        '<h2 style="color:#6366f1;margin:0 0 16px">智析Agent 验证码</h2>'
        '<p style="color:#475569;font-size:14px">您的验证码是：</p>'
        f'<div style="font-size:32px;font-weight:700;letter-spacing:8px;color:#6366f1;'
        f'text-align:center;padding:24px 0;border-radius:12px;background:#f1f5f9;margin:16px 0">{code}</div>'
        '<p style="color:#94a3b8;font-size:12px">验证码 5 分钟内有效，请尽快使用。</p>'
        '<p style="color:#94a3b8;font-size:12px">如非本人操作，请忽略此邮件。</p>'
        '</div>'
    )


# ---------------------------------------------------------------------------
#  Resend HTTP API (primary — works on Railway)
# ---------------------------------------------------------------------------

def _send_via_resend(to_email: str, code: str) -> bool:
    payload = json.dumps({
        "from": _RESEND_FROM,
        "to": [to_email],
        "subject": "智析Agent — 登录验证码",
        "html": _build_html(code),
    }).encode("utf-8")
    req = urllib.request.Request(
        _RESEND_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {_RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            log.info("[email] sent to %s via Resend (HTTP %d)", to_email, resp.status)
            return resp.status in (200, 201)
    except Exception as e:
        log.warning("[email] Resend failed: %s", e)
        return False


# ---------------------------------------------------------------------------
#  SMTP fallback (for platforms that allow outbound SMTP)
# ---------------------------------------------------------------------------

def _resolve_ipv4(host: str) -> str | None:
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
        if infos:
            return infos[0][4][0]
    except Exception as e:
        log.warning("[email] DNS resolve %s failed: %s", host, e)
    return None


def _send_smtp(ip: str, raw: str, to_email: str) -> bool:
    """Try SSL:465 then STARTTLS:587."""
    for port, method in ((_SMTP_PORT_SSL, "SSL"), (_SMTP_PORT_STARTTLS, "STARTTLS")):
        try:
            if method == "SSL":
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL(ip, port, timeout=15, context=ctx) as s:
                    s.login(_SMTP_USER, _SMTP_PASS)
                    s.sendmail(_SMTP_USER, to_email, raw)
            else:
                ctx = ssl.create_default_context()
                with smtplib.SMTP(ip, port, timeout=15) as s:
                    s.ehlo(); s.starttls(context=ctx); s.ehlo()
                    s.login(_SMTP_USER, _SMTP_PASS)
                    s.sendmail(_SMTP_USER, to_email, raw)
            log.info("[email] sent to %s via SMTP %s:%d", to_email, method, port)
            return True
        except Exception as e:
            log.warning("[email] SMTP %s:%d failed: %s", method, port, e)
    return False


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------

def send_code(to_email: str, code: str) -> bool:
    """Send verification code. Returns True on success."""
    if not is_configured():
        log.warning("[email] no email service configured")
        return False

    # 1. Resend HTTP API (works on Railway — no SMTP port blocking)
    if _RESEND_API_KEY:
        return _send_via_resend(to_email, code)

    # 2. SMTP fallback (Railway blocks this, but works elsewhere)
    ip = _resolve_ipv4(_SMTP_HOST)
    if not ip:
        log.error("[email] cannot resolve %s", _SMTP_HOST)
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = _SMTP_USER
    msg["To"] = to_email
    msg["Subject"] = "智析Agent — 登录验证码"
    msg.attach(MIMEText(_build_html(code), "html", "utf-8"))

    return _send_smtp(ip, msg.as_string(), to_email)
