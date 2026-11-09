"""Versioned, bounded execution presets for Dynamic Team tasks.

The preset is intentionally a task-level policy snapshot rather than an
implicit model switch.  Interactive sessions retain the model selected by the
user; ``model_tier`` is an auditable routing recommendation until a workspace
administrator maps tiers to approved provider models.
"""
from __future__ import annotations

from copy import deepcopy

from agent.tools.workspace.teams import WorkspaceTeamError


TEAM_PROFILE_PRESETS = {
    "data-scout": {
        "key": "data-scout",
        "version": 1,
        "name": "数据勘查",
        "model_policy": "inherit",
        "model_tier": "low_cost",
        "allowed_tools": ("get_schema", "profile_data"),
        "max_output_tokens": 800,
        "max_tool_calls": 12,
    },
    "sql-analyst": {
        "key": "sql-analyst",
        "version": 1,
        "name": "SQL 分析",
        "model_policy": "inherit",
        "model_tier": "high_reliability",
        "allowed_tools": ("get_schema", "profile_data", "query_data"),
        "max_output_tokens": 1_600,
        "max_tool_calls": 24,
    },
    "knowledge-researcher": {
        "key": "knowledge-researcher",
        "version": 1,
        "name": "知识检索",
        "model_policy": "inherit",
        "model_tier": "balanced",
        "allowed_tools": ("query_knowledge", "workspace_read_file"),
        "max_output_tokens": 1_400,
        "max_tool_calls": 16,
    },
    "quality-reviewer": {
        "key": "quality-reviewer",
        "version": 1,
        "name": "质量复核",
        "model_policy": "inherit",
        "model_tier": "high_reliability",
        "allowed_tools": ("get_schema", "profile_data", "query_data"),
        "max_output_tokens": 1_600,
        "max_tool_calls": 24,
    },
    "report-writer": {
        "key": "report-writer",
        "version": 1,
        "name": "报告整理",
        "model_policy": "inherit",
        "model_tier": "low_cost",
        "allowed_tools": (),
        "max_output_tokens": 2_000,
        "max_tool_calls": 0,
    },
}


def normalize_team_profile(value) -> dict:
    """Return a serializable preset snapshot, or an explicit inherit policy."""
    key = str(value or "").strip().lower()
    if not key:
        return {
            "key": "inherit",
            "version": 1,
            "name": "继承团队默认策略",
            "model_policy": "inherit",
            "model_tier": "inherit",
            "allowed_tools": None,
            "max_output_tokens": None,
            "max_tool_calls": None,
        }
    preset = TEAM_PROFILE_PRESETS.get(key)
    if preset is None:
        raise WorkspaceTeamError(
            "unknown dynamic team profile: " + key
        )
    snapshot = deepcopy(preset)
    snapshot["allowed_tools"] = list(snapshot["allowed_tools"])
    return snapshot
