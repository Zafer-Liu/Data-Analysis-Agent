"""Opt-in local forecast settings and explicit lifecycle operations."""
from __future__ import annotations

from flask import Blueprint, abort, jsonify, request

from data.forecast_config import DEFAULTS, load_config, normalize_config, public_config, save_config
from infrastructure import forecast_runtime
from infrastructure.forecast_client import ForecastClient, ForecastError
from infrastructure.model_assets import MODEL, REVISION, WEIGHT_SIZE

bp = Blueprint("forecast", __name__)


@bp.before_request
def _local_only():
    from .auth import run_mode
    if run_mode() == "cloud":
        abort(404)  # Localhost here belongs to the cloud host, not the visitor.


@bp.errorhandler(ValueError)
@bp.errorhandler(ForecastError)
@bp.errorhandler(RuntimeError)
def _error(exc):
    return jsonify({"ok": False, "message": str(exc)}), 400


def _body():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("请发送 JSON 对象 / Send a JSON object")
    return value


def _candidate(payload):
    old = load_config()
    fields = {k: v for k, v in payload.items() if k in DEFAULTS and k not in {"credential_ref", "model"}}
    cfg = normalize_config(fields, old)
    if cfg["base_url"] != old["base_url"]:
        cfg["credential_ref"] = ""  # Never send a saved token to a different endpoint.
        cfg["service_id"] = ""
    return old, cfg


@bp.get("/api/forecast/config")
def config():
    return jsonify({"ok": True, "config": public_config(), "runtime": forecast_runtime.status(),
                    "model": {"id": MODEL, "revision": REVISION, "weight_bytes": WEIGHT_SIZE,
                              "license_url": "https://huggingface.co/google/timesfm-3.0-pytorch/blob/main/LICENSE"}})


@bp.put("/api/forecast/config")
def update_config():
    if forecast_runtime.busy():
        return jsonify({"ok": False, "message": "模型操作正在运行，请完成后再保存 / Wait for the model operation to finish"}), 409
    payload = _body()
    old, cfg = _candidate(payload)
    token = payload.get("token")
    if token is not None and (not isinstance(token, str) or len(token) > 4096 or "\n" in token or "\r" in token):
        raise ValueError("认证密钥无效 / Invalid authentication token")
    clear = payload.get("clear_token") is True
    if clear:
        cfg["credential_ref"] = ""
    if cfg["enabled"]:
        ForecastClient(cfg, require_enabled=False, token=token if token else ("" if clear else None)).health()
    new_reference = None
    if token:
        from infrastructure.credential_store import store_secret
        new_reference = store_secret(token, label="forecast-api")
        cfg["credential_ref"] = new_reference
    try:
        saved = save_config(cfg)
    except Exception:
        if new_reference:
            from infrastructure.credential_store import delete_secret
            delete_secret(new_reference)
        raise
    if old.get("credential_ref") and old["credential_ref"] != saved.get("credential_ref"):
        from infrastructure.credential_store import delete_secret
        try:
            delete_secret(old["credential_ref"])
        except RuntimeError:
            pass  # The obsolete reference is no longer used by configuration.
    return jsonify({"ok": True, "config": public_config(saved)})


@bp.post("/api/forecast/test")
def test_connection():
    payload = _body()
    _, cfg = _candidate(payload)
    token = payload.get("token")
    if token is not None and (not isinstance(token, str) or "\n" in token or "\r" in token or len(token) > 4096):
        raise ValueError("认证密钥无效 / Invalid authentication token")
    client = ForecastClient(cfg, require_enabled=False, token=token or None)
    health = client.health()
    result = {"ok": True, "health": health}
    if payload.get("inference") is True:
        value = client.smoke_test()
        result["inference"] = {"horizon": value["horizon"], "device": value.get("device"),
                               "elapsed_seconds": value.get("elapsed_seconds")}
    return jsonify(result)


@bp.get("/api/forecast/runtime")
def runtime_status():
    return jsonify({"ok": True, "runtime": forecast_runtime.status(), "config": public_config()})


@bp.post("/api/forecast/runtime")
def runtime_action():
    payload = _body()
    action = payload.get("action")
    if action == "download" and (payload.get("confirm_download") is not True or payload.get("license_accepted") is not True):
        raise ValueError("下载需要明确操作并确认模型研究许可 / Explicit download and research-license acceptance required")
    return jsonify({"ok": True, "runtime": forecast_runtime.submit(action)}), 202
