"""Blueprint: LLM model management — /api/models/*"""
import json
import logging
from queue import Empty, Full, Queue
from threading import Event, Thread

from flask import Blueprint, Response, request, jsonify, stream_with_context

from agent.model_call_audit import audit_model_call
from .state import (
    config_manager,
    session_manager,
    require_local_runtime_config,
    require_session_ownership,
)

log = logging.getLogger(__name__)

bp = Blueprint("models", __name__)

_HOSTED_CATALOG_PUBLIC_FIELDS = (
    "id",
    "display_name",
    "public_model_name",
    "tier",
    "description",
    "quota_source",
    "quota_multiplier",
    "input_multiplier",
    "output_multiplier",
    "cache_read_multiplier",
    "cache_write_multiplier",
    "requires_paid_balance",
    "available",
)


@bp.get("/api/models")
def list_models():
    from infrastructure.paths import is_cloud_managed_runtime

    if is_cloud_managed_runtime():
        # Managed deployment credentials are platform secrets, not personal
        # models. Cloud clients receive hosted entries only via /catalog.
        return jsonify({})
    return jsonify(config_manager.list_configs())


@bp.get("/api/models/catalog")
def hosted_model_catalog():
    """Return display-only metadata for platform-managed models."""
    from .auth import (
        _cloud_auth_base,
        _cloud_proxy,
        _resolve_token_user,
        current_user,
        is_desktop_client,
    )

    if is_desktop_client():
        payload, status = _cloud_proxy("GET", "/api/models/catalog", None)
        if status == 200 and isinstance(payload, dict):
            from data.auth_store import get_desktop_token

            stored = get_desktop_token() or {}
            config_manager.sync_remote_hosted_models(
                payload.get("models") or [],
                base_url=f"{_cloud_auth_base()}/api/desktop/llm",
                api_key=str(stored.get("token") or ""),
            )
        response = jsonify(payload)
        response.status_code = status
        response.headers["Cache-Control"] = "private, no-store"
        return response

    models = [
        {key: item.get(key) for key in _HOSTED_CATALOG_PUBLIC_FIELDS}
        for item in config_manager.list_hosted_models()
    ]
    paid_balance = 0
    try:
        from data.auth_store import get_bonus_balance

        user = current_user() or _resolve_token_user()
        if user:
            paid_balance = get_bonus_balance(user["id"])
    except Exception:
        log.exception("[models] failed to resolve hosted model access")

    for item in models:
        requires_paid = bool(item.get("requires_paid_balance"))
        item["selectable"] = not requires_paid or paid_balance > 0
    default_model_id = next(
        (
            item["id"] for item in models
            if item.get("available") and item.get("selectable")
        ),
        None,
    )
    response = jsonify({
        "models": models,
        "default_model_id": default_model_id,
    })
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie, Authorization"
    return response


def _desktop_gateway_error(message: str, code: str, status: int) -> Response:
    response = jsonify({
        "error": {
            "message": message,
            "type": "authentication_error" if status == 401 else "api_error",
            "param": None,
            "code": code,
        },
    })
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _desktop_gateway_user() -> dict | None:
    authorization = (request.headers.get("Authorization") or "").strip()
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        return None
    from .auth import _resolve_token_user

    return _resolve_token_user(token.strip())


_STREAM_END = object()


def _usage_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _normalize_gateway_usage(usage: dict | None) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    prompt_details = usage.get("prompt_tokens_details") or {}
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    prompt_tokens = _usage_int(
        usage.get("prompt_tokens") or usage.get("input_tokens")
    )
    completion_tokens = _usage_int(
        usage.get("completion_tokens") or usage.get("output_tokens")
    )
    cached_input_tokens = _usage_int(
        prompt_details.get("cached_tokens")
        or prompt_details.get("cache_read_tokens")
        or usage.get("cached_input_tokens")
        or usage.get("cache_read_input_tokens")
    )
    cache_write_tokens = _usage_int(
        prompt_details.get("cache_write_tokens")
        or prompt_details.get("cache_creation_tokens")
        or usage.get("cache_write_tokens")
        or usage.get("cache_write_input_tokens")
        or usage.get("cache_creation_input_tokens")
    )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_tokens": cache_write_tokens,
    }


def _record_gateway_usage(
    user_id: str, hosted: dict, usage: dict | None, model_call_id: str = "",
) -> bool:
    normalized = _normalize_gateway_usage(usage)
    if not normalized or (
        normalized["prompt_tokens"] + normalized["completion_tokens"] <= 0
    ):
        return False
    from data.auth_store import record_usage_event

    record_usage_event(
        user_id,
        prompt_tokens=normalized["prompt_tokens"],
        completion_tokens=normalized["completion_tokens"],
        cached_input_tokens=normalized["cached_input_tokens"],
        cache_write_tokens=normalized["cache_write_tokens"],
        model=str(hosted.get("public_model_name") or hosted["display_name"]),
        model_id=hosted["id"],
        tier=hosted["tier"],
        quota_source=hosted["quota_source"],
        multiplier=hosted["quota_multiplier"],
        input_multiplier=hosted["input_multiplier"],
        output_multiplier=hosted["output_multiplier"],
        cache_read_multiplier=hosted["cache_read_multiplier"],
        cache_write_multiplier=hosted["cache_write_multiplier"],
        session_id="desktop-llm-gateway",
        model_call_id=str(model_call_id or ""),
    )
    return True


def _settle_gateway_usage(
    user_id: str,
    hosted: dict,
    usage: dict | None,
    model_id: str,
    audit=None,
) -> None:
    normalized = _normalize_gateway_usage(usage)
    if audit is not None:
        if normalized and normalized["prompt_tokens"] + normalized["completion_tokens"] > 0:
            audit.succeed(usage)
        else:
            audit.missing_usage()
    try:
        recorded = _record_gateway_usage(
            user_id,
            hosted,
            usage,
            model_call_id=str(getattr(audit, "request_id", "") or ""),
        )
        if not recorded:
            log.warning("[desktop-llm] upstream usage missing model=%s", model_id)
    except Exception:
        log.exception("[desktop-llm] failed to record usage model=%s", model_id)


def _desktop_stream_relay(
    upstream,
    client,
    *,
    user_id: str,
    hosted: dict,
    model_id: str,
    audit=None,
):
    output: Queue = Queue(maxsize=128)
    downstream_closed = Event()

    def enqueue(item: object) -> bool:
        while not downstream_closed.is_set():
            try:
                output.put(item, timeout=0.25)
                return True
            except Full:
                continue
        return False

    def consume_upstream() -> None:
        final_usage = None
        try:
            for line in upstream.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    enqueue("data: [DONE]\n\n")
                    continue
                try:
                    chunk = json.loads(data)
                except (TypeError, ValueError):
                    continue
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("error"):
                    chunk = {
                        "error": {
                            "message": "模型服务暂时不可用。",
                            "type": "api_error",
                            "code": "upstream_error",
                        },
                    }
                chunk["model"] = model_id
                if isinstance(chunk.get("usage"), dict):
                    final_usage = chunk["usage"]
                enqueue(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n")
        except Exception:
            if audit is not None:
                audit.fail("upstream_stream_error")
            log.exception("[desktop-llm] upstream stream failed model=%s", model_id)
            enqueue(
                "data: "
                + json.dumps({
                    "error": {
                        "message": "模型服务暂时不可用。",
                        "type": "api_error",
                        "code": "upstream_error",
                    },
                }, ensure_ascii=False)
                + "\n\n"
            )
        finally:
            upstream.close()
            client.close()
            _settle_gateway_usage(user_id, hosted, final_usage, model_id, audit=audit)
            enqueue(_STREAM_END)

    producer = Thread(
        target=consume_upstream,
        name=f"desktop-llm-{model_id[:32]}",
        daemon=True,
    )
    producer.start()

    try:
        while True:
            try:
                item = output.get(timeout=1.0)
            except Empty:
                if not producer.is_alive():
                    break
                continue
            if item is _STREAM_END:
                break
            yield item
    finally:
        downstream_closed.set()


@bp.post("/api/desktop/llm/chat/completions")
def desktop_llm_gateway():
    """Authenticated OpenAI-compatible LLM gateway for the desktop Agent.

    Agent orchestration and tools remain local.  The gateway resolves the
    stable catalog ID to Railway-only upstream configuration, streams the
    provider response back, and charges the account on the cloud database.
    """
    from infrastructure.paths import is_cloud_managed_runtime

    if not is_cloud_managed_runtime():
        return _desktop_gateway_error("仅云端提供模型网关。", "cloud_only", 403)
    user = _desktop_gateway_user()
    if not user:
        return _desktop_gateway_error("桌面端登录已失效，请重新登录。", "invalid_token", 401)

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _desktop_gateway_error("请求正文必须是 JSON 对象。", "invalid_request", 400)
    model_id = str(body.get("model") or "").strip()
    hosted = config_manager.get_hosted_model(model_id)
    config = config_manager.get_config(model_id)
    if not hosted or not config or not hosted.get("available"):
        return _desktop_gateway_error("未知或不可用的模型。", "model_not_found", 404)

    from data.auth_store import check_quota, get_bonus_balance

    if hosted.get("requires_paid_balance"):
        if get_bonus_balance(user["id"]) <= 0:
            return _desktop_gateway_error("付费额度不足。", "insufficient_quota", 402)
    elif check_quota(user["id"])["exceeded"]:
        return _desktop_gateway_error("今日赠送额度已用尽。", "quota_exceeded", 429)

    upstream_body = dict(body)
    upstream_body["model"] = config.model
    upstream_body.pop("user", None)
    is_stream = bool(upstream_body.get("stream"))
    if is_stream:
        stream_options = upstream_body.get("stream_options")
        if not isinstance(stream_options, dict):
            stream_options = {}
        upstream_body["stream_options"] = {**stream_options, "include_usage": True}

    client = None
    audit = audit_model_call(
        user_id=user["id"], session_id="desktop-llm-gateway", source="desktop",
        purpose="chat", billing_scope="user", provider=model_id,
        model=str(hosted.get("public_model_name") or hosted["display_name"]),
    )
    try:
        import httpx

        client = httpx.Client(
            timeout=httpx.Timeout(600.0, connect=15.0),
            follow_redirects=False,
        )
        upstream_request = client.build_request(
            "POST",
            f"{str(config.base_url).rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Accept": "text/event-stream" if is_stream else "application/json",
                "Content-Type": "application/json",
            },
            json=upstream_body,
        )
        audit.attempt()
        upstream = client.send(upstream_request, stream=is_stream)
    except Exception:
        audit.fail("upstream_connection_failed")
        if client is not None:
            client.close()
        log.exception("[desktop-llm] upstream connection failed model=%s", model_id)
        return _desktop_gateway_error("模型服务暂时不可用。", "upstream_unavailable", 502)

    if upstream.status_code >= 400:
        audit.fail(f"upstream_http_{upstream.status_code}")
        log.warning(
            "[desktop-llm] upstream rejected request model=%s status=%s",
            model_id,
            upstream.status_code,
        )
        upstream.close()
        client.close()
        return _desktop_gateway_error("模型服务暂时不可用。", "upstream_error", 502)

    if not is_stream:
        try:
            payload = upstream.json()
        except Exception:
            audit.fail("invalid_upstream_response")
            upstream.close()
            client.close()
            return _desktop_gateway_error("模型响应格式无效。", "invalid_upstream_response", 502)
        upstream.close()
        client.close()
        if isinstance(payload, dict):
            payload["model"] = model_id
            _settle_gateway_usage(
                user["id"], hosted, payload.get("usage"), model_id, audit=audit,
            )
        response = jsonify(payload)
        response.headers["Cache-Control"] = "no-store"
        return response

    response = Response(
        stream_with_context(_desktop_stream_relay(
            upstream,
            client,
            user_id=user["id"],
            hosted=hosted,
            model_id=model_id,
            audit=audit,
        )),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache, no-store"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@bp.get("/api/models/defaults")
def model_defaults():
    from infrastructure.paths import is_cloud_managed_runtime

    if is_cloud_managed_runtime():
        # Provider endpoints and real model IDs are local configuration aids.
        # A managed browser never needs them and must not receive them.
        return jsonify({})
    return jsonify({
        k: {
            "base_url": v["base_url"],
            "model": v["model"],
            "context_window": v.get("context_window"),
            "max_output_tokens": v.get("max_output_tokens"),
        }
        for k, v in config_manager.DEFAULT_CONFIGS.items()
    })


@bp.post("/api/models/set-builtin")
@require_local_runtime_config
def set_builtin():
    d = request.json or {}
    provider = d.get("provider", "").strip()
    api_key  = d.get("api_key", "").strip()
    base_url = d.get("base_url", "").strip() or None
    model    = d.get("model", "").strip() or None
    context_window    = _to_int(d.get("context_window"))
    max_output_tokens = _to_int(d.get("max_output_tokens"))
    enable_thinking   = bool(d.get("enable_thinking", False))
    thinking_budget   = _to_int(d.get("thinking_budget")) or 8000
    try:
        input_price, output_price, _ = _price_payload(d)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not provider:
        return jsonify({"error": "provider 不能为空"}), 400
    try:
        ok = config_manager.set_config(
            provider, api_key, base_url=base_url, model=model,
            context_window=context_window, max_output_tokens=max_output_tokens,
            enable_thinking=enable_thinking, thinking_budget=thinking_budget,
            input_price_per_million=input_price,
            output_price_per_million=output_price,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if ok:
        return jsonify({"ok": True})
    return jsonify({"error": f"不支持的内置提供商: {provider}"}), 400


@bp.post("/api/models/clear-builtin")
@require_local_runtime_config
def clear_builtin():
    d = request.json or {}
    ok, msg = config_manager.clear_builtin_config(d.get("provider", "").strip())
    return jsonify({"ok": ok, "message": msg})


@bp.post("/api/models/add")
@require_local_runtime_config
def add_model():
    d = request.json or {}
    try:
        input_price, output_price, _ = _price_payload(d)
        ok, msg = config_manager.add_custom_model(
            name=d.get("name", ""),
            base_url=d.get("base_url", ""),
            model_name=d.get("model_name", ""),
            api_key=d.get("api_key", ""),
            context_window=_to_int(d.get("context_window")),
            max_output_tokens=_to_int(d.get("max_output_tokens")),
            enable_thinking=bool(d.get("enable_thinking", False)),
            thinking_budget=_to_int(d.get("thinking_budget")) or 8000,
            input_price_per_million=input_price,
            output_price_per_million=output_price,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if ok:
        return jsonify({"ok": True, "message": msg})
    return jsonify({"error": msg}), 400


def _to_int(v) -> int | None:
    try:
        return int(v) if v not in (None, "", "0") else None
    except (TypeError, ValueError):
        return None


def _price_payload(data: dict) -> tuple[float | None, float | None, bool]:
    """Parse the optional input/output USD-per-million price pair."""
    keys = ("input_price_per_million", "output_price_per_million")
    configured = any(key in data for key in keys)
    values: list[float | None] = []
    for key in keys:
        value = data.get(key)
        if value in (None, ""):
            values.append(None)
            continue
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("模型价格必须是数字") from exc
        if value < 0 or value == float("inf") or value != value:
            raise ValueError("模型价格必须是非负的有限数字")
        values.append(value)
    if configured and (values[0] is None) != (values[1] is None):
        raise ValueError("模型价格必须同时填写输入与输出单价")
    return values[0], values[1], configured


@bp.post("/api/models/update")
@require_local_runtime_config
def update_model():
    d = request.json or {}
    try:
        input_price, output_price, price_configured = _price_payload(d)
        ok, msg = config_manager.update_custom_model(
            provider=d.get("provider", "").strip(),
            base_url=d.get("base_url", ""),
            model_name=d.get("model_name", ""),
            api_key=d.get("api_key", ""),
            context_window=_to_int(d.get("context_window")),
            max_output_tokens=_to_int(d.get("max_output_tokens")),
            enable_thinking=bool(d.get("enable_thinking", False)),
            thinking_budget=_to_int(d.get("thinking_budget")) or 8000,
            input_price_per_million=input_price,
            output_price_per_million=output_price,
            price_configured=price_configured,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if ok:
        return jsonify({"ok": True, "message": msg})
    return jsonify({"error": msg}), 400


@bp.post("/api/models/delete")
@require_local_runtime_config
def delete_model():
    d = request.json or {}
    ok, msg = config_manager.delete_config(d.get("provider", "").strip())
    if ok:
        return jsonify({"ok": True, "message": msg})
    return jsonify({"error": msg}), 400


@bp.post("/api/models/test")
@require_local_runtime_config
def test_model():
    d = request.json or {}
    provider  = d.get("provider", "").strip()
    if config_manager.is_hosted_provider(provider):
        return jsonify({
            "error": "平台内置模型由服务端统一维护",
            "code": "hosted_model_managed",
        }), 403
    # 前端可传入输入框中尚未保存的临时值，优先用于测试
    tmp_key   = (d.get("api_key")  or "").strip() or None
    tmp_url   = (d.get("base_url") or "").strip() or None
    tmp_model = (d.get("model")    or "").strip() or None
    return jsonify(config_manager.test_config(
        provider,
        api_key=tmp_key, base_url=tmp_url, model=tmp_model,
    ))


@bp.post("/api/session/<sid>/model")
@require_session_ownership
def set_session_model(sid: str):
    d = request.json or {}
    provider = (d.get("model_id") or d.get("provider") or "").strip()
    from infrastructure.paths import is_cloud_managed_runtime

    if is_cloud_managed_runtime() and not config_manager.is_hosted_provider(provider):
        return jsonify({
            "error": "云端只能选择平台内置模型",
            "code": "hosted_model_required",
        }), 403
    if not config_manager.get_config(provider):
        return jsonify({"error": f"未知的模型: {provider}"}), 400
    public_model = config_manager.get_hosted_model(provider)
    if public_model and public_model.get("requires_paid_balance"):
        from .auth import current_user, run_mode
        from data.auth_store import get_bonus_balance

        user = current_user()
        if run_mode() == "cloud" and (
            not user or get_bonus_balance(user["id"]) <= 0
        ):
            return jsonify({
                "error": "该模型需要可用额度，请先获取或购买额度。",
                "code": "paid_quota_required",
            }), 402
    sess = session_manager.get_or_create(sid)
    sess.model_provider = provider
    return jsonify({
        "ok": True,
        "model_id": provider,
        "hosted": bool(public_model),
        "model": public_model,
    })
