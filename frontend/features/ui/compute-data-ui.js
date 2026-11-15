import { eventBus } from "../../core/event-bus.js";
import { openOverlay, closeOverlay } from "../../core/overlay.js";
import { getUiIsland, registerUiIsland } from "../../core/ui-registry.js";
import { computeText as text, computeExpiry, createComputeDataController, executionStateLabel, safeComputeDownload, trainingConnectionReadiness, trainingTransferReview, canSnapshotClusterQuery } from "../compute-data.js";

const tabs = ["datasets", "training", "cluster"];
const idOf = (item, key) => item?.[key] || item?.id || "";
const absent = () => text("unavailable", "Unavailable");
const locale = () => String(globalThis.getLang?.() || document.documentElement.lang || "zh").startsWith("en") ? "en" : "zh-CN";
const number = value => value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) ? absent() : new Intl.NumberFormat(locale()).format(Number(value));
function bytes(value) {
  if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) return absent();
  const size = Number(value);
  const unit = size >= 1073741824 ? "GiB" : size >= 1048576 ? "MiB" : size >= 1024 ? "KiB" : "B";
  const divisor = { GiB: 1073741824, MiB: 1048576, KiB: 1024, B: 1 }[unit];
  return `${new Intl.NumberFormat(locale(), { maximumFractionDigits: 2 }).format(size / divisor)} ${unit}`;
}
const named = item => item?.name || item?.input_name || item?.source?.name || item?.config?.name || idOf(item, "dataset_id") || idOf(item, "connection_id");
const statusText = status => text(`status.${status}`, status || absent());
const ended = execution => ["succeeded", "completed", "failed", "canceled", "cancelled", "exited"].includes(executionStateLabel(execution));

export function mountComputeDataUi(options = {}) {
  const existing = getUiIsland("computeData");
  if (existing) return existing;
  const root = document.getElementById("compute-data-root");
  const overlay = document.getElementById("ov-compute-data");
  const Vue = globalThis.Vue;
  if (!root || !overlay || !Vue?.h || !Vue?.render) return null;
  const { h, render } = Vue;
  let controller;
  let destroyed = false;
  const subscriptions = [];
  controller = createComputeDataController({ ...options, onChange: state => { draw(); options.onChange?.(state); } });
  async function activateSelectedDataset() {
    const ticket = controller.requests.capture();
    const { selected, snapshot } = controller.local.dataset;
    const result = await controller.activateDataset();
    if (result && ticket.isCurrent()) eventBus.emit("compute:dataset-activated", { ...result, dataset_id: selected, snapshot_id: snapshot });
    return result;
  }
  async function activateClusterSource(id) {
    const ticket = controller.requests.capture();
    const result = await controller.activateClusterConnection(id);
    if (result && ticket.isCurrent()) eventBus.emit("compute:source-activated", result);
    return result;
  }
  const state = controller.local;
  const working = (...keys) => !state.contextReady || keys.some(key => state.busy[key]);

  const button = (key, fallback, action, disabled = false, primary = false, extra = {}) => h("button", {
    type: "button", class: `btn-sm ${primary ? "btn-sm-primary" : "btn-sm-ghost"}`, disabled,
    onClick: action, "data-action": key, ...extra,
  }, text(key, fallback));
  const actions = children => h("div", { class: "compute-actions" }, children);
  const hint = (key, fallback, id) => h("p", { class: "compute-hint", ...(id ? { id } : {}) }, text(key, fallback));
  const heading = (key, fallback) => h("h3", { class: "compute-section-title" }, text(key, fallback));
  const empty = (key, fallback) => h("p", { class: "compute-empty" }, text(key, fallback));
  const dl = rows => h("dl", { class: "compute-facts" }, rows.flatMap(([key, fallback, value]) => [
    h("dt", null, text(key, fallback)), h("dd", null, value === null || value === undefined || value === "" ? absent() : String(value)),
  ]));

  function field(prefix, key, fallback, form, update, { type = "text", choices, multiline = false, hintKey, hintText, ...props } = {}) {
    const id = `compute-${prefix}-${key}`;
    const controlProps = { id, name: key, value: form[key] ?? "", ...props,
      ...(hintKey ? { "aria-describedby": `${id}-hint` } : {}),
      [choices ? "onChange" : "onInput"]: event => update(key, type === "checkbox" ? event.target.checked : event.target.value),
    };
    const control = choices ? h("select", controlProps, choices.map(choice => h("option", { value: choice.value, disabled: Boolean(choice.disabled) }, choice.label)))
      : multiline ? h("textarea", { rows: 3, ...controlProps })
        : h("input", { type, ...controlProps, ...(type === "checkbox" ? { checked: Boolean(form[key]) } : {}) });
    return h("div", { class: type === "checkbox" ? "compute-field compute-check" : `compute-field${multiline ? " compute-field-wide" : ""}` }, [
      type === "checkbox" ? control : h("label", { for: id }, text(key, fallback)),
      type === "checkbox" ? h("label", { for: id }, text(key, fallback)) : control,
      hintKey ? hint(hintKey, hintText, `${id}-hint`) : null,
    ]);
  }

  function form(area, children, submit, locked = false) {
    return h("form", {
      class: "compute-form", "data-form": area, "aria-busy": locked,
      "aria-describedby": state.errors[area] ? `compute-${area}-error` : undefined,
      onSubmit: event => { event.preventDefault(); return submit(); },
    }, children);
  }

  function budget(prefix, values, update, disabled) {
    return h("details", { class: "compute-details", key: `budget-${prefix}` }, [
      h("summary", null, text("budget", "Resource limits")),
      hint("budgetHint", "The server tightens these limits to the available resources. Process memory limits may be monitored rather than enforced by the operating system."),
      h("fieldset", { class: "compute-grid", disabled }, [
        h("legend", { class: "compute-sr-only" }, text("budget", "Resource limits")),
        ...(prefix === "query-budget"
          ? [["seconds", "Duration (seconds)"], ["rows", "Result rows"], ["outputMiB", "Output (MiB)"], ["batchKiB", "Result batch (KiB)"]]
          : [["seconds", "Duration (seconds)"], ["rows", "Result rows"], ["inputMiB", "Input (MiB)"], ["outputMiB", "Output (MiB)"], ["memoryMiB", "Process memory (MiB)"], ["tempMiB", "Temporary disk (MiB)"]])
          .map(([key, label]) => field(prefix, key, label, values, update, { type: "number", min: 1, step: 1, required: true })),
        ...(prefix === "query-budget" ? [
          field(prefix, "scanMiB", "Scan limit (MiB, optional)", values, update, { type: "number", min: 1, step: 1 }),
          field(prefix, "cpuSeconds", "CPU seconds (optional)", values, update, { type: "number", min: 1, step: 1 }),
          field(prefix, "strictServer", "Require server-enforced budgets", values, update, { type: "checkbox", hintKey: "strictServerHint", hintText: "Unsupported server budgets block strict requests. Client result limits still apply otherwise; scans and fees may remain unbounded." }),
        ] : []),
      ]),
    ]);
  }

  function errorBox(area, retry) {
    const error = state.errors[area];
    if (!error) return null;
    const guidance = error.status === 401 ? hint("authRequired", "Your login has expired. Sign in again before retrying.")
      : error.status === 403 || /permission|forbidden|denied/i.test(error.code) ? hint("permissionDenied", "You do not have permission for this session, data or connection. Ask its owner for access.")
        : /expir/i.test(`${error.code} ${error.message}`) ? hint("expiredHint", "This plan or approval has expired. Prepare a new plan and review it again.")
          : [404, 501, 503].includes(error.status) ? hint("unavailableHint", "This capability or record is unavailable. Check the server configuration, then refresh.") : null;
    return h("div", { class: "compute-error", id: `compute-${area}-error`, role: "alert" }, [
      h("p", null, error.message), guidance,
      h("p", { class: "compute-reference" }, [error.code, error.requestId].filter(Boolean).join(" · ")),
      retry ? button("retry", "Retry loading", state.contextReady ? retry : () => controller.open(state.tab)) : null,
    ]);
  }

  function readStatus(area, keys) {
    if (["compute-context", ...keys].some(key => state.loading[key])) return h("div", { class: "compute-load", role: "status" }, [
      h("span", null, text("loading", "Loading…")), button("cancelLoading", "Cancel loading", () => controller.cancelReads(area)),
    ]);
    return state.cancelledReads[area] ? h("p", { class: "compute-hint", role: "status" }, text("loadingCancelled", "Loading cancelled. Use Refresh to load again.")) : null;
  }

  function execution(execution, area, refresh, cancel, cancelKey) {
    if (!execution) return null;
    const status = executionStateLabel(execution);
    const artifacts = execution.artifacts || execution.result?.artifacts || execution.result?.files || [];
    return h("section", { class: "compute-execution", "aria-label": text("execution", "Execution") }, [
      h("div", { class: "compute-section-head" }, [heading("execution", "Execution"), h("span", { class: `compute-status compute-status-${status}`, role: "status" }, statusText(status))]),
      status === "cancel_pending" ? hint("cancelPending", "Cancellation requested. Resources remain reserved until the executor confirms exit.") : null,
      status === "recovery_pending" ? hint("recoveryPending", "The remote state is being reconciled. This does not prove the process has stopped.") : null,
      execution.message ? h("p", { class: "compute-wrap" }, String(execution.message)) : null,
      dl([
        ["reference", "Reference", execution.run_id || execution.query_id || execution.operation_id || execution.id],
        ["phase", "Phase", execution.phase || execution.recovery_phase || execution.job?.phase],
        ["nativeReference", "Remote reference", execution.task_id || execution.remote_task_id || execution.native_query_id || execution.external_id],
        ["exitAcknowledged", "Executor exit confirmed", execution.exit_acknowledged === true ? text("yes", "Yes") : execution.exit_acknowledged === false ? text("no", "No") : absent()],
      ]),
      execution.error ? h("p", { class: "compute-error", role: "alert" }, typeof execution.error === "string" ? execution.error : String(execution.error.message || execution.error.code || "")) : null,
      actions([button("refreshStatus", "Refresh status", refresh, state.loading[`${area}-status`]),
        !ended(execution) ? button("cancelExecution", "Request cancellation", cancel, working(cancelKey) || status === "cancel_pending") : null]),
      Array.isArray(artifacts) && artifacts.length ? h("ul", { class: "compute-artifacts" }, artifacts.slice(0, 100).map(artifact => {
        const href = safeComputeDownload(artifact.download_url || artifact.url);
        const label = artifact.name || artifact.filename || artifact.artifact_id || text("artifact", "Result file");
        return h("li", null, href ? h("a", { href, target: "_blank", rel: "noopener" }, label) : h("span", null, `${label} · ${absent()}`));
      })) : null,
    ]);
  }

  function events(item, refresh, key) {
    return h("details", { class: "compute-details", key: `events-${key}` }, [
      h("summary", null, text("events", "Execution events")),
      actions([button("refreshEvents", "Refresh events", refresh, state.loading[`${key}-events`])]),
      item.eventsTruncated ? hint("eventsTruncated", "Earlier events have left the retention window. Only available events are shown.") : null,
      item.events.length ? h("ol", { class: "compute-events" }, item.events.map(event => h("li", { key: event.seq }, [
        h("span", { class: "compute-reference" }, `${event.seq} · `),
        String(event.message || event.payload?.message || event.phase || event.type || event.status || ""),
      ]))) : empty("eventsEmpty", "No events have been reported yet."),
    ]);
  }

  function previewTable(preview) {
    if (!preview) return null;
    const rowData = preview.rows || preview.data || preview.records;
    const rows = Array.isArray(rowData) ? rowData.slice(0, 50) : [];
    const columnData = preview.columns || preview.schema || (rows[0] && !Array.isArray(rows[0]) ? Object.keys(rows[0]) : []);
    const columns = (Array.isArray(columnData) ? columnData : []).slice(0, 100).map(item => typeof item === "string" ? item : item.name || item.column || "");
    if (!columns.length) return empty("previewEmpty", "No preview rows are available.");
    const cell = value => value === null || value === undefined ? "—" : String(typeof value === "object" ? JSON.stringify(value) : value).slice(0, 1000);
    return h("div", { class: "compute-table-scroll", tabindex: 0, role: "region", "aria-label": text("preview", "Bounded preview") }, [
      h("table", { class: "compute-table" }, [
        h("caption", null, text("previewLimit", "Preview: up to 50 rows. Full data stays on the server.")),
        h("thead", null, h("tr", null, columns.map(name => h("th", { scope: "col" }, name)))),
        h("tbody", null, rows.map(row => h("tr", null, columns.map((name, index) => h("td", null, cell(Array.isArray(row) ? row[index] : row[name])))))),
      ]),
    ]);
  }

  function datasetPlan(plan) {
    if (!plan) return null;
    const schema = plan.schema_policy?.columns || plan.tables?.[0]?.schema_policy?.columns || [];
    return h("section", { class: "compute-review" }, [
      heading("importReview", "Review import plan"),
      dl([["file", "File", plan.input_name || state.dataset.file?.name || state.dataset.form.workspaceFile || state.dataset.form.sourceId],
        ["format", "Format", plan.format], ["inputSize", "Input size", bytes(plan.source?.size_bytes ?? plan.fingerprint?.size_bytes ?? plan.input_bytes)],
        ["durationLimit", "Maximum duration", plan.budget?.max_run_seconds != null ? `${number(plan.budget.max_run_seconds)} s` : absent()],
      ]),
      hint("importReviewHint", "Check the frozen columns and limits before importing. Invalid rows and type changes fail the import; the current dataset remains available."),
      schema.length ? h("details", { class: "compute-details", key: "import-schema" }, [
        h("summary", null, text("frozenSchema", "Frozen columns")),
        h("ul", { class: "compute-schema" }, schema.slice(0, 200).map(column => h("li", null, [h("strong", null, column.original_name || column.name), " · ", String(column.type)]))),
      ]) : hint("schemaUnavailable", "The server has not reported column metadata."),
      actions([button("importDataset", "Import dataset", controller.importDataset, working("dataset-import", "dataset-plan") || Boolean(state.dataset.operation), true)]),
    ]);
  }

  function datasetsPanel() {
    const item = state.dataset;
    const locked = working("dataset-plan", "dataset-import") || item.submissionUnknown || Boolean(item.operation);
    const update = controller.updateDatasetForm;
    return [
      errorBox("datasets", controller.refreshDatasets), readStatus("datasets", ["datasets", "dataset-status", "dataset-versions", "dataset-preview"]),
      h("div", { class: "compute-columns" }, [
        h("section", { class: "compute-column" }, [
          heading("importHeading", "Import a dataset"),
          form("datasets", [
            h("fieldset", { class: "compute-grid", disabled: locked }, [
              h("legend", { class: "compute-sr-only" }, text("source", "Data source")),
              field("dataset", "sourceMode", "Data source", item.form, update, { choices: [
                { value: "upload", label: text("upload", "Upload file") }, { value: "workspace", label: text("workspace", "Workspace file") }, { value: "source", label: text("existingSource", "Existing file source") },
              ] }),
              item.form.sourceMode === "upload" ? h("div", { class: "compute-field compute-field-wide" }, [
                h("label", { for: "compute-file" }, text("file", "File")),
                h("input", { id: "compute-file", type: "file", accept: ".csv,.parquet,.xlsx", required: !item.file, "aria-describedby": "compute-file-hint", onChange: event => controller.chooseFile(event.target.files?.[0]) }),
                item.file ? h("p", { class: "compute-file-name" }, `${item.file.name} · ${bytes(item.file.size)}`) : null,
                hint("fileHint", "CSV, Parquet or XLSX. Files are uploaded directly without loading the whole file into browser memory. Use a workspace file for data already on this server.", "compute-file-hint"),
              ]) : item.form.sourceMode === "workspace"
                ? field("dataset", "workspaceFile", "Workspace file", item.form, update, { required: true, maxlength: 1024, hintKey: "workspaceFileHint", hintText: "Path relative to the currently authorized workspace." })
                : field("dataset", "sourceId", "File source reference", item.form, update, { required: true, maxlength: 160 }),
            ]),
            h("details", { class: "compute-details", key: "import-options" }, [
              h("summary", null, text("importOptions", "Column and missing-value options")),
              h("fieldset", { class: "compute-grid", disabled: locked }, [
                h("legend", { class: "compute-sr-only" }, text("importOptions", "Column and missing-value options")),
                field("dataset", "schemaMode", "Column types", item.form, update, { choices: [{ value: "infer", label: text("infer", "Infer from a bounded sample") }, { value: "text", label: text("preserveText", "Keep CSV and Excel columns as text") }] }),
                field("dataset", "idColumns", "Identifier columns", item.form, update, { maxlength: 4096, hintKey: "idColumnsHint", hintText: "Comma-separated columns whose leading zeros must be kept." }),
                field("dataset", "timezone", "Source time zone", item.form, update, { maxlength: 100, placeholder: "Asia/Shanghai" }),
                field("dataset", "nullValues", "Additional null tokens", item.form, update, { multiline: true, maxlength: 4096, hintKey: "nullValuesHint", hintText: "One exact token per line. Empty cells are null; NA remains a category unless you add it here. Decimal values retain their exact precision." }),
              ]),
            ]),
            budget("import-budget", item.form, update, locked),
            actions([h("button", { class: "btn-sm btn-sm-primary", type: "submit", disabled: locked }, text("planDataset", "Prepare import plan"))]),
          ], controller.planDataset, working("dataset-plan", "dataset-import")),
          datasetPlan(item.plan),
          item.submissionUnknown ? h("div", { class: "compute-notice", role: "status" }, [
            hint("importUnknown", "The import receipt is unconfirmed. Check using the same submission reference; do not create another import."),
            dl([["submissionReference", "Submission reference", item.idempotencyKey]]),
            button("checkImport", "Check import receipt", controller.refreshImport, working("dataset-import")),
          ]) : null,
          execution(item.operation, "dataset", controller.refreshImport, controller.cancelImport, "dataset-cancel"),
          item.operation && ended(item.operation) ? actions([
            button("newImport", "Prepare another import", controller.newDatasetImport), button("refresh", "Refresh", controller.refreshDatasets),
          ]) : null,
        ]),
        h("section", { class: "compute-column" }, [
          h("div", { class: "compute-section-head" }, [heading("publishedDatasets", "Published datasets"), button("refresh", "Refresh", controller.refreshDatasets, state.loading.datasets)]),
          state.datasets.length ? field("browse", "selected", "Dataset", item, (_, value) => controller.selectDataset(value), { choices: [
            { value: "", label: text("chooseDataset", "Choose a dataset") }, ...state.datasets.map(dataset => ({ value: idOf(dataset, "dataset_id"), label: named(dataset) })),
          ] }) : empty("datasetsEmpty", "No datasets yet. Prepare an import plan to publish your first snapshot."),
          item.selected ? field("browse", "snapshot", "Snapshot version", item, (_, value) => controller.selectDataset(item.selected, value), { choices: [
            { value: "", label: text("currentVersion", "Current published version") }, ...item.versions.map(version => ({ value: version.snapshot_id, label: `${version.snapshot_id} · ${statusText(version.status || "unknown")}` })),
          ] }) : null,
          previewTable(item.preview),
          item.selected ? actions([
            button("activateDataset", "Use in this session", activateSelectedDataset, working("dataset-activate") || state.loading["dataset-preview"], true),
            button("downloadDataset", "Download snapshot files", controller.loadDatasetFiles, state.loading["dataset-files"]),
          ]) : null,
          item.files?.length ? h("ul", { class: "compute-artifacts" }, item.files.map(file => {
            const href = safeComputeDownload(file.url);
            return h("li", { key: file.index }, href ? h("a", { href, target: "_blank", rel: "noopener" }, `${file.name} · ${bytes(file.size_bytes)}`) : null);
          })) : null,
          item.activation ? h("p", { class: "compute-notice", role: "status" }, text("activated", "This snapshot is now available in the current session.")) : null,
        ]),
      ]),
    ];
  }

  function transferReview() {
    const item = state.training;
    if (!item.plan) return null;
    const review = trainingTransferReview(item.plan, state.connections.find(connection => idOf(connection, "connection_id") === item.plan.connection_id));
    const grantExpiry = computeExpiry(item.grant?.expires_at || item.grant?.valid_until);
    const expired = item.grant && (grantExpiry === null || grantExpiry <= Date.now());
    return h("section", { class: "compute-review", "aria-labelledby": "compute-transfer-title" }, [
      h("h3", { class: "compute-section-title", id: "compute-transfer-title" }, text("externalReview", "Review data sent to the training server")),
      dl([["targetHost", "Destination", review.target], ["hostFingerprint", "Host fingerprint", review.fingerprint], ["snapshot", "Input snapshot", review.snapshot],
        ["columnsSent", "Columns sent", Array.isArray(review.columns) ? review.columns.join(", ") : absent()], ["inputSize", "Input size", bytes(review.bytes)],
        ["durationLimit", "Maximum duration", review.seconds != null ? `${number(review.seconds)} s` : absent()],
        ["retention", "Remote retention", review.retention != null ? text("hours", "{count} hours", { count: number(review.retention) }) : absent()],
      ]),
      !review.complete ? hint("reviewIncomplete", "Required transfer details are unavailable. Refresh the preflight and prepare a new plan before approval.") : null,
      h("div", { class: "compute-check" }, [
        h("input", { id: "compute-transfer-reviewed", type: "checkbox", checked: item.reviewed, disabled: !review.complete || working("training-approve", "training-submit"), onChange: event => controller.setTransferReviewed(event.target.checked) }),
        h("label", { for: "compute-transfer-reviewed" }, text("transferAck", "I reviewed the destination, columns, size, duration and retention, and authorize this transfer.")),
      ]),
      actions([
        button("approveTraining", "Approve this transfer", controller.approveTraining, !review.complete || !item.reviewed || Boolean(item.grant) || working("training-approve", "training-submit"), !item.grant),
        button("submitTraining", "Start training", controller.submitTraining, !item.grant || expired || !item.reviewed || Boolean(item.run) || item.submissionUnknown || working("training-submit", "training-approve"), Boolean(item.grant) && !expired),
      ]),
      item.grant ? h("p", { class: expired ? "compute-error" : "compute-hint", role: "status" }, expired
        ? text("grantExpired", "The transfer approval has expired. Prepare and review a new plan.")
        : text("grantValidUntil", "Approval expires: {time}", { time: new Date(grantExpiry).toLocaleString(locale()) })) : null,
    ]);
  }

  function trainingPanel() {
    const item = state.training;
    const locked = working("training-plan", "training-approve", "training-submit") || item.submissionUnknown || item.run && !ended(item.run);
    const update = controller.updateTrainingForm;
    return [
      errorBox("training", controller.refreshTrainingConnections), errorBox("datasets", controller.refreshDatasets),
      readStatus("training", ["datasets", "training-connections", "training-snapshots", "training-status", "training-events"]),
      h("div", { class: "compute-columns" }, [
        h("section", { class: "compute-column" }, [
          heading("prepareTraining", "Prepare training"),
          hint("trainingHint", "Train an MLP, GRU or LSTM on an immutable dataset snapshot. Sending data requires a separate review and approval."),
          actions([button("refreshConnections", "Refresh connections", controller.refreshTrainingConnections, state.loading["training-connections"])]),
          !state.connections.length ? empty("trainingConnectionsEmpty", "No training connections are available. Add an SSH connection and run training preflight in GPU settings.") : null,
          form("training", [
            h("fieldset", { class: "compute-grid", disabled: locked }, [
              h("legend", { class: "compute-sr-only" }, text("trainingInput", "Training input and model")),
              field("training", "connectionId", "Training server", item.form, update, { required: true, choices: [{ value: "", label: text("chooseConnection", "Choose a connection") },
                ...state.connections.map(connection => ({ value: idOf(connection, "connection_id"), label: `${named(connection)} · ${text(`training.${trainingConnectionReadiness(connection)}`, absent())}`, disabled: trainingConnectionReadiness(connection) !== "ready" })),
              ] }),
              field("training", "datasetId", "Dataset", item.form, (_, value) => controller.loadTrainingSnapshots(value), { required: true, choices: [{ value: "", label: text("chooseDataset", "Choose a dataset") }, ...state.datasets.map(dataset => ({ value: idOf(dataset, "dataset_id"), label: named(dataset) }))] }),
              field("training", "snapshotId", "Snapshot version", item.form, update, { required: true, choices: [{ value: "", label: text("chooseSnapshot", "Choose an immutable snapshot") }, ...item.snapshots.map(snapshot => ({ value: snapshot.snapshot_id, label: snapshot.snapshot_id, disabled: snapshot.status !== "ready" }))] }),
              field("training", "family", "Model family", item.form, update, { choices: [{ value: "Torch_MLP", label: text("mlp", "Multilayer perceptron (MLP)") }, { value: "Torch_RNN", label: text("rnn", "Recurrent network (GRU / LSTM)") }] }),
              item.form.family === "Torch_MLP" ? field("training", "task", "Learning task", item.form, update, { choices: [{ value: "classification", label: text("classification", "Classification") }, { value: "regression", label: text("regression", "Regression") }] }) : null,
              field("training", "targetColumn", "Target column", item.form, update, { required: true, maxlength: 256 }),
              h("div", { class: "compute-field-wide" }, [hint("modelColumnsHint", "MLP uses every non-target column in the snapshot. RNN uses the target and optional time and sequence columns. Review the server plan for the exact transfer scope.")]),
              field("training", "epochs", "Epochs", item.form, update, { type: "number", min: 1, max: 500, step: 1, required: true }),
              field("training", "batchSize", "Batch size", item.form, update, { type: "number", min: 1, max: 1024, step: 1, required: true }),
              field("training", "hiddenSize", "Hidden units", item.form, update, { type: "number", min: 1, max: 256, step: 1, required: true }),
              item.form.family === "Torch_RNN" ? field("training", "cell", "Recurrent cell", item.form, update, { choices: [{ value: "gru", label: "GRU" }, { value: "lstm", label: "LSTM" }] }) : null,
              item.form.family === "Torch_RNN" ? field("training", "windowSize", "Sequence window", item.form, update, { type: "number", min: 1, max: 512, step: 1, required: true }) : null,
              item.form.family === "Torch_RNN" ? field("training", "timeColumn", "Time column (optional)", item.form, update, { maxlength: 256 }) : null,
              item.form.family === "Torch_RNN" ? field("training", "sequenceColumn", "Sequence column (optional)", item.form, update, { maxlength: 256 }) : null,
              item.form.family === "Torch_RNN" ? field("training", "forecastSteps", "Forecast steps", item.form, update, { type: "number", min: 1, max: 1024, step: 1, required: true }) : null,
              field("training", "seed", "Random seed", item.form, update, { type: "number", min: 0, step: 1, required: true }),
              field("training", "retentionHours", "Remote retention (hours)", item.form, update, { type: "number", min: 1, max: 168, step: 1, required: true }),
            ]),
            budget("training-budget", item.form, update, locked),
            actions([h("button", { class: "btn-sm btn-sm-primary", type: "submit", disabled: locked }, text("planTraining", "Prepare training plan"))]),
          ], controller.planTraining, working("training-plan", "training-approve", "training-submit")),
        ]),
        h("section", { class: "compute-column" }, [
          transferReview(),
          item.submissionUnknown ? h("div", { class: "compute-notice", role: "status" }, [
            hint("submissionUnknown", "The submission receipt is unconfirmed. Check the existing reference before submitting another execution."),
            dl([["submissionReference", "Submission reference", item.idempotencyKey]]), button("checkSubmission", "Check submission", controller.refreshTrainingRun, working("training-submit")),
          ]) : null,
          form("training-lookup", [
            field("training", "runLookup", "Execution reference", item, (_, value) => controller.setLookup("training", value), { maxlength: 160 }),
            actions([h("button", { class: "btn-sm btn-sm-ghost", type: "submit", disabled: state.loading["training-status"] }, text("findExecution", "Find execution"))]),
          ], controller.refreshTrainingRun),
          execution(item.run, "training", controller.refreshTrainingRun, controller.cancelTraining, "training-cancel"),
          item.run ? events(item, controller.refreshTrainingEvents, "training") : !item.plan ? empty("trainingEmpty", "Prepare a plan to review the transfer, or enter an existing execution reference.") : null,
        ]),
      ]),
    ];
  }

  function capabilityFacts() {
    const item = state.cluster;
    const connection = state.clusterConnections.find(connection => idOf(connection, "connection_id") === item.queryForm.connectionId);
    const caps = item.test?.capabilities || connection?.capabilities || {};
    const reported = value => value === true ? text("supported", "Supported") : value === false ? text("unsupported", "Unsupported") : typeof value === "string" ? value : absent();
    return h("section", { class: "compute-capabilities" }, [
      heading("capabilities", "Reported capabilities"),
      dl([["dialect", "SQL dialect", caps.dialect], ["nativeCancel", "Native cancellation", reported(caps.native_cancel ?? caps.cancel)],
        ["scanBudget", "Enforceable scan limit", reported(Array.isArray(caps.hard_server_budgets) ? caps.hard_server_budgets.includes("max_scan_bytes") : caps.scan_budget ?? caps.hard_scan_limit)], ["cost", "Query cost", absent()]]),
      hint("clusterBudgetHint", "Result limits do not limit scanned data or fees. Missing scan, cost or enforcement metadata is unavailable; the server checks the requested budget before execution."),
    ]);
  }

  function connectionEditor() {
    const item = state.cluster;
    const editing = Boolean(item.editingId);
    const update = controller.updateClusterForm;
    return h("details", { class: "compute-details", key: `connection-editor-${item.editingId}`, open: editing || !state.clusterConnections.length }, [
      h("summary", null, editing ? text("editConnection", "Edit connection") : text("addConnection", "Add connection")),
      form("cluster-connection", [
        h("fieldset", { class: "compute-grid", disabled: working("cluster-save") }, [
          h("legend", { class: "compute-sr-only" }, text("connectionSettings", "Connection settings")),
          field("connection", "name", "Connection name", item.form, update, { required: true, maxlength: 80 }),
          field("connection", "kind", "Cluster engine", item.form, update, { choices: [{ value: "trino", label: "Trino" }, { value: "hive", label: "HiveServer2" }, { value: "spark_sql", label: "Spark Thrift Server" }] }),
          field("connection", "host", "Host", item.form, update, { required: true, maxlength: 253 }),
          field("connection", "port", "Port", item.form, update, { type: "number", required: true, min: 1, max: 65535, step: 1 }),
          field("connection", "tls", "Verify TLS", item.form, update, { type: "checkbox" }),
          field("connection", "username", "Read-only username", item.form, update, { required: true, maxlength: 128, autocomplete: "username" }),
          field("connection", "auth", "Authentication", item.form, update, { choices: [{ value: "none", label: text("authNone", "No credentials") },
            ...(item.form.kind === "trino" ? [{ value: "basic", label: "Basic" }, { value: "jwt", label: "JWT" }] : [{ value: "ldap", label: "LDAP" }]),
          ] }),
          item.form.auth !== "none" ? field("connection", "secret", "Authentication secret", item.form, update, { type: "password", maxlength: 16384, autocomplete: "new-password", required: !editing,
            hintKey: "credentialHint", hintText: "Sent only to the server's system credential vault. Saved secrets are never displayed. Leave blank to keep the existing credential when editing the same connection." }) : null,
          item.form.kind === "trino" ? field("connection", "catalog", "Default catalog", item.form, update, { required: true, maxlength: 128 }) : null,
          field("connection", "schema", "Default schema", item.form, update, { required: true, maxlength: 128 }),
          field("connection", "tables", "Authorized tables", item.form, update, { required: true, multiline: true, maxlength: 32768, hintKey: "tablesHint", hintText: "One qualified table per line. Trino uses catalog.schema.table; Hive and Spark use schema.table." }),
          item.form.kind === "trino" ? field("connection", "resourceGroup", "Resource group (optional)", item.form, update, { maxlength: 128 }) : null,
        ]),
        actions([h("button", { class: "btn-sm btn-sm-primary", type: "submit", disabled: working("cluster-save") }, editing ? text("saveConnection", "Save connection") : text("addConnection", "Add connection")),
          editing ? button("cancelEdit", "Cancel editing", () => controller.editClusterConnection(), working("cluster-save")) : null]),
      ], controller.saveClusterConnection, working("cluster-save")),
    ]);
  }

  function clusterPanel() {
    const item = state.cluster;
    const update = controller.updateQueryForm;
    const locked = working("cluster-submit") || item.submissionUnknown || item.query && !ended(item.query);
    const query = item.query;
    return [
      errorBox("cluster", controller.refreshClusterConnections), readStatus("cluster", ["cluster-connections", "cluster-status", "cluster-events"]),
      h("div", { class: "compute-columns" }, [
        h("section", { class: "compute-column" }, [
          h("div", { class: "compute-section-head" }, [heading("clusterConnections", "Cluster connections"), button("refresh", "Refresh", controller.refreshClusterConnections, state.loading["cluster-connections"])]),
          state.clusterConnections.length ? h("ul", { class: "compute-connections" }, state.clusterConnections.map(connection => {
            const id = idOf(connection, "connection_id");
            const config = connection.config || connection;
            return h("li", { key: id }, [
              h("strong", null, named(connection)),
              h("p", { class: "compute-hint" }, `${config.kind || absent()} · ${config.host || absent()}:${config.port || ""}`),
              actions([button("testConnection", "Test connection", () => controller.testClusterConnection(id), working("cluster-test")),
                button("activateDataset", "Use in this session", () => activateClusterSource(id), working("cluster-activate")),
                button("editConnection", "Edit connection", () => controller.editClusterConnection(id), working("cluster-save")),
                button("deleteConnection", "Delete connection", () => controller.requestDeleteConnection(id), working("cluster-delete"))]),
              item.deleteId === id ? h("div", { class: "compute-notice" }, [
                hint("deleteConnectionQuestion", "Delete this connection? Existing remote queries may still be running. Check or cancel them using their query references."),
                actions([button("confirmDelete", "Delete this connection", controller.deleteClusterConnection, working("cluster-delete"), false, { class: "btn-sm btn-sm-danger" }), button("keepConnection", "Keep connection", () => controller.requestDeleteConnection(), working("cluster-delete"))]),
              ]) : null,
            ]);
          })) : empty("clusterConnectionsEmpty", "No cluster connections yet. Add a read-only connection and its authorized tables."),
          connectionEditor(), capabilityFacts(),
        ]),
        h("section", { class: "compute-column" }, [
          heading("readOnlyQuery", "Read-only SQL query"),
          form("cluster-query", [
            h("fieldset", { class: "compute-grid", disabled: locked }, [
              h("legend", { class: "compute-sr-only" }, text("readOnlyQuery", "Read-only SQL query")),
              field("query", "connectionId", "Cluster connection", item.queryForm, update, { required: true, choices: [{ value: "", label: text("chooseConnection", "Choose a connection") }, ...state.clusterConnections.map(connection => ({ value: idOf(connection, "connection_id"), label: named(connection) }))] }),
              field("query", "sql", "SQL", item.queryForm, update, { multiline: true, required: true, rows: 7, maxlength: 65536, spellcheck: false, class: "compute-sql", hintKey: "sqlHint", hintText: "Use authorized tables and the cluster's SQL dialect. Only a complete, bounded result can become a dataset snapshot." }),
            ]),
            budget("query-budget", item.queryForm, update, locked),
            actions([h("button", { class: "btn-sm btn-sm-primary", type: "submit", disabled: locked }, text("submitQuery", "Run bounded query"))]),
          ], controller.submitClusterQuery, working("cluster-submit")),
          item.submissionUnknown ? h("div", { class: "compute-notice", role: "status" }, [
            hint("submissionUnknown", "The submission receipt is unconfirmed. Check the existing reference before submitting another execution."),
            dl([["submissionReference", "Submission reference", item.idempotencyKey]]), button("checkSubmission", "Check submission", controller.refreshClusterQuery, working("cluster-submit")),
          ]) : null,
          form("cluster-lookup", [field("query", "queryLookup", "Query reference", item, (_, value) => controller.setLookup("cluster", value), { maxlength: 160 }),
            actions([h("button", { class: "btn-sm btn-sm-ghost", type: "submit", disabled: state.loading["cluster-status"] }, text("findQuery", "Find query"))]),
          ], controller.refreshClusterQuery),
          execution(query, "cluster", controller.refreshClusterQuery, controller.cancelClusterQuery, "cluster-cancel"),
          query ? h("section", { class: "compute-query-result" }, [
            dl([["resultComplete", "Result complete", query.complete === true ? text("yes", "Yes") : query.complete === false ? text("no", "No") : absent()],
              ["resultRows", "Result rows", number(query.result_rows ?? query.row_count ?? query.stats?.row_count)], ["resultBytes", "Result size", bytes(query.result_bytes ?? query.output_bytes ?? query.stats?.output_bytes)],
              ["scannedBytes", "Scanned bytes", bytes(query.statistics?.scan_bytes ?? query.stats?.scanned_bytes)], ["statsSource", "Statistics source", query.statistics?.server_stats_source || query.server_stats_source || query.stats?.source]]),
            query.complete === false && query.fetch_started ? hint("incompleteResult", "This result is incomplete and cannot be used as a complete dataset or training input.") : hint("snapshotHint", "The server fetches within the submitted budget and publishes only after the complete result is read and verified."),
            actions([button("snapshotQuery", "Fetch bounded result and save snapshot", controller.snapshotClusterQuery, !canSnapshotClusterQuery(query) || working("cluster-snapshot") || Boolean(item.snapshot), true)]),
          ]) : null,
          item.snapshot ? h("div", { class: "compute-notice", role: "status" }, [
            h("p", null, text("snapshotSaved", "The server created a dataset snapshot.")),
            dl([["datasetId", "Dataset reference", item.snapshot.dataset_id], ["snapshot", "Input snapshot", item.snapshot.snapshot_id]]),
            button("viewDatasets", "View datasets", () => controller.open("datasets")),
          ]) : null,
          query ? events(item, controller.refreshClusterEvents, "cluster") : empty("queryEmpty", "Run a bounded read-only query, or find an existing query by its reference."),
        ]),
      ]),
    ];
  }

  function close() { controller.close(); closeOverlay("ov-compute-data"); }
  function selectTab(tab) { return controller.open(tab); }
  function tabKey(event, tab) {
    const index = tabs.indexOf(tab);
    const next = event.key === "ArrowRight" ? tabs[(index + 1) % tabs.length] : event.key === "ArrowLeft" ? tabs[(index + tabs.length - 1) % tabs.length]
      : event.key === "Home" ? tabs[0] : event.key === "End" ? tabs.at(-1) : "";
    if (!next) return;
    event.preventDefault();
    selectTab(next);
    document.getElementById(`compute-tab-${next}`)?.focus();
  }

  function draw() {
    if (destroyed || !controller) return;
    if (!state.open) { render(null, root); return; }
    const context = options.getContext?.() || {};
    const tabNames = { datasets: text("datasets", "Datasets"), training: text("training", "Training"), cluster: text("cluster", "Cluster SQL") };
    render(h("section", { class: "compute-data-shell", "aria-labelledby": "compute-data-title" }, [
      h("header", { class: "compute-head" }, [
        h("div", null, [h("h2", { class: "modal-title", id: "compute-data-title" }, text("title", "Data and compute")),
          h("p", { class: "compute-hint" }, text("subtitle", "Import snapshots, approve remote training and query authorized clusters.")),
          context.sessionId ? h("p", { class: "compute-reference" }, `${context.sessionId}${context.workspaceId ? ` · ${context.workspaceId}` : ""}`) : null]),
        button("close", "Close", close, false, false, { "aria-label": text("close", "Close") }),
      ]),
      h("div", { class: "compute-tabs", role: "tablist", "aria-label": text("views", "Data and compute views") }, tabs.map(tab => h("button", {
        type: "button", id: `compute-tab-${tab}`, role: "tab", "aria-selected": state.tab === tab,
        "aria-controls": `compute-panel-${tab}`, tabindex: state.tab === tab ? 0 : -1, class: state.tab === tab ? "active" : "",
        onClick: () => selectTab(tab), onKeydown: event => tabKey(event, tab),
      }, tabNames[tab]))),
      h("div", { class: "compute-body", id: `compute-panel-${state.tab}`, role: "tabpanel", "aria-labelledby": `compute-tab-${state.tab}`, tabindex: 0 },
        state.tab === "datasets" ? datasetsPanel() : state.tab === "training" ? trainingPanel() : clusterPanel()),
      Object.values(state.busy).some(Boolean) ? h("p", { class: "compute-pending", role: "status" }, text("saving", "The server is processing this operation. Closing the panel does not cancel it.")) : null,
    ]), root);
  }

  async function open(tab = "datasets", openOptions = {}) {
    const pending = controller.open(tab, openOptions);
    openOverlay("ov-compute-data");
    return pending;
  }
  function reset() {
    controller.scopeChanged();
    if (state.open) controller.open(state.tab);
  }
  for (const key of ["SID", "workspace_id", "workspaceId", "workspace"]) subscriptions.push(eventBus.on(`state:${key}`, reset));
  subscriptions.push(eventBus.on("knowledge:scope-changed", reset));
  subscriptions.push(eventBus.on("overlay:open", ({ id }) => {
    if (id === "ov-compute-data" && !state.open) controller.open(state.tab);
  }));
  document.addEventListener("langchange", draw);
  const observer = globalThis.MutationObserver ? new MutationObserver(() => {
    if (!overlay.classList.contains("open") && state.open) controller.close();
  }) : null;
  observer?.observe(overlay, { attributes: true, attributeFilter: ["class"] });
  const island = Object.freeze({ open, close, reset, controller, isOpen: () => state.open,
    destroy() {
      close();
      destroyed = true;
      observer?.disconnect();
      subscriptions.forEach(unsubscribe => unsubscribe());
      document.removeEventListener("langchange", draw);
      render(null, root);
      registerUiIsland("computeData", null);
    },
  });
  registerUiIsland("computeData", island);
  return island;
}
