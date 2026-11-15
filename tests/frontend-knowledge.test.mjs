import assert from "node:assert/strict";
import test from "node:test";

class Element {
  constructor() { this.textContent = ""; this.innerHTML = ""; this.value = ""; this.dataset = {}; this.disabled = false; this.attrs = {};
    const classes = new Set(); this.classList = { add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name) }; }
  addEventListener() {}
  querySelectorAll() { return []; }
  setAttribute(key, value) { this.attrs[key] = value; }
  removeAttribute(key) { delete this.attrs[key]; }
}
const elements = new Map();
for (const id of ["kb-preview-list", "kb-confirm-label", "kb-ontology-list", "kb-parsing", "kb-preview-area", "kb-preview-title", "kb-preview-sub",
  "kb-import-err", "kb-import-ok", "kb-file-input", "kb-drop-zone", "kb-files-list", "sb-panel-knowledge"]) elements.set(id, new Element());
elements.get("sb-panel-knowledge").classList.add("collapsed");
const confirm = new Element();
globalThis.document = { body: { dataset: { cloudManaged: "0" } }, documentElement: { lang: "en" }, addEventListener() {},
  getElementById: id => elements.get(id), querySelectorAll: () => [], querySelector: selector => selector === '[data-action="kbConfirmImport"]' ? confirm : null };
globalThis.__baaOverlayDocumentListenersRegistered = true;
globalThis.window = globalThis;
globalThis.sessionStorage = { getItem: () => "" };
globalThis.MutationObserver = class { observe() {} };
let formValues;
let active = 1;
let opened;
let closed = 0;
const lists = { metrics: [], rules: [], notes: [] };
const statuses = {};
const toasts = [];
const island = { isAvailable: () => true, sync() {}, getActiveCategoryId: () => active,
  setCategories: (items, id) => { active = id; }, setCategoryErr() {}, setCategoryBusy() {}, setTab() {},
  setActiveCategory: id => { active = id; }, setItems: (type, items) => { lists[type] = items; },
  setListStatus: (type, value) => { statuses[type] = value; }, getItem: (type, id) => lists[type].find(item => item.id === id),
  updateItem: (type, id, patch) => Object.assign(lists[type].find(item => item.id === id) || {}, patch),
  removeItem: (type, id) => { lists[type] = lists[type].filter(item => item.id !== id); },
  openForm: value => { opened = value; }, closeForm: () => { closed += 1; }, getFormValues: () => structuredClone(formValues), setFormBusy() {}, setFormErr() {} };
globalThis.BAA = { state: { SID: "initial" }, ui: { knowledge: island, toast: value => toasts.push(value), confirm: async () => true }, sidebar: {} };
const { state } = await import("../frontend/core/runtime.js");
const { knowledge, installKnowledgePanel } = await import("../frontend/features/knowledge.js");
installKnowledgePanel();
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
const flush = () => new Promise(resolve => setImmediate(resolve));
const fallback = url => url.includes("categories") ? json([{ id: 1, name: "Retail" }, { id: 2, name: "Other" }]) : json([]);

test("a late knowledge list cannot overwrite the new session, including A-B-A", async () => {
  state.SID = "list-old";
  const pending = deferred();
  globalThis.fetch = () => pending.promise;
  const reading = knowledge.kbRefresh("metrics");
  state.SID = "list-other";
  state.SID = "list-old";
  globalThis.fetch = async () => json([{ id: 2, name: "current", enabled: true }]);
  await knowledge.kbRefresh("metrics");
  pending.resolve(json([{ id: 1, name: "stale", enabled: true }]));
  await reading;
  assert.equal(lists.metrics[0].name, "current");
});

test("knowledge toggles lock one record and suppress late mutation feedback", async () => {
  state.SID = "toggle";
  lists.metrics = [{ id: 1, name: "Revenue", enabled: true }];
  const pending = deferred();
  let sent = 0;
  globalThis.fetch = async url => { if (url.includes("/toggle?")) { sent += 1; return pending.promise; } return fallback(url); };
  // The island facade is deliberately exercised through its registered callbacks.
  let callbacks;
  island.sync = value => { callbacks = value; };
  installKnowledgePanel();
  const toggling = callbacks.onToggle("metrics", 1);
  await callbacks.onToggle("metrics", 1);
  assert.equal(sent, 1);
  state.SID = "toggle-other";
  const before = toasts.length;
  pending.resolve(json({ id: 1, enabled: false }));
  await toggling;
  assert.deepEqual(lists.metrics, []);
  assert.equal(toasts.length, before);
});

test("closing an edit before its read finishes cannot reopen the form", async () => {
  state.SID = "edit";
  const pending = deferred();
  opened = undefined;
  globalThis.fetch = () => pending.promise;
  const editing = knowledge.kbOpenForm("metrics", 1);
  knowledge.kbCancelForm();
  pending.resolve(json([{ id: 1, name: "old edit" }]));
  await editing;
  assert.equal(opened, undefined);
});

test("duplicate form saves send once and an old save cannot close the new form", async () => {
  state.SID = "form";
  formValues = { type: "metrics", mode: "add", editId: null, body: { name: "first" } };
  const pending = deferred();
  let saves = 0;
  globalThis.fetch = async (url, init) => {
    if (init.method === "POST") { saves += 1; return pending.promise; }
    return fallback(url);
  };
  await knowledge.kbOpenForm("metrics");
  const saving = knowledge.kbSubmitForm();
  await knowledge.kbSubmitForm();
  assert.equal(saves, 1);
  knowledge.kbCancelForm();
  await knowledge.kbOpenForm("notes");
  const closedBefore = closed;
  pending.resolve(json({ id: 7, name: "first" }));
  await saving;
  assert.equal(closed, closedBefore);
  assert.equal(opened.type, "notes");
});

test("uploaded preview ignores a late response after the workspace changes", async () => {
  state.SID = "upload";
  const pending = deferred();
  globalThis.fetch = () => pending.promise;
  const uploading = knowledge.kbOnFileSelect({ target: { files: [new File(["test"], "knowledge.xlsx")] } });
  knowledge.kbOnWorkspaceChanged();
  pending.resolve(json({ format: "structured", filename: "old.xlsx", preview: [{ table: "metrics", name: "stale preview" }] }));
  await uploading;
  assert.equal(elements.get("kb-preview-list").innerHTML, "");
  assert.equal(elements.get("kb-preview-area").classList.contains("hidden"), true);
});

test("ordinary import retries local persistence without a second server confirmation", async () => {
  state.SID = "import";
  document.body.dataset.cloudManaged = "0";
  let confirmations = 0;
  globalThis.fetch = async (url, init) => {
    if (url.includes("/parse?")) return json({ format: "structured", filename: "knowledge.xlsx", preview: [{ table: "metrics", name: "Revenue" }] });
    if (url.includes("/confirm?")) { confirmations += 1; return json({ ok: true, inserted: { metrics: 1 }, rag: { chunks: 2 } }); }
    return fallback(url);
  };
  await knowledge.kbOnFileSelect({ target: { files: [new File(["test"], "knowledge.xlsx")] } });
  document.body.dataset.cloudManaged = "1";
  await knowledge.kbConfirmImport();
  assert.equal(confirmations, 1);
  assert.match(elements.get("kb-import-err").textContent, /local copy/);
  assert.match(elements.get("kb-confirm-label").textContent, /Retry local/);
  document.body.dataset.cloudManaged = "0";
  await knowledge.kbConfirmImport();
  assert.equal(confirmations, 1);
  assert.match(elements.get("kb-import-ok").textContent, /Imported: 1 metrics/);
  assert.equal(confirm.disabled, false);
  await flush();
});

test("file-list failure escapes server text and offers an explicit retry", async () => {
  state.SID = "files";
  globalThis.fetch = async () => json({ error: '<img src=x onerror="alert(1)">' }, 500);
  await knowledge.kbLoadFiles();
  const html = elements.get("kb-files-list").innerHTML;
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
  assert.match(html, /data-action="kbLoadFiles"/);
  assert.equal(elements.get("kb-files-list").attrs["aria-busy"], undefined);
});
