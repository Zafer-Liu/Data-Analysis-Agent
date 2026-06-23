#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for agent/retry.py — exponential backoff classifier + driver."""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.retry import call_with_retry, is_retryable


class TestIsRetryable(unittest.TestCase):

    def test_rate_limit_429(self):
        ok, wait = is_retryable(Exception("HTTP 429 Too Many Requests"))
        self.assertTrue(ok)
        self.assertEqual(wait, 5.0)

    def test_rate_limit_text(self):
        ok, wait = is_retryable(Exception("rate limit exceeded"))
        self.assertTrue(ok)

    def test_server_error_503(self):
        ok, wait = is_retryable(Exception("503 Service Unavailable"))
        self.assertTrue(ok)
        self.assertEqual(wait, 3.0)

    def test_timeout(self):
        ok, wait = is_retryable(Exception("Connection timed out"))
        self.assertTrue(ok)
        self.assertEqual(wait, 2.0)

    def test_network_keyword(self):
        ok, _ = is_retryable(Exception("network unreachable"))
        self.assertTrue(ok)

    def test_auth_error_not_retryable(self):
        ok, _ = is_retryable(Exception("401 Unauthorized: invalid api_key"))
        self.assertFalse(ok)

    def test_bad_request_not_retryable(self):
        ok, _ = is_retryable(Exception("400 Bad Request"))
        self.assertFalse(ok)


class TestCallWithRetry(unittest.TestCase):

    def test_succeeds_first_try(self):
        calls = []
        def fn():
            calls.append(1)
            return "ok"
        with patch("agent.retry.time.sleep") as sleep:
            self.assertEqual(call_with_retry(fn), "ok")
            sleep.assert_not_called()
        self.assertEqual(len(calls), 1)

    def test_retries_transient_then_succeeds(self):
        attempts = {"n": 0}
        def fn():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise Exception("503 transient")
            return "finally"
        with patch("agent.retry.time.sleep") as sleep:
            self.assertEqual(call_with_retry(fn), "finally")
        # Two retries → two sleeps
        self.assertEqual(sleep.call_count, 2)
        # Backoff: 3s, 6s for 503
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [3.0, 6.0])

    def test_non_retryable_raises_immediately(self):
        def fn():
            raise Exception("401 Unauthorized")
        with patch("agent.retry.time.sleep") as sleep:
            with self.assertRaises(Exception):
                call_with_retry(fn)
            sleep.assert_not_called()

    def test_gives_up_after_max_retries(self):
        def fn():
            raise Exception("503 still down")
        with patch("agent.retry.time.sleep"):
            with self.assertRaises(Exception):
                call_with_retry(fn, max_retries=2)

    def test_passes_args_through(self):
        def fn(a, b, c=0):
            return a + b + c
        with patch("agent.retry.time.sleep"):
            self.assertEqual(call_with_retry(fn, 1, 2, c=3), 6)


if __name__ == "__main__":
    unittest.main()
