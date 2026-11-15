# -*- coding: utf-8 -*-
"""Generous numeric provenance checks with explicit, bounded rate derivations.

Ordinals, identifiers, years, ranges and magnitude notation retain the original
false-positive suppression. Percentages require a percentage source, a named
rate field, or an explicit numerator/denominator relationship. We never search
all possible pairs of unrelated evidence numbers for a matching ratio.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP

_RANGE_RE = re.compile(r"\d[\d.,]*\s*[-–—~～]\s*\d[\d.,]*")
_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_.·])\d{1,3}(?:,\d{3})+(?:\.\d+)?"
    r"|(?<![A-Za-z0-9_.·])\d+(?:\.\d+)?"
)
_PERCENT_RE = re.compile(r"(?<![A-Za-z0-9_.])(-?\d+(?:,\d{3})*(?:\.\d+)?)\s*([%％‰])")
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_SKIP_SUFFIX_CHARS = {"%", "％", "‰", "万", "亿", "倍"}
_MIN_INT = 100
_MIN_DECIMAL = Decimal("3")
_MAX_LISTED = 15
_MAX_TEXT_CHARS = 60_000

NUMBER_EVIDENCE_TOOLS = frozenset({
    "query_data", "run_analysis", "create_analysis_table", "profile_data",
    "fetch_table", "browser_extract_tables", "generate_chart",
})
# A member's prose cannot certify its own numbers, even inside a tool message.
_COORDINATION_TOOLS = frozenset({
    "agent_delegate", "team_delegate", "agent_task", "team_status", "send_message",
    "workflow_start", "workflow_status", "team_plan_create",
})


def _iter_number_tokens(text: str):
    for match in _NUM_RE.finditer(text):
        yield match.group(0), text[match.end():match.end() + 3].lstrip()


def _to_decimal(raw: str) -> tuple[Decimal, int] | None:
    cleaned = str(raw).replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not value.is_finite() or value < 0:
        return None
    frac = cleaned.split(".", 1)[1] if "." in cleaned else ""
    return value, len(frac)


def _extract_report_numbers(report_text: str) -> list[tuple[str, Decimal, int]]:
    masked = _RANGE_RE.sub(" ", str(report_text or ""))
    seen = {}
    for raw, tail in _iter_number_tokens(masked):
        if tail[:1] in _SKIP_SUFFIX_CHARS:
            continue
        parsed = _to_decimal(raw)
        if parsed is None:
            continue
        value, decimals = parsed
        if decimals == 0:
            if value < _MIN_INT or _YEAR_RE.match(raw.replace(",", "")):
                continue
        elif value < _MIN_DECIMAL:
            continue
        seen.setdefault(raw.replace(",", ""), (value, decimals))
    return [(key, *value) for key, value in seen.items()]


def _quantize(value: Decimal, decimals: int) -> Decimal:
    exp = Decimal(1).scaleb(-decimals)
    try:
        return value.quantize(exp, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        try:
            return value.quantize(exp, rounding=ROUND_HALF_EVEN)
        except InvalidOperation:
            return value


def _parse_corpus_values(texts) -> set[Decimal]:
    values = set()
    for text in texts:
        for raw, _tail in _iter_number_tokens(str(text or "")[:_MAX_TEXT_CHARS]):
            parsed = _to_decimal(raw)
            if parsed is not None:
                values.add(parsed[0])
    return values


def _decode_envelope(content):
    if isinstance(content, dict):
        return content, str(content.get("tool") or "")
    text = str(content or "")
    name = ""
    if text.startswith("[TOOL_RESULT] "):
        first, _, text = text.partition("\n")
        name = first.split(" ", 2)[1]
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None, name
    return (parsed, name or str(parsed.get("tool") or "")) if isinstance(parsed, dict) else (None, name)


def collect_number_evidence(messages) -> tuple[list[str], list[str]]:
    """Only tool data is numeric evidence; assistant/delegate prose is excluded.

    The executor carries full child tool results separately for the current
    turn. Persisted envelopes supply bounded actual data, never summary/debug,
    token counters, argument echoes, claim IDs, or a member's final answer.
    """
    evidence_texts, context_texts, call_names = [], [], {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or {}
                call_names[call.get("id")] = function.get("name", "")
                # SQL thresholds retain their legacy allowance. Delegation
                # prompts, chart titles and argument prose cannot certify
                # numbers authored by the same model.
                if function.get("name") in {"query_data", "create_analysis_table", "run_analysis", "generate_chart"}:
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except (TypeError, ValueError):
                        arguments = {}
                    if isinstance(arguments, dict) and arguments.get("sql"):
                        context_texts.append(" ".join(match.group(0) for match in _NUM_RE.finditer(str(arguments["sql"]))))
        elif role == "user":
            # System repair prompts use role=user for provider compatibility.
            # They must not turn their own rejected values into evidence.
            text = str(message.get("content") or "")
            if not text.startswith("[QUALITY GATE]"):
                context_texts.append(text)
        elif role == "tool":
            content = message.get("content") or ""
            envelope, name = _decode_envelope(content)
            name = name or str(message.get("name") or call_names.get(message.get("tool_call_id"), ""))
            if name in _COORDINATION_TOOLS:
                continue
            if envelope is not None and envelope.get("type") == "tool_result":
                if envelope.get("ok") is not True:
                    continue
                data = envelope.get("data", "")
                evidence_texts.append(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))
            elif not str(content).startswith(("ERROR:", "[ARG ERROR]", "[SYSTEM BLOCK]")):
                evidence_texts.append(str(content))
    return evidence_texts, context_texts


def _rate_values(texts):
    """Percentage points supported by explicit units and named local records."""
    rates = set()
    pairs = (("numerator", "denominator"), ("part", "total"), ("count", "total"),
             ("missing_count", "row_count"), ("missing_rows", "total_rows"),
             ("matched_rows", "total_rows"), ("分子", "分母"))
    visited = 0

    def walk(record, depth=0):
        nonlocal visited
        visited += 1
        if visited > 2000 or depth > 5:
            return
        if isinstance(record, dict):
            # Only names that declare a ratio/percent unit establish a rate.
            for key, value in record.items():
                if isinstance(value, (int, float, str)) and not isinstance(value, bool):
                    parsed = _to_decimal(str(value))
                    if parsed:
                        number = parsed[0]
                        label = str(key).lower()
                        if label.endswith(("_pct", "_percent", "%", "百分比")):
                            rates.add(number)
                        elif label.endswith(("_ratio", "_rate")) and number <= 1:
                            rates.add(number * 100)
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)
            for numerator, denominator in pairs:
                a, b = record.get(numerator), record.get(denominator)
                if a is None or b is None or isinstance(a, bool) or isinstance(b, bool):
                    continue
                pa, pb = _to_decimal(str(a)), _to_decimal(str(b))
                # Pair names declare the relationship. An explicit unit
                # mismatch overrides them rather than validating nonsense.
                units_match = record.get("numerator_unit") == record.get("denominator_unit")
                if pa and pb and pb[0] > 0 and units_match:
                    rates.add(pa[0] / pb[0] * 100)
            # Named period values support growth, not arbitrary pair ratios.
            if record.get("previous") is not None and record.get("current") is not None:
                pa, pb = _to_decimal(str(record["previous"])), _to_decimal(str(record["current"]))
                if pa and pb and pa[0] > 0 and record.get("previous_unit") == record.get("current_unit"):
                    rates.add((pb[0] - pa[0]) / pa[0] * 100)
        elif isinstance(record, list):
            for item in record[:1000]:
                walk(item, depth + 1)

    for text in texts:
        bounded = str(text or "")[:_MAX_TEXT_CHARS]
        for match in _PERCENT_RE.finditer(bounded):
            value = Decimal(match[1].replace(",", ""))
            rates.add(value / 10 if match[2] == "‰" else value)
        try:
            walk(json.loads(bounded))
        except (TypeError, ValueError):
            pass
    return rates


_EXPLICIT_FRACTION_RE = re.compile(
    r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(行|条|人|件|元|rows?|items?|people)\s*"
    r"(?:/|除以|out of|of)\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(行|条|人|件|元|rows?|items?|people)", re.IGNORECASE,
)


def _percentage_gaps(report_text, evidence_texts, context_texts):
    rates = _rate_values([*evidence_texts, *context_texts])
    # Report-declared fractions are bounded to the same sentence and same unit;
    # both operands must actually occur in tool evidence, not model arguments.
    evidence_values = _parse_corpus_values(evidence_texts)
    gaps = []
    for match in _PERCENT_RE.finditer(str(report_text or "")):
        number = Decimal(match[1].replace(",", ""))
        decimals = len(match[1].partition(".")[2])
        target = number / 10 if match[2] == "‰" else number
        digits = decimals + (1 if match[2] == "‰" else 0)
        candidates = set(rates)
        left = max(str(report_text).rfind(delimiter, 0, match.start()) for delimiter in ("\n", "。", ";", "；")) + 1
        sentence = str(report_text)[max(left, match.start() - 200):match.end()]
        for fraction in _EXPLICIT_FRACTION_RE.finditer(sentence):
            a, b = Decimal(fraction[1].replace(",", "")), Decimal(fraction[3].replace(",", ""))
            if fraction[2].lower().rstrip("s") == fraction[4].lower().rstrip("s") and a in evidence_values and b in evidence_values and b > 0:
                candidates.add(a / b * 100)
        if not any(_quantize(value, digits) == target for value in candidates):
            key = match[1].replace(",", "") + ("‰" if match[2] == "‰" else "%")
            gaps.append({"value": key, "reason": "missing matching percent/rate source or same-unit numerator and denominator"})
    return gaps


def number_evidence_gaps(report_text, evidence_texts, context_texts=()):
    return _percentage_gaps(report_text, list(evidence_texts or []), list(context_texts or []))


def audit_report_numbers(report_text: str, evidence_texts, context_texts=()) -> tuple[list[str], int]:
    evidence_texts, context_texts = list(evidence_texts or []), list(context_texts or [])
    values = _parse_corpus_values([*evidence_texts, *context_texts])
    substantial = {value for value in values if value >= 100}
    unsupported = []
    for key, value, decimals in _extract_report_numbers(report_text):
        if value in values or any(_quantize(source, decimals) == value for source in values):
            continue
        if decimals == 0 and any(value - a in substantial or a - value in substantial for a in substantial):
            continue
        unsupported.append(key)
    unsupported.extend(gap["value"] for gap in _percentage_gaps(report_text, evidence_texts, context_texts))
    unsupported = list(dict.fromkeys(unsupported))
    return unsupported[:_MAX_LISTED], len(unsupported)
