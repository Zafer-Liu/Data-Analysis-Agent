/* knowledge_panel.js — Business Knowledge Base UI
 *
 * Depends on:  openOverlay / closeOverlay / toast / t  (dist/core.js, i18n.js)
 * API routes:  /api/knowledge/*  (api/knowledge.py)
 */
import { getUiIsland } from "../core/ui-registry.js";

// ── State ─────────────────────────────────────────────────────────────────────

const _kb = {
  tab:         "metrics",
  previewRecs: [],
  sourceFile:  "",
};

function kbScopeUrl(path) {
  const sid = (typeof SID !== "undefined" ? SID : "")
            || sessionStorage.getItem("baa_session_id") || "";
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}session_id=${encodeURIComponent(sid)}`;
}

function kbFetch(path, options) {
  return fetch(kbScopeUrl(path), options);
}

// ── Tab switching ─────────────────────────────────────────────────────────────

function loadByTab(tab) {
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
    const data = await kbFetch("/api/knowledge/metrics").then(r => r.json());
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
    const data = await kbFetch("/api/knowledge/rules").then(r => r.json());
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
    const data = await kbFetch("/api/knowledge/notes").then(r => r.json());
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
  } catch (e) {
    vk.updateItem(type, id, { enabled: oldEnabled });  // 回滚
    showToast(`切换失败: ${e.message}`);
  }
}

// ── Form: open ────────────────────────────────────────────────────────────────

async function kbOpenForm(type, id = null) {
  const vk = getUiIsland("knowledge");
  if (!vk || !vk.isAvailable()) return;
  let rec = null;
  if (id !== null) {
    try {
      const list = await kbFetch(`/api/knowledge/${type}`).then(r => r.json());
      rec = list.find(r => r.id === id) || null;
    } catch (_) {}
  }
  vk.openForm({ type, mode: id !== null ? "edit" : "add", editId: id, rec });
  openOverlay("ov-kb-form");
}

// ── Form: submit ──────────────────────────────────────────────────────────────

async function kbSubmitForm() {
  const vk = getUiIsland("knowledge");
  if (!vk || !vk.isAvailable()) return;
  const fv = vk.getFormValues();
  const { type, mode, editId, body } = fv;
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
    showToast(mode === "edit" ? "已更新 ✓" : "已添加 ✓");
    loadByTab(type);  // 刷新当前类型列表
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
    showToast("已删除");
  } catch (e) {
    showToast(`删除失败: ${e.message}`);
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
        <button class="kb-act-btn danger" style="padding:2px 7px;font-size:11px"
                onclick="kbDeleteFile('${esc(f.filename)}')">删除</button>
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
    await kbFetch(`/api/knowledge/files/${encodeURIComponent(filename)}`, { method: "DELETE" });
    showToast("文件已删除");
    kbLoadFiles();
  } catch (e) {
    showToast(`删除失败: ${e.message}`);
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
  const sid = (typeof SID !== "undefined" ? SID : "")
            || sessionStorage.getItem("baa_session_id") || "";
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
    { key: "definition",   label: "定义",       required: false },
    { key: "sql_template", label: "SQL 模板",   required: false, multiline: true },
    { key: "notes",        label: "备注",       required: false },
  ],
  business_rules: [
    { key: "rule_id",     label: "规则 ID",  required: true  },
    { key: "description", label: "描述",      required: false },
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
    `解析结果预览（${recs.length} 条）`;
  document.getElementById("kb-preview-sub").textContent =
    `格式：${fmtLabel}  ·  请核对后点击「全部入库」`;

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
           oninput="kbPreviewUpdate(this)">${esc(val)}</textarea>`
      : `<input class="kb-prev-input" type="text" value="${esc(val)}"
           data-idx="${idx}" data-key="${f.key}"
           oninput="kbPreviewUpdate(this)">`;
    return `
      <div class="kb-prev-field">
        <div class="kb-prev-label">${f.label}${f.required ? " *" : ""}</div>
        ${inputEl}
      </div>`;
  }).join("");

  return `
  <div class="kb-prev-card" id="kb-prev-card-${idx}">
    <div class="kb-prev-card-head">
      <span class="kb-prev-card-type">${label}</span>
      <button class="kb-prev-delete" title="移除此条" onclick="kbPreviewRemove(${idx})">×</button>
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
    `解析结果预览（${remaining} 条）`;
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
      body: JSON.stringify({ records, filename: _kb.sourceFile }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "入库失败");

    const { inserted } = data;
    const ragChunks = data.rag?.chunks || 0;
    okEl.textContent =
      `✓ 入库成功：指标 ${inserted.metrics} 条，规则 ${inserted.rules} 条，背景知识 ${inserted.notes} 条，RAG 分块 ${ragChunks} 条`;
    _kb.previewRecs = [];
    setTimeout(() => kbResetImport(), 1800);
  } catch (e) {
    errEl.textContent = `入库失败：${e.message}`;
  }
}

// ── Init: refresh data when modal opens ──────────────────────────────────────

function syncKnowledgeIsland(vk) {
  if (!vk?.isAvailable()) return false;
  vk.sync({
    onSwitchTab:  (tab)       => kbSwitchTab(tab),
    onToggle:     (type, id)  => kbToggle(type, id),
    onOpenForm:   (type, id)  => kbOpenForm(type, id),
    onSubmitForm: ()          => kbSubmitForm(),
    onCancelForm: ()          => closeOverlay("ov-kb-form"),
    onDelete:     (type, id)  => kbDelete(type, id),
  });
  return true;
}

export function installKnowledgePanel() {
  const originalOpenOverlay = window.openOverlay;
  window.openOverlay = async function(id, ...rest) {
    const result = originalOpenOverlay
      ? await originalOpenOverlay(id, ...rest)
      : undefined;
    if (id === "ov-knowledge") {
      const vk = getUiIsland("knowledge");
      if (syncKnowledgeIsland(vk)) {
        vk.onOpen();
      } else {
        loadByTab(_kb.tab);
      }
    }
    return result;
  };

  syncKnowledgeIsland(getUiIsland("knowledge"));
}

export const knowledge = Object.freeze({
  kbSwitchTab,
  kbRefresh,
  kbOpenForm,
  kbSubmitForm,
  kbLoadFiles,
  kbDeleteFile,
  kbOnDrop,
  kbOnFileSelect,
  kbPreviewUpdate,
  kbPreviewRemove,
  kbCancelImport,
  kbConfirmImport,
});
