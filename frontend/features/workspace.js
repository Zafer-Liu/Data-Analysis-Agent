// Workspace (workdir mount) business module.
// Talks to /api/session/<sid>/workspace/* endpoints.
// Sidebar status row + ov-workspace modal shell stay as plain DOM (consistent
// with src-name / mcp-status-text); the current-state card inside the modal is
// rendered by the registered workspace UI island.
import { $, state } from "../core/runtime.js";
import { getUiIsland } from "../core/ui-registry.js";
import { onSourcesUpdated, resetSourceState } from "../legacy/datasource.js";
import { sysMsg } from "../legacy/msg.js";

  // ── Sidebar status row sync ───────────────────────────────────────
  function _setSidebarState(mounted, workdir, name = "") {
    const dot = $("ws-dot");
    const txt = $("ws-status-text");
    if (!dot || !txt) return;
    if (mounted) {
      dot.classList.add("on");
      // Show the last path segment so the row stays narrow.
      const seg = (workdir || "").split(/[\\/]/).filter(Boolean).pop() || workdir || "";
      txt.textContent = name || seg;
      txt.title = workdir || "";
    } else {
      dot.classList.remove("on");
      txt.textContent = window.t("workspace.unmounted");
      txt.title = "";
    }
  }

  // ── API helpers ───────────────────────────────────────────────────
  async function _fetchStatus() {
    const r = await fetch(`/api/session/${state.SID}/workspace`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  async function _fetchKnownWorkspaces() {
    const r = await fetch(`/api/session/${state.SID}/workspaces`);
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  }

  async function _refreshKnownWorkspaces() {
    try {
      const data = await _fetchKnownWorkspaces();
      getUiIsland("workspace")?.setKnownWorkspaces?.(data.workspaces || []);
    } catch (error) {
      getUiIsland("workspace")?.setKnownError?.(String(error.message || error));
    }
  }

  async function _mount(path, permission, expectedWorkspaceId = "") {
    const r = await fetch(`/api/session/${state.SID}/workspace/mount`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, permission, expected_workspace_id: expectedWorkspaceId }),
    });
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;  // 完整响应：{ ok, workspace, added, errors, sources }
  }

  async function _previewSwitch(workspaceId) {
    const r = await fetch(
      `/api/session/${state.SID}/workspaces/${encodeURIComponent(workspaceId)}/switch-preview`
    );
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  }

  async function _renameWorkspace(workspaceId, name) {
    const r = await fetch(
      `/api/session/${state.SID}/workspaces/${encodeURIComponent(workspaceId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      }
    );
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  }

  async function _previewWorkspaceRemoval(workspaceId) {
    const r = await fetch(
      `/api/session/${state.SID}/workspaces/${encodeURIComponent(workspaceId)}/remove-preview`
    );
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  }

  async function _removeWorkspaceRecord(workspaceId) {
    const r = await fetch(
      `/api/session/${state.SID}/workspaces/${encodeURIComponent(workspaceId)}`,
      {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmed: true }),
      }
    );
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  }

  async function _previewStorageCleanup(workspaceId) {
    const r = await fetch(
      `/api/session/${state.SID}/workspaces/${encodeURIComponent(workspaceId)}/storage-cleanup-preview`
    );
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  }

  async function _runStorageCleanup(workspaceId, candidateIds) {
    const r = await fetch(
      `/api/session/${state.SID}/workspaces/${encodeURIComponent(workspaceId)}/storage-cleanup`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmed: true, candidate_ids: candidateIds || undefined }),
      }
    );
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  }

  async function _setPermission(permission) {
    const r = await fetch(`/api/session/${state.SID}/workspace/permission`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ permission }),
    });
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  }

  async function _unmount() {
    const r = await fetch(`/api/session/${state.SID}/workspace/unmount`, {
      method: "POST",
    });
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;  // 完整响应：{ ok, sources }
  }

  // ── Sync Vue island state + sidebar from a workspace object ───────
  function _syncFromWorkspace(ws) {
    const mounted = !!(ws && ws.mounted);
    const workdir = mounted ? ws.workdir : "";
    const artifacts = mounted ? (ws.artifacts_dir || "") : "";
    const permission = mounted ? (ws.permission || "read_only") : "read_only";

    _setSidebarState(mounted, workdir, mounted ? (ws.name || "") : "");

    if (getUiIsland("workspace") && getUiIsland("workspace").isAvailable()) {
      getUiIsland("workspace").setState({
        mounted,
        workspace_id: mounted ? (ws.workspace_id || "") : "",
        name: mounted ? (ws.name || "") : "",
        workdir,
        artifacts_dir: artifacts,
        mounted_at: (ws && ws.mounted_at) || null,
        permission,
      });
    }
    const composerPermission = $("workspace-permission-select");
    if (composerPermission) {
      composerPermission.value = permission;
      composerPermission.disabled = false;
      composerPermission.dataset.mounted = mounted ? "1" : "0";
    }
    const modalPermission = $("ws-permission");
    if (modalPermission) modalPermission.value = permission;
  }

  // ── Public actions ────────────────────────────────────────────────
  async function loadStatus() {
    try {
      const d = await _fetchStatus();
      _syncFromWorkspace(d.workspace);
    } catch (e) {
      _setSidebarState(false, "");
      console.warn("[workspace] loadStatus failed:", e);
    }
    await _refreshKnownWorkspaces();
  }

  async function doMount(options = {}) {
    const input = $("ws-path-input");
    const errEl = $("ws-err");
    const okEl = $("ws-ok");
    if (errEl) errEl.textContent = "";
    if (okEl) okEl.textContent = "";

    const path = String(options.path || (input && input.value) || "").trim();
    const permission = options.permission
      || (($("ws-permission") && $("ws-permission").value) || "read_only");
    const expectedWorkspaceId = String(options.expectedWorkspaceId || "");
    if (!path) {
      if (errEl) errEl.textContent = window.t("workspace.path_required");
      return;
    }

    if (getUiIsland("workspace")) getUiIsland("workspace").setBusy(true, "mount");
    const btn = $("ws-mount-btn");
    if (btn) btn.disabled = true;

    try {
      const d = await _mount(path, permission, expectedWorkspaceId);
      const ws = d.workspace;
      _syncFromWorkspace(ws);
      await _refreshKnownWorkspaces();
      if (d.continued_workspace?.active_job_count) {
        const count = d.continued_workspace.active_job_count;
        window.BAA.overlay?.toast?.(
          window.t("workspace.switched_jobs_continue", { count }), "info"
        );
      }
      await Promise.all([
        window.BAA.slash?.loadCommands?.(),
        window.BAA.skills?.loadSkills?.(),
      ]);

      // B2: large mounted workbooks are parsed outside the request thread.
      const pending = d.pending_jobs || [];
      if (pending.length) {
        const closeBtn = document.querySelector('#ov-workspace [data-action="closeOverlay:ov-workspace"]');
        let cancelRequested = false;
        if (closeBtn) {
          delete closeBtn.dataset.action;
          closeBtn.textContent = "取消解析";
          closeBtn.onclick = async (event) => {
            event.preventDefault();
            event.stopPropagation();
            cancelRequested = true;
            closeBtn.disabled = true;
            await Promise.all(pending.map(job => fetch(
              `/api/session/${state.SID}/jobs/${job.id}/cancel`, { method: "POST" }
            ).catch(() => null)));
          };
        }
        try {
          for (let index = 0; index < pending.length; index++) {
            const job = pending[index];
            let sequence = 0;
            let terminal = null;
            while (!terminal) {
              const response = await fetch(
                `/api/session/${state.SID}/jobs/events?job_id=${encodeURIComponent(job.id)}&after_sequence=${sequence}`
              );
              if (!response.ok) throw new Error(`任务事件读取失败（HTTP ${response.status}）`);
              const payload = await response.json();
              sequence = payload.next_sequence || sequence;
              for (const event of (payload.events || [])) {
                if (event.type === "job_progress" && okEl) {
                  okEl.textContent = pending.length > 1
                    ? `[${index + 1}/${pending.length}] ${event.message}`
                    : event.message;
                }
                if (["job_done", "job_error", "job_canceled"].includes(event.type)) terminal = event;
              }
              if (!terminal) await new Promise(resolve => setTimeout(resolve, 350));
            }
            if (terminal.type === "job_canceled" || cancelRequested) throw new Error("Excel 解析已取消");
            if (terminal.type === "job_error") throw new Error(terminal.error || "Excel 解析失败");
            const finalizedResponse = await fetch(
              `/api/session/${state.SID}/workspace/jobs/${job.id}/finalize`, { method: "POST" }
            );
            const finalized = await finalizedResponse.json();
            if (!finalizedResponse.ok || finalized.error) throw new Error(finalized.error || "解析结果挂载失败");
            d.added = [...(d.added || []), ...(finalized.added || [])];
            d.sources = finalized.sources || d.sources;
            d.schema_preview = finalized.schema_preview || d.schema_preview;
            d.source_name = finalized.source_name || d.source_name;
          }
        } finally {
          if (closeBtn) {
            closeBtn.onclick = null;
            closeBtn.dataset.action = "closeOverlay:ov-workspace";
            closeBtn.disabled = false;
            closeBtn.textContent = "关闭";
          }
        }
      }

      // A5+：同步数据源列表到 sidebar（持久化 source + 缓存复用）
      const added = d.added || [];
      const reused = d.reused || 0;
      const sources = d.sources || [];
      const sourceName = d.source_name || (added.length > 0 ? added[0].source_name : "");
      const hasData = added.length > 0 || reused > 0;

      if (hasData) {
        // schema_preview 从响应取（持久化 source 的完整 schema）
        if (d.schema_preview) {
          state.schemaText = d.schema_preview;
        }
        onSourcesUpdated(sources, sourceName, 'src.hint.file');
        // 关闭 modal
        if (window.BAA.overlay && window.BAA.overlay.closeOverlay) {
          window.BAA.overlay.closeOverlay("ov-workspace");
        }
        // 提示：区分"新加载"和"缓存复用"
        let msg;
        if (added.length > 0 && reused > 0) {
          msg = `已挂载工作目录，新加载 ${added.length} 个文件，${reused} 个缓存复用`;
        } else if (added.length > 1) {
          msg = `已挂载工作目录，加载 ${added.length} 个数据文件`;
        } else if (added.length === 1) {
          msg = `已挂载工作目录，加载 ${added[0].source_name}`;
        } else {
          msg = `已挂载工作目录，${reused} 个文件缓存复用（秒开）`;
        }
        if (window.BAA.overlay && window.BAA.overlay.toast) {
          window.BAA.overlay.toast(msg, "ok");
        }
        sysMsg(msg);
        if (okEl) okEl.textContent = window.t("workspace.mount_ok", { path: ws.workdir });
      } else {
        // 挂载成功但没注册到数据文件
        if (okEl) okEl.textContent = window.t("workspace.mount_ok", { path: ws.workdir });
        // 显示部分错误（如果有）
        if (d.errors && d.errors.length && errEl) {
          errEl.textContent = "部分文件失败: " + d.errors.join("; ");
        }
        // 提示用户目录内无数据文件
        if (window.BAA.overlay && window.BAA.overlay.toast) {
          window.BAA.overlay.toast("工作目录已挂载，但未识别到数据文件（csv/xlsx/xls）", "warn");
        }
      }
      if (input) input.value = "";
      return true;
    } catch (e) {
      if (errEl) errEl.textContent = window.t("workspace.mount_fail", { err: String(e.message || e) });
      return false;
    } finally {
      if (btn) btn.disabled = false;
      if (getUiIsland("workspace")) getUiIsland("workspace").setBusy(false);
    }
  }

  async function onPermissionChange(permission) {
    if (!permission) return;
    const select = $("workspace-permission-select");
    if (!select || select.dataset.mounted !== "1") {
      openModal(permission);
      return;
    }
    if (select) select.disabled = true;
    try {
      const d = await _setPermission(permission);
      _syncFromWorkspace({ mounted: true, ...d.workspace });
      window.BAA.overlay?.toast?.(window.t("workspace.permission_updated"), "ok");
    } catch (error) {
      await loadStatus();
      window.BAA.overlay?.toast?.(String(error.message || error), "err");
    }
  }

  async function doUnmount() {
    const errEl = $("ws-err");
    const okEl = $("ws-ok");
    if (errEl) errEl.textContent = "";
    if (okEl) okEl.textContent = "";

    if (getUiIsland("workspace")) getUiIsland("workspace").setBusy(true, "unmount");

    try {
      const d = await _unmount();
      _syncFromWorkspace({ mounted: false });
      await _refreshKnownWorkspaces();
      if (d.continued_workspace?.active_job_count) {
        const count = d.continued_workspace.active_job_count;
        window.BAA.overlay?.toast?.(
          window.t("workspace.unmounted_jobs_continue", { count }), "info"
        );
      }
      window.BAA.slash?.clearCmd?.();
      window.BAA.skills?.clearSkill?.();
      await Promise.all([
        window.BAA.slash?.loadCommands?.(),
        window.BAA.skills?.loadSkills?.(),
      ]);

      // A4 修复：卸载后同步移除工作目录注册的数据源，更新 sidebar
      const sources = d.sources || [];
      if (sources.length === 0) {
        // 没有其他数据源了，重置 sidebar
        resetSourceState();
      } else {
        // 还有其他数据源（用户上传的），只更新列表
        onSourcesUpdated(sources, null, 'src.hint.file');
      }

      if (okEl) okEl.textContent = window.t("workspace.unmount_ok");
      if (window.BAA.overlay && window.BAA.overlay.toast) {
        window.BAA.overlay.toast("工作目录已卸载", "ok");
      }
    } catch (e) {
      if (errEl) errEl.textContent = window.t("workspace.unmount_fail", { err: String(e.message || e) });
    } finally {
      if (getUiIsland("workspace")) getUiIsland("workspace").setBusy(false);
    }
  }

  // ── Browse button (native directory picker) ──────────────────────
  async function pickWorkdir() {
    const pathInput = $("ws-path-input");
    const hint = $("ws-path-hint");
    const button = document.querySelector('[data-action="pickWorkdir"]');

    if (button) button.disabled = true;
    try {
      const response = await fetch("/api/system/select-directory", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initial_path: (pathInput && pathInput.value || "").trim() }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      if (data.cancelled || !data.path) return;

      if (pathInput) pathInput.value = data.path;
      if (hint) {
        hint.textContent = window.t("workspace.browse_selected", { path: data.path });
        hint.style.color = "#059669";
      }
    } catch (error) {
      if (hint) {
        hint.textContent = window.t("workspace.browse_failed", {
          err: String(error.message || error),
        });
        hint.style.color = "#f59e0b";
      }
      if (pathInput) pathInput.focus();
    } finally {
      if (button) button.disabled = false;
    }
  }

  // ── Open modal: refresh state on every open ───────────────────────
  async function openModal(preferredPermission) {
    await window.openOverlay("ov-workspace");
    // Reset hint color/text on open.
    const hint = $("ws-path-hint");
    if (hint) {
      hint.textContent = window.t("modal.workspace.hint");
      hint.style.color = "";
    }
    const errEl = $("ws-err");
    const okEl = $("ws-ok");
    if (errEl) errEl.textContent = "";
    if (okEl) okEl.textContent = "";
    loadStatus().finally(() => {
      if (preferredPermission && $("ws-permission")) {
        $("ws-permission").value = preferredPermission;
      }
    });
  }

  function selectKnownWorkspace(workspace) {
    if (!workspace?.available) return;
    const input = $("ws-path-input");
    const permission = $("ws-permission");
    if (input) input.value = workspace.root_path || "";
    if (permission) permission.value = workspace.permission || "read_only";
    const hint = $("ws-path-hint");
    if (hint) {
      hint.textContent = window.t("workspace.known_selected", { name: workspace.name || "" });
      hint.style.color = "#059669";
    }
    input?.focus();
  }

  async function activateKnownWorkspace(workspace) {
    if (!workspace?.available || !workspace.workspace_id) return false;
    const errEl = $("ws-err");
    if (errEl) errEl.textContent = "";
    try {
      const preview = await _previewSwitch(workspace.workspace_id);
      if (preview.already_current) return true;
      if (preview.requires_confirmation) {
        const jobsNote = preview.continuing_job_count
          ? window.t("workspace.switch_jobs_note", { count: preview.continuing_job_count })
          : window.t("workspace.switch_no_jobs_note");
        const accepted = await window.BAA.ui?.confirm?.({
          title: window.t("workspace.switch_confirm_title"),
          message: window.t("workspace.switch_confirm_message", {
            from: preview.current?.name || "—",
            to: preview.target.name,
            permission: window.t(
              `workspace.permission.${preview.target.effective_permission || preview.target.permission}`
            ),
            jobs: jobsNote,
          }),
          confirmText: window.t("workspace.switch_confirm_action"),
          cancelText: window.t("common.cancel"),
        });
        if (!accepted) return false;
      }
      return doMount({
        path: preview.target.root_path,
        permission: preview.target.permission,
        expectedWorkspaceId: preview.target.workspace_id,
      });
    } catch (error) {
      const message = String(error.message || error);
      if (errEl) errEl.textContent = window.t("workspace.mount_fail", { err: message });
      window.BAA.overlay?.toast?.(message, "err");
      await _refreshKnownWorkspaces();
      return false;
    }
  }

  async function renameKnownWorkspace(workspaceId, name) {
    const data = await _renameWorkspace(workspaceId, name);
    await loadStatus();
    window.BAA.overlay?.toast?.(
      window.t("workspace.rename_ok", { name: data.workspace?.name || name }), "ok"
    );
    return data;
  }

  async function removeKnownWorkspace(workspace) {
    const preview = await _previewWorkspaceRemoval(workspace.workspace_id);
    if (!preview.can_remove) {
      const reason = (preview.blockers || []).map(item => item.message).join("；");
      throw new Error(window.t("workspace.remove_blocked", { reason }));
    }
    const artifacts = preview.preserved?.artifacts?.file_count || 0;
    const cache = preview.preserved?.cache?.file_count || 0;
    const accepted = await window.BAA.ui?.confirm?.({
      danger: true,
      title: window.t("workspace.remove_confirm_title"),
      message: window.t("workspace.remove_confirm_message", {
        name: workspace.name || workspace.workspace_id.slice(0, 8),
        artifacts,
        cache,
      }),
      confirmText: window.t("workspace.remove_confirm_action"),
      cancelText: window.t("common.cancel"),
    });
    if (!accepted) return false;
    await _removeWorkspaceRecord(workspace.workspace_id);
    await _refreshKnownWorkspaces();
    window.BAA.overlay?.toast?.(
      window.t("workspace.remove_ok", { name: workspace.name || "" }), "ok"
    );
    return true;
  }

  async function cleanupWorkspaceStorage(workspace) {
    const preview = await _previewStorageCleanup(workspace.workspace_id);
    if (preview.blockers?.length) {
      const reason = preview.blockers.map(item => item.message).join("；");
      throw new Error(window.t("workspace.storage_cleanup_blocked", { reason }));
    }
    const candidates = preview.cleanup_candidates || [];
    if (!candidates.length) {
      window.BAA.overlay?.toast?.(window.t("workspace.storage_cleanup_empty"), "ok");
      return false;
    }
    const tableCount = candidates.reduce((sum, item) => sum + ((item.drop_tables || []).length), 0);
    const accepted = await window.BAA.ui?.confirm?.({
      danger: true,
      title: window.t("workspace.storage_cleanup_confirm_title"),
      message: window.t("workspace.storage_cleanup_confirm_message", {
        name: workspace.name || workspace.workspace_id.slice(0, 8),
        candidates: candidates.length,
        tables: tableCount,
      }),
      confirmText: window.t("workspace.storage_cleanup_confirm_action"),
      cancelText: window.t("common.cancel"),
    });
    if (!accepted) return false;
    const result = await _runStorageCleanup(
      workspace.workspace_id,
      candidates.map(item => item.id)
    );
    await _refreshKnownWorkspaces();
    window.BAA.overlay?.toast?.(
      window.t("workspace.storage_cleanup_ok", {
        tables: (result.dropped_tables || []).length,
      }),
      "ok"
    );
    return true;
  }

  // ── Bootstrap: sync sidebar on page load ──────────────────────────
  // Called from app.js bootstrap after session is established.

export const workspace = Object.freeze({
    loadStatus,
    doMount,
    doUnmount,
    pickWorkdir,
    openModal,
    onPermissionChange,
    selectKnownWorkspace,
    activateKnownWorkspace,
    renameKnownWorkspace,
    removeKnownWorkspace,
    cleanupWorkspaceStorage,
});
