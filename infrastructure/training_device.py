"""Choose a safe local training device without importing torch in core paths."""
from __future__ import annotations

from typing import Any

from infrastructure import gpu_detect


def select_training_device(min_free_memory_mb: int = 2048) -> dict[str, Any]:
    """Return a deterministic CPU/CUDA decision and a UI-safe explanation.

    CUDA is selected only when the user's GPU switch is enabled, PyTorch's
    runtime reports CUDA availability, and nvidia-smi has sufficient free VRAM.
    Every other case is a supported CPU fallback, not an error.
    """
    if min_free_memory_mb < 0:
        raise ValueError("min_free_memory_mb 必须非负")
    if not gpu_detect.get_gpu_enabled():
        return {"device": "cpu", "reason": "GPU 算力开关已关闭", "cuda_available": False}

    cuda = gpu_detect.detect_cuda()
    if not cuda.get("available"):
        return {"device": "cpu", "reason": cuda["message"], "cuda_available": False}

    nvidia = gpu_detect.detect_nvidia()
    gpus = nvidia.get("gpus", [])
    candidates = [
        (index, int(gpu.get("memory_total_mb", 0)) - int(gpu.get("memory_used_mb", 0)))
        for index, gpu in enumerate(gpus)
    ]
    if candidates:
        index, free_memory = max(candidates, key=lambda item: item[1])
        if free_memory < min_free_memory_mb:
            return {
                "device": "cpu",
                "reason": f"可用 CUDA 显存仅 {free_memory} MB，低于训练阈值 {min_free_memory_mb} MB",
                "cuda_available": True,
            }
        return {
            "device": f"cuda:{index}", "reason": f"CUDA 可用，剩余显存 {free_memory} MB",
            "cuda_available": True, "free_memory_mb": free_memory,
        }

    # nvidia-smi can be unavailable while torch CUDA remains usable (e.g. some
    # managed environments). Keep this explicit rather than treating WMI as
    # proof of a particular NVIDIA device.
    return {
        "device": "cuda", "reason": "PyTorch CUDA 可用，但无法读取显存；按运行时默认设备训练",
        "cuda_available": True,
    }
