"""Per-call Token observability helpers.

The estimates are intentionally fast and provider-neutral. Actual usage remains
authoritative; the breakdown exists to show which payload component is growing.
"""
from __future__ import annotations

import json
import math
import time
from typing import Any, Mapping, Sequence


DEFAULT_CHARS_PER_TOKEN = 3.5


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _tokens(chars: int, chars_per_token: float) -> int:
    return int(math.ceil(max(0, chars) / max(0.1, chars_per_token)))


def _tool_name(schema: Mapping[str, Any]) -> str:
    function = schema.get("function")
    if isinstance(function, Mapping):
        return str(function.get("name") or "")
    return str(schema.get("name") or "")


def build_prompt_breakdown(
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    *,
    current_user_message: str = "",
    model: str = "",
    provider: str = "",
    iteration: int = 0,
    activation_kind: str = "",
    activation_name: str = "",
    workflow_stage: str = "",
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
) -> dict[str, Any]:
    """Estimate the actual payload immediately before one model call.

    ``history_chars`` includes tool-result messages. ``tool_result_chars`` is a
    diagnostic subset, so callers must not add it to the component total.
    """
    normalized_messages = [dict(message) for message in messages]
    normalized_tools = [dict(tool) for tool in tools]
    current_index = None
    for index in range(len(normalized_messages) - 1, -1, -1):
        message = normalized_messages[index]
        if (
            message.get("role") == "user"
            and str(message.get("content") or "") == str(current_user_message or "")
        ):
            current_index = index
            break

    system_messages = [
        message for message in normalized_messages if message.get("role") == "system"
    ]
    current_messages = (
        [normalized_messages[current_index]] if current_index is not None else []
    )
    history_messages = [
        message
        for index, message in enumerate(normalized_messages)
        if message.get("role") != "system" and index != current_index
    ]
    tool_results = [
        message for message in normalized_messages if message.get("role") == "tool"
    ]

    system_chars = len(_json_text(system_messages))
    tool_schema_chars = len(_json_text(normalized_tools))
    history_chars = len(_json_text(history_messages))
    current_user_chars = len(_json_text(current_messages))
    tool_result_chars = sum(
        len(_json_text(message.get("content") or "")) for message in tool_results
    )
    payload_chars = len(_json_text({
        "messages": normalized_messages,
        "tools": normalized_tools,
    }))
    tool_names = [_tool_name(tool) for tool in normalized_tools]
    mcp_tool_count = sum(name.startswith("mcp__") for name in tool_names)

    return {
        "model": str(model or ""),
        "provider": str(provider or ""),
        "iteration": max(0, int(iteration or 0)),
        "activation_kind": str(activation_kind or ""),
        "activation_name": str(activation_name or "")[:80],
        "workflow_stage": str(workflow_stage or ""),
        "system_chars": system_chars,
        "system_tokens_est": _tokens(system_chars, chars_per_token),
        "tool_schema_chars": tool_schema_chars,
        "tool_schema_tokens_est": _tokens(tool_schema_chars, chars_per_token),
        "history_chars": history_chars,
        "history_tokens_est": _tokens(history_chars, chars_per_token),
        "current_user_chars": current_user_chars,
        "current_user_tokens_est": _tokens(current_user_chars, chars_per_token),
        "tool_result_chars": tool_result_chars,
        "tool_result_tokens_est": _tokens(tool_result_chars, chars_per_token),
        "payload_chars": payload_chars,
        "payload_tokens_est": _tokens(payload_chars, chars_per_token),
        "builtin_tool_count": max(0, len(tool_names) - mcp_tool_count),
        "mcp_tool_count": mcp_tool_count,
        "actual_prompt_tokens": None,
        "actual_completion_tokens": None,
        "cached_input_tokens": 0,
        "cache_miss_tokens": 0,
        "cache_write_tokens": 0,
        "recorded_at": time.time(),
    }


def _value(obj: Any, name: str, default: Any = 0) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _nested_value(obj: Any, parent: str, child: str) -> int:
    nested = _value(obj, parent, None)
    return int(_value(nested, child, 0) or 0) if nested is not None else 0


def finalize_prompt_breakdown(
    breakdown: Mapping[str, Any],
    usage: Any,
) -> dict[str, Any]:
    """Attach normalized provider usage and Prompt Cache counters."""
    prompt_tokens = int(
        _value(usage, "prompt_tokens", _value(usage, "input_tokens", 0)) or 0
    )
    completion_tokens = int(
        _value(usage, "completion_tokens", _value(usage, "output_tokens", 0)) or 0
    )
    cached_input = max(
        int(_value(usage, "cache_read_input_tokens", 0) or 0),
        int(_value(usage, "prompt_cache_hit_tokens", 0) or 0),
        _nested_value(usage, "prompt_tokens_details", "cached_tokens"),
        _nested_value(usage, "input_tokens_details", "cached_tokens"),
    )
    cache_miss = int(_value(usage, "prompt_cache_miss_tokens", 0) or 0)
    cache_write = max(
        int(_value(usage, "cache_creation_input_tokens", 0) or 0),
        _nested_value(usage, "prompt_tokens_details", "cache_creation_tokens"),
        _nested_value(usage, "input_tokens_details", "cache_creation_tokens"),
    )
    result = dict(breakdown)
    result.update({
        "actual_prompt_tokens": prompt_tokens,
        "actual_completion_tokens": completion_tokens,
        "actual_total_tokens": int(
            _value(usage, "total_tokens", prompt_tokens + completion_tokens)
            or prompt_tokens + completion_tokens
        ),
        "cached_input_tokens": cached_input,
        "cache_miss_tokens": cache_miss,
        "cache_write_tokens": cache_write,
    })
    return result
