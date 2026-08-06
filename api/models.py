"""Blueprint: LLM model management — /api/models/*"""
import logging

from flask import Blueprint, request, jsonify
from .state import config_manager, session_manager, require_session_ownership

log = logging.getLogger(__name__)

bp = Blueprint("models", __name__)


@bp.get("/api/models")
def list_models():
    return jsonify(config_manager.list_configs())


@bp.get("/api/models/defaults")
def model_defaults():
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
    if not provider or not api_key:
        return jsonify({"error": "provider 和 api_key 不能为空"}), 400
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
def clear_builtin():
    d = request.json or {}
    ok, msg = config_manager.clear_builtin_config(d.get("provider", "").strip())
    return jsonify({"ok": ok, "message": msg})


@bp.post("/api/models/add")
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
def delete_model():
    d = request.json or {}
    ok, msg = config_manager.delete_config(d.get("provider", "").strip())
    if ok:
        return jsonify({"ok": True, "message": msg})
    return jsonify({"error": msg}), 400


@bp.post("/api/models/test")
def test_model():
    d = request.json or {}
    provider  = d.get("provider", "").strip()
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
    provider = d.get("provider", "").strip()
    if not config_manager.get_config(provider):
        return jsonify({"error": f"未知的模型: {provider}"}), 400
    sess = session_manager.get_or_create(sid)
    sess.model_provider = provider
    return jsonify({"ok": True})
