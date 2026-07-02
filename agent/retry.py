#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Retry policy for LLM API calls.

Wraps any callable with exponential backoff on transient errors
(rate limits, 5xx, connection/timeout). Non-retryable exceptions
(4xx other than 429, auth errors, etc.) re-raise immediately.

Usage:
    from agent.retry import call_with_retry
    response = call_with_retry(lambda: client.chat.completions.create(...))
"""
import logging
import time
from typing import Tuple

log = logging.getLogger(__name__)

# HTTP status codes that warrant a retry. Kept as a public constant so tests
# (and future tweaks) can reason about the policy without grepping the module.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def is_context_length_error(exc: Exception) -> bool:
    """Return True for provider variants of context-window overflow errors."""
    message = str(exc).lower().replace("-", "_").replace(" ", "_")
    markers = (
        "prompt_too_long",
        "context_length_exceeded",
        "maximum_context_length",
        "max_context_length",
        "context_window_exceeded",
        "too_many_input_tokens",
        "request_too_large_for_model",
    )
    return any(marker in message for marker in markers)


def is_retryable(exc: Exception) -> Tuple[bool, float]:
    """Return (should_retry, base_wait_seconds) for an exception.

    Base wait is multiplied by 2**(attempt-1) by `call_with_retry` to produce
    the actual sleep, so values here are the *first attempt* delay only.
    """
    msg = str(exc).lower()
    # Context overflow needs compaction, not exponential network retries.
    if is_context_length_error(exc):
        return False, 0.0
    # Rate limit: respect Retry-After if present (not parsed yet — just back off more)
    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return True, 5.0
    # Transient server errors
    for code in ("500", "502", "503", "504"):
        if code in msg:
            return True, 3.0
    # Connection / timeout
    if any(k in msg for k in ("timeout", "connection", "network", "timed out")):
        return True, 2.0
    return False, 0.0


def call_with_retry(fn, *args, max_retries: int = 3, **kwargs):
    """Call fn(*args, **kwargs) with exponential backoff on transient errors.

    Schedule (base_wait × 2**(attempt-1)):
      - rate limit (429):     5s → 10s → 20s
      - server error (5xx):   3s →  6s → 12s
      - network/timeout:      2s →  4s →  8s
    """
    attempt = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            retryable, base_wait = is_retryable(exc)
            attempt += 1
            if not retryable or attempt > max_retries:
                raise
            wait = base_wait * (2 ** (attempt - 1))
            log.warning("[retry] attempt %d/%d failed (%s), waiting %.1fs",
                        attempt, max_retries, exc, wait)
            time.sleep(wait)
