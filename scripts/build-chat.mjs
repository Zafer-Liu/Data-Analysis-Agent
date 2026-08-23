#!/usr/bin/env node
// Chat-app build with guardrails: clean stale chunks -> vite build -> verify bundle.
// Prevents stale hashed chunks accumulating in static/dist/chunks (fe1 contract).
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const node = process.execPath;
const vite = path.join(root, "node_modules", "vite", "bin", "vite.js");

function run(label, cmd, args) {
  const r = spawnSync(cmd, args, { stdio: "inherit", cwd: root, shell: false });
  if (r.status !== 0) {
    console.error(`[build-chat] ${label} failed (exit ${r.status})`);
    process.exit(r.status ?? 1);
  }
}

run("clean-chat-chunks.mjs", node, [path.join(root, "scripts", "clean-chat-chunks.mjs")]);
run("vite.chat-app.config.js", node, [vite, "build", "--config", "vite.chat-app.config.js"]);
run("verify-chat-bundle.mjs", node, [path.join(root, "scripts", "verify-chat-bundle.mjs")]);
