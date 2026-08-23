"""Durable, non-secret definitions for remote GPU inference connections."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from infrastructure.paths import runtime_config_path


CONNECTIONS_FILE = runtime_config_path("gpu_connections.json", "config/gpu_connections.json")
_HOST_RE = re.compile(r"^[A-Za-z0-9._:-]+$")


def _port(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是 1 到 65535 的整数")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是 1 到 65535 的整数") from exc
    if not 1 <= result <= 65535:
        raise ValueError(f"{field} 必须是 1 到 65535 的整数")
    return result


def _host(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result or not _HOST_RE.fullmatch(result):
        raise ValueError(f"{field} 格式无效")
    return result


def _base_url(value: Any) -> str:
    result = str(value or "").strip().rstrip("/")
    parsed = urlsplit(result)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("base_url 必须是有效的 HTTP(S) 地址，且不能包含凭据")
    is_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not is_local:
        raise ValueError("公网直连必须使用 HTTPS；仅 localhost 可使用 HTTP")
    return result


def _read() -> dict[str, dict[str, Any]]:
    if not CONNECTIONS_FILE.exists():
        return {}
    try:
        raw = json.loads(CONNECTIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("GPU 连接配置损坏，未加载任何连接") from exc
    return raw if isinstance(raw, dict) else {}


def _write(data: dict[str, dict[str, Any]]) -> None:
    CONNECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONNECTIONS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(CONNECTIONS_FILE)


def list_connections() -> list[dict[str, Any]]:
    return [{"id": identifier, **config} for identifier, config in _read().items()]


def get_connection(connection_id: str) -> Optional[dict[str, Any]]:
    config = _read().get(connection_id)
    return {"id": connection_id, **config} if config else None


def create_connection(payload: dict[str, Any], *, credential_ref: Optional[str] = None) -> dict[str, Any]:
    """Validate and persist non-secret settings. Password itself never enters this file."""
    connection_type = str(payload.get("connection_type") or "ssh").strip()
    if connection_type not in {"ssh", "direct"}:
        raise ValueError("connection_type 必须是 ssh 或 direct")
    auth_method = str(payload.get("auth_method") or "agent").strip()
    if auth_method not in {"agent", "password", "key_file"}:
        raise ValueError("auth_method 必须是 agent、password 或 key_file")
    if connection_type == "ssh" and auth_method == "password" and not credential_ref:
        raise ValueError("密码认证必须先保存凭据")
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ValueError("name 不能为空且最长 80 个字符")
    config: dict[str, Any] = {
        "name": name,
        "connection_type": connection_type,
    }
    if connection_type == "direct":
        config["base_url"] = _base_url(payload.get("base_url"))
    else:
        config.update({
            "host": _host(payload.get("host"), "host"),
            "port": _port(payload.get("port", 22), "port"),
            "username": str(payload.get("username") or "").strip(),
            "target_host": _host(payload.get("target_host", "127.0.0.1"), "target_host"),
            "target_port": _port(payload.get("target_port"), "target_port"),
            "auth_method": auth_method,
        })
        if not config["username"]:
            raise ValueError("username 不能为空")
        if auth_method == "password":
            config["credential_ref"] = credential_ref
        elif auth_method == "key_file":
            key_file = str(payload.get("key_file") or "").strip()
            if not key_file:
                raise ValueError("key_file 不能为空")
            config["key_file"] = key_file
    identifier = uuid.uuid4().hex
    data = _read()
    data[identifier] = config
    _write(data)
    return {"id": identifier, **config}


def delete_connection(connection_id: str) -> Optional[dict[str, Any]]:
    data = _read()
    config = data.pop(connection_id, None)
    if config is None:
        return None
    _write(data)
    return {"id": connection_id, **config}


def set_training_runner_status(connection_id: str, status: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Persist only non-secret, validated M3 preflight metadata."""
    data = _read()
    config = data.get(connection_id)
    if config is None:
        return None
    config["training_runner"] = {
        "runner_ready": bool(status.get("runner_ready")),
        "runner_version": str(status.get("runner_version") or "unknown")[:80],
        "python": str(status.get("python") or "unknown")[:120],
        "cuda": bool(status.get("cuda")),
        "gpu_name": str(status.get("gpu_name") or "")[:160],
    }
    _write(data)
    return {"id": connection_id, **config}
