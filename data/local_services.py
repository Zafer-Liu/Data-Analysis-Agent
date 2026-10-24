"""Named HTTP/Docker endpoints; no discovery, downloads or requests on load."""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from urllib.parse import unquote, urlsplit

from data.forecast_config import validate_url
from infrastructure.paths import runtime_config_path

LOCK = threading.RLock()
DEFAULTS = {"name": "", "transport": "http", "protocol": "http", "base_url": "",
            "health_path": "/health", "request_path": "/predict", "method": "POST",
            "request_body": {}, "timeout": 30, "credential_ref": ""}


def config_path():
    return runtime_config_path("local_services.json", "config/local_services.json")


def validate_path(value):
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 1024:
        raise ValueError("路径须以 / 开头 / Path must start with /")
    decoded = unquote(value)
    parsed = urlsplit(value)
    if (parsed.scheme or parsed.netloc or parsed.fragment or decoded.startswith("//")
            or "\\" in decoded or any(ord(c) < 32 or ord(c) == 127 for c in decoded)
            or any(part in {".", ".."} for part in decoded.split("?")[0].split("/"))):
        raise ValueError("请使用当前服务内的路径 / Use a path within this service")
    return value


def normalize(payload, previous=None):
    if not isinstance(payload, dict):
        raise ValueError("服务配置须为对象 / Service configuration must be an object")
    cfg = {**DEFAULTS, **(previous or {}), **{k: v for k, v in payload.items() if k in DEFAULTS and k != "credential_ref"}}
    for key in ("name", "base_url", "transport", "protocol", "method"):
        if not isinstance(cfg[key], str):
            raise ValueError(f"{key} 格式无效 / Invalid field")
        cfg[key] = cfg[key].strip()
    if not cfg["name"] or len(cfg["name"]) > 80:
        raise ValueError("服务名称需要 1–80 个字符 / Service name needs 1–80 characters")
    if cfg["transport"] not in {"http", "docker"} or cfg["protocol"] not in {"http", "openai", "timesfm"}:
        raise ValueError("接入方式或协议无效 / Invalid transport or protocol")
    cfg["base_url"] = validate_url(cfg["base_url"], "docker" if cfg["transport"] == "docker" else "api")
    if not cfg["base_url"]:
        raise ValueError("请填写服务地址 / Enter the service URL")
    for key in ("health_path", "request_path"):
        cfg[key] = validate_path(cfg[key])
    if cfg["method"] not in {"GET", "POST"}:
        raise ValueError("仅支持 GET / POST / Only GET and POST are supported")
    if isinstance(cfg["timeout"], bool) or not isinstance(cfg["timeout"], int) or not 5 <= cfg["timeout"] <= 180:
        raise ValueError("超时需为 5–180 秒 / Timeout must be 5–180 seconds")
    if not isinstance(cfg["request_body"], dict):
        raise ValueError("请求模板须为 JSON 对象 / Request template must be a JSON object")
    try:
        size = len(json.dumps(cfg["request_body"], allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("请求模板不是有效 JSON / Invalid request JSON") from exc
    if size > 256 * 1024:
        raise ValueError("请求模板过大 / Request template is too large")
    changed = any(cfg.get(k) != (previous or {}).get(k) for k in DEFAULTS if k != "credential_ref")
    if changed:
        cfg.pop("last_check", None)
    if previous and cfg["base_url"] != previous["base_url"]:
        cfg["credential_ref"] = ""  # Never reuse a token at another endpoint.
    return cfg


def _read():
    path = config_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or any(not isinstance(v, dict) for v in value.values()):
            raise ValueError()
        return value
    except (ValueError, OSError) as exc:
        raise ValueError("本地服务配置无法读取，请检查配置文件 / Cannot read local service settings") from exc


def _write(value):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temp, path)


def get(identifier):
    with LOCK:
        return _read().get(identifier)


def list_services():
    with LOCK:
        return [public(v) for v in _read().values()]


def public(cfg):
    value = dict(cfg)
    value["token_configured"] = bool(value.pop("credential_ref", ""))
    return value


def save(cfg, identifier=None):
    with LOCK:
        items = _read()
        if identifier and identifier not in items:
            raise KeyError(identifier)
        if len(items) >= 40 and not identifier:
            raise ValueError("最多保存 40 个服务 / Up to 40 services can be saved")
        identifier = identifier or uuid.uuid4().hex
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("服务标识无效 / Invalid service ID")
        items[identifier] = {**cfg, "id": identifier}
        _write(items)
        return items[identifier]


def remove(identifier):
    with LOCK:
        items = _read()
        previous = items.pop(identifier, None)
        if previous is None:
            raise KeyError(identifier)
        _write(items)
        return previous


def record_check(identifier, cfg, check):
    with LOCK:
        items = _read()
        current = items.get(identifier)
        if current and all(current.get(k) == cfg.get(k) for k in DEFAULTS):
            items[identifier] = {**current, "last_check": check}
            _write(items)
