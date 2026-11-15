"""Bounded recommendations and explicit stable/dynamic prompt assembly.

Recommendations never discard evidence, shorten user instructions, expand tool
permissions or enable a model/provider switch. Callers adopt them deliberately.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from LLM.prompt_cache import build_cacheable_prompt


_COMPONENTS = {
    "tool_schema_tokens_est": ("tool_exposure", 2400, "Rank authorized tools; ranking never changes invocation permissions."),
    "history_tokens_est": ("history", 6000, "Summarize old narration; retain current instructions, tool-call pairs and evidence references."),
    "tool_result_tokens_est": ("tool_results", 2000, "Use bounded previews plus authorized artifact reads; preserve claim references."),
    "memory_tokens_est": ("memory", 1200, "Select relevant memory; retain explicit user constraints."),
    "system_tokens_est": ("stable_prefix", 4000, "Place stable rules before dynamic context without changing instruction precedence."),
}


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
        return int(number) if math.isfinite(number) and number >= 0 and number.is_integer() else None
    except (ValueError, TypeError, OverflowError):
        return None


def recommend_role_limits(
    profile: Mapping[str, Any], *, requested_output_tokens: int | None = None,
    requested_tool_calls: int | None = None,
) -> dict[str, Any]:
    """Intersect caller limits with an existing profile, including zero tools.

    None inherits the existing limit. These are recommendations until applied by
    the executor; they neither grant tools nor remove required quality review.
    """
    result: dict[str, Any] = {"applied": False, "reason": "recommendation_only"}
    for field, requested in (("max_output_tokens", requested_output_tokens), ("max_tool_calls", requested_tool_calls)):
        profile_value = profile.get(field)
        cap = _count(profile_value)
        request = _count(requested)
        if profile_value is not None and cap is None or requested is not None and request is None:
            raise ValueError("role limits must be non-negative integers or None")
        result[field] = min(cap, request) if cap is not None and request is not None else (cap if cap is not None else request)
    result["model_policy"] = "inherit"
    result["review_required"] = True
    return result


def recommend_context_optimization(
    breakdown: Mapping[str, Any], *, max_recommendations: int = 3,
    gate: Mapping[str, Any] | None = None, policy_id: str = "context-v1",
) -> dict[str, Any]:
    """Rank measured prompt estimates; each budget is bounded and advisory.

    History already contains tool results, so these overlapping diagnostics must
    not be summed to claim savings. Missing estimates produce no recommendation.
    """
    limit = max(0, min(int(max_recommendations), 5))
    ranked = []
    for field, (component, budget, guidance) in _COMPONENTS.items():
        count = _count(breakdown.get(field))
        if count is not None and count > budget:
            ranked.append({"component": component, "observed_tokens_est": count,
                           "target_tokens_est": budget, "guidance": guidance})
    ranked.sort(key=lambda row: (-row["observed_tokens_est"], row["component"]))
    eligible = bool(gate and gate.get("eligible") is True and gate.get("policy_id") == policy_id
                    and gate.get("measurement_mode") == "actual_model"
                    and gate.get("manifest_hash") and gate.get("comparison_hash")
                    and gate.get("reasons") == [])
    return {"policy_id": policy_id, "applied": False, "reason": "recommendation_only",
            "gate_eligible": eligible, "estimates_are_additive": False,
            "recommendations": ranked[:limit], "preserve_evidence": True,
            "preserve_user_constraints": True, "model_policy": "inherit"}


def assemble_stable_context(
    *, stable_rules: Sequence[Mapping[str, Any]], dynamic_context: Sequence[Mapping[str, Any]],
    history: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]],
    provider: str, model: str, workflow_stage: str = "", stable_revision: str = "",
) -> dict[str, Any]:
    """Keep caller order and separate immutable rules from per-run state.

    Inputs are copied. The caller decides which rules are genuinely stable and
    preserves system/developer instruction precedence when adopting this helper.
    """
    return build_cacheable_prompt(stable_messages=stable_rules,
                                 dynamic_messages=[*dynamic_context, *history], tools=tools,
                                 provider=provider, model=model, workflow_stage=workflow_stage,
                                 stable_revision=stable_revision)
