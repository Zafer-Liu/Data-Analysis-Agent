import { registerUiIsland } from "../../core/ui-registry.js";
import {
  clearDrawioRuntimeCaches,
  drawioEmbedUrl,
  initDrawioProtocol,
  downloadDataUrl,
  downloadXmlFile,
} from "./drawio-postmessage.js";
function text(key, fallback, params) {
  const value = window.t?.(key, params);
  return value && value !== key ? value : fallback;
}


// ── relative time ─────────────────────────────────────────────

function _relativeTime(iso) {
  if (!iso) return "";
  const then = new Date(iso);
  if (isNaN(then)) return "";
  const now = new Date();
  const diff = Math.floor((now - then) / 1000);
  if (diff < 60) return text("canvas.just_now", "刚刚");
  if (diff < 3600) return text("canvas.minutes_ago", `${Math.floor(diff / 60)}分钟前`, { count: Math.floor(diff / 60) });
  if (diff < 86400) return text("canvas.hours_ago", `${Math.floor(diff / 3600)}小时前`, { count: Math.floor(diff / 3600) });
  if (diff < 604800) return text("canvas.days_ago", `${Math.floor(diff / 86400)}天前`, { count: Math.floor(diff / 86400) });
  return then.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

const TEMPLATE_ICONS = {
  blank_canvas:           "⬜",
  business_model_canvas:  "🗺",
  bcg_matrix:             "📊",
  swot_analysis:          "🔲",
  value_proposition:      "💎",
};

function _templateField(tpl, field) {
  const lang = (window.getLang?.() || "zh").toLowerCase();
  const en = lang.startsWith("en") ? tpl[`${field}_en`] : null;
  return en || tpl[field];
}

export function mountBusinessCanvasUi() {
  window.BAA = window.BAA || {};
  const Vue  = window.Vue;
  const root = document.getElementById("business-canvas-root");
  const hasVue = root && Vue && Vue.h && Vue.render;
  if (!hasVue) { registerUiIsland("businessCanvas", null); return; }

  const { h, render, reactive } = Vue;

  const state = reactive({
    open:           false,
    fullscreen:     false,
    loading:        false,
    saving:         false,
    error:          "",
    templates:      [],
    projects:       [],
    currentProject: null,
    drawioReady:    false,
    exporting:      false,
    editingTitle:   false,
    draftTitle:     "",
    editingProjectId: null,
    editingProjectTitle: "",
  });

  // ════════════════════════════════════════════════════════════
  //  DRAWER SKELETON — plain DOM, Vue NEVER touches this structure
  //
  //  root (business-canvas-drawer)
  //    └─ dw-body
  //         ├─ dw-sidebar          ← Vue renders INTO this (children only)
  //         └─ dw-canvas
  //              ├─ dw-toolbar-mount  ← Vue renders INTO this (children only)
  //              └─ dw-iframe-container ← Vue NEVER touches this
  //                   └─ <iframe>     ← created once, lives forever
  // ════════════════════════════════════════════════════════════

  root.textContent = "";

  const body = document.createElement("div");
  body.className = "dw-body";

  const sidebarMount = document.createElement("aside");
  sidebarMount.className = "dw-sidebar";

  const canvasEl = document.createElement("main");
  canvasEl.className = "dw-canvas";

  const toolbarMount = document.createElement("div");
  const iframeMount  = document.createElement("div");
  iframeMount.className = "dw-iframe-container";

  canvasEl.appendChild(toolbarMount);
  canvasEl.appendChild(iframeMount);
  body.appendChild(sidebarMount);
  body.appendChild(canvasEl);
  root.appendChild(body);

  // ── draw.io iframe — created once, lives in iframeMount forever ──

  const DRAWIO_INIT_TIMEOUT_MS = 60000;
  const DRAWIO_MAX_RETRIES = 2;
  const DRAWIO_LOAD_ERROR = text("canvas.load_error", "draw.io 编辑器加载失败，请关闭商业画布后重试。");

  let _protocol   = null;
  let _iframe     = null;
  let _pendingXml = null;
  let _initTimer  = null;
  let _retries    = 0;
  let _starting   = false;

  function _clearInitTimer() {
    if (_initTimer) {
      clearTimeout(_initTimer);
      _initTimer = null;
    }
  }

  function _destroyIframe() {
    _clearInitTimer();
    if (_protocol) _protocol.destroy();
    _protocol = null;
    if (_iframe?.parentNode) _iframe.parentNode.removeChild(_iframe);
    _iframe = null;
    state.drawioReady = false;
    _starting = false;
  }

  function _retryIframe() {
    _destroyIframe();
    if (_retries >= DRAWIO_MAX_RETRIES) {
      state.error = DRAWIO_LOAD_ERROR;
      _render();
      return;
    }
    _retries += 1;
    _ensureIframe({ forceCacheBust: true });
  }

  function _armInitTimer() {
    _clearInitTimer();
    _initTimer = setTimeout(() => {
      if (!state.drawioReady) _retryIframe();
    }, DRAWIO_INIT_TIMEOUT_MS);
  }

  function _ensureIframe({ forceCacheBust = false } = {}) {
    if (_protocol || _starting) return;
    _starting = true;

    (async () => {
      await clearDrawioRuntimeCaches();
      if (_protocol) return;

      const iframe = document.createElement("iframe");
      iframe.src = drawioEmbedUrl({
        cacheBust: forceCacheBust ? `${Date.now()}-${_retries}` : "",
      });
      iframe.style.cssText = "width:100%;height:100%;border:none;";
      iframe.setAttribute("allow", "clipboard-read; clipboard-write");
      iframeMount.appendChild(iframe);
      _iframe = iframe;
      _armInitTimer();

      _protocol = initDrawioProtocol(iframe, {
        onReady() {
          _clearInitTimer();
          _starting = false;
          _retries = 0;
          state.drawioReady = true;
          if (state.error === DRAWIO_LOAD_ERROR) state.error = "";
          _render();
          if (_pendingXml) {
            _protocol.loadXml(_pendingXml);
            _pendingXml = null;
          }
        },
        onAutosave(xml) {
          if (state.currentProject?.id) {
            window.BAA.businessCanvas?.saveDiagramXml?.(state.currentProject.id, xml);
          }
        },
        onExport(result) {
          state.exporting = false;
          _render();
          if (!result?.dataUrl) return;
          const name = (state.currentProject?.title || "diagram").replace(/[/\\?%*:|"<>]/g, "_");
          const fmt  = result.format || "png";
          if (fmt === "png")         downloadDataUrl(result.dataUrl, `${name}.png`);
          else if (fmt === "svg")    downloadDataUrl(result.dataUrl, `${name}.svg`);
          else if (fmt === "xmlsvg") downloadXmlFile(result.xml || "", `${name}.drawio`);
        },
      });
    })().catch((err) => {
      _destroyIframe();
      state.error = `draw.io 编辑器加载失败：${String(err?.message || err)}`;
      _render();
    });
  }

  function loadXml(xml) {
    if (_protocol && state.drawioReady) {
      _protocol.loadXml(xml);
    } else {
      _pendingXml = xml;
    }
  }

  function doExport(fmt) {
    if (!_protocol || !state.drawioReady) return;
    state.exporting = true;
    _render();
    _protocol.exportDiagram(fmt);
  }

  // ── Vue render functions (sidebar and toolbar ONLY) ──────────

  function _renderSidebar() {
    const templates = state.templates || [];
    const projects  = state.projects  || [];

    const templateButtons = templates.map(tpl =>
      h("button", {
        class: "dw-tpl-item",
        type: "button",
        key: `tpl-${tpl.id}`,
        onClick: () => window.BAA.businessCanvas?.createProject?.(tpl.id),
      }, [
        h("span", { class: "dw-tpl-icon" }, TEMPLATE_ICONS[tpl.id] || "📋"),
        h("span", { class: "dw-tpl-name", title: _templateField(tpl, "description") },
          _templateField(tpl, "name")),
      ])
    );

    const projectItems = projects.length
      ? projects.map(p => {
          const isEditing = state.editingProjectId === p.id;
          return h("button", {
            class: `dw-proj-item${state.currentProject?.id === p.id ? " active" : ""}`,
            type: "button",
            key: `proj-${p.id}`,
            onClick: () => window.BAA.businessCanvas?.selectProject?.(p.id),
          }, [
            h("span", { class: "dw-proj-meta" }, [
              isEditing
                ? h("input", {
                    class: "dw-proj-title-input",
                    type: "text",
                    value: state.editingProjectTitle,
                    onInput: (e) => { state.editingProjectTitle = e.target.value; },
                    onKeydown: (e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        const t = state.editingProjectTitle.trim();
                        state.editingProjectId = null;
                        state.editingProjectTitle = "";
                        if (t) window.BAA.businessCanvas?.renameProject?.(p.id, t);
                        _render();
                      } else if (e.key === "Escape") {
                        state.editingProjectId = null;
                        state.editingProjectTitle = "";
                        _render();
                      }
                    },
                    onBlur: () => {
                      // Enter/Escape already closed the editor — skip duplicate save.
                      if (state.editingProjectId !== p.id) return;
                      const t = state.editingProjectTitle.trim();
                      state.editingProjectId = null;
                      state.editingProjectTitle = "";
                      if (t) window.BAA.businessCanvas?.renameProject?.(p.id, t);
                      _render();
                    },
                  })
                : h("span", {
                    class: "dw-proj-title",
                    title: text("canvas.rename", "点击重命名"),
                    onClick: (e) => {
                      e.stopPropagation();
                      state.editingProjectId = p.id;
                      state.editingProjectTitle = p.title || "";
                      _render();
                      setTimeout(() => {
                        const inp = sidebarMount.querySelector(".dw-proj-title-input");
                        if (inp) { inp.focus(); inp.select(); }
                      }, 0);
                    },
                  }, p.title),
              h("span", { class: "dw-proj-sub" }, _relativeTime(p.updated_at)),
            ]),
            h("span", {
              class: "dw-proj-delete",
              title: text("canvas.delete", "删除"),
              onClick: (e) => {
                e.stopPropagation();
                window.BAA.businessCanvas?.deleteProject?.(p.id);
              },
            }, "×"),
          ]);
        })
      : [h("div", { class: "dw-empty", key: "empty" }, text("canvas.empty", "暂无项目"))];

    const templateSection = h("div", { class: "dw-sidebar-section", key: "sec-new" }, [
      h("div", { class: "dw-sidebar-label", key: "lbl-new" }, text("canvas.new", "新建")),
      h("div", { class: "dw-tpl-list", key: "tpl-list" }, templateButtons),
    ]);

    const projectSection = h("div", { class: "dw-sidebar-section", key: "sec-recent" }, [
      h("div", { class: "dw-sidebar-header", key: "hdr-recent" }, [
        h("div", { class: "dw-sidebar-label" }, text("canvas.recent", "最近")),
        projects.length > 0
          ? h("button", {
              class: "dw-sidebar-clear",
              type: "button",
              key: "btn-clear",
              title: text("canvas.clear_all", "清空全部"),
              onClick: async () => {
                const ok = await window.BAA.ui?.confirm?.({
                  title: text("canvas.clear_all", "清空全部"),
                  message: text("canvas.clear_confirm", "确定要清空全部最近项目？此操作不可撤销。"),
                  danger: true,
                });
                if (ok) {
                  await Promise.all(projects.map(p =>
                    window.BAA.businessCanvas?.deleteProject?.(p.id)));
                  window.BAA.businessCanvas?.refresh?.();
                }
              },
            }, text("canvas.clear", "清空"))
          : null,
      ]),
      h("div", { class: "dw-proj-list", key: "proj-list" }, projectItems),
    ]);

    return h("div", { class: "dw-sidebar-inner", key: "sidebar-inner" }, [templateSection, projectSection]);
  }

  function _renderToolbar() {
    const hasProject = !!state.currentProject;
    const title = state.currentProject?.title
      || (state.drawioReady ? "" : text("canvas.loading_editor", "加载编辑器…"));

    const titleVnode = state.editingTitle && state.currentProject
      ? h("input", {
          class: "dw-toolbar-title-input",
          key: "title-input",
          type: "text",
          value: state.draftTitle,
          onInput: (e) => { state.draftTitle = e.target.value; },
          onKeydown: (e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              const newTitle = state.draftTitle.trim();
              state.editingTitle = false;
              state.draftTitle = "";
              if (newTitle && state.currentProject?.id) {
                window.BAA.businessCanvas?.renameProject?.(state.currentProject.id, newTitle);
              }
              _render();
            } else if (e.key === "Escape") {
              state.editingTitle = false;
              state.draftTitle = "";
              _render();
            }
          },
          onBlur: () => {
            // Enter/Escape already closed the editor — skip duplicate save.
            if (!state.editingTitle) return;
            const newTitle = state.draftTitle.trim();
            state.editingTitle = false;
            state.draftTitle = "";
            if (newTitle && state.currentProject?.id) {
              window.BAA.businessCanvas?.renameProject?.(state.currentProject.id, newTitle);
            }
            _render();
          },
        })
      : h("span", {
          class: "dw-toolbar-title",
          key: "title",
          title: text("canvas.rename", "点击重命名"),
          style: state.currentProject ? "cursor:pointer;" : "",
          onClick: () => {
            if (state.currentProject?.id) {
              state.draftTitle = state.currentProject.title || "";
              state.editingTitle = true;
              _render();
              // Focus the input after next render
              setTimeout(() => {
                const input = toolbarMount.querySelector(".dw-toolbar-title-input");
                if (input) {
                  input.focus();
                  input.select();
                }
              }, 0);
            }
          },
        }, title);

    return h("div", { class: "dw-toolbar", key: "toolbar" }, [
      titleVnode,
      h("div", { class: "dw-toolbar-actions", key: "actions" }, [
        hasProject
          ? h("button", {
              class: "btn-sm btn-sm-ghost",
              type: "button",
              key: "btn-png",
              disabled: state.exporting || !state.drawioReady,
              onClick: () => doExport("png"),
            }, "PNG")
          : null,
        hasProject
          ? h("button", {
              class: "btn-sm btn-sm-ghost",
              type: "button",
              key: "btn-drawio",
              disabled: state.exporting || !state.drawioReady,
              onClick: () => doExport("xmlsvg"),
            }, ".drawio")
          : null,
        h("button", {
          class: "btn-sm btn-sm-ghost",
          type: "button",
          key: "btn-fullscreen",
          onClick: () => window.BAA.businessCanvas?.setFullscreen?.(!state.fullscreen),
        }, state.fullscreen ? text("canvas.exit_fullscreen", "退出全屏") : text("canvas.fullscreen", "全屏")),
        h("button", {
          class: "btn-sm btn-sm-ghost",
          type: "button",
          key: "btn-close",
          onClick: () => window.BAA.businessCanvas?.close?.(),
        }, text("canvas.close", "关闭")),
      ]),
    ]);
  }

  // ── master render — Vue only touches sidebar + toolbar ───────

  function _render() {
    // 1. Drawer class (plain DOM)
    root.className = [
      "business-canvas-drawer",
      state.open       ? "open"       : "",
      state.fullscreen ? "fullscreen" : "",
    ].filter(Boolean).join(" ");

    // 2. Vue renders sidebar children INTO sidebarMount
    //    (sidebarMount itself is never replaced by Vue)
    render(_renderSidebar(), sidebarMount);

    // 3. Vue renders toolbar children INTO toolbarMount
    //    (toolbarMount itself is never replaced by Vue)
    render(_renderToolbar(), toolbarMount);

    // 3a. Error banner — handled imperatively so Vue never touches it
    const existingError = toolbarMount.parentNode.querySelector(".dw-error");
    if (state.error) {
      if (!existingError) {
        const err = document.createElement("div");
        err.className = "dw-error";
        err.textContent = state.error;
        toolbarMount.parentNode.insertBefore(err, toolbarMount.nextSibling);
      } else {
        existingError.textContent = state.error;
      }
    } else if (existingError) {
      existingError.remove();
    }

    // 4. iframe lives in iframeMount which Vue NEVER touches
    _ensureIframe();
  }

  document.addEventListener("langchange", _render);
  // ── public API ───────────────────────────────────────────────

  function setState(partial = {}) {
    const hadProject = !!state.currentProject;
    Object.assign(state, partial || {});
    const hasProject = !!state.currentProject;
    if (hadProject && !hasProject) {
      if (_protocol && state.drawioReady) {
        _protocol.loadXml('<mxGraphModel><root><mxCell id="0" /></root></mxGraphModel>');
      }
      _pendingXml = null;
    }
    _render();
  }

  const api = {
    isAvailable: () => true,
    setOpen:       v  => { state.open       = !!v; _render(); },
    setFullscreen: v  => { state.fullscreen = !!v; _render(); },
    setState,
    loadXml,
  };

  registerUiIsland("businessCanvas", api);
  _render();
}
