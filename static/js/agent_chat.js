// ── Slash command registry ─────────────────────────────────────────
// descKey / groupKey reference i18n.js keys; t() is resolved at render time.
const COMMANDS = [
  // Analysis & charts
  { cmd: "chart",     icon: "📊", descKey: "cmd.chart.desc",     groupKey: "group.analysis", available: true  },
  { cmd: "sql",       icon: "🗄️", descKey: "cmd.sql.desc",       groupKey: "group.analysis", available: true  },
  { cmd: "decile",    icon: "📉", descKey: "cmd.decile.desc",     groupKey: "group.analysis", available: true  },
  { cmd: "tree",      icon: "🌳", descKey: "cmd.tree.desc",       groupKey: "group.analysis", available: true  },
  { cmd: "kmeans",    icon: "🔵", descKey: "cmd.kmeans.desc",     groupKey: "group.analysis", available: true  },
  // Data cleaning
  { cmd: "data",      icon: "🔍", descKey: "cmd.data.desc",       groupKey: "group.clean",    available: true  },
  { cmd: "inset",     icon: "🩹", descKey: "cmd.inset.desc",      groupKey: "group.clean",    available: true  },
  { cmd: "winsorize", icon: "✂️", descKey: "cmd.winsorize.desc",  groupKey: "group.clean",    available: true  },
  { cmd: "trimming",  icon: "🔪", descKey: "cmd.trimming.desc",   groupKey: "group.clean",    available: true  },
  // Export
  { cmd: "export",    icon: "📥", descKey: "cmd.export.desc",     groupKey: "group.export",   available: true  },
  { cmd: "report",    icon: "📄", descKey: "cmd.report.desc",     groupKey: "group.export",   available: true  },
  // Tools
  { cmd: "status",    icon: "📡", descKey: "cmd.status.desc",     groupKey: "group.tools",    available: true  },
];

// ── State ─────────────────────────────────────────────────────────
let SID = null;
let srcConnected = false;
let srcName = "";          // actual file/db name; "" when disconnected
let srcHintKey = 'sidebar.hint.noconn';
let schemaText = "";
let isStreaming = false;
let activeCommand = "";
let slashPopupIndex = 0;
let tokenState = { promptTokens: 0, totalInput: 0, totalOutput: 0, contextWindow: null };
let modelConfigs = {};
let _streamReader = null;   // current SSE ReadableStreamDefaultReader

// ── Bootstrap ─────────────────────────────────────────────────────
(async () => {
  buildSlashPopup();
  const r = await fetch("/api/session/new", { method: "POST" });
  SID = (await r.json()).session_id;
  await loadModels();
  await loadBuiltinProviders();
  await loadSavedList();
})();

// ── Language change handler ────────────────────────────────────────
document.addEventListener('langchange', () => {
  // Sync dynamic UI elements that are managed by JS state
  if (!srcConnected) {
    document.getElementById('src-name').textContent = t('sidebar.disconnected');
    document.getElementById('src-hint').textContent = t('sidebar.hint.noconn');
    document.getElementById('hdr-sub').textContent  = t('header.subtitle');
  } else {
    document.getElementById('src-hint').textContent = t(srcHintKey);
    document.getElementById('hdr-sub').textContent  = t('connected_to', { name: srcName });
  }
  // Model placeholder
  const sel = document.getElementById('model-sel');
  if (sel && sel.options.length > 0 && sel.options[0].value === '') {
    sel.options[0].textContent = t('sidebar.model_placeholder');
  }
  // Send button title
  const sendBtn = document.getElementById('send-btn');
  if (sendBtn && !sendBtn.classList.contains('stopping')) {
    sendBtn.title = t('send.title');
  }
  // Input placeholder (data-i18n-ph handles it via applyI18n, but set explicitly too)
  const msgInput = document.getElementById('msg-input');
  if (msgInput) msgInput.placeholder = t('input.placeholder');
  // Saved-list empty text if currently shown
  const savedEmpty = document.querySelector('#saved-list .saved-empty');
  if (savedEmpty) savedEmpty.textContent = t('saved_empty');
  // Rebuild slash popup if open
  if (isSlashOpen()) buildSlashPopup();
});

// ── Slash popup ────────────────────────────────────────────────────
function _highlightMatch(text, term) {
  if (!term) return `/${text}`;
  const idx = text.indexOf(term);
  if (idx < 0) return `/${text}`;
  return `/${text.slice(0, idx)}<mark>${text.slice(idx, idx + term.length)}</mark>${text.slice(idx + term.length)}`;
}

function buildSlashPopup(filter = "") {
  const pop    = document.getElementById("slash-popup");
  const scroll = document.getElementById("slash-popup-scroll");
  scroll.querySelectorAll(".slash-item, .slash-group-label, .slash-empty").forEach(el => el.remove());

  const term    = filter.toLowerCase();
  const matched = COMMANDS.filter(c =>
    !term || c.cmd.includes(term) || t(c.descKey).toLowerCase().includes(term)
  );

  const header = pop.querySelector(".slash-pop-header");
  if (header) {
    header.textContent = term
      ? t('slash.searching', { term })
      : t('slash.header');
  }

  if (matched.length === 0) {
    const empty = document.createElement("div");
    empty.className = "slash-empty";
    empty.textContent = t('slash.empty', { term });
    scroll.appendChild(empty);
    return;
  }

  let lastGroup = null;
  matched.forEach((c, i) => {
    if (c.groupKey && c.groupKey !== lastGroup) {
      const gl = document.createElement("div");
      gl.className = "slash-group-label";
      gl.textContent = t(c.groupKey);
      scroll.appendChild(gl);
      lastGroup = c.groupKey;
    }
    const div = document.createElement("div");
    div.className = "slash-item" + (c.available ? "" : " disabled") + (i === 0 ? " active" : "");
    div.dataset.cmd = c.cmd;
    div.innerHTML = `
      <span class="slash-icon">${c.icon}</span>
      <div class="slash-info">
        <div class="slash-name">${_highlightMatch(c.cmd, term)}
          ${!c.available ? `<span class="slash-soon">${t('slash.soon')}</span>` : ""}
        </div>
        <div class="slash-desc">${t(c.descKey)}</div>
      </div>`;
    if (c.available) div.onclick = () => selectCommand(c.cmd);
    scroll.appendChild(div);
  });
}

function openSlashPopup(filter = "") {
  buildSlashPopup(filter);
  slashPopupIndex = 0;
  updateSlashActive();
  document.getElementById("slash-popup").classList.add("open");
}
function closeSlashPopup() {
  document.getElementById("slash-popup").classList.remove("open");
}
function isSlashOpen() {
  return document.getElementById("slash-popup").classList.contains("open");
}
function updateSlashActive() {
  const scroll = document.getElementById("slash-popup-scroll");
  if (!scroll) return;
  const items = [...scroll.querySelectorAll(".slash-item:not(.disabled)")];
  scroll.querySelectorAll(".slash-item").forEach(el => el.classList.remove("active"));
  if (items[slashPopupIndex]) {
    items[slashPopupIndex].classList.add("active");
    items[slashPopupIndex].scrollIntoView({ block: "nearest" });
  }
}
function selectCommand(cmd) {
  activeCommand = cmd;
  const c = COMMANDS.find(x => x.cmd === cmd);
  const badge = document.getElementById("cmd-badge");
  document.getElementById("cmd-badge-text").textContent = `${c.icon} /${cmd}`;
  badge.classList.add("show");
  const input = document.getElementById("msg-input");
  input.value = input.value.replace(/^\/\S*\s*/, "");
  closeSlashPopup();
  input.focus();
}
function clearCmd() {
  activeCommand = "";
  document.getElementById("cmd-badge").classList.remove("show");
}

// ── Input handling ─────────────────────────────────────────────────
function onInput(e) {
  autoResize(e.target);
  const v = e.target.value;

  if (v === "/stop" && isStreaming) {
    e.target.value = "";
    autoResize(e.target);
    stopStreaming();
    return;
  }

  const mFull = v.match(/^\/(\w+)\s$/);
  if (mFull) {
    const found = COMMANDS.find(c => c.cmd === mFull[1] && c.available);
    if (found) {
      selectCommand(found.cmd);
      e.target.value = "";
      autoResize(e.target);
      return;
    }
  }

  const mSlash = v.match(/^\/([\w]*)$/);
  if (mSlash) {
    const term = mSlash[1];
    if (isSlashOpen()) {
      buildSlashPopup(term);
      slashPopupIndex = 0;
      updateSlashActive();
    } else {
      openSlashPopup(term);
    }
    return;
  }

  if (isSlashOpen()) closeSlashPopup();
}

function onKeyDown(e) {
  if (isSlashOpen()) {
    const sc = document.getElementById("slash-popup-scroll");
    const available = sc ? [...sc.querySelectorAll(".slash-item:not(.disabled)")] : [];
    if (e.key === "ArrowDown") {
      e.preventDefault();
      slashPopupIndex = Math.min(slashPopupIndex + 1, available.length - 1);
      updateSlashActive(); return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      slashPopupIndex = Math.max(slashPopupIndex - 1, 0);
      updateSlashActive(); return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const item = available[slashPopupIndex];
      if (item) selectCommand(item.dataset.cmd);
      return;
    }
    if (e.key === "Escape") { closeSlashPopup(); return; }
  }
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

document.addEventListener("click", (e) => {
  if (!e.target.closest(".input-area")) closeSlashPopup();
});

function autoResize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 140) + "px";
}

// ── Model management ────────────────────────────────────────────────
async function loadModels() {
  const r = await fetch("/api/models");
  const models = await r.json();
  modelConfigs = models;
  const sel = document.getElementById("model-sel");
  sel.innerHTML = `<option value="">${t('sidebar.model_placeholder')}</option>`;
  for (const [key, cfg] of Object.entries(models)) {
    if (!cfg.has_api_key) continue;
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = cfg.model || key;
    sel.appendChild(opt);
  }
  if (sel.options.length > 1) { sel.selectedIndex = 1; onModelChange(); }
}

async function onModelChange() {
  const v = document.getElementById("model-sel").value;
  if (!v || !SID) return;
  await fetch(`/api/session/${SID}/model`, {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ provider: v })
  });
}

// ── Settings modal ─────────────────────────────────────────────────
const COMMON_ICON = "/static/Images/icon.png";

const BUILTIN_META = {
  deepseek: { label: "DeepSeek",          icon: COMMON_ICON },
  openai:   { label: "OpenAI / ChatGPT",  icon: COMMON_ICON },
  claude:   { label: "Anthropic Claude",  icon: COMMON_ICON },
};

async function loadBuiltinProviders() {
  const [cfgR, defR] = await Promise.all([
    fetch("/api/models"), fetch("/api/models/defaults")
  ]);
  const configs  = await cfgR.json();
  const defaults = await defR.json();
  renderBuiltinProviders(configs, defaults);
  renderCustomList(configs);
}

function renderBuiltinProviders(configs, defaults) {
  const container = document.getElementById("builtin-providers");
  container.innerHTML = "";
  for (const [key, def] of Object.entries(defaults)) {
    const meta = BUILTIN_META[key] || { label: key, icon: "/Images/icon.png" };
    const cfg  = configs[key] || {};
    const hasKey = cfg.has_api_key;
    container.innerHTML += `
      <div class="provider-card">
        <div class="provider-head">
          <img class="provider-icon" src="${meta.icon}" alt="${meta.label}">
          <span class="provider-name">${meta.label}</span>
          <span class="provider-status ${hasKey ? "set" : "unset"}" id="ps-${key}">
            ${hasKey ? t('settings.configured') : t('settings.not_configured')}
          </span>
        </div>
        <div class="provider-fields">
          <div class="pf-row">
            <label>${t('settings.api_key')}</label>
            <input type="password" id="pk-${key}" placeholder="${t('settings.api_key_ph')}">
          </div>
          <div class="pf-row">
            <label>${t('settings.base_url')}</label>
            <input type="text" id="pu-${key}" value="${cfg.base_url || def.base_url}" placeholder="${def.base_url}">
          </div>
          <div class="pf-row">
            <label>${t('settings.model')}</label>
            <input type="text" id="pm-${key}" value="${cfg.model || def.model}" placeholder="${def.model}">
          </div>
          <div class="pf-row">
            <label>${t('settings.ctx_window')}</label>
            <input type="number" id="pctx-${key}" value="${cfg.context_window ?? def.context_window ?? ''}" placeholder="${t('settings.ctx_ph')}">
          </div>
          <div class="pf-row">
            <label>${t('settings.max_output')}</label>
            <input type="number" id="pout-${key}" value="${cfg.max_output_tokens ?? def.max_output_tokens ?? ''}" placeholder="${t('settings.out_ph')}">
          </div>
          <div class="pf-row" style="align-items:center">
            <label>${t('settings.thinking')}</label>
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;color:#475569">
              <input type="checkbox" id="pthink-${key}" ${cfg.enable_thinking ? "checked" : ""}>
              ${t('settings.thinking_label')}
            </label>
          </div>
        </div>
        <div class="provider-actions">
          <button class="btn-sm btn-sm-danger" onclick="clearBuiltin('${key}')">${t('settings.clear')}</button>
          <button class="btn-sm btn-sm-primary" onclick="saveBuiltin('${key}')">${t('settings.save')}</button>
        </div>
        <div class="provider-msg" id="pmsg-${key}"></div>
      </div>`;
  }
}

function renderCustomList(configs) {
  const list = document.getElementById("custom-list");
  const customs = Object.entries(configs).filter(([, v]) => v.is_custom);
  if (!customs.length) {
    list.innerHTML = `<div class="custom-empty">${t('custom_empty')}</div>`;
    return;
  }
  list.innerHTML = customs.map(([key, cfg]) => `
    <div class="custom-item">
      <span class="ci-name">${cfg.model || key}</span>
      <span class="ci-model">${cfg.base_url || ""}</span>
      <button class="btn-sm btn-sm-danger" onclick="deleteCustom('${key}')">${t('settings.del_custom')}</button>
    </div>`).join("");
}

async function saveBuiltin(key) {
  const apiKey  = document.getElementById(`pk-${key}`).value.trim();
  const baseUrl = document.getElementById(`pu-${key}`).value.trim();
  const model   = document.getElementById(`pm-${key}`).value.trim();
  const ctxRaw  = document.getElementById(`pctx-${key}`).value.trim();
  const outRaw  = document.getElementById(`pout-${key}`).value.trim();
  const msgEl   = document.getElementById(`pmsg-${key}`);
  if (!apiKey) { msgEl.className="provider-msg err"; msgEl.textContent=t('settings.api_key_empty'); return; }
  msgEl.textContent = t('settings.saving');
  const body = {
    provider: key, api_key: apiKey, base_url: baseUrl, model,
    enable_thinking: document.getElementById(`pthink-${key}`).checked,
  };
  if (ctxRaw) body.context_window    = parseInt(ctxRaw);
  if (outRaw) body.max_output_tokens = parseInt(outRaw);
  const r = await fetch("/api/models/set-builtin", {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify(body)
  });
  const d = await r.json();
  if (d.ok) {
    msgEl.className = "provider-msg ok"; msgEl.textContent = t('settings.save_ok');
    document.getElementById(`ps-${key}`).className = "provider-status set";
    document.getElementById(`ps-${key}`).textContent = t('settings.configured');
    document.getElementById(`pk-${key}`).value = "";
    await loadModels();
  } else {
    msgEl.className = "provider-msg err"; msgEl.textContent = d.error || t('update.fail');
  }
}

async function clearBuiltin(key) {
  if (!confirm(t('confirm.clear_builtin', { label: BUILTIN_META[key]?.label || key }))) return;
  const r = await fetch("/api/models/clear-builtin", {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ provider: key })
  });
  const d = await r.json();
  if (d.ok) {
    document.getElementById(`ps-${key}`).className = "provider-status unset";
    document.getElementById(`ps-${key}`).textContent = t('settings.not_configured');
    const msgEl = document.getElementById(`pmsg-${key}`);
    msgEl.className = "provider-msg ok"; msgEl.textContent = t('settings.cleared');
    await loadModels();
  }
}

function toggleAddCustom() {
  const f = document.getElementById("add-custom-form");
  f.classList.toggle("show");
  if (f.classList.contains("show")) document.getElementById("ac-name").focus();
}

async function addCustomModel() {
  const ctxRaw = document.getElementById("ac-ctx").value.trim();
  const outRaw = document.getElementById("ac-output").value.trim();
  const data = {
    name:            document.getElementById("ac-name").value.trim(),
    base_url:        document.getElementById("ac-url").value.trim(),
    model_name:      document.getElementById("ac-model").value.trim(),
    api_key:         document.getElementById("ac-key").value.trim(),
    enable_thinking: document.getElementById("ac-think").checked,
    ...(ctxRaw ? { context_window:    parseInt(ctxRaw) } : {}),
    ...(outRaw ? { max_output_tokens: parseInt(outRaw) } : {}),
  };
  document.getElementById("ac-err").textContent = "";
  document.getElementById("ac-ok").textContent = "";
  const r = await fetch("/api/models/add", {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify(data)
  });
  const d = await r.json();
  if (d.error) {
    document.getElementById("ac-err").textContent = d.error;
  } else {
    document.getElementById("ac-ok").textContent = d.message;
    ["ac-name","ac-url","ac-model","ac-key","ac-ctx","ac-output"].forEach(
      id => document.getElementById(id).value = ""
    );
    document.getElementById("ac-think").checked = false;
    await Promise.all([loadModels(), loadBuiltinProviders()]);
    setTimeout(toggleAddCustom, 1200);
  }
}

async function deleteCustom(provider) {
  if (!confirm(t('confirm.delete_custom'))) return;
  await fetch("/api/models/delete", {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ provider })
  });
  await Promise.all([loadModels(), loadBuiltinProviders()]);
}

// ── Data source ────────────────────────────────────────────────────
function setSrc(name, hintKey, connected) {
  srcConnected = connected;
  srcName      = connected ? (name || "") : "";
  srcHintKey   = connected ? hintKey : 'sidebar.hint.noconn';

  document.getElementById("src-dot").className = "source-dot" + (connected ? " on" : "");
  document.getElementById("src-name").textContent = connected ? name : t('sidebar.disconnected');
  document.getElementById("src-hint").textContent = t(hintKey);
  document.getElementById("btn-disc").style.display = connected ? "block" : "none";
  document.getElementById("btn-schema").style.display = connected ? "" : "none";
  document.getElementById("hdr-sub").textContent = connected
    ? t('connected_to', { name })
    : t('header.subtitle');
  if (connected) hideWelcome();
}

async function disconnectSrc() {
  await fetch(`/api/session/${SID}/datasource`, { method: "DELETE" });
  schemaText = "";
  setSrc(null, 'sidebar.hint.noconn', false);
  toast(t('toast.disconnected'));
}

function onXlFile() {
  const f = document.getElementById("xl-file").files[0];
  document.getElementById("xl-btn").disabled = !f;
  document.getElementById("xl-err").textContent = "";
  document.getElementById("xl-schema").style.display = "none";
}
async function uploadXl() {
  const f = document.getElementById("xl-file").files[0];
  if (!f) return;
  const btn = document.getElementById("xl-btn");
  btn.disabled = true; btn.textContent = t('btn.uploading');
  const form = new FormData(); form.append("file", f);
  const r = await fetch(`/api/session/${SID}/upload`, { method: "POST", body: form });
  const d = await r.json();
  btn.disabled = false; btn.textContent = t('modal.upload');
  if (d.error) { document.getElementById("xl-err").textContent = d.error; return; }
  schemaText = d.schema_preview || "";
  document.getElementById("xl-schema").textContent = schemaText;
  document.getElementById("xl-schema").style.display = "block";
  setSrc(d.source_name, 'src.hint.file', true);
  closeOverlay("ov-excel");
  toast(t('toast.upload_ok'), "ok");
  sysMsg(t('sys.connected', { name: d.source_name }));
}

async function connectDB() {
  const conn = document.getElementById("db-conn").value.trim();
  const name = document.getElementById("db-name").value.trim();
  if (!conn) { document.getElementById("db-err").textContent = t('conn_err'); return; }
  document.getElementById("db-err").textContent = "";
  const r = await fetch(`/api/session/${SID}/connect-db`, {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ connection_string: conn, name })
  });
  const d = await r.json();
  if (d.error) { document.getElementById("db-err").textContent = d.error; return; }
  schemaText = d.schema_preview || "";
  document.getElementById("db-schema").textContent = schemaText;
  document.getElementById("db-schema").style.display = "block";
  setSrc(d.source_name, 'src.hint.db', true);
  closeOverlay("ov-db");
  toast(t('toast.db_ok'), "ok");
  sysMsg(t('sys.connected', { name: d.source_name }));
}

let _previewData = null;

function openSchemaView() {
  openOverlay("ov-schema");
  _loadPreview();
}

async function _loadPreview() {
  const wrap  = document.getElementById("preview-table-wrap");
  const tabs  = document.getElementById("preview-tabs");
  const foot  = document.getElementById("preview-footer");
  const title = document.getElementById("preview-title");
  wrap.innerHTML   = `<div class="preview-loading">${t('preview.loading')}</div>`;
  tabs.innerHTML   = "";
  foot.textContent = "";

  const r = await fetch(`/api/session/${SID}/preview`);
  if (!r.ok) {
    wrap.innerHTML = `<div class="preview-loading" style="color:#ef4444">${t('preview.fail')}</div>`;
    return;
  }
  _previewData = await r.json();
  title.textContent = `${t('modal.preview.title')} · ${_previewData.source_name}`;

  const tables = _previewData.tables || [];
  if (!tables.length) {
    wrap.innerHTML = `<div class="preview-loading">${t('preview.empty')}</div>`;
    return;
  }

  tables.forEach((tb, i) => {
    const tab = document.createElement("div");
    tab.className = "preview-tab" + (i === 0 ? " active" : "");
    tab.textContent = tb.name;
    tab.onclick = () => _switchPreviewTab(i);
    tabs.appendChild(tab);
  });

  _renderPreviewTable(tables[0]);
}

function _switchPreviewTab(idx) {
  document.querySelectorAll(".preview-tab").forEach((tb, i) =>
    tb.classList.toggle("active", i === idx));
  _renderPreviewTable(_previewData.tables[idx]);
}

function _renderPreviewTable(table) {
  const wrap = document.getElementById("preview-table-wrap");
  const foot = document.getElementById("preview-footer");
  const shown = table.rows.length;
  const total = table.total_rows ?? shown;

  let html = '<table class="preview-table"><thead><tr>';
  html += '<th class="preview-rn">#</th>';
  html += table.columns.map(c => `<th title="${esc(c)}">${esc(c)}</th>`).join("");
  html += "</tr></thead><tbody>";
  table.rows.forEach((row, i) => {
    html += `<tr><td class="preview-rn">${i + 1}</td>`;
    html += row.map(cell => {
      const s = esc(String(cell));
      return `<td title="${s}">${s}</td>`;
    }).join("");
    html += "</tr>";
  });
  html += "</tbody></table>";
  wrap.innerHTML = html;

  foot.textContent = total > shown
    ? t('preview.rows_partial', { cols: table.columns.length, total: total.toLocaleString(), shown })
    : t('preview.rows_all', { cols: table.columns.length, total: total.toLocaleString() });
}

// ── Chat ───────────────────────────────────────────────────────────
function newChat() {
  document.querySelectorAll(".msg, .sys-msg").forEach(el => el.remove());
  tokenState = { promptTokens: 0, totalInput: 0, totalOutput: 0, contextWindow: null };
  updateTokenBar();
  showWelcome();
}
function fillHint(el) {
  const txt = el.textContent;
  const m = txt.match(/^\/(\w+)\s?(.*)/);
  if (m) {
    const found = COMMANDS.find(c => c.cmd === m[1] && c.available);
    if (found) { selectCommand(found.cmd); document.getElementById("msg-input").value = m[2]; return; }
  }
  document.getElementById("msg-input").value = txt;
  sendMessage();
}

// ── Send / Stop toggle ─────────────────────────────────────────────
function onSendOrStop() {
  if (isStreaming) stopStreaming();
  else sendMessage();
}

async function stopStreaming() {
  if (!isStreaming || !SID) return;
  try {
    await fetch(`/api/session/${SID}/stop`, { method: "POST" });
  } catch (_) {}
  if (_streamReader) {
    try { _streamReader.cancel(); } catch (_) {}
  }
}

function _setSendBtnStopping(stopping) {
  const btn = document.getElementById("send-btn");
  if (stopping) {
    btn.textContent = "⬛";
    btn.classList.add("stopping");
    btn.title = "";
    btn.disabled = false;
  } else {
    btn.textContent = "↑";
    btn.classList.remove("stopping");
    btn.title = t('send.title');
    btn.disabled = false;
  }
}

async function sendMessage() {
  if (isStreaming) return;
  const input = document.getElementById("msg-input");
  const text = input.value.trim();
  if (!text && activeCommand !== "status") return;

  if (activeCommand === "status") {
    input.value = ""; input.style.height = "auto";
    hideWelcome(); clearCmd();
    appendMsg("user", "/status");
    showStatus();
    return;
  }

  input.value = ""; input.style.height = "auto";
  hideWelcome();

  const displayText = activeCommand ? `/${activeCommand} ${text}` : text;
  appendMsg("user", displayText);
  const aEl = appendMsg("assistant", null);
  const stepsEl = aEl.querySelector(".tool-steps");
  const bubbleEl = aEl.querySelector(".msg-bubble");

  const typing = document.createElement("div");
  typing.className = "typing-dots";
  typing.innerHTML = "<span></span><span></span><span></span>";
  bubbleEl.appendChild(typing);

  isStreaming = true;
  _setSendBtnStopping(true);

  const payload = { message: text };
  if (activeCommand) payload.command = activeCommand;
  clearCmd();

  const resp = await fetch(`/api/session/${SID}/chat`, {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload)
  });

  const reader = resp.body.getReader();
  _streamReader = reader;
  const dec = new TextDecoder();
  let buf = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split("\n"); buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        try { handleEvent(JSON.parse(line.slice(6)), stepsEl, bubbleEl, typing); }
        catch (_) {}
      }
    }
  } catch (_) {
    // reader.cancel() throws — expected when stopStreaming() is called
  } finally {
    _streamReader = null;
    isStreaming = false;
    _setSendBtnStopping(false);
    scrollBottom();
  }
}

function handleEvent(ev, stepsEl, bubbleEl, typing) {
  if (ev.type === "tool_start") {
    const s = document.createElement("div");
    s.className = "tool-step";
    s.innerHTML = `<span class="spin">⟳</span> ${esc(ev.display)}`;
    stepsEl.appendChild(s);
    scrollBottom();
  }
  else if (ev.type === "chart_ref") {
    stepsEl.querySelectorAll(".tool-step:not(.done)").forEach(s => {
      s.className = "tool-step done";
      const spinEl = s.querySelector(".spin");
      spinEl.classList.remove("spin"); spinEl.textContent = "✓";
    });
    const wrap = document.createElement("div");
    wrap.className = "chart-frame";
    wrap.innerHTML = `
      <button class="chart-expand-btn" onclick="window.open('/api/chart/${ev.chart_id}','_blank')" title="⛶">⛶</button>
      <iframe src="/api/chart/${ev.chart_id}" loading="lazy"></iframe>`;
    bubbleEl.before(wrap);
    scrollBottom();
  }
  else if (ev.type === "reasoning") {
    typing.remove();
    const block = document.createElement("div");
    block.className = "reasoning-block";
    const toggle = document.createElement("div");
    toggle.className = "reasoning-toggle";
    toggle.innerHTML = `<span class="reasoning-arrow">▶</span> ${t('reasoning_toggle')}`;
    const body = document.createElement("div");
    body.className = "reasoning-body";
    body.textContent = ev.content || "";
    toggle.onclick = () => {
      toggle.classList.toggle("open");
      body.classList.toggle("open");
    };
    block.appendChild(toggle);
    block.appendChild(body);
    bubbleEl.before(block);
    scrollBottom();
  }
  else if (ev.type === "text") {
    typing.remove();
    stepsEl.querySelectorAll(".tool-step:not(.done)").forEach(s => {
      s.className = "tool-step done";
      const spinEl = s.querySelector(".spin");
      spinEl.classList.remove("spin"); spinEl.textContent = "✓";
    });
    bubbleEl.innerHTML = renderMd(ev.content || "");
    scrollBottom();
  }
  else if (ev.type === "usage") {
    tokenState.promptTokens  = ev.prompt_tokens || 0;
    tokenState.totalInput    = ev.session_total_input  || 0;
    tokenState.totalOutput   = ev.session_total_output || 0;
    tokenState.contextWindow = ev.context_window || tokenState.contextWindow;
    updateTokenBar();
  }
  else if (ev.type === "error") {
    typing.remove();
    bubbleEl.innerHTML = `<span style="color:#ef4444">⚠ ${esc(ev.message)}</span>`;
  }
  else if (ev.type === "stopped") {
    typing.remove();
    stepsEl.querySelectorAll(".tool-step:not(.done)").forEach(s => {
      s.className = "tool-step done";
      const spinEl = s.querySelector(".spin");
      if (spinEl) { spinEl.classList.remove("spin"); spinEl.textContent = "✓"; }
    });
    const stopNote = document.createElement("div");
    stopNote.className = "stop-note";
    stopNote.textContent = t('stop_note');
    bubbleEl.before(stopNote);
    if (!bubbleEl.textContent.trim()) bubbleEl.remove();
  }
}

// ── Token bar ──────────────────────────────────────────────────────
function fmtK(n) {
  return n >= 1000 ? (n / 1000).toFixed(1) + "K" : String(n);
}
function updateTokenBar() {
  const wrap  = document.getElementById("token-bar-wrap");
  const fill  = document.getElementById("token-bar-fill");
  const label = document.getElementById("token-bar-label");
  const { promptTokens, totalInput, totalOutput, contextWindow } = tokenState;

  if (!promptTokens && !totalInput) { wrap.classList.remove("visible"); return; }
  wrap.classList.add("visible");

  if (contextWindow) {
    const pct = Math.min(promptTokens / contextWindow * 100, 100);
    fill.style.width = pct + "%";
    fill.className = "token-bar-fill" + (pct >= 85 ? " crit" : pct >= 60 ? " warn" : "");
    label.textContent = t('ctx.bar', {
      used: fmtK(promptTokens),
      total: fmtK(contextWindow),
      pct: pct.toFixed(1),
    });
  } else {
    fill.style.width = "0%";
    fill.className = "token-bar-fill";
    label.textContent = t('token.bar', { input: fmtK(totalInput), output: fmtK(totalOutput) });
  }
}

// ── /status ────────────────────────────────────────────────────────
function showStatus() {
  const provKey = document.getElementById("model-sel").value;
  const cfg = modelConfigs[provKey] || {};
  const modelName = cfg.model || provKey || t('status.no_model');
  const ctx  = tokenState.contextWindow;
  const pct  = (ctx && tokenState.promptTokens)
    ? ` (${(tokenState.promptTokens / ctx * 100).toFixed(1)}%)`
    : "";

  const lines = [
    t('status.line.model', { v: modelName }),
    t('status.line.src',   { v: srcConnected ? srcName : t('sidebar.disconnected') }),
    ``,
    t('status.line.usage'),
    t('status.line.input',  { v: tokenState.totalInput.toLocaleString() }),
    t('status.line.output', { v: tokenState.totalOutput.toLocaleString() }),
    ctx
      ? t('status.line.ctx',      { used: tokenState.promptTokens.toLocaleString(), total: ctx.toLocaleString(), pct })
      : t('status.line.ctx_none', { used: tokenState.promptTokens.toLocaleString() }),
  ];

  const aEl = appendMsg("assistant", null);
  aEl.querySelector(".msg-bubble").innerHTML = renderMd(lines.join("\n"));
  scrollBottom();
}

// ── DOM helpers ────────────────────────────────────────────────────
function appendMsg(role, text) {
  const msgs = document.getElementById("messages");
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  const assistantAvatar = `<img class="assistant-avatar-img" src="/static/Images/icon.png" alt="AI">`;
  div.innerHTML = `
    <div class="msg-avatar">
      ${role === "user" ? "👤" : assistantAvatar}
    </div>
    <div class="msg-body">
      <div class="tool-steps"></div>
      <div class="msg-bubble">${text !== null ? renderMd(text) : ""}</div>
    </div>`;
  msgs.appendChild(div);
  scrollBottom();
  return div;
}
function sysMsg(text) {
  const msgs = document.getElementById("messages");
  const d = document.createElement("div");
  d.className = "sys-msg";
  d.style.cssText = "text-align:center;font-size:12px;color:#94a3b8;padding:3px 0;";
  d.textContent = text;
  msgs.appendChild(d);
}
function hideWelcome() { const w = document.getElementById("welcome"); if (w) w.style.display = "none"; }
function showWelcome() { const w = document.getElementById("welcome"); if (w) w.style.display = ""; }
function scrollBottom() { const m = document.getElementById("messages"); m.scrollTop = m.scrollHeight; }

// ── Modal helpers ──────────────────────────────────────────────────
function openOverlay(id) {
  document.getElementById(id).classList.add("open");
  if (id === "ov-settings") loadBuiltinProviders();
}
function closeOverlay(id) { document.getElementById(id).classList.remove("open"); }
function closeOutside(e, id) { if (e.target.id === id) closeOverlay(id); }

// ── Toast ──────────────────────────────────────────────────────────
function toast(msg, type = "") {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = "toast show" + (type ? " " + type : "");
  setTimeout(() => el.className = "toast", 2800);
}

// ── Saved sessions ────────────────────────────────────────────────
function openSaveDialog() {
  document.getElementById("save-name").value = "";
  document.getElementById("save-err").textContent = "";
  openOverlay("ov-save");
  setTimeout(() => document.getElementById("save-name").focus(), 80);
}

async function saveSession() {
  const name = document.getElementById("save-name").value.trim();
  const errEl = document.getElementById("save-err");
  errEl.textContent = "";
  const r = await fetch(`/api/session/${SID}/save`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const d = await r.json();
  if (d.error) { errEl.textContent = d.error; return; }
  closeOverlay("ov-save");
  toast(t('toast.saved', { name: d.name }), "ok");
  await loadSavedList();
}

async function loadSavedList() {
  const box = document.getElementById("saved-list");
  const r = await fetch("/api/saved-sessions");
  const list = await r.json();
  if (!list.length) {
    box.innerHTML = `<div class="saved-empty">${t('saved_empty')}</div>`;
    return;
  }
  box.innerHTML = list.map(s => {
    const date = s.saved_at ? s.saved_at.slice(0, 16).replace("T", " ") : "";
    const ds   = s.ds_name  ? `<span class="saved-ds">${esc(s.ds_name)}</span>` : "";
    return `
      <div class="saved-item">
        <div class="saved-info" onclick="loadSavedSession('${esc(s.filename)}','${esc(s.name)}')">
          <div class="saved-name">${esc(s.name)}</div>
          <div class="saved-meta">${date} · ${s.msg_count} 条${s.ds_name ? " · " + esc(s.ds_name) : ""}</div>
        </div>
        <button class="saved-del" title="✕" onclick="deleteSavedSession('${esc(s.filename)}','${esc(s.name)}')">✕</button>
      </div>`;
  }).join("");
}

async function loadSavedSession(filename, name) {
  if (!confirm(t('confirm.load', { name }))) return;
  const r = await fetch(`/api/session/${SID}/load`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename }),
  });
  const d = await r.json();
  if (d.error) { toast(d.error, "err"); return; }

  document.querySelectorAll(".msg, .sys-msg").forEach(el => el.remove());
  hideWelcome();

  if (d.ds_connected) {
    setSrc(d.ds_name, 'src.restored', true);
  } else if (d.ds_lost) {
    setSrc(d.ds_name + t('src.lost_suffix'), 'src.lost_hint', false);
    toast(t('src.lost_hint'), "err");
  } else {
    setSrc(null, 'sidebar.hint.noconn', false);
  }

  if (d.model_provider) {
    const sel = document.getElementById("model-sel");
    if ([...sel.options].some(o => o.value === d.model_provider)) {
      sel.value = d.model_provider;
      onModelChange();
    }
  }

  tokenState = {
    promptTokens: 0,
    totalInput:   d.total_input  || 0,
    totalOutput:  d.total_output || 0,
    contextWindow: tokenState.contextWindow,
  };
  updateTokenBar();

  for (const msg of d.history) {
    if (msg.role === "user") {
      appendMsg("user", msg.content);
    } else if (msg.role === "assistant" && msg.content) {
      const el = appendMsg("assistant", null);
      el.querySelector(".msg-bubble").innerHTML = renderMd(msg.content);
    }
  }

  sysMsg(t('sys.loaded', { name: d.name }));
  toast(t('toast.loaded', { name: d.name }), "ok");
}

async function deleteSavedSession(filename, name) {
  if (!confirm(t('confirm.delete_session', { name }))) return;
  const r = await fetch(`/api/saved-sessions/${encodeURIComponent(filename)}`, { method: "DELETE" });
  const d = await r.json();
  if (d.error) { toast(d.error, "err"); return; }
  toast(t('toast.deleted', { name }));
  await loadSavedList();
}

// ── System update ─────────────────────────────────────────────────
async function runUpdate() {
  const btn     = document.getElementById("update-btn");
  const stateEl = document.getElementById("update-state");
  const outEl   = document.getElementById("update-output");
  const hintEl  = document.getElementById("update-restart-hint");

  btn.disabled = true;
  outEl.style.display = "none";
  outEl.textContent   = "";
  hintEl.style.display = "none";
  stateEl.className   = "update-state update-loading";
  stateEl.innerHTML   = `<span class="update-spinner"></span><span class="update-state-text">${t('update.loading')}</span>`;

  try {
    const r = await fetch("/api/system/update", { method: "POST" });
    const d = await r.json();

    outEl.textContent   = d.output || t('update.no_output');
    outEl.style.display = "block";

    if (d.ok && d.already_up_to_date) {
      stateEl.className = "update-state update-ok";
      stateEl.innerHTML = `<span class="update-state-icon">✅</span><span class="update-state-text">${t('update.ok_latest')}</span>`;
    } else if (d.ok) {
      stateEl.className = "update-state update-ok";
      stateEl.innerHTML = `<span class="update-state-icon">✅</span><span class="update-state-text">${t('update.ok')}</span>`;
      hintEl.style.display = "block";
    } else {
      stateEl.className = "update-state update-err";
      stateEl.innerHTML = `<span class="update-state-icon">❌</span><span class="update-state-text">${t('update.fail')}</span>`;
    }
  } catch (e) {
    stateEl.className = "update-state update-err";
    stateEl.innerHTML = `<span class="update-state-icon">❌</span><span class="update-state-text">${t('update.req_fail')}${esc(String(e))}</span>`;
  } finally {
    btn.disabled = false;
  }
}

// ── Markdown (lightweight) ─────────────────────────────────────────
function esc(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
function renderMd(text) {
  if (!text) return "";
  let s = esc(text);
  s = s.replace(/\[([^\]]+)\]\((\/[^)]+)\)/g, '<a href="$2">$1</a>');
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  s = s.replace(/```(\w*)\n?([\s\S]*?)```/g, (_,_l,c) => `<pre><code>${c}</code></pre>`);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/^### (.+)$/gm, "<strong>$1</strong>");
  s = s.replace(/^## (.+)$/gm, "<strong style='font-size:15px'>$1</strong>");
  s = s.replace(/((?:\|.+\|\n?)+)/g, match => {
    const rows = match.trim().split("\n").filter(r => r.includes("|"));
    if (rows.length < 2) return match;
    const row = (r, tag) => "<tr>" +
      r.split("|").filter((_,i,a) => i > 0 && i < a.length - 1)
       .map(c => `<${tag}>${c.trim()}</${tag}>`).join("") + "</tr>";
    const [hd, , ...body] = rows;
    return `<table><thead>${row(hd,"th")}</thead><tbody>${body.map(r=>row(r,"td")).join("")}</tbody></table>`;
  });
  s = s.replace(/\n/g, "<br>");
  return s;
}
