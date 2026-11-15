/* Typed ontology import, source binding, retrieval preview and lifecycle. */
import { toast } from "../core/overlay.js";
import { isSilentRequestError, requestErrorMessage, requestMessage } from "../core/api-client.js";
import { createRequestOwner } from "../core/request-state.js";

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const sections = {
  domain: "业务域与默认口径", entities: "实体与属性", relations: "实体关系",
  dimensions: "分析维度与分桶", metrics: "指标口径", synonyms: "同义词与作用域",
  intents: "查询意图与追问", query_surface: "查询对象", quality_rules: "质量规则", security: "权限声明",
};
let columnListId = 0;

function warningsHtml(warnings = []) {
  if (!warnings.length) return "";
  return `<details class="kb-ontology-warnings" open><summary>需要确认 ${warnings.length} 项</summary><ul>${warnings.map((item) =>
    `<li><code>${esc(item.path)}</code> ${esc(item.message)}</li>`).join("")}</ul></details>`;
}

function definitionsHtml(document = {}) {
  return Object.entries(sections).filter(([key]) => document[key]).map(([key, label]) =>
    `<details class="kb-ontology-definition"><summary>${label}</summary><pre>${esc(JSON.stringify(document[key], null, 2))}</pre></details>`).join("");
}

function summaryText(summary = {}) {
  return [["entities", "个实体"], ["properties", "个属性"], ["relations", "条关系"],
    ["dimensions", "个维度"], ["metrics", "个指标"], ["intents", "类意图"],
    ["synonyms", "个同义词"], ["query_surfaces", "个查询对象"]]
    .filter(([key]) => Number.isFinite(summary[key]))
    .map(([key, label]) => `${summary[key]} ${label}`).join(" · ");
}

function tableSpecs(document) {
  const specs = new Map();
  const add = (name, columns = []) => {
    if (!name) return;
    if (!specs.has(name)) specs.set(name, new Set());
    columns.forEach((column) => specs.get(name).add(column));
  };
  Object.values(document.entities || {}).forEach((entity) => {
    const source = entity.source || {};
    const columns = Object.keys(entity.properties || {});
    if (source.primary_key && /^[\p{L}\p{N}_]+$/u.test(source.primary_key)) columns.push(source.primary_key);
    add(source.table || source.view, columns);
  });
  (document.relations || []).forEach(({ join }) => {
    add(join.from_table, [join.from_column]);
    add(join.to_table, [join.to_column]);
    if (join.via_table) add(join.via_table, [join.via_from_column, join.via_to_column]);
  });
  Object.entries(document.query_surface || {}).forEach(([name, surface]) => {
    add(name, surface.columns || []);
    add(surface.source_view || surface.source_table || surface.table || surface.view, surface.columns || []);
  });
  return [...specs].sort(([left], [right]) => left.localeCompare(right)).map(([name, columns]) => ({ name, columns: [...columns].sort() }));
}

export function createOntologyPanel({ request, categoryId, persist, sourceUrl,
  getScope = () => JSON.stringify([categoryId(), sourceUrl("/api/knowledge/ontology-sources")]),
}) {
  const requests = createRequestOwner({ getScope });
  let preview = null;
  let previewReady = false;
  let sources = [];
  let records = [];
  let savedPreview = null;
  let storageError = "";

  function boundSource(binding = {}) {
    const exact = sources.find((source) => binding.source_id === source.source_id && binding.session_id === source.session_id);
    if (exact) return exact;
    const matches = sources.filter((source) => binding.source_key && binding.source_key === source.source_key);
    return matches.length === 1 ? matches[0] : null;
  }

  function sourceOptions(binding = {}) {
    const selected = boundSource(binding);
    return `<option value="">暂不绑定（仅保存，不参与检索）</option>` + sources.map((source) =>
      `<option value="${esc(source.source_id)}"${selected === source ? " selected" : ""}>${esc(source.source_name)}</option>`).join("");
  }

  function defaultTable(document, logical, source, mapping) {
    if (Object.hasOwn(mapping, logical)) return mapping[logical];
    if (Object.hasOwn(source.tables, logical)) return logical;
    const surface = document.query_surface?.[logical] || {};
    const physical = surface.source_view || surface.source_table || surface.table || surface.view;
    const fallback = Object.hasOwn(mapping, physical) ? mapping[physical] : physical;
    return fallback && Object.hasOwn(source.tables, fallback) ? fallback : "";
  }

  function fieldsHtml(spec, source, target, columnMapping = {}) {
    if (!target) return `<p class="kb-ontology-note">待映射；不会自动创建表或查询对象。</p>`;
    const actual = source.tables[target] || [];
    const current = columnMapping[spec.name] || {};
    const missing = spec.columns.filter((name) => !(Object.hasOwn(current, name) ? current[name] : actual.includes(name)));
    const listId = `kb-ontology-columns-${++columnListId}`;
    return `<details class="kb-ontology-columns"${missing.length ? " open" : ""}>
      <summary>字段映射（${spec.columns.length} 项）<span class="kb-ontology-note">${missing.length ? ` · ${missing.length} 项待确认` : ""}</span></summary>
      <p class="kb-ontology-note">可选择实际字段；留空表示待映射。字段映射不会修改数据库结构。</p>
      <datalist id="${listId}">${actual.map((name) => `<option value="${esc(name)}"></option>`).join("")}</datalist>
      ${spec.columns.map((name) => {
        const value = Object.hasOwn(current, name) ? current[name] : actual.includes(name) ? name : "";
        return `<label class="kb-ontology-map-row"><span>${esc(name)}</span><input class="kb-ontology-column-input" type="text" list="${listId}"
          data-ontology-owner="${esc(spec.name)}" data-ontology-column="${esc(name)}" value="${esc(value)}" placeholder="选择实际字段或留空" aria-label="${esc(spec.name)}.${esc(name)} 的实际字段"></label>`;
      }).join("")}</details>`;
  }

  function mappingHtml(document, sourceId, mapping = {}, columnMapping = {}) {
    const source = sources.find((item) => item.source_id === sourceId);
    if (!source) return `<p class="kb-ontology-note">绑定数据源后，本体才会参与该数据源的分析。</p>`;
    return `<details class="kb-ontology-mapping" open><summary>表、查询对象与字段映射</summary>${tableSpecs(document).map((spec) => {
      const target = defaultTable(document, spec.name, source, mapping);
      const isSurface = Object.hasOwn(document.query_surface || {}, spec.name);
      return `<div class="kb-ontology-table" data-ontology-table-row="${esc(spec.name)}">
        <label class="kb-ontology-map-row"><span>${esc(spec.name)}${isSurface ? "（查询对象）" : ""}</span>
          <select data-ontology-table="${esc(spec.name)}" aria-label="${esc(spec.name)} 的实际表"><option value="">待映射</option>${Object.keys(source.tables).sort().map((table) =>
            `<option value="${esc(table)}"${target === table ? " selected" : ""}>${esc(table)}</option>`).join("")}</select></label>
        ${isSurface ? '<p class="kb-ontology-note">关联到实际对象不代表已实施授权过滤。</p>' : ""}
        <div data-ontology-field-map>${fieldsHtml(spec, source, target, columnMapping)}</div></div>`;
    }).join("")}</details>`;
  }

  function bindingHtml(document, binding = {}) {
    const source = boundSource(binding);
    return `<label class="kb-ontology-field"><span>关联数据源</span><select data-ontology-source aria-label="关联数据源">${sourceOptions(binding)}</select></label>
      <div data-ontology-mapping>${mappingHtml(document, source?.source_id || "", binding.mapping, binding.column_mapping)}</div>`;
  }

  function bindingValue(root) {
    const mapping = Object.create(null);
    const columnMapping = Object.create(null);
    root.querySelectorAll("[data-ontology-table]").forEach((element) => { mapping[element.dataset.ontologyTable] = element.value; });
    root.querySelectorAll("[data-ontology-column]").forEach((element) => {
      const owner = element.dataset.ontologyOwner;
      if (!Object.hasOwn(columnMapping, owner)) columnMapping[owner] = Object.create(null);
      columnMapping[owner][element.dataset.ontologyColumn] = element.value.trim();
    });
    return { source_id: root.querySelector("[data-ontology-source]")?.value || "", mapping, column_mapping: columnMapping };
  }

  function bindChanges(root, document) {
    root.onchange = (event) => {
      if (event.target.matches("[data-ontology-source]")) {
        root.querySelector("[data-ontology-mapping]").innerHTML = mappingHtml(document, event.target.value);
      } else if (event.target.matches("[data-ontology-table]")) {
        const source = sources.find((item) => item.source_id === root.querySelector("[data-ontology-source]").value);
        const spec = tableSpecs(document).find((item) => item.name === event.target.dataset.ontologyTable);
        event.target.closest("[data-ontology-table-row]").querySelector("[data-ontology-field-map]").innerHTML = fieldsHtml(spec, source, event.target.value);
      }
    };
  }

  function storageErrorHtml() {
    return storageError ? `<div class="kb-ontology-error" role="alert">${esc(storageError)}
      <button type="button" class="kb-act-btn" data-ontology-global="retry-persist">${esc(requestMessage("retryLocal"))}</button></div>` : "";
  }

  async function load() {
    const root = document.getElementById("kb-ontology-list");
    if (!root) return;
    const ticket = requests.beginRead("list");
    const category = categoryId();
    root.setAttribute("aria-busy", "true");
    root.innerHTML = `<p class="kb-ontology-note" role="status">${esc(requestMessage("loading"))}
      <button type="button" class="kb-act-btn" data-ontology-global="cancel">${esc(requestMessage("cancel"))}</button></p>`;
    try {
      const [items, context] = await Promise.all([
        request(`/api/knowledge/ontologies?category_id=${encodeURIComponent(category)}`, { signal: ticket.signal }),
        request("/api/knowledge/ontology-sources", { signal: ticket.signal }),
      ]);
      if (!ticket.isCurrent()) return;
      if (!Array.isArray(items)) throw new Error(requestMessage("invalid"));
      records = items;
      sources = context.sources || [];
      root.innerHTML = storageErrorHtml() + (records.length ? records.map((record) => {
        const binding = record.binding || {};
        const status = !binding.source_id ? "待绑定" : boundSource(binding) ? `关联：${binding.source_name}` : "待重新绑定";
        return `<article class="kb-ontology-row" data-ontology-id="${esc(record.id)}">
          <div class="kb-ontology-head"><strong>${esc(record.name)}</strong><span>v${esc(record.version)}</span></div>
          <p class="kb-ontology-note">${esc(record.source_name)} · ${esc(status)} · ${record.enabled ? "已启用" : "已停用"}</p>
          <p class="kb-ontology-note">${esc(summaryText(record.summary))}</p>
          <div class="kb-ontology-actions">
            <button type="button" class="kb-act-btn" data-ontology-action="inspect">查看、绑定与试用</button>
            <button type="button" class="kb-act-btn" data-ontology-action="toggle" aria-label="${record.enabled ? "停用" : "启用"} ${esc(record.name)}">${record.enabled ? "停用" : "启用"}</button>
            <a class="kb-act-btn" href="${esc(sourceUrl(`/api/knowledge/ontologies/${record.id}/source`))}" download>下载 YAML</a>
            <button type="button" class="kb-act-btn danger" data-ontology-action="delete">删除</button>
          </div><div data-ontology-detail></div><p class="kb-ontology-error" role="alert"></p></article>`;
      }).join("") : '<p class="kb-ontology-note">上传 YAML 本体，保存业务实体、关系、指标与查询规则。关联数据源后自动参与分析。</p>');
    } catch (error) {
      if (ticket.isCurrent() && !isSilentRequestError(error)) root.innerHTML = `<div class="kb-ontology-error" role="alert">${esc(requestErrorMessage(error))}
        <button type="button" class="kb-act-btn" data-ontology-global="reload">${esc(requestMessage("retry"))}</button></div>`;
    } finally {
      if (ticket.isCurrent()) root.removeAttribute("aria-busy");
      ticket.finish();
    }
  }

  async function persistMutation(ticket) {
    try {
      await persist();
      if (ticket.isCurrent()) storageError = "";
    } catch (error) {
      if (ticket.isCurrent() && !isSilentRequestError(error)) storageError = requestMessage("localSave", { message: error.message });
    }
  }

  async function act(event) {
    const retry = event.target.closest("[data-ontology-global]");
    if (retry) {
      const action = retry.dataset.ontologyGlobal;
      if (action === "reload") return load();
      if (action === "cancel") {
        requests.cancelRead("list");
        const root = document.getElementById("kb-ontology-list");
        root.removeAttribute("aria-busy");
        root.innerHTML = `<p class="kb-ontology-note" role="status">${esc(requestMessage("cancelled"))}
          <button type="button" class="kb-act-btn" data-ontology-global="reload">${esc(requestMessage("retry"))}</button></p>`;
        return;
      }
      const ticket = requests.beginWrite("persist");
      if (!ticket) return;
      retry.disabled = true;
      try { await persistMutation(ticket); if (ticket.isCurrent()) await load(); }
      finally { ticket.finish(); if (ticket.isCurrent()) retry.disabled = false; }
      return;
    }
    const button = event.target.closest("[data-ontology-action]");
    if (!button || button.disabled) return;
    const row = button.closest("[data-ontology-id]");
    const record = records.find((item) => item.id === row.dataset.ontologyId);
    if (!record) return;
    const action = button.dataset.ontologyAction;
    const errorEl = row.querySelector("[role=alert]");
    errorEl.textContent = "";
    const read = action === "inspect" || action === "preview";
    const ticket = read ? requests.beginRead(`row:${record.id}`) : requests.beginWrite(`row:${record.id}`);
    if (!ticket) return;
    const current = () => ticket.isCurrent() && row.isConnected;
    const buttons = [...row.querySelectorAll("button")];
    buttons.forEach((item) => { item.disabled = true; });
    try {
      if (action === "delete" && !await window.BAA.ui?.confirm?.({
        title: "删除业务本体", message: `确认删除“${record.name}”？其实体、关系与检索记录将一起删除。`, danger: true,
      })) return;
      if (!current()) return;
      const path = `/api/knowledge/ontologies/${record.id}`;
      if (action === "inspect") {
        const [full, context] = await Promise.all([request(path, { signal: ticket.signal }), request("/api/knowledge/ontology-sources", { signal: ticket.signal })]);
        if (!current()) return;
        sources = context.sources || [];
        const detail = row.querySelector("[data-ontology-detail]");
        detail.innerHTML = `<div class="kb-ontology-editor">${bindingHtml(full.document, full.binding)}
          <p class="kb-ontology-note">权限声明参与业务解释；身份认证和行级授权仍由后端应用实施。</p>
          <button type="button" class="btn-sm btn-sm-primary" data-ontology-action="bind">保存绑定</button>
          <details class="kb-ontology-definition"><summary>检索试用</summary>
            <p class="kb-ontology-note">查看本体为一次提问提供的定义、默认口径和关系，不调用聊天模型或执行 SQL。</p>
            <label class="kb-ontology-field"><span>分析问题</span><input type="text" maxlength="4000" data-ontology-question placeholder="例如：各班数学及格率" aria-label="检索试用问题"></label>
            <button type="button" class="kb-act-btn" data-ontology-action="preview">查看检索上下文</button>
            <pre data-ontology-preview-output aria-live="polite"></pre></details>
          ${warningsHtml(full.warnings)}${definitionsHtml(full.document)}</div>`;
        bindChanges(detail, full.document);
      } else if (action === "preview") {
        const question = row.querySelector("[data-ontology-question]").value.trim();
        if (!question) throw new Error(requestMessage("questionRequired"));
        const result = await request(`${path}/preview`, { method: "POST", signal: ticket.signal,
          headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) });
        if (!current()) return;
        const output = row.querySelector("[data-ontology-preview-output]");
        output.textContent = !result.bound_to_active_source ? requestMessage("unbound")
          : !result.enabled ? requestMessage("disabled") : !result.matches.length ? requestMessage("noMatches")
          : JSON.stringify(result.matches.map((match) => ({ ...match, entries: match.entries.map((entry) => ({ ...entry, content: JSON.parse(entry.content) })) })), null, 2);
      } else {
        if (action === "delete") await request(path, { method: "DELETE" });
        else {
          const patch = action === "toggle" ? { enabled: !record.enabled } : bindingValue(row.querySelector("[data-ontology-detail]"));
          await request(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) });
        }
        if (!current()) return;
        await persistMutation(ticket);
        if (!ticket.isCurrent()) return;
        await load();
        if (ticket.isCurrent() && !storageError) toast(requestMessage(action === "delete" ? "deleted" : "saved"));
      }
    } catch (error) {
      if (current() && !isSilentRequestError(error)) errorEl.textContent = requestErrorMessage(error);
    } finally {
      if (current()) buttons.forEach((item) => { item.disabled = false; });
      ticket.finish();
    }
  }

  function install() {
    const root = document.getElementById("kb-ontology-list");
    if (!root || root.dataset.ontologyInstalled) return;
    root.dataset.ontologyInstalled = "true";
    root.addEventListener("click", act);
  }

  async function showPreview(data) {
    preview = data;
    previewReady = false;
    savedPreview = null;
    const ticket = requests.beginRead("preview");
    const category = categoryId();
    sources = data.sources || [];
    const target = document.getElementById("kb-preview-list");
    document.getElementById("kb-parsing").classList.add("hidden");
    document.getElementById("kb-preview-area").classList.remove("hidden");
    document.getElementById("kb-preview-title").textContent = data.summary.domain_name;
    document.getElementById("kb-preview-sub").textContent = `v${data.summary.version} · ${summaryText(data.summary)}`;
    target.innerHTML = `<p class="kb-ontology-note" role="status">${esc(requestMessage("optionsLoading"))}</p>`;
    try {
    const existing = await request(`/api/knowledge/ontologies?category_id=${encodeURIComponent(category)}`, { signal: ticket.signal });
    if (preview !== data || !ticket.isCurrent()) return;
    target.innerHTML = `<div class="kb-ontology-preview">
      <label class="kb-ontology-field"><span>导入方式</span><select data-ontology-replace aria-label="新增或替换本体"><option value="">新增本体</option>${existing.map((item) =>
        `<option value="${esc(item.id)}">替换 ${esc(item.name)}（v${esc(item.version)}）</option>`).join("")}</select></label>
      ${bindingHtml(data.ontology)}<p class="kb-ontology-note">查询对象和字段可以映射到实际结构；权限声明不会自动授予访问权。</p>
      ${warningsHtml(data.warnings)}${definitionsHtml(data.ontology)}</div>`;
    bindChanges(target, data.ontology);
    target.querySelector("[data-ontology-replace]").addEventListener("change", (event) => {
      const binding = existing.find((item) => item.id === event.target.value)?.binding || {};
      const source = boundSource(binding);
      target.querySelector("[data-ontology-source]").value = source?.source_id || "";
      target.querySelector("[data-ontology-mapping]").innerHTML = mappingHtml(data.ontology, source?.source_id || "", binding.mapping, binding.column_mapping);
    });
    previewReady = true;
    } catch (error) {
      if (!ticket.isCurrent() || preview !== data || isSilentRequestError(error)) return;
      target.innerHTML = `<p class="kb-ontology-error" role="alert">${esc(requestErrorMessage(error))}</p>`;
      throw error;
    } finally { ticket.finish(); }
  }

  async function confirm(filename) {
    if (!preview) return null;
    if (!previewReady) throw new Error(requestMessage("optionsPending"));
    const ticket = requests.beginWrite("confirm");
    if (!ticket) return null;
    const submittedPreview = preview;
    const current = () => ticket.isCurrent() && preview === submittedPreview;
    try {
      const root = document.getElementById("kb-preview-list");
      const result = savedPreview?.preview === submittedPreview ? savedPreview.result : await request("/api/knowledge/confirm", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename, source_name: submittedPreview.source_name, category_id: categoryId(),
          ontology_id: root.querySelector("[data-ontology-replace]").value, ...bindingValue(root) }),
      });
      if (!current()) return null;
      savedPreview = { preview: submittedPreview, result };
      root.querySelectorAll("input, textarea, select").forEach(element => { element.disabled = true; });
      try { await persist(); }
      catch (error) {
        if (!current()) return null;
        throw new Error(requestMessage("localSave", { message: error.message }));
      }
      if (!current()) return null;
      await load();
      if (!current()) return null;
      preview = null;
      previewReady = false;
      savedPreview = null;
      return result;
    } finally { ticket.finish(); }
  }

  return { install, load, showPreview, confirm,
    reset: () => {
      requests.reset();
      preview = null;
      previewReady = false;
      savedPreview = null;
      records = [];
      sources = [];
      storageError = "";
      const root = document.getElementById("kb-ontology-list");
      if (root) { root.innerHTML = ""; root.removeAttribute("aria-busy"); }
      const target = document.getElementById("kb-preview-list");
      if (target) target.innerHTML = "";
    },
    isPreview: () => Boolean(preview),
    hasSavedPreview: () => Boolean(savedPreview && savedPreview.preview === preview),
  };
}
