#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the D6 low-frequency tool discovery inventory."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.tools.exposure import filter_tools_for_turn
from agent.tools.registry import BUILTIN_TOOL_REGISTRY
from agent.tools.schemas import AGENT_TOOLS


def _escape(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def _names(**kwargs) -> set[str]:
    return {
        item["function"]["name"]
        for item in filter_tools_for_turn(AGENT_TOOLS, include_mcp=False, **kwargs)
    }


def build_inventory() -> list[dict[str, str]]:
    no_data = _names(has_data_source=False, has_workspace=False)
    data_only = _names(has_data_source=True, has_workspace=False)
    data_workspace = _names(has_data_source=True, has_workspace=True)
    rows: list[dict[str, str]] = []
    for spec in BUILTIN_TOOL_REGISTRY.all():
        rows.append({
            "tool": spec.name,
            "category": spec.category,
            "default_exposed": "yes" if spec.default_exposed else "no",
            "discoverable": "yes" if spec.discoverable else "no",
            "commands": ", ".join(sorted(spec.commands)) or "-",
            "skills": ", ".join(sorted(spec.skills)) or "-",
            "requires_data": "yes" if spec.requires_data_source else "no",
            "requires_workspace": "yes" if spec.requires_workspace else "no",
            "no_data_default": "yes" if spec.name in no_data else "no",
            "data_default": "yes" if spec.name in data_only else "no",
            "workspace_default": "yes" if spec.name in data_workspace else "no",
            "keywords": ", ".join(sorted(spec.discovery_keywords)) or "-",
            "summary": spec.discovery_summary or "-",
        })
    return rows


def render_markdown() -> str:
    rows = build_inventory()
    columns = [
        ("tool", "Tool"),
        ("category", "Category"),
        ("default_exposed", "Default"),
        ("discoverable", "Discoverable"),
        ("commands", "Commands"),
        ("skills", "Skills"),
        ("requires_data", "Data"),
        ("requires_workspace", "Workspace"),
        ("no_data_default", "No-data"),
        ("data_default", "Data default"),
        ("workspace_default", "Workspace default"),
        ("keywords", "Keywords"),
        ("summary", "Summary"),
    ]
    no_data_count = sum(1 for row in rows if row["no_data_default"] == "yes")
    data_count = sum(1 for row in rows if row["data_default"] == "yes")
    workspace_count = sum(1 for row in rows if row["workspace_default"] == "yes")
    discoverable_count = sum(1 for row in rows if row["discoverable"] == "yes")
    lines = [
        "# D6 Tool Discovery Inventory",
        "",
        "Generated from `agent.tools.registry.BUILTIN_TOOL_REGISTRY` and "
        "`agent.tools.exposure.filter_tools_for_turn`.",
        "",
        f"- No-data default exposed tools: {no_data_count}",
        f"- Data default exposed tools: {data_count}",
        f"- Workspace default exposed tools: {workspace_count}",
        f"- Discoverable low-frequency tools: {discoverable_count}",
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
        help="write docs/d6-tool-discovery.md instead of printing",
    )
    args = parser.parse_args()
    markdown = render_markdown()
    if args.write:
        target = ROOT / "docs" / "d6-tool-discovery.md"
        target.write_text(markdown, encoding="utf-8")
        print(target)
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
