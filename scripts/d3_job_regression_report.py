#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the D3 JobRunner regression matrix."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.tools.job_migration import build_job_regression_matrix


def _escape(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_markdown() -> str:
    rows = build_job_regression_matrix()
    rows = sorted(rows, key=lambda row: row["tool"])
    columns = [
        ("tool", "Tool"),
        ("registry_threshold", "Threshold"),
        ("test_targets", "Regression tests"),
        ("benchmark_baseline", "Baseline / pressure target"),
        ("cancellation_behavior", "Cancellation behavior"),
        ("rollback_boundary", "Rollback boundary"),
        ("isolation_notes", "Isolation notes"),
    ]
    lines = [
        "# D3 JobRunner Regression Matrix",
        "",
        "Generated from `agent.tools.job_migration.D3_JOB_REGRESSION_MATRIX`.",
        "",
        "This matrix is the D3.5 guardrail for auto/job tools: every migrated tool "
        "must have a test target, a baseline or pressure target, cancellation "
        "expectations, an independent rollback boundary, and an isolation note.",
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
        help="write docs/d3-job-regression.md instead of printing",
    )
    args = parser.parse_args()
    markdown = render_markdown()
    if args.write:
        target = ROOT / "docs" / "d3-job-regression.md"
        target.write_text(markdown, encoding="utf-8")
        print(target)
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
