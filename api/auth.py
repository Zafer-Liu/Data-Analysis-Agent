"""Authentication blueprint — email + verification code register / password login.

Three run modes govern behaviour (see :func:`run_mode`):

* ``cloud``   — Railway/Vercel hosted; owns the user DB, SMTP registration, quota.
* ``desktop`` — PyWebView packaged client; proxies auth to the cloud backend and
  persists the issued token locally so the user does not re-login on every launch.
* ``local``   — ``python app.py`` direct launch; no auth, no quota, local-first.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import secrets
import urllib.error
import urllib.request

from flask import Blueprint, request, jsonify, session, render_template, url_for

from data.auth_store import (
    create_user, verify_user, get_user_by_id, get_user_by_email,
    reset_user_password,
    check_quota, DAILY_TOKEN_LIMIT,
    generate_code, store_email_code, can_resend, consume_email_code,
    record_auth_event,
    get_or_create_referral_code, get_referral_stats,
    get_sponsor_prompt_status, mark_sponsor_prompt_shown,
    save_desktop_token, get_desktop_token, clear_desktop_token,
)
from data.email_sender import send_code as _send_email_code, is_configured as _smtp_configured

log = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)

_DEFAULT_SPONSOR_EVM_ADDRESS = "0xdf90fe91ff172eca936c10ccb108540408d80d06"
_SPONSOR_TOKEN_CONTRACTS = {
    "usdt": "0x1E4a5963aBFD975d8c9021ce480b42188849D41d",
    "usdc": "0x74b7F16337b8972027F6196A17a631aC6dE26d22",
}
_DESKTOP_CLOUD_USER_AGENT = "BusinessAnalyticsAgentDesktop/1.3"


def _quota_with_model(uid: str) -> dict:
    """Attach safe model metadata to the daily gifted quota payload."""
    quota = check_quota(uid)
    try:
        from LLM.llm_config_manager import get_config_manager

        hosted = get_config_manager().list_hosted_models()
        model = next(
            (
                item for item in hosted
                if item.get("tier") == "free" and item.get("available")
            ),
            None,
        )
        if model:
            quota["model"] = {
                "id": model["id"],
                "display_name": model["display_name"],
                "public_model_name": model.get("public_model_name", ""),
                "tier": model["tier"],
            }
    except Exception:
        log.exception("[auth] failed to attach hosted model metadata")
    return quota


def _audit_auth(event_type: str, success: bool, *, user_id: str = "", email: str = "", detail: str = "") -> None:
    """Best-effort structured auth audit; never block the auth response."""
    try:
        record_auth_event(
            event_type=event_type,
            success=success,
            user_id=user_id,
            email=email,
            ip_address=str(request.remote_addr or ""),
            user_agent=str(request.user_agent.string or ""),
            detail=detail,
        )
    except Exception:
        log.exception("[auth] failed to persist audit event type=%s", event_type)


def _qr_data_url(value: str) -> str:
    """Generate a same-origin-safe SVG QR data URL without external services."""
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage

        image = qrcode.make(value, image_factory=SvgPathImage, box_size=6, border=3)
        output = io.BytesIO()
        image.save(output)
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        return f"data:image/svg+xml;base64,{encoded}"
    except Exception:
        log.exception("[auth] failed to generate QR code")
        return ""


def _sponsor_addresses() -> tuple[str, str]:
    """Resolve equivalent standard-EVM and XKO sponsor address formats."""
    configured = (os.environ.get("BAA_SPONSOR_XLAYER_ADDRESS") or "").strip()
    candidate = configured or _DEFAULT_SPONSOR_EVM_ADDRESS
    if re.fullmatch(r"0x[0-9a-fA-F]{40}", candidate):
        core = candidate[2:]
    elif re.fullmatch(r"XKO[0-9a-fA-F]{40}", candidate, flags=re.IGNORECASE):
        core = candidate[3:]
    else:
        log.warning("[auth] invalid BAA_SPONSOR_XLAYER_ADDRESS; using configured default")
        core = _DEFAULT_SPONSOR_EVM_ADDRESS[2:]
    return f"0x{core}", f"XKO{core}"


def _sponsor_contract(token: str) -> str:
    default = _SPONSOR_TOKEN_CONTRACTS[token]
    candidate = (
        os.environ.get(f"BAA_SPONSOR_{token.upper()}_CONTRACT") or default
    ).strip()
    if re.fullmatch(r"0x[0-9a-fA-F]{40}", candidate):
        return candidate
    log.warning("[auth] invalid sponsor %s contract; using default", token.upper())
    return default


def get_or_create_secret_key() -> str:
    """Resolve the Flask session secret key.

    Priority:
      1. ``BAA_SECRET_KEY`` environment variable (highest precedence).
      2. Durable account-storage ``secret_key`` file (survives redeploys on a
         Railway mounted volume).
      3. Auto-generate a 64‑char hex key, persist it to disk so the next
         cold start reads the same value, and return it.
    """
    # 1. Explicit override via env
    env_val = os.environ.get("BAA_SECRET_KEY")
    if env_val:
        return env_val

    from infrastructure.paths import account_data_path, is_cloud_managed_runtime
    key_file = account_data_path("secret_key")

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
        if is_cloud_managed_runtime():
            # An ephemeral key would invalidate every session after a restart
            # and is never an acceptable fallback for a hosted account system.
            raise
        log.exception("[auth] could not persist secret_key to %s — key lives in memory only", key_file)

    return new_key


SECRET_KEY = get_or_create_secret_key()


def is_cloud_managed() -> bool:
    return bool(os.environ.get("RAILWAY_PROJECT_ID")) or os.environ.get("VERCEL") == "1"


def is_desktop_client() -> bool:
    """True when launched as the packaged PyWebView desktop client.

    The launcher sets ``BAA_DESKTOP_LIFECYCLE=1`` (see desktop_launcher.py).
    """
    return os.environ.get("BAA_DESKTOP_LIFECYCLE") == "1"


def run_mode() -> str:
    """Classify the current process: ``cloud`` | ``desktop`` | ``local``.

    ``cloud`` and ``desktop`` both require authentication (one owns the user DB,
    the other proxies to it); ``local`` is the no-auth direct launch.
    """
    if is_cloud_managed():
        return "cloud"
    if is_desktop_client():
        return "desktop"
    return "local"


def _cloud_auth_base() -> str:
    """Root URL of the cloud backend the desktop client proxies auth to."""
    url = (os.environ.get("BAA_CLOUD_AUTH_URL") or "").strip().rstrip("/")
    return url


def _cloud_proxy(method: str, path: str, body: dict | None = None) -> tuple[dict, int]:
    """Forward an auth request to the cloud backend (desktop client only).

    Returns ``(parsed_json, http_status)``. On network failure returns a 503.
    """
    base = _cloud_auth_base()
    if not base:
        return {"error": "桌面端未配置云端验证地址"}, 503
    url = base + path
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        # Some managed networks and edge services reject Python's default
        # ``Python-urllib/*`` signature before the request reaches the cloud.
        # Use one stable product identifier for every desktop-to-cloud call.
        "User-Agent": _DESKTOP_CLOUD_USER_AGENT,
        # Tells the cloud side this call comes from the desktop proxy so it
        # can mint a desktop token directly (no browser session involved).
        "X-BAA-Desktop-Client": "1",
    }
    # Forward any existing desktop token so the cloud side can authorize /me etc.
    stored = get_desktop_token()
    if stored and stored.get("token"):
        headers["X-BAA-Desktop-Token"] = stored["token"]
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return payload, int(resp.status)
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw_body)
        except Exception:
            payload = {"error": exc.reason or "云端验证失败"}
        if exc.code == 403 and "error code: 1010" in raw_body.lower():
            payload = {
                "error": (
                    "云端验证请求被网络代理或边缘安全策略拦截。"
                    "请检查桌面端是否为最新版本后重试。"
                ),
                "code": "desktop_auth_edge_blocked",
            }
            return payload, 503
        return payload, int(exc.code)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        log.warning("[auth] desktop cloud proxy %s %s failed: %s", method, path, exc)
        return {"error": "无法连接云端验证服务"}, 503


def current_user() -> dict | None:
    """Return the authenticated user dict, or None.

    Cloud mode reads the Flask session; desktop mode reads the persisted local
    token row; local mode has no auth (returns None, callers short-circuit).
    """
    if is_desktop_client():
        stored = get_desktop_token()
        if not stored:
            return None
        return {"id": stored["user_id"], "email": stored.get("email") or ""}
    uid = session.get("uid")
    if not uid:
        return None
    user = get_user_by_id(uid)
    if not user:
        session.clear()
        return None
    try:
        session_version = int(session.get("auth_version") or 0)
    except (TypeError, ValueError):
        session_version = 0
    if session_version != int(user.get("auth_version") or 1):
        session.clear()
        return None
    return user


def _load_agreement_html() -> str:
    """Read Information/User_Agreement.md and convert to simple HTML."""
    import pathlib
    import traceback
    md_path = pathlib.Path(__file__).resolve().parent.parent / "Information" / "User_Agreement.md"
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        log.exception("Failed to load user agreement from %s", md_path)
        return "<p>用户协议加载失败。</p>"

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
    """Serve the login page (cloud or desktop). Redirect to app if already authed."""
    if not (is_cloud_managed() or is_desktop_client()):
        return ("", 403)
    if current_user():
        return render_template("agent_chat.html",
                              desktop_lifecycle_enabled=is_desktop_client(),
                              is_cloud_managed=is_cloud_managed(),
                              run_mode=run_mode())
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

    if is_desktop_client():
        payload, status = _cloud_proxy("POST", "/api/auth/send-code", data)
        return jsonify(payload), status

    if not can_resend(email, purpose="register"):
        return jsonify({"error": "发送太频繁，请 60 秒后再试"}), 429

    if not _smtp_configured():
        return jsonify({"error": "邮件服务未配置，请联系管理员"}), 503

    code = generate_code()
    store_email_code(email, code, purpose="register")

    ok = _send_email_code(email, code, purpose="register")
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
    referral_code = (data.get("ref") or "").strip()

    if is_desktop_client():
        payload, status = _cloud_proxy("POST", "/api/auth/register", data)
        # The cloud response carries a desktop_token for proxy callers; persist
        # it locally and strip it from the browser-facing response.
        if status == 200 and payload.get("desktop_token"):
            user = payload.get("user") or {}
            save_desktop_token(user.get("id", ""), user.get("email", ""), payload["desktop_token"])
            payload = {k: v for k, v in payload.items() if k != "desktop_token"}
        return jsonify(payload), status

    if not email or "@" not in email:
        return jsonify({"error": "请输入有效邮箱"}), 400
    if len(password) < 8:
        return jsonify({"error": "密码至少 8 个字符"}), 400
    if not code or len(code) != 6:
        return jsonify({"error": "请输入 6 位验证码"}), 400

    if not consume_email_code(email, code, purpose="register"):
        _audit_auth("register", False, email=email, detail="invalid_or_expired_code")
        return jsonify({"error": "验证码无效或已过期"}), 400

    user = create_user(email, password, referral_code=referral_code)
    if not user:
        _audit_auth("register", False, email=email, detail="email_already_registered")
        return jsonify({"error": "该邮箱已注册"}), 409

    session["uid"] = user["id"]
    session["auth_version"] = int(user.get("auth_version") or 1)
    _audit_auth("register", True, user_id=user["id"], email=user["email"])
    resp = {"ok": True, "user": {"id": user["id"], "email": user["email"]}}
    if _is_desktop_proxy_request():
        resp["desktop_token"] = _mint_desktop_token(user)
    return jsonify(resp)


# ---------------------------------------------------------------------------
#  Login (email + password)
# ---------------------------------------------------------------------------

@bp.post("/api/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if is_desktop_client():
        payload, status = _cloud_proxy("POST", "/api/auth/login", data)
        # The cloud response carries a desktop_token for proxy callers; persist
        # it locally and strip it from the browser-facing response.
        if status == 200 and payload.get("desktop_token"):
            user = payload.get("user") or {}
            save_desktop_token(user.get("id", ""), user.get("email", ""), payload["desktop_token"])
            payload = {k: v for k, v in payload.items() if k != "desktop_token"}
        return jsonify(payload), status

    if not email or not password:
        return jsonify({"error": "请输入邮箱和密码"}), 400

    user = verify_user(email, password)
    if not user:
        _audit_auth("login", False, email=email, detail="invalid_credentials")
        return jsonify({"error": "邮箱或密码错误"}), 401

    session["uid"] = user["id"]
    session["auth_version"] = int(user.get("auth_version") or 1)
    _audit_auth("login", True, user_id=user["id"], email=user["email"])
    resp = {"ok": True, "user": {"id": user["id"], "email": user["email"]}}
    if _is_desktop_proxy_request():
        resp["desktop_token"] = _mint_desktop_token(user)
    return jsonify(resp)


# ---------------------------------------------------------------------------
#  Password reset (email verification code + new password)
# ---------------------------------------------------------------------------

@bp.post("/api/auth/password-reset/send-code")
def send_password_reset_code():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    if is_desktop_client():
        payload, status = _cloud_proxy(
            "POST", "/api/auth/password-reset/send-code", data
        )
        return jsonify(payload), status

    if not email or "@" not in email:
        return jsonify({"error": "请输入有效邮箱"}), 400
    if not _smtp_configured():
        return jsonify({"error": "邮件服务未配置，请联系管理员"}), 503

    # Use the same outward response for unknown addresses so this endpoint
    # cannot be used to enumerate registered accounts.
    user = get_user_by_email(email)
    if not user:
        return jsonify({
            "ok": True,
            "message": "如果该邮箱已注册，验证码将发送至邮箱",
        })

    if not can_resend(email, purpose="password_reset"):
        return jsonify({"error": "发送太频繁，请 60 秒后再试"}), 429

    code = generate_code()
    store_email_code(email, code, purpose="password_reset")
    if not _send_email_code(email, code, purpose="password_reset"):
        return jsonify({"error": "验证码发送失败，请稍后重试"}), 500

    _audit_auth(
        "password_reset_code", True, user_id=user["id"], email=user["email"]
    )
    return jsonify({
        "ok": True,
        "message": "如果该邮箱已注册，验证码将发送至邮箱",
    })


@bp.post("/api/auth/password-reset")
def password_reset():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    code = (data.get("code") or "").strip()
    password = data.get("password") or ""

    if is_desktop_client():
        payload, status = _cloud_proxy("POST", "/api/auth/password-reset", data)
        if status == 200:
            clear_desktop_token()
        return jsonify(payload), status

    if not email or "@" not in email:
        return jsonify({"error": "请输入有效邮箱"}), 400
    if not code or len(code) != 6 or not code.isdigit():
        return jsonify({"error": "请输入 6 位验证码"}), 400
    if len(password) < 8:
        return jsonify({"error": "新密码至少 8 个字符"}), 400

    user = get_user_by_email(email)
    if not user or not consume_email_code(
        email, code, purpose="password_reset"
    ):
        _audit_auth(
            "password_reset", False, email=email, detail="invalid_or_expired_code"
        )
        return jsonify({"error": "验证码无效或已过期"}), 400

    if not reset_user_password(user["id"], password):
        _audit_auth(
            "password_reset", False, user_id=user["id"], email=user["email"],
            detail="account_update_failed",
        )
        return jsonify({"error": "密码重置失败，请稍后重试"}), 500

    session.clear()
    _audit_auth(
        "password_reset", True, user_id=user["id"], email=user["email"]
    )
    return jsonify({"ok": True, "message": "密码已重置，请使用新密码登录"})


def _is_desktop_proxy_request() -> bool:
    """True when the request comes from the desktop client's cloud proxy."""
    return (request.headers.get("X-BAA-Desktop-Client") or "").strip() == "1"


def _mint_desktop_token(user: dict) -> str:
    """Issue and persist an opaque desktop token for the given user (cloud side)."""
    token = secrets.token_urlsafe(32)
    save_desktop_token(user["id"], user.get("email", ""), token)
    return token


# ---------------------------------------------------------------------------
#  Logout / me
# ---------------------------------------------------------------------------

@bp.post("/api/auth/logout")
def logout():
    user = current_user()
    if user:
        _audit_auth("logout", True, user_id=user["id"], email=user["email"])
    if is_desktop_client():
        clear_desktop_token()
    session.clear()
    return jsonify({"ok": True})


@bp.get("/api/auth/me")
def me():
    user = current_user()
    if not user:
        return jsonify({"authenticated": False}), 401
    if is_desktop_client():
        # Validate the persisted token against the cloud and pull live quota.
        payload, status = _cloud_proxy("GET", "/api/auth/me-by-token", None)
        if status == 200:
            return jsonify(payload)
        if status == 401:
            clear_desktop_token()
            return jsonify(payload), status
        # Cloud unreachable: return locally-known identity without quota.
        return jsonify({
            "authenticated": True,
            "user": {"id": user["id"], "email": user.get("email", "")},
            "quota": None,
            "offline": True,
        })
    quota = _quota_with_model(user["id"])
    return jsonify({
        "authenticated": True,
        "user": {"id": user["id"], "email": user["email"]},
        "quota": quota,
    })


@bp.get("/api/auth/me-by-token")
def me_by_token():
    """Cloud-only: resolve the desktop token (header) to user + quota.

    Lets the desktop proxy authenticate without a Flask session cookie.
    """
    if not is_cloud_managed():
        return jsonify({"error": "仅云端可解析桌面令牌"}), 403
    user = _resolve_token_user()
    if not user:
        return jsonify({"authenticated": False}), 401
    quota = _quota_with_model(user["id"])
    return jsonify({
        "authenticated": True,
        "user": {"id": user["id"], "email": user["email"]},
        "quota": quota,
    })


# ---------------------------------------------------------------------------
#  Referral endpoints
# ---------------------------------------------------------------------------

def _resolve_token_user(token: str | None = None) -> dict | None:
    """Resolve a desktop token supplied directly or through its proxy header.

    Used by cloud endpoints that the desktop proxy calls without a session
    cookie. Returns the user dict or None.
    """
    token = (token or request.headers.get("X-BAA-Desktop-Token") or "").strip()
    if not token:
        return None
    from data.auth_store import _get_conn
    conn = None
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT user_id, email FROM desktop_tokens "
            "WHERE token = ? AND expires_at IS NOT NULL "
            "AND expires_at > datetime('now') LIMIT 1",
            (token,),
        ).fetchone()
    except Exception:
        row = None
    finally:
        if conn:
            conn.close()
    if not row:
        return None
    return {"id": row["user_id"], "email": row["email"]}


# ---------------------------------------------------------------------------
#  Sponsor prompt + public payment details
# ---------------------------------------------------------------------------

@bp.get("/api/auth/sponsor")
def sponsor_info():
    user = current_user()
    if not user:
        user = _resolve_token_user()
    if not user:
        return jsonify({"error": "未登录"}), 401
    if is_desktop_client():
        payload, status = _cloud_proxy("GET", "/api/auth/sponsor", None)
        return jsonify(payload), status

    evm_address, xko_address = _sponsor_addresses()
    prompt = get_sponsor_prompt_status(user["id"])
    qr_data_url = _qr_data_url(evm_address)
    return jsonify({
        "prompt": prompt,
        "methods": {
            "wechat": {
                "type": "fiat",
                "image_url": url_for(
                    "static", filename="Images/sponsor/wechat.png", v="square-20260830"
                ),
            },
            "alipay": {
                "type": "fiat",
                "image_url": url_for(
                    "static", filename="Images/sponsor/alipay.jpg", v="square-20260830"
                ),
            },
            "usdt": {
                "type": "crypto",
                "symbol": "USDT",
                "network": "X Layer",
                "address": evm_address,
                "xko_address": xko_address,
                "contract": _sponsor_contract("usdt"),
                "qr_data_url": qr_data_url,
            },
            "usdc": {
                "type": "crypto",
                "symbol": "USDC",
                "network": "X Layer",
                "address": evm_address,
                "xko_address": xko_address,
                "contract": _sponsor_contract("usdc"),
                "qr_data_url": qr_data_url,
            },
        },
    })


@bp.post("/api/auth/sponsor/shown")
def sponsor_prompt_shown():
    user = current_user()
    if not user:
        user = _resolve_token_user()
    if not user:
        return jsonify({"error": "未登录"}), 401
    if is_desktop_client():
        payload, status = _cloud_proxy("POST", "/api/auth/sponsor/shown", {})
        return jsonify(payload), status
    return jsonify(mark_sponsor_prompt_shown(user["id"]))


@bp.post("/api/auth/referral/code")
def referral_code():
    user = current_user()
    if not user:
        user = _resolve_token_user()
    if not user:
        return jsonify({"error": "未登录"}), 401
    if is_desktop_client():
        payload, status = _cloud_proxy("POST", "/api/auth/referral/code", {})
        return jsonify(payload), status
    code = get_or_create_referral_code(user["id"])
    base = (os.environ.get("BAA_WEB_BASE_URL") or "").rstrip("/")
    if not base:
        forwarded_proto = request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
        scheme = forwarded_proto if forwarded_proto in {"http", "https"} else request.scheme
        base = f"{scheme}://{request.host}"
    link = f"{base}/login?ref={code}"

    qr_data_url = _qr_data_url(link)

    return jsonify({
        "referral_code": code,
        "link": link,
        "qr_data_url": qr_data_url,
    })


@bp.get("/api/auth/referral/stats")
def referral_stats():
    user = current_user()
    if not user:
        user = _resolve_token_user()
    if not user:
        return jsonify({"error": "未登录"}), 401
    if is_desktop_client():
        payload, status = _cloud_proxy("GET", "/api/auth/referral/stats", None)
        return jsonify(payload), status
    return jsonify(get_referral_stats(user["id"]))
