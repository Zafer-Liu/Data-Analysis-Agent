// Compatibility bootstrap + global event delegation.
// Replaces all HTML inline on* handlers. Modules under /static/js/modules/ register
// their public API on window.BAA.* and (where needed) on window.* for back-compat.
import * as appSettings from "./app_settings.js";
import * as autosave from "./autosave.js";
import * as datasource from "./datasource.js";
import * as jobHistory from "./job_history.js";
import { renderMd } from "./markdown.js";
import * as preview from "./preview.js";
import * as sessions from "./sessions.js";
import { runUpdate } from "./update.js";

(function () {
  const { $ } = window.BAA.dom;
  const state = window.BAA.state;

  function setSidebarNav(nav = "agent") {
    document.querySelectorAll(".sb-nav-item").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.sidebarNav === nav);
    });
  }

  function setDrawerTab(tab = "sessions") {
    const drawer = $("sb-drawer");
    if (!drawer) return;
    drawer.dataset.drawerPanel = tab;
    const title = $("sb-drawer-title");
    if (title) title.textContent = tab === "sources" ? "数据链接" : "会话文件";
    document.querySelectorAll(".sb-drawer-tab").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.drawerTab === tab);
    });
    document.querySelectorAll(".sb-drawer-page").forEach(page => {
      page.classList.toggle("active", page.dataset.drawerPage === tab);
    });
    if (tab === "sources") datasource.loadWarehouseList();
  }

  function openSidebarDrawer(tab = "sessions") {
    const drawer = $("sb-drawer");
    if (!drawer) return;
    setDrawerTab(tab);
    drawer.classList.remove("collapsed");
    if (tab === "sessions") setSidebarNav("history");
  }

  function closeSidebarDrawer() {
    const drawer = $("sb-drawer");
    if (!drawer) return;
    drawer.classList.add("collapsed");
  }

  function syncSessionStatus() {
    const el = $("session-status-text");
    if (!el) return;
    el.textContent = state.sessionName || "新会话";
  }

  function setSessionName(name, filename = "") {
    state.sessionName = String(name || "").trim() || "新会话";
    if (filename !== undefined) state.loadedSessionFilename = filename || "";
    syncSessionStatus();
  }

  function toggleFocusMode() {
    const enabled = !document.body.classList.contains("focus-mode");
    document.body.classList.toggle("focus-mode", enabled);
    const btn = $("btn-focus-mode");
    if (btn) {
      btn.classList.toggle("active", enabled);
      btn.textContent = enabled ? "↩ 退出专注" : "⛶ 专注对话";
      btn.title = enabled ? "恢复完整界面" : "隐藏左侧面板，专注对话";
    }
  }

  window.BAA.sidebar = { setSessionName, syncSessionStatus };

  // ── Action registry (data-action="name[:arg]") ─────────────────────
  // Resolved at click time so modules registered after app.js still work.
  const ACTIONS = {
    // Slash / chat
    onSendOrStop: ()    => window.BAA.chatStream.onSendOrStop(),
    clearCmd:     ()    => window.BAA.slash.clearCmd(),
    clearSkill:   ()    => window.BAA.skills.clearSkill(),
    openSkillPicker: () => window.BAA.skills.open(),
    closeSkillPicker: () => window.BAA.skills.close(),
    openModelPicker: (el) => window.BAA.models.openModelPicker(el),
    closeModelPicker: () => window.BAA.models.closeModelPicker(),
    fillHint:     (el)  => window.BAA.slash.fillHint(el),
    toggleComposerExpanded: () => {
      const shell = document.querySelector(".composer-shell");
      const button = $("composer-expand-btn");
      const input = $("msg-input");
      const expanded = !shell.classList.contains("expanded");
      shell.classList.toggle("expanded", expanded);
      button.setAttribute("aria-expanded", String(expanded));
      button.title = t(expanded ? "composer.collapse" : "composer.expand");
      if (expanded) input.style.height = "220px";
      else window.BAA.slash.autoResize(input);
      input.focus();
    },
    newChat:      ()    => window.BAA.chatStream.newChat(),
    retryStream:  ()    => window.BAA.chatStream.retryLast?.(),

    // Overlay
    openOverlay:  (_el, id) => window.openOverlay(id),
    closeOverlay: (_el, id) => window.BAA.overlay.closeOverlay(id),

    // Sidebar / header
    disconnectSrc:     () => datasource.disconnectSrc(),
    openSaveWarehouseDialog: () => datasource.openSaveWarehouseDialog(),
    loadWarehouseList: () => datasource.loadWarehouseList(),
    saveDataWarehouse: () => datasource.saveDataWarehouse(),
    loadDataWarehouse: (el) => datasource.loadDataWarehouse(el.dataset.filename, el.dataset.name),
    deleteDataWarehouse: (el, event) => {
      event?.stopPropagation?.();
      datasource.deleteDataWarehouse(el.dataset.filename, el.dataset.name);
    },
    openSchemaView:    () => preview.openSchemaView(),
    openJobHistory:    () => jobHistory.open(),
    toggleFocusMode:   () => toggleFocusMode(),
    openSaveDialog:    () => sessions.openSaveDialog(),
    loadSavedList:     () => sessions.loadSavedList(),
    setSidebarNav:     (_el, nav) => setSidebarNav(nav || "agent"),
    openSidebarDrawer: (_el, tab) => openSidebarDrawer(tab || "sessions"),
    closeSidebarDrawer: () => closeSidebarDrawer(),
    setDrawerTab:      (_el, tab) => setDrawerTab(tab || "sessions"),
    openMcpSettings:   () => window.BAA.mcp.openMcpSettings(),
    loadMcpServers:    () => window.BAA.mcp.loadMcpServers(),
    toggleLang:        () => window.BAA.i18n.setLang(window.BAA.i18n.getLang() === 'zh' ? 'en' : 'zh'),
    toggleTheme:       () => window.BAA.theme.toggleTheme(),
    togglePromptSuggestion: (el) => appSettings.setPromptSuggestionEnabled(el.checked),

    // Data source modals
    uploadXl:          () => datasource.uploadXl(),
    connectDB:         () => datasource.connectDB(),
    connectGSheets:    () => datasource.connectGSheets(),
    connectAPI:        () => datasource.connectAPI(),

    // Settings — model providers
    toggleAddCustom:   () => window.BAA.models.toggleAddCustom(),
    addCustomModel:    () => window.BAA.models.addCustomModel(),
    saveBuiltin:       (_el, key) => window.BAA.models.saveBuiltin(key),
    clearBuiltin:      (_el, key) => window.BAA.models.clearBuiltin(key),
    editCustom:        (_el, key) => window.BAA.models.editCustomModel(key),
    deleteCustom:      (_el, key) => window.BAA.models.deleteCustom(key),
    toggleThinkBudget: (_el, key) => window.BAA.models.toggleThinkBudget(key),
    testProvider:      (_el, key) => window.BAA.models.testModel(key),
    toggleAcBudget:    ()         => {
      const cb  = $("ac-think");
      const row = $("ac-budget-row");
      if (cb && row) row.classList.toggle('hidden', !cb.checked);
    },

    // Saved sessions
    saveSession:   () => sessions.saveSession(),
    loadSession:   (el) => sessions.loadSavedSession(el.dataset.filename, el.dataset.name),
    cancelLoadSession: () => sessions.cancelLoadSession(),
    renameSession: (el) => sessions.renameSavedSession(el.dataset.filename, el.dataset.name),
    submitRenameSession: () => sessions.submitRenameSession(),
    deleteSession: (el) => sessions.deleteSavedSession(el.dataset.filename, el.dataset.name),
    confirmDeleteSession: () => sessions.confirmDeleteSavedSession(),

    // Update modal
    runUpdate:   () => runUpdate(),

    // Workspace (workdir mount)
    openWorkspace:   () => window.BAA.workspace.openModal(),
    openTeams:       () => window.BAA.teams.openPanel(),
    mountWorkspace:  () => window.BAA.workspace.doMount(),
    pickWorkdir:     () => window.BAA.workspace.pickWorkdir(),

    // MCP server form
    toggleMcpAddForm: () => window.BAA.mcp.toggleMcpAddForm(),
    addMcpServer:     () => window.BAA.mcp.addMcpServer(),
    switchMcpTab:     (_el, tab) => window.BAA.mcp.switchMcpTab(tab),
    scanLocalMcp:     () => window.BAA.mcp.scanLocalMcp(),
    parseMcpConfig:   () => window.BAA.mcp.parseMcpConfig(),
    updateMcpCmdPreview: () => window.BAA.mcp.updateMcpCmdPreview(),

    // Knowledge base
    kbOpenForm:      (_el, type) => window.BAA.knowledge.kbOpenForm(type),
    kbRefresh:       (_el, type) => window.BAA.knowledge.kbRefresh(type),
    kbSwitchTab:     (el, tab)   => window.BAA.knowledge.kbSwitchTab(tab, el),
    kbLoadFiles:     () => window.BAA.knowledge.kbLoadFiles(),
    kbCancelImport:  () => window.BAA.knowledge.kbCancelImport(),
    kbConfirmImport: () => window.BAA.knowledge.kbConfirmImport(),
    kbSubmitForm:    () => window.BAA.knowledge.kbSubmitForm(),
    kbPickFile:      () => $("kb-file-input").click(),

    // Temporary per-session prompt
    tpSaveRaw:    () => window.BAA.tempPrompt.tpSave(false),
    tpRefine:     () => window.BAA.tempPrompt.tpSave(true),
    tpToggle:     () => window.BAA.tempPrompt.tpToggle(),
    tpClear:      () => window.BAA.tempPrompt.tpClear(),
    tpUpdateCount:() => window.BAA.tempPrompt.tpUpdateCount(),

    // Data-source modal sub-controls
    toggleApiAuthValue: () => datasource.toggleApiAuthValue(),

    // Sidebar — open the user-facing Instruction.md doc in a modal,
    // rendered with marked + DOMPurify (same pipeline as chat messages).
    openInstruction: async () => {
      const body = $("instruction-body");
      window.openOverlay("ov-instruction");
      // Fetch on every open so doc edits show up without a page reload.
      try {
        const r = await fetch("/api/instruction");
        const d = await r.json();
        if (d.ok && d.markdown) {
          body.innerHTML = renderMd(d.markdown);
        } else {
          body.innerHTML = `<div class="instruction-loading">${
            window.BAA.dom.esc(d.error || "Instruction.md not found")
          }</div>`;
        }
      } catch (e) {
        body.innerHTML = `<div class="instruction-loading">${
          window.BAA.dom.esc(String(e))
        }</div>`;
      }
    },

    // Sidebar — "Add data source" dropdown
    toggleAddSrc: () => {
      const dd  = $("sb-add-src");
      if (!dd) return;
      const btn = dd.querySelector(".sb-btn-primary");
      const open = dd.classList.toggle("open");
      if (btn) btn.setAttribute("aria-expanded", String(open));
    },

    // Sidebar — datasource row click. Behaviour depends on connection state:
    //   connected    → open data preview modal
    //   disconnected → open the "Add data source" dropdown
    openDataSource: () => {
      openSidebarDrawer("sources");
      if (!window.BAA.state.srcConnected) {
        const dd = $("sb-add-src");
        if (dd && !dd.classList.contains("open")) {
          dd.classList.add("open");
          const btn = dd.querySelector(".sb-btn-primary");
          if (btn) btn.setAttribute("aria-expanded", "true");
        }
      }
    },
  };

  // "Add data source" dropdown — close on outside click, Esc, or menu-item pick.
  function _closeAddSrcDropdown() {
    const dd = $("sb-add-src");
    if (!dd) return;
    dd.classList.remove("open");
    const btn = dd.querySelector(".sb-btn-primary");
    if (btn) btn.setAttribute("aria-expanded", "false");
  }
  document.addEventListener("click", e => {
    const dd = $("sb-add-src");
    if (!dd || !dd.classList.contains("open")) return;
    // Click on a menu item — close the menu after letting the action fire.
    if (e.target.closest(".sb-dropdown-item")) {
      setTimeout(_closeAddSrcDropdown, 0);
      return;
    }
    // Click anywhere outside the dropdown — close.
    if (!dd.contains(e.target)) _closeAddSrcDropdown();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") _closeAddSrcDropdown();
  });

  // Click delegation
  document.addEventListener("click", e => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    if (el.dataset.sidebarNav) setSidebarNav(el.dataset.sidebarNav);
    const [name, ...args] = el.dataset.action.split(":");
    const fn = ACTIONS[name];
    if (!fn) { console.warn("[BAA] unknown action:", name); return; }
    fn(el, ...args, e);
  });

  // Change delegation (selects / checkboxes / file inputs)
  document.addEventListener("change", e => {
    const el = e.target.closest("[data-change]");
    if (!el) return;
    const [name, ...args] = el.dataset.change.split(":");
    const fn = ACTIONS[name];
    if (!fn) { console.warn("[BAA] unknown change action:", name); return; }
    fn(el, ...args);
  });

  // Input delegation (for live previews/counters)
  document.addEventListener("input", e => {
    const el = e.target.closest("[data-input]");
    if (!el) return;
    const [name, ...args] = el.dataset.input.split(":");
    const fn = ACTIONS[name];
    if (!fn) { console.warn("[BAA] unknown input action:", name); return; }
    fn(el, ...args);
  });

  // Overlay backdrop click → close
  document.addEventListener("click", e => {
    const ov = e.target.closest(".overlay");
    if (!ov || e.target !== ov) return;
    window.BAA.overlay.closeOutside(e, ov.id);
  }, true);

  // Drag & drop on knowledge base import zone
  const dropZone = document.getElementById("kb-drop-zone");
  if (dropZone) {
    dropZone.addEventListener("dragover", e => e.preventDefault());
    dropZone.addEventListener("drop",     e => window.BAA.knowledge.kbOnDrop && window.BAA.knowledge.kbOnDrop(e));
  }
  const kbFileInput = document.getElementById("kb-file-input");
  if (kbFileInput) {
    kbFileInput.addEventListener("change", e => window.BAA.knowledge.kbOnFileSelect && window.BAA.knowledge.kbOnFileSelect(e));
  }

  // Temp-prompt textarea — live character counter
  const tpTextarea = document.getElementById("tp-textarea");
  if (tpTextarea) {
    tpTextarea.addEventListener("input", () => window.BAA.tempPrompt && window.BAA.tempPrompt.tpUpdateCount());
  }

  // Textarea — slash popup driver
  const msgInput = document.getElementById("msg-input");
  if (msgInput) {
    msgInput.addEventListener("input",   e => {
      window.BAA.chatStream?.onComposerInput?.(e);
      window.BAA.slash.onInput(e);
    });
    msgInput.addEventListener("keydown", e => window.BAA.slash.onKeyDown(e));
  }

  // Model select change
  const modelSel = document.getElementById("model-sel");
  if (modelSel) {
    modelSel.addEventListener("change", e => window.BAA.models.onModelChange(e.currentTarget.value));
  }
  const sidebarModelSel = document.getElementById("model-sel-sidebar");
  if (sidebarModelSel) {
    sidebarModelSel.addEventListener("change", e => window.BAA.models.onModelChange(e.currentTarget.value));
  }

  const workspacePermission = document.getElementById("workspace-permission-select");
  if (workspacePermission) {
    workspacePermission.addEventListener("change", e => {
      window.BAA.workspace.onPermissionChange(e.currentTarget.value);
    });
  }

  // Excel file picker change
  const xlFile = document.getElementById("xl-file");
  if (xlFile) {
    xlFile.addEventListener("change", () => datasource.onXlFile());
  }

  // API auth-type select change
  const apiAuthType = document.getElementById("api-auth-type");
  if (apiAuthType) {
    apiAuthType.addEventListener("change", () => datasource.toggleApiAuthValue());
  }

  // MCP transport radios
  document.querySelectorAll('input[name="mcp-transport"]').forEach(r => {
    r.addEventListener("change", () => window.onMcpTransportChange && window.onMcpTransportChange());
  });

  // Language change — re-sync dynamic UI state.
  document.addEventListener('langchange', () => {
    if (!state.srcConnected) {
      $('src-name').textContent = t('sidebar.disconnected');
      $('src-hint').textContent = t('sidebar.hint.noconn');
      $('hdr-sub').textContent  = t('header.subtitle');
    } else {
      $('src-hint').textContent = t(state.srcHintKey);
      $('hdr-sub').textContent  = t('connected_to', { name: state.srcName });
    }
    for (const sel of [$('model-sel'), $('model-sel-sidebar')]) {
      if (sel && sel.options.length > 0 && sel.options[0].value === '') {
        sel.options[0].textContent = t('sidebar.model_placeholder');
      }
    }
    if (window.BAA.models?.renderModelPicker) {
      window.BAA.models.renderModelPicker();
      window.BAA.models.refreshModelPickerLabels?.();
    }
    const sendBtn = $('send-btn');
    if (sendBtn && !sendBtn.classList.contains('stopping')) sendBtn.title = t('send.title');
    if (window.BAA.chatStream?.syncComposerPlaceholder) {
      window.BAA.chatStream.syncComposerPlaceholder();
    } else {
      const input = $('msg-input');
      if (input) input.placeholder = t('input.placeholder');
    }
    const savedEmpty = document.querySelector('#saved-list .saved-empty');
    if (savedEmpty) savedEmpty.textContent = t('saved_empty');
    // Re-sync workspace sidebar status text if unmounted (mounted shows path segment, no need to update)
    if (!document.getElementById('ws-dot')?.classList.contains('on')) {
      const wsTxt = $('ws-status-text');
      if (wsTxt) wsTxt.textContent = t('workspace.unmounted');
    }
    if (window.BAA.slash.isSlashOpen()) window.BAA.slash.buildSlashPopup();
    if (window.BAA.skills?.isOpen()) window.BAA.skills.render();
  });

  // ── Bootstrap ─────────────────────────────────────────────────────
  (async () => {
    // Try to reuse the previous session (so autosave + in-memory history survive refresh)
    const prevSID = localStorage.getItem("baa_session_id");
    let sessionRestored = false;
    if (prevSID) {
      try {
        const ping = await fetch(`/api/session/${prevSID}/ping`);
        if (ping.ok) {
          const { alive } = await ping.json();
          if (alive) {
            state.SID = prevSID;
            sessionRestored = true;
          }
        }
        if (!sessionRestored) {
          const hasJobs = await jobHistory.hasHistory(prevSID);
          if (hasJobs) {
            state.SID = prevSID;
            sessionRestored = true;
          }
        }
      } catch (_) { /* session gone — fall through to new */ }
    }

    if (!sessionRestored) {
      const r = await fetch("/api/session/new", { method: "POST" });
      state.SID = (await r.json()).session_id;
    }
    localStorage.setItem("baa_session_id", state.SID);
    sessionStorage.setItem("baa_session_id", state.SID);
    setSessionName(state.sessionName || "新会话", state.loadedSessionFilename || "");
    await Promise.all([
      window.BAA.slash.loadCommands(),
      window.BAA.skills.loadSkills(),
    ]);
    await jobHistory.init(state.SID);
    await window.BAA.models.loadModels();
    await window.BAA.models.loadBuiltinProviders();
    await sessions.loadSavedList();
    await datasource.loadWarehouseList();
    await datasource.loadDatasourceConfigs();
    // Reflect packaged builds without bundled MCP resources immediately;
    // external MCP configuration remains available through the settings panel.
    if (window.BAA.mcp) await window.BAA.mcp.loadMcpServers();
    // Restore any sources that survived a page reload (new session = empty, that's fine)
    try {
      const sr = await fetch(`/api/session/${state.SID}/sources`);
      const sd = await sr.json();
      if (sd.sources && sd.sources.length > 0) {
        // Always render the list, regardless of active state
        datasource.renderSourceList(sd.sources);
        const active = sd.sources.find(s => s.active);
        if (active) {
          // At least one source is active → connected
          datasource.setSrc(active.name, 'src.hint.file', true);
        } else {
          // Sources exist but none active → still show list, connected=false
          datasource.setSrc(sd.sources[0].name, 'src.hint.file', false);
        }
      }
    } catch { /* non-critical */ }

    // Sync workspace mount state (sidebar dot + modal Vue state)
    if (window.BAA.workspace) window.BAA.workspace.loadStatus();

    // Check for a resumable auto-save from the previous session
    autosave.checkAutosaveOnLoad();
  })();
})();
