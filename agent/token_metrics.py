"""Fast prompt estimates with explicitly sourced, nullable provider usage."""
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
    project_instruction_chars: int = 0,
    memory_chars: int = 0,
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
) -> dict[str, Any]:
    """Estimate a payload; tool results are a subset of history, not additive."""
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
        "messages": normalized_messages, "tools": normalized_tools,
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
        "project_instruction_chars": max(0, int(project_instruction_chars or 0)),
        "project_instruction_tokens_est": _tokens(project_instruction_chars, chars_per_token),
        "memory_chars": max(0, int(memory_chars or 0)),
        "memory_tokens_est": _tokens(memory_chars, chars_per_token),
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
        "estimate_method": "characters_v1",
        "usage_source": "unavailable",
        "measurement_mode": "actual_model",
        "usage_available": False,
        "usage_complete": False,
        "actual_prompt_tokens": None,
        "actual_completion_tokens": None,
        "actual_total_tokens": None,
        "cached_input_tokens": None,
        "cache_miss_tokens": None,
        "cache_write_tokens": None,
        "recorded_at": time.time(),
    }


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _usage_number(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return int(number) if math.isfinite(number) and number >= 0 and number.is_integer() else None


def _first_number(obj: Any, *names: str) -> int | None:
    for name in names:
        value = _usage_number(_value(obj, name))
        if value is not None:
            return value
    return None


def _nested_number(obj: Any, parent: str, child: str) -> int | None:
    return _usage_number(_value(_value(obj, parent), child))


def _maximum_known(*values: int | None) -> int | None:
    known = [value for value in values if value is not None]
    return max(known) if known else None


def finalize_prompt_breakdown(
    breakdown: Mapping[str, Any], usage: Any,
    *, usage_source: str = "provider_reported", measurement_mode: str = "actual_model",
) -> dict[str, Any]:
    """Attach usage without converting absence into zero.

    Provider-neutral ``prompt_tokens`` includes cache reads/writes. Anthropic's
    ``input_tokens`` excludes them, so add reported cache counters on that path.
    Missing cache counters remain unknown; a provider-reported zero stays zero.
    Controlled adapters must pass ``usage_source='offline_adapter'``.
    """
    from data.cost_event_store import MEASUREMENT_MODES, USAGE_SOURCES

    if usage_source not in USAGE_SOURCES or measurement_mode not in MEASUREMENT_MODES:
        raise ValueError("unsupported usage source or measurement mode")
    if usage_source == "offline_adapter" and measurement_mode == "actual_model":
        measurement_mode = "offline_contract"
    if usage_source == "unavailable":
        usage = None
    cached_input = _maximum_known(
        _usage_number(_value(usage, "cache_read_input_tokens")),
        _usage_number(_value(usage, "prompt_cache_hit_tokens")),
        _nested_number(usage, "prompt_tokens_details", "cached_tokens"),
        _nested_number(usage, "input_tokens_details", "cached_tokens"),
    )
    cache_write = _maximum_known(
        _usage_number(_value(usage, "cache_creation_input_tokens")),
        _nested_number(usage, "prompt_tokens_details", "cache_creation_tokens"),
        _nested_number(usage, "input_tokens_details", "cache_creation_tokens"),
    )
    prompt_tokens = _first_number(usage, "prompt_tokens", "input_tokens")
    if (
        _usage_number(_value(usage, "prompt_tokens")) is None
        and prompt_tokens is not None
        and any(_value(usage, key) is not None for key in (
            "cache_read_input_tokens", "cache_creation_input_tokens",
        ))
    ):
        prompt_tokens += (cached_input or 0) + (cache_write or 0)
    completion_tokens = _first_number(usage, "completion_tokens", "output_tokens")
    available = prompt_tokens is not None and completion_tokens is not None
    total_tokens = _usage_number(_value(usage, "total_tokens"))
    if total_tokens is None and available:
        total_tokens = prompt_tokens + completion_tokens
    counters_known = any(value is not None for value in (prompt_tokens, completion_tokens, cached_input, cache_write, total_tokens))
    consistent = bool(available and total_tokens == prompt_tokens + completion_tokens
                      and (cached_input or 0) + (cache_write or 0) <= prompt_tokens)
    result = dict(breakdown)
    result.update({
        "usage_source": usage_source if counters_known else "unavailable",
        "measurement_mode": measurement_mode,
        "usage_available": available,
        "usage_complete": consistent,
        "actual_prompt_tokens": prompt_tokens,
        "actual_completion_tokens": completion_tokens,
        "actual_total_tokens": total_tokens,
        "cached_input_tokens": cached_input,
        "cache_miss_tokens": _usage_number(_value(usage, "prompt_cache_miss_tokens")),
        "cache_write_tokens": cache_write,
    })
    return result
