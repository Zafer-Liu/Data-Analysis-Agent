"""Runtime switches for incremental Workflow graph rollout.

The switches are intentionally environment based: an operator can turn off a
new graph capability and immediately fall back to the proven agent-only graph
without rewriting published workflow definitions.
"""
from __future__ import annotations

import os


_DEFAULTS = {
    "deterministic_nodes": False,
    "conditional_edges": False,
    "verifier_nodes": False,
}


def workflow_feature_flags() -> dict[str, bool]:
    """Return the effective Workflow rollout switches.

    ``BAA_WORKFLOW_FEATURES`` accepts comma-separated ``name=true|false``
    pairs, e.g. ``deterministic_nodes=true,verifier_nodes=true``. New graph
    capabilities are deliberately disabled by default; pure ``agent`` graphs
    remain available without configuration. Unknown
    names and malformed values are ignored so a typo cannot disable a flow.
    """
    flags = dict(_DEFAULTS)
    raw = os.getenv("BAA_WORKFLOW_FEATURES", "")
    for item in raw.split(","):
        name, separator, raw_value = item.strip().partition("=")
        if not separator or name not in flags:
            continue
        value = raw_value.strip().lower()
        if value in {"1", "true", "on", "yes"}:
            flags[name] = True
        elif value in {"0", "false", "off", "no"}:
            flags[name] = False
    return flags


def workflow_feature_enabled(name: str) -> bool:
    return bool(workflow_feature_flags().get(name, False))
