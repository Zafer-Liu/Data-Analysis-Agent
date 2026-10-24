"""Read-only operations console for the managed Railway deployment."""
from __future__ import annotations

import os
from functools import wraps

from flask import (
    Blueprint, abort, g, jsonify, make_response, redirect, render_template,
    request, url_for,
)

from data.auth_store import (
    get_admin_overview,
    get_admin_trends,
    get_admin_user_detail,
    list_admin_audit_events,
    list_admin_users,
    record_admin_audit_event,
)
from .auth import current_user, is_cloud_managed

bp = Blueprint("admin", __name__)


def configured_admin_emails() -> frozenset[str]:
    """Resolve the deployment-managed administrator allowlist."""
    return frozenset(
        item.strip().lower()
        for item in (os.environ.get("BAA_ADMIN_EMAILS") or "").split(",")
        if item.strip()
    )


def is_admin_user(user: dict | None) -> bool:
    if not user:
        return False
    return str(user.get("email") or "").strip().lower() in configured_admin_emails()


def _audit(user: dict, action: str, *, target_user_id: str = "", detail: str = "") -> None:
    record_admin_audit_event(
        admin_user_id=user["id"],
        admin_email=user.get("email", ""),
        action=action,
        target_user_id=target_user_id,
        detail=detail,
    )


def require_admin_api(view):
    """Authorize an admin API without revealing the allowlist."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_cloud_managed():
            return jsonify({"error": "not_found"}), 404
        user = current_user()
        if not user:
            return jsonify({"error": "请先登录", "needs_auth": True}), 401
        if not is_admin_user(user):
            return jsonify({"error": "not_found"}), 404
        g.admin_user = user
        return view(*args, **kwargs)
    return wrapped


def _int_arg(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


@bp.get("/admin")
def admin_page():
    if not is_cloud_managed():
        abort(404)
    user = current_user()
    if not user:
        return redirect(url_for("auth.login_page"))
    if not is_admin_user(user):
        abort(404)
    _audit(user, "admin_page_view")
    response = make_response(render_template(
        "admin.html",
        admin_email=user.get("email", ""),
    ))
    response.headers["Cache-Control"] = "no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@bp.get("/api/admin/overview")
@require_admin_api
def admin_overview():
    return jsonify(get_admin_overview())


@bp.get("/api/admin/trends")
@require_admin_api
def admin_trends():
    return jsonify(get_admin_trends(_int_arg("days", 30, 7, 90)))


@bp.get("/api/admin/users")
@require_admin_api
def admin_users():
    sort = (request.args.get("sort") or "created_desc")[:30]
    result = list_admin_users(
        search=(request.args.get("search") or "")[:320],
        page=_int_arg("page", 1, 1, 100_000),
        page_size=_int_arg("page_size", 25, 1, 100),
        sort=sort,
    )
    return jsonify(result)


@bp.get("/api/admin/users/<user_id>")
@require_admin_api
def admin_user_detail(user_id: str):
    result = get_admin_user_detail(user_id[:100])
    if result is None:
        return jsonify({"error": "user_not_found"}), 404
    _audit(g.admin_user, "admin_user_view", target_user_id=user_id[:100])
    return jsonify(result)


@bp.get("/api/admin/audit")
@require_admin_api
def admin_audit():
    return jsonify(list_admin_audit_events(
        page=_int_arg("page", 1, 1, 100_000),
        page_size=_int_arg("page_size", 30, 1, 100),
    ))
