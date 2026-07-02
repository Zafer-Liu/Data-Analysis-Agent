# -*- coding: utf-8 -*-
"""
Conversation history compaction — LLM-based semantic summarization.

Inspired by Claude Code's compact.ts / prompt.ts:
  - When the payload approaches a fixed-reserve threshold, summarize the
    oldest portion of history via a lightweight LLM call while keeping a
    token-budgeted recent tail verbatim.
  - The summary is injected as a single system message so the agent retains
    full semantic context without bloating the prompt.
  - Images and large tool results are stripped before summarization to keep
    the compaction request itself small.

Trigger口径与前端上下文条一致:
  前端显示 prompt_tokens / context_window；compaction 使用上一轮真实
  prompt_tokens 与当前 Payload 估算，并为输出和单轮增长保留固定空间。

Usage (in agent.run):
    if should_compact_history(history, last_prompt_tokens, ctx_window):
        yield {"type": "tool_start", "tool": "compaction", ...}
        history, ok = compact_history(history, client, model, summary_model=...)
        yield {"type": "tool_end", "tool": "compaction"}
"""
import json
import hashlib
import logging
import time
from typing import List, Dict, Any, Tuple, Optional, Callable

log = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

# Tool result messages longer than this are truncated during rule-based trim.
_TRIM_TOOL_RESULT_CAP = 2000   # chars

# Maximum aggregate model-visible tool-result content kept in one payload
# preparation pass. Recoverable artifact-backed results are shortened first.
_TOOL_RESULT_AGGREGATE_CAP = 16_000

# Keep this many recent turns verbatim (never summarized).
# Increased so more recent analysis context survives intact.
_KEEP_RECENT_TURNS = 8   # legacy fallback for callers that do not use token tails
_KEEP_RECENT_MIN_MESSAGES = 5
_KEEP_RECENT_TOKEN_BUDGET = 10_000

# Hard cap on chars fed to the summarizer.
# Reduced so the compaction prompt focuses on conclusions, not raw data dumps.
_MAX_SUMMARY_INPUT_CHARS = 24_000   # was 40_000

# Max tokens the summary itself may use.
# Increased to allow richer retention of key numbers and findings.
_SUMMARY_MAX_TOKENS = 2500   # was 1200

# Minimum history messages before compaction is worthwhile.
_MIN_TURNS_FOR_COMPACT = 4

_COMPACTION_FAILURE_LIMIT = 3
_DEFAULT_OUTPUT_RESERVE = 384_000
_DEFAULT_TURN_SAFETY_MARGIN = 8_000

# Marker used to tag the injected summary message so downstream pruning logic
# can recognise and protect it.
COMPACTION_SUMMARY_MARKER = "[CONVERSATION SUMMARY — earlier context compressed]"

# ── Summarization prompt (adapted from Claude Code prompt.ts) ─────────────────

_COMPACT_SYSTEM = (
    "You are a helpful assistant. Your only task is to produce a concise structured "
    "summary of a conversation. Output ONLY the summary — no preamble, no commentary."
)

_COMPACT_PROMPT_TEMPLATE = """\
Below is a segment of a business-analytics conversation that needs to be summarized.
The user is working with an AI data-analytics agent (queries data, generates charts, produces reports).

CRITICAL INSTRUCTIONS:
- PRESERVE ALL SPECIFIC NUMBERS: revenue figures, percentages, counts, rankings, dates, thresholds.
  A summary that says "sales were high" is USELESS. Write "sales were ¥1,234,567 (+12.3% YoY)".
- PRESERVE COLUMN NAMES AND TABLE NAMES exactly as used, so future queries can reference them.
- PRESERVE explicit user constraints, corrections, prohibitions, and output preferences as close
  to the user's original wording as possible.
- Never turn an unverified claim into a verified fact. Keep evidence boundaries explicit.
- Be thorough in sections 3 and 4 — these are the most important for continuity.

<conversation_to_summarize>
{conversation_text}
</conversation_to_summarize>

Write a structured summary using the exact headings below.
Omit a section only if there is truly nothing to report.

## 1. User Goals
What the user explicitly asked for. Be specific — include metric names, dimensions, time ranges.

## 2. Data & Schema
Data sources connected. Table names, key column names, row counts, date ranges.
List the exact column names that were queried or mentioned.

## 3. Key Query Results  ← MOST IMPORTANT: preserve all specific numbers
For each query that was run:
- What was asked (intent)
- The SQL or tool used (brief)
- The actual result: top values, totals, breakdowns, rankings — with EXACT numbers
Example: "Top 3 cities by revenue: BJ ¥2.1M, SH ¥1.8M, GZ ¥1.2M"

## 4. Analysis & Charts
Analyses executed (type, target column, key finding with numbers).
Charts generated (type, x/y axes, key insight).

## 5. Conclusions & Insights
Business conclusions the user or agent drew from the data.
Include any comparisons, anomalies, or recommendations made.

## 6. Outputs Produced
Reports / PPT / Excel / dashboards created. Include filenames.

## 7. Errors & Fixes
Errors encountered and how they were resolved.

## 8. Pending / Next Steps
Tasks requested but NOT yet completed. What should happen next.

## 9. Current State
Exactly where the conversation left off — last action taken, what the user asked most recently.

## 10. User Constraints & Corrections
Explicit constraints, corrections, prohibited approaches, metric-definition changes, and output
preferences. Preserve short important user statements verbatim when possible.
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_heavy_content(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of msg with images stripped and large content truncated.

    Tool results are aggressively truncated — the summarizer only needs to
    know *what* was queried and *key values*, not the full raw data table.
    """
    # Tight cap for tool results: keep enough to show key numbers/structure,
    # but drop the bulk of raw data rows that add noise to the summary.
    _TOOL_RESULT_CAP = 600   # chars — ~170 tokens per tool result
    _TEXT_CAP        = 2000  # chars for regular assistant/user messages

    content = msg.get("content")
    if content is None:
        return msg

    # role=tool: always apply the tight cap
    if msg.get("role") == "tool":
        if isinstance(content, str) and len(content) > _TOOL_RESULT_CAP:
            from agent.tools.results import truncate_tool_result_preserving_refs
            content = truncate_tool_result_preserving_refs(content, _TOOL_RESULT_CAP)
        return {**msg, "content": content}

    # String content (assistant / user text)
    if isinstance(content, str):
        if len(content) > _TEXT_CAP:
            content = content[:_TEXT_CAP] + "\n…[truncated]"
        return {**msg, "content": content}

    # List content (multimodal)
    if isinstance(content, list):
        cleaned = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "image":
                    cleaned.append({"type": "text", "text": "[image removed]"})
                elif block.get("type") == "text":
                    text = block.get("text", "")
                    if len(text) > _TEXT_CAP:
                        text = text[:_TEXT_CAP] + "\n…[truncated]"
                    cleaned.append({**block, "text": text})
                else:
                    cleaned.append(block)
            else:
                cleaned.append(block)
        return {**msg, "content": cleaned}

    return msg


def _messages_to_text(messages: List[Dict]) -> str:
    """Render messages to a readable text block for the summarizer."""
    parts = []
    for m in messages:
        role = m.get("role", "unknown")
        content = m.get("content") or ""

        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif isinstance(block, dict) and block.get("type") == "tool_result":
                    text_parts.append(f"[tool_result: {str(block)[:200]}]")
            content = " ".join(text_parts)

        if role == "assistant" and m.get("tool_calls"):
            tool_names = [tc.get("function", {}).get("name", "?")
                          for tc in m.get("tool_calls", [])]
            content = f"{content} [calls: {', '.join(tool_names)}]".strip()

        if role == "tool":
            content = f"[tool_result] {str(content)[:500]}"

        if isinstance(content, str) and content.strip():
            parts.append(f"[{role.upper()}]: {content.strip()}")

    return "\n\n".join(parts)


def _bounded_summary_text(
    messages: List[Dict[str, Any]],
    max_chars: int = _MAX_SUMMARY_INPUT_CHARS,
) -> str:
    """Build summary input without blindly chopping off the newest head data."""
    rendered = [_messages_to_text([message]) for message in messages]
    selected: set[int] = set()
    used = 0

    # User intent and corrections have the highest retention priority.
    for index, message in enumerate(messages):
        text = rendered[index]
        if message.get("role") != "user" or not text:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = text[:remaining]
            rendered[index] = text
        selected.add(index)
        used += len(text) + 2

    # Fill the remaining budget from newest to oldest, then restore chronology.
    for index in range(len(messages) - 1, -1, -1):
        if index in selected or not rendered[index]:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        text = rendered[index]
        if len(text) > remaining:
            text = text[-remaining:]
            rendered[index] = "[earlier part omitted] " + text
        selected.add(index)
        used += len(rendered[index]) + 2

    result = "\n\n".join(rendered[index] for index in sorted(selected))
    return result[:max_chars]


def _safe_tail_start(history: List[Dict], desired_keep: int) -> int:
    """Return the index where the verbatim tail should start.

    Adjusts `len(history) - desired_keep` so the tail never begins with an
    orphan `role: tool` message — OpenAI requires every tool message to
    immediately follow the assistant message that contains its tool_calls.
    Walks the cut point earlier until it lands on a non-tool message.
    """
    if desired_keep <= 0:
        return len(history)
    idx = max(0, len(history) - desired_keep)
    # If the tail would start with a tool message, move the cut earlier so the
    # preceding assistant(tool_calls) message is kept together with it.
    while idx > 0 and history[idx].get("role") == "tool":
        idx -= 1
    return idx


def _message_chars(message: Dict[str, Any]) -> int:
    try:
        return len(json.dumps(message, ensure_ascii=False, default=str))
    except Exception:
        return len(str(message))


def _atomic_message_groups(history: List[Dict]) -> List[Tuple[int, int]]:
    """Return tool-call-safe [start, end) message groups.

    An assistant tool-call message and all immediately following tool results
    are kept together. Other messages form one-message groups.
    """
    groups: List[Tuple[int, int]] = []
    i = 0
    while i < len(history):
        start = i
        message = history[i]
        i += 1
        if message.get("role") == "assistant" and message.get("tool_calls"):
            while i < len(history) and history[i].get("role") == "tool":
                i += 1
        groups.append((start, i))
    return groups


def safe_tail_start_by_tokens(
    history: List[Dict],
    *,
    token_budget: int = _KEEP_RECENT_TOKEN_BUDGET,
    min_messages: int = _KEEP_RECENT_MIN_MESSAGES,
    chars_per_token: float = 3.5,
) -> int:
    """Choose a recent verbatim tail by token budget without splitting tools."""
    if not history:
        return 0
    groups = _atomic_message_groups(history)
    kept_chars = 0
    kept_messages = 0
    start = len(history)
    char_budget = max(1, int(token_budget * chars_per_token))
    for group_start, group_end in reversed(groups):
        group_chars = sum(_message_chars(item) for item in history[group_start:group_end])
        group_messages = group_end - group_start
        if kept_messages >= min_messages and kept_chars + group_chars > char_budget:
            break
        start = group_start
        kept_chars += group_chars
        kept_messages += group_messages
    return start


def compaction_threshold(
    context_window: int,
    *,
    output_reserve: Optional[int] = None,
    safety_margin: Optional[int] = None,
) -> int:
    """Return a fixed-reserve auto-compaction threshold.

    Reserves are capped for small windows so a useful input budget remains.
    """
    if not context_window or context_window <= 0:
        return 0
    output = (
        _DEFAULT_OUTPUT_RESERVE if output_reserve is None else max(0, int(output_reserve))
    )
    safety = (
        _DEFAULT_TURN_SAFETY_MARGIN
        if safety_margin is None else max(0, int(safety_margin))
    )
    output = min(output, max(1000, int(context_window * 0.45)))
    safety = min(safety, max(1000, int(context_window * 0.15)))
    # Always leave at least 35% of the window usable for input.
    return max(1, context_window - min(output + safety, int(context_window * 0.65)))


def compaction_circuit_open(state: Optional[Dict[str, Any]]) -> bool:
    return bool((state or {}).get("circuit_open"))


def record_compaction_result(
    state: Optional[Dict[str, Any]],
    *,
    success: bool,
    error_type: str = "",
) -> None:
    """Update a mutable session-owned compaction circuit state."""
    if state is None:
        return
    state["last_attempt_at"] = time.time()
    if success:
        state["consecutive_failures"] = 0
        state["last_failure_type"] = ""
        state["circuit_open"] = False
        state["last_success_at"] = state["last_attempt_at"]
        return
    failures = int(state.get("consecutive_failures") or 0) + 1
    state["consecutive_failures"] = failures
    state["last_failure_type"] = str(error_type or "compaction_failed")
    if failures >= _COMPACTION_FAILURE_LIMIT:
        state["circuit_open"] = True


def apply_tool_result_budget(
    messages: List[Dict[str, Any]],
    *,
    per_result_cap: int = _TRIM_TOOL_RESULT_CAP,
    aggregate_cap: int = _TOOL_RESULT_AGGREGATE_CAP,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Apply deterministic per-result and aggregate tool-result budgets.

    Artifact-backed results are safe to shorten because their stable URI remains
    in the preview. Non-recoverable results are only shortened when the whole
    payload still exceeds the aggregate cap.
    """
    from agent.tools.results import (
        extract_tool_result_references,
        truncate_tool_result_to_total_cap,
    )

    bounded = [dict(message) for message in messages]
    tool_indexes = [
        index for index, message in enumerate(bounded)
        if message.get("role") == "tool" and isinstance(message.get("content"), str)
    ]
    trimmed = 0
    original_chars = sum(len(str(bounded[index].get("content") or "")) for index in tool_indexes)
    fair_share_cap = max(
        240,
        min(per_result_cap, aggregate_cap // max(1, len(tool_indexes))),
    )

    # First shorten recoverable large results. The truncation helper preserves
    # artifact URIs, and applying it repeatedly is idempotent.
    for index in tool_indexes:
        content = str(bounded[index].get("content") or "")
        if (
            len(content) > fair_share_cap
            and "[result truncated for payload," not in content
            and extract_tool_result_references(content)
        ):
            bounded[index]["content"] = truncate_tool_result_to_total_cap(
                content, fair_share_cap,
            )
            trimmed += 1

    def current_total() -> int:
        return sum(len(str(bounded[index].get("content") or "")) for index in tool_indexes)

    # If aggregate content is still too large, trim the largest messages first.
    # Prefer recoverable messages, then use deterministic truncation as a safety
    # valve for non-recoverable content.
    if current_total() > aggregate_cap:
        ordered = sorted(
            tool_indexes,
            key=lambda index: (
                not bool(extract_tool_result_references(str(bounded[index].get("content") or ""))),
                -len(str(bounded[index].get("content") or "")),
                index,
            ),
        )
        for index in ordered:
            if current_total() <= aggregate_cap:
                break
            content = str(bounded[index].get("content") or "")
            excess = current_total() - aggregate_cap
            target_cap = max(240, min(per_result_cap, len(content) - excess))
            if len(content) <= target_cap:
                continue
            if "[result truncated for payload," in content:
                continue
            bounded[index]["content"] = truncate_tool_result_to_total_cap(
                content, target_cap,
            )
            trimmed += 1

    final_chars = current_total()
    return bounded, {
        "tool_results": len(tool_indexes),
        "trimmed": trimmed,
        "original_chars": original_chars,
        "final_chars": final_chars,
    }


def estimate_payload_tokens(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    *,
    chars_per_token: float = 3.5,
) -> int:
    """Estimate the exact payload shape that is about to be sent."""
    chars = sum(_message_chars(message) for message in messages)
    if tools:
        try:
            chars += len(json.dumps(tools, ensure_ascii=False, default=str))
        except Exception:
            chars += len(str(tools))
    return max(1, int(chars / chars_per_token))


def build_payload_signature(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build a compact signature that can prove byte-stable payload prefixes."""
    message_hashes: List[str] = []
    message_chars: List[int] = []
    for message in messages:
        try:
            serialized = json.dumps(
                message,
                ensure_ascii=False,
                default=str,
                sort_keys=True,
                separators=(",", ":"),
            )
        except Exception:
            serialized = str(message)
        message_hashes.append(hashlib.sha256(serialized.encode("utf-8")).hexdigest())
        message_chars.append(len(serialized))
    try:
        tools_text = json.dumps(
            tools or [],
            ensure_ascii=False,
            default=str,
            sort_keys=True,
            separators=(",", ":"),
        )
    except Exception:
        tools_text = str(tools or [])
    return {
        "message_hashes": message_hashes,
        "message_chars": message_chars,
        "tools_hash": hashlib.sha256(tools_text.encode("utf-8")).hexdigest(),
        "tools_chars": len(tools_text),
    }


def _signature_extends(current: Dict[str, Any], previous: Dict[str, Any]) -> bool:
    previous_hashes = list(previous.get("message_hashes") or [])
    current_hashes = list(current.get("message_hashes") or [])
    return (
        bool(previous_hashes)
        and current.get("tools_hash") == previous.get("tools_hash")
        and len(current_hashes) >= len(previous_hashes)
        and current_hashes[:len(previous_hashes)] == previous_hashes
    )


def estimate_payload_tokens_with_anchor(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    state: Optional[Dict[str, Any]],
    *,
    chars_per_token: float = 3.5,
) -> Tuple[int, Dict[str, Any], bool]:
    """Use exact prior API usage plus only the byte-stable appended delta."""
    signature = build_payload_signature(messages, tools)
    anchor = dict((state or {}).get("usage_anchor") or {})
    if anchor and _signature_extends(signature, anchor):
        previous_count = len(anchor.get("message_hashes") or [])
        delta_chars = sum(signature["message_chars"][previous_count:])
        estimate = int(anchor.get("prompt_tokens") or 0) + int(
            delta_chars / chars_per_token
        )
        if estimate > 0:
            return estimate, signature, True
    return (
        estimate_payload_tokens(
            messages,
            tools,
            chars_per_token=chars_per_token,
        ),
        signature,
        False,
    )


def record_payload_usage(
    state: Optional[Dict[str, Any]],
    signature: Dict[str, Any],
    *,
    prompt_tokens: int,
    completion_tokens: int = 0,
) -> None:
    """Record a real provider usage anchor and bounded turn-growth samples."""
    if state is None:
        return
    previous = dict(state.get("usage_anchor") or {})
    prompt_tokens = max(0, int(prompt_tokens or 0))
    if previous and _signature_extends(signature, previous):
        growth = prompt_tokens - int(previous.get("prompt_tokens") or 0)
        if growth >= 0:
            samples = [
                max(0, int(value))
                for value in list(state.get("turn_growth_samples") or [])
            ]
            samples.append(growth)
            state["turn_growth_samples"] = samples[-100:]
    state["usage_anchor"] = {
        "message_hashes": list(signature.get("message_hashes") or []),
        "tools_hash": str(signature.get("tools_hash") or ""),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": max(0, int(completion_tokens or 0)),
        "recorded_at": time.time(),
    }


def adaptive_safety_margin(
    state: Optional[Dict[str, Any]],
    *,
    default: int = _DEFAULT_TURN_SAFETY_MARGIN,
    minimum: int = 2_000,
    maximum: int = 20_000,
) -> int:
    """Return a bounded P95 single-call growth margin with 20% headroom."""
    samples = sorted(
        max(0, int(value))
        for value in list((state or {}).get("turn_growth_samples") or [])
    )
    if len(samples) < 5:
        return max(minimum, min(maximum, int(default)))
    index = max(0, min(len(samples) - 1, (95 * len(samples) + 99) // 100 - 1))
    p95 = samples[index]
    return max(minimum, min(maximum, int(p95 * 1.2)))


# ── Core compaction logic ─────────────────────────────────────────────────────

def _call_summarizer(
    client,
    summary_model: str,
    conversation_text: str,
    focus: str = "",
) -> tuple[str, Any]:
    """Call the LLM and return summary text plus provider usage."""
    prompt = _COMPACT_PROMPT_TEMPLATE.format(conversation_text=conversation_text)
    focus = str(focus or "").strip()[:1000]
    if focus:
        prompt += (
            "\n\nAdditional user retention priority:\n"
            + focus
            + "\nPreserve this priority when it is supported by the conversation."
        )

    response = client.chat.completions.create(
        model=summary_model,
        messages=[
            {"role": "system", "content": _COMPACT_SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.1,
        max_tokens=_SUMMARY_MAX_TOKENS,
        stream=False,
    )
    return response.choices[0].message.content or "", getattr(response, "usage", None)


def compact_history(
    history: List[Dict],
    client,
    model: str,
    summary_model: Optional[str] = None,
    usage_callback: Optional[Callable[[Any], None]] = None,
    focus: str = "",
) -> Tuple[List[Dict], bool]:
    """
    Summarize the oldest portion of history, keeping the most recent turns verbatim.

    Args:
        history:       full conversation history (list of message dicts)
        client:        the LLM client (same one the session uses)
        model:         the session model — used as the summarizer model when
                       `summary_model` is not given (guarantees a valid model
                       for the active provider/endpoint).
        summary_model: optional explicit model id for the summary call. If the
                       caller does not provide one, `model` is used as-is so we
                       never request a model the provider does not host.

    Returns:
        (new_history, did_compact)
        new_history[0] is a system message containing the summary if compacted.
    """
    if len(history) < _MIN_TURNS_FOR_COMPACT:
        return history, False

    # Split: summarize the head, keep a token-budgeted, tool-call-safe tail.
    tail_start = safe_tail_start_by_tokens(history)
    if tail_start == 0:
        # Small histories can fit entirely inside the tail. Keep the legacy
        # count fallback so manual compaction can still make progress.
        desired_keep = min(_KEEP_RECENT_TURNS, len(history) // 2)
        tail_start = _safe_tail_start(history, desired_keep)
    to_summarize = history[:tail_start]
    to_keep      = history[tail_start:]

    if not to_summarize:
        return history, False

    # Strip heavy content and build text
    stripped = [_strip_heavy_content(m) for m in to_summarize]

    # Use the session model for summarization unless the caller supplied a
    # lighter one. Never hard-code a model id — a custom provider/endpoint may
    # not host it, which would 404 the whole compaction.
    use_model = summary_model or model

    t0 = time.monotonic()
    summary = ""
    summary_input = stripped
    for attempt in range(3):
        conversation_text = _bounded_summary_text(summary_input)
        try:
            summary, usage = _call_summarizer(
                client, use_model, conversation_text, focus=focus,
            )
            if usage is not None and usage_callback is not None:
                usage_callback(usage)
            break
        except Exception as exc:
            from agent.retry import is_context_length_error

            if not is_context_length_error(exc) or attempt >= 2:
                log.warning(
                    "[compaction] summarization failed after %d attempt(s): %s — "
                    "keeping history as-is",
                    attempt + 1, exc,
                )
                return history, False
            groups = _atomic_message_groups(summary_input)
            if len(groups) <= 1:
                log.warning(
                    "[compaction] summary input still too long with one atomic group"
                )
                return history, False
            drop_groups = max(1, (len(groups) + 4) // 5)
            cut_index = groups[min(drop_groups, len(groups) - 1)][0]
            log.warning(
                "[compaction] summary prompt too long; dropping %d oldest atomic "
                "group(s) and retrying",
                drop_groups,
            )
            summary_input = summary_input[cut_index:]

    if not summary.strip():
        log.warning("[compaction] summarizer returned empty output — keeping history as-is")
        return history, False

    elapsed = time.monotonic() - t0
    log.info(
        "[compaction] summarized %d→1 messages in %.1fs (kept %d recent turns)",
        len(to_summarize), elapsed, len(to_keep),
    )

    summary_msg: Dict[str, Any] = {
        "role": "system",
        # Tagged with COMPACTION_SUMMARY_MARKER so _hard_prune can protect it.
        "content": (
            COMPACTION_SUMMARY_MARKER + "\n\n"
            + summary.strip()
            + "\n\n[End of summary. Continue from the current state described above.]"
        ),
        "_compaction_summary": True,
    }

    new_history = [summary_msg] + to_keep
    return new_history, True


def _estimate_history_tokens(history: List[Dict], chars_per_token: float = 3.5) -> int:
    """Rough token estimate of the current history (chars / chars_per_token)."""
    import json as _json
    total_chars = 0
    for m in history:
        try:
            total_chars += len(_json.dumps(m, ensure_ascii=False))
        except Exception:
            total_chars += len(str(m))
    return max(1, int(total_chars / chars_per_token))


def trim_oversized_tool_results(history: List[Dict]) -> Tuple[List[Dict], int]:
    """Rule-based trim: truncate tool result messages that exceed _TRIM_TOOL_RESULT_CAP.

    This is a cheap, zero-LLM operation that runs in the 60–70% context zone
    BEFORE semantic compaction is considered.  It only shrinks bulky tool
    results (large query outputs, profile text) and leaves all other messages
    intact, so the agent retains full structural context.

    Returns:
        (trimmed_history, n_trimmed) — n_trimmed is the number of messages that
        were actually shortened (useful for logging).
    """
    trimmed = []
    n_trimmed = 0
    for msg in history:
        if msg.get("role") == "tool":
            raw = msg.get("content", "")
            if isinstance(raw, str) and len(raw) > _TRIM_TOOL_RESULT_CAP:
                from agent.tools.results import truncate_tool_result_preserving_refs
                msg = {
                    **msg,
                    "content": truncate_tool_result_preserving_refs(
                        raw, _TRIM_TOOL_RESULT_CAP,
                    ),
                }
                n_trimmed += 1
        trimmed.append(msg)
    return trimmed, n_trimmed


def should_trim_history(
    history: List[Dict],
    last_prompt_tokens: int,
    context_window: int,
    chars_per_token: float = 3.5,
    *,
    output_reserve: Optional[int] = None,
    safety_margin: Optional[int] = None,
) -> bool:
    """Return True shortly before the fixed-reserve compaction threshold.

    Used to gate trim_oversized_tool_results() — avoids unnecessary iteration
    when the context is comfortably below the trim threshold.
    """
    if len(history) < _MIN_TURNS_FOR_COMPACT:
        return False
    if not context_window or context_window <= 0:
        return False

    hi = compaction_threshold(
        context_window,
        output_reserve=output_reserve,
        safety_margin=safety_margin,
    )
    trim_band = max(1000, int(safety_margin or _DEFAULT_TURN_SAFETY_MARGIN))
    lo = max(1, hi - trim_band)

    # Signal 1: real token usage from previous turn
    if last_prompt_tokens:
        if lo <= last_prompt_tokens < hi:
            return True

    # Signal 2: estimated history size
    est = _estimate_history_tokens(history, chars_per_token)
    return lo <= est < hi


def should_compact_history(
    history: List[Dict],
    last_prompt_tokens: int,
    context_window: int,
    chars_per_token: float = 3.5,
    *,
    output_reserve: Optional[int] = None,
    safety_margin: Optional[int] = None,
) -> bool:
    """
    Decide whether to run semantic compaction.

    Triggers on EITHER of two signals reaching the fixed-reserve threshold:

      1. last_prompt_tokens — the real prompt-token count the LLM reported on
         the previous turn (same measure the frontend context bar shows).
      2. an estimate of the CURRENT history size — covers the case where the
         previous turn stuffed huge tool results into history, or where usage
         data is missing (e.g. right after a server restart).

    Using the current-history estimate as a second signal means compaction is
    not blocked just because `last_prompt_tokens` happens to be 0/stale.

    Returns False when there is not enough history to bother, when the window
    is unknown, or when both signals are still below the threshold.
    """
    if len(history) < _MIN_TURNS_FOR_COMPACT:
        return False
    if not context_window or context_window <= 0:
        return False

    threshold = compaction_threshold(
        context_window,
        output_reserve=output_reserve,
        safety_margin=safety_margin,
    )

    # Signal 1: real usage from the previous turn.
    if last_prompt_tokens and last_prompt_tokens >= threshold:
        return True

    # Signal 2: estimated size of the history we are about to send.
    est = _estimate_history_tokens(history, chars_per_token)
    return est >= threshold
