// Contract tests for the UI island registry (frontend/core/ui-registry.js).
//
// The registry is the single facade surface between business modules and Vue
// islands. These tests pin the contract that islands are registered by name,
// retrievable via getUiIsland, and that re-registration replaces (not stacks)
// the previous API — a regression here would make ensureUiIsland() return
// stale facades or null.
//
// ui-registry.js attaches to globalThis.BAA at import time, so we isolate each
// test by resetting the registry object between cases.
import { test } from "node:test";
import assert from "node:assert/strict";

// The module reads globalThis.BAA at import; set up a clean namespace then
// import fresh per test via dynamic import with cache busting.
async function freshRegistry() {
  const prev = globalThis.BAA;
  globalThis.BAA = { events: undefined };
  delete globalThis.BAA;
  globalThis.BAA = {};
  // bust the module cache so event-bus/ui-registry re-init against clean BAA
  const busUrl = new URL("../frontend/core/event-bus.js", import.meta.url);
  const regUrl = new URL("../frontend/core/ui-registry.js", import.meta.url);
  const bus = await import(`${busUrl.href}?t=${Date.now()}`);
  const mod = await import(`${regUrl.href}?t=${Date.now()}`);
  return mod;
}

test("registerUiIsland stores and getUiIsland retrieves by name", async () => {
  const { registerUiIsland, getUiIsland } = await freshRegistry();
  const api = Object.freeze({ open: () => {} });
  registerUiIsland("test-island", api);
  assert.equal(getUiIsland("test-island"), api);
});

test("getUiIsland returns null for unregistered names", async () => {
  const { getUiIsland } = await freshRegistry();
  assert.equal(getUiIsland("nonexistent"), null);
});

test("re-registration replaces the previous API (no stacking)", async () => {
  const { registerUiIsland, getUiIsland } = await freshRegistry();
  const v1 = Object.freeze({ version: 1 });
  const v2 = Object.freeze({ version: 2 });
  registerUiIsland("island", v1);
  assert.equal(getUiIsland("island"), v1);
  registerUiIsland("island", v2);
  assert.equal(getUiIsland("island"), v2);
  assert.notEqual(getUiIsland("island"), v1);
});

test("registerUiIsland(name, null) unregisters (returns null facade)", async () => {
  const { registerUiIsland, getUiIsland } = await freshRegistry();
  registerUiIsland("temp", Object.freeze({}));
  assert.ok(getUiIsland("temp"));
  registerUiIsland("temp", null);
  assert.equal(getUiIsland("temp"), null);
});

test("registered island APIs are exposed on globalThis.BAA.ui", async () => {
  const { registerUiIsland } = await freshRegistry();
  const api = Object.freeze({ open: () => {} });
  registerUiIsland("shared", api);
  assert.equal(globalThis.BAA.ui.shared, api);
});
