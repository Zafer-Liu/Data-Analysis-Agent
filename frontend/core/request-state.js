// Each feature owns one instance. No global store, retries, or write cancellation.
export function createRequestOwner({ getScope = () => "" } = {}) {
  const reads = new Map();
  const writes = new Map();
  let generation = 0;
  let scope = String(getScope());

  function cancelRead(key) {
    const ticket = reads.get(key);
    if (!ticket) return false;
    reads.delete(key);
    ticket.controller.abort();
    return true;
  }

  function reset() {
    generation += 1;
    for (const key of reads.keys()) cancelRead(key);
  }

  function currentScope() {
    const next = String(getScope());
    if (next !== scope) {
      scope = next;
      reset();
    }
    return scope;
  }

  function capture() {
    const capturedScope = currentScope();
    const version = generation;
    return {
      scope: capturedScope,
      isCurrent: () => currentScope() === capturedScope && generation === version,
    };
  }

  function beginRead(key) {
    const captured = capture();
    cancelRead(key);
    const controller = new AbortController();
    const ticket = {
      ...captured,
      controller,
      signal: controller.signal,
      isCurrent: () =>
        captured.isCurrent() && reads.get(key) === ticket && !controller.signal.aborted,
      finish: () => {
        if (reads.get(key) === ticket) reads.delete(key);
      },
    };
    reads.set(key, ticket);
    return ticket;
  }

  function beginWrite(key) {
    const captured = capture();
    const scopedKey = JSON.stringify([captured.scope, key]);
    if (writes.has(scopedKey)) return null;
    const ticket = {
      ...captured,
      // Keep the lock across reset/close until the original write settles.
      finish: () => {
        if (writes.get(scopedKey) === ticket) writes.delete(scopedKey);
      },
    };
    writes.set(scopedKey, ticket);
    return ticket;
  }

  function hasWrite(key) {
    return writes.has(JSON.stringify([currentScope(), key]));
  }

  return Object.freeze({ capture, beginRead, beginWrite, hasWrite, cancelRead, reset });
}
