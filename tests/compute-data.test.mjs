import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = globalThis;
globalThis.document = { addEventListener() {}, documentElement: { lang: "en" } };
globalThis.BAA = { state: { SID: "session-a" } };
const { createComputeDataController, buildComputeBudget, trainingConnectionReadiness, trainingTransferReview, executionStateLabel, safeComputeDownload } = await import("../frontend/features/compute-data.js");
const { ApiError, createApiClient } = await import("../frontend/core/api-client.js");
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const readyConnection = () => ({ id: "gpu-a", purpose: "training", training_runner: { runner_ready: true, protocol_version: 1, families: ["Torch_MLP", "Torch_RNN"], expires_at: Date.now() / 1000 + 600 } });
const transferPlan = () => ({ plan_id: "plan-a", plan_hash: "hash-a", connection_id: "gpu-a", snapshot_id: "snapshot-a", params: { feature_columns: ["x"], target_column: "y" }, transfer_preview: { destination: "gpu.example", host_fingerprint: "SHA256:test", input_bytes: 4096, retention_hours: 24 }, budget: { max_run_seconds: 60 } });
const grant = () => ({ grant_id: "grant-a", expires_at: Date.now() / 1000 + 300 });
function fixture(handler = () => ({ ok: true })) {
  let scope = { sessionId: "session-a", workspaceId: "workspace-a" };
  let nextKey = 0;
  const calls = [];
  const controller = createComputeDataController({ client: { request(url, options) { calls.push({ url, ...options }); return Promise.resolve().then(() => handler(url, options)); } }, getContext: () => scope, idFactory: () => `key-${++nextKey}` });
  return { controller, state: controller.local, calls, setScope: value => { scope = value; } };
}
function trainingForm(controller) {
  controller.local.connections = [readyConnection()];
  for (const [field, value] of Object.entries({ datasetId: "dataset-a", snapshotId: "snapshot-a", connectionId: "gpu-a", targetColumn: "y", features: "x", seconds: "60" })) controller.updateTrainingForm(field, value);
}
function queryForm(controller) {
  controller.updateQueryForm("connectionId", "cluster-a");
  controller.updateQueryForm("sql", "SELECT sum(value) FROM sales.public.orders");
}

test("dataset upload sends the native file and options in one multipart plan request", async () => {
  const sent = [];
  const client = createApiClient({ fetchImpl: async (url, options) => { sent.push({ url, ...options }); return new Response(JSON.stringify({ ok: true, plan: { plan_id: "import-plan", plan_hash: "abc" } }), { headers: { "content-type": "application/json" } }); } });
  const controller = createComputeDataController({ client, getContext: () => ({ sessionId: "s/1", workspaceId: "w/1" }), idFactory: () => "import-key" });
  const file = new File(["id,value\n0001,1"], "业务.csv", { type: "text/csv" });
  file.arrayBuffer = () => { throw new Error("whole-file read forbidden"); };
  file.text = () => { throw new Error("whole-file text read forbidden"); };
  controller.chooseFile(file);
  controller.updateDatasetForm("idColumns", "id");
  controller.updateDatasetForm("nullValues", "NULL");
  await controller.planDataset();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].url, "/api/session/s%2F1/datasets/plans");
  assert.ok(sent[0].body instanceof FormData);
  assert.equal(sent[0].body.get("file"), file);
  assert.equal(sent[0].headers.has("Content-Type"), false);
  assert.equal(sent[0].body.get("workspace_id"), "w/1");
  const options = JSON.parse(sent[0].body.get("options"));
  assert.deepEqual(options.schema_policy.id_columns, ["id"]);
  assert.deepEqual(options.schema_policy.null_values, ["", "NULL"]);
  assert.equal(options.schema_policy.bad_rows, "error");
  assert.equal(JSON.parse(sent[0].body.get("budget")).max_input_bytes, 4294967296);
  assert.equal(controller.local.dataset.plan.plan_id, "import-plan");
});

test("workspace and existing-source plans use the real JSON API fields", async () => {
  const f = fixture(() => ({ ok: true, plan: { plan_id: "p" } }));
  f.controller.updateDatasetForm("sourceMode", "workspace");
  f.controller.updateDatasetForm("workspaceFile", "data/业务.parquet");
  await f.controller.planDataset();
  assert.equal(f.calls[0].body.workspace_file, "data/业务.parquet");
  assert.equal(f.calls[0].body.workspace_id, "workspace-a");
  assert.equal(f.calls[0].body.source, undefined);
  f.controller.updateDatasetForm("sourceMode", "source");
  f.controller.updateDatasetForm("sourceId", "source-a");
  await f.controller.planDataset();
  assert.equal(f.calls[1].body.source_id, "source-a");
});

test("invalid budgets and oversized or unsupported files never submit", async () => {
  const f = fixture();
  f.controller.chooseFile({ name: "large.csv", size: 5 * 1073741824 });
  await f.controller.planDataset();
  assert.match(f.state.errors.datasets.message, /exceeds/);
  f.controller.chooseFile({ name: "old.xls", size: 100 });
  await f.controller.planDataset();
  assert.match(f.state.errors.datasets.message, /XLSX/);
  assert.throws(() => buildComputeBudget({ seconds: "1.5" }));
  assert.equal(f.calls.length, 0);
});

test("import submit is locked and a lost receipt reuses its original key", async () => {
  const pending = deferred();
  let submits = 0;
  const f = fixture((url, options) => {
    if (url.endsWith("/datasets/imports")) {
      submits += 1;
      if (submits === 1) return pending.promise;
      return { ok: true, operation_id: "operation-a", job_id: "job-a", status: "queued" };
    }
    if (url.includes("/jobs/")) return { ok: true, status: "canceling" };
    return { ok: true };
  });
  Object.assign(f.state.dataset, { plan: { plan_id: "p" }, idempotencyKey: "import-key" });
  const first = f.controller.importDataset();
  await f.controller.importDataset();
  assert.equal(f.calls.length, 1);
  pending.reject(new ApiError("offline", { method: "POST", kind: "network" }));
  await first;
  assert.equal(f.state.dataset.submissionUnknown, true);
  await f.controller.refreshImport();
  assert.equal(f.calls[1].body.idempotency_key, "import-key");
  assert.equal(f.state.dataset.operation.operation_id, "operation-a");
  await f.controller.cancelImport();
  assert.equal(f.calls[2].url, "/api/session/session-a/jobs/job-a/cancel");
  assert.equal(executionStateLabel(f.state.dataset.operation), "cancel_pending");
});

test("version selection ignores older previews and activation uses the selected snapshot", async () => {
  const old = deferred();
  const f = fixture(url => url.includes("datasets/old/") ? old.promise : url.includes("/versions") ? { versions: [{ snapshot_id: "v2" }] } : { preview: { rows: [{ x: 2 }] } });
  const first = f.controller.selectDataset("old", "v1");
  await f.controller.selectDataset("new", "v2");
  old.resolve({ versions: [{ snapshot_id: "wrong" }], preview: { rows: [{ x: 0 }] } });
  await first;
  assert.equal(f.state.dataset.selected, "new");
  assert.deepEqual(f.state.dataset.preview.rows, [{ x: 2 }]);
  assert.deepEqual(f.state.dataset.versions, [{ snapshot_id: "v2" }]);
  await f.controller.activateDataset();
  const call = f.calls.at(-1);
  assert.equal(call.url, "/api/session/session-a/datasets/new/activate");
  assert.equal(call.body.snapshot_id, "v2");
});

test("scope changes drop late reads and plans without leaking prior records", async () => {
  const pending = deferred();
  const f = fixture(() => pending.promise);
  const read = f.controller.refreshDatasets();
  f.state.dataset.form.sourceMode = "workspace";
  f.state.dataset.form.workspaceFile = "input.csv";
  const write = f.controller.planDataset();
  f.setScope({ sessionId: "session-b", workspaceId: "workspace-b" });
  f.controller.scopeChanged();
  pending.resolve({ datasets: [{ dataset_id: "old" }], plan: { plan_id: "old" } });
  await Promise.all([read, write]);
  assert.deepEqual(f.state.datasets, []);
  assert.equal(f.state.dataset.plan, null);
  assert.equal(f.state.dataset.file, null);
  assert.deepEqual(f.state.errors, {});
});

test("write locks survive close and explicit reset in the same scope", async () => {
  const pending = deferred();
  const f = fixture(() => pending.promise);
  Object.assign(f.state.training, { plan: transferPlan(), reviewed: true });
  const first = f.controller.approveTraining();
  f.controller.close();
  f.controller.scopeChanged();
  Object.assign(f.state.training, { plan: transferPlan(), reviewed: true });
  await f.controller.approveTraining();
  assert.equal(f.calls.length, 1);
  assert.equal(f.state.busy["training-approve"], true);
  pending.resolve({ ok: true, grant: grant() });
  await first;
  assert.equal(f.state.training.grant, null);
  assert.equal(f.state.busy["training-approve"], false);
});

test("training preflight requires current protocol, families and expiry", () => {
  assert.equal(trainingConnectionReadiness({ connected: true }), "unavailable");
  assert.equal(trainingConnectionReadiness({ training_runner: { runner_ready: true } }), "unavailable");
  assert.equal(trainingConnectionReadiness(readyConnection()), "ready");
  const expired = readyConnection(); expired.training_runner.expires_at = Date.now() / 1000 - 1;
  assert.equal(trainingConnectionReadiness(expired), "expired");
  assert.equal(trainingTransferReview({ connection_id: "gpu-a" }).complete, false);
  assert.equal(trainingTransferReview(transferPlan()).complete, true);
});

test("training plan uses model controls and never approves implicitly", async () => {
  const f = fixture(() => ({ ok: true, plan: transferPlan() }));
  trainingForm(f.controller);
  f.controller.updateTrainingForm("family", "Torch_RNN");
  f.controller.updateTrainingForm("cell", "lstm");
  await f.controller.planTraining();
  assert.equal(f.calls.length, 1);
  const call = f.calls[0];
  assert.equal(call.url, "/api/session/session-a/training/plans");
  assert.equal(call.body.family, "Torch_RNN");
  assert.equal(call.body.params.cell, "lstm");
  assert.equal(call.body.params.window, 24);
  assert.equal(call.body.snapshot_id, "snapshot-a");
  assert.equal(call.body.connection_id, "gpu-a");
  assert.equal(call.body.retention_seconds, 86400);
  assert.equal(call.body.workspace_id, undefined);
  assert.equal(call.body.execution_target, undefined);
  assert.equal(call.body.params.feature_columns, undefined);
  assert.equal(f.state.training.grant, null);
  await f.controller.approveTraining();
  assert.equal(f.calls.length, 1);
  assert.match(f.state.errors.training.message, /acknowledgment/);
});

test("approval and submit each reject duplicate clicks and expiry is visible", async () => {
  const approval = deferred();
  const submission = deferred();
  const f = fixture(url => url.endsWith("/approve") ? approval.promise : submission.promise);
  Object.assign(f.state.training, { plan: transferPlan(), reviewed: true, idempotencyKey: "training-key" });
  const approve = f.controller.approveTraining();
  await f.controller.approveTraining();
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].body.expected_plan_hash, "hash-a");
  approval.resolve({ ok: true, grant: grant() });
  await approve;
  const submit = f.controller.submitTraining();
  await f.controller.submitTraining();
  assert.equal(f.calls.length, 2);
  assert.deepEqual(f.calls[1].body, { plan_id: "plan-a", grant_id: "grant-a", idempotency_key: "training-key" });
  submission.resolve({ ok: true, run: { run_id: "run-a", status: "queued" } });
  await submit;
  assert.equal(f.state.training.run.run_id, "run-a");
  const expired = fixture();
  Object.assign(expired.state.training, { plan: transferPlan(), reviewed: true, grant: { grant_id: "g", expires_at: Date.now() / 1000 - 1 } });
  await expired.controller.submitTraining();
  assert.equal(expired.calls.length, 0);
  assert.equal(expired.state.training.reviewed, false);
  assert.match(expired.state.errors.training.message, /expired/);
});

test("changed training form invalidates a late grant", async () => {
  const pending = deferred();
  const f = fixture(() => pending.promise);
  Object.assign(f.state.training, { plan: transferPlan(), reviewed: true });
  const first = f.controller.approveTraining();
  f.controller.updateTrainingForm("epochs", "30");
  pending.resolve({ grant: grant() });
  await first;
  assert.equal(f.state.training.grant, null);
  assert.equal(f.state.training.plan, null);
});

test("unknown training submission is reconciled by key without a second submit", async () => {
  let first = true;
  const f = fixture(() => {
    if (first) { first = false; return Promise.reject(new ApiError("lost receipt", { method: "POST", kind: "network" })); }
    return { ok: true, run: { run_id: "run-a", status: "running" } };
  });
  Object.assign(f.state.training, { plan: transferPlan(), reviewed: true, grant: grant(), idempotencyKey: "training-key" });
  await f.controller.submitTraining();
  await f.controller.submitTraining();
  assert.equal(f.calls.length, 1);
  await f.controller.refreshTrainingRun();
  assert.equal(f.calls[1].url, "/api/session/session-a/training/runs");
  assert.deepEqual(f.calls[1].body, f.calls[0].body);
  assert.equal(f.calls[1].body.idempotency_key, "training-key");
  assert.equal(f.state.training.run.run_id, "run-a");
  assert.equal(f.state.training.submissionUnknown, false);
});

test("cancellation is pending until exit acknowledgment and recovery stays distinct", async () => {
  const f = fixture(() => ({ ok: true, run: { status: "canceling", exit_acknowledged: false } }));
  f.state.training.run = { run_id: "run-a", status: "running" };
  const cancel = f.controller.cancelTraining();
  await f.controller.cancelTraining();
  await cancel;
  assert.equal(f.calls.length, 1);
  assert.equal(executionStateLabel(f.state.training.run), "cancel_pending");
  assert.equal(executionStateLabel({ status: "canceled", exit_acknowledged: false }), "cancel_pending");
  assert.equal(executionStateLabel({ status: "canceled", exit_acknowledged: true }), "exited");
  assert.equal(executionStateLabel({ status: "running", cancel_requested: true, recovery_phase: "recovery_pending" }), "recovery_pending");
});

test("cluster connection CRUD sends vault-only secrets, flat fields and scoped PATCH updates", async () => {
  const f = fixture(url => url.includes("connections?") ? { connections: [{ connection_id: "cluster-a", config: { name: "Warehouse", credential_configured: true } }] } : { ok: true, connection: { connection_id: "cluster-a" } });
  for (const [key, value] of Object.entries({ name: "Warehouse", host: "trino.example", catalog: "sales", tables: "sales.public.orders", auth: "basic", secret: "credential-reference" })) f.controller.updateClusterForm(key, value);
  await f.controller.saveClusterConnection();
  assert.equal(f.calls[0].url, "/api/datasource/cluster/connections");
  assert.equal(f.calls[0].body.session_id, "session-a");
  assert.equal(f.calls[0].body.workspace_id, "workspace-a");
  assert.equal(f.calls[0].body.secret, "credential-reference");
  assert.equal(f.calls[0].body.auth, "basic");
  assert.equal(f.calls[0].body.config, undefined);
  assert.equal(f.calls[0].body.credential_ref, undefined);
  assert.equal(f.state.cluster.form.secret, "");
  f.controller.editClusterConnection("cluster-a");
  assert.equal(f.state.cluster.form.secret, "");
  Object.assign(f.state.cluster.form, { name: "Warehouse", host: "trino.example", catalog: "sales", tables: "sales.public.orders" });
  await f.controller.saveClusterConnection();
  assert.equal(f.calls[2].method, "PATCH");
  f.controller.requestDeleteConnection("cluster-a");
  await f.controller.deleteClusterConnection();
  const deletion = f.calls.find(call => call.method === "DELETE");
  assert.equal(new URL(deletion.url, "https://local.invalid").searchParams.get("workspace_id"), "workspace-a");
});

test("cluster submit uses SQL, budgets and a single key even across close", async () => {
  const pending = deferred();
  const f = fixture(() => pending.promise);
  queryForm(f.controller);
  const first = f.controller.submitClusterQuery();
  await f.controller.submitClusterQuery();
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].body.sql, "SELECT sum(value) FROM sales.public.orders");
  assert.equal(f.calls[0].body.connection_id, "cluster-a");
  assert.deepEqual(f.calls[0].body.budget, { max_run_seconds: 120, max_rows: 10000, max_output_bytes: 8388608, batch_bytes: 262144, max_scan_bytes: null, max_cpu_seconds: null, strict_server: false });
  f.controller.close();
  assert.equal(f.state.cluster.submissionUnknown, true);
  pending.resolve({ query: { query_id: "query-a", status: "running" } });
  await first;
  assert.equal(f.state.cluster.query, null);
  assert.equal(f.state.busy["cluster-submit"], false);
  assert.equal(f.state.cluster.submissionUnknown, true);
});

test("cluster status and event requests carry scope and deduplicate sequence numbers", async () => {
  const f = fixture(url => url.includes("/events?") ? { events: [{ seq: 2, message: "running" }, { seq: 2, message: "updated" }, { seq: 1, message: "queued" }] } : { query: { query_id: "query-a", status: "running" } });
  f.controller.setLookup("cluster", "query-a");
  await f.controller.refreshClusterQuery();
  await f.controller.refreshClusterEvents();
  await f.controller.refreshClusterEvents();
  for (const call of f.calls) {
    const url = new URL(call.url, "https://local.invalid");
    assert.equal(url.searchParams.get("session_id"), "session-a");
    assert.equal(url.searchParams.get("workspace_id"), "workspace-a");
  }
  assert.equal(f.state.cluster.events.length, 2);
  assert.equal(f.state.cluster.events[1].message, "updated");
  assert.equal(new URL(f.calls.at(-1).url, "https://local.invalid").searchParams.get("after_seq"), "2");
});

test("cluster snapshots require complete results and cancellation reports pending", async () => {
  const f = fixture(url => url.endsWith("/cancel") ? { query: { status: "cancel_pending", exit_acknowledged: false } } : { snapshot: { dataset_id: "d", snapshot_id: "s" } });
  f.state.cluster.query = { query_id: "q", status: "failed", complete: false, fetch_started: true };
  await f.controller.snapshotClusterQuery();
  assert.equal(f.calls.length, 0);
  f.state.cluster.query = { query_id: "q", status: "running", complete: false, fetch_started: true };
  await f.controller.snapshotClusterQuery();
  assert.equal(f.calls.length, 0);
  await f.controller.cancelClusterQuery();
  assert.equal(executionStateLabel(f.state.cluster.query), "cancel_pending");
  Object.assign(f.state.cluster.query, { status: "completed", complete: true, cancel_requested: false, cancel_pending: false });
  await f.controller.snapshotClusterQuery();
  assert.equal(f.calls[1].url, "/api/datasource/cluster/queries/q/snapshot");
  assert.equal(f.calls[1].body.session_id, "session-a");
  assert.equal(f.state.cluster.snapshot.snapshot_id, "s");
});

test("permission errors expose request identity and cancelled reads ignore late payloads", async () => {
  const pending = deferred();
  let failed = true;
  const f = fixture(() => failed ? Promise.reject(new ApiError("Denied", { code: "cluster_permission_denied", status: 403, requestId: "request-a" })) : pending.promise);
  await f.controller.refreshClusterConnections();
  assert.deepEqual(f.state.errors.cluster, { code: "cluster_permission_denied", status: 403, message: "Denied", requestId: "request-a" });
  failed = false;
  const loading = f.controller.refreshClusterConnections();
  f.controller.cancelReads("cluster");
  pending.resolve({ connections: [{ connection_id: "late" }] });
  await loading;
  assert.deepEqual(f.state.clusterConnections, []);
  assert.equal(f.state.loading["cluster-connections"], false);
  assert.equal(f.state.cancelledReads.cluster, true);
});

test("download links accept registered same-origin API paths only", () => {
  assert.equal(safeComputeDownload("/api/artifacts/a/download"), "/api/artifacts/a/download");
  for (const href of ["javascript:alert(1)", "https://runner.invalid/a", "//runner.invalid/a", "/api/../outside", "/api/\\evil"]) assert.equal(safeComputeDownload(href), "");
});

test("default controller resolves the authorized workspace before cluster requests", async () => {
  const calls = [];
  const controller = createComputeDataController({ client: { async request(url, options) {
    calls.push({ url, ...options });
    return url.endsWith("/workspace") ? { ok: true, workspace: { mounted: true, workspace_id: "actual-workspace" } } : { ok: true, connections: [] };
  } } });
  assert.equal(controller.local.contextReady, false);
  await controller.open("cluster");
  assert.equal(controller.local.contextReady, true);
  assert.equal(calls[0].url, "/api/session/session-a/workspace");
  assert.equal(new URL(calls[1].url, "https://local.invalid").searchParams.get("workspace_id"), "actual-workspace");
});

test("default workspace resolution is discarded after a session switch", async () => {
  const pending = deferred();
  const calls = [];
  const controller = createComputeDataController({ client: { request(url) { calls.push(url); return pending.promise; } } });
  const first = controller.open("cluster");
  BAA.state.SID = "session-b";
  controller.scopeChanged();
  pending.resolve({ ok: true, workspace: { mounted: true, workspace_id: "old-workspace" } });
  await first;
  assert.equal(controller.local.contextReady, false);
  assert.equal(calls.length, 1);
  assert.deepEqual(controller.local.clusterConnections, []);
  BAA.state.SID = "session-a";
});

test("missing workspace metadata prevents submissions and remains recoverable", async () => {
  const calls = [];
  const controller = createComputeDataController({ client: { async request(url) { calls.push(url); return { ok: true }; } } });
  await controller.open("cluster");
  assert.equal(controller.local.contextReady, false);
  queryForm(controller);
  await controller.submitClusterQuery();
  assert.equal(calls.length, 1);
  assert.match(controller.local.errors.cluster.message, /workspace/);
});

test("the actual public training plan exposes fingerprint, full column scope and seconds retention", () => {
  const plan = { plan_id: "p", connection_id: "gpu", host_fingerprint: "SHA256:real", snapshot_id: "v", input_bytes: 1000,
    columns: ["id", "x", "y"], retention_seconds: 86400, budget: { max_run_seconds: 1800 } };
  const review = trainingTransferReview(plan, { host: "gpu.example" });
  assert.equal(review.complete, true);
  assert.equal(review.target, "gpu.example");
  assert.equal(review.retention, 24);
  assert.deepEqual(review.columns, ["id", "x", "y"]);
});

test("training events use the existing Jobs route and session sequence cursor", async () => {
  const f = fixture(() => ({ events: [{ sequence: 10, seq: 1, message: "epoch 1" }, { sequence: 11, seq: 2, message: "epoch 2" }], replay_truncated: true }));
  f.state.training.run = { run_id: "r", job_id: "j", status: "running" };
  await f.controller.refreshTrainingEvents();
  await f.controller.refreshTrainingEvents();
  const url = new URL(f.calls.at(-1).url, "https://local.invalid");
  assert.equal(url.pathname, "/api/session/session-a/jobs/events");
  assert.equal(url.searchParams.get("job_id"), "j");
  assert.equal(url.searchParams.get("after_sequence"), "11");
  assert.deepEqual(f.state.training.events.map(event => event.seq), [10, 11]);
  assert.equal(f.state.training.eventsTruncated, true);
});

test("server-unknown submissions keep their identity through close and explicit reconciliation", async () => {
  let first = true;
  const f = fixture(() => {
    if (first) { first = false; throw new ApiError("Remote state unknown", { status: 503, code: "remote_state_unknown", method: "POST" }); }
    return { run: { run_id: "r", job_id: "j", status: "running" } };
  });
  Object.assign(f.state.training, { plan: transferPlan(), grant: grant(), reviewed: true, idempotencyKey: "original-key" });
  await f.controller.submitTraining();
  assert.equal(f.state.training.submissionUnknown, true);
  f.controller.close();
  assert.equal(f.state.training.grant, null);
  await f.controller.refreshTrainingRun();
  assert.deepEqual(f.calls[1].body, f.calls[0].body);
  assert.equal(f.state.training.run.run_id, "r");
});

test("editing a definitively rejected cluster query creates a new submission key", async () => {
  const f = fixture(() => Promise.reject(new ApiError("SQL invalid", { status: 400, method: "POST", code: "invalid_cluster_sql" })));
  queryForm(f.controller);
  await f.controller.submitClusterQuery();
  const firstKey = f.calls[0].body.idempotency_key;
  f.controller.updateQueryForm("sql", "SELECT count(*) FROM sales.public.orders");
  await f.controller.submitClusterQuery();
  assert.notEqual(f.calls[1].body.idempotency_key, firstKey);
});

test("late permission errors do not appear in another workspace", async () => {
  const pending = deferred();
  const f = fixture(() => pending.promise);
  queryForm(f.controller);
  const first = f.controller.submitClusterQuery();
  f.setScope({ sessionId: "session-a", workspaceId: "workspace-b" });
  f.controller.scopeChanged();
  pending.reject(new ApiError("Old workspace denied", { status: 403, code: "cluster_permission_denied" }));
  await first;
  assert.equal(f.state.errors.cluster, undefined);
  assert.equal(f.state.cluster.submissionUnknown, false);
});
