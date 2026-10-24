// 长期记忆 domain — extracted verbatim from app_settings.js.
// Functions carry over unchanged; closure refs `uiState`/`draw`/`state` resolve via ctx.
import { toast, _readLocalSetting, _writeLocalSetting, _renderPanelHead } from "./shared.js";

const Vue = window.Vue;

export function createMemoryDomain(ctx) {
  const { uiState, draw, state, appState, memoryEnabledKey } = ctx;

  function setMemoryEnabled(enabled) {
    appState.memoryEnabled = !!enabled;
    _writeLocalSetting(memoryEnabledKey, appState.memoryEnabled ? "1" : "0");
    if (uiState) {
      uiState.memoryEnabled = appState.memoryEnabled;
      if (!appState.memoryEnabled) {
        uiState.memoryRecords = [];
        uiState.memoryActivity = [];
      }
      draw();
    }
  }

  function _memorySid() {
    return state.SID
      || sessionStorage.getItem("baa_session_id")
      || _readLocalSetting("baa_session_id", "")
      || "";
  }
  function _memoryScopeUrl(path) {
    const sid = _memorySid();
    if (!sid) return path;
    const sep = path.includes("?") ? "&" : "?";
    return `${path}${sep}session_id=${encodeURIComponent(sid)}`;
  }
  async function loadMemory() {
    if (!uiState || uiState.memoryLoading) return;
    if (uiState.memoryEnabled === false) {
      uiState.memoryRecords = [];
      uiState.memoryActivity = [];
      uiState.memoryStatus = "";
      uiState.memoryStatusType = "ok";
      draw();
      return;
    }
    uiState.memoryLoading = true;
    uiState.memoryStatus = "正在加载记忆…";
    uiState.memoryStatusType = "ok";
    draw();
    try {
      const [r, activityResponse] = await Promise.all([
        fetch(_memoryScopeUrl("/api/memory")),
        fetch(_memoryScopeUrl("/api/memory-activity")),
      ]);
      const d = await r.json();
      const activityData = activityResponse.ok ? await activityResponse.json() : {};
      uiState.memoryRecords = Array.isArray(d.records) ? d.records : [];
      uiState.memoryWorkspaceMounted = !!d.workspace_mounted;
      uiState.memoryActivity = Array.isArray(activityData.activity) ? activityData.activity : [];
      uiState.memoryStatus = "";
    } catch (e) {
      uiState.memoryStatus = `加载失败：${e.message || e}`;
      uiState.memoryStatusType = "error";
    } finally {
      uiState.memoryLoading = false;
      draw();
    }
  }
  function _resetMemoryForm() {
    uiState.memoryForm = { name: "", scope: "user", title: "", body: "", why: "", how_to_apply: "" };
    uiState.memoryFormMsg = { err: "", ok: "" };
    uiState.memoryEditing = null;
    uiState.memoryFormOpen = false;
    uiState.memoryAdvancedOpen = false;
  }
  function openMemoryCreate(scope) {
    _resetMemoryForm();
    uiState.memoryForm.scope = scope || (uiState.memoryWorkspaceMounted ? "workspace" : "user");
    uiState.memoryFormOpen = true;
    draw();
  }
  function openMemoryEdit(record) {
    uiState.memoryEditing = record.name;
    uiState.memoryForm = {
      name: record.name || "",
      scope: record.scope || "user",
      title: record.title || "",
      body: record.body || "",
      why: record.why || "",
      how_to_apply: record.how_to_apply || "",
    };
    uiState.memoryFormMsg = { err: "", ok: "" };
    uiState.memoryAdvancedOpen = !!(record.why || record.how_to_apply);
    uiState.memoryFormOpen = true;
    draw();
  }
  function closeMemoryForm() {
    _resetMemoryForm();
    draw();
  }
  function _memoryScopeDisabled(scope) {
    return scope === "workspace" && !uiState.memoryWorkspaceMounted;
  }
  async function submitMemoryForm() {
    const form = uiState.memoryForm;
    if (!form.name.trim()) {
      uiState.memoryFormMsg = { err: "请填写记忆 ID（kebab-case）", ok: "" };
      draw();
      return;
    }
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(form.name.trim())) {
      uiState.memoryFormMsg = { err: "记忆 ID 必须为 kebab-case（小写字母/数字，连字符分隔）", ok: "" };
      draw();
      return;
    }
    if (_memoryScopeDisabled(form.scope)) {
      uiState.memoryFormMsg = { err: "当前未挂载工作区，无法保存工作区级记忆", ok: "" };
      draw();
      return;
    }
    const payload = {
      name: form.name.trim(),
      type: form.scope === "workspace" ? "project" : "user",
      title: form.title,
      body: form.body,
      why: form.why,
      how_to_apply: form.how_to_apply,
    };
    const editing = uiState.memoryEditing;
    const path = editing
      ? `/api/memory/${encodeURIComponent(editing)}`
      : "/api/memory";
    const method = editing ? "PUT" : "POST";
    try {
      const r = await fetch(_memoryScopeUrl(path), {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "保存失败");
      toast(editing ? "记忆已更新" : "记忆已创建");
      closeMemoryForm();
      await loadMemory();
    } catch (e) {
      uiState.memoryFormMsg = { err: String(e.message || e), ok: "" };
      draw();
    }
  }
  async function archiveMemory(name) {
    if (!window.BAA?.ui?.confirm) {
      // eslint-disable-next-line no-restricted-globals -- intentional native fallback when the Vue confirm island is unavailable
      if (!confirm(`确定归档记忆 "${name}"？归档后进入「设置 → 存储」的回收站，可随时恢复。`)) return;
    } else {
      const ok = await window.BAA.ui.confirm({
        title: "归档记忆",
        message: `确定归档 "${name}" 吗？归档后记忆将移入「设置 → 存储」的回收站，不再注入到后续对话；可在回收站中随时恢复。`,
        confirmText: "归档",
        cancelText: "取消",
      });
      if (!ok) return;
    }
    try {
      const r = await fetch(_memoryScopeUrl(`/api/memory/${encodeURIComponent(name)}`), {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "归档失败");
      toast("记忆已归档");
      await loadMemory();
    } catch (e) {
      toast(`归档失败：${e.message || e}`, "error");
    }
  }
  async function undoRememberedMemory(name) {
    try {
      const r = await fetch(_memoryScopeUrl(`/api/memory/${encodeURIComponent(name)}`), {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "撤销失败");
      toast("已撤销该自动记忆");
      await loadMemory();
    } catch (e) {
      toast(`撤销失败：${e.message || e}`, "error");
    }
  }

  function renderMemory() {
    const records = uiState.memoryRecords || [];
    const statusNode = uiState.memoryStatus
      ? Vue.h("div", { class: `app-hooks-status app-hooks-status-${uiState.memoryStatusType}` }, uiState.memoryStatus)
      : null;
    const formOpen = !!uiState.memoryFormOpen;
    const activity = uiState.memoryActivity || [];
    if (uiState.memoryEnabled === false) {
      return Vue.h("section", { class: "app-settings-panel memory-panel" }, [
        _renderPanelHead("长期记忆", "跨会话保存的用户偏好、反馈、项目事实与引用。", [
          Vue.h("button", {
            class: "btn-sm btn-sm-primary",
            type: "button",
            onClick: () => setMemoryEnabled(true),
          }, "重新开启"),
        ]),
        Vue.h("p", { class: "pf-hint" }, "记忆功能已关闭。可在「通用」设置中重新开启；开启后跨会话的用户偏好、纠正与口径结论会自动记录并注入。"),
      ]);
    }

    const scopeOptions = [
      { key: "user", label: "用户级", description: "所有工作区都生效", workspace: false },
      { key: "workspace", label: "工作区级", description: "仅当前工作区生效", workspace: true },
    ];

    const grouped = {
      user: records.filter(r => r.scope === "user"),
      workspace: records.filter(r => r.scope === "workspace"),
    };

    return Vue.h("section", { class: "app-settings-panel memory-panel" }, [
      _renderPanelHead("长期记忆", "跨会话保存的用户偏好、反馈、项目事实与引用。记忆会安全注入到后续对话的上下文。", [
        Vue.h("button", {
          class: "btn-sm btn-sm-ghost",
          type: "button",
          disabled: uiState.memoryLoading,
          onClick: loadMemory,
        }, "刷新"),
        Vue.h("button", {
          class: "btn-sm btn-sm-primary",
          type: "button",
          onClick: () => openMemoryCreate(),
        }, "新建记忆"),
      ]),
      statusNode,
      !uiState.memoryWorkspaceMounted
        ? Vue.h("p", { class: "pf-hint" }, "当前未挂载工作区，仅可管理用户级记忆。挂载工作区后可创建仅对该项目生效的记忆。")
        : null,

      // 分组：用户级 + 工作区级
      Vue.h("div", { class: "memory-group" }, [
        Vue.h("h4", null, "用户级"),
        grouped.user.length
          ? Vue.h("div", { class: "memory-list" }, grouped.user.map(r => _memoryCard(r)))
          : Vue.h("div", { class: "custom-empty" }, "暂无用户级记忆"),
      ]),
      Vue.h("div", { class: "memory-group" }, [
        Vue.h("h4", null, uiState.memoryWorkspaceMounted ? "工作区级" : "工作区级（未挂载）"),
        grouped.workspace.length
          ? Vue.h("div", { class: "memory-list" }, grouped.workspace.map(r => _memoryCard(r)))
          : Vue.h("div", { class: "custom-empty" }, "暂无工作区级记忆"),
      ]),
      Vue.h("div", { class: "memory-activity" }, [
        Vue.h("h4", null, "最近自动提取"),
        activity.length ? Vue.h("div", { class: "memory-activity-list" }, activity.map((item, index) => {
          const status = item.status || "unknown";
          const records = Array.isArray(item.records) ? item.records : [];
          return Vue.h("div", { class: `memory-activity-item ${status}`, key: `${item.ts || ""}-${index}` }, [
            Vue.h("div", { class: "memory-activity-copy" }, [
              Vue.h("strong", null, status === "saved" ? "已保存" : status === "skipped" ? "已跳过" : status === "rejected" ? "未写入" : "提取失败"),
              Vue.h("span", null, item.message || "自动提取已完成"),
              records.length ? Vue.h("small", null, records.map(record => `${record.scope === "workspace" ? "工作区级" : "用户级"}：${record.title || record.name}`).join("；")) : null,
            ]),
            records.length ? Vue.h("div", { class: "memory-activity-actions" }, records.map(record => Vue.h("button", {
              class: "btn-sm btn-sm-ghost", type: "button", onClick: () => undoRememberedMemory(record.name),
            }, "撤销"))) : null,
            Vue.h("time", { datetime: String(item.ts || "") }, _formatMemoryTime(item.ts)),
          ]);
        })) : Vue.h("div", { class: "custom-empty" }, "暂无自动提取记录"),
      ]),

      // 编辑/创建表单
      formOpen ? Vue.h("div", { class: "memory-form" }, [
        Vue.h("h4", null, uiState.memoryEditing ? `编辑：${uiState.memoryEditing}` : "新建记忆"),
        Vue.h("div", { class: "pf-row" }, [
          Vue.h("label", null, "ID (kebab-case)"),
          Vue.h("input", {
            type: "text",
            value: uiState.memoryForm.name,
            disabled: !!uiState.memoryEditing,
            onInput: e => { uiState.memoryForm.name = e.target.value; },
          }),
        ]),
        Vue.h("div", { class: "pf-row" }, [
          Vue.h("label", null, "保存范围"),
          Vue.h("select", {
            value: uiState.memoryForm.scope,
            onChange: e => { uiState.memoryForm.scope = e.target.value; draw(); },
          }, scopeOptions.map(o => Vue.h("option", {
            value: o.key,
            disabled: o.workspace && !uiState.memoryWorkspaceMounted,
          }, `${o.label} · ${o.description}`))),
          Vue.h("small", { class: "pf-hint" }, uiState.memoryForm.scope === "workspace"
            ? "适合数据口径、表字段规则和项目资料；仅当前工作区生效。"
            : "适合个人偏好和通用规则；在所有工作区生效。"),
        ]),
        Vue.h("div", { class: "pf-row" }, [
          Vue.h("label", null, "标题"),
          Vue.h("input", {
            type: "text",
            value: uiState.memoryForm.title,
            onInput: e => { uiState.memoryForm.title = e.target.value; },
          }),
        ]),
        Vue.h("div", { class: "pf-row" }, [
          Vue.h("label", null, "正文"),
          Vue.h("textarea", {
            rows: 4,
            value: uiState.memoryForm.body,
            onInput: e => { uiState.memoryForm.body = e.target.value; },
          }),
        ]),
        Vue.h("button", {
          class: "memory-advanced-toggle", type: "button",
          onClick: () => { uiState.memoryAdvancedOpen = !uiState.memoryAdvancedOpen; draw(); },
        }, uiState.memoryAdvancedOpen ? "收起高级选项" : "高级选项（原因、应用方式）"),
        uiState.memoryAdvancedOpen ? Vue.h("div", { class: "memory-advanced-fields" }, [
          Vue.h("div", { class: "pf-row" }, [
            Vue.h("label", null, "原因 (可选)"),
            Vue.h("input", { type: "text", value: uiState.memoryForm.why, onInput: e => { uiState.memoryForm.why = e.target.value; } }),
          ]),
          Vue.h("div", { class: "pf-row" }, [
            Vue.h("label", null, "应用方式 (可选)"),
            Vue.h("input", { type: "text", value: uiState.memoryForm.how_to_apply, onInput: e => { uiState.memoryForm.how_to_apply = e.target.value; } }),
          ]),
        ]) : null,
        uiState.memoryFormMsg.err
          ? Vue.h("div", { class: "app-hooks-status app-hooks-status-error" }, uiState.memoryFormMsg.err)
          : null,
        Vue.h("div", { class: "provider-actions" }, [
          Vue.h("button", {
            class: "btn-sm btn-sm-ghost",
            type: "button",
            onClick: closeMemoryForm,
          }, "取消"),
          Vue.h("button", {
            class: "btn-sm btn-sm-primary",
            type: "button",
            onClick: submitMemoryForm,
          }, uiState.memoryEditing ? "保存" : "创建"),
        ]),
      ]) : null,
    ]);
  }

  function _formatMemoryTime(value) {
    const raw = String(value || "")
      .replace(/(?:&#x20;|&#32;|&nbsp;)+/gi, " ")
      .trim();
    if (!raw) return "";
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return raw;
    const lang = window.getLang?.() === "en" ? "en" : "zh-CN";
    return new Intl.DateTimeFormat(lang, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(parsed);
  }

  function _memoryCard(r) {
    const scope = r.scope === "workspace" ? "workspace" : "user";
    const typeBadge = Vue.h("span", { class: `memory-type-badge scope-${scope}` }, scope === "workspace" ? "工作区级" : "用户级");
    const updatedRaw = r.updated_at || r.created_at || "";
    const updated = _formatMemoryTime(updatedRaw);
    return Vue.h("div", { class: "memory-card", key: r.name }, [
      Vue.h("div", { class: "memory-card-head" }, [
        typeBadge,
        Vue.h("strong", { class: "memory-title" }, r.title || r.name),
        Vue.h("time", { class: "memory-updated", datetime: String(updatedRaw || "") }, updated),
      ]),
      r.body ? Vue.h("p", { class: "memory-body" }, r.body.length > 160 ? r.body.slice(0, 160) + "…" : r.body) : null,
      r.why ? Vue.h("p", { class: "memory-why" }, `原因：${r.why}`) : null,
      r.how_to_apply ? Vue.h("p", { class: "memory-how" }, `应用：${r.how_to_apply}`) : null,
      Vue.h("div", { class: "memory-card-actions" }, [
        Vue.h("button", {
          class: "btn-sm btn-sm-ghost",
          type: "button",
          onClick: () => openMemoryEdit(r),
        }, "编辑"),
        Vue.h("button", {
          class: "btn-sm btn-sm-danger",
          type: "button",
          onClick: () => archiveMemory(r.name),
        }, "归档"),
      ]),
    ]);
  }

  return {
    setMemoryEnabled,
    loadMemory,
    renderMemory,
  };
}
