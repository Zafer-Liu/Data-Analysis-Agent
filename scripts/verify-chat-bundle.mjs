#!/usr/bin/env node
// Verify the built chat-app bundle: existence, size, chunk refs, overlay markers, Vite base guard.
// Path note: this script lives in scripts/, so "../chat-app.js" resolves to static/dist/chat-app.js.
import { existsSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appJs = path.join(root, "static", "dist", "chat-app.js");

const required = [
  "chunks/",                                  // hashed chunk references
  "__baaOverlayStack",                        // overlay debug state marker
  "__baaOverlayDocumentListenersRegistered",  // overlay listener guard marker
  "__baaAppDelegationRegistered",             // app-level delegation guard marker
  "/static/dist/",                            // Vite base guard
];

const errors = [];
if (!existsSync(appJs)) {
  errors.push(`chat-app.js missing at ${appJs}`);
} else if (statSync(appJs).size < 200000) {
  errors.push("chat-app.js too small (<200KB)");
} else {
  const text = readFileSync(appJs, "utf-8");
  for (const marker of required) {
    if (!text.includes(marker)) errors.push(`missing marker: ${marker}`);
  }
}

if (errors.length) {
  console.error("[verify-chat-bundle] FAIL:\n  " + errors.join("\n  "));
  process.exit(1);
}
console.log("[verify-chat-bundle] chat-app bundle OK");
