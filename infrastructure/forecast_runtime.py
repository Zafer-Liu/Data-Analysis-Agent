"""Opt-in local process lifecycle. All installation work starts from an explicit action."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

from data.forecast_config import load_config, save_config
from infrastructure.forecast_client import ForecastClient
from infrastructure.model_assets import download_model, inspect_model, runtime_python, validate_directory
from infrastructure.paths import resource_path, runtime_config_path

LOCK = threading.RLock()
_job = {"status": "idle", "stage": "", "message": "", "progress": 0}


def _manifest_path():
    return runtime_config_path("forecast_instance.json", "config/forecast_instance.json")


def _read_manifest():
    try:
        value = json.loads(_manifest_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied is not evidence of exit.
        try:
            code = ctypes.c_ulong()
            kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _update(stage, message, progress=0):
    with LOCK:
        _job.update(stage=stage, message=message, progress=progress)


def status():
    with LOCK:
        result = dict(_job)
    manifest = _read_manifest()
    result["instance"] = {k: manifest[k] for k in ("pid", "base_url", "instance_id") if k in manifest}
    result["managed_running"] = bool(manifest and _alive(manifest.get("pid")))
    return result


def busy():
    with LOCK:
        return _job["status"] == "running"


def _select_port(preferred):
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
        except OSError:
            sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start(config, python, env):
    existing = _read_manifest()
    if existing and _alive(existing.get("pid")):
        raise ValueError("应用托管实例仍在运行，请先停止再重新配置 / Stop the managed instance before reconfiguration")
    directory = validate_directory(config["runtime_dir"])
    script = resource_path("Function", "Analyze", "Time_Series_TimesFM3", "service.py")
    if not script.is_file():
        raise ValueError("本安装包缺少模型服务适配器 / This installation is missing the model service adapter")
    port = _select_port(config["port"])
    instance, secret = secrets.token_hex(16), secrets.token_hex(32)
    env = {**env, "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
           "TIMESFM_INSTANCE_ID": instance, "TIMESFM_INSTANCE_SECRET": secret}
    if config.get("credential_ref"):
        from infrastructure.credential_store import load_secret
        env["TIMESFM_API_TOKEN"] = load_secret(config["credential_ref"])
    else:
        env.pop("TIMESFM_API_TOKEN", None)
    device = config["device"]
    if device == "auto":
        from infrastructure.gpu_detect import get_gpu_enabled
        if not get_gpu_enabled():
            device = "cpu"
    logs = directory / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    _update("starting", "启动独立预测服务 / Starting local prediction service", 0)
    with (logs / "service.log").open("ab") as output:
        process = subprocess.Popen([python, str(script), "--model-dir", config["model_dir"],
            "--host", "127.0.0.1", "--port", str(port), "--device", device],
            cwd=directory, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    candidate = {**config, "mode": "managed", "base_url": f"http://127.0.0.1:{port}", "port": port, "service_id": ""}
    client = ForecastClient(candidate, require_enabled=False)
    manifest = {"pid": process.pid, "instance_id": instance, "secret": secret,
                "base_url": candidate["base_url"], "runtime_dir": str(directory)}
    _write_json(_manifest_path(), manifest)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("服务启动失败，请查看运行目录 logs/service.log / Service exited; see logs/service.log")
        try:
            health = client._request("/health", timeout=2)
            if health.get("instance_id") != instance:
                raise ValueError("端口被其他服务占用 / Port belongs to a different service")
            manifest["pid"] = health.get("pid", process.pid)
            _write_json(_manifest_path(), manifest)
            if health.get("status") == "failed":
                raise RuntimeError("模型加载失败，请查看设备与依赖版本 / Model loading failed; check device and dependency versions")
            if health.get("ready"):
                _update("testing", "验证预测输出 / Verifying prediction output", 95)
                client.smoke_test()
                candidate["enabled"] = True
                save_config(candidate)
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        except Exception as exc:
            from infrastructure.forecast_client import ForecastError
            if not isinstance(exc, ForecastError):
                raise
        time.sleep(0.5)
    raise RuntimeError("模型加载超时；实例保留供检查，可点击停止 / Model startup timed out; inspect or stop the managed instance")


def _stop():
    manifest = _read_manifest()
    if not manifest:
        raise ValueError("没有应用托管的实例；外部 API/Docker 请自行管理 / No app-managed instance; external services are not stopped")
    pid = manifest.get("pid")
    def stopped():
        _manifest_path().unlink(missing_ok=True)
        if load_config().get("base_url") == manifest.get("base_url"):
            save_config({"enabled": False, "auto_start": False})
    if not _alive(pid):
        stopped()
        return
    # Management authentication is independent of an API token subsequently edited in settings.
    base = manifest.get("base_url", "")
    from urllib.parse import urlsplit
    parsed = urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path or parsed.username or parsed.query:
        raise ValueError("托管实例地址无效 / Invalid managed instance address")
    request = urllib.request.Request(base + "/health", headers={"X-Instance-Token": manifest["secret"]})
    with urllib.request.urlopen(request, timeout=3) as response:
        health = json.load(response)
    if health.get("instance_id") != manifest.get("instance_id") or health.get("pid") != pid:
        raise ValueError("实例身份不匹配，拒绝停止其他进程 / Instance identity mismatch; refusing to stop another process")
    _update("stopping", "等待当前推理结束并停止服务 / Waiting for inference to finish and stopping service", 0)
    request = urllib.request.Request(manifest["base_url"] + "/shutdown", data=b"{}",
        headers={"X-Instance-Token": manifest["secret"], "Content-Type": "application/json"})
    with urllib.request.build_opener().open(request, timeout=5) as response:
        response.read(1024)
    for _ in range(240):
        if not _alive(pid):
            stopped()
            return
        time.sleep(0.5)
    raise RuntimeError("停止尚未确认；当前推理可能仍在执行 / Stop is not confirmed; inference may still be running")


def submit(operation: str):
    if not isinstance(operation, str) or operation not in {"download", "start", "stop"}:
        raise ValueError("不支持的操作 / Unsupported operation")
    with LOCK:
        if busy():
            raise ValueError("已有模型操作正在运行 / A model operation is already running")
        config = load_config()
        if operation != "stop" and config["mode"] != "managed":
            raise ValueError("请选择本地模型目录模式 / Select local model directory mode")
        if operation != "stop":
            validate_directory(config["runtime_dir"])
        _job.update(status="running", stage=operation, message="准备中 / Preparing", progress=0)

    def worker():
        try:
            if operation == "stop":
                _stop()
            else:
                if not config["model_dir"]:
                    config["model_dir"] = str(Path(config["runtime_dir"]) / "model")
                if operation == "download":
                    # Fail early for an invalid interpreter instead of downloading 1.32 GB first.
                    python, env = runtime_python(config, install=True, progress=_update)
                    download_model(config["model_dir"], _update)
                else:
                    _update("verifying", "校验已有模型 / Verifying existing model", 0)
                    inspect_model(config["model_dir"], verify_hash=True)
                    python, env = runtime_python(config, install=False, progress=_update)
                _start(config, python, env)
            with LOCK:
                _job.update(status="succeeded", stage=operation, message="操作完成 / Completed", progress=100)
        except Exception as exc:
            with LOCK:
                _job.update(status="failed", message=str(exc)[:500])

    threading.Thread(target=worker, name="forecast-local-setup", daemon=True).start()
    return status()


def resume_enabled():
    config = load_config()
    if config["mode"] == "managed" and config["enabled"] and config["auto_start"]:
        if not _alive(_read_manifest().get("pid")):
            submit("start")  # No download, pip install or image pull in this path.
