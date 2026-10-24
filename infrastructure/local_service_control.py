"""Stop only app-owned services or explicitly registered local stop scripts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit

from infrastructure import forecast_runtime
from infrastructure.paths import runtime_config_path

LOCK = threading.RLock()
_stopping = set()


def _path():
    return runtime_config_path("local_service_controls.json", "config/local_service_controls.json")


def _read():
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise RuntimeError("停止配置无法读取 / Cannot read service control settings") from exc


def _write(value):
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temp, path)


def _local(cfg):
    parsed = urlsplit(cfg["base_url"])
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _fingerprint(cfg):
    return {k: cfg[k] for k in ("base_url", "protocol", "transport")}


def register_stop_script(cfg, script):
    """Trusted local setup only. No HTTP endpoint accepts executable paths."""
    if os.name != "nt" or not _local(cfg) or cfg["transport"] != "http":
        raise ValueError("停止脚本仅用于本机 Windows 服务 / Stop scripts require a local Windows service")
    path = Path(script).expanduser()
    if not path.is_absolute() or path.suffix.lower() != ".ps1" or not path.is_file():
        raise ValueError("请指定现有 PowerShell 停止脚本 / Provide an existing PowerShell stop script")
    with LOCK:
        values = _read()
        values[cfg["id"]] = {"endpoint": _fingerprint(cfg), "script": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "stopped": False}
        _write(values)


def capability(cfg):
    runtime = forecast_runtime.status()
    owned = runtime.get("managed_running") and runtime.get("instance", {}).get("base_url") == cfg["base_url"]
    with LOCK:
        record = _read().get(cfg.get("id"), {})
        registered = bool(os.name == "nt" and record and record.get("endpoint") == _fingerprint(cfg) and _local(cfg))
        stopped = registered and record.get("stopped") is True
        return {"supported": bool(owned or registered), "can_stop": bool(owned or (registered and not stopped)),
            "stopped": bool(stopped and not owned), "stopping": cfg.get("id") in _stopping}


def mark_started(cfg):
    with LOCK:
        values = _read()
        record = values.get(cfg["id"])
        if record and record.get("endpoint") == _fingerprint(cfg):
            record["stopped"] = False
            _write(values)


def forget(identifier):
    with LOCK:
        values = _read()
        if identifier in values:
            del values[identifier]
            _write(values)


def stop_owned():
    forecast_runtime.submit("stop")
    deadline = time.monotonic() + 130
    while time.monotonic() < deadline:
        state = forecast_runtime.status()
        if state["status"] != "running":
            if state["status"] != "succeeded":
                raise RuntimeError(state.get("message") or "停止失败 / Stop failed")
            return
        time.sleep(0.25)
    raise RuntimeError("尚未确认停止，请稍后刷新 / Stop not yet confirmed; refresh shortly")


def stop(cfg):
    identifier = cfg["id"]
    with LOCK:
        if identifier in _stopping:
            raise ValueError("模型正在停止，请稍候 / Model is already stopping")
        info = capability(cfg)
        if not info["supported"]:
            raise ValueError("此服务未配置停止方式，请在运行它的程序中停止 / Stop this service in the application that runs it")
        if info["stopped"]:
            return
        _stopping.add(identifier)
        record = _read().get(identifier)
    try:
        runtime = forecast_runtime.status()
        if runtime.get("managed_running") and runtime.get("instance", {}).get("base_url") == cfg["base_url"]:
            stop_owned()
        else:
            if os.name != "nt" or not record or not _local(cfg):
                raise ValueError("停止方式不可用 / Stop control is unavailable")
            script = Path(record["script"])
            if not script.is_file():
                raise ValueError("停止脚本不存在，请重新登记 / Stop script is missing; register it again")
            if hashlib.sha256(script.read_bytes()).hexdigest() != record["sha256"]:
                raise ValueError("停止脚本已修改，请重新登记 / Stop script changed; register it again")
            # Script is explicitly registered by trusted setup; the browser supplies only a service ID.
            try:
                result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script)],
                    cwd=script.parent, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=45, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired:
                raise RuntimeError("停止超时，尚未确认模型退出 / Stop timed out; model exit is not confirmed") from None
            if result.returncode:
                raise RuntimeError("停止失败，请检查模型服务的停止脚本 / Stop failed; check the model service stop script")
            parsed = urlsplit(cfg["base_url"])
            for _ in range(20):
                try:
                    with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=0.3):
                        pass
                except OSError:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("服务仍在运行，尚未确认停止 / Service is still running; stop is not confirmed")
        with LOCK:
            values = _read()
            if record and values.get(identifier) == record:
                values[identifier]["stopped"] = True
                _write(values)
    finally:
        with LOCK:
            _stopping.discard(identifier)
