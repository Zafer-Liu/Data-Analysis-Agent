// ── Slash command registry ─────────────────────────────────────────
const COMMANDS = [
  { cmd: "chart",   icon: "📊", label: "生成图表",     desc: "用自然语言描述想要的图表",        available: true  },
  { cmd: "sql",     icon: "🗄️", label: "执行 SQL",    desc: "直接运行 SQL 查询并展示结果",      available: true  },
  { cmd: "status",  icon: "📡", label: "当前状态",     desc: "查看模型、数据源与 Token 用量",    available: true  },
  { cmd: "analyze", icon: "🔬", label: "深度分析",     desc: "趋势、异常与业务洞察",             available: false },
  { cmd: "report",  icon: "📄", label: "生成报告",     desc: "结构化分析报告",                  available: false },
];

// ── State ─────────────────────────────────────────────────────────
let SID = null;
let srcConnected = false;
let srcName = "未连接";
let schemaText = "";
let isStreaming = false;
let activeCommand = "";
let slashPopupIndex = 0;
let tokenState = { promptTokens: 0, totalInput: 0, totalOutput: 0, contextWindow: null };
let modelConfigs = {};

// ── Bootstrap ─────────────────────────────────────────────────────
(async () => {
  buildSlashPopup();
  const r = await fetch("/api/session/new", { method: "POST" });
  SID = (await r.json()).session_id;
  await loadModels();
  await loadBuiltinProviders();
  await loadSavedList();
})();

// ── Slash popup ────────────────────────────────────────────────────
function buildSlashPopup() {
  const pop = document.getElementById("slash-popup");
  pop.querySelectorAll(".slash-item").forEach(el => el.remove());
  COMMANDS.forEach((c, i) => {
    const div = document.createElement("div");
    div.className = "slash-item" + (c.available ? "" : " disabled") + (i === 0 ? " active" : "");
    div.dataset.cmd = c.cmd;
    div.dataset.available = c.available ? "1" : "0";
    div.innerHTML = `
      <span class="slash-icon">${c.icon}</span>
      <div class="slash-info">
        <div class="slash-name">/${c.cmd}
          ${!c.available ? '<span class="slash-soon">即将推出</span>' : ""}
        </div>
        <div class="slash-desc">${c.desc}</div>
      </div>`;
    if (c.available) div.onclick = () => selectCommand(c.cmd);
    pop.appendChild(div);
  });
}

function openSlashPopup() {
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
  const items = [...document.querySelectorAll(".slash-item:not(.disabled)")];
  document.querySelectorAll(".slash-item").forEach(el => el.classList.remove("active"));
  if (items[slashPopupIndex]) items[slashPopupIndex].classList.add("active");
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
  if (v === "/") { openSlashPopup(); return; }
  const m = v.match(/^\/(\w+)\s?/);
  if (m) {
    const found = COMMANDS.find(c => c.cmd === m[1] && c.available);
    if (found) {
      selectCommand(found.cmd);
      e.target.value = v.slice(m[0].length);
      autoResize(e.target);
      return;
    }
  }
  if (isSlashOpen()) closeSlashPopup();
}

function onKeyDown(e) {
  if (isSlashOpen()) {
    const available = [...document.querySelectorAll(".slash-item:not(.disabled)")];
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
  sel.innerHTML = '<option value="">— 选择模型 —</option>';
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
  deepseek: { label: "DeepSeek", icon: COMMON_ICON },
  openai:   { label: "OpenAI / ChatGPT", icon: COMMON_ICON },
  claude:   { label: "Anthropic Claude", icon: COMMON_ICON },
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
            ${hasKey ? "已配置" : "未配置"}
          </span>
        </div>
        <div class="provider-fields">
          <div class="pf-row">
            <label>API Key</label>
            <input type="password" id="pk-${key}" placeholder="sk-… 或留空清除">
          </div>
          <div class="pf-row">
            <label>Base URL</label>
            <input type="text" id="pu-${key}" value="${cfg.base_url || def.base_url}" placeholder="${def.base_url}">
          </div>
          <div class="pf-row">
            <label>Model</label>
            <input type="text" id="pm-${key}" value="${cfg.model || def.model}" placeholder="${def.model}">
          </div>
          <div class="pf-row">
            <label>上下文窗口</label>
            <input type="number" id="pctx-${key}" value="${cfg.context_window ?? def.context_window ?? ''}" placeholder="tokens，例如 64000">
          </div>
          <div class="pf-row">
            <label>最大输出</label>
            <input type="number" id="pout-${key}" value="${cfg.max_output_tokens ?? def.max_output_tokens ?? ''}" placeholder="tokens，例如 8192">
          </div>
          <div class="pf-row" style="align-items:center">
            <label>思考模式</label>
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;color:#475569">
              <input type="checkbox" id="pthink-${key}" ${cfg.enable_thinking ? "checked" : ""}>
              启用思考模式
            </label>
          </div>
        </div>
        <div class="provider-actions">
          <button class="btn-sm btn-sm-danger" onclick="clearBuiltin('${key}')">清除</button>
          <button class="btn-sm btn-sm-primary" onclick="saveBuiltin('${key}')">保存</button>
        </div>
        <div class="provider-msg" id="pmsg-${key}"></div>
      </div>`;
  }
}

function renderCustomList(configs) {
  const list = document.getElementById("custom-list");
  const customs = Object.entries(configs).filter(([, v]) => v.is_custom);
  if (!customs.length) {
    list.innerHTML = '<div class="custom-empty">暂无自定义模型</div>';
    return;
  }
  list.innerHTML = customs.map(([key, cfg]) => `
    <div class="custom-item">
      <span class="ci-name">${cfg.model || key}</span>
      <span class="ci-model">${cfg.base_url || ""}</span>
      <button class="btn-sm btn-sm-danger" onclick="deleteCustom('${key}')">删除</button>
    </div>`).join("");
}

async function saveBuiltin(key) {
  const apiKey  = document.getElementById(`pk-${key}`).value.trim();
  const baseUrl = document.getElementById(`pu-${key}`).value.trim();
  const model   = document.getElementById(`pm-${key}`).value.trim();
  const ctxRaw  = document.getElementById(`pctx-${key}`).value.trim();
  const outRaw  = document.getElementById(`pout-${key}`).value.trim();
  const msgEl   = document.getElementById(`pmsg-${key}`);
  if (!apiKey) { msgEl.className="provider-msg err"; msgEl.textContent="API Key 不能为空"; return; }
  msgEl.textContent = "保存中…";
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
    msgEl.className = "provider-msg ok"; msgEl.textContent = "保存成功 ✓";
    document.getElementById(`ps-${key}`).className = "provider-status set";
    document.getElementById(`ps-${key}`).textContent = "已配置";
    document.getElementById(`pk-${key}`).value = "";
    await loadModels();
  } else {
    msgEl.className = "provider-msg err"; msgEl.textContent = d.error || "保存失败";
  }
}

async function clearBuiltin(key) {
  if (!confirm(`确认清除 ${BUILTIN_META[key]?.label || key} 的配置？`)) return;
  const r = await fetch("/api/models/clear-builtin", {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ provider: key })
  });
  const d = await r.json();
  if (d.ok) {
    document.getElementById(`ps-${key}`).className = "provider-status unset";
    document.getElementById(`ps-${key}`).textContent = "未配置";
    const msgEl = document.getElementById(`pmsg-${key}`);
    msgEl.className = "provider-msg ok"; msgEl.textContent = "已清除";
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
  if (!confirm("确认删除此自定义模型？")) return;
  await fetch("/api/models/delete", {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ provider })
  });
  await Promise.all([loadModels(), loadBuiltinProviders()]);
}

// ── Data source ────────────────────────────────────────────────────
function setSrc(name, hint, connected) {
  srcConnected = connected;
  srcName = name;
  document.getElementById("src-dot").className = "source-dot" + (connected ? " on" : "");
  document.getElementById("src-name").textContent = name;
  document.getElementById("src-hint").textContent = hint;
  document.getElementById("btn-disc").style.display = connected ? "block" : "none";
  document.getElementById("btn-schema").style.display = connected ? "" : "none";
  document.getElementById("hdr-sub").textContent = connected ? `已连接: ${name}` : "连接数据源开始分析";
  if (connected) hideWelcome();
}
async function disconnectSrc() {
  await fetch(`/api/session/${SID}/datasource`, { method: "DELETE" });
  schemaText = "";
  setSrc("未连接", "请上传文件或连接数据库", false);
  toast("数据源已断开");
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
  btn.disabled = true; btn.textContent = "上传中…";
  const form = new FormData(); form.append("file", f);
  const r = await fetch(`/api/session/${SID}/upload`, { method: "POST", body: form });
  const d = await r.json();
  btn.disabled = false; btn.textContent = "上传";
  if (d.error) { document.getElementById("xl-err").textContent = d.error; return; }
  schemaText = d.schema_preview || "";
  document.getElementById("xl-schema").textContent = schemaText;
  document.getElementById("xl-schema").style.display = "block";
  setSrc(d.source_name, "Excel / CSV 文件", true);
  closeOverlay("ov-excel");
  toast("文件上传成功 ✓", "ok");
  sysMsg(`已加载「${d.source_name}」，可以开始提问了。`);
}

async function connectDB() {
  const conn = document.getElementById("db-conn").value.trim();
  const name = document.getElementById("db-name").value.trim();
  if (!conn) { document.getElementById("db-err").textContent = "请输入连接字符串"; return; }
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
  setSrc(d.source_name, "SQL 数据库", true);
  closeOverlay("ov-db");
  toast("数据库连接成功 ✓", "ok");
  sysMsg(`已连接「${d.source_name}」，可以开始提问了。`);
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
  wrap.innerHTML   = '<div class="preview-loading">加载中…</div>';
  tabs.innerHTML   = "";
  foot.textContent = "";

  const r = await fetch(`/api/session/${SID}/preview`);
  if (!r.ok) {
    wrap.innerHTML = '<div class="preview-loading" style="color:#ef4444">加载失败，请确认数据源已连接</div>';
    return;
  }
  _previewData = await r.json();
  title.textContent = `🗂 数据预览 · ${_previewData.source_name}`;

  const tables = _previewData.tables || [];
  if (!tables.length) {
    wrap.innerHTML = '<div class="preview-loading">无可预览的数据</div>';
    return;
  }

  tables.forEach((t, i) => {
    const tab = document.createElement("div");
    tab.className = "preview-tab" + (i === 0 ? " active" : "");
    tab.textContent = t.name;
    tab.onclick = () => _switchPreviewTab(i);
    tabs.appendChild(tab);
  });

  _renderPreviewTable(tables[0]);
}

function _switchPreviewTab(idx) {
  document.querySelectorAll(".preview-tab").forEach((t, i) =>
    t.classList.toggle("active", i === idx));
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

  const totalTip = total > shown
    ? ` / 共 ${total.toLocaleString()} 行（显示前 ${shown} 行）`
    : ` · 共 ${total.toLocaleString()} 行`;
  foot.textContent = `${table.columns.length} 列${totalTip}`;
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
  document.getElementById("send-btn").disabled = true;

  const payload = { message: text };
  if (activeCommand) payload.command = activeCommand;
  clearCmd();

  const resp = await fetch(`/api/session/${SID}/chat`, {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload)
  });

  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";

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

  isStreaming = false;
  document.getElementById("send-btn").disabled = false;
  scrollBottom();
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
      <button class="chart-expand-btn" onclick="window.open('/api/chart/${ev.chart_id}','_blank')" title="新窗口全屏查看">⛶ 全屏</button>
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
    toggle.innerHTML = `<span class="reasoning-arrow">▶</span> 推理过程`;
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
    label.textContent = `上下文 ${fmtK(promptTokens)} / ${fmtK(contextWindow)} (${pct.toFixed(1)}%)`;
  } else {
    fill.style.width = "0%";
    fill.className = "token-bar-fill";
    label.textContent = `↑ ${fmtK(totalInput)}  ↓ ${fmtK(totalOutput)} tokens`;
  }
}

// ── /status ────────────────────────────────────────────────────────
function showStatus() {
  const provKey = document.getElementById("model-sel").value;
  const cfg = modelConfigs[provKey] || {};
  const modelName = cfg.model || provKey || "未选择";
  const ctx  = tokenState.contextWindow;
  const pct  = (ctx && tokenState.promptTokens)
    ? ` (${(tokenState.promptTokens / ctx * 100).toFixed(1)}%)`
    : "";

  const lines = [
    `**当前模型**　${modelName}`,
    `**数据源**　　${srcConnected ? srcName : "未连接"}`,
    ``,
    `**Token 用量（本次会话）**`,
    `输入累计　${tokenState.totalInput.toLocaleString()} tokens`,
    `输出累计　${tokenState.totalOutput.toLocaleString()} tokens`,
    ctx
      ? `当前上下文　${tokenState.promptTokens.toLocaleString()} / ${ctx.toLocaleString()} tokens${pct}`
      : `当前上下文　${tokenState.promptTokens.toLocaleString()} tokens（未配置上下文窗口）`,
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

  // 你可以根据当前模型/供应商动态决定图标
  // 这里先给一个默认机器人图标路径（按你的实际静态目录改）
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
  toast(`已保存「${d.name}」✓`, "ok");
  await loadSavedList();
}

async function loadSavedList() {
  const box = document.getElementById("saved-list");
  const r = await fetch("/api/saved-sessions");
  const list = await r.json();
  if (!list.length) {
    box.innerHTML = '<div class="saved-empty">暂无保存的对话</div>';
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
        <button class="saved-del" title="删除" onclick="deleteSavedSession('${esc(s.filename)}','${esc(s.name)}')">✕</button>
      </div>`;
  }).join("");
}

async function loadSavedSession(filename, name) {
  if (!confirm(`加载「${name}」？当前对话内容将被替换。`)) return;
  const r = await fetch(`/api/session/${SID}/load`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename }),
  });
  const d = await r.json();
  if (d.error) { toast(d.error, "err"); return; }

  // Clear UI
  document.querySelectorAll(".msg, .sys-msg").forEach(el => el.remove());
  hideWelcome();

  // Restore data source display
  if (d.ds_connected) {
    setSrc(d.ds_name, "已恢复连接", true);
  } else if (d.ds_lost) {
    setSrc(d.ds_name + "（文件缺失）", "原文件已不存在，仅恢复对话历史", false);
    toast("数据文件已不存在，仅恢复对话历史", "err");
  } else {
    setSrc("未连接", "请上传文件或连接数据库", false);
  }

  // Restore model selector
  if (d.model_provider) {
    const sel = document.getElementById("model-sel");
    if ([...sel.options].some(o => o.value === d.model_provider)) {
      sel.value = d.model_provider;
      onModelChange();
    }
  }

  // Restore token state
  tokenState = {
    promptTokens: 0,
    totalInput:   d.total_input  || 0,
    totalOutput:  d.total_output || 0,
    contextWindow: tokenState.contextWindow,
  };
  updateTokenBar();

  // Render history messages
  for (const msg of d.history) {
    if (msg.role === "user") {
      appendMsg("user", msg.content);
    } else if (msg.role === "assistant" && msg.content) {
      const el = appendMsg("assistant", null);
      el.querySelector(".msg-bubble").innerHTML = renderMd(msg.content);
    }
  }

  sysMsg(`已加载「${d.name}」`);
  toast(`已加载「${d.name}」`, "ok");
}

async function deleteSavedSession(filename, name) {
  if (!confirm(`确认删除「${name}」？此操作不可撤销。`)) return;
  const r = await fetch(`/api/saved-sessions/${encodeURIComponent(filename)}`, { method: "DELETE" });
  const d = await r.json();
  if (d.error) { toast(d.error, "err"); return; }
  toast(`已删除「${name}」`);
  await loadSavedList();
}

// ── Markdown (lightweight) ─────────────────────────────────────────
function esc(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
function renderMd(text) {
  if (!text) return "";
  let s = esc(text);
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
