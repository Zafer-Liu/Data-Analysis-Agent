# -*- coding: utf-8 -*-
"""Dynamic tool exposure for each conversation turn."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

from copy import deepcopy

from agent.activation import ActivationContext

from .registry import BUILTIN_TOOL_REGISTRY


def _tool_name(schema: dict) -> str:
    return ((schema.get("function") or {}).get("name") or "").strip()


def _normalize_discovery_text(text: str) -> str:
    return " ".join(str(text or "").lower().replace("_", " ").split())


def discover_tool_names_for_query(query: str) -> set[str]:
    """Return discoverable built-in tools mentioned by the user query."""
    haystack = _normalize_discovery_text(query)
    if not haystack:
        return set()
    discovered: set[str] = set()
    raw = str(query or "").lower()
    for spec in BUILTIN_TOOL_REGISTRY.all():
        if not spec.discoverable:
            continue
        for term in spec.discovery_terms:
            term_text = str(term or "").strip()
            if not term_text:
                continue
            normalized_term = _normalize_discovery_text(term_text)
            if normalized_term and normalized_term in haystack:
                discovered.add(spec.name)
                break
            if term_text.lower() in raw:
                discovered.add(spec.name)
                break
    return discovered


def filter_tools_for_turn(
    tools: list[dict],
    *,
    command: str = "",
    activation: ActivationContext | None = None,
    skill_allowed_tools: frozenset[str] | None = None,
    trusted_skill: str = "",
    user_message: str = "",
    discovered_tools: frozenset[str] | set[str] | None = None,
    has_data_source: bool = False,
    has_workspace: bool = False,
    teams_enabled: bool = False,
    include_mcp: bool = True,
    allowed_mcp_tools: frozenset[str] | set[str] | None = None,
) -> list[dict]:
    """Return only tools useful for the current turn.

    Output-generation tools stay hidden unless their slash command is active.
    Data tools are hidden when no data source is connected, while knowledge,
    clarification, and confirm tools can still work.
    """
    policy_command = command or ""
    if activation is not None:
        # Skills never unlock command/action-gated tools. Internal actions keep
        # the existing confirm/revise guards while using a separate namespace.
        policy_command = activation.command_name or activation.internal_action
    discovered = set(discovered_tools or ())
    if not policy_command and not trusted_skill:
        discovered |= discover_tool_names_for_query(user_message)
    allowed = BUILTIN_TOOL_REGISTRY.exposed_names(
        command=policy_command,
        has_data_source=has_data_source,
        has_workspace=has_workspace,
        skill=trusted_skill,
        discovered_tools=discovered,
    )
    if not teams_enabled:
        allowed -= {
            "team_create", "team_delete", "team_list", "team_status",
            "send_message", "agent_delegate", "team_delegate",
        }
    else:
        allowed |= {
            "team_create", "team_delete", "team_list", "team_status",
            "send_message", "agent_delegate", "team_delegate",
        }
    if skill_allowed_tools:
        # Intersection happens after normal source/workspace/command policy, so
        # a Skill can only reduce an already authorized set.
        allowed &= set(skill_allowed_tools)
    deferred_count = sum(
        1
        for spec in BUILTIN_TOOL_REGISTRY.all()
        if spec.discoverable and spec.name not in allowed
    )
    if discovered:
        log.debug(
            "[tools] discovery  discovered=%s exposed=%d deferred=%d",
            sorted(discovered), len(allowed), deferred_count,
        )
    else:
        log.debug("[tools] discovery  exposed=%d deferred=%d", len(allowed), deferred_count)

    filtered: list[dict] = []
    selected_mcp = set(allowed_mcp_tools or ())
    for schema in tools:
        name = _tool_name(schema)
        if not name:
            continue
        if name.startswith("mcp__"):
            if (
                include_mcp
                and name in selected_mcp
                and (
                    not skill_allowed_tools
                    or name in set(skill_allowed_tools)
                )
            ):
                filtered.append(schema)
            continue
        if name in allowed:
            filtered.append(schema)

    # Copy so callers can safely tweak descriptions later without mutating the
    # global AGENT_TOOLS list.
    return deepcopy(filtered)
