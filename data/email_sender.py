"""Email sender for cloud verification codes — QQ Mail SMTP.

Tries SSL (port 465) first, falls back to STARTTLS (port 587).
Forces IPv4 to avoid "Network is unreachable" on cloud platforms
where IPv6 routing is broken.

Env vars:
    BAA_SMTP_USER  — QQ email address, e.g. "xxx@qq.com"
    BAA_SMTP_PASS  — QQ authorization code (授权码, not the login password)
"""
from __future__ import annotations

import os
import socket
import ssl
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

log = logging.getLogger(__name__)

_SMTP_HOST = "smtp.qq.com"
_SMTP_PORT_SSL = 465
_SMTP_PORT_STARTTLS = 587

_USER = os.environ.get("BAA_SMTP_USER", "")
_PASS = os.environ.get("BAA_SMTP_PASS", "")


def is_configured() -> bool:
    return bool(_USER and _PASS)


def _resolve_ipv4(host: str) -> str | None:
    """Resolve hostname to first IPv4 address (forces AF_INET)."""
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
        if infos:
            return infos[0][4][0]
    except Exception as e:
        log.warning("[email] DNS resolve %s failed: %s", host, e)
    return None


def _send_ssl(ip: str, raw: str, to_email: str) -> bool:
    """Try SSL on port 465."""
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(ip, _SMTP_PORT_SSL, timeout=15, context=ctx) as server:
        server.login(_USER, _PASS)
        server.sendmail(_USER, to_email, raw)
    return True


def _send_starttls(ip: str, raw: str, to_email: str) -> bool:
    """Try STARTTLS on port 587."""
    ctx = ssl.create_default_context()
    with smtplib.SMTP(ip, _SMTP_PORT_STARTTLS, timeout=15) as server:
        server.ehlo()
        server.starttls(context=ctx)
        server.ehlo()
        server.login(_USER, _PASS)
        server.sendmail(_USER, to_email, raw)
    return True


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
    raw = msg.as_string()

    # Force IPv4 — cloud platforms (Railway) often have broken IPv6 routing
    ip = _resolve_ipv4(_SMTP_HOST)
    if not ip:
        log.error("[email] cannot resolve %s to IPv4", _SMTP_HOST)
        return False

    log.info("[email] resolved %s -> %s, trying SSL:465 then STARTTLS:587", _SMTP_HOST, ip)

    for method, fn in (("SSL:465", _send_ssl), ("STARTTLS:587", _send_starttls)):
        try:
            fn(ip, raw, to_email)
            log.info("[email] verification code sent to %s via %s", to_email, method)
            return True
        except Exception as e:
            log.warning("[email] %s failed: %s", method, e)

    log.error("[email] all SMTP methods failed for %s", to_email)
    return False
