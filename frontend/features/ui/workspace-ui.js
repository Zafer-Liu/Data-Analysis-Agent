import { registerUiIsland } from "../../core/ui-registry.js";

// Workspace island for the ov-workspace modal current-state card.
// Renders the current mount state (mounted path + artifacts dir + unmount/switch
// buttons) inside the ov-workspace modal. Mount/unmount HTTP calls stay in
// modules/workspace.js (business module). Sidebar status row is plain DOM
// (consistent with src-name / mcp-status-text pattern).
export function mountWorkspaceUi() {
  window.BAA = window.BAA || {};
  const Vue = window.Vue;
  const root = document.getElementById("ws-current-state");
  const hasVue = root && Vue && Vue.h && Vue.render;
  if (!hasVue) { registerUiIsland("workspace", null); return; }

  const { h, render, reactive } = Vue;

  const state = reactive({
    mounted: false,
    workspace_id: "",
    name: "",
    workdir: "",
    artifacts_dir: "",
    permission: "read_only",
    mounted_at: null,
    busy: false,        // true while mount/unmount in flight
    busyKind: "",       // "mount" | "unmount"
    knownWorkspaces: [],
    knownError: "",
    editingWorkspaceId: "",
    editingWorkspaceName: "",
    renameBusy: false,
    renameError: "",
    removingWorkspaceId: "",
    cleaningWorkspaceId: "",
  });

  function isAvailable() { return true; }

  function _fmtTime(ts) {
    if (!ts) return "";
    try {
      const d = new Date(ts * 1000);
      return d.toLocaleString();
    } catch (_) { return ""; }
  }

  function _renderEmpty() {
    return h("div", { class: "ws-empty" }, window.t("workspace.empty_hint"));
  }

  function _renderMounted() {
    return h("div", { class: "ws-state-card" }, [
      h("div", { class: "ws-state-row" }, [
        h("span", { class: "ws-state-label" }, window.t("workspace.state_label")),
        h("span", { class: "ws-state-value" }, [
          h("span", { style: "color: var(--color-success); font-weight: 600" }, "● "),
          window.t("workspace.mounted_short"),
        ]),
      ]),
      h("div", { class: "ws-state-row" }, [
        h("span", { class: "ws-state-label" }, window.t("workspace.path_label")),
        h("span", { class: "ws-state-path" }, state.workdir || ""),
      ]),
      state.name ? h("div", { class: "ws-state-row" }, [
        h("span", { class: "ws-state-label" }, window.t("workspace.name_label")),
        h("span", { class: "ws-state-value" }, state.name),
      ]) : null,
      state.artifacts_dir
        ? h("div", { class: "ws-state-row" }, [
            h("span", { class: "ws-state-label" }, window.t("workspace.artifacts_label")),
            h("span", { class: "ws-state-path" }, state.artifacts_dir),
          ])
        : null,
      h("div", { class: "ws-state-row" }, [
        h("span", { class: "ws-state-label" }, window.t("workspace.permission_label")),
        h("span", { class: "ws-state-value" }, window.t(`workspace.permission.${state.permission}`)),
      ]),
      state.mounted_at
        ? h("div", { class: "ws-state-row" }, [
            h("span", { class: "ws-state-label" }, "mounted at"),
            h("span", { class: "ws-state-value", style: "font-size: 12px; color: var(--color-fg-muted)" }, _fmtTime(state.mounted_at)),
          ])
        : null,
      h("div", { class: "ws-state-actions" }, [
        h("button", {
          class: "btn-sm btn-sm-ghost",
          disabled: state.busy,
          onClick: () => {
            // "Switch directory" just focuses the path input — actual mount
            // happens via the primary "Mount" button at the bottom of the modal.
            const inp = document.getElementById("ws-path-input");
            if (inp) { inp.focus(); inp.select(); }
          },
        }, window.t("modal.workspace.remount_btn")),
        h("button", {
          class: "btn-sm btn-sm-ghost",
          style: "color: #ef4444",
          disabled: state.busy,
          onClick: () => {
            if (window.BAA.workspace && typeof window.BAA.workspace.doUnmount === "function") {
              window.BAA.workspace.doUnmount();
            }
          },
        }, state.busy && state.busyKind === "unmount"
          ? window.t("workspace.unmounting")
          : window.t("modal.workspace.unmount_btn")),
      ]),
    ]);
  }

  function _renderKnownWorkspaces() {
    const rows = state.knownWorkspaces || [];
    return h("section", { class: "ws-known" }, [
      h("div", { class: "ws-known-title" }, window.t("workspace.known_title")),
      state.knownError
        ? h("div", { class: "ws-known-error" }, state.knownError)
        : rows.length
          ? h("div", { class: "ws-known-list" }, rows.map(workspace => {
            const editing = state.editingWorkspaceId === workspace.workspace_id;
            return h("div", {
              class: `ws-known-item${workspace.current ? " current" : ""}${workspace.available ? "" : " unavailable"}`,
              key: workspace.workspace_id,
            }, [
              h("div", { class: "ws-known-main" }, [
                editing
                  ? h("div", { class: "ws-rename-editor" }, [
                      h("input", {
                        class: "ws-rename-input",
                        value: state.editingWorkspaceName,
                        maxlength: 80,
                        disabled: state.renameBusy,
                        "aria-label": window.t("workspace.rename_input_label"),
                        onInput: event => { state.editingWorkspaceName = event.target.value; },
                        onKeydown: event => {
                          if (event.key === "Enter") _saveWorkspaceRename(workspace);
                          if (event.key === "Escape") _cancelWorkspaceRename();
                        },
                      }),
                      h("button", { class: "btn-sm btn-sm-primary", type: "button",
                        disabled: state.renameBusy || !state.editingWorkspaceName.trim(),
                        onClick: () => _saveWorkspaceRename(workspace),
                      }, window.t("common.save")),
                      h("button", { class: "btn-sm btn-sm-ghost", type: "button",
                        disabled: state.renameBusy, onClick: _cancelWorkspaceRename,
                      }, window.t("common.cancel")),
                    ])
                  : h("div", { class: "ws-known-name" }, [
                      workspace.name || workspace.workspace_id.slice(0, 8),
                      workspace.current ? h("span", { class: "ws-known-badge" }, window.t("workspace.known_current")) : null,
                    ]),
                editing && state.renameError
                  ? h("div", { class: "ws-rename-error" }, state.renameError)
                  : null,
                h("div", { class: "ws-known-path", title: workspace.root_path }, workspace.root_path),
                h("div", { class: "ws-known-meta" }, [
                  h("span", null, window.t(`workspace.permission.${workspace.permission || "read_only"}`)),
                  workspace.active_job_count
                    ? h("span", null, window.t("workspace.known_active_jobs", { count: workspace.active_job_count }))
                    : null,
                  !workspace.available
                    ? h("span", { class: "ws-known-missing" }, window.t("workspace.known_unavailable"))
                    : null,
                ]),
              ]),
              editing ? null : h("div", { class: "ws-known-actions" }, [
                h("button", {
                  class: "btn-sm btn-sm-ghost",
                  type: "button",
                  disabled: !workspace.available || workspace.current || state.busy,
                  onClick: () => window.BAA.workspace?.activateKnownWorkspace?.(workspace),
                }, workspace.current
                  ? window.t("workspace.known_connected")
                  : state.mounted
                    ? window.t("workspace.known_switch")
                    : window.t("workspace.known_connect")),
                h("button", {
                  class: "btn-sm btn-sm-ghost",
                  type: "button",
                  disabled: !workspace.available || state.busy,
                  onClick: () => _startWorkspaceRename(workspace),
                }, window.t("workspace.rename_action")),
                h("button", {
                  class: "btn-sm btn-sm-ghost ws-known-remove",
                  type: "button",
                  disabled: workspace.current || state.busy || !!state.removingWorkspaceId,
                  title: workspace.current ? window.t("workspace.remove_current_hint") : "",
                  onClick: () => _removeKnownWorkspace(workspace),
                }, state.removingWorkspaceId === workspace.workspace_id
                  ? window.t("workspace.remove_running")
                  : window.t("workspace.remove_action")),
                h("button", {
                  class: "btn-sm btn-sm-ghost",
                  type: "button",
                  disabled: !workspace.available || state.busy || !!state.cleaningWorkspaceId,
                  onClick: () => _cleanupWorkspaceStorage(workspace),
                }, state.cleaningWorkspaceId === workspace.workspace_id
                  ? window.t("workspace.storage_cleanup_running")
                  : window.t("workspace.storage_cleanup_action")),
              ]),
            ]);
          }))
          : h("div", { class: "ws-known-empty" }, window.t("workspace.known_empty")),
    ]);
  }

  function _startWorkspaceRename(workspace) {
    state.editingWorkspaceId = workspace.workspace_id;
    state.editingWorkspaceName = workspace.name || "";
    state.renameError = "";
    _render();
  }

  function _cancelWorkspaceRename() {
    state.editingWorkspaceId = "";
    state.editingWorkspaceName = "";
    state.renameError = "";
    _render();
  }

  async function _saveWorkspaceRename(workspace) {
    const name = state.editingWorkspaceName.trim();
    if (!name || state.renameBusy) return;
    state.renameBusy = true;
    state.renameError = "";
    _render();
    try {
      await window.BAA.workspace?.renameKnownWorkspace?.(workspace.workspace_id, name);
      _cancelWorkspaceRename();
    } catch (error) {
      state.renameError = String(error.message || error);
      _render();
    } finally {
      state.renameBusy = false;
      _render();
    }
  }

  async function _removeKnownWorkspace(workspace) {
    if (state.removingWorkspaceId) return;
    state.removingWorkspaceId = workspace.workspace_id;
    state.knownError = "";
    _render();
    try {
      await window.BAA.workspace?.removeKnownWorkspace?.(workspace);
    } catch (error) {
      state.knownError = String(error.message || error);
      window.BAA.overlay?.toast?.(state.knownError, "err");
    } finally {
      state.removingWorkspaceId = "";
      _render();
    }
  }

  async function _cleanupWorkspaceStorage(workspace) {
    if (state.cleaningWorkspaceId) return;
    state.cleaningWorkspaceId = workspace.workspace_id;
    state.knownError = "";
    _render();
    try {
      await window.BAA.workspace?.cleanupWorkspaceStorage?.(workspace);
    } catch (error) {
      state.knownError = String(error.message || error);
      window.BAA.overlay?.toast?.(state.knownError, "err");
    } finally {
      state.cleaningWorkspaceId = "";
      _render();
    }
  }

  function _render() {
    let body;
    if (state.mounted) {
      body = _renderMounted();
    } else {
      body = _renderEmpty();
    }
    render(h("div", null, [body, _renderKnownWorkspaces()]), root);
  }

  function renderAll() {
    root.innerHTML = "";  // clear static HTML to prevent double-render
    _render();
  }

  // ── facade ──────────────────────────────────────────────────────
  function setState(payload) {
    if (!payload) return;
    if (typeof payload.mounted === "boolean") state.mounted = payload.mounted;
    if (typeof payload.workspace_id === "string") state.workspace_id = payload.workspace_id;
    if (typeof payload.name === "string") state.name = payload.name;
    if (typeof payload.workdir === "string") state.workdir = payload.workdir;
    if (typeof payload.artifacts_dir === "string") state.artifacts_dir = payload.artifacts_dir;
    if (["read_only", "read_write"].includes(payload.permission)) state.permission = payload.permission;
    if (payload.mounted_at !== undefined) state.mounted_at = payload.mounted_at;
    _render();
  }

  function setBusy(busy, kind) {
    state.busy = !!busy;
    state.busyKind = kind || "";
    _render();
  }

  function setKnownWorkspaces(workspaces) {
    state.knownWorkspaces = Array.isArray(workspaces) ? workspaces : [];
    state.knownError = "";
    _render();
  }

  function setKnownError(error) {
    state.knownError = String(error || "");
    _render();
  }

  // Initial render (empty state until loadStatus populates it).
  renderAll();

  registerUiIsland("workspace", {
    isAvailable,
    setState,
    setBusy,
    setKnownWorkspaces,
    setKnownError,
    renderAll,
  });
}
