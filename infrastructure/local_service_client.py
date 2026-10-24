"""Bounded calls to explicitly configured endpoints; never execute Docker commands."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from data.local_services import validate_path
from infrastructure.forecast_client import ForecastClient

MAX_RESPONSE = 1024 * 1024


class ServiceError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def token_for(cfg):
    if cfg.get("credential_ref"):
        from infrastructure.credential_store import load_secret
        return load_secret(cfg["credential_ref"])
    return ""


def request_service(cfg, path, *, method="GET", body=None, token=None):
    path = validate_path(path)
    if method not in {"GET", "POST"}:
        raise ValueError("请求方法无效 / Invalid request method")
    headers = {"Accept": "application/json", "User-Agent": "SageAgent-LocalServices/1.0"}
    secret = token_for(cfg) if token is None else token
    if secret:
        headers["Authorization"] = "Bearer " + secret
    data = None
    if method == "POST":
        if not isinstance(body, dict):
            raise ValueError("请求内容须为 JSON 对象 / Request body must be a JSON object")
        try:
            data = json.dumps(body, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, RecursionError) as exc:
            raise ValueError("请求内容不是有效 JSON / Invalid request JSON") from exc
        if len(data) > 256 * 1024:
            raise ValueError("请求内容过大 / Request body is too large")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(cfg["base_url"].rstrip("/") + path, data=data, method=method, headers=headers)
    start = time.perf_counter()
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=cfg["timeout"]) as response:
            raw = response.read(MAX_RESPONSE + 1)
            status = response.status
    except urllib.error.HTTPError as exc:
        raise ServiceError(f"服务返回 HTTP {exc.code}，请检查地址、路径与认证 / Service returned HTTP {exc.code}; check URL, path and authentication") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ServiceError("服务连接失败或超时，请确认服务已启动 / Connection failed or timed out; check the service is running") from None
    if len(raw) > MAX_RESPONSE:
        raise ServiceError("响应超过 1 MiB，请缩小请求 / Response exceeds 1 MiB; reduce the request")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        value = raw.decode("utf-8", errors="replace")
    return {"status_code": status, "elapsed_ms": round((time.perf_counter() - start) * 1000), "response": value}


def openai_base(cfg):
    url = cfg["base_url"].rstrip("/")
    return url if url.endswith("/v1") else url + "/v1"


def discover_models(cfg, token=None):
    value = request_service({**cfg, "base_url": openai_base(cfg)}, "/models", token=token)
    response = value["response"]
    entries = response.get("data") if isinstance(response, dict) else None
    if not isinstance(entries, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in entries):
        raise ServiceError("响应不符合 OpenAI 模型列表协议 / Invalid OpenAI model-list response")
    value["models"] = list(dict.fromkeys(item["id"] for item in entries if 0 < len(item["id"]) <= 256))[:200]
    value.pop("response")
    return value


def probe(cfg, token=None):
    start = time.perf_counter()
    if cfg["protocol"] == "timesfm":
        health = ForecastClient({**cfg, "mode": "docker" if cfg["transport"] == "docker" else "api"},
            require_enabled=False, token=token).health()
        result = {"device": health.get("device", "unknown"), "model": health.get("model")}
    elif cfg["protocol"] == "openai":
        result = discover_models(cfg, token)
    else:
        checked = request_service(cfg, cfg["health_path"], token=token)
        result = {"status_code": checked["status_code"]}
    return {**result, "elapsed_ms": round((time.perf_counter() - start) * 1000)}
