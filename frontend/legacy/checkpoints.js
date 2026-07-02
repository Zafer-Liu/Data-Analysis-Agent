// Compatibility /checkpoint file history and time travel.
import { state as appState } from "../core/runtime.js";
import { toast } from "../core/overlay.js";
import { uiRegistry } from "../core/ui-registry.js";
import { workspace } from "../features/workspace.js";
import { renderSourceList } from "./datasource.js";
import { refresh as refreshJobHistory } from "./job_history.js";

  const Vue = window.Vue;
  const root = document.getElementById("checkpoint-root");
  const hasRuntime = Boolean(root && Vue?.h && Vue?.render && Vue?.reactive);
  const h = Vue?.h;
  const render = Vue?.render;
  const state = Vue?.reactive ? Vue.reactive({
    open: false, loading: false, busy: false, error: "", notice: "",
    workspace: null, snapshots: [], restoreTarget: null,
    restoreMode: "code_and_conversation",
  }) : {
    open: false, loading: false, busy: false, error: "", notice: "",
    workspace: null, snapshots: [], restoreTarget: null,
    restoreMode: "code_and_conversation",
  };

  const MODES = [
    { value: "code_and_conversation", label: "恢复文件 + 对话", hint: "默认：撤销文件修改，并把对话退回到该消息之前" },
    { value: "conversation_only", label: "仅恢复对话", hint: "文件保持当前状态，只撤销后续对话" },
    { value: "code_only", label: "仅恢复文件", hint: "对话保持当前状态，只撤销后续文件修改" },
  ];

  async function request(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(data.error || `请求失败 (${response.status})`);
    return data;
  }

  async function load() {
    if (!hasRuntime || !appState.SID) return;
    state.loading = true; state.error = "";
    try {
      const data = await request(`/api/session/${appState.SID}/workspace/checkpoints`);
      state.workspace = data.workspace || null;
      state.snapshots = data.snapshots || [];
    } catch (error) {
      state.workspace = null; state.snapshots = [];
      state.error = error.message || String(error);
    } finally { state.loading = false; draw(); }
  }

  async function waitForJob(jobId) {
    for (let attempt = 0; attempt < 240; attempt += 1) {
      await new Promise(resolve => setTimeout(resolve, 500));
      const data = await request(`/api/session/${appState.SID}/jobs/${jobId}`);
      const job = data.job || {};
      if (["succeeded", "failed", "canceled"].includes(job.status)) {
        if (job.status !== "succeeded") throw new Error(job.error || "回退任务未完成");
        return job;
      }
    }
    throw new Error("回退任务等待超时，请在任务历史中查看状态。");
  }

  async function refreshAfterRewind(mode) {
    if (mode !== "conversation_only") {
      await workspace.loadStatus();
      try {
        const sourceData = await request(`/api/session/${appState.SID}/sources`);
        renderSourceList(sourceData.sources || []);
      } catch (_) {}
    }
  }

  async function rewind() {
    const target = state.restoreTarget;
    if (!target) return;
    const mode = MODES.find(item => item.value === state.restoreMode) || MODES[0];
    const accepted = await uiRegistry.confirm?.({
      title: "确认时光回退",
      message: `将回到“${target.user_text || "会话起点"}”之前。模式：${mode.label}。此操作会建立新的时间线。`,
      confirmText: "确认回退",
      cancelText: "取消",
      danger: true,
    });
    if (!accepted) return;
    state.busy = true; state.error = ""; state.notice = "正在回退，请勿关闭应用…"; draw();
    try {
      const data = await request(
        `/api/session/${appState.SID}/workspace/checkpoints/${encodeURIComponent(target.id)}/restore`,
        {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: true, mode: state.restoreMode }),
        },
      );
      await waitForJob(data.job_id);
      await refreshAfterRewind(state.restoreMode);
      if (state.restoreMode !== "code_only") {
        window.location.reload();
        return;
      }
      state.restoreTarget = null;
      state.notice = "时光回退完成。";
      await load();
      refreshJobHistory();
      toast("已回到所选快照", "ok");
    } catch (error) { state.error = error.message || String(error); }
    finally { state.busy = false; draw(); }
  }

  function openWorkspace() {
    state.open = false; draw();
    workspace.openModal();
  }

  function snapshotCard(item) {
    const title = item.user_text || "会话起点";
    return h("div", { class: "checkpoint-card", key: item.id }, [
      h("div", { class: "checkpoint-card-main" }, [
        h("strong", null, title),
        h("span", null, `${item.file_count || 0} 个文件发生修改`),
        h("small", null, item.created_at ? new Date(item.created_at * 1000).toLocaleString() : ""),
      ]),
      h("button", {
        class: "btn-sm btn-sm-ghost", type: "button", disabled: state.busy,
        onClick: () => {
          state.restoreTarget = item;
          state.restoreMode = "code_and_conversation";
          state.error = ""; draw();
        },
      }, "回到这里"),
    ]);
  }

  function restorePanel() {
    if (!state.restoreTarget) return null;
    const needsWrite = state.restoreMode !== "conversation_only";
    const canWrite = state.workspace?.permission === "read_write";
    return h("div", { class: "checkpoint-confirm" }, [
      h("strong", null, `回到“${state.restoreTarget.user_text || "会话起点"}”之前`),
      h("div", { class: "checkpoint-mode-list" }, MODES.map(mode => h("label", {
        class: `checkpoint-mode${state.restoreMode === mode.value ? " active" : ""}`,
      }, [
        h("input", { type: "radio", name: "rewind-mode", value: mode.value,
          checked: state.restoreMode === mode.value,
          onChange: () => { state.restoreMode = mode.value; draw(); } }),
        h("span", null, [h("b", null, mode.label), h("small", null, mode.hint)]),
      ]))),
      needsWrite && !canWrite
        ? h("div", { class: "checkpoint-error" }, "恢复文件需要将工作目录权限切换为“可读和编辑”。")
        : null,
      h("div", { class: "checkpoint-confirm-actions" }, [
        h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: state.busy,
          onClick: () => { state.restoreTarget = null; draw(); } }, "取消"),
        needsWrite && !canWrite
          ? h("button", { class: "btn-sm btn-sm-primary", type: "button", onClick: openWorkspace }, "切换权限")
          : h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: state.busy,
              onClick: rewind }, state.busy ? "正在回退…" : "时光回退"),
      ]),
    ]);
  }

  function draw() {
    if (!state.open) { render(null, root); return; }
    const mounted = Boolean(state.workspace?.workdir);
    const children = [
      h("header", { class: "checkpoint-head" }, [
        h("div", null, [h("div", { class: "modal-title" }, "时光回退"),
          h("div", { class: "checkpoint-sub" }, "撤销之前的文件修改和对话")]),
        h("button", { class: "job-history-close", type: "button",
          onClick: () => { state.open = false; draw(); } }, "×"),
      ]),
    ];
    if (state.loading) children.push(h("div", { class: "checkpoint-empty" }, "正在读取历史快照…"));
    else if (!mounted) children.push(h("div", { class: "checkpoint-empty" }, [
      h("p", null, state.error || "文件历史仅适用于主动连接的工作目录。"),
      h("button", { class: "btn-sm btn-sm-primary", type: "button", onClick: openWorkspace }, "连接工作目录"),
    ]));
    else {
      children.push(h("div", { class: "checkpoint-workspace" }, [
        h("span", null, `目录：${state.workspace.workdir}`),
        h("span", null, `权限：${state.workspace.permission === "read_write" ? "可读和编辑" : "只读"}`),
      ]));
      if (state.notice) children.push(h("div", { class: "checkpoint-notice" }, state.notice));
      if (state.error) children.push(h("div", { class: "checkpoint-error" }, state.error));
      children.push(h("div", { class: "checkpoint-list" }, state.snapshots.length
        ? state.snapshots.map(snapshotCard)
        : [h("div", { class: "checkpoint-empty" }, "还没有历史快照。发送一条消息后会自动建立快照。")]
      ));
      children.push(restorePanel());
    }
    render(h("div", { class: "overlay open", role: "dialog", "aria-modal": "true",
      onClick: event => { if (event.target === event.currentTarget) { state.open = false; draw(); } } }, [
      h("section", { class: "modal checkpoint-modal" }, children),
    ]), root);
  }

  async function open() {
    if (!hasRuntime) return;
    state.open = true; state.error = ""; state.notice = ""; state.restoreTarget = null;
    draw(); await load();
  }

  export { open, load };
