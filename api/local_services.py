"""Local service center: multiple named connections and protocol-specific actions."""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, abort, jsonify, request

from data import local_services as store
from data.forecast_config import load_config, save_config, public_config
from infrastructure import forecast_runtime
from infrastructure import local_service_control as control
from infrastructure.credential_store import delete_secret, store_secret
from infrastructure.forecast_client import ForecastError
from infrastructure.local_service_client import (
    ServiceError, discover_models, openai_base, probe, request_service, token_for,
)

bp = Blueprint("local_services", __name__)


@bp.before_request
def _local_only():
    from .auth import run_mode
    if run_mode() == "cloud":
        abort(404)


@bp.errorhandler(ValueError)
@bp.errorhandler(RuntimeError)
@bp.errorhandler(ServiceError)
@bp.errorhandler(ForecastError)
def _error(exc):
    return jsonify({"ok": False, "message": str(exc)}), 400


@bp.errorhandler(KeyError)
def _not_found(_exc):
    return jsonify({"ok": False, "message": "服务不存在，请刷新列表 / Service not found; refresh the list"}), 404


def _body():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("请发送 JSON 对象 / Send a JSON object")
    return value


def _get(identifier):
    cfg = store.get(identifier)
    if cfg is None:
        raise KeyError(identifier)
    return cfg


def _token(payload):
    token = payload.get("token")
    if token is not None and (not isinstance(token, str) or len(token) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in token)):
        raise ValueError("认证密钥无效 / Invalid authentication token")
    return token


def _discard(reference):
    if reference:
        try:
            delete_secret(reference)
        except RuntimeError:
            pass  # The reference is no longer used by the saved configuration.


def _assert_idle():
    if forecast_runtime.busy():
        raise ValueError("请等待本地模型操作结束 / Wait for the local model operation to finish")


def _deactivate(identifier):
    cfg = load_config()
    if cfg.get("service_id") == identifier:
        _assert_idle()
        previous_ref = cfg.get("credential_ref")
        save_config({"enabled": False, "service_id": "", "credential_ref": "", "base_url": "", "auto_start": False})
        _discard(previous_ref)


@bp.get("/api/local-services")
def list_services():
    cfg = public_config()
    services = [{**service, "control": control.capability(service)} for service in store.list_services()]
    forecast = {k: cfg[k] for k in ("enabled", "base_url", "mode", "service_id")}
    runtime = forecast_runtime.status()
    forecast["can_stop"] = bool(runtime["managed_running"] and runtime.get("instance", {}).get("base_url") == cfg["base_url"])
    return jsonify({"ok": True, "services": services, "forecast": forecast})


@bp.post("/api/local-services")
@bp.put("/api/local-services/<identifier>")
def save_service(identifier=None):
    payload = _body()
    old = _get(identifier) if identifier else None
    cfg = store.normalize(payload, old)
    token = _token(payload)
    if payload.get("clear_token") is True:
        cfg["credential_ref"] = ""
    new_ref = None
    if token:
        new_ref = store_secret(token, label="local-service")
        cfg["credential_ref"] = new_ref
    if token or payload.get("clear_token") is True:
        cfg.pop("last_check", None)
    try:
        if old and any(cfg.get(k) != old.get(k) for k in ("base_url", "protocol", "transport", "credential_ref", "timeout")):
            _deactivate(identifier)
        saved = store.save(cfg, identifier)
    except Exception:
        _discard(new_ref)
        raise
    if old and old.get("credential_ref") != saved.get("credential_ref"):
        _discard(old.get("credential_ref"))
    return jsonify({"ok": True, "service": store.public(saved)})


@bp.delete("/api/local-services/<identifier>")
def remove_service(identifier):
    _get(identifier)
    _deactivate(identifier)
    previous = store.remove(identifier)
    control.forget(identifier)
    _discard(previous.get("credential_ref"))
    return jsonify({"ok": True})


@bp.post("/api/local-services/<identifier>/test")
def test_service(identifier):
    cfg = _get(identifier)
    check = {"checked_at": datetime.now(timezone.utc).isoformat()}
    try:
        result = probe(cfg)
        control.mark_started(cfg)
        check.update(status="online", elapsed_ms=result["elapsed_ms"])
    except (ValueError, RuntimeError) as exc:
        store.record_check(identifier, cfg, {**check, "status": "error"})
        raise exc
    store.record_check(identifier, cfg, check)
    return jsonify({"ok": True, **result})


@bp.post("/api/local-services/<identifier>/stop")
def stop_model(identifier):
    cfg = _get(identifier)
    if _body().get("confirmed") is not True:
        raise ValueError("请确认停止模型 / Confirm stopping the model")
    _assert_idle()
    control.stop(cfg)
    _deactivate(identifier)
    store.record_check(identifier, cfg, {"status": "stopped", "checked_at": datetime.now(timezone.utc).isoformat()})
    return jsonify({"ok": True, "stopped": True})


@bp.post("/api/local-services/forecast/stop")
def stop_owned_forecast():
    if _body().get("confirmed") is not True:
        raise ValueError("请确认停止模型 / Confirm stopping the model")
    cfg = load_config()
    runtime = forecast_runtime.status()
    if not runtime["managed_running"] or runtime.get("instance", {}).get("base_url") != cfg["base_url"]:
        raise ValueError("没有可停止的应用托管模型 / No app-managed model is running")
    control.stop_owned()
    return jsonify({"ok": True, "stopped": True})


@bp.post("/api/local-services/<identifier>/invoke")
def invoke_service(identifier):
    cfg = _get(identifier)
    if cfg["protocol"] != "http":
        raise ValueError("此入口仅用于自定义 HTTP 请求 / This action is for custom HTTP requests")
    payload = _body()
    # Host, method and path come only from the saved user configuration.
    result = request_service(cfg, cfg["request_path"], method=cfg["method"], body=payload.get("body", cfg["request_body"]))
    return jsonify({"ok": True, **result})


@bp.post("/api/local-services/<identifier>/models/register")
def register_model(identifier):
    cfg = _get(identifier)
    if cfg["protocol"] != "openai":
        raise ValueError("请先选择 OpenAI 兼容协议 / Select the OpenAI-compatible protocol")
    model = _body().get("model")
    if not isinstance(model, str) or not model or model not in discover_models(cfg)["models"]:
        raise ValueError("请选择该服务实际提供的模型 / Select a model returned by this service")
    from LLM.llm_config_manager import get_config_manager
    ok, message = get_config_manager().add_custom_model(
        f"{cfg['name']} · {model}", openai_base(cfg), model, token_for(cfg), allow_anonymous=True)
    return jsonify({"ok": ok, "message": message}), (200 if ok else 409)


@bp.post("/api/local-services/<identifier>/activate")
def activate_forecast(identifier):
    cfg = _get(identifier)
    if cfg["protocol"] != "timesfm":
        raise ValueError("只有 TimesFM 适配器可用于时序预测 / Only the TimesFM adapter supports forecasting")
    _assert_idle()
    probe(cfg)
    previous = load_config()
    # Use a separate vault reference so editing either configuration is independent.
    secret = token_for(cfg)
    reference = store_secret(secret, label="forecast-api") if secret else ""
    try:
        saved = save_config({"enabled": True, "base_url": cfg["base_url"], "timeout": cfg["timeout"],
            "mode": "docker" if cfg["transport"] == "docker" else "api", "auto_start": False,
            "credential_ref": reference, "service_id": identifier})
    except Exception:
        _discard(reference)
        raise
    _discard(previous.get("credential_ref"))
    return jsonify({"ok": True, "config": public_config(saved)})
