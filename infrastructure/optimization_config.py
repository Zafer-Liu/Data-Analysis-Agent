"""Read optional operator-owned role policies; absence preserves model inheritance."""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping

log = logging.getLogger(__name__)


def load_optimization_config(environ: Mapping[str, str] | None = None) -> dict:
    """Only deployment configuration supplies approval, never tool arguments.

    The resolver still verifies policy version, actual-trial gate and candidate
    model/provider. These fields do not bypass that independent validation.
    """
    environ = os.environ if environ is None else environ
    result = {}
    for key, variable in (("tier_mapping", "BAA_APPROVED_ROLE_MODEL_POLICY"),
                          ("gate", "BAA_MODEL_ROUTING_GATE")):
        value = environ.get(variable, "")
        if not value:
            continue
        try:
            if not isinstance(value, str) or len(value) > 65_536:
                raise ValueError("configuration exceeds the supported size")
            parsed = json.loads(value)
            if not isinstance(parsed, dict):
                raise ValueError("configuration must be an object")
            result[key] = parsed
        except (ValueError, TypeError):
            log.warning("[optimization] invalid %s; retaining inherited model", variable)
    return result
