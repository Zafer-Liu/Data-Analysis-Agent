// Compatibility bootstrap + global event delegation.
// Replaces all HTML inline on* handlers. Modules under /static/js/modules/ register
// their public API on window.BAA.* and (where needed) on window.* for back-compat.

// Global 401 handler — redirect to /login when session expires (cloud mode)
(function () {
  const _fetch = window.fetch;
  if (!_fetch || _fetch.__authWrapped) return;
  const wrapped = function (input, init) {
    return _fetch.call(this, input, init).then(function (resp) {
      if (resp.status === 401) {
        resp.clone().json().then(function (d) {
          if (d && d.needs_auth) window.location.href = "/login";
        }).catch(function () {});
      }
      return resp;
    });
  };
  wrapped.__authWrapped = true;
  window.fetch = wrapped;
})();

import * as appSettings from "./app_settings.js";
import * as autosave from "./autosave.js";
import * as datasource from "./datasource.js";
import * as jobHistory from "./job_history.js";
import { sidebar } from "../features/sidebar.js";
import { renderMd } from "./markdown.js";
import * as preview from "./preview.js";
import * as sessions from "./sessions.js";
import { runUpdate } from "./update.js";

// If a second stale chat-app.js copy is somehow running, skip all listener
// registration here so there are no duplicate event handlers. This guard must
// be local to app.js because ES module imports run before chat-app.js body code.
if (globalThis.__baaAppDelegationRegistered) {
  console.warn("[app.js] duplicate registration skipped");
} else {
  globalThis.__baaAppDelegationRegistered = true;

(function () {
  const { $ } = window.BAA.dom;
  const state = window.BAA.state;

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

  window.BAA.sidebar = { ...sidebar, setSessionName, syncSessionStatus };

  // ── Action registry (data-action="name[:arg]") ─────────────────────
  // Resolved at click time so modules registered after app.js still work.
  const ACTIONS = {
    // Slash / chat
    onSendOrStop: ()    => window.BAA.chatStream.onSendOrStop(),
    clearCmd:     ()    => window.BAA.slash.clearCmd(),
    clearSkill:   ()    => window.BAA.skills.clearSkill(),
    openSkillPicker: () => sidebar.openPanel("skills"),
    closeSkillPicker: () => sidebar.closePanel("skills"),
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

    // Independent side panels (skills / knowledge / mcp) — island loading via openSidePanel
    openPanel:  (_el, name) => {
      if (name === "skills") {
        window.BAA.skills.open();
      } else {
        globalThis.openSidePanel?.(name);
      }
    },
    closePanel: (_el, name) => sidebar.closePanel(name),
    // KB inline form
    kbCancelForm: () => { window.BAA.knowledge?.kbCancelForm?.(); sidebar.closeKbInlineForm(); },

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
    openBusinessCanvas: () => window.BAA.businessCanvas.open(),
    toggleFocusMode:   () => sidebar.toggleFocusMode(),
    openSaveDialog:    () => sessions.openSaveDialog(),
    loadSavedList:     () => sessions.loadSavedList(),
    setSidebarNav:     (_el, nav) => sidebar.setSidebarNav(nav || "agent"),
    openSidebarDrawer: (_el, tab) => sidebar.openSidebarDrawer(tab || "sessions"),
    closeSidebarDrawer: () => sidebar.closeSidebarDrawer(),
    setDrawerTab:      (_el, tab) => sidebar.setDrawerTab(tab || "sessions"),
    openMcpSettings:   () => window.BAA.mcp.openMcpSettings(),
    loadMcpServers:    () => window.BAA.mcp.loadMcpServers(),
    toggleLang:        () => window.BAA.i18n.setLang(window.BAA.i18n.getLang() === 'zh' ? 'en' : 'zh'),
    toggleTheme:       () => window.BAA.theme.toggleTheme(),
    togglePromptSuggestion: (el) => appSettings.setPromptSuggestionEnabled(el.checked),

    // Data source modals
    uploadXl:          () => datasource.uploadXl(),
    loadSample:        () => datasource.loadSample(),
    connectDB:         () => datasource.connectDB(),
    connectGSheets:    () => datasource.connectGSheets(),
    connectAPI:        () => datasource.connectAPI(),
    cloudLogout:       () => { fetch("/api/auth/logout", {method:"POST"}).finally(() => { window.location.href = "/login"; }); },

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
    kbOpenForm:      (_el, type) => { window.BAA.knowledge.kbOpenForm(type); sidebar.openKbInlineForm(); },
    kbRefresh:       (_el, type) => window.BAA.knowledge.kbRefresh(type),
    kbSwitchTab:     (el, tab)   => window.BAA.knowledge.kbSwitchTab(tab, el),
    kbOpenImport:    () => window.BAA.knowledge.kbOpenImport(),
    kbLoadFiles:     () => window.BAA.knowledge.kbLoadFiles(),
    kbCancelImport:  () => window.BAA.knowledge.kbCancelImport(),
    kbConfirmImport: () => window.BAA.knowledge.kbConfirmImport(),
    kbSubmitForm:    () => window.BAA.knowledge.kbSubmitForm(),
    kbCancelForm:    () => { window.BAA.knowledge?.kbCancelForm?.(); sidebar.closeKbInlineForm(); },
    kbPickFile:      () => $("kb-file-input").click(),
    kbOnFileSelect:  (el, event) => window.BAA.knowledge?.kbOnFileSelect?.(event || { target: el }),
    kbDeleteFile:    (el) => window.BAA.knowledge?.kbDeleteFile?.(el.dataset.filename || ""),
    kbPreviewRemove: (el) => window.BAA.knowledge?.kbPreviewRemove?.(Number(el.dataset.idx)),
    kbPreviewUpdate: (el) => window.BAA.knowledge?.kbPreviewUpdate?.(el),

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
      // The desktop build serves this from a local server that may have just
      // resumed from a backgrounded tab, so retry once before surfacing an
      // error and keep the message actionable instead of a raw TypeError.
      const loadOnce = async () => {
        const r = await fetch("/api/instruction", { cache: "no-store" });
        return r.json();
      };
      try {
        let d;
        try {
          d = await loadOnce();
        } catch (_firstError) {
          await new Promise(resolve => setTimeout(resolve, 400));
          d = await loadOnce();
        }
        if (d.ok && d.markdown) {
          body.innerHTML = renderMd(d.markdown);
        } else {
          body.innerHTML = `<div class="instruction-loading">${
            window.BAA.dom.esc(d.error || "Instruction.md not found")
          }</div>`;
        }
      } catch (e) {
        body.innerHTML = `<div class="instruction-loading">`
          + `${window.BAA.dom.esc(t("modal.instruction.load_fail") || "文档加载失败，请稍后重试。")}`
          + `<br><small>${window.BAA.dom.esc(String(e))}</small></div>`;
      }
    },

    // Sidebar — "Add data source" dropdown
    toggleAddSrc: () => sidebar.toggleAddSrc(),

    // Sidebar — datasource row click. Behaviour depends on connection state:
    //   connected    → open data preview modal
    //   disconnected → open the "Add data source" dropdown
    openDataSource: () => sidebar.openDataSource(),
  };

  sidebar.initAddSourceDropdown();
  sidebar.initPanelKeyClose();

  // Click delegation
  document.addEventListener("click", e => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    if (el.dataset.sidebarNav) sidebar.setSidebarNav(el.dataset.sidebarNav);
    const [name, ...args] = el.dataset.action.split(":");
    const fn = ACTIONS[name];
    if (!fn) { console.warn("[BAA] unknown action:", name); return; }
    fn(el, ...args, e);
  });

  // Direct fallback for "添加自定义模型" toggle — some users report event delegation not firing
  // for this element, so attach a direct listener as well.
  const _directToggle = () => {
    const el = document.querySelector(".add-custom-toggle[data-action='toggleAddCustom']");
    if (!el || el.dataset._baaDirectToggleBound) return;
    el.dataset._baaDirectToggleBound = "1";
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      e.preventDefault();
      try {
        window.BAA.models.toggleAddCustom();
      } catch (err) {
        console.error("[BAA] direct toggleAddCustom error:", err);
      }
    });
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _directToggle);
  } else {
    _directToggle();
  }

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

  // Drag & drop on knowledge base import zone
  const dropZone = document.getElementById("kb-drop-zone");
  if (dropZone) {
    dropZone.addEventListener("dragover", e => e.preventDefault());
    dropZone.addEventListener("drop",     e => window.BAA.knowledge.kbOnDrop && window.BAA.knowledge.kbOnDrop(e));
  }
  const kbFileInput = document.getElementById("kb-file-input");
  if (kbFileInput && !kbFileInput.dataset.change) {
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

  // Custom dropdown for workspace permission in composer toolbar
  const permWrap = document.getElementById("composer-permission-wrap");
  if (permWrap) {
    const trigger = permWrap.querySelector(".composer-permission-trigger");
    const menu = permWrap.querySelector(".composer-permission-menu");
    const options = permWrap.querySelectorAll(".composer-permission-option");
    const label = document.getElementById("composer-permission-label");
    const nativeSelect = document.getElementById("workspace-permission-select");

    const updatePermissionUI = (value) => {
      const active = permWrap.querySelector('.composer-permission-option.active');
      if (active) active.classList.remove('active');
      const next = permWrap.querySelector(`.composer-permission-option[data-value="${value}"]`);
      if (next) {
        next.classList.add('active');
        if (label) label.textContent = next.textContent.trim();
      }
      if (nativeSelect) nativeSelect.value = value;
    };

    if (trigger) {
      trigger.addEventListener("click", (e) => {
        e.preventDefault();
        if (trigger.disabled) return;
        const willOpen = !permWrap.classList.contains("open");
        if (willOpen) {
          // Position the fixed menu above the trigger
          const r = trigger.getBoundingClientRect();
          if (menu) {
            menu.style.left = `${r.left}px`;
            menu.style.bottom = `${window.innerHeight - r.top + 6}px`;
          }
        }
        permWrap.classList.toggle("open", willOpen);
        trigger.setAttribute("aria-expanded", String(willOpen));
      });
    }

    options.forEach(opt => {
      opt.addEventListener("click", () => {
        const value = opt.dataset.value;
        updatePermissionUI(value);
        permWrap.classList.remove("open");
        if (trigger) trigger.setAttribute("aria-expanded", "false");
        if (nativeSelect) {
          nativeSelect.value = value;
          nativeSelect.dispatchEvent(new Event("change", { bubbles: true }));
        }
      });
    });

    document.addEventListener("click", (e) => {
      if (!permWrap.contains(e.target)) {
        permWrap.classList.remove("open");
        if (trigger) trigger.setAttribute("aria-expanded", "false");
      }
    });
    // Close menu on scroll/resize since fixed positioning won't track the trigger
    window.addEventListener("scroll", () => {
      if (permWrap.classList.contains("open")) {
        permWrap.classList.remove("open");
        if (trigger) trigger.setAttribute("aria-expanded", "false");
      }
    }, { passive: true });
    window.addEventListener("resize", () => {
      if (permWrap.classList.contains("open")) {
        permWrap.classList.remove("open");
        if (trigger) trigger.setAttribute("aria-expanded", "false");
      }
    });

    // Sync custom dropdown UI from workspace.js — listen to "perm:sync" (UI-only),
    // NOT "change" (which triggers onPermissionChange and may open the mount modal).
    const syncFromNative = (e) => {
      const value = (e && e.detail && e.detail.permission) || (nativeSelect && nativeSelect.value);
      if (value) updatePermissionUI(value);
    };
    if (nativeSelect) {
      nativeSelect.addEventListener("perm:sync", syncFromNative);
      syncFromNative();
    }
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
}
