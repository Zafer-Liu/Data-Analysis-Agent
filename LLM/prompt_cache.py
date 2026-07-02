"""Provider-aware Prompt Cache request adaptation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class PromptCachePolicy:
    enabled: bool = False
    mode: str = "none"
    retention: str = "in_memory"
    breakpoint_strategy: str = "stable_prefix"


def stable_prompt_cache_key(
    *,
    provider: str,
    model: str,
    workflow_stage: str,
    tools: Sequence[Mapping[str, Any]],
) -> str:
    """Build a non-sensitive routing key for one stable capability prefix."""
    tool_names = [
        str(((tool.get("function") or {}).get("name") or ""))
        for tool in tools
    ]
    payload = json.dumps({
        "v": 1,
        "provider": str(provider or ""),
        "model": str(model or ""),
        "stage": str(workflow_stage or ""),
        "tools": tool_names,
    }, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"baa-{digest}"


def cache_scope_user_id(*, user_id: str = "", workspace_id: str = "") -> str:
    """Return an opaque provider-safe cache isolation id without PII."""
    owner = f"{str(user_id or 'local-default')}|{str(workspace_id or 'user')}"
    digest = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:32]
    return f"baa_{digest}"


def apply_prompt_cache_policy(
    call_kwargs: Mapping[str, Any],
    *,
    policy: PromptCachePolicy,
    cache_key: str,
    user_id: str = "",
    workspace_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply only request fields documented for the selected provider mode."""
    updated = dict(call_kwargs)
    metadata = {
        "enabled": bool(policy.enabled),
        "mode": str(policy.mode or "none"),
        "cache_key": "",
        "retention": "",
        "scope_isolated": False,
    }
    if not policy.enabled:
        return updated, metadata

    mode = str(policy.mode or "automatic").lower()
    if mode == "openai":
        scope_id = cache_scope_user_id(
            user_id=user_id,
            workspace_id=workspace_id,
        )
        scoped_digest = hashlib.sha256(
            f"{cache_key}|{scope_id}".encode("utf-8")
        ).hexdigest()[:32]
        updated["prompt_cache_key"] = f"baa-{scoped_digest}"
        retention = (
            policy.retention
            if policy.retention in {"in_memory", "24h"}
            else "in_memory"
        )
        updated["prompt_cache_retention"] = retention
        metadata.update({
            "cache_key": updated["prompt_cache_key"],
            "retention": retention,
            "scope_isolated": True,
        })
    elif mode == "deepseek":
        extra_body = dict(updated.get("extra_body") or {})
        extra_body["user_id"] = cache_scope_user_id(
            user_id=user_id,
            workspace_id=workspace_id,
        )
        updated["extra_body"] = extra_body
        metadata["scope_isolated"] = True
    elif mode == "automatic":
        # Provider performs prefix caching without request parameters.
        pass
    else:
        metadata["enabled"] = False
        metadata["mode"] = "none"
    return updated, metadata
