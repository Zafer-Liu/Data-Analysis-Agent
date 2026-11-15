"""Pure, opt-in role tier resolution using server-approved mappings and gates."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def resolve_profile_model(
    profile: Mapping[str, Any], *, inherited_model: str, inherited_provider: str = "",
    tier_mapping: Mapping[str, Any] | None = None, gate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a model without reading configuration, credentials or the network.

    tier_mapping is trusted server configuration: {version, policy_id, approved:
    true, tiers: {tier: {provider, model}}}. A gate must come from
    evaluate_optimization_gate, match policy_id and include this tested model.
    Never accept mapping approval or gate declarations from model tool arguments.
    Missing or invalid approvals inherit the user's selected model.
    """
    mapping = tier_mapping if isinstance(tier_mapping, Mapping) else {}
    version = mapping.get("version")
    result = {"model": inherited_model, "provider": inherited_provider, "applied": False,
              "reason": "mapping_absent", "policy_version": version if isinstance(version, (str, int)) and not isinstance(version, bool) else None}

    def inherit(reason: str) -> dict[str, Any]:
        result["reason"] = reason
        return result

    tier = str(profile.get("model_tier") or "inherit")
    if tier == "inherit":
        return inherit("profile_inherits")
    if not mapping:
        return result
    if (mapping.get("approved") is not True or isinstance(version, bool)
            or not isinstance(version, (str, int)) or not str(version).strip()
            or isinstance(version, int) and version < 1 or not mapping.get("policy_id")):
        return inherit("mapping_not_explicitly_approved")
    tiers = mapping.get("tiers")
    candidate = tiers.get(tier) if isinstance(tiers, Mapping) else None
    if not isinstance(candidate, Mapping) or not isinstance(candidate.get("model"), str) or not candidate["model"].strip():
        return inherit("tier_unmapped")
    provider = candidate.get("provider")
    if not inherited_provider or provider != inherited_provider:
        return inherit("provider_change_requires_separate_configuration")
    if not isinstance(gate, Mapping) or not (
        gate.get("eligible") is True and gate.get("eligibility") == "eligible"
        and gate.get("measurement_mode") == "actual_model"
        and gate.get("policy_id") == mapping["policy_id"]
        and gate.get("manifest_hash") and gate.get("comparison_hash")
        and gate.get("reasons") == []
    ):
        return inherit("gate_absent_or_ineligible")
    tested = gate.get("models")
    if not isinstance(tested, list) or {"provider": provider, "model": candidate["model"]} not in tested:
        return inherit("model_not_tested_by_gate")
    result.update({"model": candidate["model"], "provider": provider,
                   "applied": candidate["model"] != inherited_model,
                   "reason": "approved_mapping_and_gate"})
    return result
