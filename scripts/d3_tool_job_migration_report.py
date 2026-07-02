#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the D3 ToolSpec -> JobRunner migration inventory."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.tools.job_migration import build_job_migration_inventory


def _escape(value: object) -> str:
    text = str(value).replace("\n", " ").replace("|", "\\|")
    return text


def render_markdown() -> str:
    rows = build_job_migration_inventory()
    status_order = {
        "candidate_batch_1": 0,
        "job_ready": 1,
        "candidate_batch_2": 2,
        "defer": 3,
        "keep_sync": 4,
    }
    rows = sorted(rows, key=lambda row: (status_order.get(row["status"], 9), row["tool"]))
    columns = [
        ("tool", "Tool"),
        ("category", "Category"),
        ("execution_mode", "Registry mode"),
        ("registry_threshold", "Registry threshold"),
        ("status", "D3 status"),
        ("typical_cost", "Typical cost"),
        ("threshold_plan", "Threshold plan"),
        ("cancellation_points", "Cancellation points"),
        ("result_risk", "Result risk"),
        ("next_action", "Next action"),
    ]
    lines = [
        "# D3 Tool Job Migration Inventory",
        "",
        "Generated from `agent.tools.registry.BUILTIN_TOOL_REGISTRY` and "
        "`agent.tools.job_migration.TOOL_JOB_MIGRATION_PLAN`.",
        "",
        "Status meanings:",
        "",
        "- `job_ready`: registry already marks the tool as `auto` or `job`; use it as a migration pattern or finish wiring if needed.",
        "- `candidate_batch_1`: first migration batch, focused on user-visible output generation.",
        "- `candidate_batch_2`: second migration batch, focused on heavier compute and data mutations.",
        "- `defer`: keep synchronous for now, revisit with measurements or a separate cancellation design.",
        "- `keep_sync`: intentionally bounded and should remain synchronous.",
        "",
        "| " + " | ".join(title for _key, title in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_escape(row[key]) for key, _title in columns) + " |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write",
        action="store_true",
        help="write docs/d3-tool-job-migration.md instead of printing",
    )
    args = parser.parse_args()
    markdown = render_markdown()
    if args.write:
        target = ROOT / "docs" / "d3-tool-job-migration.md"
        target.write_text(markdown, encoding="utf-8")
        print(target)
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
