#!/usr/bin/env python3
"""Validate public tool contracts without generating or reading private docs."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from agent.tools.job_migration import validate_job_migration_plan, validate_job_regression_matrix
    from agent.tools.registry import BUILTIN_TOOL_REGISTRY
    from agent.tools.schemas import AGENT_TOOLS

    BUILTIN_TOOL_REGISTRY.validate_schema_names(AGENT_TOOLS)
    validate_job_migration_plan()
    validate_job_regression_matrix()
    print("Public tool contracts are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
