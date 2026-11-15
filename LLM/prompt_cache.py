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
    stable_prefix: str | Sequence[Mapping[str, Any]] = "",
    stable_revision: str = "",
) -> str:
    """Fingerprint full stable schemas/content; return only an opaque digest.

    Callers explicitly supply stable rules. Questions, knowledge, memory, user
    instructions and task state belong in the dynamic suffix. Object key order
    is canonicalized; tool/message sequence order remains significant because
    it changes the provider's actual prefix. No prefix text is logged here.
    """
    payload = json.dumps({
        "v": 2,
        "provider": str(provider or ""),
        "model": str(model or ""),
        "stage": str(workflow_stage or ""),
        "tools": list(tools),
        "stable_prefix": stable_prefix,
        "stable_revision": str(stable_revision or ""),
    }, ensure_ascii=True, separators=(",", ":"), sort_keys=True, allow_nan=False)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"baa-{digest}"


def build_cacheable_prompt(
    *, stable_messages: Sequence[Mapping[str, Any]],
    dynamic_messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]],
    provider: str, model: str, workflow_stage: str = "", stable_revision: str = "",
) -> dict[str, Any]:
    """Assemble an explicit stable prefix followed by unmodified dynamic state.

    This is opt-in: it never guesses which existing system messages are stable,
    trims instructions, or inserts provider-specific cache breakpoints. The
    caller must preserve its existing instruction precedence when adopting it.
    """
    from copy import deepcopy

    stable = deepcopy(list(stable_messages))
    dynamic = deepcopy(list(dynamic_messages))
    schemas = deepcopy(list(tools))
    if any(message.get("role") not in {"system", "developer"} for message in stable):
        raise ValueError("stable_messages must contain only system/developer rules")
    key = stable_prompt_cache_key(
        provider=provider, model=model, workflow_stage=workflow_stage, tools=schemas,
        stable_prefix=stable, stable_revision=stable_revision,
    )
    return {
        "messages": stable + dynamic, "tools": schemas, "cache_key": key,
        "metadata": {
            "identity_version": 2, "stable_message_count": len(stable),
            "dynamic_message_count": len(dynamic), "tool_count": len(schemas),
        },
    }


def cache_scope_user_id(*, user_id: str = "", workspace_id: str = "") -> str:
    """Return an opaque provider-safe cache isolation id without PII."""
    owner = json.dumps([str(user_id or "local-default"), str(workspace_id or "user")], separators=(",", ":"))
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
