import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const listeners = new EventTarget();
const elements = new Map();
class Element {
  constructor() {
    this.attrs = {};
    const classes = new Set();
    this.classList = { add: value => classes.add(value), remove: value => classes.delete(value), contains: value => classes.has(value) };
    this.isConnected = true;
    this.dataset = {};
  }
  setAttribute(key, value) { this.attrs[key] = value; }
  hasAttribute(key) { return key in this.attrs; }
  querySelector(selector) { return selector === ".modal" ? dialog : null; }
  querySelectorAll() { return []; }
  focus() { this.focused = true; }
}
const dialog = new Element();
for (const id of ["compute-data-root", "ov-compute-data", "job-history-root"]) elements.set(id, new Element());
globalThis.window = globalThis;
globalThis.document = {
  documentElement: { lang: "en" }, activeElement: null,
  addEventListener: listeners.addEventListener.bind(listeners), removeEventListener: listeners.removeEventListener.bind(listeners), dispatchEvent: listeners.dispatchEvent.bind(listeners),
  getElementById: id => elements.get(id), querySelector: () => null, querySelectorAll: () => [], contains: () => true,
};
globalThis.BAA = { state: { SID: "session-a", workspace_id: "workspace-a" } };
globalThis.requestAnimationFrame = callback => callback();
globalThis.getComputedStyle = () => ({ display: "block", visibility: "visible" });
let language = "en";
globalThis.localStorage = { getItem: () => language, setItem: (_, value) => { language = value; } };
if (!globalThis.CustomEvent) globalThis.CustomEvent = class extends Event { constructor(type, options) { super(type); this.detail = options?.detail; } };
const h = (tag, props, children) => ({ tag, props: props || {}, children });
globalThis.Vue = { h, render: (vnode, root) => { root.vnode = vnode; }, reactive: value => value };
await import("../frontend/legacy/i18n.js");
const { mountComputeDataUi } = await import("../frontend/features/ui/compute-data-ui.js");
const { getUiIsland } = await import("../frontend/core/ui-registry.js");
const { eventBus } = await import("../frontend/core/event-bus.js");
const { ApiError } = await import("../frontend/core/api-client.js");
const { mountJobHistoryUi } = await import("../frontend/features/ui/job-history-ui.js");
const { createGpuDomain } = await import("../frontend/legacy/settings/gpu.js");
const walk = (node, result = []) => { if (Array.isArray(node)) node.forEach(item => walk(item, result)); else if (node && typeof node === "object") { result.push(node); walk(node.children, result); } return result; };
const strings = node => Array.isArray(node) ? node.map(strings).join(" ") : node && typeof node === "object" ? strings(node.children) : String(node ?? "");
const nodes = () => walk(elements.get("compute-data-root").vnode);
const action = name => nodes().find(node => node.props["data-action"] === name);
const control = id => nodes().find(node => node.props.id === id);
const event = value => ({ target: { value, checked: value, files: value }, preventDefault() {} });
function setup(t, handler = () => ({ ok: true, datasets: [], connections: [] })) {
  setLang("en");
  let scope = { sessionId: "session-a", workspaceId: "workspace-a" };
  const calls = [];
  const island = mountComputeDataUi({ client: { request(url, options) { calls.push({ url, ...options }); return Promise.resolve().then(() => handler(url, options)); } }, getContext: () => scope, idFactory: () => "ui-key" });
  t.after(() => island.destroy());
  return { island, controller: island.controller, state: island.controller.local, calls, scope: value => { scope = value; } };
}

test("dataset form events send multipart plans, import references and version activation", async t => {
  const f = setup(t, url => url.endsWith("/plans") ? { ok: true, plan: { plan_id: "p", format: "csv", schema_policy: { columns: [{ name: "id", type: "string" }] } } }
    : url.endsWith("/imports") ? { ok: true, operation_id: "op", status: "queued", job_id: "job" }
      : url.includes("/versions?") ? { versions: [{ snapshot_id: "v1", status: "ready" }] }
        : url.includes("/preview?") ? { preview: { columns: ["id"], rows: [["0001"]] } }
          : { ok: true, datasets: [{ dataset_id: "d", name: "业务数据" }] });
  await f.island.open();
  control("compute-file").props.onChange(event([new File(["id\n0001"], "data.csv")]));
  await nodes().find(node => node.props["data-form"] === "datasets").props.onSubmit(event());
  const planning = f.calls.find(call => call.url.endsWith("/plans"));
  assert.ok(planning.body instanceof FormData);
  await action("importDataset").props.onClick();
  assert.equal(f.calls.find(call => call.url.endsWith("/imports")).body.plan_id, "p");
  await control("compute-browse-selected").props.onChange(event("d"));
  await control("compute-browse-snapshot").props.onChange(event("v1"));
  await action("activateDataset").props.onClick();
  assert.equal(f.calls.at(-1).body.snapshot_id, "v1");
  assert.ok(strings(elements.get("compute-data-root").vnode).includes("0001"));
});

test("all visible forms have associated labels, review is separate and empty states are bilingual", async t => {
  const f = setup(t);
  for (const tab of ["datasets", "training", "cluster"]) {
    await f.island.open(tab);
    const tree = nodes();
    for (const label of tree.filter(node => node.tag === "label")) assert.ok(tree.some(node => node.props.id === label.props.for), `${tab}: ${label.props.for}`);
    const shell = tree.find(node => node.props.class === "compute-data-shell");
    assert.equal(shell.props["aria-labelledby"], "compute-data-title");
    assert.equal(dialog.attrs.role, "dialog");
    assert.equal(tree.some(node => String(node.props.class || "").split(" ").includes("modal")), false, "the island must not nest a second dialog inside the template modal");
    assert.equal(tree.filter(node => node.props.role === "tab" && node.props["aria-selected"]).length, 1);
  }
  assert.match(strings(elements.get("compute-data-root").vnode), /No cluster connections yet/);
  setLang("zh");
  assert.match(strings(elements.get("compute-data-root").vnode), /尚无集群连接/);
  assert.equal(globalThis.t("compute.status.recovery_pending"), "待核实远端状态");
  setLang("en");
  assert.equal(globalThis.t("compute.status.exited"), "Executor exit confirmed");
});

test("training form uses snapshot, model and budget controls without silently approving", async t => {
  const ready = { id: "gpu", name: "GPU host", training_runner: { runner_ready: true, protocol_version: 1, families: ["Torch_MLP", "Torch_RNN"], expires_at: Date.now() / 1000 + 600 } };
  const plan = { plan_id: "p", plan_hash: "hash", snapshot_id: "v1", execution_target: { host: "gpu.example", host_fingerprint: "SHA256:abc" }, params: { feature_columns: ["x"], target_column: "y" }, input_bytes: 1000, retention_hours: 24, budget: { max_run_seconds: 60 } };
  const f = setup(t, url => url.endsWith("/training/plans") ? { plan } : url.endsWith("/approve") ? { grant: { grant_id: "g", expires_at: Date.now() / 1000 + 60 } } : url.endsWith("/training/runs") ? { run: { run_id: "r", status: "queued" } }
    : url.includes("/versions?") ? { versions: [{ snapshot_id: "v1", status: "ready" }] } : { datasets: [{ dataset_id: "d" }], connections: [ready] });
  await f.island.open("training");
  control("compute-training-connectionId").props.onChange(event("gpu"));
  await control("compute-training-datasetId").props.onChange(event("d"));
  control("compute-training-snapshotId").props.onChange(event("v1"));
  control("compute-training-family").props.onChange(event("Torch_RNN"));
  control("compute-training-cell").props.onChange(event("lstm"));
  control("compute-training-targetColumn").props.onInput(event("y"));
  control("compute-training-budget-seconds").props.onInput(event("60"));
  await nodes().find(node => node.props["data-form"] === "training").props.onSubmit(event());
  assert.equal(f.calls.find(call => call.url.endsWith("/training/plans")).body.params.cell, "lstm");
  assert.equal(f.calls.some(call => call.url.endsWith("/approve")), false);
  assert.equal(action("approveTraining").props.disabled, true);
  const content = strings(elements.get("compute-data-root").vnode);
  for (const value of ["gpu.example", "SHA256:abc", "v1", "x, y", "1,000", "24 hours"]) assert.ok(content.includes(value), value);
  control("compute-transfer-reviewed").props.onChange(event(true));
  await action("approveTraining").props.onClick();
  assert.equal(f.calls.filter(call => call.url.endsWith("/approve")).length, 1);
  await action("submitTraining").props.onClick();
  assert.equal(f.calls.find(call => call.url.endsWith("/training/runs")).body.grant_id, "g");
});

test("missing capability and transfer metadata never enables approval", async t => {
  const f = setup(t, () => ({ connections: [{ id: "legacy", name: "Old server", training_runner: { runner_ready: true } }], datasets: [] }));
  await f.island.open("training");
  const legacy = nodes().find(node => node.tag === "option" && node.props.value === "legacy");
  assert.equal(legacy.props.disabled, true);
  assert.match(legacy.children, /unavailable/i);
  f.state.training.plan = { plan_id: "p", plan_hash: "hash" };
  f.controller.setTransferReviewed(false);
  assert.equal(control("compute-transfer-reviewed").props.disabled, true);
  assert.equal(action("approveTraining").props.disabled, true);
});

test("cluster forms issue config CRUD, scoped SQL, events, cancellation and snapshots", async t => {
  const connection = { connection_id: "c", config: { name: "Warehouse", kind: "trino", host: "trino.example", port: 443, catalog: "sales", schema: "public", auth: "none", allowed_tables: ["sales.public.orders"] } };
  const f = setup(t, url => url.endsWith("/queries") ? { query: { query_id: "q", status: "running", complete: false } }
    : url.includes("queries/q/events?") ? { events: [{ seq: 1, message: "running" }] }
      : url.endsWith("/cancel") ? { query: { query_id: "q", status: "cancel_pending", exit_acknowledged: false } }
        : url.endsWith("/snapshot") ? { snapshot: { dataset_id: "d", snapshot_id: "v" } }
          : url.includes("queries/q?") ? { query: { query_id: "q", status: "completed", complete: true, row_count: 2 } }
            : url.endsWith("/connections") ? { connection } : { connections: [connection] });
  await f.island.open("cluster");
  for (const [key, value] of Object.entries({ name: "Warehouse", host: "trino.example", catalog: "sales", tables: "sales.public.orders" })) control(`compute-connection-${key}`).props.onInput(event(value));
  await nodes().find(node => node.props["data-form"] === "cluster-connection").props.onSubmit(event());
  const save = f.calls.find(call => call.url.endsWith("/connections") && call.method === "POST");
  assert.equal(save.body.kind, "trino");
  control("compute-query-connectionId").props.onChange(event("c"));
  control("compute-query-sql").props.onInput(event("SELECT count(*) FROM sales.public.orders"));
  await nodes().find(node => node.props["data-form"] === "cluster-query").props.onSubmit(event());
  assert.equal(f.calls.find(call => call.url.endsWith("/queries")).body.session_id, "session-a");
  assert.equal(action("snapshotQuery").props.disabled, false);
  assert.equal(f.state.cluster.snapshot, null);
  await action("refreshEvents").props.onClick();
  await action("cancelExecution").props.onClick();
  assert.match(strings(elements.get("compute-data-root").vnode), /exit pending/);
  await action("refreshStatus").props.onClick();
  assert.equal(action("snapshotQuery").props.disabled, false);
  await action("snapshotQuery").props.onClick();
  assert.equal(f.calls.at(-1).body.workspace_id, "workspace-a");
  assert.match(strings(elements.get("compute-data-root").vnode), /created a dataset snapshot/);
});

test("loading cancellation, permission, expiry and retry controls are visible", async t => {
  let resolve;
  let mode = "load";
  const f = setup(t, () => mode === "load" ? new Promise(yes => { resolve = yes; }) : mode === "deny" ? Promise.reject(new ApiError("Not allowed", { status: 403, code: "cluster_permission_denied", requestId: "request-x" })) : { connections: [] });
  const opening = f.island.open("cluster");
  await Promise.resolve(); await Promise.resolve();
  assert.ok(nodes().some(node => node.props.role === "status"));
  action("cancelLoading").props.onClick();
  resolve({ connections: [] });
  await opening;
  assert.match(strings(elements.get("compute-data-root").vnode), /Loading cancelled/);
  mode = "deny";
  await action("refresh").props.onClick();
  assert.ok(nodes().some(node => node.props.role === "alert"));
  assert.match(strings(elements.get("compute-data-root").vnode), /Ask its owner for access/);
  assert.match(strings(elements.get("compute-data-root").vnode), /request-x/);
  mode = "ok";
  await action("retry").props.onClick();
  assert.equal(f.state.errors.cluster, null);
});

test("workspace changes clear approval and late data, and tab keys keep ARIA selection coherent", async t => {
  const f = setup(t);
  await f.island.open("training");
  f.state.training.grant = { grant_id: "g" };
  f.state.training.reviewed = true;
  f.scope({ sessionId: "session-a", workspaceId: "workspace-b" });
  eventBus.emit("knowledge:scope-changed");
  assert.equal(f.state.training.grant, null);
  assert.equal(f.state.training.reviewed, false);
  const selected = nodes().find(node => node.props.role === "tab" && node.props["aria-selected"]);
  selected.props.onKeydown({ key: "ArrowRight", preventDefault() {} });
  assert.equal(f.state.tab, "cluster");
  assert.equal(nodes().find(node => node.props.role === "tabpanel").props["aria-labelledby"], "compute-tab-cluster");
});

test("GPU panel separates inference readiness and training-only preflight and links to training", async t => {
  const f = setup(t);
  const uiState = {
    gpuConnectionStatus: { a: { connected: true } }, gpuConnectionModels: {}, gpuConnections: [{ id: "a", name: "Training server", connection_type: "ssh", purpose: "training", host: "gpu.example", username: "user", port: 22, training_runner: { runner_ready: true } }],
    gpuConnectionForm: { connectionType: "ssh", purpose: "training", name: "", host: "", username: "", authMethod: "agent", targetPort: "8000" },
  };
  const gpu = createGpuDomain({ uiState, draw() {} });
  const tree = gpu.renderGpu();
  assert.match(strings(tree), /Inference not ready/);
  assert.match(strings(tree), /Training capabilities unavailable/);
  const buttons = walk(tree).filter(node => node.tag === "button");
  const preflight = buttons.find(node => node.children === "Training preflight");
  assert.ok(preflight, "training-only connections do not require an inference tunnel before preflight");
  await buttons.find(node => node.children === "Open training panel").props.onClick();
  assert.equal(f.state.tab, "training");
  assert.equal(f.state.training.form.connectionId, "a");
  assert.equal(walk(tree).some(node => node.tag === "input" && node.props["aria-label"] === "Inference service port"), false);
});

test("training-only GPU creation and host trust use the API and an in-app review", async t => {
  const originalFetch = globalThis.fetch;
  const previousConfirm = BAA.ui.confirm;
  const calls = [];
  let review;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, ...options });
    const payload = url.endsWith("/host-key") ? { ok: true, host_key: { fingerprint: "SHA256:test", type: "ssh-ed25519", base64: "public-key" } } : { ok: true, connections: [] };
    return new Response(JSON.stringify(payload), { headers: { "content-type": "application/json" } });
  };
  BAA.ui.confirm = async options => { review = options; return true; };
  t.after(() => { globalThis.fetch = originalFetch; BAA.ui.confirm = previousConfirm; });
  const uiState = { gpuConnectionStatus: {}, gpuConnectionModels: {}, gpuConnections: [{ id: "a", host: "gpu.example" }],
    gpuConnectionForm: { connectionType: "ssh", purpose: "training", name: "Training", host: "gpu.example", username: "user", port: "2222", targetPort: "8000", authMethod: "agent" } };
  const gpu = createGpuDomain({ uiState, draw() {} });
  await gpu.createGpuConnection();
  const creation = JSON.parse(calls[0].body);
  assert.equal(creation.purpose, "training");
  assert.equal(creation.port, 2222);
  assert.equal(creation.target_port, undefined);
  uiState.gpuConnections = [{ id: "a", host: "gpu.example" }];
  await gpu.trustGpuHost("a");
  assert.match(review.message, /gpu.example/);
  assert.match(review.message, /SHA256:test/);
  const trust = calls.find(call => call.url.endsWith("/trust-host-key"));
  assert.deepEqual(JSON.parse(trust.body), { key_type: "ssh-ed25519", key_base64: "public-key" });
  assert.equal(uiState.gpuRemoteBusy, false);
});

test("job history preserves recovery and exit states and links using business run identity", async t => {
  const f = setup(t, () => ({ runs: [], run: { run_id: "run-a", status: "running" } }));
  mountJobHistoryUi();
  const jobs = getUiIsland("jobHistory");
  jobs.setJobs([{ id: "j", status: "running", type: "remote_training", execution_backend: "remote", external_id: "task-a", recovery_phase: "recovery_pending", recovery_metadata: { run_id: "run-a" } }]);
  jobs.setOpen(true);
  let tree = elements.get("job-history-root").vnode;
  assert.match(strings(tree), /Remote recovery pending/);
  await walk(tree).find(node => node.tag === "button" && node.children === "View remote training").props.onClick();
  assert.equal(f.state.training.run.run_id, "run-a");
  jobs.setJobs([{ id: "j", status: "canceled", execution_backend: "remote", exit_acknowledged: true }]);
  tree = elements.get("job-history-root").vnode;
  assert.match(strings(tree), /Executor exit confirmed/);
  jobs.setOpen(false);
});

test("job cancellation preserves local terminal behavior and remote exit uncertainty", async () => {
  mountJobHistoryUi();
  const jobs = getUiIsland("jobHistory");
  jobs.setJobs([{ id: "local", status: "running", execution_backend: "local" }], { onCancel: async () => {} });
  jobs.setOpen(true);
  let tree = elements.get("job-history-root").vnode;
  await walk(tree).find(node => node.props.class === "job-cancel-btn").props.onClick();
  jobs.applyEvent({ job_id: "local", type: "job_canceled", status: "canceled" });
  tree = elements.get("job-history-root").vnode;
  assert.doesNotMatch(strings(tree), /exit pending/);
  assert.equal(walk(tree).some(node => node.props.class === "job-cancel-btn"), false);
  jobs.setJobs([{ id: "remote", status: "canceling", execution_backend: "remote", cancel_requested: true }]);
  jobs.applyEvent({ job_id: "remote", type: "job_canceled", status: "canceled", exit_acknowledged: false });
  assert.match(strings(elements.get("job-history-root").vnode), /exit pending/);
  jobs.applyEvent({ job_id: "remote", type: "job_canceled", status: "canceled", exit_acknowledged: true });
  assert.match(strings(elements.get("job-history-root").vnode), /Executor exit confirmed/);
  jobs.setOpen(false);
});

test("responsive and access styles stay scoped and source has no native dialog calls", () => {
  const css = readFileSync(new URL("../static/css/parts/compute-data.css", import.meta.url), "utf8");
  assert.match(css, /@media \(max-width: 900px\)/);
  assert.match(css, /@media \(max-width: 600px\)/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /forced-colors/);
  for (const path of ["../frontend/features/compute-data.js", "../frontend/features/ui/compute-data-ui.js"]) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    assert.doesNotMatch(source, /(?:window\.|globalThis\.)?(?:alert|confirm|prompt)\s*\(/);
    assert.doesNotMatch(source, /FileReader|\.arrayBuffer\(|\.text\(/);
  }
});
