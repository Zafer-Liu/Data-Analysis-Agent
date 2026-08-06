"""Authentication blueprint — email + verification code register / password login (cloud-only)."""
from __future__ import annotations

import logging
import os
import secrets

from flask import Blueprint, request, jsonify, session, render_template

from data.auth_store import (
    create_user, verify_user, get_user_by_id,
    check_quota, DAILY_TOKEN_LIMIT,
    generate_code, store_email_code, can_resend, consume_email_code,
)
from data.email_sender import send_code as _send_email_code, is_configured as _smtp_configured

log = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)


def get_or_create_secret_key() -> str:
    """Resolve the Flask session secret key.

    Priority:
      1. ``BAA_SECRET_KEY`` environment variable (highest precedence).
      2. ``{data_root}/secret_key`` file on disk (survives redeploys when
         ``data_root`` lives on a Railway mounted volume).
      3. Auto-generate a 64‑char hex key, persist it to disk so the next
         cold start reads the same value, and return it.
    """
    # 1. Explicit override via env
    env_val = os.environ.get("BAA_SECRET_KEY")
    if env_val:
        return env_val

    from infrastructure.paths import data_path
    key_file = data_path("secret_key")

    # 2. Read existing key from persistent disk
    if key_file.exists():
        try:
            content = key_file.read_text(encoding="utf-8").strip()
            if len(content) >= 32:
                return content
            log.warning("[auth] secret_key file too short (%d chars), regenerating", len(content))
        except Exception:
            log.exception("[auth] failed to read secret_key file, regenerating")

    # 3. First run — generate, persist, return
    new_key = secrets.token_hex(32)  # 64 hex chars
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(new_key, encoding="utf-8")
        log.info("[auth] generated and persisted new secret_key to %s", key_file)
    except Exception:
        log.exception("[auth] could not persist secret_key to %s — key lives in memory only", key_file)

    return new_key


SECRET_KEY = get_or_create_secret_key()


def is_cloud_managed() -> bool:
    return bool(os.environ.get("RAILWAY_PROJECT_ID")) or os.environ.get("VERCEL") == "1"


def current_user() -> dict | None:
    """Return the authenticated user dict from the Flask session, or None."""
    uid = session.get("uid")
    if not uid:
        return None
    return get_user_by_id(uid)


def _load_agreement_html() -> str:
    """Read Information/User_Agreement.md and convert to simple HTML."""
    import pathlib
    import traceback
    md_path = pathlib.Path(__file__).resolve().parent.parent / "Information" / "User_Agreement.md"
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        _log.exception("Failed to load user agreement from %s", md_path)
        return f"<p>用户协议加载失败。（路径: {md_path}）</p>"

    lines = text.splitlines()
    html_parts = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped == "---":
            continue
        if stripped.startswith("> "):
            continue  # skip blockquote meta
        if stripped.startswith("## "):
            html_parts.append(f"<h4>{stripped[3:]}</h4>")
        elif stripped.startswith("# "):
            html_parts.append(f"<h3>{stripped[2:]}</h3>")
        else:
            html_parts.append(f"<p>{stripped}</p>")
    return "\n".join(html_parts)


def _agreement_ctx() -> dict:
    """Template context dict with agreement HTML."""
    return {"agreement_html": _load_agreement_html()}


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
    return render_template("login.html", quota_limit=DAILY_TOKEN_LIMIT, **_agreement_ctx())


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
