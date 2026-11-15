import { ApiError, apiClient, isSilentRequestError, requestErrorMessage } from "../core/api-client.js";
import { createRequestOwner } from "../core/request-state.js";
import { getUiIsland } from "../core/ui-registry.js";

export function computeText(key, fallback, vars = {}) {
  const translated = globalThis.t?.(`compute.${key}`, vars);
  return String(translated && translated !== `compute.${key}` ? translated : fallback)
    .replace(/\{(\w+)\}/g, (match, name) => String(vars[name] ?? match));
}

const invalid = (key, fallback) => new ApiError(computeText(key, fallback), { code: "INVALID_FORM", kind: "request" });
const list = value => [...new Set(String(value || "").split(/[,\n]/).map(item => item.trim()).filter(Boolean))];
const enc = value => encodeURIComponent(String(value));
const record = value => value && typeof value === "object" && !Array.isArray(value) ? value : {};
const asList = (data, key) => Array.isArray(data?.[key]) ? data[key] : Array.isArray(data) ? data : [];
const idOf = (value, key) => value?.[key] || value?.id || "";
const terminal = value => ["succeeded", "completed", "failed", "canceled", "cancelled"].includes(value?.status || value?.state)
  && value?.recovery_phase !== "recovery_pending" && value?.phase !== "recovery_pending"
  && !((value?.cancel_pending || value?.cancel_requested || value?.cancel_intent || value?.exit_acknowledged === false)
    && ["canceled", "cancelled"].includes(value?.status || value?.state) && !value?.exit_acknowledged && !value?.cancel_confirmed);
let fallbackKey = 0;
const newKey = () => globalThis.crypto?.randomUUID?.() || `compute-${Date.now()}-${++fallbackKey}`;
export const defaultComputeContext = () => {
  const appState = globalThis.BAA?.state || {};
  return {
    sessionId: appState.SID || "",
    workspaceId: appState.workspace_id || appState.workspaceId || appState.workspace?.workspace_id || "",
  };
};

export function computeExpiry(value) {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  const time = Number.isFinite(numeric) ? numeric * (numeric < 1e12 ? 1000 : 1) : Date.parse(value);
  return Number.isFinite(time) ? time : null;
}

export function trainingConnectionReadiness(connection, now = Date.now()) {
  const runner = record(connection?.training_runner);
  const capabilities = record(runner.capabilities || runner.handshake || connection?.training_capabilities);
  const expires = computeExpiry(runner.expires_at ?? capabilities.expires_at ?? connection?.training_expires_at);
  const families = capabilities.families || capabilities.supported_families || capabilities.tasks || runner.families;
  const protocol = capabilities.protocol_version ?? runner.protocol_version ?? capabilities.protocol_versions;
  const supported = Array.isArray(families) ? families.map(item => typeof item === "string" ? item : item?.family).filter(Boolean) : [];
  if (expires !== null && expires <= now) return "expired";
  if (!supported.some(family => ["Torch_MLP", "Torch_RNN"].includes(family)) || !(protocol === 1 || Array.isArray(protocol) && protocol.includes(1)) || expires === null) return "unavailable";
  if (connection?.purpose && !["training", "both"].includes(connection.purpose)) return "not_ready";
  if (connection?.connection_type && connection.connection_type !== "ssh") return "unavailable";
  // Legacy runner_ready alone is insufficient: the M3 metadata above must also be present.
  return connection?.training_ready === true || runner.training_ready === true || runner.runner_ready === true ? "ready" : "not_ready";
}

export function buildComputeBudget(form) {
  const numeric = (key, multiplier = 1) => {
    const number = Number(form[key]);
    if (!Number.isSafeInteger(number) || number <= 0 || !Number.isSafeInteger(number * multiplier)) {
      throw invalid("budgetInvalid", "Enter positive whole numbers for every budget limit.");
    }
    return number * multiplier;
  };
  return {
    max_run_seconds: numeric("seconds"), max_rows: numeric("rows"),
    max_input_bytes: numeric("inputMiB", 1048576), max_output_bytes: numeric("outputMiB", 1048576),
    rss_bytes: numeric("memoryMiB", 1048576), temp_bytes: numeric("tempMiB", 1048576),
  };
}

function budgetDefaults() {
  return { seconds: "1800", rows: "10000000", inputMiB: "4096", outputMiB: "4096", memoryMiB: "2048", tempMiB: "8192" };
}

function initialState() {
  return {
    open: false, tab: "datasets", errors: {}, busy: {}, loading: {}, cancelledReads: {},
    datasets: [], connections: [], clusterConnections: [],
    dataset: {
      form: { sourceMode: "upload", workspaceFile: "", sourceId: "", schemaMode: "infer", idColumns: "", nullValues: "", timezone: "", ...budgetDefaults() },
      file: null, plan: null, operation: null, idempotencyKey: "", revision: 0, submissionUnknown: false,
      selected: "", snapshot: "", versions: [], files: [], preview: null, activation: null,
    },
    training: {
      form: { datasetId: "", snapshotId: "", family: "Torch_MLP", task: "classification", targetColumn: "", connectionId: "", epochs: "20", batchSize: "256", hiddenSize: "64", windowSize: "24", cell: "gru", timeColumn: "", sequenceColumn: "", forecastSteps: "12", seed: "42", retentionHours: "24", ...budgetDefaults() },
      revision: 0, snapshots: [], plan: null, grant: null, reviewed: false, run: null, runLookup: "", events: [], afterSeq: 0, eventsTruncated: false, idempotencyKey: "", submissionUnknown: false, submission: null,
    },
    cluster: {
      form: { name: "", kind: "trino", host: "", port: "443", username: "baa_readonly", auth: "none", secret: "", tables: "", catalog: "", schema: "default", tls: true, resourceGroup: "" },
      editingId: "", deleteId: "", queryForm: { connectionId: "", sql: "", seconds: "120", rows: "10000", outputMiB: "8", batchKiB: "256", scanMiB: "", cpuSeconds: "", strictServer: false },
      revision: 0, submissionRevision: null, query: null, queryLookup: "", idempotencyKey: "", submissionUnknown: false,
      events: [], afterSeq: 0, eventsTruncated: false, test: null, snapshot: null,
    },
  };
}

export function buildClusterBudget(form) {
  const positive = (key, multiplier = 1) => {
    const value = Number(form[key]);
    if (!Number.isSafeInteger(value) || value < 1 || !Number.isSafeInteger(value * multiplier)) throw invalid("budgetInvalid", "Enter positive whole numbers for every budget limit.");
    return value * multiplier;
  };
  return { max_run_seconds: positive("seconds"), max_rows: positive("rows"), max_output_bytes: positive("outputMiB", 1048576),
    batch_bytes: positive("batchKiB", 1024), max_scan_bytes: String(form.scanMiB || "").trim() ? positive("scanMiB", 1048576) : null,
    max_cpu_seconds: String(form.cpuSeconds || "").trim() ? positive("cpuSeconds") : null, strict_server: Boolean(form.strictServer) };
}

export function canSnapshotClusterQuery(query) {
  const status = query?.status || query?.state;
  return Boolean(query && idOf(query, "query_id") && !query.cancel_requested && !query.error
    && !["failed", "cancelled", "canceled", "cancel_pending", "remote_state_unknown", "submitting"].includes(status)
    && (query.complete === true || query.fetch_started !== true && ["running", "queued", "succeeded", "completed", "fetching"].includes(status)));
}

export function trainingTransferReview(plan, connection) {
  const transfer = record(plan?.transfer_preview || plan?.transfer || plan?.data_transfer);
  const input = record(plan?.input || plan?.dataset || plan?.input_manifest);
  const target = record(plan?.target || plan?.execution_target);
  const review = {
    target: transfer.destination || transfer.target_label || target.name || target.host || connection?.host || plan?.connection_id || "",
    fingerprint: transfer.host_fingerprint || target.host_fingerprint || target.fingerprint || plan?.host_fingerprint || "",
    snapshot: transfer.snapshot_id || input.snapshot_id || plan?.snapshot_id || "",
    columns: transfer.columns || input.columns || plan?.columns || plan?.params?.feature_columns || [],
    bytes: transfer.input_bytes ?? transfer.size_bytes ?? input.size_bytes ?? plan?.input_bytes ?? null,
    seconds: plan?.budget?.max_run_seconds ?? transfer.max_run_seconds ?? null,
    retention: transfer.retention_hours ?? plan?.retention_hours ?? (Number.isFinite(plan?.retention_seconds) ? plan.retention_seconds / 3600 : null),
  };
  if (plan?.params?.target_column && Array.isArray(review.columns)) review.columns = [...new Set([...review.columns, plan.params.target_column])];
  review.complete = Boolean(review.target && review.fingerprint && review.snapshot && Array.isArray(review.columns) && review.columns.length
    && Number.isFinite(review.bytes) && review.bytes >= 0 && Number.isFinite(review.seconds) && review.seconds > 0
    && Number.isFinite(review.retention) && review.retention > 0);
  return review;
}

export function executionStateLabel(execution) {
  if (execution?.recovery_phase === "recovery_pending" || execution?.phase === "recovery_pending" || execution?.status === "recovery_pending") return "recovery_pending";
  const status = execution?.status || execution?.state || "unknown";
  if ((execution?.exit_acknowledged === true || execution?.cancel_confirmed === true) && ["canceled", "cancelled", "cancel_pending", "canceling"].includes(status)) return "exited";
  if (!terminal(execution) && (execution?.cancel_requested || execution?.cancel_intent || execution?.cancel_pending || ["cancel_pending", "canceling", "canceled", "cancelled"].includes(status))) return "cancel_pending";
  return status;
}

export function safeComputeDownload(href) {
  if (typeof href !== "string" || !href.startsWith("/api/") || href.includes("\\") || [...href].some(char => char.charCodeAt(0) < 32)) return "";
  try {
    const parsed = new URL(href, "https://baa.invalid");
    return parsed.origin === "https://baa.invalid" && parsed.pathname.startsWith("/api/") ? href : "";
  } catch { return ""; }
}

export function createComputeDataController({ client = apiClient, getContext = defaultComputeContext, onChange = () => {}, idFactory = newKey } = {}) {
  const local = initialState();
  const usesDefaultContext = getContext === defaultComputeContext;
  let resolvedWorkspace = null;
  const context = () => {
    const value = getContext() || {};
    const sessionId = String(value.sessionId || "");
    const workspaceId = usesDefaultContext && resolvedWorkspace?.sessionId === sessionId ? resolvedWorkspace.workspaceId : String(value.workspaceId || "");
    return { sessionId, workspaceId };
  };
  const scopeReady = () => !usesDefaultContext || resolvedWorkspace?.sessionId === context().sessionId;
  local.contextReady = scopeReady();
  const scope = () => JSON.stringify(Object.values(context()));
  const requests = createRequestOwner({ getScope: scope });
  const writeKeys = new Set();
  let currentScope = scope();
  let selection = 0;
  const notify = () => {
    local.contextReady = scopeReady();
    for (const key of writeKeys) local.busy[key] = requests.hasWrite(key);
    onChange(local);
  };

  function syncScope() {
    const next = scope();
    if (next === currentScope) return false;
    const { open, tab } = local;
    currentScope = next;
    requests.reset();
    selection += 1;
    Object.assign(local, initialState(), { open, tab });
    return true;
  }

  function requireScope() {
    if (!scopeReady()) throw invalid("scopeUnavailable", "The current workspace has not been verified. Refresh the panel before retrying.");
  }
  function sessionPath(suffix) {
    const { sessionId } = context();
    if (!sessionId) throw invalid("sessionRequired", "Open a session before using data and compute.");
    if (suffix !== "workspace") requireScope();
    return `/api/session/${enc(sessionId)}/${suffix}`;
  }
  const scopedBody = payload => { requireScope(); return { ...payload, workspace_id: context().workspaceId }; };
  const clusterPath = suffix => `/api/datasource/cluster/${suffix}`;
  const scopedQuery = path => {
    requireScope();
    const value = context();
    if (!value.sessionId) throw invalid("sessionRequired", "Open a session before using data and compute.");
    const params = new URLSearchParams({ session_id: value.sessionId, workspace_id: value.workspaceId });
    return `${path}${path.includes("?") ? "&" : "?"}${params}`;
  };

  function showError(area, error) {
    local.errors[area] = { message: requestErrorMessage(error), code: error?.code || "REQUEST_FAILED", status: error?.status || 0, requestId: error?.requestId || "" };
    if (area === "training" && /grant.*expir|expir.*grant|grant_invalid/i.test(`${error?.code} ${error?.message}`)) {
      local.training.grant = null;
      local.training.reviewed = false;
    }
  }

  async function read(key, area, url, accept, isCurrent = () => true) {
    syncScope();
    const ticket = requests.beginRead(key);
    local.loading[key] = true;
    local.cancelledReads[area] = false;
    local.errors[area] = null;
    notify();
    try {
      const data = await client.request(url, { signal: ticket.signal, responseType: "json", requireOk: true });
      if (!ticket.isCurrent() || !isCurrent()) return null;
      accept(data || {});
      return data;
    } catch (error) {
      if (ticket.isCurrent() && isCurrent() && !isSilentRequestError(error)) showError(area, error);
      return null;
    } finally {
      if (ticket.isCurrent()) local.loading[key] = false;
      ticket.finish();
      syncScope();
      notify();
    }
  }

  async function write(key, area, url, body, accept, { isCurrent = () => true, method = "POST", started, unknown } = {}) {
    syncScope();
    const ticket = requests.beginWrite(key);
    if (!ticket) return null;
    writeKeys.add(key);
    local.errors[area] = null;
    started?.();
    notify();
    try {
      const data = await client.request(url, { method, body, responseType: "json", requireOk: true, timeoutMs: 0 });
      if (!ticket.isCurrent() || !isCurrent()) return null;
      accept(data || {});
      return data;
    } catch (error) {
      if (ticket.isCurrent() && isCurrent() && !isSilentRequestError(error)) {
        unknown?.(Boolean(error?.outcomeUnknown || error?.status >= 500 || ["remote_state_unknown", "submission_unknown"].includes(error?.code)));
        showError(area, error);
      }
      return null;
    } finally {
      ticket.finish();
      syncScope();
      notify();
    }
  }

  async function validate(area, action) {
    syncScope();
    try { return await action(); }
    catch (error) { showError(area, error); notify(); return null; }
  }

  function cancelReads(area) {
    const keys = area === "datasets" ? ["datasets", "dataset-status", "dataset-versions", "dataset-preview"]
      : area === "training" ? ["datasets", "training-connections", "training-snapshots", "training-status", "training-events"]
        : ["cluster-connections", "cluster-status", "cluster-events"];
    for (const key of ["compute-context", ...keys]) { requests.cancelRead(key); local.loading[key] = false; }
    local.cancelledReads[area] = true;
    notify();
  }

  function updateDatasetForm(field, value) {
    syncScope();
    if (local.dataset.submissionUnknown || local.dataset.operation && !terminal(local.dataset.operation)) return;
    local.dataset.form[field] = value;
    local.dataset.revision += 1;
    local.dataset.plan = null;
    local.dataset.idempotencyKey = "";
    notify();
  }

  function chooseFile(file) {
    syncScope();
    if (local.dataset.submissionUnknown || local.dataset.operation && !terminal(local.dataset.operation)) return;
    local.dataset.file = file || null;
    updateDatasetForm("sourceMode", "upload");
  }

  async function planDataset() {
    return validate("datasets", () => {
      const item = local.dataset;
      if (item.submissionUnknown || item.operation) throw invalid("importFirst", "Check the current import, then choose Prepare another import.");
      const revision = item.revision;
      const budget = buildComputeBudget(item.form);
      const options = {
        infer_types: item.form.schemaMode === "infer",
        schema_policy: { null_values: ["", ...String(item.form.nullValues).split("\n").filter(Boolean)], id_columns: list(item.form.idColumns),
          ...(item.form.timezone.trim() ? { source_timezone: item.form.timezone.trim() } : {}), bad_rows: "error" },
      };
      let body;
      if (item.form.sourceMode === "upload") {
        if (!item.file) throw invalid("fileRequired", "Choose a file to upload.");
        if (item.file.size > budget.max_input_bytes) throw invalid("fileBudget", "The file exceeds the input budget. Increase the budget or choose a smaller file.");
        if (!/\.(csv|parquet|xlsx)$/i.test(item.file.name)) throw invalid("fileFormat", "Choose a CSV, Parquet or XLSX file.");
        // Send the native File. Never read, parse or hash the entire file in the browser.
        body = new FormData();
        body.append("file", item.file);
        body.append("workspace_id", context().workspaceId);
        body.append("options", JSON.stringify(options));
        body.append("budget", JSON.stringify(budget));
      } else if (item.form.sourceMode === "workspace") {
        if (!item.form.workspaceFile.trim()) throw invalid("workspaceFileRequired", "Enter a file reference from the authorized workspace.");
        body = scopedBody({ workspace_file: item.form.workspaceFile.trim(), options, budget });
      } else if (item.form.sourceMode === "source") {
        if (!item.form.sourceId.trim()) throw invalid("sourceRequired", "Enter an existing authorized file source reference.");
        body = scopedBody({ source_id: item.form.sourceId.trim(), options, budget });
      } else throw invalid("fileRequired", "Choose a file to upload.");
      return write("dataset-plan", "datasets", sessionPath("datasets/plans"), body, data => {
        item.plan = data.plan || data;
        item.idempotencyKey = idFactory();
      }, { isCurrent: () => item === local.dataset && revision === item.revision });
    });
  }

  async function importDataset() {
    return validate("datasets", () => {
      const item = local.dataset;
      const planId = idOf(item.plan, "plan_id");
      if (!planId) throw invalid("planRequired", "Create and review a server plan first.");
      if (item.operation) return null;
      return write("dataset-import", "datasets", sessionPath("datasets/imports"), scopedBody({ plan_id: planId, idempotency_key: item.idempotencyKey }), data => {
        item.operation = data.operation || data;
        item.submissionUnknown = false;
      }, { isCurrent: () => item === local.dataset, started: () => { item.submissionUnknown = true; }, unknown: value => { item.submissionUnknown = value; } });
    });
  }

  async function refreshImport() {
    return validate("datasets", () => {
      const item = local.dataset;
      const id = idOf(item.operation, "operation_id");
      if (!id) return item.submissionUnknown ? importDataset() : null;
      return read("dataset-status", "datasets", scopedQuery(sessionPath(`datasets/imports/${enc(id)}`)), data => {
        item.operation = data.operation || data;
      }, () => item === local.dataset && id === idOf(item.operation, "operation_id"));
    });
  }

  async function cancelImport() {
    return validate("datasets", () => {
      const item = local.dataset;
      const id = item.operation?.job_id;
      if (!id || terminal(item.operation) || item.operation.cancel_requested) return null;
      return write("dataset-cancel", "datasets", sessionPath(`jobs/${enc(id)}/cancel`), scopedBody({}), data => {
        item.operation = { ...item.operation, status: data.status || "canceling", cancel_requested: true };
      }, { isCurrent: () => item === local.dataset });
    });
  }

  function newDatasetImport() {
    syncScope();
    if (local.dataset.submissionUnknown || local.dataset.operation && !terminal(local.dataset.operation) || requests.hasWrite("dataset-import")) return;
    Object.assign(local.dataset, { plan: null, operation: null, idempotencyKey: "", submissionUnknown: false });
    local.dataset.revision += 1;
    notify();
  }

  async function refreshDatasets() {
    return validate("datasets", () => read("datasets", "datasets", scopedQuery(sessionPath("datasets")), data => { local.datasets = asList(data, "datasets"); }));
  }

  async function selectDataset(datasetId, snapshotId = "") {
    syncScope();
    const item = local.dataset;
    const version = ++selection;
    Object.assign(item, { selected: datasetId, snapshot: snapshotId, versions: [], files: [], preview: null, activation: null });
    notify();
    if (!datasetId) return null;
    return validate("datasets", () => Promise.all([
      read("dataset-versions", "datasets", scopedQuery(sessionPath(`datasets/${enc(datasetId)}/versions`)), data => { item.versions = asList(data, "versions"); }, () => version === selection),
      read("dataset-preview", "datasets", scopedQuery(sessionPath(`datasets/${enc(datasetId)}/preview?${new URLSearchParams({ snapshot_id: snapshotId, limit: "50" })}`)), data => { item.preview = data.preview || data; }, () => version === selection),
    ]));
  }

  async function loadDatasetFiles() {
    return validate("datasets", () => {
      const item = local.dataset;
      if (!item.selected) throw invalid("datasetRequired", "Select a dataset first.");
      const version = selection;
      const parameters = new URLSearchParams({ snapshot_id: item.snapshot });
      return read("dataset-files", "datasets", sessionPath(`datasets/${enc(item.selected)}/files?${parameters}`), data => {
        item.files = asList(data, "files");
      }, () => version === selection);
    });
  }

  async function activateDataset() {
    return validate("datasets", () => {
      const item = local.dataset;
      if (!item.selected) throw invalid("datasetRequired", "Select a dataset to activate.");
      const version = selection;
      return write("dataset-activate", "datasets", sessionPath(`datasets/${enc(item.selected)}/activate`), scopedBody({ snapshot_id: item.snapshot }), data => {
        item.activation = data;
      }, { isCurrent: () => version === selection });
    });
  }

  function updateTrainingForm(field, value) {
    syncScope();
    const item = local.training;
    if (item.submissionUnknown || item.run && !terminal(item.run)) return;
    item.form[field] = value;
    if (field === "datasetId") { item.form.snapshotId = ""; item.snapshots = []; }
    item.revision += 1;
    item.plan = null;
    item.grant = null;
    item.reviewed = false;
    notify();
  }

  function setTransferReviewed(value) { syncScope(); local.training.reviewed = Boolean(value); notify(); }

  async function refreshTrainingConnections() {
    return validate("training", () => read("training-connections", "training", "/api/gpu/connections", data => { local.connections = asList(data, "connections"); }));
  }

  async function loadTrainingSnapshots(datasetId) {
    updateTrainingForm("datasetId", datasetId);
    if (!datasetId) return null;
    const item = local.training;
    return validate("training", () => read("training-snapshots", "training", scopedQuery(sessionPath(`datasets/${enc(datasetId)}/versions`)), data => {
      item.snapshots = asList(data, "versions");
    }, () => item === local.training && datasetId === item.form.datasetId));
  }

  async function planTraining() {
    return validate("training", () => {
      const item = local.training;
      if (item.submissionUnknown || item.run && !terminal(item.run)) throw invalid("reconcileFirst", "Check the previous submission before creating another execution.");
      if (requests.hasWrite("training-approve") || requests.hasWrite("training-submit")) return null;
      const form = item.form;
      if (!form.datasetId || !form.snapshotId || !form.connectionId || !form.targetColumn.trim()) throw invalid("trainingRequired", "Choose a snapshot, training connection and target column.");
      const connection = local.connections.find(item => idOf(item, "connection_id") === form.connectionId);
      if (trainingConnectionReadiness(connection) !== "ready") throw invalid("trainingUnavailable", "Refresh the training preflight in GPU settings. Current training capabilities are unavailable or expired.");
      if (!["Torch_MLP", "Torch_RNN"].includes(form.family)) throw invalid("familyInvalid", "Choose a supported training algorithm.");
      const positive = (value, maximum) => {
        const number = Number(value);
        if (!Number.isSafeInteger(number) || number < 1 || number > maximum) throw invalid("trainingLimits", "Check the training parameter limits.");
        return number;
      };
      const params = { target_column: form.targetColumn.trim(), epochs: positive(form.epochs, 500),
        batch_size: positive(form.batchSize, 1024), hidden_size: positive(form.hiddenSize, 256), seed: Number(form.seed) };
      if (!Number.isSafeInteger(params.seed) || params.seed < 0 || params.seed > 2147483647) throw invalid("seedInvalid", "Enter a non-negative whole number for the random seed.");
      if (form.family === "Torch_MLP") {
        if (!["classification", "regression"].includes(form.task)) throw invalid("familyInvalid", "Choose a supported training algorithm.");
        params.mode = form.task;
      } else {
        params.window = positive(form.windowSize, 512);
        params.steps = positive(form.forecastSteps, 1024);
        if (!["gru", "lstm"].includes(form.cell)) throw invalid("familyInvalid", "Choose a supported training algorithm.");
        Object.assign(params, { cell: form.cell, time_column: form.timeColumn.trim(), sequence_column: form.sequenceColumn.trim() });
      }
      const revision = item.revision;
      // Training derives owner/workspace from the session; its strict API accepts no client scope fields.
      const body = { dataset_id: form.datasetId, snapshot_id: form.snapshotId, family: form.family, params,
        connection_id: form.connectionId, budget: buildComputeBudget(form), retention_seconds: positive(form.retentionHours, 168) * 3600 };
      item.plan = null;
      item.grant = null;
      item.reviewed = false;
      return write("training-plan", "training", sessionPath("training/plans"), body, data => {
        item.plan = data.plan || data;
        item.idempotencyKey = idFactory();
        item.run = null;
        item.runLookup = "";
        item.submission = null;
        item.events = [];
        item.afterSeq = 0;
      }, { isCurrent: () => item === local.training && revision === item.revision });
    });
  }

  async function approveTraining() {
    return validate("training", () => {
      const item = local.training;
      const planId = idOf(item.plan, "plan_id");
      if (!planId || !item.plan.plan_hash || !item.reviewed || !trainingTransferReview(item.plan).complete) throw invalid("reviewRequired", "Review the complete data transfer and select the acknowledgment before approving.");
      if (item.grant || item.run && !terminal(item.run)) return null;
      const revision = item.revision;
      return write("training-approve", "training", sessionPath(`training/plans/${enc(planId)}/approve`), { expected_plan_hash: item.plan.plan_hash }, data => {
        item.grant = data.grant || data;
      }, { isCurrent: () => item === local.training && revision === item.revision });
    });
  }

  async function submitTraining() {
    return validate("training", () => {
      const item = local.training;
      const planId = idOf(item.plan, "plan_id");
      const grantId = idOf(item.grant, "grant_id");
      if (!planId || !grantId || !item.reviewed) throw invalid("approvalRequired", "Approve the current transfer plan before submitting.");
      const expires = computeExpiry(item.grant.expires_at || item.grant.valid_until);
      if (expires === null || expires <= Date.now()) {
        item.grant = null;
        item.reviewed = false;
        throw invalid("grantExpired", "The transfer approval has expired. Prepare and review a new plan.");
      }
      if (item.run || item.submissionUnknown) return null;
      const receipt = { plan_id: planId, grant_id: grantId, idempotency_key: item.idempotencyKey };
      return write("training-submit", "training", sessionPath("training/runs"), receipt, data => {
        item.run = data.run || data;
        item.submissionUnknown = false;
      }, { isCurrent: () => item === local.training, started: () => { item.submission = receipt; item.submissionUnknown = true; item.runLookup = ""; }, unknown: value => { item.submissionUnknown = value; } });
    });
  }

  function applyEvents(item, events) {
    const bySeq = new Map(item.events.map(event => [Number(event.seq), event]));
    for (const event of events) {
      const seq = Number(event.sequence ?? event.seq);
      if (Number.isSafeInteger(seq) && seq > 0) bySeq.set(seq, { ...event, seq });
    }
    item.events = [...bySeq.values()].sort((a, b) => a.seq - b.seq).slice(-100);
    item.afterSeq = Math.max(item.afterSeq, ...item.events.map(event => event.seq));
  }

  async function refreshTrainingRun() {
    return validate("training", () => {
      const item = local.training;
      const id = item.runLookup.trim() || idOf(item.run, "run_id");
      if (!id && item.submissionUnknown && item.submission) {
        // The service checks the existing key before grant expiry. Replay only the exact approved submission.
        return write("training-submit", "training", sessionPath("training/runs"), item.submission, data => {
          item.run = data.run || data;
          item.submissionUnknown = false;
        }, { isCurrent: () => item === local.training });
      }
      const query = id ? `training/runs/${enc(id)}` : "";
      if (!query) throw invalid("runRequired", "Enter an execution reference to check its status.");
      return read("training-status", "training", scopedQuery(sessionPath(query)), data => {
        const run = data.run || asList(data, "runs")[0] || (data.run_id || data.id ? data : null);
        if (!run) throw invalid("runUnknown", "The execution has not been located. Keep its submission reference and check again.");
        if (idOf(item.run, "run_id") !== idOf(run, "run_id")) { item.events = []; item.afterSeq = 0; }
        item.run = run;
        item.submissionUnknown = false;
        if (Array.isArray(data.events || run.events)) applyEvents(item, data.events || run.events);
      }, () => item === local.training && id === (item.runLookup.trim() || idOf(item.run, "run_id")));
    });
  }

  async function refreshTrainingEvents() {
    return validate("training", () => {
      const item = local.training;
      const id = idOf(item.run, "run_id");
      const jobId = item.run?.job_id;
      if (!id || !jobId) throw invalid("eventsUnavailable", "The server has not reported a job reference for event replay. Refresh the execution status.");
      return read("training-events", "training", scopedQuery(sessionPath(`jobs/events?${new URLSearchParams({ job_id: jobId, after_sequence: String(item.afterSeq), limit: "100" })}`)), data => {
        applyEvents(item, asList(data, "events"));
        item.eventsTruncated = Boolean(data.replay_truncated);
      }, () => item === local.training && id === idOf(item.run, "run_id"));
    });
  }

  async function cancelTraining() {
    return validate("training", () => {
      const item = local.training;
      const id = idOf(item.run, "run_id");
      if (!id || terminal(item.run) || item.run.cancel_requested || item.run.cancel_pending) return null;
      return write("training-cancel", "training", sessionPath(`training/runs/${enc(id)}/cancel`), {}, data => {
        item.run = { ...item.run, ...(data.run || data), cancel_requested: true };
      }, { isCurrent: () => item === local.training && id === idOf(item.run, "run_id") });
    });
  }

  function updateClusterForm(field, value) {
    syncScope();
    local.cluster.form[field] = value;
    if (field === "kind") {
      Object.assign(local.cluster.form, { auth: "none", secret: "", port: value === "trino" ? "443" : "10000", catalog: "" });
    }
    local.cluster.test = null;
    notify();
  }
  function updateQueryForm(field, value) {
    syncScope();
    local.cluster.queryForm[field] = value;
    local.cluster.revision += 1;
    if (field === "connectionId") local.cluster.test = null;
    notify();
  }

  function editClusterConnection(id = "") {
    syncScope();
    const connection = local.clusterConnections.find(item => idOf(item, "connection_id") === id);
    const config = connection?.config || connection || {};
    Object.assign(local.cluster, { editingId: id, deleteId: "", test: null, form: {
      ...initialState().cluster.form, name: config.name || "", kind: config.kind || "trino", host: config.host || "", port: String(config.port || 443),
      tls: config.tls !== false, username: config.username || "baa_readonly", auth: config.auth || "none", catalog: config.catalog || "", schema: config.schema || "default",
      tables: (config.allowed_tables || []).join("\n"), resourceGroup: config.resource_group || "", secret: "",
    } });
    notify();
  }

  function requestDeleteConnection(id = "") { syncScope(); local.cluster.deleteId = id; notify(); }

  async function refreshClusterConnections() {
    return validate("cluster", () => read("cluster-connections", "cluster", scopedQuery(clusterPath("connections")), data => { local.clusterConnections = asList(data, "connections"); }));
  }

  async function saveClusterConnection() {
    return validate("cluster", async () => {
      const item = local.cluster;
      const form = item.form;
      if (!form.name.trim() || !form.host.trim() || !list(form.tables).length) throw invalid("connectionRequired", "Enter a connection name, host and authorized tables.");
      if (!/^[a-zA-Z0-9.:-]+$/.test(form.host.trim()) || !["trino", "hive", "spark_sql"].includes(form.kind)) throw invalid("hostInvalid", "Use a host name or IP address, without a URL, path or query string.");
      const port = Number(form.port);
      if (!Number.isSafeInteger(port) || port < 1 || port > 65535) throw invalid("portInvalid", "Enter a port between 1 and 65535.");
      const editingId = item.editingId;
      const config = { name: form.name.trim(), kind: form.kind, host: form.host.trim(), port, tls: Boolean(form.tls), username: form.username.trim(), auth: form.auth,
        allowed_tables: list(form.tables), catalog: form.catalog.trim(), schema: form.schema.trim(), resource_group: form.resourceGroup.trim() };
      const body = scopedBody({ session_id: context().sessionId, ...config, ...(form.secret ? { secret: form.secret } : {}) });
      // Only the API creates vault references. Never copy the secret into list or result state.
      form.secret = "";
      const result = await write("cluster-save", "cluster", clusterPath(`connections${editingId ? `/${enc(editingId)}` : ""}`), body, data => {
        const saved = data.connection || data;
        item.queryForm.connectionId = idOf(saved, "connection_id");
        item.form.secret = "";
        item.editingId = "";
      }, { method: editingId ? "PATCH" : "POST", isCurrent: () => item === local.cluster });
      if (result && item === local.cluster) await refreshClusterConnections();
      return result;
    });
  }

  async function deleteClusterConnection() {
    return validate("cluster", async () => {
      const item = local.cluster;
      const id = item.deleteId;
      if (!id) return null;
      const result = await write("cluster-delete", "cluster", scopedQuery(clusterPath(`connections/${enc(id)}`)), undefined, () => {
        item.deleteId = "";
        item.test = null;
        if (item.queryForm.connectionId === id) item.queryForm.connectionId = "";
        if (item.editingId === id) editClusterConnection();
      }, { method: "DELETE", isCurrent: () => item === local.cluster });
      if (result && item === local.cluster) await refreshClusterConnections();
      return result;
    });
  }

  async function testClusterConnection(id = local.cluster.queryForm.connectionId) {
    return validate("cluster", () => {
      if (!id) throw invalid("connectionRequired", "Select a configured connection first.");
      const item = local.cluster;
      item.queryForm.connectionId = id;
      return write("cluster-test", "cluster", clusterPath(`connections/${enc(id)}/test`), scopedBody({ session_id: context().sessionId }), data => { item.test = data; }, { isCurrent: () => item === local.cluster && id === item.queryForm.connectionId });
    });
  }

  async function activateClusterConnection(id = local.cluster.queryForm.connectionId) {
    return validate("cluster", () => {
      if (!id) throw invalid("connectionRequired", "Select a configured connection first.");
      const item = local.cluster;
      return write("cluster-activate", "cluster", clusterPath(`connections/${enc(id)}/activate`), scopedBody({ session_id: context().sessionId }), data => {
        item.activation = data;
      }, { isCurrent: () => item === local.cluster });
    });
  }

  async function submitClusterQuery() {
    return validate("cluster", () => {
      const item = local.cluster;
      const form = item.queryForm;
      if (!form.connectionId || !form.sql.trim()) throw invalid("queryRequired", "Select a configured connection and enter a read-only SQL query.");
      if (item.submissionUnknown || item.query && !terminal(item.query)) throw invalid("reconcileFirst", "Check the previous submission before creating another execution.");
      const budget = buildClusterBudget(form);
      const revision = item.revision;
      const key = !item.query && item.submissionRevision === revision && item.idempotencyKey ? item.idempotencyKey : idFactory();
      return write("cluster-submit", "cluster", clusterPath("queries"), scopedBody({
        session_id: context().sessionId, connection_id: form.connectionId, sql: form.sql.trim(), budget, idempotency_key: key,
      }), data => { item.query = data.query || data; item.submissionUnknown = false; item.snapshot = null; item.events = []; item.afterSeq = 0; }, {
        isCurrent: () => item === local.cluster,
        started: () => { item.idempotencyKey = key; item.submissionRevision = revision; item.submissionUnknown = true; item.query = null; item.queryLookup = ""; },
        unknown: value => { item.submissionUnknown = value; },
      });
    });
  }

  async function refreshClusterQuery() {
    return validate("cluster", () => {
      const item = local.cluster;
      const id = item.queryLookup.trim() || idOf(item.query, "query_id");
      const suffix = id ? `queries/${enc(id)}` : item.submissionUnknown ? `queries?${new URLSearchParams({ idempotency_key: item.idempotencyKey })}` : "";
      if (!suffix) throw invalid("queryReferenceRequired", "Enter a query reference to check its status.");
      return read("cluster-status", "cluster", scopedQuery(clusterPath(suffix)), data => {
        const query = data.query || asList(data, "queries")[0] || (data.query_id || data.id ? data : null);
        if (!query) throw invalid("queryUnknown", "The query has not been located. Keep its submission reference and check again.");
        item.query = query;
        item.submissionUnknown = false;
      }, () => item === local.cluster && id === (item.queryLookup.trim() || idOf(item.query, "query_id")));
    });
  }

  async function refreshClusterEvents() {
    return validate("cluster", () => {
      const item = local.cluster;
      const id = idOf(item.query, "query_id");
      if (!id) return null;
      return read("cluster-events", "cluster", scopedQuery(clusterPath(`queries/${enc(id)}/events?${new URLSearchParams({ after_seq: String(item.afterSeq), limit: "100" })}`)), data => {
        applyEvents(item, asList(data, "events"));
        item.eventsTruncated = Boolean(data.truncated);
      }, () => item === local.cluster && id === idOf(item.query, "query_id"));
    });
  }

  async function cancelClusterQuery() {
    return validate("cluster", () => {
      const item = local.cluster;
      const id = idOf(item.query, "query_id");
      if (!id || terminal(item.query) || item.query.cancel_requested || item.query.cancel_pending) return null;
      return write("cluster-cancel", "cluster", clusterPath(`queries/${enc(id)}/cancel`), scopedBody({ session_id: context().sessionId }), data => {
        item.query = { ...item.query, ...(data.query || data), cancel_requested: true };
      }, { isCurrent: () => item === local.cluster && id === idOf(item.query, "query_id") });
    });
  }

  async function snapshotClusterQuery() {
    return validate("cluster", () => {
      const item = local.cluster;
      const id = idOf(item.query, "query_id");
      if (!canSnapshotClusterQuery(item.query)) throw invalid("completeRequired", "Only a fetchable bounded result can be verified and published as a dataset snapshot.");
      return write("cluster-snapshot", "cluster", clusterPath(`queries/${enc(id)}/snapshot`), scopedBody({ session_id: context().sessionId, idempotency_key: `snapshot-${id}` }), data => {
        item.snapshot = data.snapshot || data.manifest || data;
        if (data.query) item.query = data.query;
      }, { isCurrent: () => item === local.cluster && id === idOf(item.query, "query_id") });
    });
  }

  function close() {
    requests.reset();
    local.open = false;
    local.loading = {};
    local.training.grant = null;
    local.training.reviewed = false;
    local.cluster.form.secret = "";
    notify();
  }

  async function open(tab = "datasets", options = {}) {
    syncScope();
    local.open = true;
    local.tab = ["datasets", "training", "cluster"].includes(tab) ? tab : "datasets";
    notify();
    if (!scopeReady()) {
      const sid = context().sessionId;
      const resolved = await validate(local.tab, () => read("compute-context", local.tab, sessionPath("workspace"), data => {
        const workspace = data.workspace;
        if (!workspace || workspace.mounted !== false && !workspace.workspace_id) throw invalid("scopeUnavailable", "The current workspace has not been verified. Refresh the panel before retrying.");
        resolvedWorkspace = { sessionId: sid, workspaceId: String(workspace.workspace_id || "") };
      }));
      if (!resolved || context().sessionId !== sid || !local.open || !scopeReady()) return null;
    }
    if (options.connectionId) updateTrainingForm("connectionId", options.connectionId);
    if (options.runId) { local.training.runLookup = String(options.runId); local.training.run = null; local.training.events = []; local.training.afterSeq = 0; }
    if (options.queryId) { local.cluster.queryLookup = String(options.queryId); local.cluster.query = null; local.cluster.events = []; local.cluster.afterSeq = 0; }
    notify();
    if (local.tab === "datasets") return refreshDatasets();
    if (local.tab === "training") return Promise.all([refreshDatasets(), refreshTrainingConnections(), options.runId || local.training.submissionUnknown ? refreshTrainingRun() : null]);
    return Promise.all([refreshClusterConnections(), options.queryId || local.cluster.submissionUnknown ? refreshClusterQuery() : null]);
  }

  function scopeChanged() { resolvedWorkspace = null; currentScope = ""; syncScope(); notify(); }
  function setLookup(area, value) {
    syncScope();
    if (area === "training") local.training.runLookup = value;
    else local.cluster.queryLookup = value;
    notify();
  }

  return Object.freeze({ local, requests, open, close, scopeChanged, syncScope, setLookup, cancelReads,
    updateDatasetForm, chooseFile, planDataset, importDataset, refreshImport, cancelImport, newDatasetImport, refreshDatasets, selectDataset, loadDatasetFiles, activateDataset,
    updateTrainingForm, setTransferReviewed, refreshTrainingConnections, loadTrainingSnapshots, planTraining, approveTraining, submitTraining,
    refreshTrainingRun, refreshTrainingEvents, cancelTraining,
    updateClusterForm, updateQueryForm, editClusterConnection, requestDeleteConnection, refreshClusterConnections, saveClusterConnection, deleteClusterConnection, testClusterConnection,
    activateClusterConnection, submitClusterQuery, refreshClusterQuery, refreshClusterEvents, cancelClusterQuery, snapshotClusterQuery,
  });
}

let mounting;
export async function mountComputeDataUi(options) {
  if (getUiIsland("computeData")) return getUiIsland("computeData");
  if (!mounting) mounting = import("./ui/compute-data-ui.js").then(island => island.mountComputeDataUi(options)).finally(() => { mounting = null; });
  return mounting;
}

export async function openComputeDataPanel(tab = "datasets", options = {}) {
  const island = getUiIsland("computeData") || await mountComputeDataUi();
  return island?.open(tab, options);
}
