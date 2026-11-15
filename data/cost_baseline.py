"""Privacy-safe aggregation of fixed trials; offline contracts are not LLM evidence."""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping


_METRIC_FIELDS = (
    "elapsed_ms", "input_tokens", "output_tokens", "cached_input_tokens", "cache_write_tokens",
    "tool_call_count", "estimated_cost", "retry_count", "rework_count",
    "context_chars", "context_tokens_est", "output_tokens_est",
)
_QUALITY_FIELDS = ("quality_pass", "evidence_complete", "delivery_pass")
_REQUIRED_CATEGORIES = {
    "single_table_query", "cross_table_analysis", "knowledge_retrieval", "long_report",
    "chart", "file_read", "mcp_query", "workflow", "dynamic_team", "failure_retry",
}
TRIAL_MODES = frozenset({"offline_contract", "actual_model", "legacy_unknown"})
USAGE_SOURCES = frozenset({
    "provider_reported", "operator_verified", "unavailable", "estimated",
    "offline_adapter", "legacy_unknown",
})
TRIAL_STATUSES = frozenset({"succeeded", "failed", "canceled", "timeout", "unknown"})
TRIAL_FIELDS = frozenset({
    "case_id", "trial_id", "pair_id", "variant", "policy_id", "manifest_hash",
    "snapshot_id", "prompt_hash", "measurement_mode", "usage_source", "status",
    "provider", "model", "price_version", "currency", "cache_config", "team_config",
    "runtime_version", "tool_contract_version", "model_call_ids", "evidence_refs",
    "assertions", "failure_stage", "attribution", "cache_write_tokens",
    "usage_complete", "cost_source", "delivery_count",
    *_METRIC_FIELDS, *_QUALITY_FIELDS,
})
_FORBIDDEN = frozenset({"prompt", "answer", "tool_result", "source_data", "messages", "sql", "arguments"})


def _valid_metric(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value)) and float(value) >= 0
    except (TypeError, ValueError, OverflowError):
        return False


def _forbid_content(value: Any) -> None:
    if isinstance(value, Mapping):
        if _FORBIDDEN.intersection(str(key).lower() for key in value):
            raise ValueError("trial contains prompt/answer/source content")
        for child in value.values():
            _forbid_content(child)
    elif isinstance(value, list):
        for child in value:
            _forbid_content(child)


def validate_trial(trial: Mapping[str, Any], *, strict: bool = False) -> None:
    """Validate numeric metrics and typed provenance without inferring missing data."""
    if not isinstance(trial, Mapping):
        raise ValueError("trial must be an object")
    _forbid_content(trial)
    if strict and set(trial) - TRIAL_FIELDS:
        raise ValueError("trial contains unsupported fields: " + ", ".join(sorted(set(trial) - TRIAL_FIELDS)))
    if not str(trial.get("case_id") or "").strip() or not str(trial.get("trial_id") or "").strip():
        raise ValueError("trial needs case_id and trial_id")
    for field in (*_METRIC_FIELDS, "delivery_count"):
        if trial.get(field) is not None and not _valid_metric(trial[field]):
            raise ValueError(f"trial has invalid {field}")
        if (trial.get(field) is not None and field not in {"elapsed_ms", "estimated_cost"}
                and not float(trial[field]).is_integer()):
            raise ValueError(f"trial has non-integer {field}")
    for field in ("case_id", "trial_id", "pair_id", "variant", "policy_id", "manifest_hash", "snapshot_id",
                  "prompt_hash", "provider", "model", "price_version", "currency", "cache_config", "team_config",
                  "runtime_version", "tool_contract_version", "failure_stage", "cost_source"):
        if field in trial and (not isinstance(trial[field], str) or len(trial[field]) > 180):
            raise ValueError(f"trial has invalid opaque {field}")
    mode, source = trial.get("measurement_mode"), trial.get("usage_source")
    if (mode == "offline_contract" or source in {"estimated", "offline_adapter", "unavailable"}) and trial.get("estimated_cost") is not None:
        raise ValueError("offline/estimated/unavailable usage cannot carry actual cost")
    if source == "unavailable" and any(trial.get(field) is not None for field in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_tokens")):
        raise ValueError("unavailable usage must have nullable counters")
    for field in (*_QUALITY_FIELDS, "usage_complete"):
        if field in trial and not isinstance(trial[field], bool):
            raise ValueError(f"trial has non-boolean {field}")
    for field, choices in (
        ("measurement_mode", TRIAL_MODES), ("usage_source", USAGE_SOURCES), ("status", TRIAL_STATUSES),
    ):
        if field in trial and trial[field] not in choices:
            raise ValueError(f"trial has unsupported {field}")
    for field in ("model_call_ids", "evidence_refs"):
        if field in trial and (
            not isinstance(trial[field], list)
            or any(not isinstance(item, str) or not item or len(item) > 180 for item in trial[field])
            or len(set(trial[field])) != len(trial[field])
        ):
            raise ValueError(f"trial needs unique opaque {field}")
    if "assertions" in trial:
        assertions = trial["assertions"]
        if not isinstance(assertions, Mapping) or any(
            not isinstance(key, str) or len(key) > 100 or not isinstance(value, bool)
            for key, value in assertions.items()
        ):
            raise ValueError("assertions must be named booleans")
    if "attribution" in trial and (
        not isinstance(trial["attribution"], Mapping)
        or any(not key.endswith("_tokens_est") or not _valid_metric(value)
               for key, value in trial["attribution"].items())
    ):
        raise ValueError("attribution must contain numeric token estimates only")


def load_manifest(path: Path | str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read baseline manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("baseline manifest must be a JSON object")
    validate_manifest(payload)
    return payload


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    if not isinstance(manifest, Mapping):
        raise ValueError("baseline manifest must be an object")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or len(cases) < 12:
        raise ValueError("baseline manifest requires at least 12 fixed cases")
    case_ids: set[str] = set()
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, Mapping):
            raise ValueError(f"case {index} must be an object")
        case_id = str(case.get("id") or "").strip()
        if not case_id or case_id in case_ids:
            raise ValueError(f"case {index} needs a unique id")
        case_ids.add(case_id)
        if not str(case.get("snapshot_id") or "").strip():
            raise ValueError(f"case {case_id} needs a stable snapshot_id")
        if not str(case.get("prompt") or "").strip():
            raise ValueError(f"case {case_id} needs a fixed prompt")
        evidence = case.get("expected_evidence")
        if (not isinstance(evidence, list) or not evidence
                or any(not isinstance(item, str) or not item.strip() or len(item) > 100 for item in evidence)
                or len(set(evidence)) != len(evidence)):
            raise ValueError(f"case {case_id} needs unique expected_evidence names")
        minimum = case.get("min_trials")
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 3:
            raise ValueError(f"case {case_id} needs at least three trials")
    missing = sorted(_REQUIRED_CATEGORIES - {str(case.get("category") or "") for case in cases})
    if missing:
        raise ValueError("baseline manifest is missing required categories: " + ", ".join(missing))


def load_trials(path: Path | str) -> list[dict[str, Any]]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"unable to read baseline trials: {exc}") from exc
    trials = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            trial = json.loads(line)
            validate_trial(trial, strict=True)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid trial at line {number}: {exc}") from exc
        trials.append(trial)
    return trials


def summarize(manifest: Mapping[str, Any], trials: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Medians/ranges and coverage; missing quality values fail closed."""
    validate_manifest(manifest)
    cases = {str(item["id"]): item for item in manifest["cases"]}
    grouped: dict[str, list[Mapping[str, Any]]] = {key: [] for key in cases}
    seen = set()
    for trial in trials:
        validate_trial(trial)
        case_id, trial_id = str(trial["case_id"]), str(trial["trial_id"])
        if case_id not in grouped:
            raise ValueError(f"trial references unknown case: {case_id}")
        if (case_id, trial_id) in seen:
            raise ValueError(f"duplicate trial id for case: {case_id}")
        seen.add((case_id, trial_id))
        grouped[case_id].append(trial)

    measurement_groups: dict[str, list[Mapping[str, Any]]] = {}
    for records in grouped.values():
        for record in records:
            source = record.get("usage_source", "legacy_unknown")
            kind = "actual" if source in {"provider_reported", "operator_verified"} else str(source)
            key = str(record.get("measurement_mode", "legacy_unknown")) + ":" + kind
            measurement_groups.setdefault(key, []).append(record)
    rows = []
    for case_id, case in cases.items():
        records = grouped[case_id]
        row: dict[str, Any] = {
            "case_id": case_id, "category": str(case.get("category") or "other"),
            "trial_count": len(records), "minimum_trials": int(case["min_trials"]),
            "complete": len(records) >= int(case["min_trials"]),
        }
        for field in _METRIC_FIELDS:
            values = [float(record[field]) for record in records if record.get(field) is not None]
            row[f"measured_{field}_count"] = len(values)
            row[f"median_{field}"] = median(values) if values else None
            row[f"min_{field}"] = min(values) if values else None
            row[f"max_{field}"] = max(values) if values else None
        for field in _QUALITY_FIELDS:
            measured = [record[field] for record in records if field in record]
            row[f"{field}_measured_count"] = len(measured)
            row[f"{field}_rate"] = sum(measured) / len(records) if records else None
        for field in ("measurement_mode", "usage_source", "status"):
            row[field + "s"] = dict(Counter(str(record.get(field) or "legacy_unknown") for record in records))
        groups = {
            tuple(str(record.get(field) or "") for field in (
                "measurement_mode", "usage_source", "variant", "snapshot_id", "provider", "model", "price_version", "currency", "cache_config", "team_config",
            )) for record in records
        }
        row["comparison_group_count"] = len(groups)
        row["quality_gate_passed"] = bool(
            row["complete"] and len(groups) == 1
            and all(record.get(field) is True for record in records for field in _QUALITY_FIELDS)
            and all(record.get("status") == "succeeded" for record in records)
        )
        success_costs = [float(record["estimated_cost"]) for record in records
                         if record.get("status") == "succeeded" and record.get("estimated_cost") is not None]
        row["median_successful_task_cost"] = median(success_costs) if success_costs else None
        if len(groups) > 1 or len({(record.get("measurement_mode"), record.get("usage_source")) for record in records}) > 1:
            # A mixed bag is coverage, not a comparable numeric baseline.
            for field in _METRIC_FIELDS:
                for prefix in ("median_", "min_", "max_"):
                    row[prefix + field] = None
            row["median_successful_task_cost"] = None
        rows.append(row)
    return {
        "case_count": len(rows),
        "complete_case_count": sum(row["complete"] for row in rows),
        "quality_gate_passed_case_count": sum(row["quality_gate_passed"] for row in rows),
        "cases": rows,
        "measurement_summaries": {
            mode: summarize(manifest, records) for mode, records in sorted(measurement_groups.items())
        } if len(measurement_groups) > 1 else {},
    }


def markdown_report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# 成本与质量基线报告", "",
        "仅含脱敏指标。offline_contract 只测本地工具与受控边界契约，不能证明真实模型的质量或降本。",
        "缺失 usage/价格以 unavailable 显示；字符估算与真实用量分别记录。小样本使用中位数与范围，不计算 P95。", "",
        f"- 固定任务：{summary.get('case_count', 0)}",
        f"- 完成最低运行次数：{summary.get('complete_case_count', 0)}",
        f"- 通过契约质量门槛：{summary.get('quality_gate_passed_case_count', 0)}", "",
        "| 任务 | 模式 | 运行 | 工具次数中位数 | 本地耗时中位数/范围 ms | 输入 Token | 成本 USD | 契约门槛 |",
        "|---|---|---:|---:|---|---:|---:|---|",
    ]
    for row in summary.get("cases", []):
        def label(field: str, digits: int = 3) -> str:
            value = row.get(field)
            return "unavailable" if value is None else f"{value:.{digits}f}"
        elapsed = "/".join(label(field) for field in ("median_elapsed_ms", "min_elapsed_ms", "max_elapsed_ms"))
        lines.append(
            f"| {row['case_id']} | {','.join(row.get('measurement_modes', {})) or 'legacy_unknown'} "
            f"| {row['trial_count']}/{row['minimum_trials']} | {label('median_tool_call_count', 0)} "
            f"| {elapsed} | {label('median_input_tokens', 0)} | {label('median_estimated_cost', 8)} "
            f"| {'通过' if row.get('quality_gate_passed') else '待补齐/未通过'} |"
        )
    for mode, subset in summary.get("measurement_summaries", {}).items():
        lines.extend(["", "## " + mode, "", markdown_report(subset)])
    return "\n".join(lines) + "\n"


def evaluate_optimization_gate(manifest, before, after, *, policy_id="context-v1") -> dict[str, Any]:
    """Compatibility entry point for callers already using this aggregator."""
    from data.optimization_trials import evaluate_optimization_gate as evaluate
    return evaluate(manifest, before, after, policy_id=policy_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate de-identified fixed cost/quality trials.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--trials", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    summary = summarize(load_manifest(args.manifest), load_trials(args.trials))
    with Path(args.output).open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(markdown_report(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
