"""Unified local-CUDA / remote-runner / CPU training target selection."""
from __future__ import annotations

from typing import Any, Iterable

from infrastructure.training_device import select_training_device


def select_training_target(
    remote_runners: Iterable[dict[str, Any]] = (), *, min_free_memory_mb: int = 2048,
) -> dict[str, Any]:
    """Pick the safest available training backend in priority order.

    A regular GPU inference tunnel is *not* a remote runner.  A remote entry is
    eligible only after M3's restricted runner has completed its preflight and
    advertises ``runner_ready=True``.  This prevents sending training code to a
    generic SSH host or LLM endpoint.
    """
    local = select_training_device(min_free_memory_mb)
    if local["device"].startswith("cuda"):
        return {"kind": "local", "device": local["device"], "reason": local["reason"]}

    for runner in remote_runners:
        if runner.get("runner_ready") and runner.get("id"):
            return {
                "kind": "remote", "runner_id": runner["id"],
                "reason": f"本机 CUDA 不可用；使用已就绪远程训练器 {runner.get('name') or runner['id']}",
            }
    return {"kind": "cpu", "device": "cpu", "reason": local["reason"]}
