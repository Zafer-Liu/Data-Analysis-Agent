"""Import deidentified real trials and fail closed on incomparable cost evidence.

No model calls are made here. An operator supplies provider receipts or verified
usage and an immutable price version. Offline adapters cannot activate policies.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from statistics import median
from typing import Any

from data.cost_baseline import load_trials, validate_manifest, validate_trial


_MATCH_FIELDS = (
    "manifest_hash", "snapshot_id", "prompt_hash", "provider", "model",
    "price_version", "currency", "cache_config", "team_config", "runtime_version",
    "tool_contract_version",
)
_ACTUAL_SOURCES = frozenset({"provider_reported", "operator_verified"})
_COST_SOURCES = frozenset({"provider_reported", "operator_verified", "price_table"})


def _fingerprint(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def manifest_fingerprint(manifest: Mapping[str, Any]) -> str:
    validate_manifest(manifest)
    return _fingerprint(dict(manifest))


def prompt_fingerprint(prompt: str) -> str:
    return "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _records(records: Iterable[Mapping[str, Any]] | Path | str) -> list[dict[str, Any]]:
    return load_trials(records) if isinstance(records, (Path, str)) else [dict(row) for row in records]


def import_real_trials(
    manifest: Mapping[str, Any], records: Iterable[Mapping[str, Any]] | Path | str,
    *, variant: str | None = None,
) -> list[dict[str, Any]]:
    """Validate actual usage receipts; preserve failed calls/trials for reporting.

    This checks provenance declarations and metric completeness, not authenticity
    of a provider invoice. The import source must be controlled by the operator.
    Raw prompts, answers, SQL, messages and source rows are rejected.
    """
    fingerprint = manifest_fingerprint(manifest)
    cases = {case["id"]: case for case in manifest["cases"]}
    result = _records(records)
    seen_pairs, seen_trials, seen_calls = set(), set(), set()
    for row in result:
        validate_trial(row, strict=True)
        case = cases.get(row["case_id"])
        if case is None:
            raise ValueError("real trial references an unknown case")
        if row.get("measurement_mode") != "actual_model" or row.get("usage_source") not in _ACTUAL_SOURCES:
            raise ValueError("actual_model with reported/verified usage is required")
        if row.get("usage_complete") is not True:
            raise ValueError("real trial must include complete usage for every call attempt")
        if row.get("cost_source") not in _COST_SOURCES:
            raise ValueError("real trial needs a reported, verified or versioned-table cost")
        for field in (*_MATCH_FIELDS, "pair_id", "variant", "policy_id"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"real trial needs an opaque {field}")
        if row["manifest_hash"] != fingerprint or row["snapshot_id"] != case["snapshot_id"]:
            raise ValueError("real trial does not match the fixed manifest/snapshot")
        if row["prompt_hash"] != prompt_fingerprint(case["prompt"]):
            raise ValueError("real trial does not match the fixed question")
        if variant is not None and row["variant"] != variant:
            raise ValueError("real trial has a different variant")
        for field in ("input_tokens", "output_tokens", "estimated_cost", "elapsed_ms", "tool_call_count", "retry_count", "rework_count", "delivery_count"):
            if row.get(field) is None:
                raise ValueError(f"real trial has unavailable {field}")
        if row.get("cost_source") == "price_table" and any(row.get(field) is None for field in ("cached_input_tokens", "cache_write_tokens")):
            raise ValueError("table-priced trials need known cache read/write usage")
        if (row.get("cached_input_tokens", 0) or 0) + (row.get("cache_write_tokens", 0) or 0) > row["input_tokens"]:
            raise ValueError("cache usage cannot exceed total input usage")
        if row.get("status") not in {"succeeded", "failed", "canceled", "timeout"}:
            raise ValueError("real trial needs a terminal status")
        calls = row.get("model_call_ids")
        if not calls or any(call in seen_calls for call in calls):
            raise ValueError("real trials need distinct call-attempt ids, not replayed receipts")
        seen_calls.update(calls)
        for key, seen in (((row["case_id"], row["pair_id"]), seen_pairs),
                          ((row["case_id"], row["trial_id"]), seen_trials)):
            if key in seen:
                raise ValueError("real trials contain duplicate trial/pair ids")
            seen.add(key)
    return result


def evaluate_optimization_gate(
    manifest: Mapping[str, Any], before: Iterable[Mapping[str, Any]] | Path | str,
    after: Iterable[Mapping[str, Any]] | Path | str, *, policy_id: str = "context-v1",
) -> dict[str, Any]:
    """Require paired snapshots/configs, real usage, quality and stable savings.

    Every pair must preserve quality and not increase cost, retries or rework.
    All cases meet their minimum sample count. Aggregate case medians must save
    at least 1%; latency medians may not increase. Small samples report ranges,
    never a P95 or a claim of future savings. No runtime switch occurs here.
    """
    result: dict[str, Any] = {
        "eligible": False, "eligibility": "blocked", "policy_id": str(policy_id),
        "measurement_mode": "actual_model", "reasons": [], "models": [],
        "case_count": 0, "pair_count": 0,
    }
    try:
        result["manifest_hash"] = manifest_fingerprint(manifest)
        old = import_real_trials(manifest, before)
        new = import_real_trials(manifest, after)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        result["reasons"] = ["invalid_or_nonactual_trials: " + str(exc)]
        return result
    reasons: set[str] = set()
    if not policy_id or any(row["policy_id"] != policy_id for row in new):
        reasons.add("candidate_policy_mismatch")
    if {row["variant"] for row in old} == {row["variant"] for row in new} or len({row["variant"] for row in old}) != 1 or len({row["variant"] for row in new}) != 1:
        reasons.add("variants_must_be_distinct_and_fixed")
    old_by_pair = {(row["case_id"], row["pair_id"]): row for row in old}
    new_by_pair = {(row["case_id"], row["pair_id"]): row for row in new}
    if old_by_pair.keys() != new_by_pair.keys():
        reasons.add("trial_pairs_differ")
    old_calls = {call for row in old for call in row["model_call_ids"]}
    if old_calls.intersection(call for row in new for call in row["model_call_ids"]):
        reasons.add("call_attempt_reused_across_variants")
    models = {(row["provider"], row["model"]) for row in [*old, *new]}
    result["models"] = [{"provider": provider, "model": model} for provider, model in sorted(models)]
    currencies = {row["currency"] for row in [*old, *new]}
    if len(currencies) != 1:
        reasons.add("comparison_currencies_differ")
    old_costs, new_costs = [], []
    for case in manifest["cases"]:
        case_id = case["id"]
        left = [row for row in old if row["case_id"] == case_id]
        right = [row for row in new if row["case_id"] == case_id]
        if len(left) < case["min_trials"] or len(right) < case["min_trials"]:
            reasons.add("minimum_trials_missing:" + case_id)
            continue
        # Fixed configurations within each case, in addition to paired equality.
        configurations = {tuple(row[field] for field in _MATCH_FIELDS) for row in [*left, *right]}
        if len(configurations) != 1:
            reasons.add("comparison_config_changed:" + case_id)
        for row in [*left, *right]:
            if row["status"] != "succeeded" or any(row.get(field) is not True for field in ("quality_pass", "evidence_complete", "delivery_pass")):
                reasons.add("quality_not_proven:" + case_id)
            if row["delivery_count"] != 1:
                reasons.add("delivery_count_invalid:" + case_id)
            assertions = row.get("assertions", {})
            if (not row.get("evidence_refs") or any(value is not True for value in assertions.values())
                    or any(assertions.get(key) is not True for key in case["expected_evidence"])):
                reasons.add("expected_evidence_missing:" + case_id)
        for key in old_by_pair.keys() & new_by_pair.keys():
            if key[0] != case_id:
                continue
            a, b = old_by_pair[key], new_by_pair[key]
            if any(a[field] != b[field] for field in _MATCH_FIELDS):
                reasons.add("pair_config_changed:" + case_id)
            if b["estimated_cost"] > a["estimated_cost"]:
                reasons.add("cost_regression:" + case_id)
            if b["retry_count"] > a["retry_count"] or b["rework_count"] > a["rework_count"]:
                reasons.add("retry_or_rework_regression:" + case_id)
        if median(row["elapsed_ms"] for row in right) > median(row["elapsed_ms"] for row in left):
            reasons.add("latency_regression:" + case_id)
        old_costs.append(median(row["estimated_cost"] for row in left))
        new_costs.append(median(row["estimated_cost"] for row in right))
    before_cost = sum(old_costs) if len(currencies) == 1 else None
    after_cost = sum(new_costs) if len(currencies) == 1 else None
    reduction = (before_cost - after_cost) / before_cost if before_cost else None
    if reduction is None or reduction < 0.01:
        reasons.add("stable_cost_reduction_below_one_percent")
    result.update({
        "case_count": len(manifest["cases"]), "pair_count": len(old_by_pair.keys() & new_by_pair.keys()),
        "cost_reduction_fraction": reduction,
        "sum_case_median_before_cost": before_cost, "sum_case_median_after_cost": after_cost,
        "comparison_hash": _fingerprint({"policy_id": policy_id, "before": old, "after": new}),
        "reasons": sorted(reasons), "eligible": not reasons,
        "eligibility": "eligible" if not reasons else "blocked",
    })
    return result
