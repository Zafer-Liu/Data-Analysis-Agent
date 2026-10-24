"""Optional, local forecast connection. Importing never installs or starts a model."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

from infrastructure.paths import runtime_config_path

LOCK = threading.RLock()
DEFAULTS = {
    "enabled": False, "mode": "api", "base_url": "", "timeout": 180,
    "runtime_dir": "", "model_dir": "", "python_executable": "",
    "device": "auto", "port": 8787, "auto_start": False,
    "credential_ref": "", "model": "google/timesfm-3.0-pytorch", "service_id": "",
}


def config_path() -> Path:
    return runtime_config_path("forecast.json", "config/forecast.json")


def load_config() -> dict:
    with LOCK:
        path = config_path()
        if not path.exists():
            return dict(DEFAULTS)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise ValueError("预测服务配置无法读取 / Cannot read forecast configuration") from exc
        if not isinstance(value, dict):
            raise ValueError("预测服务配置格式错误 / Invalid forecast configuration")
        return {**DEFAULTS, **{k: v for k, v in value.items() if k in DEFAULTS}}


def validate_url(value: str, mode: str = "api") -> str:
    value = str(value or "").strip().rstrip("/")
    if not value:
        return ""
    try:
        p = urlsplit(value)
        port = p.port
    except ValueError as exc:
        raise ValueError("服务地址或端口无效 / Invalid service URL or port") from exc
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError("请填写不含密钥和查询参数的 HTTP(S) 地址 / Use an HTTP(S) URL without credentials or query")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("端口应为 1–65535 / Port must be 1–65535")
    local = p.hostname in {"localhost", "127.0.0.1", "::1"}
    docker_host = mode == "docker" and (
        p.hostname == "host.docker.internal" or ("." not in p.hostname and ":" not in p.hostname)
    )
    if p.scheme != "https" and not (local or docker_host):
        raise ValueError("HTTP 仅限本机或 Docker 服务名，远程地址须 HTTPS / Remote connections require HTTPS")
    return value


def normalize_config(payload: dict, previous: dict | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("配置必须为对象 / Configuration must be an object")
    cfg = {**DEFAULTS, **(previous or {}), **{k: v for k, v in payload.items() if k in DEFAULTS}}
    if not isinstance(cfg["mode"], str) or cfg["mode"] not in {"api", "docker", "managed"}:
        raise ValueError("接入方式无效 / Invalid connection mode")
    for key in ("enabled", "auto_start"):
        if not isinstance(cfg[key], bool):
            raise ValueError(f"{key} 必须为布尔值 / must be boolean")
    for key, low, high in (("timeout", 5, 600), ("port", 1024, 65535)):
        if isinstance(cfg[key], bool) or not isinstance(cfg[key], int) or not low <= cfg[key] <= high:
            raise ValueError(f"{key} 必须在 {low}–{high} 之间 / must be within range")
    if not isinstance(cfg["device"], str) or cfg["device"] not in {"auto", "cpu", "cuda"}:
        raise ValueError("设备应为 auto/cpu/cuda / Invalid device")
    for key in ("base_url", "runtime_dir", "model_dir", "python_executable", "credential_ref", "service_id"):
        if not isinstance(cfg[key], str) or len(cfg[key]) > 2048:
            raise ValueError(f"{key} 无效 / invalid")
        cfg[key] = cfg[key].strip()
    cfg["base_url"] = validate_url(cfg["base_url"], cfg["mode"])
    for key in ("runtime_dir", "model_dir", "python_executable"):
        if cfg[key] and not Path(cfg[key]).expanduser().is_absolute():
            raise ValueError(f"{key} 需要绝对路径 / requires an absolute path")
    if cfg["enabled"] and not cfg["base_url"]:
        raise ValueError("启用前请填写并测试服务地址 / Configure a service URL before enabling")
    cfg["model"] = DEFAULTS["model"]
    if cfg["mode"] != "managed":
        cfg["auto_start"] = False
    return cfg


def save_config(payload: dict) -> dict:
    with LOCK:
        cfg = normalize_config(payload, load_config())
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, path)
        return cfg


def public_config(cfg: dict | None = None) -> dict:
    cfg = dict(cfg or load_config())
    cfg["token_configured"] = bool(cfg.pop("credential_ref", ""))
    cfg["auto_download"] = False
    cfg["ownership"] = "managed" if cfg["mode"] == "managed" else "external"
    return cfg
