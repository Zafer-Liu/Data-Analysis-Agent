"""Small, safe condition evaluator for hooks."""

from __future__ import annotations

import re
import shlex
from typing import Any

from .models import HookContext


_OPS = {"==", "!=", "contains", "not_contains", "startswith", "endswith", "exists"}


def evaluate_condition(expression: str, ctx: HookContext) -> bool:
    expr = str(expression or "").strip()
    if not expr:
        return True
    or_parts = re.split(r"\s+\|\|\s+", expr)
    return any(_eval_and(part, ctx) for part in or_parts if part.strip())


def _eval_and(expression: str, ctx: HookContext) -> bool:
    parts = re.split(r"\s+&&\s+", expression)
    return all(_eval_clause(part.strip(), ctx) for part in parts if part.strip())


def _eval_clause(clause: str, ctx: HookContext) -> bool:
    try:
        tokens = shlex.split(clause)
    except ValueError:
        return False
    if not tokens:
        return True
    if len(tokens) == 1:
        return _truthy(ctx.get_field(tokens[0]))
    field = tokens[0]
    op = tokens[1]
    if op not in _OPS:
        return False
    left = ctx.get_field(field)
    if op == "exists":
        return _truthy(left)
    right = " ".join(tokens[2:]) if len(tokens) > 2 else ""
    return _compare(left, op, right)


def _compare(left: Any, op: str, right: str) -> bool:
    if isinstance(left, (dict, list)):
        left_text = str(left)
    elif left is None:
        left_text = ""
    else:
        left_text = str(left)
    if op == "==":
        return left_text == right
    if op == "!=":
        return left_text != right
    left_low = left_text.lower()
    right_low = str(right).lower()
    if op == "contains":
        return right_low in left_low
    if op == "not_contains":
        return right_low not in left_low
    if op == "startswith":
        return left_low.startswith(right_low)
    if op == "endswith":
        return left_low.endswith(right_low)
    return False


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return str(value).strip().lower() not in {"", "0", "false", "no", "none", "null"}
