"""Authentication blueprint — email + verification code register / password login (cloud-only)."""
from __future__ import annotations

import os
import secrets

from flask import Blueprint, request, jsonify, session, render_template

from data.auth_store import (
    create_user, verify_user, get_user_by_id,
    check_quota, DAILY_TOKEN_LIMIT,
    generate_code, store_email_code, can_resend, consume_email_code,
)
from data.email_sender import send_code as _send_email_code, is_configured as _smtp_configured

bp = Blueprint("auth", __name__)

# Flask session requires a secret key; allow env override.
_DEFAULT_KEY = secrets.token_hex(32)
SECRET_KEY = os.environ.get("BAA_SECRET_KEY", _DEFAULT_KEY)


def is_cloud_managed() -> bool:
    return bool(os.environ.get("RAILWAY_PROJECT_ID")) or os.environ.get("VERCEL") == "1"


def current_user() -> dict | None:
    """Return the authenticated user dict from the Flask session, or None."""
    uid = session.get("uid")
    if not uid:
        return None
    return get_user_by_id(uid)


# ---------------------------------------------------------------------------
#  Pages
# ---------------------------------------------------------------------------

@bp.get("/login")
def login_page():
    """Serve the login page (cloud-only). Redirect to app if already authed."""
    if not is_cloud_managed():
        return ("", 403)
    if current_user():
        return render_template("agent_chat.html",
                              desktop_lifecycle_enabled=False,
                              is_cloud_managed=True)
    return render_template("login.html", quota_limit=DAILY_TOKEN_LIMIT)


# ---------------------------------------------------------------------------
#  Send verification code
# ---------------------------------------------------------------------------

@bp.post("/api/auth/send-code")
def send_verification_code():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "请输入有效邮箱"}), 400

    if not can_resend(email):
        return jsonify({"error": "发送太频繁，请 60 秒后再试"}), 429

    if not _smtp_configured():
        return jsonify({"error": "邮件服务未配置，请联系管理员"}), 503

    code = generate_code()
    store_email_code(email, code)

    ok = _send_email_code(email, code)
    if not ok:
        return jsonify({"error": "验证码发送失败，请稍后重试"}), 500

    return jsonify({"ok": True, "message": "验证码已发送至邮箱"})


# ---------------------------------------------------------------------------
#  Register (email + verification code + password)
# ---------------------------------------------------------------------------

@bp.post("/api/auth/register")
def register():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    code = (data.get("code") or "").strip()
    password = data.get("password") or ""

    if not email or "@" not in email:
        return jsonify({"error": "请输入有效邮箱"}), 400
    if len(password) < 4:
        return jsonify({"error": "密码至少 4 个字符"}), 400
    if not code or len(code) != 6:
        return jsonify({"error": "请输入 6 位验证码"}), 400

    if not consume_email_code(email, code):
        return jsonify({"error": "验证码无效或已过期"}), 400

    user = create_user(email, password)
    if not user:
        return jsonify({"error": "该邮箱已注册"}), 409

    session["uid"] = user["id"]
    return jsonify({"ok": True, "user": {"id": user["id"], "email": user["email"]}})


# ---------------------------------------------------------------------------
#  Login (email + password)
# ---------------------------------------------------------------------------

@bp.post("/api/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "请输入邮箱和密码"}), 400

    user = verify_user(email, password)
    if not user:
        return jsonify({"error": "邮箱或密码错误"}), 401

    session["uid"] = user["id"]
    return jsonify({"ok": True, "user": {"id": user["id"], "email": user["email"]}})


@bp.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.get("/api/auth/me")
def me():
    user = current_user()
    if not user:
        return jsonify({"authenticated": False}), 401
    quota = check_quota(user["id"])
    return jsonify({
        "authenticated": True,
        "user": {"id": user["id"], "email": user["email"]},
        "quota": quota,
    })
