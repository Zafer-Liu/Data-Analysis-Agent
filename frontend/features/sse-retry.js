export const MAX_SSE_RETRIES = 2;
export const SSE_RETRY_DELAYS_MS = [1000, 3000];

export function isNetworkStreamError(error) {
  if (!error) return false;
  const name = error.name || "";
  const message = error.message || "";
  if (name === "TypeError") return true;
  return /network|fetch|loadfailed/i.test(message);
}

export function canAutoRetrySse({
  error,
  receivedEvent,
  retries,
  stopRequested = false,
  cancelled = false,
}) {
  if (stopRequested || cancelled || receivedEvent) return false;
  if (!Number.isFinite(retries) || retries < 0) return false;
  return retries < MAX_SSE_RETRIES && isNetworkStreamError(error);
}

export function sseRetryDelayMs(retries) {
  const index = Number(retries) - 1;
  return SSE_RETRY_DELAYS_MS[index] ?? SSE_RETRY_DELAYS_MS.at(-1);
}
