/* MCP Settings UI — loaded after dist/core.js (depends on window.openOverlay / closeOverlay / toast) */
import { getUiIsland } from "../core/ui-registry.js";
import { ensureUiIsland } from "./vue-app.js";

let _mcpActiveTab = "local"; // "local" | "paste" — smart-fill 区用

function switchMcpTab(tab) {
  _mcpActiveTab = tab;
  document.getElementById("mcp-panel-local").classList.toggle('hidden', tab !== "local");
  document.getElementById("mcp-panel-paste").classList.toggle('hidden', tab !== "paste");
  document.getElementById("mcp-tab-local").classList.toggle("active", tab === "local");
  document.getElementById("mcp-tab-paste").classList.toggle("active", tab === "paste");
}

async function openMcpSettings() {
  await ensureUiIsland("mcp");
  installMcpPanel();
  await loadMcpServers();
  await window.openOverlay("ov-mcp");
}

function toggleMcpAddForm() {
  if (window.BAA && getUiIsland("mcp")) getUiIsland("mcp").toggleForm();
}

function openMcpEditForm(server) {
  if (window.BAA && getUiIsland("mcp")) {
    getUiIsland("mcp").openForm({ mode: "edit", editId: server.server_id, server });
  }
}

function onMcpTransportChange() {
  // Vue 路径下 transport 由 Vue @change 直接调 setTransport 管理
  // 此函数仅保留给 smart-fill 区 _legacyOpenMcpEditForm 内部调用（已删，留空壳防 HTML data-action 引用）
}

/* ── list ─────────────────────────────────────────────────────── */

async function loadMcpServers() {
  if (!window.BAA || !getUiIsland("mcp")) return;
  const vueMcp = getUiIsland("mcp");
  vueMcp.setListStatus({ loading: true, err: "" });
  try {
    const res = await fetch("/api/mcp/servers");
    const data = await res.json();
    const servers = data.servers || [];
    vueMcp.setServers(servers);
    vueMcp.setListStatus({ loading: false, err: "" });
    _updateMcpSidebarStatus(servers, data.bundled_resources_available !== false);
  } catch (e) {
    vueMcp.setListStatus({ loading: false, err: e.message });
  }
}

function _updateMcpSidebarStatus(servers, bundledResourcesAvailable = true) {
  const dot      = document.getElementById("mcp-dot");
  const textEl   = document.getElementById("mcp-status-text");
  const hintEl   = document.getElementById("mcp-status-hint");
  if (!dot) return;
  const connected = servers.filter(s => s.status === "connected");
  // Only toggle the .on modifier so the dot keeps its base class (.sb-status-dot
  // in the new sidebar; was .source-dot in the legacy layout).
  if (connected.length > 0) {
    dot.classList.add("on");
    textEl.textContent = `${connected.length} 个服务器已连接`;
    const toolCount = connected.reduce((n, s) => n + (s.tool_count || 0), 0);
    hintEl.textContent = toolCount ? `共 ${toolCount} 个工具可用` : "点击管理 MCP 工具服务器";
  } else if (servers.length > 0) {
    dot.classList.remove("on");
    textEl.textContent = `${servers.length} 个服务器未连接`;
    hintEl.textContent = "点击管理 MCP 工具服务器";
  } else if (!bundledResourcesAvailable) {
    dot.classList.remove("on");
    textEl.textContent = window.t ? window.t("sidebar.mcp_not_bundled") : "内置 MCP 未随安装包提供";
    hintEl.textContent = window.t ? window.t("sidebar.mcp_external_hint") : "仍可配置外部 MCP 服务器";
  } else {
    dot.classList.remove("on");
    textEl.textContent = "未配置";
    hintEl.textContent = "点击管理 MCP 工具服务器";
  }
}

function renderMcpServerList(servers) {
  if (window.BAA && getUiIsland("mcp")) getUiIsland("mcp").setServers(servers);
}

/* ── tool detail expand ───────────────────────────────────────── */

async function toggleMcpTools(serverId, _btn) {
  if (!window.BAA || !getUiIsland("mcp")) return;
  const vueMcp = getUiIsland("mcp");
  const server = vueMcp.getServer(serverId);
  if (!server) return;
  const willOpen = !server.toolsOpen;
  vueMcp.toggleToolsOpen(serverId);
  if (!willOpen) return; // 收起，不加载
  if (server.tools && server.tools.length) return; // 已缓存
  vueMcp.setToolsLoading(serverId, true);
  try {
    const res  = await fetch(`/api/mcp/servers/${encodeURIComponent(serverId)}/tools`);
    const data = await res.json();
    vueMcp.setTools(serverId, data.tools || []);
  } catch (e) {
    vueMcp.setToolsErr(serverId, e.message);
  }
}

/* ── add / edit ───────────────────────────────────────────────── */

async function addMcpServer() {
  if (!window.BAA || !getUiIsland("mcp")) return;
  const vueMcp = getUiIsland("mcp");
  const v = vueMcp.getFormValues();
  const fs = vueMcp.getFormState();
  const isEdit = fs.mode === "edit";
  const editId = fs.editId;

  vueMcp.setFormErr("");
  vueMcp.setFormOk("");

  if (!v.label)                           { vueMcp.setFormErr("请填写服务器名称"); return; }
  if (!isEdit && !v.id)                   { vueMcp.setFormErr("请填写服务器 ID");  return; }
  if (!isEdit && !/^[a-zA-Z0-9_]+$/.test(v.id)) {
    vueMcp.setFormErr("服务器 ID 只能包含字母、数字和下划线"); return;
  }

  const payload = { label: v.label, description: v.desc, transport: v.transport };

  if (v.transport === "stdio") {
    if (!v.command) { vueMcp.setFormErr("请填写命令"); return; }
    payload.command = v.command;
    payload.args    = v.args ? v.args.split(/\s+/).filter(Boolean) : [];
    payload.env     = _parseKV(v.env, "=");
  } else {
    if (!v.url) { vueMcp.setFormErr("请填写 SSE 端点 URL"); return; }
    payload.url     = v.url;
    payload.headers = _parseKV(v.headers, ":");
  }

  vueMcp.setFormBusy(true);
  try {
    let res;
    if (isEdit) {
      res = await fetch(`/api/mcp/servers/${encodeURIComponent(editId)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
    } else {
      payload.server_id = v.id;
      res = await fetch("/api/mcp/servers", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
    }
    const data = await res.json();
    if (!res.ok) { vueMcp.setFormErr(data.error || (isEdit ? "更新失败" : "添加失败")); return; }
    vueMcp.setFormOk(isEdit ? "已更新，正在重连…" : "已保存，正在尝试连接…");
    setTimeout(() => {
      if (vueMcp.getFormState().open) vueMcp.closeForm();
      _clearMcpForm();
      loadMcpServers();
    }, 800);
  } catch (e) {
    vueMcp.setFormErr("请求失败: " + e.message);
  } finally {
    vueMcp.setFormBusy(false);
  }
}

/* ── remove ───────────────────────────────────────────────────── */

async function removeMcpServer(serverId) {
  if (!window.BAA || !getUiIsland("mcp")) return;
  if (!await window.BAA.ui?.confirm?.({
    title: "删除 MCP 服务器",
    message: `确定要删除服务器“${serverId}”吗？`,
    danger: true,
  })) return;
  const vueMcp = getUiIsland("mcp");
  vueMcp.removeServer(serverId); // 乐观删除
  try {
    const res = await fetch(`/api/mcp/servers/${encodeURIComponent(serverId)}`, { method: "DELETE" });
    if (!res.ok) {
      const data = await res.json();
      showToast(data.error || "删除失败", "error");
      loadMcpServers(); // 回滚
      return;
    }
    loadMcpServers(); // 刷新 sidebar 状态
  } catch (e) {
    showToast("请求失败: " + e.message, "error");
    loadMcpServers(); // 回滚
  }
}

/* ── connect ──────────────────────────────────────────────────── */

async function connectMcpServer(serverId) {
  if (window.BAA && getUiIsland("mcp")) getUiIsland("mcp").updateServer(serverId, { busy: true });
  try {
    await fetch(`/api/mcp/servers/${encodeURIComponent(serverId)}/connect`, { method: "POST" });
    showToast("正在连接…", "info");
    setTimeout(loadMcpServers, 1500);
  } catch (e) {
    showToast("连接请求失败: " + e.message, "error");
    if (window.BAA && getUiIsland("mcp")) getUiIsland("mcp").updateServer(serverId, { busy: false });
  }
}

/* ── enable/disable ───────────────────────────────────────────── */

async function toggleMcpEnabled(serverId, enabled) {
  if (!window.BAA || !getUiIsland("mcp")) return;
  const vueMcp = getUiIsland("mcp");
  // 乐观更新：先翻转 enabled
  vueMcp.updateServer(serverId, { enabled });
  const action = enabled ? "enable" : "disable";
  try {
    await fetch(`/api/mcp/servers/${encodeURIComponent(serverId)}/${action}`, { method: "POST" });
    if (!enabled) setTimeout(loadMcpServers, 300); // 禁用后刷新状态
  } catch (e) {
    // 回滚
    vueMcp.updateServer(serverId, { enabled: !enabled });
    showToast("操作失败: " + e.message, "error");
  }
}

/* ── helpers ──────────────────────────────────────────────────── */

function _esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function _parseKV(raw, sep) {
  if (!raw) return {};
  return Object.fromEntries(
    raw.split(",")
       .map(s => s.trim())
       .filter(Boolean)
       .map(s => {
         const idx = s.indexOf(sep);
         if (idx === -1) return [s.trim(), ""];
         return [s.slice(0, idx).trim(), s.slice(idx + sep.length).trim()];
       })
  );
}

function _clearMcpForm() {
  if (!window.BAA || !getUiIsland("mcp")) return;
  // Vue 路径：重置 Vue state
  getUiIsland("mcp").resetForm();
  // 同时清 smart-fill 区旧 DOM（不在 Vue 挂载点内）
  _clearSmartFillDom();
}

function _clearSmartFillDom() {
  // 清 smart-fill 区 DOM（smart-fill 区 Vue 不接管）
  const sp = document.getElementById("mcp-scan-path");
  if (sp) sp.value = "";
  const sc = document.getElementById("mcp-scan-status");
  if (sc) { sc.textContent = ""; sc.style.color = ""; sc.innerHTML = ""; }
  const scw = document.getElementById("mcp-scan-warnings");
  if (scw) scw.classList.add('hidden');
  const si = document.getElementById("mcp-smart-input");
  if (si) si.value = "";
  const ss = document.getElementById("mcp-smart-status");
  if (ss) { ss.textContent = ""; ss.style.color = ""; }
  const sw = document.getElementById("mcp-smart-warnings");
  if (sw) sw.classList.add('hidden');
  const sh = document.getElementById("mcp-smart-llm-hint");
  if (sh) sh.classList.add('hidden');
  // Reset smart-fill tab to local
  if (typeof switchMcpTab === "function") switchMcpTab("local");
}

/* ── command preview ──────────────────────────────────────────── */

function updateMcpCmdPreview() {
  // Vue 路径下命令预览由 _renderForm computed 自动渲染，此函数保留为空壳防 HTML data-action 引用
}

/* ── local scan ───────────────────────────────────────────────── */

async function scanLocalMcp() {
  const pathEl  = document.getElementById("mcp-scan-path");
  const statusEl = document.getElementById("mcp-scan-status");
  const warnEl   = document.getElementById("mcp-scan-warnings");
  const btn      = document.getElementById("mcp-scan-btn");

  const path = (pathEl?.value || "").trim();
  if (!path) {
    statusEl.textContent = "请先填写目录路径";
    statusEl.style.color = "#ef4444";
    return;
  }

  statusEl.textContent = "扫描中…";
  statusEl.style.color = "#64748b";
  warnEl.classList.add('hidden');
  btn.disabled = true;

  try {
    const res  = await fetch("/api/mcp/scan-local", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ path }),
    });
    const data = await res.json();

    if (!res.ok) {
      statusEl.textContent = data.error || "扫描失败";
      statusEl.style.color = "#ef4444";
      if (data.hint) {
        warnEl.innerHTML = `💡 ${_esc(data.hint)}`;
        warnEl.style.color = "#64748b";
        warnEl.style.background = "#f8fafc";
        warnEl.classList.remove('hidden');
      }
      return;
    }

    _applyMcpConfig(data.config);

    // Show confidence badge in status
    const pct = data.confidence ?? 0;
    const confColor = pct >= 80 ? "#10b981" : pct >= 50 ? "#f59e0b" : "#ef4444";
    statusEl.innerHTML =
      `✓ 已识别 <strong style="color:${confColor}">${_esc(data.pkg_name)}</strong>` +
      `（置信度 ${pct}%）— 请检查命令预览`;
    statusEl.style.color = "#475569";

    if (data.warnings && data.warnings.length) {
      warnEl.style.color = "#f59e0b";
      warnEl.style.background = "#fef3c7";
      warnEl.innerHTML = "⚠️ 注意：<br>" +
        data.warnings.map(w => `• ${_esc(w)}`).join("<br>");
      warnEl.classList.remove('hidden');
    }

  } catch (e) {
    statusEl.textContent = "请求失败: " + e.message;
    statusEl.style.color = "#ef4444";
  } finally {
    btn.disabled = false;
  }
}

function _applyMcpConfig(cfg, { overwriteLabel = true } = {}) {
  if (!window.BAA || !getUiIsland("mcp")) return;
  // 桥接：写入 Vue state
  const vueMcp = getUiIsland("mcp");
  const fs = vueMcp.getFormState();
  const out = {};
  if (cfg.transport) out.transport = cfg.transport;
  // server_id only fillable in add mode (immutable when editing)
  if (fs.mode === "add" && cfg.server_id) {
    const cur = vueMcp.getFormValues();
    if (!cur.id) out.id = cfg.server_id;
  }
  if (overwriteLabel) {
    if (cfg.label != null) out.label = cfg.label;
    if (cfg.description != null) out.desc = cfg.description;
  } else {
    const cur = vueMcp.getFormValues();
    if (!cur.label && cfg.label != null) out.label = cfg.label;
    if (!cur.desc && cfg.description != null) out.desc = cfg.description;
  }
  if (cfg.transport === "stdio") {
    if (cfg.command != null) out.command = cfg.command;
    if (cfg.args != null) out.args = (cfg.args || []).join(" ");
    if (cfg.env != null) out.env = Object.entries(cfg.env || {}).map(([k, v]) => `${k}=${v}`).join(", ");
  } else {
    if (cfg.url != null) out.url = cfg.url;
    if (cfg.headers != null) out.headers = Object.entries(cfg.headers || {}).map(([k, v]) => `${k}:${v}`).join(", ");
  }
  // setFields 需要支持部分写入，用 setField 逐个写
  Object.entries(out).forEach(([k, v]) => vueMcp.setField(k, v));
}

/* ── smart parse ──────────────────────────────────────────────── */

async function parseMcpConfig() {
  const text     = (document.getElementById("mcp-smart-input")?.value || "").trim();
  const statusEl = document.getElementById("mcp-smart-status");
  const warnEl   = document.getElementById("mcp-smart-warnings");
  const hintEl   = document.getElementById("mcp-smart-llm-hint");
  const btn      = document.getElementById("mcp-smart-btn");

  if (!text) {
    statusEl.textContent = "请先粘贴配置内容";
    statusEl.style.color = "#ef4444";
    return;
  }

  // reset UI
  statusEl.textContent = "解析中…";
  statusEl.style.color = "#64748b";
  warnEl.classList.add('hidden');
  hintEl.classList.add('hidden');
  btn.disabled = true;

  try {
    const res  = await fetch("/api/mcp/parse", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ text }),
    });
    const data = await res.json();

    if (!res.ok) {
      statusEl.textContent = data.error || "解析失败";
      statusEl.style.color = "#ef4444";
      // If LLM not configured, show hint
      if (res.status === 503) {
        hintEl.textContent = "💡 请先在「模型设置」中配置 LLM，再使用智能填充功能";
        hintEl.classList.remove('hidden');
      }
      return;
    }

    _applyMcpConfig(data.config, { overwriteLabel: false });

    // Show warnings if any
    if (data.warnings && data.warnings.length) {
      warnEl.innerHTML = "⚠️ 注意：<br>" +
        data.warnings.map(w => `• ${_esc(w)}`).join("<br>");
      warnEl.classList.remove('hidden');
    }

    statusEl.textContent = "✓ 已填充，请检查并补全标红的必填项";
    statusEl.style.color = "#10b981";

    // Scroll form into view so user sees the filled fields
    document.getElementById("mcp-label")?.scrollIntoView({ behavior: "smooth", block: "nearest" });

  } catch (e) {
    statusEl.textContent = "请求失败: " + e.message;
    statusEl.style.color = "#ef4444";
  } finally {
    btn.disabled = false;
  }
}

// ── Vue island callbacks 注入 ──────────────────────────────────
export function installMcpPanel() {
  if (!window.BAA || !getUiIsland("mcp")) return;
  getUiIsland("mcp").sync({
    onToggleEnabled: (id, enabled) => toggleMcpEnabled(id, enabled),
    onToggleTools:   (id) => toggleMcpTools(id, null),
    onOpenEdit: (id) => {
      const s = getUiIsland("mcp").getServer(id);
      if (s) openMcpEditForm(s);
    },
    onConnect: (id) => connectMcpServer(id),
    onRemove:  (id) => removeMcpServer(id),
    onCancel:  () => _clearMcpForm(),
  });
}

export const mcp = Object.freeze({
  switchMcpTab,
  openMcpSettings,
  toggleMcpAddForm,
  openMcpEditForm,
  onMcpTransportChange,
  loadMcpServers,
  renderMcpServerList,
  toggleMcpTools,
  addMcpServer,
  removeMcpServer,
  connectMcpServer,
  toggleMcpEnabled,
  updateMcpCmdPreview,
  scanLocalMcp,
  parseMcpConfig,
});
