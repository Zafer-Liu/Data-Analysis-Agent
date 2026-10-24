// Hooks domain — extracted verbatim from app_settings.js.
// Functions carry over unchanged; closure refs `uiState`/`draw` resolve via ctx.
import { toast, _renderPanelHead } from "./shared.js";

const Vue = window.Vue;

export const DEFAULT_HOOKS_TEXT = JSON.stringify({
  enabled: true,
  allow_command_hooks: false,
  hooks: [],
}, null, 2);

const HOOK_EVENTS = [
  "startup",
  "session_start",
  "user_prompt_submit",
  "turn_start",
  "turn_end",
  "tool_call",
  "pre_tool_use",
  "post_tool_use",
  "permission_request",
  "subagent_start",
  "subagent_stop",
  "pre_compact",
  "post_compact",
  "stop",
  "error",
];

const BUILTIN_HOOK_TEMPLATES = [
  {
    id: "safe-sql",
    title: "破坏性 SQL 防护",
    detail: "拦截 query_data 中的 DROP、DELETE 与 UPDATE 请求。",
    hooks: [["safe-sql-drop", "DROP"], ["safe-sql-delete", "DELETE"], ["safe-sql-update", "UPDATE"]].map(([id, keyword]) => ({
      id, name: `破坏性 SQL 防护（${keyword}）`, enabled: true, event: "pre_tool_use",
      if: `tool == 'query_data' && args.sql contains '${keyword}'`, reject: true, once: false,
      action: { type: "prompt", message: `已拦截包含 ${keyword} 的数据查询请求。请改用只读 SELECT 查询；如确需修改数据，请通过受控工作流执行。` },
    })),
  },
  {
    id: "query-review", title: "查询结果复核", detail: "每次成功查询后，提醒 Agent 检查口径、空值与异常值。",
    hooks: [{ id: "query-result-review", name: "查询结果复核", enabled: true, event: "post_tool_use", if: "tool == 'query_data' && ok == true", action: { type: "prompt", message: "查询已成功。继续分析前，核对时间范围、统计口径、空值和异常值；回答中明确说明关键筛选条件与数据限制。" } }],
  },
  {
    id: "tool-recovery", title: "工具失败恢复", detail: "工具失败时引导 Agent 阅读错误并尝试最小范围的修复。",
    hooks: [{ id: "tool-error-recovery", name: "工具失败恢复", enabled: true, event: "post_tool_use", if: "ok == false", action: { type: "prompt", message: "刚才的工具调用失败。先根据错误信息定位参数、表名、SQL 或权限问题；优先用更小的只读查询验证，不要重复提交相同调用。" } }],
  },
  {
    id: "answer-quality", title: "结论质量检查", detail: "在每轮开始时注入交付检查：结论、数字依据、限制与下一步。",
    hooks: [{ id: "answer-quality-check", name: "结论质量检查", enabled: true, event: "turn_start", action: { type: "prompt", message: "本轮交付前检查：结论是否直接回答问题；关键数字是否有数据依据；不确定性或数据限制是否已说明；必要时给出下一步建议。" } }],
  },
];
const BUILTIN_HOOK_IDS = new Set(BUILTIN_HOOK_TEMPLATES.flatMap(template => template.hooks.map(hook => hook.id)));

export function createHooksDomain(ctx) {
  const { uiState, draw } = ctx;

  async function parseHooksJson() {
    try {
      return JSON.parse(uiState.hooksText || "{}");
    } catch (error) {
      uiState.hooksStatus = `JSON 格式错误：${error.message || error}`;
      uiState.hooksStatusType = "error";
      draw();
      return null;
    }
  }

  async function loadHooks() {
    if (!uiState) return;
    uiState.hooksLoading = true;
    uiState.hooksStatus = "";
    draw();
    try {
      const resp = await fetch("/api/hooks");
      const data = await resp.json();
      uiState.hooksText = JSON.stringify(data.settings || JSON.parse(DEFAULT_HOOKS_TEXT), null, 2);
      uiState.hooksRuntime = data.runtime || { enabled: false, active_hooks: [], enabled_count: 0, runnable_count: 0, pending_count: 0, configured_count: 0 };
      await loadHookHistory();
      uiState.hooksStatus = data.ok ? "Hooks 配置已加载。" : (data.error || "Hooks 配置存在错误。");
      uiState.hooksStatusType = data.ok ? "ok" : "error";
    } catch (error) {
      uiState.hooksStatus = `加载失败：${error.message || error}`;
      uiState.hooksStatusType = "error";
    } finally {
      uiState.hooksLoading = false;
      draw();
    }
  }

  async function validateHooks() {
    const raw = await parseHooksJson();
    if (!raw) return false;
    uiState.hooksLoading = true;
    draw();
    try {
      const resp = await fetch("/api/hooks/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(raw),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "校验失败");
      uiState.hooksText = JSON.stringify(data.settings || raw, null, 2);
      uiState.hooksStatus = "校验通过。";
      uiState.hooksStatusType = "ok";
      toast("Hooks 校验通过");
      return true;
    } catch (error) {
      uiState.hooksStatus = `校验失败：${error.message || error}`;
      uiState.hooksStatusType = "error";
      return false;
    } finally {
      uiState.hooksLoading = false;
      draw();
    }
  }

  async function saveHooks() {
    const raw = await parseHooksJson();
    if (!raw) return;
    uiState.hooksLoading = true;
    draw();
    try {
      const resp = await fetch("/api/hooks", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(raw),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "保存失败");
      uiState.hooksText = JSON.stringify(data.settings || raw, null, 2);
      await loadHooks();
      uiState.hooksStatus = "已保存，下一轮对话生效。";
      uiState.hooksStatusType = "ok";
      toast("Hooks 已保存");
    } catch (error) {
      uiState.hooksStatus = `保存失败：${error.message || error}`;
      uiState.hooksStatusType = "error";
    } finally {
      uiState.hooksLoading = false;
      draw();
    }
  }

  async function testHooks() {
    const raw = await parseHooksJson();
    if (!raw) return;
    uiState.hooksLoading = true;
    draw();
    try {
      const resp = await fetch("/api/hooks/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          event: uiState.testEvent || "turn_start",
          settings: raw,
          context: {
            session_id: "preview",
            turn_id: "preview-turn",
            tool_name: "query_data",
            tool_args: { sql: "SELECT 1" },
            message: "测试 Hooks",
          },
        }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "测试失败");
      uiState.hooksStatus = JSON.stringify(data, null, 2);
      uiState.hooksStatusType = "ok";
    } catch (error) {
      uiState.hooksStatus = `测试失败：${error.message || error}`;
      uiState.hooksStatusType = "error";
    } finally {
      uiState.hooksLoading = false;
      draw();
    }
  }

  async function refreshHookRuntime() {
    const resp = await fetch("/api/hooks");
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.ok) throw new Error(data.error || "读取 Hook 状态失败");
    uiState.hooksRuntime = data.runtime || uiState.hooksRuntime;
  }

  async function loadHookHistory() {
    const response = await fetch("/api/hooks/history?limit=50");
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      uiState.hookHistory = [];
      uiState.hookHistoryAvailable = false;
      return;
    }
    uiState.hookHistoryAvailable = true;
    uiState.hookHistory = data.items || [];
  }

  async function clearHookHistory() {
    if (!(await window.BAA.ui?.confirm?.({ title: "清空触发记录", message: "确定清空全部 Hook 触发记录吗？此操作不可恢复。", danger: true }))) return;
    uiState.hookHistoryLoading = true;
    draw();
    try {
      const response = await fetch("/api/hooks/history", { method: "DELETE" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || "清理失败");
      uiState.hookHistory = [];
      uiState.hooksStatus = `已清理 ${data.cleared || 0} 条触发记录。`;
      uiState.hooksStatusType = "ok";
    } catch (error) {
      uiState.hooksStatus = `清理失败：${error.message || error}`;
      uiState.hooksStatusType = "error";
    } finally {
      uiState.hookHistoryLoading = false;
      draw();
    }
  }

  async function clearHookHistoryFromStorage() {
    if (!(await window.BAA.ui?.confirm?.({ title: "清空触发记录", message: "确定清空全部 Hook 触发记录吗？此操作不可恢复。", danger: true }))) return;
    uiState.lifecycleStatus = "正在清理 Hook 触发记录…";
    draw();
    try {
      const response = await fetch("/api/hooks/history", { method: "DELETE" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || "清理失败");
      uiState.lifecycleHookHistory = [];
      uiState.hookHistory = [];
      uiState.lifecycleStatus = `已清理 ${data.cleared || 0} 条 Hook 触发记录。`;
    } catch (error) {
      uiState.lifecycleStatus = `清理失败：${error.message || error}`;
    } finally {
      draw();
    }
  }

  function isBuiltinTemplateEnabled(template) {
    const activeIds = new Set((uiState.hooksRuntime?.active_hooks || []).map(hook => hook.id));
    return template.hooks.every(hook => activeIds.has(hook.id));
  }

  async function setBuiltinHookTemplateEnabled(template, enabled) {
    if (uiState.hooksLoading) return;
    uiState.hooksLoading = true;
    draw();
    try {
      const response = await fetch("/api/hooks");
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok || !data.settings) throw new Error(data.error || "读取配置失败");
      const settings = data.settings;
      const hooks = Array.isArray(settings.hooks) ? settings.hooks : [];
      const byId = new Map(hooks.map(hook => [hook?.id, hook]));
      for (const hook of template.hooks) {
        const existing = byId.get(hook.id);
        byId.set(hook.id, existing ? { ...existing, name: existing.name || hook.name, enabled } : { ...hook, enabled });
      }
      if (enabled) settings.enabled = true;
      settings.hooks = [...byId.values()];
      const saveResponse = await fetch("/api/hooks", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings),
      });
      const saved = await saveResponse.json().catch(() => ({}));
      if (!saveResponse.ok || !saved.ok) throw new Error(saved.error || "启用失败");
      uiState.hooksText = JSON.stringify(saved.settings || settings, null, 2);
      await refreshHookRuntime();
      uiState.hooksStatus = enabled ? `已启用“${template.title}”。` : `已关闭“${template.title}”。`;
      uiState.hooksStatusType = "ok";
      toast(`${enabled ? "已启用" : "已关闭"}：${template.title}`);
    } catch (error) {
      uiState.hooksStatus = `${enabled ? "启用" : "关闭"}失败：${error.message || error}`;
      uiState.hooksStatusType = "error";
    } finally {
      uiState.hooksLoading = false;
      draw();
    }
  }

  async function setCustomHookEnabled(hook, enabled) {
    if (uiState.hooksLoading) return;
    uiState.hooksLoading = true;
    draw();
    try {
      const response = await fetch("/api/hooks");
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok || !data.settings) throw new Error(data.error || "读取配置失败");
      const target = (data.settings.hooks || []).find(item => item?.id === hook.id);
      if (!target) throw new Error("未找到该 Hook");
      target.enabled = enabled;
      if (enabled) data.settings.enabled = true;
      const saveResponse = await fetch("/api/hooks", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data.settings) });
      const saved = await saveResponse.json().catch(() => ({}));
      if (!saveResponse.ok || !saved.ok) throw new Error(saved.error || "保存失败");
      await loadHooks();
      uiState.hooksStatus = `已${enabled ? "启用" : "关闭"}“${hook.name || hook.id}”。`;
      uiState.hooksStatusType = "ok";
    } catch (error) {
      uiState.hooksStatus = `${enabled ? "启用" : "关闭"}失败：${error.message || error}`;
      uiState.hooksStatusType = "error";
    } finally {
      uiState.hooksLoading = false;
      draw();
    }
  }

  function addNamedCustomHook() {
    const name = String(uiState.customHookName || "").trim();
    if (!name) {
      uiState.hooksStatus = "请先填写 Hook 名称。";
      uiState.hooksStatusType = "error";
      draw();
      return;
    }
    try {
      const settings = JSON.parse(uiState.hooksText);
      if (!settings || typeof settings !== "object") throw new Error("配置必须是 JSON 对象");
      const hooks = Array.isArray(settings.hooks) ? settings.hooks : [];
      const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "custom-hook";
      const id = `${base}-${Date.now().toString(36)}`;
      hooks.push({
        id,
        name,
        enabled: true,
        event: "turn_start",
        action: { type: "prompt", message: "填写要在此 Hook 触发时注入给 Agent 的提示。" },
      });
      settings.hooks = hooks;
      uiState.hooksText = JSON.stringify(settings, null, 2);
      uiState.customHookName = "";
      uiState.hooksStatus = `已添加“${name}”，请补充规则后保存。`;
      uiState.hooksStatusType = "ok";
    } catch (error) {
      uiState.hooksStatus = `无法添加：${error.message || error}`;
      uiState.hooksStatusType = "error";
    }
    draw();
  }

  function customHookNameValue(hook) {
    return Object.prototype.hasOwnProperty.call(uiState.customHookNames, hook.id)
      ? uiState.customHookNames[hook.id]
      : (hook.name || "");
  }

  async function saveCustomHookName(hook) {
    const name = String(customHookNameValue(hook) || "").trim();
    if (!name) {
      uiState.hooksStatus = "请为自定义 Hook 填写名称。";
      uiState.hooksStatusType = "error";
      draw();
      return;
    }
    uiState.hooksLoading = true;
    draw();
    try {
      const response = await fetch("/api/hooks");
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok || !data.settings) throw new Error(data.error || "读取配置失败");
      const target = (data.settings.hooks || []).find(item => item?.id === hook.id);
      if (!target) throw new Error("未找到该 Hook");
      target.name = name;
      const saveResponse = await fetch("/api/hooks", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data.settings),
      });
      const saved = await saveResponse.json().catch(() => ({}));
      if (!saveResponse.ok || !saved.ok) throw new Error(saved.error || "保存失败");
      uiState.customHookNames[hook.id] = name;
      await loadHooks();
      await loadHookHistory();
      uiState.hooksStatus = `已将“${hook.id}”命名为“${name}”。`;
      uiState.hooksStatusType = "ok";
    } catch (error) {
      uiState.hooksStatus = `保存名称失败：${error.message || error}`;
      uiState.hooksStatusType = "error";
    } finally {
      uiState.hooksLoading = false;
      draw();
    }
  }

  // Renders the hooks runtime status (active hooks, internal endpoints, custom rules).
  // Currently superseded by renderHooks()/renderCustomHookRules()/renderInternalEventEndpoints()
  // but retained as a candidate for a future collapsible runtime panel.
  function _renderHookRuntime() {
    const runtime = uiState.hooksRuntime || {};
    const activeHooks = Array.isArray(runtime.active_hooks) ? runtime.active_hooks : [];
    const headline = runtime.enabled ? `已启用 ${runtime.enabled_count || 0} 条规则` : "Hooks 全局开关已关闭";
    return Vue.h("section", { class: "hooks-runtime", "aria-live": "polite" }, [
      Vue.h("div", { class: "hooks-runtime-head" }, [
        Vue.h("div", null, [
          Vue.h("strong", null, headline),
          Vue.h("span", null, runtime.enabled
            ? `其中 ${runtime.runnable_count || 0} 条可触发${runtime.pending_count ? `，${runtime.pending_count} 条等待事件接入` : ""}。`
            : `运行配置共 ${runtime.configured_count || 0} 项，保存并开启总开关后才会执行。`),
        ]),
        Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.hooksLoading, onClick: loadHooks }, "刷新状态"),
      ]),
      activeHooks.length
        ? Vue.h("div", { class: "hooks-runtime-list" }, activeHooks.map(hook =>
          Vue.h("div", { class: "hooks-runtime-row", key: hook.id }, [
            Vue.h("span", { class: "hooks-runtime-dot", "aria-hidden": "true" }),
            Vue.h("div", { class: "hooks-runtime-main" }, [
              Vue.h("strong", null, hook.name || hook.id),
              Vue.h("span", null, hook.condition || "无条件"),
            ]),
            Vue.h("span", { class: "hooks-runtime-event" }, `监听：${hook.event}`),
            Vue.h("span", { class: "hooks-runtime-action" }, `动作：${hook.action_type}`),
            hook.event_dispatched ? null : Vue.h("span", { class: "hooks-runtime-warn" }, "等待事件接入"),
          ])
        ))
        : Vue.h("p", { class: "hooks-runtime-empty" }, "暂无激活 Hook。可从下方模板添加，或在 JSON 中创建自定义规则。"),
    ]);
  }

  function renderInternalEventEndpoints() {
    const endpoints = Array.isArray(uiState.hooksRuntime?.internal_endpoints) ? uiState.hooksRuntime.internal_endpoints : [];
    return endpoints.length
      ? Vue.h("div", { class: "hooks-runtime-list" }, endpoints.map(endpoint =>
        Vue.h("div", { class: "hooks-runtime-row", key: endpoint.id }, [
          Vue.h("span", { class: "hooks-runtime-dot", "aria-hidden": "true" }),
          Vue.h("div", { class: "hooks-runtime-main" }, [
            Vue.h("strong", null, endpoint.name || endpoint.id),
            Vue.h("span", null, endpoint.condition || "无条件"),
          ]),
          Vue.h("span", { class: "hooks-runtime-event" }, `事件：${endpoint.event}`),
          Vue.h("span", { class: "hooks-runtime-action" }, `转发：${endpoint.action_type}`),
          endpoint.event_dispatched ? null : Vue.h("span", { class: "hooks-runtime-warn" }, "等待事件接入"),
        ])
      ))
      : Vue.h("p", { class: "hooks-runtime-empty" }, "暂无内部事件端点。" );
  }

  function renderBuiltinHookTemplates() {
    return Vue.h("section", { class: "hooks-templates" }, [
      Vue.h("div", { class: "hooks-templates-head" }, [
        Vue.h("strong", null, "内置 Hook"),
        Vue.h("span", null, "点击即可启用"),
      ]),
      Vue.h("div", { class: "hooks-template-list" }, BUILTIN_HOOK_TEMPLATES.map(template =>
        Vue.h("div", { class: "hooks-template-row", key: template.id }, [
          Vue.h("div", null, [Vue.h("strong", null, template.title), Vue.h("span", null, template.detail)]),
          Vue.h("button", {
            class: `btn-sm ${isBuiltinTemplateEnabled(template) ? "btn-sm-primary" : "btn-sm-ghost"}`,
            type: "button",
            disabled: uiState.hooksLoading,
            onClick: () => setBuiltinHookTemplateEnabled(template, !isBuiltinTemplateEnabled(template)),
          }, isBuiltinTemplateEnabled(template) ? "关闭" : "启用"),
        ])
      )),
    ]);
  }

  function renderCustomHookRules() {
    const hooks = (uiState.hooksRuntime?.configured_hooks || []).filter(hook => !BUILTIN_HOOK_IDS.has(hook.id));
    return Vue.h("section", { class: "hooks-custom-rules" }, [
      Vue.h("div", { class: "hooks-templates-head" }, [
        Vue.h("strong", null, "自定义 Hook"),
        Vue.h("span", null, "名称会用于触发记录"),
      ]),
      hooks.length ? Vue.h("div", { class: "hooks-template-list" }, hooks.map(hook =>
        Vue.h("div", { class: "hooks-template-row hooks-custom-rule-row", key: hook.id }, [
          Vue.h("div", { class: "hooks-custom-rule-info" }, [
            Vue.h("strong", null, hook.name || "未命名 Hook"),
            Vue.h("span", null, `ID：${hook.id} · ${hook.event} · ${hook.action_type}`),
            Vue.h("label", { class: "hooks-custom-rule-name" }, [
              Vue.h("span", null, "名称"),
              Vue.h("input", {
                type: "text", value: customHookNameValue(hook), placeholder: "填写显示名称",
                onInput: event => { uiState.customHookNames[hook.id] = event.target.value; },
              }),
              Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.hooksLoading, onClick: () => saveCustomHookName(hook) }, "保存名称"),
            ]),
          ]),
          Vue.h("button", { class: `btn-sm ${hook.enabled ? "btn-sm-primary" : "btn-sm-ghost"}`, type: "button", disabled: uiState.hooksLoading, onClick: () => setCustomHookEnabled(hook, !hook.enabled) }, hook.enabled ? "关闭" : "启用"),
        ])
      )) : Vue.h("p", { class: "hooks-runtime-empty" }, "暂无自定义 Hook。可点击“自定义 Hook”新建规则。"),
    ]);
  }

  function renderHookHistory() {
    const items = uiState.hookHistory || [];
    return Vue.h("details", { class: "hooks-history-disclosure" }, [
      Vue.h("summary", null, uiState.hookHistoryAvailable === false ? "Hook 触发记录（重启后可用）" : `Hook 触发记录（${items.length}）`),
      Vue.h("div", { class: "hooks-history-actions" }, [
        Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.hookHistoryLoading, onClick: async () => { await loadHookHistory(); draw(); } }, "刷新"),
        Vue.h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: uiState.hookHistoryLoading || !items.length, onClick: clearHookHistory }, "清理记录"),
      ]),
      uiState.hookHistoryAvailable === false
        ? Vue.h("p", { class: "hooks-runtime-empty" }, "当前后端尚未加载触发记录接口；重启应用后可查看和清理记录。")
        : items.length ? Vue.h("div", { class: "hooks-history-list" }, items.map((item, index) =>
        Vue.h("div", { class: `hooks-history-row ${item.ok ? "ok" : "failed"}`, key: `${item.at}-${item.hook_id}-${index}` }, [
          Vue.h("div", { class: "hooks-history-identity" }, [
            Vue.h("strong", null, item.hook_name || "未命名 Hook"),
            Vue.h("span", null, `ID：${item.hook_id}`),
            Vue.h("span", null, `${item.event} · ${item.action_type}`),
            item.configured === false ? Vue.h("span", { class: "hooks-history-retired" }, "已删除") : null,
          ]),
          Vue.h("time", null, item.at?.replace("T", " ").replace("+00:00", "Z") || ""),
          item.output ? Vue.h("small", null, item.output) : null,
        ])
      )) : Vue.h("p", { class: "hooks-runtime-empty" }, "暂无触发记录。"),
    ]);
  }

  function renderHooks() {
    const hint = "示例条件：tool == 'query_data' && args.sql contains 'DROP'";
    return Vue.h("section", { class: "app-settings-panel app-hooks-panel" }, [
      _renderPanelHead("Hooks", "工具调用前后的拦截规则与自定义逻辑。", [
        Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.hooksLoading, onClick: loadHooks }, "重新加载"),
        Vue.h("button", {
          class: "btn-sm btn-sm-primary", type: "button", disabled: uiState.hooksLoading,
          onClick: () => { uiState.customHookOpen = !uiState.customHookOpen; draw(); },
        }, uiState.customHookOpen ? "收起自定义 Hook" : "自定义 Hook"),
      ]),
      renderBuiltinHookTemplates(),
      renderCustomHookRules(),
      uiState.customHookOpen ? Vue.h("div", { class: "hooks-custom-editor" }, [
        Vue.h("div", { class: "hooks-custom-head" }, [
          Vue.h("div", null, [
            Vue.h("strong", null, "自定义 Hook"),
            Vue.h("span", null, "可组合事件、条件与动作；保存后在下一轮对话生效。"),
          ]),
          Vue.h("div", { class: "app-hooks-toolbar" }, [
            Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.hooksLoading, onClick: validateHooks }, "校验"),
            Vue.h("button", { class: "btn-sm btn-sm-primary", type: "button", disabled: uiState.hooksLoading, onClick: saveHooks }, "保存"),
          ]),
        ]),
        Vue.h("label", { class: "hooks-custom-name-field" }, [
          Vue.h("span", null, "Hook 名称"),
          Vue.h("input", {
            type: "text", value: uiState.customHookName, placeholder: "例如：回答语气规范",
            onInput: event => { uiState.customHookName = event.target.value; },
          }),
          Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", onClick: addNamedCustomHook }, "添加命名 Hook"),
        ]),
        Vue.h("p", { class: "app-hooks-hint" }, "每条自定义规则都应有 name；它会显示在 Hook 触发记录中。也可直接在 JSON 中补充 name。"),
        Vue.h("textarea", {
          class: "app-hooks-editor", spellcheck: "false", value: uiState.hooksText,
          onInput: event => { uiState.hooksText = event.target.value; },
        }),
        Vue.h("div", { class: "app-hooks-test-row" }, [
          Vue.h("select", {
            class: "app-hooks-select", value: uiState.testEvent,
            onChange: event => { uiState.testEvent = event.target.value; draw(); },
          }, HOOK_EVENTS.map(event => Vue.h("option", { value: event }, event))),
          Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.hooksLoading, onClick: testHooks }, "测试运行"),
          Vue.h("span", { class: "app-hooks-hint-inline" }, hint),
        ]),
      ]) : null,
      uiState.hooksStatus
        ? Vue.h("pre", { class: `app-hooks-status app-hooks-status-${uiState.hooksStatusType}` }, uiState.hooksStatus)
        : null,
      Vue.h("details", { class: "hooks-runtime-disclosure" }, [
        Vue.h("summary", null, "内部触发事件端点"),
        Vue.h("p", null, "这些端点只负责把内部事件转发给外部集成，不属于用户 Hook，也不会写入 Hook 触发记录。"),
        renderInternalEventEndpoints(),
      ]),
      renderHookHistory(),
    ]);
  }

  return {
    parseHooksJson,
    loadHooks,
    validateHooks,
    saveHooks,
    testHooks,
    refreshHookRuntime,
    loadHookHistory,
    clearHookHistory,
    clearHookHistoryFromStorage,
    renderHooks,
  };
}
