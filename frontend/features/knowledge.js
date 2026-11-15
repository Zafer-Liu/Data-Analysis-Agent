/* knowledge_panel.js — Business Knowledge Base UI
 *
 * Depends on:  openOverlay / closeOverlay / toast / t  (dist/core.js, i18n.js)
 * API routes:  /api/knowledge/*  (api/knowledge.py)
 */
import { getUiIsland } from "../core/ui-registry.js";
import { closeOverlay, toast } from "../core/overlay.js";
import { ApiError, apiClient, isSilentRequestError, requestErrorMessage, requestMessage } from "../core/api-client.js";
import { createRequestOwner } from "../core/request-state.js";
import { eventBus } from "../core/event-bus.js";
import { state } from "../core/runtime.js";
import { createOntologyPanel } from "./ontology.js";
import {
  deleteLocalKnowledgeFileByServerFilename,
  deleteLocalKnowledgeFilesByCategory,
  getLocalKnowledgeSnapshot,
  isBrowserLocalMode,
  listLocalKnowledgeFiles,
  saveLocalKnowledgeFile,
  saveLocalKnowledgeSnapshot,
  updateLocalKnowledgeFile,
} from "../core/local-data-store.js";

// ── State ─────────────────────────────────────────────────────────────────────

const _kb = {
  tab:         "metrics",
  previewRecs: [],
  sourceFile:  "",
  categories: [],
  activeCategoryId: 1,
  categoriesLoaded: false,
  pendingSourceFile: null,
  importGeneration: 0,
  confirming: false,
  confirmResult: null,
  localFileSaved: false,
};

let workspaceGeneration = 0;
let formGeneration = 0;
const requests = createRequestOwner({ getScope: () => JSON.stringify([knowledgeSid(), workspaceGeneration, activeCategoryId()]) });
const ontologyPanel = createOntologyPanel({
  request: kbJson, categoryId: activeCategoryId,
  persist: () => kbPersistBrowserSnapshot({ strict: true }), sourceUrl: kbScopeUrl,
  getScope: () => JSON.stringify([knowledgeSid(), workspaceGeneration, activeCategoryId()]),
});
let browserKnowledgeRestorePromise = null;

function knowledgeSid() {
  return state.SID || sessionStorage.getItem("baa_session_id") || "";
}

function kbScopeUrl(path) {
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}session_id=${encodeURIComponent(knowledgeSid())}`;
}

function staleKnowledgeRequest() {
  return new ApiError(requestMessage("cancelled"), { code: "STALE_REQUEST", kind: "abort" });
}

function beginKnowledgeWrite(key) {
  const ticket = requests.beginWrite(key);
  if (!ticket) toast(requestMessage("busy"));
  return ticket;
}

function activeCategoryId() {
  const vk = getUiIsland("knowledge");
  const id = vk?.getActiveCategoryId?.() || _kb.activeCategoryId || 1;
  return Number(id) || 1;
}

function kbCategoryUrl(path) {
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}category_id=${encodeURIComponent(activeCategoryId())}`;
}

function kbJson(path, options = {}) {
  return apiClient.request(kbScopeUrl(path), { responseType: "json", ...options });
}

async function kbBuildBrowserSnapshot() {
  const scope = requests.capture();
  const categories = await kbJson("/api/knowledge/categories");
  if (!scope.isCurrent()) throw staleKnowledgeRequest();
  const categoryNames = new Map(categories.map((item) => [Number(item.id), item.name]));
  const [metrics, rules, notes, documents, ontologies] = await Promise.all([
    Promise.all(categories.map((item) => kbJson(`/api/knowledge/metrics?category_id=${item.id}`))).then((rows) => rows.flat()),
    Promise.all(categories.map((item) => kbJson(`/api/knowledge/rules?category_id=${item.id}`))).then((rows) => rows.flat()),
    Promise.all(categories.map((item) => kbJson(`/api/knowledge/notes?category_id=${item.id}`))).then((rows) => rows.flat()),
    listLocalKnowledgeFiles(),
    kbJson("/api/knowledge/ontologies?export=1"),
  ]);
  if (!scope.isCurrent()) throw staleKnowledgeRequest();
  const withCategory = (row) => ({
    ...row,
    category_name: categoryNames.get(Number(row.category_id)) || "",
  });
  return {
    version: 2,
    ontologies,
    categories: categories.map(({ name, enabled }) => ({ name, enabled: Boolean(enabled) })),
    metrics: metrics.map(withCategory),
    rules: rules.map(withCategory),
    notes: notes.map(withCategory),
    documents: documents.map((item) => ({
      id: item.id,
      name: item.name,
      server_filename: item.server_filename,
      category_name: item.category_name,
    })),
  };
}

async function kbPersistBrowserSnapshot({ strict = false } = {}) {
  if (!isBrowserLocalMode()) return;
  const scope = requests.capture();
  try {
    const snapshot = await kbBuildBrowserSnapshot();
    if (!scope.isCurrent()) throw staleKnowledgeRequest();
    await saveLocalKnowledgeSnapshot(snapshot);
  } catch (error) {
    if (isSilentRequestError(error) || !scope.isCurrent()) { if (strict) throw staleKnowledgeRequest(); return; }
    console.warn("[knowledge] browser snapshot failed", error);
    toast(requestMessage("localSave", { message: error.message }));
    if (strict) throw error;
  }
}

async function kbRestoreBrowserKnowledge() {
  if (!isBrowserLocalMode() || !knowledgeSid()) return false;
  if (!browserKnowledgeRestorePromise) {
    const scope = requests.capture();
    const check = () => { if (!scope.isCurrent()) throw staleKnowledgeRequest(); };
    browserKnowledgeRestorePromise = (async () => {
      const snapshot = await getLocalKnowledgeSnapshot();
      check();
      if (!snapshot) {
        await kbPersistBrowserSnapshot();
        return false;
      }
      const restored = await kbJson("/api/knowledge/restore", { method: "POST", body: { snapshot } });
      check();
      const documentMeta = new Map((snapshot.documents || []).map((item) => [item.id, item]));
      const files = await listLocalKnowledgeFiles();
      check();
      for (const record of files) {
        const meta = documentMeta.get(record.id) || {};
        const file = record.blob instanceof File ? record.blob : new File([record.blob], record.name, {
          type: record.type || "application/octet-stream", lastModified: record.last_modified || Date.now(),
        });
        const formData = new FormData();
        formData.append("file", file, record.name);
        const categoryName = record.category_name || meta.category_name || "";
        formData.append("category_name", categoryName);
        const data = await kbJson("/api/knowledge/restore-file", { method: "POST", body: formData });
        check();
        await updateLocalKnowledgeFile(record.id, { server_filename: data.filename, category_name: categoryName });
        check();
      }
      await kbPersistBrowserSnapshot();
      return Boolean(restored.ok);
    })().catch((error) => {
      if (!scope.isCurrent() || isSilentRequestError(error)) return false;
      browserKnowledgeRestorePromise = null;
      console.warn("[knowledge] browser restore failed", error);
      toast(requestMessage("restoreFailed", { message: requestErrorMessage(error) }));
      return false;
    });
  }
  return browserKnowledgeRestorePromise;
}


async function kbLoadCategories() {
  const vk = getUiIsland("knowledge");
  const ticket = requests.beginRead("categories");
  try {
    const categories = await kbJson("/api/knowledge/categories", { signal: ticket.signal });
    if (!ticket.isCurrent()) return;
    _kb.categories = Array.isArray(categories) ? categories : [];
    _kb.categoriesLoaded = true;
    if (!_kb.categories.find(c => Number(c.id) === Number(_kb.activeCategoryId))) {
      _kb.activeCategoryId = Number(_kb.categories[0]?.id || 1);
    }
    vk?.setCategories?.(_kb.categories, _kb.activeCategoryId);
    return true;
  } catch (e) {
    if (!ticket.isCurrent() || isSilentRequestError(e)) return;
    _kb.categoriesLoaded = false;
    vk?.setCategoryErr?.(requestErrorMessage(e));
  } finally { ticket.finish(); }
}

async function kbEnsureCategories() {
  return _kb.categoriesLoaded || await kbLoadCategories();
}

async function kbAddCategory(name) {
  const value = String(name || "").trim();
  const vk = getUiIsland("knowledge");
  if (!value) { vk?.setCategoryErr?.(requestMessage("enterName")); return; }
  const ticket = beginKnowledgeWrite("category:add");
  if (!ticket) return;
  vk?.setCategoryBusy?.(true);
  try {
    const data = await kbJson("/api/knowledge/categories", { method: "POST", body: { name: value } });
    if (!ticket.isCurrent()) return;
    vk?.setCategoryErr?.("");
    // Finish the old category's UI before selecting the newly created scope.
    vk?.setCategoryBusy?.(false);
    kbSelectCategory(Number(data.id || _kb.activeCategoryId));
    await kbLoadCategories();
    await kbPersistBrowserSnapshot();
  } catch (e) {
    if (ticket.isCurrent() && !isSilentRequestError(e)) vk?.setCategoryErr?.(requestErrorMessage(e));
  } finally { if (ticket.isCurrent()) vk?.setCategoryBusy?.(false); ticket.finish(); }
}

async function kbToggleCategory(id) {
  const cid = Number(id);
  const vk = getUiIsland("knowledge");
  const rec = _kb.categories.find(c => Number(c.id) === cid);
  if (!rec) return;
  const ticket = beginKnowledgeWrite(`category:${cid}`);
  if (!ticket) return;
  rec.pending = true;
  vk?.setCategories?.(_kb.categories, _kb.activeCategoryId);
  try {
    const data = await kbJson(`/api/knowledge/categories/${cid}/toggle`, { method: "POST" });
    if (!ticket.isCurrent()) return;
    Object.assign(rec, data);
    await kbPersistBrowserSnapshot();
  } catch (e) {
    if (ticket.isCurrent() && !isSilentRequestError(e)) toast(requestErrorMessage(e));
  } finally {
    rec.pending = false;
    if (ticket.isCurrent()) vk?.setCategories?.(_kb.categories, _kb.activeCategoryId);
    ticket.finish();
  }
}

async function kbDeleteCategory(id) {
  const cid = Number(id);
  const rec = _kb.categories.find(c => Number(c.id) === cid);
  if (!rec) return;
  const ticket = beginKnowledgeWrite(`category:${cid}`);
  if (!ticket) return;
  try {
    const accepted = await window.BAA.ui?.confirm?.({
      title: "删除业务分类", message: `确认删除“${rec.name}”？该分类下的指标、规则、背景知识和文档索引将被永久删除。`, danger: true,
    });
    if (!accepted || !ticket.isCurrent()) return;
    await kbJson(`/api/knowledge/categories/${cid}`, { method: "DELETE" });
    if (!ticket.isCurrent()) return;
    await deleteLocalKnowledgeFilesByCategory(rec.name);
    if (!ticket.isCurrent()) return;
    if (Number(_kb.activeCategoryId) === cid) kbSelectCategory(1);
    await kbLoadCategories();
    await loadByTab(_kb.tab);
    await kbPersistBrowserSnapshot();
  } catch (e) {
    if (ticket.isCurrent() && !isSilentRequestError(e)) toast(requestErrorMessage(e));
  } finally { ticket.finish(); }
}

function kbSelectCategory(id) {
  requests.reset();
  formGeneration += 1;
  kbResetImport();
  _kb.activeCategoryId = Number(id) || 1;
  const vk = getUiIsland("knowledge");
  vk?.closeForm?.();
  vk?.setCategoryBusy?.(false);
  for (const type of ["metrics", "rules", "notes"]) vk?.setItems?.(type, []);
  vk?.setActiveCategory?.(_kb.activeCategoryId);
  loadByTab(_kb.tab);
}

function kbOpenImport() {
  kbSwitchTab("import");
}

// ── Tab switching ─────────────────────────────────────────────────────────────

async function loadByTab(tab) {
  const scope = JSON.stringify([knowledgeSid(), workspaceGeneration]);
  if (!await kbEnsureCategories()) return;
  if (scope !== JSON.stringify([knowledgeSid(), workspaceGeneration]) || tab !== _kb.tab) return;
  if      (tab === "metrics") return kbLoadMetrics();
  else if (tab === "rules")   return kbLoadRules();
  else if (tab === "notes")   return kbLoadNotes();
  else if (tab === "import")  { kbResetImport(); ontologyPanel.install(); return Promise.all([kbLoadFiles(), ontologyPanel.load()]); }
}

function kbSwitchTab(tab, _btn) {
  _kb.tab = tab;  // 保持 _kb.tab 同步（import 区仍用）
  const vk = getUiIsland("knowledge");
  if (vk && vk.isAvailable()) {
    vk.setTab(tab);
    loadByTab(tab);
  }
}

// ── Refresh (manual) ──────────────────────────────────────────────────────────

async function kbRefresh(type) {
  if      (type === "metrics") await kbLoadMetrics();
  else if (type === "rules")   await kbLoadRules();
  else if (type === "notes")   await kbLoadNotes();
}

// ── Load lists ────────────────────────────────────────────────────────────────

async function kbLoadList(type) {
  const vk = getUiIsland("knowledge");
  if (!vk?.isAvailable()) return;
  const ticket = requests.beginRead(`list:${type}`);
  vk.setListStatus(type, { loading: true, err: "" });
  try {
    const data = await kbJson(kbCategoryUrl(`/api/knowledge/${type}`), { signal: ticket.signal });
    if (!ticket.isCurrent()) return;
    if (!Array.isArray(data)) throw new ApiError(requestMessage("invalid"), { code: "INVALID_RESPONSE", kind: "parse" });
    vk.setItems(type, data);
  } catch (error) {
    if (ticket.isCurrent() && !isSilentRequestError(error)) vk.setListStatus(type, { loading: false, err: requestErrorMessage(error) });
  } finally { ticket.finish(); }
}

const kbLoadMetrics = () => kbLoadList("metrics");
const kbLoadRules = () => kbLoadList("rules");
const kbLoadNotes = () => kbLoadList("notes");

function kbCancelList(type) {
  requests.cancelRead(`list:${type}`);
  getUiIsland("knowledge")?.setListStatus?.(type, { loading: false, err: requestMessage("cancelled") });
}

// ── Card renderers ────────────────────────────────────────────────────────────

function esc(s) {
  return String(s || "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

// ── Toggle enabled ────────────────────────────────────────────────────────────

async function kbToggle(type, id) {
  const vk = getUiIsland("knowledge");
  if (!vk?.isAvailable() || !vk.getItem(type, id)) return;
  const ticket = beginKnowledgeWrite(`item:${type}:${id}`);
  if (!ticket) return;
  vk.updateItem(type, id, { pending: true });
  try {
    const data = await kbJson(`/api/knowledge/${type}/${id}/toggle`, { method: "POST" });
    if (!ticket.isCurrent()) return;
    vk.updateItem(type, id, data);
    await kbPersistBrowserSnapshot();
  } catch (error) {
    if (ticket.isCurrent() && !isSilentRequestError(error)) {
      toast(requestErrorMessage(error));
      if (error.outcomeUnknown) await kbRefresh(type);
    }
  } finally {
    if (ticket.isCurrent()) vk.updateItem(type, id, { pending: false });
    ticket.finish();
  }
}

// ── Form: open ────────────────────────────────────────────────────────────────

async function kbOpenForm(type, id = null) {
  const vk = getUiIsland("knowledge");
  if (!vk?.isAvailable() || !["metrics", "rules", "notes"].includes(type)) return;
  const version = ++formGeneration;
  const ticket = requests.beginRead("form");
  try {
    let rec = null;
    if (id !== null) {
      const list = await kbJson(kbCategoryUrl(`/api/knowledge/${type}`), { signal: ticket.signal });
      if (!ticket.isCurrent() || version !== formGeneration) return;
      rec = list.find(r => r.id === id) || null;
      if (!rec) throw new Error(requestMessage("recordMissing"));
    }
    vk.openForm({ type, mode: id !== null ? "edit" : "add", editId: id, rec });
    window.BAA?.sidebar?.openKbInlineForm?.();
  } catch (error) {
    if (ticket.isCurrent() && !isSilentRequestError(error)) toast(requestErrorMessage(error));
  } finally { ticket.finish(); }
}

// ── Form: submit ──────────────────────────────────────────────────────────────

async function kbSubmitForm() {
  const vk = getUiIsland("knowledge");
  if (!vk || !vk.isAvailable()) return;
  const fv = vk.getFormValues();
  const { type, mode, editId, body } = fv;
  body.category_id = activeCategoryId();
  if (type === "metrics" && !body.name)    { vk.setFormErr(requestMessage("metricRequired")); return; }
  if (type === "rules" && !body.rule_id)   { vk.setFormErr(requestMessage("ruleRequired")); return; }
  if (type === "notes" && !body.topic)     { vk.setFormErr(requestMessage("topicRequired")); return; }
  const ticket = beginKnowledgeWrite(mode === "edit" ? `item:${type}:${editId}` : "form:add");
  if (!ticket) return;
  const version = formGeneration;
  const currentForm = () => ticket.isCurrent() && version === formGeneration;
  vk.setFormErr("");
  vk.setFormBusy(true);
  const method = mode === "edit" ? "PUT" : "POST";
  const url = mode === "edit" ? `/api/knowledge/${type}/${editId}` : `/api/knowledge/${type}`;
  try {
    await kbJson(url, { method, body });
    if (!ticket.isCurrent()) return;
    if (currentForm()) {
      closeOverlay("ov-kb-form");
      vk.closeForm();
      window.BAA?.sidebar?.closeKbInlineForm?.();
    }
    toast(requestMessage(mode === "edit" ? "updated" : "saved"));
    await loadByTab(type);
    if (ticket.isCurrent()) await kbPersistBrowserSnapshot();
  } catch (error) {
    if (currentForm() && !isSilentRequestError(error)) vk.setFormErr(requestErrorMessage(error));
  } finally {
    if (currentForm()) vk.setFormBusy(false);
    ticket.finish();
  }
}

// ── Delete ────────────────────────────────────────────────────────────────────

async function kbDelete(type, id) {
  const vk = getUiIsland("knowledge");
  if (!vk?.isAvailable()) return;
  const ticket = beginKnowledgeWrite(`item:${type}:${id}`);
  if (!ticket) return;
  vk.updateItem(type, id, { pending: true });
  try {
    if (!await window.BAA.ui?.confirm?.({ title: "删除知识记录", message: "确认删除这条记录？", danger: true }) || !ticket.isCurrent()) return;
    await kbJson(`/api/knowledge/${type}/${id}`, { method: "DELETE" });
    if (!ticket.isCurrent()) return;
    vk.removeItem(type, id);
    toast(requestMessage("deleted"));
    await kbPersistBrowserSnapshot();
  } catch (error) {
    if (ticket.isCurrent() && !isSilentRequestError(error)) toast(requestErrorMessage(error));
  } finally {
    if (ticket.isCurrent()) vk.updateItem(type, id, { pending: false });
    ticket.finish();
  }
}

// ── Historical source files ───────────────────────────────────────────────────

async function kbLoadFiles() {
  const list = document.getElementById("kb-files-list");
  if (!list) return;
  const ticket = requests.beginRead("files");
  list.setAttribute("aria-busy", "true");
  list.innerHTML = `<div class="kb-empty kb-loading-text" role="status">${esc(requestMessage("loading"))}</div>`;
  try {
    const files = await kbJson("/api/knowledge/files", { signal: ticket.signal });
    if (!ticket.isCurrent()) return;
    if (!Array.isArray(files)) throw new Error(requestMessage("invalid"));
    if (!files.length) {
      list.innerHTML = `<div class="kb-empty kb-loading-text" role="status">${esc(requestMessage("empty"))}</div>`;
      return;
    }
    list.innerHTML = files.map(f => {
      const date = new Date(f.mtime * 1000).toLocaleDateString(globalThis.getLang?.() === "en" ? "en" : "zh-CN",
        { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
      const kb  = f.size > 1024 * 1024
        ? (f.size / 1024 / 1024).toFixed(1) + " MB"
        : Math.round(f.size / 1024) + " KB";
      return `
      <div class="kb-file-row">
        <span class="kb-file-icon">${f.filename.endsWith(".docx") ? "📝" : "📊"}</span>
        <span class="kb-file-name" title="${esc(f.filename)}">${esc(f.filename)}</span>
        <span class="kb-file-meta">${kb} · ${date}</span>
        <button class="kb-file-delete" type="button" title="删除源文件"
                aria-label="删除源文件 ${esc(f.filename)}"
                data-action="kbDeleteFile" data-filename="${esc(f.filename)}">×</button>
      </div>`;
    }).join("");
  } catch (error) {
    if (!ticket.isCurrent() || isSilentRequestError(error)) return;
    list.innerHTML = `<div class="kb-request-error" role="alert">${esc(requestErrorMessage(error))}
      <button type="button" class="kb-act-btn" data-action="kbLoadFiles">${esc(requestMessage("retry"))}</button></div>`;
  } finally {
    if (ticket.isCurrent()) list.removeAttribute("aria-busy");
    ticket.finish();
  }
}

async function kbDeleteFile(filename) {
  const ticket = beginKnowledgeWrite(`file:${filename}`);
  if (!ticket) return;
  try {
    if (!await window.BAA.ui?.confirm?.({ title: "删除知识文件", message: `确认删除文件“${filename}”？`, danger: true }) || !ticket.isCurrent()) return;
    await kbJson(`/api/knowledge/files/${encodeURIComponent(filename)}`, { method: "DELETE" });
    if (!ticket.isCurrent()) return;
    await deleteLocalKnowledgeFileByServerFilename(filename);
    if (!ticket.isCurrent()) return;
    await kbPersistBrowserSnapshot();
    if (!ticket.isCurrent()) return;
    await ontologyPanel.load();
    if (!ticket.isCurrent()) return;
    toast(requestMessage("deleted"));
    await kbLoadFiles();
  } catch (error) {
    if (ticket.isCurrent() && !isSilentRequestError(error)) toast(requestErrorMessage(error));
  } finally { ticket.finish(); }
}

// ── Import: file selection & drag-drop ────────────────────────────────────────

function kbResetImport() {
  _kb.importGeneration += 1;
  _kb.confirmResult = null;
  _kb.localFileSaved = false;
  _kb.confirming = false;
  const confirm = document.querySelector('[data-action="kbConfirmImport"]');
  if (confirm) confirm.disabled = false;
  document.getElementById("kb-preview-list").innerHTML = "";
  ontologyPanel.reset();
  document.getElementById("kb-confirm-label").textContent = "全部入库";
  document.getElementById("kb-parsing").classList.add('hidden');
  document.getElementById("kb-preview-area").classList.add('hidden');
  document.getElementById("kb-import-err").textContent     = "";
  document.getElementById("kb-import-ok").textContent      = "";
  document.getElementById("kb-file-input").value           = "";
  document.getElementById("kb-drop-zone").classList.remove('hidden');
  _kb.previewRecs = [];
  _kb.sourceFile = "";
  _kb.pendingSourceFile = null;
}

function kbOnDrop(e) {
  e.preventDefault();
  document.getElementById("kb-drop-zone").classList.remove("drag-over");
  const file = e.dataTransfer.files[0];
  if (file) return kbParseFile(file);
}

function kbOnFileSelect(e) {
  const file = e.target.files[0];
  if (file) return kbParseFile(file);
}

document.addEventListener("DOMContentLoaded", () => {
  const zone = document.getElementById("kb-drop-zone");
  if (!zone) return;
  zone.addEventListener("dragover",  () => zone.classList.add("drag-over"));
  zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
});

async function kbParseFile(file) {
  const ext = file.name.split(".").pop().toLowerCase();
  if (!["xlsx", "xls", "docx", "yaml", "yml"].includes(ext)) {
    document.getElementById("kb-import-err").textContent = requestMessage("invalidFile");
    return;
  }
  if (["yaml", "yml"].includes(ext) && file.size > 256 * 1024) {
    document.getElementById("kb-import-err").textContent = requestMessage("fileTooLarge");
    return;
  }

  const ticket = beginKnowledgeWrite("import:parse");
  if (!ticket) return;
  kbResetImport();
  const importTicket = _kb.importGeneration;
  const current = () => ticket.isCurrent() && importTicket === _kb.importGeneration;
  document.getElementById("kb-drop-zone").classList.add('hidden');
  document.getElementById("kb-parsing").classList.remove('hidden');
  document.getElementById("kb-import-err").textContent   = "";
  document.getElementById("kb-import-ok").textContent    = "";

  const formData = new FormData();
  formData.append("file", file);
  const sid = state.SID || sessionStorage.getItem("baa_session_id") || "";
  formData.append("session_id", sid);
  // Also pass the currently selected provider so the backend uses the exact model
  const provider = document.getElementById("model-sel")?.value || "";
  if (provider) formData.append("provider", provider);

  try {
    const data = await kbJson("/api/knowledge/parse", { method: "POST", body: formData, timeoutMs: 120000 });
    if (!current()) return;

    _kb.previewRecs = data.preview || [];
    _kb.sourceFile = data.filename || "";
    _kb.pendingSourceFile = file;
    if (data.format === "ontology") {
      document.getElementById("kb-confirm-label").textContent = "保存本体";
      await ontologyPanel.showPreview(data);
    } else {
      kbRenderPreview(data);
    }
    if (current()) kbLoadFiles();
  } catch (e) {
    if (!current() || isSilentRequestError(e)) return;
    ontologyPanel.reset();
    _kb.sourceFile = "";
    _kb.pendingSourceFile = null;
    document.getElementById("kb-preview-area").classList.add("hidden");
    document.getElementById("kb-parsing").classList.add('hidden');
    document.getElementById("kb-drop-zone").classList.remove('hidden');
    document.getElementById("kb-import-err").textContent = requestErrorMessage(e);
  } finally { ticket.finish(); }
}

// ── Import: preview rendering ─────────────────────────────────────────────────

const _KB_TABLE_LABELS = {
  metrics:        "📐 指标",
  business_rules: "🛡 规则",
  context_notes:  "📝 背景",
};

const _KB_FIELDS_META = {
  metrics: [
    { key: "name",         label: "指标名称",  required: true  },
    { key: "alias",        label: "别名",       required: false },
    { key: "definition",   label: "定义",       required: false, multiline: true },
    { key: "sql_template", label: "SQL 模板",   required: false, multiline: true },
    { key: "notes",        label: "备注",       required: false, multiline: true },
  ],
  business_rules: [
    { key: "rule_id",     label: "规则 ID",  required: true  },
    { key: "description", label: "描述",      required: false, multiline: true },
    { key: "condition",   label: "违反条件",  required: false, multiline: true },
    { key: "severity",    label: "严重程度",  required: false },
  ],
  context_notes: [
    { key: "topic",   label: "主题",  required: true  },
    { key: "content", label: "内容",  required: false, multiline: true },
    { key: "tags",    label: "标签",  required: false },
  ],
};

function kbRenderPreview(data) {
  document.getElementById("kb-parsing").classList.add('hidden');
  const recs = _kb.previewRecs;
  const fmtLabel = data.format === "structured" ? "模板格式（直接映射）"
                 : data.format === "mixed"       ? "混合格式（部分模板 + LLM 提取）"
                 :                                 "自由文本（LLM 提取）";

  document.getElementById("kb-preview-title").textContent =
    "解析结果";
  document.getElementById("kb-preview-sub").textContent =
    `${recs.length} 条 · ${fmtLabel}`;

  const listEl = document.getElementById("kb-preview-list");
  listEl.innerHTML = recs.length
    ? recs.map((rec, idx) => kbPreviewCard(rec, idx)).join("")
    : '<div class="kb-empty">未提取到任何知识条目</div>';

  document.getElementById("kb-preview-area").classList.remove('hidden');
}

function kbPreviewCard(rec, idx) {
  const table  = rec.table || "metrics";
  const label  = _KB_TABLE_LABELS[table] || table;
  const fields = _KB_FIELDS_META[table]  || [];

  const fieldsHtml = fields.map(f => {
    const val = rec[f.key] || "";
    const inputEl = f.multiline
      ? `<textarea class="kb-prev-input" rows="2"
           data-idx="${idx}" data-key="${f.key}"
           data-input="kbPreviewUpdate">${esc(val)}</textarea>`
      : `<input class="kb-prev-input" type="text" value="${esc(val)}"
           data-idx="${idx}" data-key="${f.key}"
           data-input="kbPreviewUpdate">`;
    return `
      <div class="kb-prev-field${f.multiline ? " kb-prev-field-wide" : ""}">
        <div class="kb-prev-label">${f.label}${f.required ? " *" : ""}</div>
        ${inputEl}
      </div>`;
  }).join("");

  return `
  <div class="kb-prev-card" id="kb-prev-card-${idx}">
    <div class="kb-prev-card-head">
      <span class="kb-prev-card-type">${label}</span>
      <button class="kb-prev-delete" title="移除此条" data-action="kbPreviewRemove" data-idx="${idx}">×</button>
    </div>
    <div class="kb-prev-fields">${fieldsHtml}</div>
  </div>`;
}

function kbPreviewUpdate(el) {
  const idx = parseInt(el.dataset.idx, 10);
  _kb.previewRecs[idx][el.dataset.key] = el.value;
}

function kbPreviewRemove(idx) {
  _kb.previewRecs[idx] = null;
  const card = document.getElementById(`kb-prev-card-${idx}`);
  if (card) card.classList.add('hidden');
  const remaining = _kb.previewRecs.filter(r => r !== null).length;
  document.getElementById("kb-preview-title").textContent =
    `解析结果 · 剩余 ${remaining} 条`;
}

function kbCancelImport() {
  if (_kb.confirming) return;
  kbResetImport();
  document.getElementById("kb-import-err").textContent = requestMessage("cancelledImport")
    + (requests.hasWrite("import:parse") ? ` ${requestMessage("unknown")}` : "");
}

// ── Import: confirm ───────────────────────────────────────────────────────────

async function kbConfirmImport() {
  const records = _kb.previewRecs.filter(r => r !== null);
  if (!records.length && !_kb.sourceFile) {
    document.getElementById("kb-import-err").textContent = requestMessage("noImport");
    return;
  }
  const ticket = beginKnowledgeWrite("import:confirm");
  if (!ticket) return;
  const version = _kb.importGeneration;
  const current = () => ticket.isCurrent() && version === _kb.importGeneration;
  const okEl = document.getElementById("kb-import-ok");
  const errEl = document.getElementById("kb-import-err");
  const confirmButton = document.querySelector('[data-action="kbConfirmImport"]');
  const file = _kb.pendingSourceFile;
  const filename = _kb.sourceFile;
  const category = activeCategoryId();
  const categoryName = _kb.categories.find((item) => Number(item.id) === category)?.name || "";
  _kb.confirming = true;
  confirmButton.disabled = true;
  okEl.textContent = "";
  errEl.textContent = "";
  try {
    if (ontologyPanel.isPreview()) {
      const result = await ontologyPanel.confirm(filename);
      if (!result || !current()) return;
      kbResetImport();
      okEl.textContent = requestMessage("saved");
      kbLoadFiles();
      return;
    }
    // A retry after local storage failure only saves the local copy.
    const data = _kb.confirmResult || await kbJson("/api/knowledge/confirm", {
      method: "POST", body: { records, filename, category_id: category },
    });
    if (!current()) return;
    _kb.confirmResult = data;
    document.getElementById("kb-preview-list").querySelectorAll("input, textarea, select, button").forEach(element => { element.disabled = true; });
    if (file && filename && !_kb.localFileSaved) {
      await saveLocalKnowledgeFile({ file, serverFilename: filename, categoryName });
      if (!current()) return;
      _kb.localFileSaved = true;
    }
    await kbPersistBrowserSnapshot({ strict: true });
    if (!current()) return;
    const inserted = data.inserted || {};
    kbResetImport();
    okEl.textContent = requestMessage("importSaved", { metrics: inserted.metrics || 0, rules: inserted.rules || 0,
      notes: inserted.notes || 0, chunks: data.rag?.chunks || 0 });
    kbLoadFiles();
  } catch (error) {
    if (!current() || isSilentRequestError(error)) return;
    errEl.textContent = _kb.confirmResult ? requestMessage("localSave", { message: error.message }) : requestErrorMessage(error);
    if (_kb.confirmResult || ontologyPanel.hasSavedPreview()) document.getElementById("kb-confirm-label").textContent = requestMessage("retryLocal");
  } finally {
    if (current()) { _kb.confirming = false; confirmButton.disabled = false; }
    ticket.finish();
  }
}

// ── Init: refresh data when modal opens ──────────────────────────────────────

let __kbOverlayListenerInstalled = false;

function syncKnowledgeIsland(vk) {
  if (!vk?.isAvailable()) return false;
  vk.sync({
    onSwitchTab:  (tab)       => kbSwitchTab(tab),
    onToggle:     (type, id)  => kbToggle(type, id),
    onOpenForm:   (type, id)  => kbOpenForm(type, id),
    onSubmitForm: ()          => kbSubmitForm(),
    onCancelForm: ()          => kbCancelForm(),
    onCancelList: (type)      => kbCancelList(type),
    onDelete:     (type, id)  => kbDelete(type, id),
    onSelectCategory: (id)     => kbSelectCategory(id),
    onAddCategory: (name)      => kbAddCategory(name),
    onToggleCategory: (id)     => kbToggleCategory(id),
    onDeleteCategory: (id)     => kbDeleteCategory(id),
    onOpenImport: ()           => kbOpenImport(),
  });
  return true;
}

export function installKnowledgePanel() {
  // F3 mid-term: subscribe to the overlay:open event instead of monkey-patching
  // window.openOverlay (core/overlay.js emits after the overlay is open, so the
  // island is already ensured by the legacy-core wrapper at that point).
  if (!__kbOverlayListenerInstalled) {
    __kbOverlayListenerInstalled = true;
    eventBus.on("state:SID", () => {
      resetKnowledgeScope();
      const panel = document.getElementById("sb-panel-knowledge");
      if (panel && !panel.classList.contains("collapsed")) loadByTab(_kb.tab);
    });
    eventBus.on("overlay:open", ({ id }) => {
      // Legacy path: still handle ov-knowledge if it ever gets opened programmatically.
      if (id !== "ov-knowledge") return;
      const vk = getUiIsland("knowledge");
      if (syncKnowledgeIsland(vk)) {
        vk.onOpen();
      } else {
        loadByTab(_kb.tab);
      }
    });
  }

  // New path: trigger data loading when the sb-panel-knowledge panel opens.
  // openSidePanel() first loads the Vue island, then calls sidebar.openPanel().
  // We hook after the island is ready by watching for the panel element gaining
  // the open class via a MutationObserver.
  const panelEl = document.getElementById("sb-panel-knowledge");
  if (panelEl && !panelEl.__kbPanelObserved) {
    panelEl.__kbPanelObserved = true;
    const obs = new MutationObserver(() => {
      if (!panelEl.classList.contains("collapsed")) {
        const vk = getUiIsland("knowledge");
        if (syncKnowledgeIsland(vk)) {
          vk.onOpen();
        } else {
          loadByTab(_kb.tab);
        }
      }
    });
    obs.observe(panelEl, { attributes: true, attributeFilter: ["class"] });
  }

  syncKnowledgeIsland(getUiIsland("knowledge"));
}

/**
 * Called after workspace mount/unmount to refresh the knowledge panel if it
 * is currently open. Without this, the panel keeps showing stale data (or
 * empty state) because the MutationObserver only fires on class changes,
 * not on workspace state changes. Workspace switch changes the knowledge
 * scope (workspace_id), so the API returns data for the new workspace.
 */
function resetKnowledgeScope() {
  requests.reset();
  formGeneration += 1;
  browserKnowledgeRestorePromise = null;
  _kb.categories = [];
  _kb.categoriesLoaded = false;
  _kb.activeCategoryId = 1;
  const vk = getUiIsland("knowledge");
  vk?.closeForm?.();
  vk?.setCategories?.([], 1);
  vk?.setCategoryBusy?.(false);
  for (const type of ["metrics", "rules", "notes"]) vk?.setItems?.(type, []);
  if (document.getElementById("kb-preview-area")) kbResetImport();
  const files = document.getElementById("kb-files-list");
  if (files) { files.innerHTML = ""; files.removeAttribute("aria-busy"); }
}

export function kbOnWorkspaceChanged() {
  workspaceGeneration += 1;
  resetKnowledgeScope();
  eventBus.emit("knowledge:scope-changed");
  const panelEl = document.getElementById("sb-panel-knowledge");
  if (!panelEl || panelEl.classList.contains("collapsed")) return;
  const vk = getUiIsland("knowledge");
  if (syncKnowledgeIsland(vk)) {
    vk.onOpen();
  } else {
    // Island not yet loaded (panel open but island lazy-load pending) — fall back to direct load.
    loadByTab(_kb.tab);
  }
}

export function kbCancelForm() {
  formGeneration += 1;
  requests.cancelRead("form");
  getUiIsland("knowledge")?.closeForm?.();
  window.BAA?.sidebar?.closeKbInlineForm?.();
}

export const knowledge = Object.freeze({
  kbSwitchTab,
  kbRefresh,
  kbLoadCategories,
  kbSelectCategory,
  kbAddCategory,
  kbToggleCategory,
  kbDeleteCategory,
  kbOpenImport,
  kbOnWorkspaceChanged,
  kbOpenForm,
  kbSubmitForm,
  kbCancelForm,
  kbLoadFiles,
  kbDeleteFile,
  kbOnDrop,
  kbOnFileSelect,
  kbPreviewUpdate,
  kbPreviewRemove,
  kbCancelImport,
  kbConfirmImport,
  restoreBrowserKnowledge: kbRestoreBrowserKnowledge,
});
