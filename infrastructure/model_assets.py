"""Explicit, pinned model downloads and isolated runtime preparation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

MODEL = "google/timesfm-3.0-pytorch"
REVISION = "43046b85ec22d584a13f8098c2ed39c889e129c2"
WEIGHT_SIZE = 1322898824
WEIGHT_SHA256 = "a7592b0a8432baee54483254e5647856911ce69e09d09a9bb65904b2d98f17da"


def validate_directory(value: str) -> Path:
    if not value or not Path(value).expanduser().is_absolute():
        raise ValueError("请选择绝对目录 / Select an absolute directory")
    path = Path(value).expanduser().resolve()
    if path == Path(path.anchor) or len(path.parts) < 3:
        raise ValueError("请选择专用子目录，不能使用磁盘根目录 / Select a dedicated subdirectory")
    return path


def inspect_model(directory: str, *, verify_hash=False) -> dict:
    path = validate_directory(directory)
    config, weights = path / "config.json", path / "model.safetensors"
    if not config.is_file() or not weights.is_file():
        raise ValueError("模型目录须包含 config.json 和 model.safetensors / Missing model files")
    try:
        value = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("模型 config.json 无法读取 / Invalid model config") from None
    if (value.get("input_patch_len") != 32 or value.get("output_patch_len") != 64
            or value.get("transformer_config", {}).get("num_layers") != 20
            or weights.stat().st_size != WEIGHT_SIZE):
        raise ValueError("目录与已支持的官方 TimesFM-3 版本不匹配 / Model does not match supported TimesFM-3 revision")
    if verify_hash:
        with weights.open("rb") as source:
            sha = hashlib.file_digest(source, "sha256").hexdigest()
        if sha != WEIGHT_SHA256:
            raise ValueError("模型 SHA-256 不匹配，请重新下载 / Model SHA-256 mismatch")
    return {"model": MODEL, "revision": REVISION, "directory": str(path),
            "bytes": weights.stat().st_size, "hash_verified": verify_hash}


def download_model(directory: str, progress=lambda *_: None):
    path = validate_directory(directory)
    path.mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "LICENSE", "README.md", "model.safetensors"):
        target = path / name
        if target.exists() and name != "model.safetensors":
            continue
        if name == "model.safetensors" and target.is_file():
            try:
                inspect_model(str(path), verify_hash=True)
                progress("verifying", "现有模型校验通过 / Existing model verified", 100)
                continue
            except ValueError:
                raise ValueError("已有同名权重与固定版本不匹配，请选择空目录 / Existing weights differ; choose an empty directory") from None
        partial = target.with_suffix(target.suffix + ".part")
        offset = partial.stat().st_size if partial.exists() else 0
        if name == "model.safetensors" and offset >= WEIGHT_SIZE:
            with partial.open("rb") as source:
                valid = offset == WEIGHT_SIZE and hashlib.file_digest(source, "sha256").hexdigest() == WEIGHT_SHA256
            if valid:
                os.replace(partial, target)
                continue  # A previous download finished before the application exited.
            partial.write_bytes(b"")  # Reset only this owned, incomplete weight download.
            offset = 0
        elif name != "model.safetensors":
            offset = 0  # Small metadata files restart; only the large weight is resumed.
        headers = {"User-Agent": "SageAgent-TimesFM-Setup/1.0"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}", headers=headers)
        progress("downloading", f"下载 {name} / Downloading {name}", 0)
        with urllib.request.urlopen(request, timeout=45) as response:
            append = bool(offset and response.status == 206)
            if append and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                raise ValueError("下载续传范围不一致 / Invalid resumed download range")
            received = offset if append else 0
            with partial.open("ab" if append else "wb") as dest:
                while chunk := response.read(1024 * 1024):
                    dest.write(chunk)
                    received += len(chunk)
                    if name == "model.safetensors":
                        progress("downloading", f"权重 {received // (1024*1024)} / 1262 MiB", min(99, int(received / WEIGHT_SIZE * 100)))
        os.replace(partial, target)
    progress("verifying", "校验模型文件 / Verifying model files", 100)
    return inspect_model(str(path), verify_hash=True)


def runtime_python(config, *, install=False, progress=lambda *_: None):
    root = validate_directory(config.get("runtime_dir", ""))
    configured = config.get("python_executable", "").strip()
    python = Path(configured) if configured else root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    root.mkdir(parents=True, exist_ok=True)
    temp = root / "temp"
    temp.mkdir(exist_ok=True)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
           "PIP_CACHE_DIR": str(root / "cache" / "pip"), "TEMP": str(temp), "TMP": str(temp),
           "HF_HOME": str(root / "cache" / "huggingface")}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    logs = root / "logs"
    logs.mkdir(exist_ok=True)

    def command(args):
        with (logs / "setup.log").open("ab") as log:
            result = subprocess.run(args, cwd=root, env=env, stdout=log, stderr=log,
                                    timeout=3600, check=False, creationflags=flags)
        if result.returncode:
            raise RuntimeError("运行环境准备失败，请查看所选目录 logs/setup.log / Runtime preparation failed; see logs/setup.log")

    if not python.is_file():
        if not install or configured:
            raise ValueError("Python 环境不存在，请选择已有解释器或使用下载并配置 / Python environment missing")
        if getattr(sys, "frozen", False):
            raise ValueError("桌面安装版需指定已有 Python 解释器，或改接 API/Docker / Packaged app requires an external Python interpreter")
        progress("environment", "创建独立运行环境 / Creating isolated environment", 0)
        command([sys.executable, "-m", "venv", str(root / ".venv")])
    # Probe availability without loading torch or allocating GPU memory.
    check = subprocess.run([str(python), "-c", "import importlib.util,sys; sys.exit(0 if all(importlib.util.find_spec(n) for n in ('numpy','torch','timesfm3')) else 1)"],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, creationflags=flags)
    if check.returncode:
        if not install:
            raise ValueError("解释器缺少 timesfm3/torch/numpy，请准备依赖或选择下载并配置 / Required runtime packages are missing")
        if configured:
            raise ValueError("不会修改指定的外部 Python 环境；请先安装依赖，或留空让应用创建独立环境 / External Python environments are never modified")
        progress("environment", "安装独立运行依赖（可能较大）/ Installing isolated runtime dependencies", 0)
        command([str(python), "-m", "pip", "install", "timesfm[torch]==3.0.2"])
    return str(python), env
