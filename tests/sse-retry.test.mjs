// Contract tests for SSE retry logic (frontend/features/sse-retry.js).
//
// These pin the behaviour that protects the chat stream from transient
// network failures: only zero-event TypeError/network errors auto-retry,
// at most twice, with fixed backoff. Any change that widens retry to
// non-network errors or already-streamed turns is a regression.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  MAX_SSE_RETRIES,
  SSE_RETRY_DELAYS_MS,
  canAutoRetrySse,
  isNetworkStreamError,
  sseRetryDelayMs,
} from "../frontend/features/sse-retry.js";

test("MAX_SSE_RETRIES is 2 with two fixed delays", () => {
  assert.equal(MAX_SSE_RETRIES, 2);
  assert.deepEqual(SSE_RETRY_DELAYS_MS, [1000, 3000]);
});

test("isNetworkStreamError: TypeError and network/loadfailed messages", () => {
  assert.equal(isNetworkStreamError(new TypeError("fetch failed")), true);
  assert.equal(isNetworkStreamError(new Error("NetworkError")), true);
  assert.equal(isNetworkStreamError(new Error("loadfailed")), true);
  assert.equal(isNetworkStreamError(new Error("quota_exceeded")), false);
  assert.equal(isNetworkStreamError(null), false);
  assert.equal(isNetworkStreamError(undefined), false);
});

test("canAutoRetrySse: allows retry only when no event received and retries remain", () => {
  // Fresh network error, zero events → retryable
  assert.equal(
    canAutoRetrySse({
      error: new TypeError("fetch failed"),
      receivedEvent: false,
      retries: 0,
    }),
    true,
  );
  // Same but already streamed an event → never retry (avoids duplicate side-effects)
  assert.equal(
    canAutoRetrySse({
      error: new TypeError("fetch failed"),
      receivedEvent: true,
      retries: 0,
    }),
    false,
  );
  // Non-network error → no retry
  assert.equal(
    canAutoRetrySse({
      error: new Error("server error 500"),
      receivedEvent: false,
      retries: 0,
    }),
    false,
  );
  // Stop requested → no retry
  assert.equal(
    canAutoRetrySse({
      error: new TypeError("fetch failed"),
      receivedEvent: false,
      retries: 0,
      stopRequested: true,
    }),
    false,
  );
  // Cancelled → no retry
  assert.equal(
    canAutoRetrySse({
      error: new TypeError("fetch failed"),
      receivedEvent: false,
      retries: 0,
      cancelled: true,
    }),
    false,
  );
  // Exhausted retries → no retry
  assert.equal(
    canAutoRetrySse({
      error: new TypeError("fetch failed"),
      receivedEvent: false,
      retries: MAX_SSE_RETRIES,
    }),
    false,
  );
});

test("sseRetryDelayMs: returns configured delays, clamps to last", () => {
  assert.equal(sseRetryDelayMs(1), 1000);
  assert.equal(sseRetryDelayMs(2), 3000);
  // Beyond the table → last delay (no undefined)
  assert.equal(sseRetryDelayMs(3), 3000);
});
