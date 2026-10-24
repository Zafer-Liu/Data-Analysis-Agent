# -*- coding: utf-8 -*-
"""Numeric provenance audit for final agent answers.

A final report may only present numbers that trace back to tool results from
the same conversation (or to numbers supplied by the user / written by the
agent into SQL arguments). This module extracts "data-derived" numbers from a
report draft and returns the ones that cannot be matched against the
conversation's evidence corpus.

The audit is deliberately generous to avoid false alarms:

- comma separators are normalized (``2,954,669`` == ``2954669``)
- rounded values match their evidence source (``40.7`` matches ``40.73``)
- integers that equal the sum or difference of two evidence numbers match
  (legitimately derived deltas such as "556 rows removed")
- percentages (``2.69%``), magnitude-suffixed values (``295.5 万``),
  ranges (``2013-2017``, ``GB 3095-2012``), years, small integers (< 100),
  tiny decimals (< 3) and identifier-embedded tokens (``PM2.5``, ``v1.3``)
  are skipped entirely

Typical failure it must catch: the tool returned ``1,956`` but the final
report transcribed ``2,956``; or a monthly-trend table carries PM2.5/PM10
columns although the executed query only returned AQI.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP

# Hyphen / en-dash / em-dash / wave ranges: dates, standard codes, thresholds.
_RANGE_RE = re.compile(r"\d[\d.,]*\s*[-–—~～]\s*\d[\d.,]*")
# Numbers not glued to an ASCII identifier token (PM2.5, v1.3, L966, NO2).
_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_.\u00b7])\d{1,3}(?:,\d{3})+(?:\.\d+)?"
    r"|(?<![A-Za-z0-9_.\u00b7])\d+(?:\.\d+)?"
)
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
# Skip a number when the next non-space character is one of these.
_SKIP_SUFFIX_CHARS = {"%", "％", "‰", "万", "亿", "倍"}

_MIN_INT = 100            # integers below this are noise (ordinals, ids, thresholds)
_MIN_DECIMAL = Decimal("3")  # decimals below this (ratios, coefficients) are noise
_MAX_LISTED = 15          # cap for values listed in the gate message / footnote
_MAX_TEXT_CHARS = 60_000  # per-text cap when parsing evidence corpora

# Tools whose success marks a turn as "data-derived"; only those turns are audited.
NUMBER_EVIDENCE_TOOLS = frozenset({
    "query_data", "run_analysis", "create_analysis_table", "profile_data",
    "fetch_table", "browser_extract_tables", "generate_chart",
})


def _iter_number_tokens(text: str):
    """Yield ``(raw, tail)`` for every number token, with the text after it."""
    for match in _NUM_RE.finditer(text):
        tail = text[match.end():match.end() + 3].lstrip()
        yield match.group(0), tail


def _to_decimal(raw: str) -> tuple[Decimal, int] | None:
    cleaned = raw.replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not value.is_finite() or value < 0:
        return None
    frac = cleaned.split(".", 1)[1] if "." in cleaned else ""
    return value, len(frac)


def _extract_report_numbers(report_text: str) -> list[tuple[str, Decimal, int]]:
    """Data-derived numbers worth verifying: ``(key, value, decimals)``."""
    masked = _RANGE_RE.sub(" ", str(report_text or ""))
    seen: dict[str, tuple[Decimal, int]] = {}
    order: list[str] = []
    for raw, tail in _iter_number_tokens(masked):
        if tail[:1] in _SKIP_SUFFIX_CHARS:
            continue
        parsed = _to_decimal(raw)
        if parsed is None:
            continue
        value, decimals = parsed
        if decimals == 0:
            if value < _MIN_INT:
                continue
            if _YEAR_RE.match(raw.replace(",", "")):
                continue
        elif value < _MIN_DECIMAL:
            continue
        key = raw.replace(",", "")
        if key not in seen:
            seen[key] = (value, decimals)
            order.append(key)
    return [(key, seen[key][0], seen[key][1]) for key in order]


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
    values: set[Decimal] = set()
    for text in texts:
        text = str(text or "")
        if not text:
            continue
        for raw, _tail in _iter_number_tokens(text[:_MAX_TEXT_CHARS]):
            parsed = _to_decimal(raw)
            if parsed is not None:
                values.add(parsed[0])
    return values


def collect_number_evidence(messages) -> tuple[list[str], list[str]]:
    """Split a message list into ``(tool_result_texts, context_texts)``.

    Context texts (user messages and the agent's own SQL/tool arguments) are
    matched as legitimate number sources: thresholds written into SQL such as
    ``AQI > 100`` and facts the user stated must not trigger the gate.
    Assistant prose is intentionally excluded — that is exactly the channel
    through which fabricated numbers would re-enter.
    """
    evidence_texts: list[str] = []
    context_texts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "tool":
            evidence_texts.append(str(message.get("content") or ""))
        elif role == "user":
            context_texts.append(str(message.get("content") or ""))
        elif role == "assistant":
            for call in message.get("tool_calls") or []:
                if isinstance(call, dict):
                    function = call.get("function") or {}
                    context_texts.append(str(function.get("arguments") or ""))
    return evidence_texts, context_texts


def audit_report_numbers(
    report_text: str,
    evidence_texts,
    context_texts=(),
) -> tuple[list[str], int]:
    """Return ``(unsupported_keys, total_count)`` for the report draft.

    ``unsupported_keys`` is capped at ``_MAX_LISTED`` entries (deduped, in
    order of first appearance); ``total_count`` is the full count.
    """
    texts = [*(evidence_texts or []), *(context_texts or [])]
    blob = " ".join(str(t)[:_MAX_TEXT_CHARS].replace(",", "") for t in texts if t)
    values = _parse_corpus_values(texts)
    float_values = {float(v) for v in values if v}
    unsupported: list[str] = []
    for key, value, decimals in _extract_report_numbers(report_text):
        if key in blob:
            continue
        if value in values:
            continue
        if any(_quantize(e, decimals) == value for e in values):
            continue
        if decimals == 0:
            # Derived integer deltas: x == a + b or x == a - b, where BOTH
            # operands are substantial evidence numbers (>= 100). The floor
            # blocks trivial pairings such as 920 = 919 + 1 where "1" is only
            # a row label, while real deltas like 556 = 2954669 - 2954113
            # still pass.
            target = float(value)
            derived = False
            for a in float_values:
                if a < 100:
                    continue
                for partner in (round(target - a, 6), round(a - target, 6)):
                    if partner >= 100 and partner in float_values:
                        derived = True
                        break
                if derived:
                    break
            if derived:
                continue
        unsupported.append(key)
    return unsupported[:_MAX_LISTED], len(unsupported)
