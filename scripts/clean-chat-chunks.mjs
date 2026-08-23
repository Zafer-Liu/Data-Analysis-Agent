#!/usr/bin/env node
// Remove stale hashed chunks in static/dist/chunks that chat-app.js no longer references.
// Skipped (with a notice) when the running app locks a chunk file on Windows.
import { existsSync, readFileSync, readdirSync, unlinkSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(root, "static", "dist");
const appJs = path.join(dist, "chat-app.js");
const chunksDir = path.join(dist, "chunks");

if (!existsSync(appJs) || !existsSync(chunksDir)) {
  console.log("[clean-chat-chunks] nothing to clean");
  process.exit(0);
}

const refs = new Set(
  [...readFileSync(appJs, "utf-8").matchAll(/chunks\/[A-Za-z0-9._-]+\.js/g)]
    .map((m) => m[0].slice("chunks/".length)),
);

let removed = 0;
for (const f of readdirSync(chunksDir)) {
  if (f.endsWith(".js") && !refs.has(f)) {
    try {
      unlinkSync(path.join(chunksDir, f));
      removed += 1;
    } catch {
      // File locked by the running app on Windows — skip, harmless.
    }
  }
}
console.log(`[clean-chat-chunks] removed ${removed} stale chunk(s)`);
