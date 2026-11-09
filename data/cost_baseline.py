"""Repeatable, privacy-safe reporting for cost-and-quality baseline trials.

The runner intentionally does not invoke an LLM or read workspace data.  It
aggregates operator-supplied, de-identified trial metrics so a before/after
comparison cannot accidentally change the task input while measuring cost.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping


_METRIC_FIELDS = (
    "elapsed_ms",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "tool_call_count",
    "estimated_cost",
)
_QUALITY_FIELDS = ("quality_pass", "evidence_complete", "delivery_pass")
_REQUIRED_CATEGORIES = {
    "single_table_query",
    "cross_table_analysis",
    "knowledge_retrieval",
    "long_report",
    "chart",
    "file_read",
    "mcp_query",
    "workflow",
    "dynamic_team",
    "failure_retry",
}


def _number(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _valid_metric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value)) and float(value) >= 0
    except (TypeError, ValueError):
        return False


def _median(values: Iterable[float]) -> float | None:
    values = list(values)
    return median(values) if values else None


def load_manifest(path: Path | str) -> dict[str, Any]:
    """Load and validate a fixed evaluation manifest without its source data."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read baseline manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("baseline manifest must be a JSON object")
    validate_manifest(payload)
    return payload


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    """Require a stable 12-case / three-trial minimum before reporting."""
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
        if not isinstance(case.get("expected_evidence"), list) or not case["expected_evidence"]:
            raise ValueError(f"case {case_id} needs expected_evidence")
        if int(case.get("min_trials") or 0) < 3:
            raise ValueError(f"case {case_id} needs at least three trials")
    missing_categories = sorted(_REQUIRED_CATEGORIES - {
        str(case.get("category") or "") for case in cases
    })
    if missing_categories:
        raise ValueError(
            "baseline manifest is missing required categories: "
            + ", ".join(missing_categories)
        )


def load_trials(path: Path | str) -> list[dict[str, Any]]:
    """Read JSONL trial records; prompts, answers, and source data are forbidden."""
    trials: list[dict[str, Any]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"unable to read baseline trials: {exc}") from exc
    forbidden = {"prompt", "answer", "tool_result", "source_data", "messages"}
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            trial = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {line_number}: {exc.msg}") from exc
        if not isinstance(trial, dict):
            raise ValueError(f"trial {line_number} must be an object")
        leaked = forbidden.intersection(trial)
        if leaked:
            raise ValueError(f"trial {line_number} contains forbidden fields: {', '.join(sorted(leaked))}")
        if not str(trial.get("case_id") or "").strip() or not str(trial.get("trial_id") or "").strip():
            raise ValueError(f"trial {line_number} needs case_id and trial_id")
        for field in _METRIC_FIELDS:
            if field in trial and trial[field] is not None and not _valid_metric(trial[field]):
                raise ValueError(f"trial {line_number} has invalid {field}")
        for field in _QUALITY_FIELDS:
            if field in trial and not isinstance(trial[field], bool):
                raise ValueError(f"trial {line_number} has non-boolean {field}")
        trials.append(trial)
    return trials


def summarize(manifest: Mapping[str, Any], trials: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Produce per-case medians and quality gating without returning task text."""
    validate_manifest(manifest)
    cases = {str(item["id"]): item for item in manifest["cases"]}
    grouped: dict[str, list[Mapping[str, Any]]] = {case_id: [] for case_id in cases}
    seen_trial_ids: set[tuple[str, str]] = set()
    for trial in trials:
        case_id = str(trial.get("case_id") or "").strip()
        trial_id = str(trial.get("trial_id") or "").strip()
        if case_id not in grouped:
            raise ValueError(f"trial references unknown case: {case_id}")
        key = (case_id, trial_id)
        if not trial_id or key in seen_trial_ids:
            raise ValueError(f"duplicate or missing trial id for case: {case_id}")
        seen_trial_ids.add(key)
        grouped[case_id].append(trial)

    rows: list[dict[str, Any]] = []
    for case_id, case in cases.items():
        records = grouped[case_id]
        row: dict[str, Any] = {
            "case_id": case_id,
            "category": str(case.get("category") or "other"),
            "trial_count": len(records),
            "minimum_trials": int(case["min_trials"]),
            "complete": len(records) >= int(case["min_trials"]),
        }
        for field in _METRIC_FIELDS:
            values = [_number(record.get(field)) for record in records if record.get(field) is not None]
            row[f"median_{field}"] = _median(values)
            row[f"min_{field}"] = min(values) if values else None
            row[f"max_{field}"] = max(values) if values else None
        for field in _QUALITY_FIELDS:
            measured = [bool(record[field]) for record in records if field in record]
            row[f"{field}_rate"] = sum(measured) / len(measured) if measured else None
        row["quality_gate_passed"] = bool(
            row["complete"]
            and all(row[f"{field}_rate"] == 1.0 for field in _QUALITY_FIELDS)
        )
        rows.append(row)
    return {
        "case_count": len(rows),
        "complete_case_count": sum(1 for row in rows if row["complete"]),
        "quality_gate_passed_case_count": sum(1 for row in rows if row["quality_gate_passed"]),
        "cases": rows,
    }


def markdown_report(summary: Mapping[str, Any]) -> str:
    """Render a compact report suitable for a change-review attachment."""
    lines = [
        "# 成本与质量基线报告",
        "",
        "此报告仅聚合脱敏试运行指标；不包含问题原文、回答、工具结果或源数据。",
        "",
        f"- 固定任务：{summary.get('case_count', 0)}",
        f"- 已完成至少三次试运行：{summary.get('complete_case_count', 0)}",
        f"- 通过全部质量门槛：{summary.get('quality_gate_passed_case_count', 0)}",
        "",
        "| 任务 | 类别 | 试运行 | 中位输入 Token | 中位成本 (USD) | 质量门槛 |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in summary.get("cases", []):
        tokens = row.get("median_input_tokens")
        cost = row.get("median_estimated_cost")
        token_label = "-" if tokens is None else f"{tokens:,.0f}"
        cost_label = "未配置" if cost is None else f"${cost:.6f}"
        quality = "通过" if row.get("quality_gate_passed") else "待补齐/未通过"
        lines.append(
            f"| {row['case_id']} | {row['category']} | {row['trial_count']}/{row['minimum_trials']} "
            f"| {token_label} | {cost_label} | {quality} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate a fixed cost/quality baseline from JSONL trials.")
    parser.add_argument("--manifest", required=True, help="JSON fixed-case manifest")
    parser.add_argument("--trials", required=True, help="De-identified JSONL trial metrics")
    parser.add_argument("--output", required=True, help="Markdown report path")
    args = parser.parse_args()
    summary = summarize(load_manifest(args.manifest), load_trials(args.trials))
    Path(args.output).write_text(markdown_report(summary), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
