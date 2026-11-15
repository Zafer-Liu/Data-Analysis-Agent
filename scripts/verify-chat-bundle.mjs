#!/usr/bin/env node
// Validate the Vite dependency graph and the template that loads the chat entry.
import { existsSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const required = [
  "chunks/",
  "__baaAppLoaded",
  "__baaOverlayStack",
  "__baaOverlayDocumentListenersRegistered",
  "__baaAppDelegationRegistered",
  "/static/dist/",
];

export function verifyChatBundle(root) {
  const errors = [];
  const dist = path.resolve(root, "static", "dist");

  function resource(file) {
    if (typeof file !== "string" || !file || file.includes("\\") || path.isAbsolute(file)) {
      errors.push(`invalid bundle resource: ${String(file)}`);
      return null;
    }
    const resolved = path.resolve(dist, file);
    const relative = path.relative(dist, resolved);
    if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
      errors.push(`bundle resource escapes static/dist: ${file}`);
      return null;
    }
    if (!existsSync(resolved) || !statSync(resolved).isFile()) {
      errors.push(`missing bundle resource: ${file}`);
      return null;
    }
    return resolved;
  }

  function json(file, label) {
    try {
      return JSON.parse(readFileSync(file, "utf8"));
    } catch (error) {
      errors.push(`cannot read ${label}: ${error.message}`);
      return null;
    }
  }

  function list(value, label) {
    if (value === undefined) return [];
    if (!Array.isArray(value) || value.some((entry) => typeof entry !== "string")) {
      errors.push(`invalid manifest list: ${label}`);
      return [];
    }
    return value;
  }

  const appJs = resource("chat-app.js");
  const bundleTexts = [];
  if (appJs) {
    if (statSync(appJs).size < 200000) errors.push("chat-app.js too small (<200KB)");
    const text = readFileSync(appJs, "utf8");
    bundleTexts.push(text);
    if (!text.includes("__baaAppLoaded")) errors.push("missing entry guard: __baaAppLoaded");
  }

  const manifest = json(path.join(dist, ".vite", "manifest.json"), "chat build manifest");
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
    errors.push("chat build manifest must be an object");
  } else {
    const entries = Object.entries(manifest);
    const chatEntries = entries.filter(([, chunk]) => chunk?.isEntry && chunk.file === "chat-app.js");
    if (chatEntries.length !== 1) errors.push("manifest must declare exactly one chat-app.js entry");
    for (const [key, chunk] of entries) {
      if (!chunk || typeof chunk !== "object" || Array.isArray(chunk)) {
        errors.push(`invalid manifest chunk: ${key}`);
        continue;
      }
      const file = resource(chunk.file);
      for (const asset of [...list(chunk.css, `${key}.css`), ...list(chunk.assets, `${key}.assets`)]) {
        resource(asset);
      }
      for (const dependency of [...list(chunk.imports, `${key}.imports`), ...list(chunk.dynamicImports, `${key}.dynamicImports`)]) {
        if (!Object.hasOwn(manifest, dependency)) {
          errors.push(`missing manifest dependency: ${key} -> ${dependency}`);
        } else if (chunk.file !== "chat-app.js" && manifest[dependency]?.file === "chat-app.js") {
          errors.push(`reverse entry dependency: ${key} -> chat-app.js`);
        }
      }
      if (file && chunk.file !== "chat-app.js" && chunk.file.endsWith(".js")) {
        const text = readFileSync(file, "utf8");
        bundleTexts.push(text);
        if (/(?:\bfrom\s*|\bimport\s*\(\s*)["'][^"']*chat-app\.js(?:\?[^"']*)?["']/.test(text)) {
          errors.push(`reverse entry import: ${chunk.file}`);
        }
      }
    }
  }

  const graphText = bundleTexts.join("\n");
  for (const marker of required) {
    if (!graphText.includes(marker)) errors.push(`missing marker in bundle graph: ${marker}`);
  }

  const metadata = json(path.join(root, "package.json"), "frontend package metadata");
  try {
    const template = readFileSync(path.join(root, "templates", "agent_chat.html"), "utf8");
    const tags = [...template.matchAll(/<script\b[^>]*>/gi)]
      .map((match) => match[0]).filter((tag) => tag.includes("dist/chat-app.js"));
    if (tags.length !== 1 || !/\btype\s*=\s*["']module["']/.test(tags[0])) {
      errors.push("template must load chat-app.js exactly once as a module");
    } else {
      const version = tags[0].match(/\bv\s*=\s*["']v?([^"']+)["']/)?.[1]
        ?? tags[0].match(/[?&]v=v?([^&"'\s<>]+)/)?.[1];
      if (!metadata?.version || version !== metadata.version) {
        errors.push(`template frontend version ${version ?? "missing"} does not match package version ${metadata?.version ?? "missing"}`);
      }
    }
  } catch (error) {
    errors.push(`cannot read chat template: ${error.message}`);
  }
  return errors;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const errors = verifyChatBundle(root);
  if (errors.length) {
    console.error("[verify-chat-bundle] FAIL:\n  " + errors.join("\n  "));
    process.exitCode = 1;
  } else {
    console.log("[verify-chat-bundle] chat bundle, dependency graph and template version OK");
  }
}
