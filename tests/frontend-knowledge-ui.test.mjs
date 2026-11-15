import assert from "node:assert/strict";
import test from "node:test";

const elements = new Map();
class Element {
  constructor() { this.innerHTML = ""; this.textContent = ""; this.disabled = false; this.attrs = {}; this.classList = { add() {}, remove() {}, toggle() {} }; }
  setAttribute(key, value) { this.attrs[key] = value; }
}
for (const id of ["kb-tabs", "kb-panel-metrics", "kb-panel-rules", "kb-panel-notes", "kb-form-body", "kb-panel-import", "kb-form-title", "kb-submit-btn"]) elements.set(id, new Element());
globalThis.document = { addEventListener() {}, getElementById: id => elements.get(id), documentElement: { lang: "en" } };
const h = (tag, props, children) => ({ tag, props: props || {}, children });
globalThis.BAA = {};
globalThis.window = globalThis;
window.Vue = { h, Fragment: "fragment", reactive: value => value, render: (node, root) => { root.vnode = node; } };
const { mountKnowledgeUi } = await import("../frontend/features/ui/knowledge-ui.js");
const { getUiIsland } = await import("../frontend/core/ui-registry.js");
mountKnowledgeUi();
const island = getUiIsland("knowledge");
const walk = (node, matches = []) => { if (Array.isArray(node)) node.forEach(child => walk(child, matches)); else if (node && typeof node === "object") { matches.push(node); walk(node.children, matches); } return matches; };
const nodes = id => walk(elements.get(id).vnode);
const strings = node => Array.isArray(node) ? node.map(strings).join(" ") : node && typeof node === "object" ? strings(node.children) : String(node ?? "");

test("knowledge loading is announced, cancel is reachable and failure exposes retry", () => {
  let cancelled;
  let retried;
  island.sync({ onCancelList: type => { cancelled = type; }, onSwitchTab: type => { retried = type; } });
  island.setListStatus("metrics", { loading: true });
  let panel = nodes("kb-panel-metrics");
  assert.equal(panel.find(node => node.props.id === "kb-list-metrics").props["aria-busy"], true);
  assert.ok(panel.some(node => node.props.role === "status"));
  const cancel = panel.find(node => node.tag === "button" && node.children === "Cancel");
  cancel.props.onClick();
  assert.equal(cancelled, "metrics");
  island.setListStatus("metrics", { loading: false, err: "Offline" });
  panel = nodes("kb-panel-metrics");
  assert.ok(panel.some(node => node.props.role === "alert"));
  panel.find(node => node.tag === "button" && node.children === "Retry").props.onClick();
  assert.equal(retried, "metrics");
});

test("knowledge toggles and category choices are native keyboard controls with pending locks", () => {
  island.setCategories([{ id: 1, name: "业务", enabled: true, pending: true }], 1);
  nodes("kb-tabs").find(node => node.props.class?.[0] === "kb-category-select").props.onClick({ stopPropagation() {} });
  const categoryNodes = nodes("kb-tabs");
  assert.ok(categoryNodes.some(node => node.tag === "button" && node.props["aria-pressed"] === true));
  assert.equal(categoryNodes.find(node => node.props.class === "kb-category-delete").props.disabled, true);
  island.setCategoryBusy(true);
  assert.equal(nodes("kb-tabs").find(node => node.props.class === "kb-category-add-btn").props.disabled, true);
  island.setItems("metrics", [{ id: 1, name: "Revenue", enabled: true, pending: true }]);
  const toggle = nodes("kb-panel-metrics").find(node => node.props.role === "switch");
  assert.equal(toggle.tag, "button");
  assert.equal(toggle.props.disabled, true);
  assert.equal(toggle.props["aria-checked"], true);
  assert.ok(toggle.props["aria-label"]);
});

test("knowledge form labels target controls and save stays disabled while pending", () => {
  island.openForm({ type: "metrics" });
  island.setFormBusy(true);
  const form = nodes("kb-form-body");
  for (const label of form.filter(node => node.tag === "label")) assert.ok(form.some(node => node.props.id === label.props.for));
  assert.equal(form.find(node => node.tag === "input").props.disabled, true);
  assert.equal(elements.get("kb-submit-btn").disabled, true);
  assert.equal(elements.get("kb-form-body").attrs["aria-busy"], "true");
  island.setFormErr("Required");
  assert.ok(nodes("kb-form-body").some(node => node.props.role === "alert"));
  island.closeForm();
  assert.equal(elements.get("kb-submit-btn").disabled, false);
});

test("empty and recovery status copy is bilingual without changing layout", () => {
  island.setItems("notes", []);
  assert.match(strings(elements.get("kb-panel-notes").vnode), /No background notes yet/);
  document.documentElement.lang = "zh";
  island.setItems("notes", []);
  assert.match(strings(elements.get("kb-panel-notes").vnode), /暂无背景知识/);
});
