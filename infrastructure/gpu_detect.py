#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPU 算力探测模块（纯函数，无状态，零 GPU 库依赖）。

双路径设计（G1 规划）：
- NVIDIA 独显：``nvidia-smi`` 探测（型号 / 显存 / 利用率）
- 集显 / 无独显：降级报告并引导远程方案，**绝不抛错**

「启用 GPU 算力」总开关状态也由此模块管理（存 runtime_config，与
llm_config.json 同级，非打包内目录）。

该模块是 G2 远程 / M2 训练 / K2 集群路由的共用算力探测基础。
"""
from __future__ import annotations

import json
import importlib
import logging
import shutil
import subprocess
import sys
from typing import Dict, List

from infrastructure.paths import runtime_config_path

log = logging.getLogger(__name__)

GPU_CONFIG_FILE = runtime_config_path("gpu_config.json", "config/gpu_config.json")

_NVIDIA_QUERY = (
    "--query-gpu=name,memory.total,memory.used,utilization.gpu",
    "--format=csv,noheader,nounits",
)


# ── NVIDIA 独显探测 ────────────────────────────────────────────────────────

def _to_int(raw: str, default: int = 0) -> int:
    """宽松解析整数，失败返回 default（不抛错）。"""
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _to_mb(raw: str) -> int:
    """nvidia-smi 显存输出带单位（如 '8192 MiB'），解析为 MB 整数。"""
    text = (raw or "").strip()
    if text.lower().endswith("mib"):
        return _to_int(text[:-3].strip())
    if text.lower().endswith("gb"):
        return _to_int(text[:-2].strip()) * 1024
    return _to_int(text)


def detect_nvidia() -> Dict:
    """探测 NVIDIA 独显。nvidia-smi 缺失 / 非零 / 解析失败时降级，不抛错。

    返回: {"kind": "nvidia"|"none", "gpus": [...], "message": str}
    """
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {
            "kind": "none",
            "gpus": [],
            "message": "未检测到 nvidia-smi：本机无 NVIDIA 独显，或驱动未安装",
        }
    try:
        proc = subprocess.run(
            [exe, *_NVIDIA_QUERY],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"kind": "none", "gpus": [], "message": f"nvidia-smi 执行失败: {exc}"}
    if proc.returncode != 0:
        return {
            "kind": "none",
            "gpus": [],
            "message": f"nvidia-smi 返回非零: {proc.stderr.strip() or 'unknown'}",
        }

    gpus: List[Dict] = []
    for line in proc.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            gpus.append({
                "name": parts[0],
                "memory_total_mb": _to_mb(parts[1]),
                "memory_used_mb": _to_mb(parts[2]),
                "utilization_pct": _to_int(parts[3]),
            })
    if not gpus:
        return {"kind": "none", "gpus": [], "message": "nvidia-smi 无 GPU 输出"}

    total_mb = sum(g["memory_total_mb"] for g in gpus)
    return {
        "kind": "nvidia",
        "gpus": gpus,
        "message": f"检测到 {len(gpus)} 块 NVIDIA 独显（显存合计 {total_mb} MB）",
    }


# ── Ollama 本地推理引擎探测（轻量，短超时）─────────────────────────────────

_OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"


def detect_ollama(timeout: float = 0.6) -> Dict:
    """探测本地 Ollama 服务是否在线并列出模型。失败降级，不抛错。"""
    import urllib.request

    try:
        with urllib.request.urlopen(_OLLAMA_TAGS_URL, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        models = [m.get("name") for m in data.get("models", []) if m.get("name")]
        return {
            "online": True,
            "models": models,
            "message": f"Ollama 在线，发现 {len(models)} 个模型",
        }
    except Exception as exc:  # noqa: BLE001 - 探测必须全面降级
        return {
            "online": False,
            "models": [],
            "message": f"Ollama 未运行（{exc.__class__.__name__}）",
        }


# ── 全显卡枚举（含集显，Windows WMI）────────────────────────────────────────

def detect_all_gpus() -> List[Dict]:
    """用 Windows WMI 枚举所有显卡（独显 + 集显），返回型号列表。

    nvidia-smi 只能看到 NVIDIA 独显；本函数通过 WMI 补全 Intel/AMD 集显信息。
    失败/非 Windows 返回空列表，不抛错。
    """
    if sys.platform != "win32":
        return []
    exe = shutil.which("wmic")
    if not exe:
        # wmic 在新版 Windows 可能被移除，回退 PowerShell
        exe = shutil.which("powershell")
        if not exe:
            return []
        try:
            proc = subprocess.run(
                [exe, "-NoProfile", "-Command",
                 "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if proc.returncode != 0:
            return []
        names = [n.strip() for n in proc.stdout.splitlines() if n.strip()]
    else:
        try:
            proc = subprocess.run(
                [exe, "path", "win32_VideoController", "get", "name", "/format:csv"],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if proc.returncode != 0:
            return []
        # CSV 输出：Node,Name\n DESKTOP,Intel...
        lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
        names = []
        for line in lines[1:]:  # 跳过表头
            parts = line.split(",")
            if len(parts) >= 2:
                names.append(parts[-1].strip())

    gpus = []
    for name in names:
        if not name:
            continue
        lower = name.lower()
        if "nvidia" in lower or "geforce" in lower or "rtx" in lower or "gtx" in lower:
            kind = "discrete"
        elif "intel" in lower:
            kind = "integrated"
        elif "amd" in lower or "radeon" in lower:
            kind = "discrete" if "radeon" in lower and "vega" not in lower else "integrated"
        else:
            kind = "unknown"
        gpus.append({"name": name, "kind": kind})
    return gpus


def detect_cuda() -> Dict:
    """Report whether this process can actually train with CUDA.

    A display adapter (including a WMI-discovered discrete adapter) is not
    evidence that the installed PyTorch build can use CUDA.  Keep that
    capability decision separate from hardware inventory so M2 never routes a
    job to a non-functional GPU.
    """
    try:
        torch = importlib.import_module("torch")
    except ImportError:
        return {
            "available": False,
            "device_count": 0,
            "message": "未安装 PyTorch，无法使用本机 CUDA 训练",
        }
    try:
        available = bool(torch.cuda.is_available())
        count = int(torch.cuda.device_count()) if available else 0
        return {
            "available": available,
            "device_count": count,
            "cuda_version": getattr(torch.version, "cuda", None),
            "message": (
                f"PyTorch CUDA 可用（{count} 块设备）" if available
                else "PyTorch 未检测到可用 CUDA 设备"
            ),
        }
    except Exception as exc:  # pragma: no cover - third-party driver failures
        return {
            "available": False,
            "device_count": 0,
            "message": f"CUDA 运行时探测失败: {exc.__class__.__name__}",
        }


# ── 「启用 GPU 算力」总开关 ────────────────────────────────────────────────

def get_gpu_enabled() -> bool:
    """读取总开关状态，默认开启。文件缺失/损坏时返回 True（不抛错）。"""
    try:
        if GPU_CONFIG_FILE.exists():
            data = json.loads(GPU_CONFIG_FILE.read_text(encoding="utf-8"))
            return bool(data.get("gpu_enabled", True))
    except Exception as exc:  # noqa: BLE001
        log.warning("[gpu] 读取开关失败，回退默认开启: %s", exc)
    return True


def set_gpu_enabled(enabled: bool) -> None:
    """写入总开关状态（原子：先写临时文件再替换）。"""
    GPU_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = GPU_CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"gpu_enabled": bool(enabled)}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(GPU_CONFIG_FILE)


# ── 聚合探测 ───────────────────────────────────────────────────────────────

def detect_all() -> Dict:
    """一次返回完整算力状态（前端 GPU 算力区使用）。"""
    nvidia = detect_nvidia()
    all_gpus = detect_all_gpus()
    # 如果 nvidia-smi 没检测到但 WMI 列出了 NVIDIA 独显，用 WMI 补充
    if nvidia["kind"] == "none" and any(g["kind"] == "discrete" for g in all_gpus):
        discrete = [g for g in all_gpus if g["kind"] == "discrete"]
        nvidia = {
            "kind": "discrete_wmi",
            "gpus": discrete,
            "message": f"WMI 检测到 {len(discrete)} 块独显（nvidia-smi 不可用，型号信息有限）",
        }
    # 没有 NVIDIA 独显但有集显时，更新 message
    if nvidia["kind"] == "none" and all_gpus:
        integrated = [g for g in all_gpus if g["kind"] == "integrated"]
        if integrated:
            names = "、".join(g["name"] for g in integrated)
            nvidia = {
                "kind": "integrated",
                "gpus": all_gpus,
                "message": f"检测到集显：{names}（不支持 CUDA，推理走云端/CPU）",
            }
    return {
        "gpu": nvidia,
        "all_gpus": all_gpus,
        "cuda": detect_cuda(),
        "ollama": detect_ollama(),
        "enabled": get_gpu_enabled(),
    }


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(detect_all(), indent=2, ensure_ascii=False))
