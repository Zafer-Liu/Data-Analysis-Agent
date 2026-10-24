/* knowledge_panel.js — Business Knowledge Base UI
 *
 * Depends on:  openOverlay / closeOverlay / toast / t  (dist/core.js, i18n.js)
 * API routes:  /api/knowledge/*  (api/knowledge.py)
 */
import { getUiIsland } from "../core/ui-registry.js";
import { toast } from "../core/overlay.js";
import { eventBus } from "../core/event-bus.js";
import { state } from "../core/runtime.js";
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
};

let browserKnowledgeRestorePromise = null;

function kbScopeUrl(path) {
  // Prefer state.SID (always current) over the legacy SID global or sessionStorage.
  const sid = state.SID
            || sessionStorage.getItem("baa_session_id") || "";
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}session_id=${encodeURIComponent(sid)}`;
}

function kbFetch(path, options) {
  return fetch(kbScopeUrl(path), options);
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

async function kbJson(path, options) {
  const response = await kbFetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "知识库请求失败");
  return data;
}

async function kbBuildBrowserSnapshot() {
  const categories = await kbJson("/api/knowledge/categories");
  const categoryNames = new Map(categories.map((item) => [Number(item.id), item.name]));
  const [metrics, rules, notes, documents] = await Promise.all([
    Promise.all(categories.map((item) => kbJson(`/api/knowledge/metrics?category_id=${item.id}`))).then((rows) => rows.flat()),
    Promise.all(categories.map((item) => kbJson(`/api/knowledge/rules?category_id=${item.id}`))).then((rows) => rows.flat()),
    Promise.all(categories.map((item) => kbJson(`/api/knowledge/notes?category_id=${item.id}`))).then((rows) => rows.flat()),
    listLocalKnowledgeFiles(),
  ]);
  const withCategory = (row) => ({
    ...row,
    category_name: categoryNames.get(Number(row.category_id)) || "",
  });
  return {
    version: 1,
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

async function kbPersistBrowserSnapshot() {
  if (!isBrowserLocalMode()) return;
  try {
    await saveLocalKnowledgeSnapshot(await kbBuildBrowserSnapshot());
  } catch (error) {
    console.warn("[knowledge] browser snapshot failed", error);
    toast(`业务知识库本地保存失败：${error.message}`);
  }
}

async function kbRestoreBrowserKnowledge() {
  if (!isBrowserLocalMode()) return false;
  if (!browserKnowledgeRestorePromise) {
    browserKnowledgeRestorePromise = (async () => {
      const snapshot = await getLocalKnowledgeSnapshot();
      if (!snapshot) {
        // First cloud visit: seed the local durable copy from the live temporary store.
        await kbPersistBrowserSnapshot();
        return false;
      }
      const restored = await kbJson("/api/knowledge/restore", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ snapshot }),
      });
      const documentMeta = new Map((snapshot.documents || []).map((item) => [item.id, item]));
      const files = await listLocalKnowledgeFiles();
      for (const record of files) {
        const meta = documentMeta.get(record.id) || {};
        const file = record.blob instanceof File
          ? record.blob
          : new File([record.blob], record.name, {
            type: record.type || "application/octet-stream",
            lastModified: record.last_modified || Date.now(),
          });
        const formData = new FormData();
        formData.append("file", file, record.name);
        const categoryName = record.category_name || meta.category_name || "";
        formData.append("category_name", categoryName);
        const response = await kbFetch("/api/knowledge/restore-file", { method: "POST", body: formData });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `恢复知识文件 ${record.name} 失败`);
        await updateLocalKnowledgeFile(record.id, {
          server_filename: data.filename,
          category_name: categoryName,
        });
      }
      await kbPersistBrowserSnapshot();
      return Boolean(restored.ok);
    })().catch((error) => {
      browserKnowledgeRestorePromise = null;
      console.warn("[knowledge] browser restore failed", error);
      toast(`业务知识库本地恢复失败：${error.message}`);
      return false;
    });
  }
  return browserKnowledgeRestorePromise;
}


async function kbLoadCategories() {
  const vk = getUiIsland("knowledge");
  try {
    const categories = await kbFetch("/api/knowledge/categories").then(r => r.json());
    _kb.categories = Array.isArray(categories) ? categories : [];
    _kb.categoriesLoaded = true;
    if (!_kb.categories.find(c => Number(c.id) === Number(_kb.activeCategoryId))) {
      _kb.activeCategoryId = Number(_kb.categories[0]?.id || 1);
    }
    vk?.setCategories?.(_kb.categories, _kb.activeCategoryId);
  } catch (e) {
    _kb.categoriesLoaded = false;
    toast(`业务分类加载失败: ${e.message}`);
  }
}

async function kbEnsureCategories() {
  if (!_kb.categoriesLoaded) await kbLoadCategories();
}

async function kbAddCategory(name) {
  const value = String(name || "").trim();
  const vk = getUiIsland("knowledge");
  if (!value) { vk?.setCategoryErr?.("请输入业务名称"); return; }
  try {
    const res = await kbFetch("/api/knowledge/categories", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: value }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "新增分类失败");
    _kb.activeCategoryId = Number(data.id || _kb.activeCategoryId);
    vk?.setCategoryErr?.("");
    await kbLoadCategories();
    loadByTab(_kb.tab);
    await kbPersistBrowserSnapshot();
  } catch (e) {
    vk?.setCategoryErr?.(e.message);
  }
}

async function kbToggleCategory(id) {
  const cid = Number(id);
  const vk = getUiIsland("knowledge");
  const rec = _kb.categories.find(c => Number(c.id) === cid);
  if (!rec) return;
  const oldEnabled = rec.enabled;
  rec.enabled = oldEnabled ? 0 : 1;
  vk?.setCategories?.(_kb.categories, _kb.activeCategoryId);
  try {
    const res = await kbFetch(`/api/knowledge/categories/${cid}/toggle`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "切换分类失败");
    Object.assign(rec, data);
    vk?.setCategories?.(_kb.categories, _kb.activeCategoryId);
    await kbPersistBrowserSnapshot();
  } catch (e) {
    rec.enabled = oldEnabled;
    vk?.setCategories?.(_kb.categories, _kb.activeCategoryId);
    toast(`分类切换失败: ${e.message}`);
  }
}

async function kbDeleteCategory(id) {
  const cid = Number(id);
  const rec = _kb.categories.find(c => Number(c.id) === cid);
  if (!rec) return;
  if (!await window.BAA.ui?.confirm?.({
    title: "删除业务分类",
    message: `确认删除“${rec.name}”？该分类下的指标、规则、背景知识和文档索引将被永久删除。`,
    danger: true,
  })) return;

  try {
    const res = await kbFetch(`/api/knowledge/categories/${cid}`, { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "删除分类失败");
    if (Number(_kb.activeCategoryId) === cid) _kb.activeCategoryId = 1;
    await deleteLocalKnowledgeFilesByCategory(rec.name);
    await kbLoadCategories();
    await loadByTab(_kb.tab);
    await kbPersistBrowserSnapshot();
    toast(`已删除业务分类“${rec.name}”`);
  } catch (e) {
    toast(`分类删除失败: ${e.message}`);
  }
}

function kbSelectCategory(id) {
  _kb.activeCategoryId = Number(id) || 1;
  const vk = getUiIsland("knowledge");
  vk?.setActiveCategory?.(_kb.activeCategoryId);
  loadByTab(_kb.tab);
}

function kbOpenImport() {
  kbSwitchTab("import");
}

// ── Tab switching ─────────────────────────────────────────────────────────────

async function loadByTab(tab) {
  await kbEnsureCategories();
  if      (tab === "metrics") kbLoadMetrics();
  else if (tab === "rules")   kbLoadRules();
  else if (tab === "notes")   kbLoadNotes();
  else if (tab === "import")  { kbResetImport(); kbLoadFiles(); }
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

async function kbLoadMetrics() {
  const vk = getUiIsland("knowledge");
  if (!vk || !vk.isAvailable()) return;
  vk.setListStatus("metrics", { loading: true, err: "" });
  try {
    const data = await kbFetch(kbCategoryUrl("/api/knowledge/metrics")).then(r => r.json());
    vk.setItems("metrics", data);
  } catch (e) {
    vk.setListStatus("metrics", { loading: false, err: e.message });
  }
}

async function kbLoadRules() {
  const vk = getUiIsland("knowledge");
  if (!vk || !vk.isAvailable()) return;
  vk.setListStatus("rules", { loading: true, err: "" });
  try {
    const data = await kbFetch(kbCategoryUrl("/api/knowledge/rules")).then(r => r.json());
    vk.setItems("rules", data);
  } catch (e) {
    vk.setListStatus("rules", { loading: false, err: e.message });
  }
}

async function kbLoadNotes() {
  const vk = getUiIsland("knowledge");
  if (!vk || !vk.isAvailable()) return;
  vk.setListStatus("notes", { loading: true, err: "" });
  try {
    const data = await kbFetch(kbCategoryUrl("/api/knowledge/notes")).then(r => r.json());
    vk.setItems("notes", data);
  } catch (e) {
    vk.setListStatus("notes", { loading: false, err: e.message });
  }
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
  if (!vk || !vk.isAvailable()) return;
  const item = vk.getItem(type, id);
  if (!item) return;
  const oldEnabled = item.enabled;
  vk.updateItem(type, id, { enabled: !oldEnabled });  // 乐观更新
  try {
    const res  = await kbFetch(`/api/knowledge/${type}/${id}/toggle`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "切换失败");
    // 成功：state 已是乐观值，无需 reload
    await kbPersistBrowserSnapshot();
  } catch (e) {
    vk.updateItem(type, id, { enabled: oldEnabled });  // 回滚
    toast(`切换失败: ${e.message}`);
  }
}

// ── Form: open ────────────────────────────────────────────────────────────────

async function kbOpenForm(type, id = null) {
  const vk = getUiIsland("knowledge");
  if (!vk || !vk.isAvailable()) return;
  let rec = null;
  if (id !== null) {
    try {
      const list = await kbFetch(kbCategoryUrl(`/api/knowledge/${type}`)).then(r => r.json());
      rec = list.find(r => r.id === id) || null;
    } catch (_) {}
  }
  vk.openForm({ type, mode: id !== null ? "edit" : "add", editId: id, rec });
  // inline form in sb-panel-knowledge — panel open triggers are in app.js ACTIONS
}

// ── Form: submit ──────────────────────────────────────────────────────────────

async function kbSubmitForm() {
  const vk = getUiIsland("knowledge");
  if (!vk || !vk.isAvailable()) return;
  const fv = vk.getFormValues();
  const { type, mode, editId, body } = fv;
  body.category_id = activeCategoryId();
  // 校验
  if (type === "metrics" && !body.name)     { vk.setFormErr("指标名称不能为空"); return; }
  if (type === "rules"   && !body.rule_id)  { vk.setFormErr("规则 ID 不能为空"); return; }
  if (type === "notes"   && !body.topic)    { vk.setFormErr("主题不能为空"); return; }
  vk.setFormErr("");
  vk.setFormBusy(true);

  const method = mode === "edit" ? "PUT" : "POST";
  const url    = mode === "edit" ? `/api/knowledge/${type}/${editId}` : `/api/knowledge/${type}`;
  try {
    const res  = await kbFetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) { vk.setFormErr(data.error || "保存失败"); vk.setFormBusy(false); return; }

    closeOverlay("ov-kb-form");
    vk.closeForm();
    vk.setFormBusy(false);
    // close inline form in panel too
    if (window.BAA?.sidebar?.closeKbInlineForm) window.BAA.sidebar.closeKbInlineForm();
    toast(mode === "edit" ? "已更新 ✓" : "已添加 ✓");
    loadByTab(type);  // 刷新当前类型列表
    await kbPersistBrowserSnapshot();
  } catch (e) {
    vk.setFormErr(`请求失败: ${e.message}`);
    vk.setFormBusy(false);
  }
}

// ── Delete ────────────────────────────────────────────────────────────────────

async function kbDelete(type, id) {
  if (!await window.BAA.ui?.confirm?.({
    title: "删除知识记录", message: "确认删除这条记录？", danger: true,
  })) return;
  const vk = getUiIsland("knowledge");
  if (!vk || !vk.isAvailable()) return;
  vk.removeItem(type, id);  // 乐观删除
  try {
    const delRes = await kbFetch(`/api/knowledge/${type}/${id}`, { method: "DELETE" });
    if (!delRes.ok) throw new Error("删除请求失败");
    toast("已删除");
    await kbPersistBrowserSnapshot();
  } catch (e) {
    toast(`删除失败: ${e.message}`);
    loadByTab(type);  // 回滚：重新 load
  }
}

// ── Historical source files ───────────────────────────────────────────────────

async function kbLoadFiles() {
  const list = document.getElementById("kb-files-list");
  if (!list) return;
  list.innerHTML = '<div class="kb-empty" style="padding:8px 0;font-size:12px">加载中…</div>';
  try {
    const files = await kbFetch("/api/knowledge/files").then(r => r.json());
    if (!files.length) {
      list.innerHTML = '<div class="kb-empty" style="padding:8px 0;font-size:12px">暂无上传记录</div>';
      return;
    }
    list.innerHTML = files.map(f => {
      const date = new Date(f.mtime * 1000).toLocaleDateString("zh-CN",
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
  } catch (e) {
    list.innerHTML = `<div class="kb-empty" style="color:#ef4444;font-size:12px">加载失败: ${e.message}</div>`;
  }
}

async function kbDeleteFile(filename) {
  if (!await window.BAA.ui?.confirm?.({
    title: "删除知识文件", message: `确认删除文件“${filename}”？`, danger: true,
  })) return;
  try {
    const response = await kbFetch(`/api/knowledge/files/${encodeURIComponent(filename)}`, { method: "DELETE" });
    if (!response.ok) throw new Error("删除知识文件失败");
    await deleteLocalKnowledgeFileByServerFilename(filename);
    toast("文件已删除");
    kbLoadFiles();
  } catch (e) {
    toast(`删除失败: ${e.message}`);
  }
}

// ── Import: file selection & drag-drop ────────────────────────────────────────

function kbResetImport() {
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
  if (file) kbParseFile(file);
}

function kbOnFileSelect(e) {
  const file = e.target.files[0];
  if (file) kbParseFile(file);
}

document.addEventListener("DOMContentLoaded", () => {
  const zone = document.getElementById("kb-drop-zone");
  if (!zone) return;
  zone.addEventListener("dragover",  () => zone.classList.add("drag-over"));
  zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
});

async function kbParseFile(file) {
  const ext = file.name.split(".").pop().toLowerCase();
  if (!["xlsx","xls","docx"].includes(ext)) {
    document.getElementById("kb-import-err").textContent =
      "不支持的格式，请上传 .xlsx / .xls / .docx";
    return;
  }

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
    const res  = await kbFetch("/api/knowledge/parse", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "解析失败");

    _kb.previewRecs = data.preview || [];
    _kb.sourceFile = data.filename || "";
    _kb.pendingSourceFile = file;
    kbRenderPreview(data);
    kbLoadFiles();   // refresh file list after upload
  } catch (e) {
    document.getElementById("kb-parsing").classList.add('hidden');
    document.getElementById("kb-drop-zone").classList.remove('hidden');
    document.getElementById("kb-import-err").textContent  = `解析失败：${e.message}`;
  }
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

function kbCancelImport() { kbResetImport(); }

// ── Import: confirm ───────────────────────────────────────────────────────────

async function kbConfirmImport() {
  const records = _kb.previewRecs.filter(r => r !== null);
  if (!records.length && !_kb.sourceFile) {
    document.getElementById("kb-import-err").textContent = "没有可入库的记录或源文件";
    return;
  }
  const okEl  = document.getElementById("kb-import-ok");
  const errEl = document.getElementById("kb-import-err");
  okEl.textContent  = "";
  errEl.textContent = "";

  try {
    const res  = await kbFetch("/api/knowledge/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ records, filename: _kb.sourceFile, category_id: activeCategoryId() }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "入库失败");

    const { inserted } = data;
    const ragChunks = data.rag?.chunks || 0;
    okEl.textContent =
      `✓ 入库成功：指标 ${inserted.metrics} 条，规则 ${inserted.rules} 条，背景知识 ${inserted.notes} 条，RAG 分块 ${ragChunks} 条`;
    const categoryName = _kb.categories.find((item) => Number(item.id) === activeCategoryId())?.name || "";
    if (_kb.pendingSourceFile && _kb.sourceFile) {
      await saveLocalKnowledgeFile({
        file: _kb.pendingSourceFile,
        serverFilename: _kb.sourceFile,
        categoryName,
      });
    }
    await kbPersistBrowserSnapshot();
    _kb.previewRecs = [];
    setTimeout(() => kbResetImport(), 1800);
  } catch (e) {
    errEl.textContent = `入库失败：${e.message}`;
  }
}

// ── Init: refresh data when modal opens ──────────────────────────────────────

let __kbOverlayListenerInstalled = false;

function syncKnowledgeIsland(vk) {
  if (!vk?.isAvailable()) return false;
  vk.sync({
    onSwitchTab:  (tab)       => kbSwitchTab(tab),
    onToggle:     (type, id)  => kbToggle(type, id),
    onOpenForm:   (type, id)  => { kbOpenForm(type, id); if (window.BAA?.sidebar?.openKbInlineForm) window.BAA.sidebar.openKbInlineForm(); },
    onSubmitForm: ()          => kbSubmitForm(),
    onCancelForm: ()          => { vk.closeForm(); if (window.BAA?.sidebar?.closeKbInlineForm) window.BAA.sidebar.closeKbInlineForm(); },
    onDelete:     (type, id)  => kbDelete(type, id),
    onSelectCategory: (id)     => kbSelectCategory(id),
    onAddCategory: (name)      => kbAddCategory(name),
    onToggleCategory: (id)     => kbToggleCategory(id),
    onDeleteCategory: (id)     => kbDeleteCategory(id),
    onOpenImport: ()           => kbOpenImport(),
  });
  kbLoadCategories();
  return true;
}

export function installKnowledgePanel() {
  // F3 mid-term: subscribe to the overlay:open event instead of monkey-patching
  // window.openOverlay (core/overlay.js emits after the overlay is open, so the
  // island is already ensured by the legacy-core wrapper at that point).
  if (!__kbOverlayListenerInstalled) {
    __kbOverlayListenerInstalled = true;
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
export function kbOnWorkspaceChanged() {
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
  const vk = getUiIsland("knowledge");
  if (vk) vk.closeForm();
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
