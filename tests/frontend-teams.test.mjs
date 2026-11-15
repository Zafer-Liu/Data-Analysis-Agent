import assert from "node:assert/strict";
import test from "node:test";

const storage = new Map();
const root = { textContent: "" };
let tree;
const h = (tag, props, children) => ({ tag, props: props || {}, children });
globalThis.document = { documentElement: { lang: "en" }, addEventListener() {}, getElementById: id => id === "teams-panel-root" ? root : null,
  querySelectorAll: () => [], querySelector: () => null };
globalThis.__baaOverlayDocumentListenersRegistered = true;
globalThis.localStorage = { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) };
globalThis.BAA = { state: { SID: "s1" }, ui: { toast() {} } };
globalThis.window = globalThis;
window.Vue = { h, render: vnode => { tree = vnode; } };
const { createApiClient, ApiError } = await import("../frontend/core/api-client.js");
const { state } = await import("../frontend/core/runtime.js");
const { createRequestOwner } = await import("../frontend/core/request-state.js");
const { createTeamApi } = await import("../frontend/features/teams/api.js");
const { createTeamActions } = await import("../frontend/features/teams/team-actions.js");
const { createWorkflowActions } = await import("../frontend/features/teams/workflow-actions.js");
const { teamHasRunningMembers } = await import("../frontend/features/teams/shared.js");
const { teams } = await import("../frontend/features/teams.js");
const deferred = () => { let resolve; let reject; const promise = new Promise((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject }; };
const json = data => new Response(JSON.stringify({ ok: true, ...data }), { headers: { "content-type": "application/json" } });
const flush = () => new Promise(resolve => setImmediate(resolve));
const text = node => node == null ? "" : Array.isArray(node) ? node.map(text).join(" ") : typeof node === "object" ? text(node.children) : String(node);

function fixture(fetchImpl) {
  const local = { teams: [{ name: "analysts", members: [] }], selected: "analysts", selectedParticipant: "leader", team: null,
    clearing: false, deleting: "", error: "", workflowsError: "", selectedRun: "old-run", runDetail: null,
    workflowStarting: "", workflowCreating: false, workflowDeleting: "", workflowGeneratingCandidates: "",
    workflowInputs: {}, workflowInputSavedAt: {}, workflowExpanded: {}, workflowApprovalForms: {},
    workflowCreate: { creationMode: "template", template: "analysis" }, workflowArtifactContents: {} };
  const requests = createRequestOwner({ getScope: () => state.SID });
  const api = createTeamApi({ local, requests, renderPanel() {}, client: createApiClient({ fetchImpl }) });
  return { local, api, requests };
}

test("team facade uses the shared client, retains envelope metadata and prevents duplicate POSTs", async () => {
  state.SID = "api-session";
  const pending = deferred();
  let calls = 0;
  const { api } = fixture(() => { calls += 1; return pending.promise; });
  const writing = api.fetchJson("/write", { method: "POST", body: { goal: "销售分析" } });
  await assert.rejects(api.fetchJson("/write", { method: "POST" }), { code: "REQUEST_BUSY" });
  pending.resolve(json({ run: { id: "r1" } }));
  assert.equal((await writing).run.id, "r1");
  assert.equal(calls, 1);
  const { api: failure } = fixture(async () => new Response(JSON.stringify({ ok: false, code: "REVIEW_BLOCKED", error: "Evidence missing" }),
    { headers: { "content-type": "application/json", "x-request-id": "review-id" } }));
  await assert.rejects(failure.fetchTeams(), error => error instanceof ApiError && error.code === "REVIEW_BLOCKED" && error.requestId === "review-id");
});

test("team facade discards a late read after the session changes", async () => {
  state.SID = "read-old";
  const pending = deferred();
  const { api } = fixture(() => pending.promise);
  const reading = api.fetchTeam("analysts");
  state.SID = "read-new";
  pending.resolve(json({ team: { name: "old data" } }));
  await assert.rejects(reading, { code: "STALE_REQUEST" });
});

test("team confirmation is locked before the dialog and cannot delete from a new session", async () => {
  state.SID = "delete-old";
  const confirmation = deferred();
  let confirmations = 0;
  let requestsSent = 0;
  BAA.ui.confirm = () => { confirmations += 1; return confirmation.promise; };
  const { local, api } = fixture(async () => { requestsSent += 1; return json({}); });
  const actions = createTeamActions({ local, api, fetchJson: api.fetchJson, renderPanel() {}, refresh: async () => {} });
  const deleting = actions.dissolveTeam("analysts");
  await actions.dissolveTeam("analysts");
  assert.equal(confirmations, 1);
  state.SID = "delete-new";
  confirmation.resolve(true);
  await deleting;
  assert.equal(requestsSent, 0);
});

test("workflow creation stops between side effects when the session changes", async () => {
  state.SID = "create-old";
  const pending = deferred();
  const sent = [];
  const { local, api } = fixture((url) => { sent.push(url); return pending.promise; });
  const actions = createWorkflowActions({ local, api, renderPanel() {}, refreshWorkflows: async () => {} });
  const creating = actions.createWorkflowFromTemplate();
  await actions.createWorkflowFromTemplate();
  assert.equal(sent.length, 1);
  state.SID = "create-new";
  pending.resolve(json({ profile: { id: "p1" } }));
  await creating;
  assert.equal(sent.length, 1);
  assert.equal(local.workflowsError, "");
});

test("workflow publication errors are visible and do not start a workflow", async () => {
  state.SID = "publish";
  const sent = [];
  const { local, api } = fixture(async (url) => {
    sent.push(url);
    if (url.endsWith("/validate")) return json({});
    return new Response(JSON.stringify({ ok: false, code: "PUBLISH_FAILED", error: "Definition is incomplete" }),
      { status: 409, headers: { "content-type": "application/json" } });
  });
  const actions = createWorkflowActions({ local, api, renderPanel() {}, refreshWorkflows: async () => { throw new Error("must not refresh after failure"); } });
  await actions.publishSavedWorkflowDraft({ id: "wf1" });
  assert.equal(local.workflowsError, "Definition is incomplete");
  assert.equal(local.workflowCreating, false);
  assert.equal(sent.length, 2);
});

test("late workflow write and follow-up read preserve a newly selected run", async () => {
  state.SID = "candidate";
  const pending = deferred();
  const { local, api } = fixture(async url => url.endsWith("knowledge-candidates") ? pending.promise : json({ run: { id: "old-run" } }));
  const actions = createWorkflowActions({ local, api, renderPanel() {}, refreshWorkflows: async () => {} });
  const creating = actions.generateWorkflowKnowledgeCandidates({ id: "old-run" });
  local.selectedRun = "new-run";
  local.runDetail = { run: { id: "new-run" } };
  pending.resolve(json({}));
  globalThis.requestAnimationFrame = () => {};
  await creating;
  assert.equal(local.runDetail.run.id, "new-run");
  assert.equal(local.workflowGeneratingCandidates, "");
});

test("overlapping team selections render only the newest detail", async () => {
  state.SID = "panel-select";
  const first = deferred();
  globalThis.fetch = async url => {
    if (url.endsWith("/teams/first")) return first.promise;
    if (url.endsWith("/teams/second")) return json({ team: { name: "second", description: "second-current", members: [] } });
    return json({ plans: [] });
  };
  const a = teams.selectTeam("first");
  await teams.selectTeam("second");
  first.resolve(json({ team: { name: "first", description: "first-stale", members: [] } }));
  await a;
  assert.match(text(tree), /second-current/);
  assert.doesNotMatch(text(tree), /first-stale/);
});

test("an old team refresh cannot overwrite a selected team or a restored session", async () => {
  state.SID = "panel-refresh";
  const list = deferred();
  globalThis.fetch = async url => {
    if (url.endsWith("/teams")) return list.promise;
    if (url.endsWith("/teams/second")) return json({ team: { name: "second", description: "selected-during-refresh", members: [] } });
    return json({ plans: [] });
  };
  const reading = teams.refresh();
  await teams.selectTeam("second");
  list.resolve(json({ teams: [{ name: "first" }, { name: "second" }] }));
  await reading;
  assert.match(text(tree), /selected-during-refresh/);
  const old = deferred();
  globalThis.fetch = () => old.promise;
  const loadingOld = teams.refresh();
  state.SID = "panel-restored";
  old.resolve(json({ teams: [{ name: "stale-session-team" }] }));
  await loadingOld;
  await flush();
  assert.doesNotMatch(text(tree), /stale-session-team|selected-during-refresh/);
});

test("workflow run selections ignore old details and costs", async () => {
  state.SID = "panel-runs";
  const old = deferred();
  globalThis.fetch = async url => {
    if (url.includes("/workflow-runs/first")) return old.promise;
    if (url.endsWith("/costs")) return json({ costs: { total_tokens: 7 } });
    return json({ run: { id: "second", status: "succeeded" }, nodes: [], manifests: [], artifacts: [] });
  };
  const first = teams.selectWorkflowRun("first");
  await teams.selectWorkflowRun("second");
  old.resolve(json({ run: { id: "first-stale", status: "failed" } }));
  await first;
  teams.switchView("workflow");
  assert.match(text(tree), /second/);
  assert.doesNotMatch(text(tree), /first-stale/);
  teams.switchView("teams");
});

test("team loading can be cancelled with a visible retry without a stale result", async () => {
  state.SID = "panel-cancel";
  const pending = deferred();
  globalThis.fetch = () => pending.promise;
  const reading = teams.refresh();
  const walk = node => node == null ? [] : Array.isArray(node) ? node.flatMap(walk) : typeof node === "object" ? [node, ...walk(node.children)] : [];
  const cancel = walk(tree).find(node => node.tag === "button" && node.children === "Cancel");
  assert.ok(cancel);
  cancel.props.onClick();
  pending.resolve(json({ teams: [{ name: "cancelled-stale" }] }));
  await reading;
  assert.match(text(tree), /Loading cancelled/);
  assert.ok(walk(tree).some(node => node.tag === "button" && node.children === "Retry"));
  assert.doesNotMatch(text(tree), /cancelled-stale/);
});

test("review blockers and executor exit acknowledgements remain visible", async () => {
  state.SID = "review-state";
  const member = { name: "analyst", status: "running", execution: { cancel_requested: true, exit_acknowledged: false },
    quality_review: { decision: "rework", issues: ["Missing source"], missing_evidence: ["Refund denominator"] } };
  assert.equal(teamHasRunningMembers({ members: [{ ...member, status: "idle" }] }), true);
  assert.equal(teamHasRunningMembers({ members: [{ ...member, status: "completed", execution: { exit_acknowledged: true } }] }), false);
  globalThis.fetch = async url => url.includes("/team-plans") ? json({ plans: [] })
    : json({ team: { name: "reviewers", members: [member] } });
  await teams.selectTeam("reviewers");
  assert.match(text(tree), /Quality review requires changes/);
  assert.match(text(tree), /Refund denominator/);
  assert.match(text(tree), /Resources remain in use until the executor exits/);
});

test("an old workflow follow-up read cannot replace details after selecting away and back", async () => {
  state.SID = "run-selection-generation";
  const detail = deferred();
  const { local, api } = fixture(async url => url.endsWith("/template") ? json({}) : detail.promise);
  local.runSelection = 0;
  const actions = createWorkflowActions({ local, api, renderPanel() {}, refreshWorkflows: async () => {} });
  const saving = actions.saveWorkflowTemplate({ id: "old-run" });
  await flush();
  local.selectedRun = "other-run"; local.runSelection += 1;
  local.selectedRun = "old-run"; local.runSelection += 1;
  local.runDetail = { run: { id: "old-run", marker: "new selection" } };
  detail.resolve(json({ run: { id: "old-run", marker: "old follow-up" } }));
  await saving;
  assert.equal(local.runDetail.run.marker, "new selection");
});
