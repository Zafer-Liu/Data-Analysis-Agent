// Contract tests for the built chat-app bundle structure.
//
// These guard against the most expensive historical frontend bug: a dynamic
// island chunk reverse-importing the entry bundle, causing chat-app.js to
// execute twice (duplicate listeners, overlayStack corruption, "button
// triggers twice" / "state flickers" symptoms documented in
// docs/02-architecture.md §2.1.2).
//
// They run against the BUILT output in static/dist/ (produced by build:chat),
// so they must run after `npm run build:check`. They are intentionally
// structural — they verify invariants that would be invisible at runtime
// until a user clicks a dynamic island.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const distDir = path.join(root, "static", "dist");
const appJsPath = path.join(distDir, "chat-app.js");
const chunksDir = path.join(distDir, "chunks");
const graphText = () => [readFileSync(appJsPath, "utf8"), ...readdirSync(chunksDir)
  .filter(name => name.endsWith(".js")).map(name => readFileSync(path.join(chunksDir, name), "utf8"))].join("\n");

test("chat-app.js bundle exists and is non-trivial", () => {
  assert.ok(existsSync(appJsPath), "static/dist/chat-app.js missing — run build:chat");
  const stat = readFileSync(appJsPath, "utf-8");
  assert.ok(stat.length > 200_000, "chat-app.js suspiciously small (<200KB)");
});

test("bundle contains the anti-double-load guards (prevents duplicate listeners)", () => {
  const text = graphText();
  // __baaAppLoaded: entry-level guard (chat-app.js duplicate load)
  assert.ok(text.includes("__baaAppLoaded"), "missing __baaAppLoaded guard");
  // __baaAppDelegationRegistered: event-delegation guard (legacy/app.js)
  assert.ok(
    text.includes("__baaAppDelegationRegistered"),
    "missing __baaAppDelegationRegistered guard",
  );
  // __baaOverlayStack + listener guard: overlay singleton integrity
  assert.ok(text.includes("__baaOverlayStack"), "missing __baaOverlayStack global");
  assert.ok(
    text.includes("__baaOverlayDocumentListenersRegistered"),
    "missing overlay document-listener guard",
  );
});

test("bundle uses /static/dist/ as Vite base (chunk preload paths resolve correctly)", () => {
  const text = graphText();
  assert.ok(
    text.includes("/static/dist/"),
    "Vite base not /static/dist/ — dynamic import() paths would 404",
  );
});

test("dynamic island chunks do NOT reverse-import the entry bundle (no double execution)", () => {
  assert.ok(existsSync(chunksDir), "static/dist/chunks/ missing");
  const chunks = readdirSync(chunksDir).filter((f) => f.endsWith(".js"));
  assert.ok(chunks.length > 0, "no island chunks built — ensureUiIsland would have nothing to load");

  const offenders = [];
  for (const chunk of chunks) {
    const text = readFileSync(path.join(chunksDir, chunk), "utf-8");
    // A reverse import of the entry would appear as a relative import of
    // chat-app.js (with or without extension) inside a chunk file.
    if (/from\s+["'][^"']*chat-app\.js?["']/.test(text) || /import\s*\(\s*["'][^"']*chat-app\.js?["']\s*\)/.test(text)) {
      offenders.push(chunk);
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `chunks reverse-import the entry bundle (would cause double execution): ${offenders.join(", ")}`,
  );
});

test("all expected on-demand island chunks are present", () => {
  const chunks = readdirSync(chunksDir).filter((f) => f.endsWith(".js"));
  const expected = [
    "settings-ui",
    "knowledge-ui",
    "mcp-ui",
    "workspace-ui",
    "job-history-ui",
    "business-canvas-ui",
    "ui-registry", // shared chunk (ui-registry + event-bus)
  ];
  const missing = expected.filter(
    (name) => !chunks.some((chunk) => chunk.startsWith(name + "-")),
  );
  assert.deepEqual(missing, [], `missing island chunks: ${missing.join(", ")}`);
});
