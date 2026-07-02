// Compatibility data-source module: uploads, databases, sheets, APIs and multi-source management.
import { eventBus } from "../core/event-bus.js";
import { $, esc, hideWelcome } from "../core/dom.js";
import { closeOverlay, openOverlay, toast } from "../core/overlay.js";
import { sysMsg } from "./msg.js";
import { invalidate as invalidatePreview } from "./preview.js";

  const state = window.BAA.state;
  let warehouseDeleteBusy = false;

  // ── Type icon map ──────────────────────────────────────────────────────────
  const TYPE_ICON = {
    excel: "📊", csv: "📄", sql: "🗄️", gsheets: "📋", http: "🔗",
  };
  const TYPE_LABEL = {
    excel: "Excel", csv: "CSV", sql: "SQL", gsheets: "Sheets", http: "API",
  };

  // ── Render the source list in the sidebar ─────────────────────────────────
  function renderSourceList(sources) {
    state.sources = sources || [];
    const wrap = $("source-list-wrap");
    const ul   = $("source-list");
    if (!wrap || !ul) return;

    if (!sources || sources.length === 0) {
      wrap.hidden = true;
      ul.innerHTML = "";
      return;
    }

    wrap.hidden = false;
    ul.innerHTML = sources.map(src => {
      const icon  = TYPE_ICON[src.type] || "📁";
      const label = TYPE_LABEL[src.type] || src.type;
      const activeClass = src.active ? " source-item--active" : "";
      const toggleTitle = src.active ? "点击取消激活" : "点击激活此数据源";
      return `
        <li class="source-item${activeClass}" data-source-id="${src.id}">
          <button class="source-item-toggle" data-sid="${src.id}" title="${toggleTitle}" aria-pressed="${src.active}">
            <span class="source-toggle-track">
              <span class="source-toggle-thumb"></span>
            </span>
          </button>
          <span class="source-item-icon">${icon}</span>
          <span class="source-item-info">
            <span class="source-item-name" title="${src.name}">${src.name}</span>
            <span class="source-item-type">${label}${src.active ? " · 已激活" : " · 未激活"}</span>
          </span>
          <button class="source-item-btn source-item-btn--remove" data-sid="${src.id}" title="移除此数据源">✕</button>
        </li>`;
    }).join("");

    // Toggle active state
    ul.querySelectorAll(".source-item-toggle").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleSource(btn.dataset.sid);
      });
    });
    // Remove
    ul.querySelectorAll(".source-item-btn--remove").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        removeSource(btn.dataset.sid);
      });
    });
  }

  // ── Update sidebar status row ──────────────────────────────────────────────
  function setSrc(name, hintKey, connected) {
    state.srcConnected = connected;
    state.srcName      = connected ? (name || "") : "";
    state.srcHintKey   = connected ? hintKey : 'sidebar.hint.noconn';

    invalidatePreview();

    const dot = $("src-dot");
    if (dot) dot.classList.toggle("on", connected);

    // Status text: show active count
    const total = state.sources.length;
    const activeCount = state.sources.filter(s => s.active).length;
    let displayName = name || "";
    if (total > 1) {
      displayName = activeCount > 0
        ? `${activeCount}/${total} 个数据源已激活`
        : `${total} 个数据源（均未激活）`;
    }
    $("src-name").textContent = connected ? displayName : t('sidebar.disconnected');

    const hint = $("src-hint");
    if (hint) hint.textContent = t(hintKey);

    const disc = $("btn-disc");
    if (disc) {
      disc.hidden = !connected;
      const sep = $("sb-disc-sep");
      if (sep) sep.hidden = !connected;
    }

    const schemaBtn = $("btn-schema");
    if (schemaBtn) {
      schemaBtn.classList.toggle("is-empty", !connected);
      schemaBtn.title = connected ? t('header.schema') : t('header.subtitle');
    }
    $("hdr-sub").textContent = connected
      ? t('connected_to', { name: displayName })
      : t('header.subtitle');

    document.querySelector(".sidebar")?.classList.toggle("has-source", connected);
    if (connected) hideWelcome();
  }

  // ── After any connect/add operation ───────────────────────────────────────
  function onSourcesUpdated(sources, newSourceName, hintKey) {
    const list = Array.isArray(sources) ? sources : [];
    if (state.analysisContext) {
      const contextTables = Array.isArray(state.analysisContext.tables)
        ? state.analysisContext.tables
        : (state.analysisContext.table ? [state.analysisContext] : []);
      const remaining = contextTables.filter(ctxTable => list.some(
        src => src.id === ctxTable.source_id && src.active
      ));
      state.analysisContext = remaining.length ? { tables: remaining } : null;
    }
    renderSourceList(list);
    const active = list.find(s => s.active);
    const displayName = active ? active.name : (newSourceName || "");
    setSrc(displayName, hintKey || 'src.hint.file', Boolean(active));
  }

  function resetSourceState() {
    state.schemaText = "";
    state.sources = [];
    state._previewData = null;
    state._previewCache = {};
    state._previewSid = null;
    state.analysisContext = null;
    renderSourceList([]);
    setSrc(null, 'sidebar.hint.noconn', false);
  }

  function escAttr(s) {
    return esc(s).replace(/"/g, "&quot;");
  }

  function openSaveWarehouseDialog() {
    const input = $("warehouse-save-name");
    const errEl = $("warehouse-save-err");
    if (input) input.value = "";
    if (errEl) errEl.textContent = "";
    openOverlay("ov-save-warehouse");
    setTimeout(() => input?.focus(), 80);
  }

  async function loadWarehouseList() {
    const box = $("warehouse-list");
    if (!box) return;
    let list;
    try {
      const r = await fetch("/api/data-warehouses");
      list = await r.json();
      if (!r.ok) throw new Error(list.error || `HTTP ${r.status}`);
    } catch (error) {
      box.innerHTML = `<div class="saved-empty">加载失败：${esc(error.message || error)}</div>`;
      return;
    }
    if (!Array.isArray(list) || list.length === 0) {
      box.innerHTML = `<div class="saved-empty">暂无历史数据链接</div>`;
      return;
    }
    box.innerHTML = list.map(item => {
      const date = item.saved_at ? String(item.saved_at).slice(0, 16).replace("T", " ") : "";
      const count = `${item.active_count || 0}/${item.source_count || 0} 激活`;
      const names = Array.isArray(item.source_names) && item.source_names.length
        ? ` · ${esc(item.source_names.filter(Boolean).join("、"))}`
        : "";
      return `
        <div class="saved-item warehouse-item" data-action="loadDataWarehouse"
             data-filename="${escAttr(item.filename)}" data-name="${escAttr(item.name)}"
             title="点击重新连接这组历史数据源">
          <div class="saved-info warehouse-info">
            <div class="saved-name">${esc(item.name || item.filename)}</div>
            <div class="saved-meta">${[date, count].filter(Boolean).join(" · ")}${names}</div>
            <div class="warehouse-hint">点击重新连接数据源，不会加载或覆盖当前对话</div>
          </div>
          <button class="saved-del" title="✕" data-action="deleteDataWarehouse"
                  data-filename="${escAttr(item.filename)}" data-name="${escAttr(item.name)}">✕</button>
        </div>`;
    }).join("");
  }

  async function saveDataWarehouse() {
    const input = $("warehouse-save-name");
    const errEl = $("warehouse-save-err");
    if (errEl) errEl.textContent = "";
    const name = (input?.value || "").trim();
    try {
      const r = await fetch(`/api/session/${state.SID}/data-warehouse/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
      closeOverlay("ov-save-warehouse");
      toast(`已保存数据仓库「${d.name}」`, "ok");
      await loadWarehouseList();
    } catch (error) {
      if (errEl) errEl.textContent = String(error.message || error);
      else toast(String(error.message || error), "err");
    }
  }

  async function loadDataWarehouse(filename, name) {
    const accepted = await window.BAA.ui?.confirm?.({
      title: "重新连接历史数据？",
      message: `将用「${name || filename}」替换当前会话的数据源连接，但不会加载或覆盖当前对话内容。`,
      confirmText: "重新连接",
      cancelText: "取消",
    });
    if (!accepted) return;
    try {
      const r = await fetch(`/api/session/${state.SID}/data-warehouse/load`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
      state.schemaText = "";
      const sources = d.sources || [];
      renderSourceList(sources);
      const active = sources.find(s => s.active);
      if (active) setSrc(active.name, 'src.restored', true);
      else if (sources.length) setSrc(sources[0].name, 'src.hint.file', false);
      else resetSourceState();
      if (d.errors && d.errors.length) {
        toast(`数据仓库已部分加载，${d.errors.length} 个源失败`, "err");
      } else {
        toast(`已加载数据仓库「${d.name || name || filename}」`, "ok");
      }
    } catch (error) {
      toast(String(error.message || error), "err");
    }
  }

  async function deleteDataWarehouse(filename, name) {
    if (warehouseDeleteBusy) return;
    const accepted = await window.BAA.ui?.confirm?.({
      danger: true,
      title: "删除数据仓库？",
      message: `删除「${name || filename}」后无法从列表恢复，但不会删除原始数据文件。`,
      confirmText: "确认删除",
      cancelText: "取消",
    });
    if (!accepted) return;
    warehouseDeleteBusy = true;
    try {
      const r = await fetch(`/api/data-warehouses/${encodeURIComponent(filename)}`, { method: "DELETE" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
      toast(`已删除「${name || filename}」`);
      await loadWarehouseList();
    } catch (error) {
      toast(String(error.message || error), "err");
    } finally {
      warehouseDeleteBusy = false;
    }
  }

  // ── Toggle a source active/inactive ───────────────────────────────────────
  async function toggleSource(sourceId) {
    const r = await fetch(`/api/session/${state.SID}/sources/${sourceId}/toggle`, { method: "POST" });
    const d = await r.json();
    if (d.error) { toast(d.error, "err"); return; }
    state.schemaText = "";
    onSourcesUpdated(d.sources, null, 'src.hint.file');
    const msg = d.active
      ? (t('toast.source_activated') || "已激活数据源")
      : (t('toast.source_deactivated') || "已取消激活");
    toast(msg, "ok");
  }

  // ── Remove one source ──────────────────────────────────────────────────────
  async function removeSource(sourceId) {
    const source = (state.sources || []).find(s => s.id === sourceId);
    const accepted = await window.BAA.ui?.confirm?.({
      danger: true,
      title: "移除此数据源？",
      message: `将从当前会话移除「${source?.name || sourceId}」，不会删除原始文件或已保存的数据仓库。`,
      confirmText: "移除",
      cancelText: "取消",
    });
    if (!accepted) return;
    const r = await fetch(`/api/session/${state.SID}/sources/${sourceId}`, { method: "DELETE" });
    const d = await r.json();
    if (d.error) { toast(d.error, "err"); return; }
    state.schemaText = "";
    if (d.sources.length === 0) {
      setSrc(null, 'sidebar.hint.noconn', false);
      renderSourceList([]);
      toast(t('toast.disconnected'));
    } else {
      onSourcesUpdated(d.sources, null, 'src.hint.file');
      toast(t('toast.source_removed') || "已移除数据源");
    }
  }

  // ── Disconnect ALL sources ─────────────────────────────────────────────────
  async function disconnectSrc() {
    await fetch(`/api/session/${state.SID}/datasource`, { method: "DELETE" });
    resetSourceState();
    toast(t('toast.disconnected'));
  }

  // ── Load saved datasource configs (autofill forms) ────────────────────────
  function _showDsStatus(elId, name) {
    const el = $(elId);
    if (el) { el.textContent = t('ds.configured', { name }); el.classList.remove('hidden'); }
  }

  async function loadDatasourceConfigs() {
    let cfgs;
    try {
      const r = await fetch("/api/datasource-configs");
      cfgs = await r.json();
    } catch { return; }

    const sql = cfgs.sql || {};
    if (sql.has_connection_string) {
      $("db-conn").placeholder        = t('ds.conn_saved_ph');
      $("db-conn").dataset.hasSaved   = "1";
      if (sql.name) $("db-name").value = sql.name;
      _showDsStatus("db-status", sql.name || "SQL DB");
    }

    const gs = cfgs.gsheets || {};
    if (gs.has_creds_json) {
      $("gsheets-creds").placeholder      = t('ds.conn_saved_ph');
      $("gsheets-creds").dataset.hasSaved = "1";
      if (gs.spreadsheet) $("gsheets-sheet").value = gs.spreadsheet;
      if (gs.name)         $("gsheets-name").value = gs.name;
      _showDsStatus("gsheets-status", gs.name || "Google Sheets");
    }

    const api = cfgs.api || {};
    if (api.url) {
      $("api-url").value = api.url;
      if (api.auth_type) $("api-auth-type").value = api.auth_type;
      if (api.auth_type && api.auth_type !== "none") {
        $("api-auth-row").classList.remove('hidden');
      }
      if (api.has_auth_value) {
        $("api-auth-value").placeholder      = t('ds.conn_saved_ph');
        $("api-auth-value").dataset.hasSaved = "1";
      }
      if (api.name) $("api-name").value = api.name;
      _showDsStatus("api-status", api.name || api.url);
    }
  }

  // ── File upload (multi-file) ───────────────────────────────────────────────
  function onXlFile() {
    const files = $("xl-file").files;
    $("xl-btn").disabled        = files.length === 0;
    $("xl-err").textContent     = "";
    $("xl-schema").classList.add('hidden');
    // Show selected file names
    const label = $("xl-file-label");
    if (label) {
      label.textContent = files.length === 0 ? ""
        : files.length === 1 ? files[0].name
        : `${files.length} 个文件`;
    }
  }

  async function uploadXl() {
    const files = $("xl-file").files;
    if (!files || files.length === 0) return;

    const btn           = $("xl-btn");
    const cancelBtn     = $("xl-cancel-btn");
    const progressWrap  = $("xl-progress");
    const progressBar   = $("xl-progress-bar");
    const progressLabel = $("xl-progress-label");
    const errEl         = $("xl-err");

    btn.disabled       = true;
    cancelBtn.disabled = true;
    errEl.textContent  = "";
    progressWrap.classList.remove('hidden');
    progressBar.style.width    = "0%";

    const form = new FormData();
    for (const f of files) form.append("file", f);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/session/${state.SID}/upload`);

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const pct = Math.round(e.loaded / e.total * 100);
        progressBar.style.width = pct + "%";
        progressBar.classList.remove("indeterminate");
        progressLabel.textContent = `${t('btn.uploading')} ${pct}%`;
      } else {
        progressBar.classList.add("indeterminate");
      }
    };

    xhr.upload.onloadend = () => {
      progressBar.classList.remove("indeterminate");
      $("xl-parsing").classList.remove('hidden');
    };

    const d = await new Promise((resolve, reject) => {
      xhr.onload  = () => { try { resolve(JSON.parse(xhr.responseText)); } catch { reject(new Error("服务器响应异常")); } };
      xhr.onerror = () => reject(new Error("网络错误"));
      xhr.send(form);
    }).catch(err => ({ error: err.message }));

    progressBar.classList.remove("indeterminate");
    $("xl-parsing").classList.add('hidden');

    if (d.error) {
      progressWrap.classList.add('hidden');
      btn.disabled = false;
      cancelBtn.disabled = false;
      errEl.textContent = d.error;
      return;
    }

    const pending = d.pending_jobs || [];
    const finalized = [];
    if (pending.length) {
      let canceled = false;
      const oldAction = cancelBtn.dataset.action || "closeOverlay:ov-excel";
      delete cancelBtn.dataset.action;
      cancelBtn.disabled = false;
      cancelBtn.onclick = async (event) => {
        event.preventDefault();
        event.stopPropagation();
        canceled = true;
        cancelBtn.disabled = true;
        progressLabel.textContent = "正在取消 Excel 解析…";
        await Promise.all(pending.map(job => fetch(
          `/api/session/${state.SID}/jobs/${job.id}/cancel`, { method: "POST" }
        ).catch(() => null)));
      };

      try {
        for (let i = 0; i < pending.length; i++) {
          const job = pending[i];
          let sequence = 0;
          let terminal = null;
          while (!terminal) {
            const eventResponse = await fetch(
              `/api/session/${state.SID}/jobs/events?job_id=${encodeURIComponent(job.id)}&after_sequence=${sequence}`
            );
            if (!eventResponse.ok) throw new Error(`任务事件读取失败（HTTP ${eventResponse.status}）`);
            const eventData = await eventResponse.json();
            sequence = eventData.next_sequence || sequence;
            for (const event of (eventData.events || [])) {
              if (event.type === "job_progress") {
                const pct = Math.max(0, Math.min(100, Number(event.progress) || 0));
                progressBar.style.width = pct + "%";
                progressLabel.textContent = pending.length > 1
                  ? `[${i + 1}/${pending.length}] ${event.message || `正在解析 ${pct}%`}`
                  : (event.message || `正在解析 ${pct}%`);
              }
              if (["job_done", "job_error", "job_canceled"].includes(event.type)) terminal = event;
            }
            if (!terminal) {
              const statusResponse = await fetch(`/api/session/${state.SID}/jobs/${job.id}`);
              const statusData = await statusResponse.json();
              const status = statusData.job && statusData.job.status;
              if (["succeeded", "failed", "canceled"].includes(status)) {
                terminal = {
                  type: status === "succeeded" ? "job_done" : status === "failed" ? "job_error" : "job_canceled",
                  error: statusData.job.error,
                };
              }
            }
            if (!terminal) await new Promise(resolve => setTimeout(resolve, 350));
          }
          if (terminal.type === "job_canceled" || canceled) throw new Error("Excel 解析已取消");
          if (terminal.type === "job_error") throw new Error(terminal.error || "Excel 解析失败");

          const finalizeResponse = await fetch(
            `/api/session/${state.SID}/upload-jobs/${job.id}/finalize`, { method: "POST" }
          );
          const finalizeData = await finalizeResponse.json();
          if (!finalizeResponse.ok || finalizeData.error) {
            throw new Error(finalizeData.error || "Excel 解析结果挂载失败");
          }
          finalized.push(...(finalizeData.added || []));
          d.sources = finalizeData.sources || d.sources;
          if (finalizeData.warehouse_autosave) {
            d.warehouse_autosave = finalizeData.warehouse_autosave;
          }
        }
      } catch (error) {
        errEl.textContent = error.message || String(error);
        progressWrap.classList.add('hidden');
        return;
      } finally {
        cancelBtn.onclick = null;
        cancelBtn.dataset.action = oldAction;
        cancelBtn.disabled = false;
        btn.disabled = false;
      }
      d.added = [...(d.added || []), ...finalized];
      if (finalized.length) {
        d.source_name = finalized[0].source_name;
        d.schema_preview = finalized[0].schema_preview;
      }
    } else {
      btn.disabled = false;
      cancelBtn.disabled = false;
    }

    progressWrap.classList.add('hidden');

    // Show partial errors if any
    if (d.errors && d.errors.length) {
      errEl.textContent = "部分文件失败: " + d.errors.join("; ");
    }

    // Update schema display (first added file)
    if (d.added && d.added.length > 0) {
      state.schemaText = d.added[0].schema_preview || "";
      $("xl-schema").textContent  = state.schemaText;
      $("xl-schema").classList.remove('hidden');
    }

    onSourcesUpdated(d.sources || [], d.source_name, 'src.hint.file');
    await loadWarehouseList();
    closeOverlay("ov-excel");

    const msg = d.warehouse_autosave
      ? `已上传并自动缓存到数据仓库「${d.warehouse_autosave.name}」`
      : d.added && d.added.length > 1
      ? `已上传 ${d.added.length} 个文件`
      : t('toast.upload_ok');
    toast(msg, "ok");
    sysMsg(t('sys.connected', { name: d.source_name }));
  }

  // ── SQL DB ─────────────────────────────────────────────────────────────────
  async function connectDB() {
    const conn = $("db-conn").value.trim();
    const name = $("db-name").value.trim();
    const hasSaved = $("db-conn").dataset.hasSaved === "1";
    if (!conn && !hasSaved) { $("db-err").textContent = t('conn_err'); return; }
    $("db-err").textContent = "";
    const loadingEl = $("db-loading");
    const btn       = $("db-btn");
    const cancelBtn = $("db-cancel-btn");
    loadingEl.classList.remove('hidden');
    btn.disabled       = true;
    cancelBtn.disabled = true;
    const r = await fetch(`/api/session/${state.SID}/connect-db`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ connection_string: conn, name }),
    });
    const d = await r.json();
    loadingEl.classList.add('hidden');
    btn.disabled       = false;
    cancelBtn.disabled = false;
    if (d.error) { $("db-err").textContent = d.error; return; }
    state.schemaText = d.schema_preview || "";
    $("db-schema").textContent  = state.schemaText;
    $("db-schema").classList.remove('hidden');
    onSourcesUpdated(d.sources || [], d.source_name, 'src.hint.db');
    closeOverlay("ov-db");
    toast(t('toast.db_ok'), "ok");
    sysMsg(t('sys.connected', { name: d.source_name }));
  }

  // ── Google Sheets ──────────────────────────────────────────────────────────
  async function connectGSheets() {
    const creds = $("gsheets-creds").value.trim();
    const sheet = $("gsheets-sheet").value.trim();
    const name  = $("gsheets-name").value.trim();
    const errEl = $("gsheets-err");
    const hasSavedCreds = $("gsheets-creds").dataset.hasSaved === "1";
    if (!creds && !hasSavedCreds) { errEl.textContent = t('gsheets_err.no_creds'); return; }
    if (!sheet)                   { errEl.textContent = t('gsheets_err.no_sheet'); return; }
    errEl.textContent = "";
    const loadingEl = $("gsheets-loading");
    const btn       = $("gsheets-btn");
    const cancelBtn = $("gsheets-cancel-btn");
    loadingEl.classList.remove('hidden');
    btn.disabled       = true;
    cancelBtn.disabled = true;
    const r = await fetch(`/api/session/${state.SID}/connect-gsheets`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ creds_json: creds, spreadsheet: sheet, name }),
    });
    const d = await r.json();
    loadingEl.classList.add('hidden');
    btn.disabled       = false;
    cancelBtn.disabled = false;
    if (d.error) { errEl.textContent = d.error; return; }
    state.schemaText = d.schema_preview || "";
    $("gsheets-schema").textContent  = state.schemaText;
    $("gsheets-schema").classList.remove('hidden');
    onSourcesUpdated(d.sources || [], d.source_name, 'src.hint.gsheets');
    closeOverlay("ov-gsheets");
    toast(t('toast.gsheets_ok'), "ok");
    sysMsg(t('sys.connected', { name: d.source_name }));
  }

  // ── Custom API ─────────────────────────────────────────────────────────────
  function toggleApiAuthValue() {
    const type = $("api-auth-type").value;
    $("api-auth-row").classList.toggle('hidden', type === "none");
  }

  async function connectAPI() {
    const url       = $("api-url").value.trim();
    const authType  = $("api-auth-type").value;
    const authValue = $("api-auth-value").value.trim();
    const name      = $("api-name").value.trim();
    const errEl     = $("api-err");
    if (!url) { errEl.textContent = t('api_err.no_url'); return; }
    errEl.textContent = "";
    const loadingEl = $("api-loading");
    const btn       = $("api-btn");
    const cancelBtn = $("api-cancel-btn");
    loadingEl.classList.remove('hidden');
    btn.disabled       = true;
    cancelBtn.disabled = true;
    const r = await fetch(`/api/session/${state.SID}/connect-api`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, auth_type: authType, auth_value: authValue, name }),
    });
    const d = await r.json();
    loadingEl.classList.add('hidden');
    btn.disabled       = false;
    cancelBtn.disabled = false;
    if (d.error) { errEl.textContent = d.error; return; }
    state.schemaText = d.schema_preview || "";
    $("api-schema").textContent  = state.schemaText;
    $("api-schema").classList.remove('hidden');
    onSourcesUpdated(d.sources || [], d.source_name, 'src.hint.api');
    closeOverlay("ov-api");
    toast(t('toast.api_ok'), "ok");
    sysMsg(t('sys.connected', { name: d.source_name }));
  }

  export {
    setSrc, renderSourceList, onSourcesUpdated,
    loadDatasourceConfigs, disconnectSrc, resetSourceState,
    openSaveWarehouseDialog, loadWarehouseList, saveDataWarehouse,
    loadDataWarehouse, deleteDataWarehouse,
    onXlFile, uploadXl, connectDB, connectGSheets, connectAPI, toggleApiAuthValue,
  };

  eventBus.on("overlay:open", ({ id }) => {
    if (id === "ov-db" || id === "ov-gsheets" || id === "ov-api") {
      loadDatasourceConfigs();
    }
  });
