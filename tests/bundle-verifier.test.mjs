import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { verifyChatBundle } from "../scripts/verify-chat-bundle.mjs";

function fixture(t) {
  const root = mkdtempSync(path.join(os.tmpdir(), "baa-bundle-contract-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const dist = path.join(root, "static", "dist");
  mkdirSync(path.join(dist, "chunks"), { recursive: true });
  mkdirSync(path.join(dist, ".vite"));
  mkdirSync(path.join(root, "templates"));
  writeFileSync(path.join(root, "package.json"), JSON.stringify({ version: "1.5.2" }));
  writeFileSync(path.join(root, "templates", "agent_chat.html"), '<script type="module" src="{{ url_for(\'static\', filename=\'dist/chat-app.js\', v=\'v1.5.2\') }}"></script>\n');
  writeFileSync(path.join(dist, "chat-app.js"), [
    "chunks/", "__baaAppLoaded", "__baaOverlayStack", "__baaOverlayDocumentListenersRegistered",
    "__baaAppDelegationRegistered", "/static/dist/", " ".repeat(200001),
  ].join("\n"));
  writeFileSync(path.join(dist, "chunks", "registry.js"), "// shared registry\n");
  writeFileSync(path.join(dist, "chunks", "panel.js"), "// dynamic panel\n");
  const manifest = {
    "frontend/entries/chat-app.js": {
      file: "chat-app.js", isEntry: true,
      imports: ["_registry.js"], dynamicImports: ["frontend/features/ui/panel.js"],
    },
    "_registry.js": { file: "chunks/registry.js" },
    "frontend/features/ui/panel.js": { file: "chunks/panel.js", isDynamicEntry: true, imports: ["_registry.js"] },
  };
  const save = () => writeFileSync(path.join(dist, ".vite", "manifest.json"), JSON.stringify(manifest));
  save();
  return { root, dist, manifest, save };
}

test("valid graph and matching template version pass", (t) => {
  const { root } = fixture(t);
  assert.deepEqual(verifyChatBundle(root), []);
});

test("missing dynamic resource is rejected", (t) => {
  const { root, dist } = fixture(t);
  rmSync(path.join(dist, "chunks", "panel.js"));
  assert.ok(verifyChatBundle(root).some((error) => error.includes("missing bundle resource: chunks/panel.js")));
});

test("manifest reverse dependency on the entry is rejected", (t) => {
  const { root, manifest, save } = fixture(t);
  manifest["frontend/features/ui/panel.js"].imports.push("frontend/entries/chat-app.js");
  save();
  assert.ok(verifyChatBundle(root).some((error) => error.includes("reverse entry dependency")));
});

test("literal reverse import is rejected even if absent from the manifest graph", (t) => {
  const { root, dist } = fixture(t);
  writeFileSync(path.join(dist, "chunks", "panel.js"), 'import("../chat-app.js?v=v1.5.2");\n');
  assert.ok(verifyChatBundle(root).some((error) => error.includes("reverse entry import")));
});

test("dangling dependency and missing manifest fail clearly", (t) => {
  const { root, dist, manifest, save } = fixture(t);
  manifest["_registry.js"].imports = ["unknown.js"];
  save();
  assert.ok(verifyChatBundle(root).some((error) => error.includes("missing manifest dependency")));
  rmSync(path.join(dist, ".vite", "manifest.json"));
  assert.ok(verifyChatBundle(root).some((error) => error.includes("cannot read chat build manifest")));
});

test("null and array manifests are not accepted as dependency graphs", (t) => {
  const { root, dist } = fixture(t);
  for (const invalid of [null, []]) {
    writeFileSync(path.join(dist, ".vite", "manifest.json"), JSON.stringify(invalid));
    assert.ok(verifyChatBundle(root).some((error) => error.includes("manifest must be an object")));
  }
});

test("template cache version must match package metadata", (t) => {
  const { root } = fixture(t);
  writeFileSync(path.join(root, "package.json"), JSON.stringify({ version: "1.5.3" }));
  assert.ok(verifyChatBundle(root).some((error) => error.includes("does not match package version")));
});

test("duplicate entry loading and escaping resources are rejected", (t) => {
  const { root, manifest, save } = fixture(t);
  const file = path.join(root, "templates", "agent_chat.html");
  writeFileSync(file, readFileSync(file, "utf8").repeat(2));
  manifest["_registry.js"].assets = ["../../outside.js"];
  save();
  const errors = verifyChatBundle(root);
  assert.ok(errors.some((error) => error.includes("exactly once")));
  assert.ok(errors.some((error) => error.includes("escapes static/dist")));
});
