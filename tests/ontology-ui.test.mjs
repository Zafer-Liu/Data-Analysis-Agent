import assert from "node:assert/strict";
import test from "node:test";

class Element {
  constructor() { this.innerHTML = ""; this.textContent = ""; this.value = ""; this.dataset = {}; this.attrs = {}; this.listeners = {};
    this.classList = { add() {}, remove() {} }; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  setAttribute(key, value) { this.attrs[key] = value; }
  removeAttribute(key) { delete this.attrs[key]; }
  querySelector(selector) { return controls.get(selector) || null; }
  querySelectorAll() { return []; }
}
const controls = new Map([
  ["[data-ontology-replace]", new Element()], ["[data-ontology-source]", new Element()],
  ["[data-ontology-mapping]", new Element()],
]);
const elements = new Map();
for (const id of ["kb-preview-list", "kb-ontology-list", "kb-parsing", "kb-preview-area", "kb-preview-title", "kb-preview-sub"]) elements.set(id, new Element());
globalThis.document = { getElementById: (id) => elements.get(id), addEventListener() {}, querySelectorAll: () => [] };
globalThis.__baaOverlayDocumentListenersRegistered = true;
globalThis.BAA = { ui: { toast() {} } };
const { createOntologyPanel } = await import("../frontend/features/ontology.js");
const data = (name) => ({ source_name: `${name}.yaml`, summary: { domain_name: name, version: "1.0.0", entities: 1 },
  ontology: { domain: { name }, entities: {} }, sources: [], warnings: [] });
function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}
function options(request, persist = async () => {}) {
  return { request, persist, categoryId: () => 1, sourceUrl: (path) => `${path}?session_id=test` };
}
function ordinary(path) { return path.includes("ontology-sources") ? { sources: [] } : []; }

test("cancelled preview does not render stale import controls", async () => {
  const pending = deferred();
  const panel = createOntologyPanel(options(() => pending.promise));
  const showing = panel.showPreview(data("old"));
  panel.reset();
  pending.resolve([]);
  await showing;
  assert.equal(panel.isPreview(), false);
  assert.doesNotMatch(elements.get("kb-preview-list").innerHTML, /data-ontology-replace/);
});

test("completion of an old save preserves a newer preview", async () => {
  const pending = deferred();
  const submissions = [];
  let first = true;
  const request = async (path, init) => {
    if (path === "/api/knowledge/confirm") {
      submissions.push(JSON.parse(init.body));
      if (first) { first = false; return pending.promise; }
      return { ontology: { name: "new" } };
    }
    return ordinary(path);
  };
  const panel = createOntologyPanel(options(request));
  await panel.showPreview(data("old"));
  const saving = panel.confirm("old-upload.yaml");
  panel.reset();
  await panel.showPreview(data("new"));
  pending.resolve({ ontology: { name: "old" } });
  await saving;
  assert.equal(panel.isPreview(), true);
  await panel.confirm("new-upload.yaml");
  assert.equal(submissions[0].source_name, "old.yaml");
  assert.equal(submissions[1].source_name, "new.yaml");
  assert.equal(panel.isPreview(), false);
});

test("local persistence failure retries only the local copy without repeating confirmation", async () => {
  let fail = true;
  let confirmations = 0;
  const request = async (path) => {
    if (path === "/api/knowledge/confirm") { confirmations += 1; return { ontology: { name: "saved" } }; }
    return ordinary(path);
  };
  const persist = async () => { if (fail) throw new Error("storage full"); };
  const panel = createOntologyPanel(options(request, persist));
  await panel.showPreview(data("saved"));
  await assert.rejects(panel.confirm("upload.yaml"), /storage full/);
  assert.equal(panel.isPreview(), true);
  fail = false;
  await panel.confirm("upload.yaml");
  assert.equal(confirmations, 1);
  assert.equal(panel.isPreview(), false);
});

test("late ontology lists and errors cannot replace the new scope", async () => {
  const pending = deferred();
  let scope = "old";
  const panel = createOntologyPanel({ ...options(() => pending.promise), getScope: () => scope });
  const loading = panel.load();
  scope = "new";
  elements.get("kb-ontology-list").innerHTML = "new scope";
  pending.resolve([{ id: "old", name: "stale" }]);
  await loading;
  assert.equal(elements.get("kb-ontology-list").innerHTML, "new scope");
});

test("ontology loading has cancel and retry states and escapes failure text", async () => {
  const pending = deferred();
  const panel = createOntologyPanel(options(() => pending.promise));
  const root = elements.get("kb-ontology-list");
  delete root.dataset.ontologyInstalled;
  panel.install();
  const loading = panel.load();
  assert.match(root.innerHTML, /data-ontology-global="cancel"/);
  const cancel = { dataset: { ontologyGlobal: "cancel" } };
  await root.listeners.click({ target: { closest: selector => selector === "[data-ontology-global]" ? cancel : null } });
  assert.match(root.innerHTML, /data-ontology-global="reload"/);
  assert.equal(root.attrs["aria-busy"], undefined);
  pending.resolve([]);
  await loading;
  assert.match(root.innerHTML, /data-ontology-global="reload"/);
  const failing = createOntologyPanel(options(async () => { throw new Error('<img onerror="bad">'); }));
  await failing.load();
  assert.doesNotMatch(root.innerHTML, /<img/);
  assert.match(root.innerHTML, /&lt;img/);
  assert.match(root.innerHTML, /role="alert"/);
});

test("a saved ontology from a previous session never persists into the next one", async () => {
  const pending = deferred();
  let scope = "old";
  let persisted = 0;
  const panel = createOntologyPanel({ ...options(async path => path === "/api/knowledge/confirm" ? pending.promise : ordinary(path), async () => { persisted += 1; }), getScope: () => scope });
  await panel.showPreview(data("old"));
  const saving = panel.confirm("old.yaml");
  scope = "new";
  pending.resolve({ ontology: { name: "old" } });
  assert.equal(await saving, null);
  assert.equal(persisted, 0);
});
