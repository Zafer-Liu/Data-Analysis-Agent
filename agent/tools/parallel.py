# -*- coding: utf-8 -*-
"""Conservative parallelization policy for tool batches."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

from .registry import get_tool_spec


def is_parallel_safe_tool(name: str) -> bool:
    """Return whether a tool can run in a background worker safely.

    We intentionally do not include query_data yet: current DataSource
    implementations share local DB connections, and not all of them guarantee
    thread-safe reads. This can be relaxed once a source advertises that
    capability.
    """
    # MCP tools are external and may mutate remote state. Tool names alone do
    # not carry trustworthy read-only/idempotency metadata, so they stay
    # sequential until the connection layer supplies an audited capability.
    if name.startswith("mcp__"):
        return False
    spec = get_tool_spec(name)
    return bool(spec and spec.concurrency_safe)


def should_parallelize_batch(parsed_tools: list[tuple]) -> bool:
    return len(parsed_tools) > 1 and all(
        is_parallel_safe_tool(name) for _, name, _ in parsed_tools
    )
