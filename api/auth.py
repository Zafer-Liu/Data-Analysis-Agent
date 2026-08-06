"""Authentication blueprint — register / login / logout / me (cloud-managed only)."""
from __future__ import annotations

import os
import secrets

from flask import Blueprint, request, jsonify, session, render_template

from data.auth_store import (
    create_user, verify_user, get_user_by_id,
    check_quota, DAILY_TOKEN_LIMIT,
)

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


@bp.get("/login")
def login_page():
    """Serve the login page (cloud-only). Redirect to app if already authed."""
    if not is_cloud_managed():
        return ("", 403)
    if current_user():
        return render_template("agent_chat.html",
                              desktop_lifecycle_enabled=False,
                              is_cloud_managed=True)
    return render_template("login.html")


@bp.post("/api/auth/register")
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or len(username) < 2:
        return jsonify({"error": "用户名至少 2 个字符"}), 400
    if len(password) < 4:
        return jsonify({"error": "密码至少 4 个字符"}), 400
    user = create_user(username, password)
    if not user:
        return jsonify({"error": "用户名已被占用"}), 409
    session["uid"] = user["id"]
    return jsonify({"ok": True, "user": {"id": user["id"], "username": user["username"]}})


@bp.post("/api/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "请输入用户名和密码"}), 400
    user = verify_user(username, password)
    if not user:
        return jsonify({"error": "用户名或密码错误"}), 401
    session["uid"] = user["id"]
    return jsonify({"ok": True, "user": {"id": user["id"], "username": user["username"]}})


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
        "user": {"id": user["id"], "username": user["username"]},
        "quota": quota,
    })
