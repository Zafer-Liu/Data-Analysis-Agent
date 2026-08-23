// Compatibility application settings rendered as a Vue island.
import { state as appState, state } from "../core/runtime.js";
import { uiRegistry } from "../core/ui-registry.js";
import { chatStream } from "../features/chat-stream.js";

  const Vue = window.Vue;
  const root = document.getElementById("app-settings-root");
  const PROMPT_SUGGESTION_KEY = "baa_prompt_suggestion_enabled";
  const TEAMS_KEY = "baa_teams_enabled";
  const AUTO_MATCH_SKILL_KEY = "baa_auto_match_skill";
  const MEMORY_ENABLED_KEY = "baa_memory_enabled";

  const DEFAULT_HOOKS_TEXT = JSON.stringify({
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
  const LIFECYCLE_AUDIT_LABELS = {
    session_registered: "会话登记",
    session_soft_deleted: "会话软删除",
    session_trash_reclaimed: "回收站清理",
    session_trash_restored: "会话恢复",
    artifact_registered: "产物登记",
  };

  function _enabledFromStorage() {
    return localStorage.getItem(PROMPT_SUGGESTION_KEY) !== "0";
  }

  function _teamsEnabledFromStorage() {
    return localStorage.getItem(TEAMS_KEY) === "1";
  }

  function _autoMatchSkillFromStorage() {
    return localStorage.getItem(AUTO_MATCH_SKILL_KEY) !== "0";
  }

  function _memoryEnabledFromStorage() {
    return localStorage.getItem(MEMORY_ENABLED_KEY) !== "0";
  }

  function setPromptSuggestionEnabled(enabled) {
    appState.promptSuggestionEnabled = !!enabled;
    localStorage.setItem(PROMPT_SUGGESTION_KEY, appState.promptSuggestionEnabled ? "1" : "0");
    if (!appState.promptSuggestionEnabled) {
      chatStream.clearPromptSuggestion();
    }
    if (uiState) {
      uiState.promptSuggestionEnabled = appState.promptSuggestionEnabled;
      draw();
    }
  }

  function setTeamsEnabled(enabled) {
    appState.teamsEnabled = !!enabled;
    localStorage.setItem(TEAMS_KEY, appState.teamsEnabled ? "1" : "0");
    if (uiState) {
      uiState.teamsEnabled = appState.teamsEnabled;
      draw();
    }
  }

  function setAutoMatchSkill(enabled) {
    appState.autoMatchSkill = !!enabled;
    localStorage.setItem(AUTO_MATCH_SKILL_KEY, appState.autoMatchSkill ? "1" : "0");
    if (uiState) {
      uiState.autoMatchSkill = appState.autoMatchSkill;
      draw();
    }
  }

  function setMemoryEnabled(enabled) {
    appState.memoryEnabled = !!enabled;
    localStorage.setItem(MEMORY_ENABLED_KEY, appState.memoryEnabled ? "1" : "0");
    if (uiState) {
      uiState.memoryEnabled = appState.memoryEnabled;
      if (!appState.memoryEnabled) {
        uiState.memoryRecords = [];
        uiState.memoryActivity = [];
      }
      draw();
    }
  }

  let uiState = null;
  let draw = () => {};

  function toast(message, type = "") {
    uiRegistry.toast?.(message, type);
  }

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

  async function loadFeishuBot() {
    if (!uiState || uiState.feishuBotLoading) return;
    uiState.feishuBotLoading = true;
    uiState.feishuBotStatus = "";
    draw();
    try {
      const response = await fetch("/api/feishu-bot");
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || "读取飞书机器人配置失败");
      uiState.feishuBot = data.connection || uiState.feishuBot;
      if (uiState.feishuBot.app_secret_configured) loadFeishuChats();
    } catch (error) {
      uiState.feishuBotStatus = `加载失败：${error.message || error}`;
      uiState.feishuBotStatusType = "error";
    } finally {
      uiState.feishuBotLoading = false;
      draw();
    }
  }

  async function loadFeishuChats() {
    if (!uiState || uiState.feishuBotChatsLoading) return;
    uiState.feishuBotChatsLoading = true;
    uiState.feishuBotChatsStatus = "";
    draw();
    try {
      const response = await fetch("/api/feishu-bot/chats");
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || "读取群列表失败");
      uiState.feishuBotChats = Array.isArray(data.chats) ? data.chats : [];
      if (!uiState.feishuBotChats.length) {
        uiState.feishuBotChatsStatus = "未读取到机器人所在的群。请确认机器人已加入群、应用已开通“获取群组信息”权限并发布。";
      }
    } catch (error) {
      uiState.feishuBotChatsStatus = `读取群列表失败：${error.message || error}`;
    } finally {
      uiState.feishuBotChatsLoading = false;
      draw();
    }
  }

  async function saveFeishuBot() {
    if (!uiState || uiState.feishuBotLoading) return;
    uiState.feishuBotLoading = true;
    uiState.feishuBotStatus = "";
    draw();
    try {
      const response = await fetch("/api/feishu-bot", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: !!uiState.feishuBot.enabled,
          app_id: String(uiState.feishuBot.app_id || "").trim(),
          app_secret: String(uiState.feishuBotAppSecretDraft || "").trim(),
          event_verification_token: String(uiState.feishuBotVerificationTokenDraft || "").trim(),
          inbound_transport: uiState.feishuBot.inbound_transport || "long_connection",
          receive_id_type: uiState.feishuBot.receive_id_type || "chat_id",
          receive_id: String(uiState.feishuBot.receive_id || "").trim(),
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || "保存失败");
      uiState.feishuBot = data.connection || uiState.feishuBot;
      uiState.feishuBotAppSecretDraft = "";
      uiState.feishuBotVerificationTokenDraft = "";
      uiState.feishuBotStatus = uiState.feishuBot.configured
        ? "配置已保存。App Secret 已交由系统凭据库保护。"
        : "应用凭据已保存。请从下方群列表选择目标群，再保存一次。";
      uiState.feishuBotStatusType = "ok";
      toast("飞书机器人配置已保存");
      if (uiState.feishuBot.app_secret_configured) loadFeishuChats();
    } catch (error) {
      uiState.feishuBotStatus = `保存失败：${error.message || error}`;
      uiState.feishuBotStatusType = "error";
    } finally {
      uiState.feishuBotLoading = false;
      draw();
    }
  }

  async function testFeishuBot() {
    if (!uiState || uiState.feishuBotLoading) return;
    uiState.feishuBotLoading = true;
    uiState.feishuBotStatus = "";
    draw();
    try {
      const response = await fetch("/api/feishu-bot/test", { method: "POST" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || "测试发送失败");
      uiState.feishuBotStatus = data.message || "测试消息已发送到飞书群。";
      uiState.feishuBotStatusType = "ok";
      toast("飞书机器人连接成功");
    } catch (error) {
      uiState.feishuBotStatus = `测试失败：${error.message || error}`;
      uiState.feishuBotStatusType = "error";
    } finally {
      uiState.feishuBotLoading = false;
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
    if (!window.confirm("确定清空全部 Hook 触发记录吗？此操作不可恢复。")) return;
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
    if (!window.confirm("确定清空全部 Hook 触发记录吗？此操作不可恢复。")) return;
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

  function renderHookRuntime() {
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

  function renderSwitch(checked, onChange) {
    return Vue.h("span", { class: "app-setting-switch" }, [
      Vue.h("input", {
        type: "checkbox",
        checked,
        onChange: event => onChange(event.target.checked),
      }),
      Vue.h("span", { "aria-hidden": "true" }),
    ]);
  }


  async function downloadBgeModel() {
    uiState.bgeDownloading = true;
    uiState.bgeStatus = "";
    draw();
    try {
      const resp = await fetch("/api/system/bge-model/download", { method: "POST" });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "download failed");
      uiState.bgeInstalled = true;
      uiState.bgeNeural = !!data.neural_active;
      uiState.bgeStatus = data.neural_active
        ? ""
        : "download-done";
      uiState.bgeStatusType = "ok";
      toast("BGE \u6a21\u578b\u4e0b\u8f7d\u5b8c\u6210\uff0c\u8bed\u4e49\u68c0\u7d22\u5df2\u542f\u7528");
    } catch (e) {
      uiState.bgeStatus = String(e.message || e);
      uiState.bgeStatusType = "error";
    } finally {
      uiState.bgeDownloading = false;
      draw();
    }
  }

  function applyEmbedInfo(data) {
    uiState.embedMode = data.mode || uiState.embedMode || "auto";
    uiState.embedActive = data.active || "hash";
    uiState.embedDim = Number(data.dim || 384);
    uiState.embedModel = data.model || "";
    uiState.embedCloudUrl = data.cloud_url || "";
    uiState.embedCloudAvailable = !!data.cloud_available;
    uiState.embedCloudConfigured = !!data.cloud_configured;
    uiState.embedCloudStatus = data.cloud_status || (data.cloud_available ? "available" : "unavailable");
    uiState.embedLocalAvailable = !!data.local_available;
    if ("installed" in data) uiState.bgeInstalled = !!data.installed;
    uiState.bgeNeural = "neural_active" in data
      ? !!data.neural_active
      : uiState.embedActive !== "hash";
  }

  async function loadEmbedMode(probe = false) {
    if (!uiState) return;
    try {
      const resp = await fetch(`/api/system/embed-mode${probe ? "?probe=1" : ""}`);
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "嵌入模式加载失败");
      applyEmbedInfo(data);
      if (data.init_error) {
        uiState.bgeStatus = `模型初始化失败：${data.init_error}`;
        uiState.bgeStatusType = "error";
      }
    } catch (error) {
      uiState.bgeStatus = `模式加载失败：${error.message || error}`;
      uiState.bgeStatusType = "error";
    } finally {
      draw();
    }
  }

  async function loadCloudConfig() {
    if (!uiState) return;
    try {
      const resp = await fetch("/api/system/embed-cloud-config");
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "云端配置加载失败");
      uiState.cloudUrl = data.url || "";
      uiState.cloudModel = data.model || "bge-large-zh";
      uiState.cloudTokenConfigured = !!data.token_configured;
    } catch (error) {
      uiState.bgeStatus = `云端配置加载失败：${error.message || error}`;
      uiState.bgeStatusType = "error";
    } finally {
      draw();
    }
  }

  async function saveCloudConfig(test = false, clearToken = false) {
    if (!uiState || uiState.cloudSaving) return;
    const url = String(uiState.cloudUrl || "").trim();
    const model = String(uiState.cloudModel || "").trim();
    const token = String(uiState.cloudToken || "").trim();
    if (!url || !model) {
      uiState.bgeStatus = "请填写云端 URL 和模型名。";
      uiState.bgeStatusType = "error";
      draw();
      return;
    }
    if (test && !token && !uiState.cloudTokenConfigured && !clearToken) {
      uiState.bgeStatus = "测试连接前请填写 Bearer Token。";
      uiState.bgeStatusType = "error";
      draw();
      return;
    }
    uiState.cloudSaving = true;
    uiState.bgeStatus = test ? "正在保存并测试云端连接…" : "正在保存云端配置…";
    uiState.bgeStatusType = "ok";
    draw();
    try {
      const body = { url, model, test, clear_token: clearToken };
      if (token) body.token = token;
      const resp = await fetch("/api/system/embed-cloud-config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "云端配置保存失败");
      uiState.cloudUrl = data.url || url;
      uiState.cloudModel = data.model || model;
      uiState.cloudToken = "";
      uiState.cloudTokenConfigured = !!data.token_configured;
      uiState.embedCloudAvailable = !!data.test?.available;
      uiState.bgeStatus = data.test?.available
        ? `云端连接成功：${data.test.model}，${data.test.dim} 维。`
        : clearToken
          ? "云端凭据已清除。"
          : "云端配置已保存。";
      uiState.bgeStatusType = "ok";
      if (test) await loadEmbedMode();
      toast(data.test?.available ? "云端 Embedding 连接成功" : "云端配置已保存");
    } catch (error) {
      uiState.embedCloudAvailable = false;
      uiState.bgeStatus = `${test ? "连接测试" : "保存"}失败：${error.message || error}`;
      uiState.bgeStatusType = "error";
      // 测试失败后后端已把云端标记为不可用，刷新“当前运行”徽章避免仍显示云端。
      if (test) await loadEmbedMode();
    } finally {
      uiState.cloudSaving = false;
      draw();
    }
  }

  async function clearCloudToken() {
    if (!uiState?.cloudTokenConfigured) return;
    const accepted = await window.BAA.ui?.confirm?.({
      title: "清除云端凭据",
      message: "清除后云端 Embedding 将不可用，自动模式会降级到本地模型。",
      danger: true,
    });
    if (accepted) await saveCloudConfig(false, true);
  }

  async function checkEmbedStatus() {
    if (!uiState || uiState.embedChecking) return;
    uiState.embedChecking = true;
    uiState.bgeStatus = "正在检查后端状态…";
    uiState.bgeStatusType = "ok";
    draw();
    try {
      await loadEmbedMode(true);
      const s = uiState.embedCloudStatus;
      uiState.bgeStatus = uiState.embedCloudConfigured
        ? (s === "available" ? "状态已刷新：云端连接正常。" : "状态已刷新：云端不可达，已按实际后端显示。")
        : "状态已刷新。";
      uiState.bgeStatusType = uiState.embedCloudConfigured && s !== "available" ? "error" : "ok";
    } finally {
      uiState.embedChecking = false;
      draw();
    }
  }

  async function setEmbedMode(mode) {
    if (!uiState || uiState.embedSwitching) return;
    uiState.embedSwitching = true;
    uiState.bgeStatus = "正在切换嵌入模式…";
    uiState.bgeStatusType = "ok";
    draw();
    try {
      const resp = await fetch("/api/system/embed-mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "嵌入模式切换失败");
      applyEmbedInfo(data);
      const modeNames = { auto: "自动", cloud: "云端", local: "本地", hash: "基础" };
      const fallback = data.active === "hash" && mode !== "hash";
      uiState.bgeStatus = fallback
        ? `已选择${modeNames[mode] || mode}，但当前回退到基础匹配；请检查模型或云端连接。`
        : `已切换为${modeNames[mode] || mode}模式，请重建向量库以统一已有文档向量。`;
      uiState.bgeStatusType = fallback ? "error" : "ok";
      toast("嵌入模式已切换");
    } catch (error) {
      uiState.bgeStatus = `切换失败：${error.message || error}`;
      uiState.bgeStatusType = "error";
      await loadEmbedMode();
    } finally {
      uiState.embedSwitching = false;
      draw();
    }
  }

  async function rebuildEmbeddings() {
    if (!uiState || uiState.embedRebuilding) return;
    const accepted = await window.BAA.ui?.confirm?.({
      title: "重建知识库向量",
      message: "将检查文档、结构化知识和 Skill，仅重建内容或模型发生变化的向量。",
      danger: false,
    });
    if (!accepted) return;
    uiState.embedRebuilding = true;
    uiState.bgeStatus = "正在重建知识库向量…";
    uiState.bgeStatusType = "ok";
    draw();
    try {
      const resp = await fetch("/api/system/embed-rebuild", { method: "POST" });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "向量重建失败");
      uiState.bgeStatus = `向量缓存已同步：文档分块 ${Number(data.document_chunks || 0)}、结构化知识 ${Number(data.structured_records || 0)}、Skill ${Number(data.skills || 0)}。`;
      uiState.bgeStatusType = "ok";
      toast("知识库向量重建完成");
    } catch (error) {
      uiState.bgeStatus = `重建失败：${error.message || error}`;
      uiState.bgeStatusType = "error";
    } finally {
      uiState.embedRebuilding = false;
      draw();
    }
  }

  // Unified panel header for all settings tabs: title + description on the
  // left, action buttons on the right. Replaces the previously divergent
  // settings-primary-title / embed-settings-head / lifecycle-hero styles.
  function _renderPanelHead(title, description, actions) {
    return Vue.h("header", { class: "settings-panel-head" }, [
      Vue.h("div", null, [
        Vue.h("h3", null, title),
        description ? Vue.h("p", null, description) : null,
      ]),
      actions && actions.length
        ? Vue.h("div", { class: "settings-panel-head-actions" }, actions)
        : null,
    ]);
  }

  function renderGeneral() {
    return Vue.h("section", { class: "app-settings-panel" }, [
      _renderPanelHead("通用", "提示建议、团队协作与记忆功能的总开关。", null),
      Vue.h("label", { class: "app-setting-row" }, [
        Vue.h("span", { class: "app-setting-copy" }, [
          Vue.h("strong", null, "Prompt Suggestion"),
          Vue.h("span", null, "AI 回复完成后，在输入框中以浅色提示下一步可能要问的问题。"),
        ]),
        renderSwitch(uiState.promptSuggestionEnabled, setPromptSuggestionEnabled),
      ]),
      Vue.h("label", { class: "app-setting-row" }, [
        Vue.h("span", { class: "app-setting-copy" }, [
          Vue.h("strong", null, "Teams"),
          Vue.h("span", null, "开启后 Agent 会自动构建轻量分析团队并委派子任务，以更多 token 换取准确度和速度。默认关闭。"),
        ]),
        renderSwitch(uiState.teamsEnabled, setTeamsEnabled),
      ]),
      Vue.h("label", { class: "app-setting-row" }, [
        Vue.h("span", { class: "app-setting-copy" }, [
          Vue.h("strong", null, "自动匹配 Skill"),
          Vue.h("span", null, "开启后 Agent 会根据用户提问自动检索并激活匹配的分析 Skill（SWOT、漏斗等）。关闭后仅可通过 / 命令手动激活。默认开启。"),
        ]),
        renderSwitch(uiState.autoMatchSkill, setAutoMatchSkill),
      ]),
      Vue.h("label", { class: "app-setting-row" }, [
        Vue.h("span", { class: "app-setting-copy" }, [
          Vue.h("strong", null, "长期记忆"),
          Vue.h("span", null, "自动记录你的偏好、纠正与口径结论，并在新会话开始时注入。关闭后不再提取、注入与整理记忆。默认开启。"),
        ]),
        renderSwitch(uiState.memoryEnabled, setMemoryEnabled),
      ]),
    ]);
  }

  function renderGpu() {
    const gpu = uiState.gpuStatus;
    const kind = gpu ? gpu.kind : "none";
    const hasNvidia = kind === "nvidia";
    const hasDiscrete = kind === "nvidia" || kind === "discrete_wmi";
    const hasIntegrated = kind === "integrated";
    // 圆点：NVIDIA 独显=绿（online），集显=灰（offline），无=灰
    const dotClass = hasNvidia ? "online" : "offline";

    const gpuItems = [];
    if (gpu && gpu.gpus && gpu.gpus.length) {
      gpu.gpus.forEach(function (g) {
        if (hasNvidia) {
          // nvidia-smi 有详细显存/利用率
          gpuItems.push(Vue.h("div", { class: "gpu-item" }, [
            Vue.h("span", { class: "gpu-item-name" }, g.name),
            Vue.h("span", { class: "gpu-item-meta" },
              "显存 " + g.memory_used_mb + "/" + g.memory_total_mb + " MB · 利用率 " + g.utilization_pct + "%"),
          ]));
        } else {
          // WMI 只有型号（集显/discrete_wmi），无显存/利用率
          gpuItems.push(Vue.h("div", { class: "gpu-item" }, [
            Vue.h("span", { class: "gpu-item-name" }, g.name),
            Vue.h("span", { class: "gpu-item-meta" },
              g.kind === "integrated" ? "集显" : "独显"),
          ]));
        }
      });
    }
    // 无独显且有集显时不显示引导（已有型号列表）；纯无显卡时显示引导
    if (kind === "none" && !gpuItems.length) {
      gpuItems.push(Vue.h("div", { class: "gpu-hint" },
        "未检测到显卡。如需 GPU 推理与训练，可在后续「远程 GPU 连接」中配置 AutoDL 等远程服务器。"));
    } else if (hasIntegrated || (!hasNvidia && kind === "none" && gpuItems.length === 0)) {
      gpuItems.push(Vue.h("div", { class: "gpu-hint" },
        "本机无 NVIDIA 独显（不支持 CUDA）。如需 GPU 推理与训练，可在后续「远程 GPU 连接」中配置 AutoDL 等远程服务器。"));
    }

    const ollama = uiState.gpuOllama;
    const remoteConnections = (uiState.gpuConnections || []).map(function (connection) {
      const active = uiState.gpuConnectionStatus[connection.id] || {};
      const connected = !!active.connected;
      const models = uiState.gpuConnectionModels[connection.id] || [];
      return Vue.h("div", { class: "gpu-remote-item", key: connection.id }, [
        Vue.h("div", { class: "gpu-remote-copy" }, [
          Vue.h("strong", null, connection.name),
          Vue.h("span", null, connection.connection_type === "direct" ? connection.base_url : connection.username + "@" + connection.host + ":" + connection.port + " → " + connection.target_port),
          connected ? Vue.h("span", { class: "gpu-remote-online" }, "已连接") : Vue.h("span", { class: "gpu-remote-offline" }, "未连接"),
          connection.training_runner ? Vue.h("span", { class: connection.training_runner.runner_ready ? "gpu-remote-online" : "gpu-remote-offline" },
            connection.training_runner.runner_ready ? "远程训练器已就绪 · " + connection.training_runner.gpu_name : "远程训练器未预检") : null,
          models.length ? Vue.h("span", { class: "gpu-remote-models" }, "模型：" + models.join("、")) : null,
          models.length ? Vue.h("div", { class: "gpu-remote-model-actions" }, models.map(model => Vue.h("button", {
            class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.gpuRemoteBusy,
            onClick: () => registerGpuModel(connection.id, model),
          }, "注册 " + model)).concat(models.map(model => Vue.h("button", {
            class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.gpuRemoteBusy,
            onClick: () => testGpuModel(connection.id, model),
          }, "测试 " + model)))) : null,
        ]),
        Vue.h("div", { class: "gpu-remote-actions" }, [
          Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.gpuRemoteBusy,
            onClick: () => connected ? disconnectGpuConnection(connection.id) : connectGpuConnection(connection.id),
          }, connected ? "断开" : "连接"),
          connected ? Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.gpuRemoteBusy,
            onClick: () => discoverGpuModels(connection.id),
          }, "模型") : null,
          connected && connection.connection_type === "ssh" ? Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.gpuRemoteBusy,
            onClick: () => preflightTrainingRunner(connection.id),
          }, "训练预检") : null,
          Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.gpuRemoteBusy,
            onClick: () => deleteGpuConnection(connection.id),
          }, "删除"),
        ]),
      ]);
    });
    const form = uiState.gpuConnectionForm;
    return Vue.h("section", { class: "app-settings-panel" }, [
      _renderPanelHead("GPU 算力", "检测本机 GPU 状态，控制 GPU 算力用于推理与训练。", [
        Vue.h("button", {
          class: "btn-sm btn-sm-ghost",
          type: "button",
          disabled: uiState.gpuRefreshing || uiState.gpuBusy || uiState.gpuLoading,
          onClick: refreshGpuStatus,
        }, uiState.gpuRefreshing ? "检测中…" : "重新检测"),
      ]),
      // 开关行
      Vue.h("label", { class: "app-setting-row" }, [
        Vue.h("span", { class: "app-setting-copy" }, [
          Vue.h("strong", null, "启用 GPU 算力"),
          Vue.h("span", null, "开启后本地 GPU 优先用于推理与训练；关闭则使用云端 API 与 CPU。"),
        ]),
        renderSwitch(uiState.gpuEnabled, setGpuEnabled),
      ]),
      // 检测状态行
      Vue.h("div", { class: "gpu-status-row" }, [
        Vue.h("span", { class: "gpu-dot " + dotClass }),
        Vue.h("span", { class: "gpu-status-text" },
          uiState.gpuLoading ? "检测中…" : (gpu && gpu.message) || "尚未检测"),
      ]),
      // GPU 型号列表 + 引导
      ...gpuItems,
      // Ollama 状态
      ollama ? Vue.h("div", { class: "gpu-ollama-row" }, [
        Vue.h("span", { class: "gpu-dot " + (ollama.online ? "online" : "offline") }),
        ollama.online
          ? "本地 Ollama：在线 · " + (ollama.models || []).length + " 个模型"
          : "本地 Ollama：未运行（可安装 Ollama 后在 LLM 模型 tab 填写模型名）",
      ]) : null,
      Vue.h("div", { class: "gpu-remote-section" }, [
        Vue.h("div", { class: "gpu-remote-head" }, [
          Vue.h("div", null, [Vue.h("strong", null, "远程 GPU 连接"), Vue.h("span", null, "通过 SSH 隧道连接到远端 OpenAI 兼容服务。")]),
          Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.gpuRemoteBusy,
            onClick: loadGpuConnections,
          }, "刷新"),
        ]),
        ...(remoteConnections.length ? remoteConnections : [Vue.h("div", { class: "gpu-hint" }, "尚未添加远程连接。连接密码仅保存到系统凭据库，不会写入配置文件。")]),
        Vue.h("div", { class: "gpu-remote-form" }, [
          Vue.h("select", { value: form.connectionType, onChange: e => { form.connectionType = e.target.value; } }, [
            Vue.h("option", { value: "ssh" }, "SSH 隧道（推荐）"),
            Vue.h("option", { value: "direct" }, "公网 HTTPS 端点"),
          ]),
          Vue.h("input", { value: form.name, placeholder: "连接名称", onInput: e => { form.name = e.target.value; } }),
          form.connectionType === "direct" ? Vue.h("input", { value: form.baseUrl, placeholder: "https://gpu.example.com", onInput: e => { form.baseUrl = e.target.value; } }) : Vue.h("input", { value: form.host, placeholder: "SSH 主机", onInput: e => { form.host = e.target.value; } }),
          form.connectionType === "ssh" ? Vue.h("input", { value: form.username, placeholder: "SSH 用户名", onInput: e => { form.username = e.target.value; } }) : null,
          form.connectionType === "ssh" ? Vue.h("input", { value: form.targetPort, placeholder: "远端服务端口（如 8000）", inputmode: "numeric", onInput: e => { form.targetPort = e.target.value; } }) : null,
          form.connectionType === "ssh" ? Vue.h("select", { value: form.authMethod, onChange: e => { form.authMethod = e.target.value; } }, [
            Vue.h("option", { value: "agent" }, "SSH Agent / 默认密钥"),
            Vue.h("option", { value: "password" }, "密码（保存至系统凭据库）"),
          ]) : null,
          form.connectionType === "ssh" && form.authMethod === "password" ? Vue.h("input", { value: form.password, type: "password", autocomplete: "new-password", placeholder: "SSH 密码", onInput: e => { form.password = e.target.value; } }) : null,
          Vue.h("button", { class: "btn-sm btn-sm-primary", type: "button", disabled: uiState.gpuRemoteBusy,
            onClick: createGpuConnection,
          }, "添加安全连接"),
        ]),
        uiState.gpuRemoteMessage ? Vue.h("div", { class: "gpu-remote-message" }, uiState.gpuRemoteMessage) : null,
      ]),
    ]);
  }

  function renderModel() {
    const installed = uiState.bgeInstalled;
    const downloading = uiState.bgeDownloading;
    const switching = uiState.embedSwitching;
    const rebuilding = uiState.embedRebuilding;
    const cloudBusy = uiState.cloudSaving;
    const statusText = uiState.bgeStatus;
    const statusType = uiState.bgeStatusType;
    const mode = uiState.embedMode || "auto";
    const active = uiState.embedActive || "hash";
    const dim = uiState.embedDim || 384;
    const embedModel = uiState.embedModel || "";
    const cloudOk = uiState.embedCloudAvailable;
    const cloudConfigured = uiState.embedCloudConfigured;
    const cloudLabel = cloudOk ? "连接正常" : cloudConfigured ? "已配置" : "未连接";
    const localOk = uiState.embedLocalAvailable;

    const modeOptions = [
      { key: "auto", label: "自动", detail: "云端优先，失败后使用本地，再回退基础匹配" },
      { key: "cloud", label: "云端", detail: "强制使用 BGE-large-zh 1024维" },
      { key: "local", label: "本地", detail: "强制使用 BGE-small-zh 512维" },
      { key: "hash", label: "基础", detail: "关键词匹配，无需模型与网络；语义理解能力有限" },
    ];
    const activeLabel = active === "cloud" ? "云端" : active === "local" ? "本地" : "基础";
    const btnLabel = downloading ? "下载中…" : installed ? "重新下载" : "下载本地模型";
    const tokenPlaceholder = uiState.cloudTokenConfigured
      ? "已配置；留空将保留原 Token"
      : "输入 Bearer Token";

    return Vue.h("section", { class: "app-settings-panel model-settings-panel" }, [
      _renderPanelHead("知识库检索模型", "配置云端与本地 Embedding，并选择知识库和 Skill 检索使用的后端。", [
        Vue.h("div", { class: "embed-active-state", title: embedModel }, [
          Vue.h("span", null, "当前运行"),
          Vue.h("strong", null, `${activeLabel} · ${dim}维`),
        ]),
        Vue.h("button", {
          class: "btn-sm btn-sm-ghost embed-check-btn",
          type: "button",
          disabled: uiState.embedChecking,
          title: "手动探测云端连通性并刷新状态（不会自动轮询）",
          onClick: checkEmbedStatus,
        }, uiState.embedChecking ? "检查中…" : "检查状态"),
      ]),

      Vue.h("div", { class: "embed-provider-grid" }, [
        Vue.h("section", { class: "embed-provider-section" }, [
          Vue.h("div", { class: "embed-provider-head" }, [
            Vue.h("div", null, [
              Vue.h("strong", null, "本地模型"),
              Vue.h("span", null, "BGE-small-zh-v1.5 · 512维"),
            ]),
            Vue.h("span", { class: `bge-badge ${localOk ? "bge-badge-ok" : "bge-badge-warn"}` },
              localOk ? "可用" : "不可用"),
          ]),
          Vue.h("p", { class: "embed-provider-copy" }, "离线运行，无需网络；首次使用需下载约 91 MB 模型文件。"),
          Vue.h("button", {
            class: "btn-sm btn-sm-ghost embed-provider-action",
            type: "button",
            disabled: downloading,
            onClick: downloadBgeModel,
          }, btnLabel),
        ]),

        Vue.h("section", { class: "embed-provider-section embed-cloud-section" }, [
          Vue.h("div", { class: "embed-provider-head" }, [
            Vue.h("div", null, [
              Vue.h("strong", null, "云端服务"),
              Vue.h("span", null, "OpenAI 兼容 /v1/embeddings · 1024维"),
            ]),
            Vue.h("span", { class: `bge-badge ${cloudOk ? "bge-badge-ok" : "bge-badge-warn"}` },
              cloudLabel),
          ]),
          Vue.h("form", {
            class: "embed-cloud-form",
            onSubmit: event => { event.preventDefault(); saveCloudConfig(false); },
          }, [
            Vue.h("label", { class: "embed-config-field embed-config-field-wide" }, [
              Vue.h("span", null, "服务 URL"),
              Vue.h("input", {
                type: "url",
                value: uiState.cloudUrl,
                placeholder: "https://embed.example.com",
                onInput: event => { uiState.cloudUrl = event.target.value; },
              }),
            ]),
            Vue.h("label", { class: "embed-config-field" }, [
              Vue.h("span", null, "模型名"),
              Vue.h("input", {
                type: "text",
                value: uiState.cloudModel,
                placeholder: "bge-large-zh",
                onInput: event => { uiState.cloudModel = event.target.value; },
              }),
            ]),
            Vue.h("label", { class: "embed-config-field" }, [
              Vue.h("span", null, uiState.cloudTokenConfigured ? "Bearer Token · 已配置" : "Bearer Token"),
              Vue.h("input", {
                type: "password",
                autocomplete: "new-password",
                value: uiState.cloudToken,
                placeholder: tokenPlaceholder,
                onInput: event => { uiState.cloudToken = event.target.value; },
              }),
            ]),
            Vue.h("div", { class: "embed-cloud-actions" }, [
              uiState.cloudTokenConfigured ? Vue.h("button", {
                class: "btn-sm btn-sm-ghost embed-clear-credential",
                type: "button",
                disabled: cloudBusy,
                onClick: clearCloudToken,
              }, "清除凭据") : null,
              Vue.h("span", { class: "embed-actions-spacer" }),
              Vue.h("button", {
                class: "btn-sm btn-sm-ghost",
                type: "submit",
                disabled: cloudBusy,
              }, cloudBusy ? "处理中…" : "保存"),
              Vue.h("button", {
                class: "btn-sm btn-sm-primary",
                type: "button",
                disabled: cloudBusy,
                onClick: () => saveCloudConfig(true),
              }, cloudBusy ? "测试中…" : "保存并测试"),
            ]),
          ]),
        ]),
      ]),

      Vue.h("section", { class: "embed-mode-section" }, [
        Vue.h("div", { class: "embed-section-heading" }, [
          Vue.h("strong", null, "运行模式"),
          Vue.h("span", null, "切换后建议重建已有文档向量"),
        ]),
        Vue.h("div", { class: "embed-mode-control", role: "group", "aria-label": "嵌入模式" },
          modeOptions.map(option => Vue.h("button", {
            class: `embed-mode-option${mode === option.key ? " active" : ""}`,
            type: "button",
            disabled: switching,
            title: option.detail,
            onClick: () => setEmbedMode(option.key),
          }, [
            Vue.h("strong", null, option.label),
            Vue.h("span", null, option.key === "auto" ? "推荐" : option.key === "cloud" ? "1024维" : option.key === "local" ? "512维" : "384维"),
          ]))
        ),
        Vue.h("p", { class: "embed-mode-description" },
          modeOptions.find(option => option.key === mode)?.detail || ""),
        Vue.h("div", { class: "embed-capability-row" }, [
          Vue.h("span", { class: `bge-badge ${active !== "hash" ? "bge-badge-ok" : "bge-badge-warn"}` }, `${activeLabel} ${dim}维`),
          Vue.h("span", { class: `bge-badge ${cloudOk ? "bge-badge-ok" : "bge-badge-warn"}` }, cloudOk ? "云端可用" : uiState.embedCloudStatus === "configured" ? "云端未验证" : cloudConfigured ? "云端不可用" : "云端未配置"),
          Vue.h("span", { class: `bge-badge ${localOk ? "bge-badge-ok" : "bge-badge-warn"}` }, localOk ? "本地可用" : "本地不可用"),
        ]),
      ]),

      Vue.h("section", { class: "embed-rebuild-section" }, [
        Vue.h("div", { class: "embed-section-heading" }, [
          Vue.h("strong", null, "向量库"),
          Vue.h("span", null, "仅同步内容或模型发生变化的知识与 Skill"),
        ]),
        Vue.h("button", {
          class: "btn-sm btn-sm-ghost",
          type: "button",
          disabled: rebuilding,
          onClick: rebuildEmbeddings,
        }, rebuilding ? "重建中…" : "重建向量库"),
      ]),

      statusText
        ? Vue.h("div", { class: `embed-status-message embed-status-${statusType}`, role: "status" }, statusText)
        : null,
    ]);
  }
  function renderLlm() {
    /* Full interactive LLM config: built-in providers (expand/collapse, API key,
       save/test/clear) + custom model list + add-custom form.
       Mirrors the quick-settings modal (⚙ LLM模型) but rendered inside the
       app-settings panel, reusing the same /api/models endpoints. */

    /* ── trigger data load (once, with guard) ── */
    if (!uiState._llmLoading && !uiState._llmLoaded) {
      uiState._llmLoading = true;
      loadLlmData().then(function () {
        uiState._llmLoaded = true;
        uiState._llmLoading = false;
        draw();
      });
      // Fall through with whatever data we have (or empty) — draw() will be
      // called again after the data arrives.
    }

    const configs = state.modelConfigs || {};
    const llmDefaults = uiState.modelDefaults || {};  // loaded by loadLlmData()

    const BUILTIN_META = {
      deepseek:   { label: "DeepSeek",         icon: "/static/Images/icon.png" },
      openai:     { label: "OpenAI / ChatGPT", icon: "/static/Images/icon.png" },
      atlascloud: { label: "AtlasCloud",       icon: "/static/Images/icon.png" },
      ollama:     { label: "Ollama (本地)",     icon: "/static/Images/icon.png", local: true },
    };
    const BUILTIN_ORDER = ["deepseek", "openai", "atlascloud", "ollama"];

    /* ── LLM tab reactive state ────────────────────── */
    if (!uiState._llmProviders) {
      uiState._llmProviders = [];
      uiState._llmCustoms = [];
      uiState._llmFormOpen = false;
      uiState._llmEditingKey = null;
      uiState._llmForm = { name: "", url: "", model: "", key: "", ctx: "", output: "", inputPrice: "", outputPrice: "", think: false, budget: "8000" };
      uiState._llmFormMsg = { err: "", ok: "" };
      uiState._llmMsg = null;
    }

    /* ── build provider state from defaults + configs ── */
    var newProviders = BUILTIN_ORDER.map(function (key) {
      var meta = BUILTIN_META[key] || { label: key, icon: "/static/Images/icon.png" };
      var def = llmDefaults[key] || {};
      var cfg = configs[key] || {};
      var hasKey = !!cfg.has_api_key;
      var existing = (uiState._llmProviders || []).find(function (p) { return p.key === key; });
      var wasCleared = existing && existing.hasKey && !hasKey;
      return {
        key: key, label: meta.label, icon: meta.icon, local: !!meta.local,
        hasKey: hasKey, defaults: def, cfg: cfg,
        expanded: existing ? existing.expanded : false,
        busy: existing ? existing.busy : null,
        fields: (existing && !wasCleared) ? existing.fields : {
          apiKey: "", baseUrl: cfg.base_url || def.base_url || "",
          model: cfg.model || def.model || "",
          ctx: cfg.context_window != null ? String(cfg.context_window) : (def.context_window != null ? String(def.context_window) : ""),
          output: cfg.max_output_tokens != null ? String(cfg.max_output_tokens) : (def.max_output_tokens != null ? String(def.max_output_tokens) : ""),
          inputPrice: cfg.input_price_per_million != null ? String(cfg.input_price_per_million) : "",
          outputPrice: cfg.output_price_per_million != null ? String(cfg.output_price_per_million) : "",
          think: !!cfg.enable_thinking,
          budget: cfg.thinking_budget != null ? String(cfg.thinking_budget) : "8000",
        },
      };
    });
    uiState._llmProviders = newProviders;

    /* ── custom models ── */
    uiState._llmCustoms = Object.entries(configs)
      .filter(function (e) { return e[1].is_custom; })
      .map(function (e) { var key = e[0]; var c = e[1]; return { key: key, name: c.name || "", model: c.model || "", baseUrl: c.base_url || "" }; });

    /* ── render the panel ── */
    return Vue.h("section", { class: "app-settings-panel llm-settings-panel" }, [
      _renderPanelHead("LLM模型", "管理聊天、分析、团队协作使用的 LLM 后端。", [
        Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", onClick: function () { loadLlmData().then(draw); } }, "刷新"),
        Vue.h("button", { class: "btn-sm btn-sm-primary", type: "button", onClick: function () { _llmToggleForm(); draw(); } },
          uiState._llmFormOpen ? "收起添加表单" : "＋ 添加自定义模型"),
      ]),

      /* ── global status banner ── */
      uiState._llmMsg ? Vue.h("div", { class: "llm-global-msg " + (uiState._llmMsg.type || "ok") }, uiState._llmMsg.text) : null,

      /* ── built-in providers ── */
      Vue.h("section", { class: "settings-sec" }, [
        Vue.h("div", { class: "settings-sec-title" }, "内置模型提供商"),
        Vue.h("div", null, uiState._llmProviders.map(function (p) { return _llmProviderCard(p); })),
      ]),

      /* ── custom models ── */
      Vue.h("section", { class: "settings-sec" }, [
        Vue.h("div", { class: "settings-sec-title" }, "自定义模型"),
        uiState._llmCustoms.length
          ? Vue.h("div", null, uiState._llmCustoms.map(function (c) { return _llmCustomItem(c); }))
          : Vue.h("div", { class: "custom-empty" }, "暂无自定义模型"),
        _llmFormContent(),
      ]),
    ]);
  }

  /* ── LLM tab helper functions ──────────────────────── */

  function loadLlmData() {
    return Promise.all([
      fetch("/api/models").then(function (r) { return r.json(); }),
      fetch("/api/models/defaults").then(function (r) { return r.json(); }),
    ]).then(function (results) {
      state.modelConfigs = results[0];
      uiState.modelDefaults = results[1];
      return results;
    });
  }

  function _llmToggleForm() {
    uiState._llmFormOpen = !uiState._llmFormOpen;
    uiState._llmEditingKey = null;
    uiState._llmForm = { name: "", url: "", model: "", key: "", ctx: "", output: "", inputPrice: "", outputPrice: "", think: false, budget: "8000" };
    uiState._llmFormMsg = { err: "", ok: "" };
  }

  function _llmEditCustom(key) {
    var cfg = state.modelConfigs ? state.modelConfigs[key] : null;
    if (!cfg) return;
    uiState._llmEditingKey = key;
    uiState._llmForm = {
      name: cfg.name || "", url: cfg.base_url || "", model: cfg.model || "", key: "",
      ctx: cfg.context_window != null ? String(cfg.context_window) : "",
      output: cfg.max_output_tokens != null ? String(cfg.max_output_tokens) : "",
      inputPrice: cfg.input_price_per_million != null ? String(cfg.input_price_per_million) : "",
      outputPrice: cfg.output_price_per_million != null ? String(cfg.output_price_per_million) : "",
      think: !!cfg.enable_thinking,
      budget: cfg.thinking_budget != null ? String(cfg.thinking_budget) : "8000",
    };
    uiState._llmFormOpen = true;
    uiState._llmFormMsg = { err: "", ok: "" };
    draw();
  }

  function _llmFormContent() {
    if (!uiState._llmFormOpen) return null;
    var f = uiState._llmForm;
    var m = uiState._llmFormMsg;
    var editing = !!uiState._llmEditingKey;

    return Vue.h("div", { class: "add-custom-form show" }, [
      Vue.h("div", { class: "acf-section-title" }, editing ? "编辑自定义模型" : "添加自定义模型"),

      /* ── 基本信息 ── */
      Vue.h("div", { class: "acf-field" }, [
        Vue.h("label", null, "供应商名称"),
        Vue.h("input", { type: "text", placeholder: "例如 DeepSeek", value: f.name, onInput: function (e) { f.name = e.target.value; } }),
      ]),
      Vue.h("div", { class: "acf-field" }, [
        Vue.h("label", null, "API Base URL"),
        Vue.h("input", { type: "text", placeholder: "例如 https://api.deepseek.com", value: f.url, onInput: function (e) { f.url = e.target.value; } }),
      ]),
      Vue.h("div", { class: "acf-field" }, [
        Vue.h("label", null, "Model ID"),
        Vue.h("input", { type: "text", placeholder: "例如 deepseek-chat", value: f.model, onInput: function (e) { f.model = e.target.value; } }),
      ]),
      Vue.h("div", { class: "acf-field" }, [
        Vue.h("label", null, "API Key"),
        Vue.h("input", { type: "password", autocomplete: "off", "data-lpignore": "true", placeholder: editing ? "留空保留原 Key" : "输入 API Key", value: f.key, onInput: function (e) { f.key = e.target.value; } }),
      ]),

      /* ── 模型参数 ── */
      Vue.h("div", { class: "acf-section-title" }, "模型参数"),
      Vue.h("div", { class: "acf-row-2" }, [
        Vue.h("div", { class: "acf-field" }, [
          Vue.h("label", null, "上下文窗口"),
          Vue.h("input", { type: "number", placeholder: "1000000", value: f.ctx, onInput: function (e) { f.ctx = e.target.value; } }),
        ]),
        Vue.h("div", { class: "acf-field" }, [
          Vue.h("label", null, "最大输出"),
          Vue.h("input", { type: "number", placeholder: "384000", value: f.output, onInput: function (e) { f.output = e.target.value; } }),
        ]),
      ]),

      /* ── 价格（可选） ── */
      Vue.h("div", { class: "acf-section-title" }, "价格（可选）"),
      Vue.h("div", { class: "acf-row-2" }, [
        Vue.h("div", { class: "acf-field" }, [
          Vue.h("label", null, "输入价格"),
          Vue.h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", placeholder: "每百万 token 价格", value: f.inputPrice, onInput: function (e) { f.inputPrice = e.target.value; } }),
        ]),
        Vue.h("div", { class: "acf-field" }, [
          Vue.h("label", null, "输出价格"),
          Vue.h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", placeholder: "每百万 token 价格", value: f.outputPrice, onInput: function (e) { f.outputPrice = e.target.value; } }),
        ]),
      ]),

      /* ── 思考模式 ── */
      Vue.h("label", { class: "acf-check-row" }, [
        Vue.h("input", { type: "checkbox", checked: f.think, onChange: function (e) { f.think = e.target.checked; draw(); } }),
        Vue.h("span", null, "启用思考模式"),
      ]),
      f.think ? Vue.h("div", { class: "acf-field" }, [
        Vue.h("label", null, "思考预算（tokens）"),
        Vue.h("input", { type: "number", min: "1000", max: "100000", step: "1000", value: f.budget, onInput: function (e) { f.budget = e.target.value; } }),
      ]) : null,

      /* ── 消息 ── */
      m.err ? Vue.h("div", { class: "msg-err" }, m.err) : null,
      m.ok  ? Vue.h("div", { class: "msg-ok" }, m.ok)   : null,

      /* ── 按钮 ── */
      Vue.h("div", { class: "acf-actions" }, [
        Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", onClick: function () { _llmToggleForm(); draw(); } }, "取消"),
        Vue.h("button", { class: "btn-sm btn-sm-primary", type: "button", onClick: function () { _llmSubmitForm(); } }, editing ? "保存修改" : "添加模型"),
      ]),
    ]);
  }

  function _llmIsLocalUrl(url) {
    if (!url) return false;
    var u = String(url).toLowerCase();
    return ["localhost", "127.0.0.1", "0.0.0.0", "[::1]", "0:0:0:0:0:0:0:1"]
      .some(function (m) { return u.includes(m); });
  }

  function _llmProviderCard(p) {
    var isBusy = !!p.busy;
    var isExpanded = !!p.expanded;

    var header = Vue.h("div", {
      class: "provider-head",
      onClick: function () { p.expanded = !isExpanded; draw(); },
    }, [
      Vue.h("img", { class: "provider-icon", src: p.icon, alt: p.label }),
      Vue.h("span", { class: "provider-name" }, p.label),
      Vue.h("span", { class: "provider-status " + (p.hasKey ? "set" : "unset") },
        p.hasKey ? "已配置" : "未配置"),
      Vue.h("span", { class: "provider-toggle " + (isExpanded ? "open" : "") },
        isExpanded ? "▾" : "▸"),
    ]);

    if (!isExpanded) {
      return Vue.h("div", { class: "provider-card collapsed", key: p.key }, [ header ]);
    }

    var isLocal = !!(p.local) || _llmIsLocalUrl(p.fields.baseUrl);
    return Vue.h("div", { class: "provider-card expanded", key: p.key }, [
      header,
      Vue.h("div", { class: "provider-fields" }, [
        _llmPfRow("API Key",
          Vue.h("input", {
            type: isLocal ? "text" : "password", autocomplete: "off", "data-lpignore": "true",
            placeholder: isLocal ? "本地模型无需 API Key，可留空" : "输入 API Key",
            value: p.fields.apiKey,
            onInput: function (e) { p.fields.apiKey = e.target.value; },
          })
        ),
        _llmPfRow("Base URL",
          Vue.h("input", { type: "text", autocomplete: "off", placeholder: p.defaults.base_url, value: p.fields.baseUrl, onInput: function (e) { p.fields.baseUrl = e.target.value; } })
        ),
        _llmPfRow("模型",
          Vue.h("input", { type: "text", autocomplete: "off", placeholder: p.defaults.model, value: p.fields.model, onInput: function (e) { p.fields.model = e.target.value; } })
        ),
        _llmPfRow("上下文窗口",
          Vue.h("input", { type: "number", autocomplete: "off", placeholder: "默认 1000000", value: p.fields.ctx, onInput: function (e) { p.fields.ctx = e.target.value; } })
        ),
        _llmPfRow("最大输出",
          Vue.h("input", { type: "number", autocomplete: "off", placeholder: "默认 384000", value: p.fields.output, onInput: function (e) { p.fields.output = e.target.value; } })
        ),
        _llmPfRow("输入价格（可选）",
          Vue.h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", placeholder: "输入价格", value: p.fields.inputPrice, onInput: function (e) { p.fields.inputPrice = e.target.value; } })
        ),
        _llmPfRow("输出价格（可选）",
          Vue.h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", placeholder: "输出价格", value: p.fields.outputPrice, onInput: function (e) { p.fields.outputPrice = e.target.value; } })
        ),
        Vue.h("div", { class: "pf-row pf-row-left" }, [
          Vue.h("label", { style: "display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;color:#475569;width:auto;flex-shrink:0" }, [
            Vue.h("input", { type: "checkbox", checked: p.fields.think, onChange: function (e) { p.fields.think = e.target.checked; draw(); } }),
            "启用思考模式",
          ]),
        ]),
        p.fields.think ? Vue.h("div", { class: "pf-row", style: "align-items:center" }, [
          Vue.h("label", null, "思考预算（tokens）"),
          Vue.h("input", { type: "number", min: "1000", max: "100000", step: "1000", value: p.fields.budget, onInput: function (e) { p.fields.budget = e.target.value; } }),
        ]) : null,
      ]),
      Vue.h("div", { class: "provider-actions" }, [
        Vue.h("button", { class: "btn-sm btn-sm-danger", disabled: isBusy, onClick: function (e) { e.stopPropagation(); _llmClearBuiltin(p.key); } }, "清除"),
        Vue.h("button", { class: "btn-sm btn-sm-ghost", disabled: isBusy, onClick: function (e) { e.stopPropagation(); _llmTestModel(p.key); } },
          p.busy === "test" ? "测试中…" : "测试"),
        Vue.h("button", { class: "btn-sm btn-sm-primary", disabled: isBusy, onClick: function (e) { e.stopPropagation(); _llmSaveBuiltin(p.key); } },
          p.busy === "save" ? "保存中…" : "保存"),
      ]),
    ]);
  }

  function _llmPfRow(labelText, inputEl) {
    return Vue.h("div", { class: "pf-row" }, [
      Vue.h("label", null, labelText),
      inputEl,
    ]);
  }

  function _llmCustomItem(c) {
    return Vue.h("div", { class: "custom-item" }, [
      Vue.h("span", { class: "ci-name" }, c.name || c.model || c.key),
      Vue.h("span", { class: "ci-model" }, c.model || c.baseUrl || ""),
      Vue.h("button", { class: "btn-sm btn-sm-ghost", onClick: function () { _llmTestModel(c.key); } }, "测试"),
      Vue.h("button", { class: "btn-sm btn-sm-ghost", onClick: function () { _llmEditCustom(c.key); } }, "编辑"),
      Vue.h("button", { class: "btn-sm btn-sm-danger", onClick: function () { _llmDeleteCustom(c.key); } }, "删除"),
    ]);
  }

  /* ── LLM tab API actions ──────────────────────────── */

  function _llmSetProviderBusy(key, busy) {
    var p = uiState._llmProviders.find(function (x) { return x.key === key; });
    if (p) { p.busy = busy || null; }
    uiState._llmMsg = null;
    draw();
  }

  function _llmSetProviderMsg(key, type, text) {
    uiState._llmMsg = { type: type, text: text };
    draw();
  }

  function _llmSaveBuiltin(key) {
    var p = uiState._llmProviders.find(function (x) { return x.key === key; });
    if (!p) return;
    var f = p.fields;
    var apiKey = f.apiKey.trim();
    var isLocalProvider = key === "ollama" || _llmIsLocalUrl(f.baseUrl);
    if (!apiKey && !isLocalProvider) {
      _llmSetProviderMsg(key, "err", "请输入 API Key");
      return;
    }
    _llmSetProviderBusy(key, "save");
    _llmSetProviderMsg(key, "", "保存中…");
    var body = { provider: key, api_key: apiKey, base_url: f.baseUrl.trim(), model: f.model.trim(), enable_thinking: f.think, thinking_budget: f.budget ? parseInt(f.budget) : 8000 };
    if (f.ctx) body.context_window = parseInt(f.ctx);
    if (f.output) body.max_output_tokens = parseInt(f.output);
    body.input_price_per_million = f.inputPrice === "" ? null : Number(f.inputPrice);
    body.output_price_per_million = f.outputPrice === "" ? null : Number(f.outputPrice);
    fetch("/api/models/set-builtin", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        _llmSetProviderBusy(key, null);
        if (d.ok) {
          _llmSetProviderMsg(key, "ok", "保存成功");
          loadLlmData().then(function () {
            // Reset API key field after successful save
            var p2 = uiState._llmProviders.find(function (x) { return x.key === key; });
            if (p2) { p2.fields.apiKey = ""; }
            draw();
          });
        } else {
          _llmSetProviderMsg(key, "err", d.error || "保存失败");
        }
      })
      .catch(function (e) {
        _llmSetProviderBusy(key, null);
        _llmSetProviderMsg(key, "err", "网络错误: " + (e.message || "unknown"));
      });
  }

  function _llmClearBuiltin(key) {
    var label = (BUILTIN_META[key] || {}).label || key;
    if (!window.BAA.ui || !window.BAA.ui.confirm) {
      fetch("/api/models/clear", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: key }) })
        .then(function () { return loadLlmData(); }).then(draw);
      return;
    }
    window.BAA.ui.confirm({ title: "确认", message: "清除 " + label + " 的 API Key 和配置？此操作不可恢复。", danger: true })
      .then(function (ok) {
        if (!ok) return;
        return fetch("/api/models/clear", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: key }) })
          .then(function () { return loadLlmData(); }).then(draw);
      });
  }

  function _llmTestModel(key) {
    var p = uiState._llmProviders.find(function (x) { return x.key === key; });
    if (!p) {
      // Test a custom model
      fetch("/api/models/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: key }) })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.success) {
            uiState._llmMsg = { type: "ok", text: (d.model || key) + " 连接成功" };
          } else {
            uiState._llmMsg = { type: "err", text: d.message || d.error || "连接失败" };
          }
          draw();
        });
      return;
    }
    _llmSetProviderBusy(key, "test");
    fetch("/api/models/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: key, base_url: p.fields.baseUrl.trim(), model: p.fields.model.trim(), api_key: p.fields.apiKey.trim() }) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        _llmSetProviderBusy(key, null);
        if (d.success) {
          _llmSetProviderMsg(key, "ok", (d.model || key) + " 连接成功");
        } else {
          _llmSetProviderMsg(key, "err", d.message || d.error || "连接失败");
        }
      })
      .catch(function (e) {
        _llmSetProviderBusy(key, null);
        _llmSetProviderMsg(key, "err", "网络错误: " + (e.message || "unknown"));
      });
  }

  function _llmDeleteCustom(key) {
    if (!window.BAA.ui || !window.BAA.ui.confirm) {
      fetch("/api/models/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: key }) })
        .then(function () { return loadLlmData(); }).then(draw);
      return;
    }
    window.BAA.ui.confirm({ title: "确认", message: "删除自定义模型 " + key + "？此操作不可恢复。", danger: true })
      .then(function (ok) {
        if (!ok) return;
        return fetch("/api/models/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: key }) })
          .then(function () { return loadLlmData(); }).then(draw);
      });
  }

  function _llmSubmitForm() {
    var f = uiState._llmForm;
    var editingKey = uiState._llmEditingKey;
    var ctxRaw = (f.ctx || "").trim();
    var outRaw = (f.output || "").trim();
    var budgetRaw = (f.budget || "").trim();
    var inputPriceRaw = (f.inputPrice || "").trim();
    var outputPriceRaw = (f.outputPrice || "").trim();
    uiState._llmFormMsg = { err: "", ok: "" };

    if (editingKey) {
      var body = { provider: editingKey, base_url: f.url.trim(), model_name: f.model.trim(), api_key: f.key.trim(), enable_thinking: f.think, thinking_budget: budgetRaw ? parseInt(budgetRaw) : 8000 };
      if (ctxRaw) body.context_window = parseInt(ctxRaw);
      if (outRaw) body.max_output_tokens = parseInt(outRaw);
      body.input_price_per_million = inputPriceRaw === "" ? null : Number(inputPriceRaw);
      body.output_price_per_million = outputPriceRaw === "" ? null : Number(outputPriceRaw);
      fetch("/api/models/update", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.error) { uiState._llmFormMsg = { err: d.error, ok: "" }; draw(); return; }
          uiState._llmFormMsg = { err: "", ok: d.message || "保存成功" };
          uiState._llmEditingKey = null;
          loadLlmData().then(function () { draw(); });
          setTimeout(function () { uiState._llmFormOpen = false; draw(); }, 1200);
        });
      return;
    }

    var data = { name: f.name.trim(), base_url: f.url.trim(), model_name: f.model.trim(), api_key: f.key.trim(), enable_thinking: f.think, thinking_budget: budgetRaw ? parseInt(budgetRaw) : 8000 };
    if (ctxRaw) data.context_window = parseInt(ctxRaw);
    if (outRaw) data.max_output_tokens = parseInt(outRaw);
    if (inputPriceRaw || outputPriceRaw) {
      data.input_price_per_million = inputPriceRaw === "" ? null : Number(inputPriceRaw);
      data.output_price_per_million = outputPriceRaw === "" ? null : Number(outputPriceRaw);
    }
    fetch("/api/models/add", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { uiState._llmFormMsg = { err: d.error, ok: "" }; draw(); return; }
        uiState._llmFormMsg = { err: "", ok: d.message || "添加成功" };
        loadLlmData().then(function () { draw(); });
        setTimeout(function () { uiState._llmFormOpen = false; draw(); }, 1200);
      });
  }

  /* ── end LLM tab ─────────────────────────────────── */
  function renderFeishuBot() {
    const bot = uiState.feishuBot || {};
    const configured = !!bot.configured;
    const credentialsReady = !!bot.app_id && !!bot.app_secret_configured;
    const statusLabel = configured
      ? (bot.enabled ? "已连接，发送已启用" : "已连接，发送已暂停")
      : (credentialsReady ? "应用凭据已保存，待选择目标群" : "尚未配置");
    return Vue.h("section", { class: "app-settings-panel feishu-bot-panel" }, [
      _renderPanelHead("飞书渠道", "使用 App ID 与 App Secret 连接飞书应用机器人，将分析结论安全同步到协作群。", [
        Vue.h("button", {
          class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.feishuBotLoading,
          onClick: loadFeishuBot,
        }, "重新加载"),
        Vue.h("button", {
          class: "btn-sm btn-sm-primary", type: "button", disabled: uiState.feishuBotLoading,
          onClick: saveFeishuBot,
        }, uiState.feishuBotLoading ? "保存中…" : "保存配置"),
      ]),
      Vue.h("div", { class: `feishu-bot-connection ${configured ? "configured" : "empty"}`, "aria-live": "polite" }, [
        Vue.h("span", { class: "feishu-bot-connection-dot", "aria-hidden": "true" }),
        Vue.h("div", { class: "feishu-bot-connection-copy" }, [
          Vue.h("strong", null, statusLabel),
          Vue.h("span", null, configured
            ? `应用 ${bot.app_id_masked || "已配置"} · 目标 ${bot.receive_id_masked || "已配置"}`
            : "保存应用凭据与目标群 chat_id 后，可通过应用机器人发送消息。"),
        ]),
        Vue.h("button", {
          class: "btn-sm btn-sm-ghost", type: "button",
          disabled: uiState.feishuBotLoading || !configured,
          onClick: testFeishuBot,
        }, "测试连接"),
      ]),
      Vue.h("label", { class: "app-setting-row" }, [
        Vue.h("span", { class: "app-setting-copy" }, [
          Vue.h("strong", null, "允许发送分析结果"),
          Vue.h("span", null, "关闭后会保留连接信息，但项目不会向飞书群发送新的分析消息。"),
        ]),
        renderSwitch(!!bot.enabled, enabled => { uiState.feishuBot.enabled = enabled; draw(); }),
      ]),
      Vue.h("div", { class: "feishu-bot-form" }, [
        Vue.h("label", { for: "feishu-bot-app-id" }, "App ID"),
        Vue.h("input", {
          id: "feishu-bot-app-id", type: "text", autocomplete: "off", spellcheck: "false",
          value: uiState.feishuBot.app_id || "", placeholder: "cli_xxxxxxxxxxxxxxxx",
          onInput: event => { uiState.feishuBot.app_id = event.target.value; },
        }),
        Vue.h("label", { for: "feishu-bot-app-secret" }, "App Secret"),
        Vue.h("input", {
          id: "feishu-bot-app-secret", type: "password", autocomplete: "new-password", spellcheck: "false",
          value: uiState.feishuBotAppSecretDraft, placeholder: bot.app_secret_configured ? "已安全保存；留空可保留" : "输入 App Secret",
          onInput: event => { uiState.feishuBotAppSecretDraft = event.target.value; },
        }),
        Vue.h("p", null, bot.app_secret_configured
          ? "App Secret 已保存于系统凭据库，留空保存会保留当前凭据。"
          : "App Secret 只会保存到系统凭据库，不会写入项目配置或显示在页面中。"),
        Vue.h("label", { for: "feishu-bot-inbound-transport" }, "机器人入站方式"),
        Vue.h("select", {
          id: "feishu-bot-inbound-transport", value: bot.inbound_transport || "long_connection",
          onChange: event => { uiState.feishuBot.inbound_transport = event.target.value; draw(); },
        }, [
          Vue.h("option", { value: "long_connection" }, "长连接（本地优先，无需公网 URL）"),
          Vue.h("option", { value: "webhook" }, "Webhook（服务器部署备用）"),
        ]),
        bot.inbound_transport === "webhook" ? Vue.h("div", { class: "feishu-bot-webhook-fields" }, [
          Vue.h("label", { for: "feishu-bot-verification-token" }, "事件校验 Token"),
          Vue.h("input", {
            id: "feishu-bot-verification-token", type: "password", autocomplete: "new-password", spellcheck: "false",
            value: uiState.feishuBotVerificationTokenDraft,
            placeholder: bot.event_verification_token_configured ? "已安全保存；留空可保留" : "在飞书事件订阅中生成",
            onInput: event => { uiState.feishuBotVerificationTokenDraft = event.target.value; },
          }),
          Vue.h("p", null, bot.event_verification_token_configured
            ? "事件校验 Token 已保存于系统凭据库。事件回调路径为 /api/feishu-bot/events。"
            : "填写 Token，并把公开 HTTPS 地址 + /api/feishu-bot/events 填入飞书“事件与回调”。"),
        ]) : Vue.h("p", null, "本机主动连接飞书即可接收群内 @机器人 消息；在飞书后台选择“使用长连接接收事件”，无需填写公网回调地址。"),
        Vue.h("div", { class: "feishu-bot-target-head" }, [
          Vue.h("label", { for: "feishu-bot-chat" }, "目标群"),
          Vue.h("button", {
            class: "btn-sm btn-sm-ghost", type: "button",
            disabled: uiState.feishuBotLoading || uiState.feishuBotChatsLoading || !credentialsReady,
            onClick: loadFeishuChats,
          }, uiState.feishuBotChatsLoading ? "读取中…" : "刷新群列表"),
        ]),
        Vue.h("select", {
          id: "feishu-bot-chat", value: bot.receive_id || "",
          disabled: !credentialsReady || uiState.feishuBotChatsLoading,
          onChange: event => { uiState.feishuBot.receive_id_type = "chat_id"; uiState.feishuBot.receive_id = event.target.value; draw(); },
        }, [
          Vue.h("option", { value: "" }, credentialsReady ? "请选择机器人已加入的群" : "请先保存 App ID 与 App Secret"),
          ...(uiState.feishuBotChats || []).map(chat => Vue.h("option", { value: chat.chat_id, key: chat.chat_id }, chat.name)),
        ]),
        Vue.h("p", null, uiState.feishuBotChatsStatus || "不需要手动填写 chat_id；项目会读取应用机器人已加入的群。"),
      ]),
      Vue.h("details", { class: "feishu-bot-guide" }, [
        Vue.h("summary", null, "如何完成飞书应用机器人配置"),
        Vue.h("ol", null, [
          Vue.h("li", null, "在飞书开放平台创建或打开应用机器人，复制 App ID 与 App Secret。"),
          Vue.h("li", null, "开通发送消息和获取群组信息权限，并将该机器人加入目标飞书群。"),
          Vue.h("li", null, "保存凭据后，从自动读取的群列表选择目标群，再点击“测试连接”。"),
          Vue.h("li", null, "本地使用时，在“事件与回调”选择“使用长连接接收事件”并订阅“接收消息”；服务器可改为 Webhook，填写公开 HTTPS 地址 /api/feishu-bot/events 和事件校验 Token。"),
        ]),
      ]),
      uiState.feishuBotStatus
        ? Vue.h("div", { class: `app-hooks-status app-hooks-status-${uiState.feishuBotStatusType}`, role: "status" }, uiState.feishuBotStatus)
        : null,
    ]);
  }

  function renderBots() {
    const channel = uiState.botChannel || "feishu";
    return Vue.h("div", { class: "bots-settings-stack" }, [
      Vue.h("section", { class: "app-settings-panel bots-overview-panel" }, [
        _renderPanelHead("机器人", "集中管理协作平台机器人。每个渠道独立保存凭据、收发策略与会话连接。", null),
        Vue.h("div", { class: "bot-channel-tabs", role: "tablist", "aria-label": "机器人渠道" }, [
          Vue.h("button", {
            class: `bot-channel-tab${channel === "feishu" ? " active" : ""}`,
            type: "button", role: "tab", "aria-selected": String(channel === "feishu"),
            onClick: () => { uiState.botChannel = "feishu"; draw(); loadFeishuBot(); },
          }, [Vue.h("span", { "aria-hidden": "true" }, "飞"), Vue.h("span", null, "飞书")]),
          Vue.h("button", {
            class: "bot-channel-tab", type: "button", disabled: true,
            title: "微信渠道正在规划，尚未接入。",
          }, [Vue.h("span", { "aria-hidden": "true" }, "微"), Vue.h("span", null, "微信（规划中）")]),
        ]),
        Vue.h("p", { class: "bots-overview-hint" }, "当前可用：飞书应用机器人。后续接入微信时，不会影响已保存的飞书连接或会话绑定。"),
      ]),
      channel === "feishu" ? renderFeishuBot() : null,
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

  async function loadLifecycle() {
    if (!uiState || uiState.lifecycleLoading) return;
    uiState.lifecycleLoading = true;
    uiState.lifecycleStatus = "正在读取存储信息…";
    draw();
    try {
      const [settingsResponse, reportResponse, trashResponse, artifactTrashResponse, uploadTrashResponse, memoryTrashResponse, previewResponse, referencesResponse, uploadsResponse, workspaceResponse, auditResponse, hookHistoryResponse] = await Promise.all([
        fetch("/api/lifecycle/settings"),
        fetch("/api/lifecycle/report"),
        fetch("/api/lifecycle/session-trash"),
        fetch("/api/lifecycle/artifact-trash"),
        fetch("/api/lifecycle/upload-trash"),
        fetch("/api/lifecycle/memory-trash"),
        fetch("/api/lifecycle/artifacts/preview"),
        fetch("/api/lifecycle/artifacts/references/preview"),
        fetch("/api/lifecycle/uploads/preview"),
        fetch("/api/lifecycle/workspaces/preview"),
        fetch("/api/lifecycle/audit?limit=50"),
        fetch("/api/hooks/history?limit=50"),
      ]);
      const settingsData = await settingsResponse.json();
      const reportData = await reportResponse.json();
      const trashData = await trashResponse.json();
      const artifactTrashData = await artifactTrashResponse.json();
      const uploadTrashData = await uploadTrashResponse.json();
      const memoryTrashData = await memoryTrashResponse.json();
      const previewData = await previewResponse.json();
      const referencesData = await referencesResponse.json();
      const uploadsData = await uploadsResponse.json();
      const workspaceData = await workspaceResponse.json();
      const auditData = await auditResponse.json();
      const hookHistoryData = await hookHistoryResponse.json().catch(() => ({}));
      if (!settingsResponse.ok || !settingsData.ok) throw new Error(settingsData.error || "读取生命周期设置失败");
      if (!reportResponse.ok || !reportData.ok) throw new Error(reportData.error || "读取存储统计失败");
      if (!trashResponse.ok || !trashData.ok) throw new Error(trashData.error || "读取已归档对话失败");
      if (!artifactTrashResponse.ok || !artifactTrashData.ok) throw new Error(artifactTrashData.error || "读取产物回收站失败");
      if (!uploadTrashResponse.ok || !uploadTrashData.ok) throw new Error(uploadTrashData.error || "读取上传回收站失败");
      if (!memoryTrashResponse.ok || !memoryTrashData.ok) throw new Error(memoryTrashData.error || "读取记忆回收站失败");
      if (!previewResponse.ok || !previewData.ok) throw new Error(previewData.error || "读取产物扫描失败");
      if (!referencesResponse.ok || !referencesData.ok) throw new Error(referencesData.error || "读取产物引用失败");
      if (!uploadsResponse.ok || !uploadsData.ok) throw new Error(uploadsData.error || "读取上传分类失败");
      if (!workspaceResponse.ok || !workspaceData.ok) throw new Error(workspaceData.error || "读取工作区存储失败");
      if (!auditResponse.ok || !auditData.ok) throw new Error(auditData.error || "读取生命周期审计失败");
      uiState.lifecycleRetentionPreset = settingsData.settings?.retention_preset || uiState.lifecycleRetentionPreset;
      uiState.lifecycleRetentionCustomDays = settingsData.settings?.retention_custom_days ?? uiState.lifecycleRetentionCustomDays;
      uiState.lifecycleReport = reportData.report;
      uiState.lifecycleTrash = trashData.items || [];
      uiState.lifecycleArtifactTrash = artifactTrashData.items || [];
      uiState.lifecycleUploadTrash = uploadTrashData.items || [];
      uiState.lifecycleMemoryTrash = memoryTrashData.items || [];
      uiState.lifecyclePreview = previewData.preview || null;
      uiState.lifecycleReferencePreview = referencesData.preview || null;
      uiState.lifecycleUploadsPreview = uploadsData.preview || null;
      uiState.lifecycleWorkspacePreview = workspaceData.preview || null;
      uiState.lifecycleAudit = auditData.items || [];
      uiState.lifecycleHookHistory = hookHistoryResponse.ok && hookHistoryData.ok ? (hookHistoryData.items || []) : [];
      uiState.lifecycleStatus = "";
    } catch (error) {
      uiState.lifecycleStatus = `读取失败：${error.message || error}`;
    } finally {
      uiState.lifecycleLoading = false;
      draw();
    }
  }

  function lifecycleRetentionDaysValue() {
    if (!uiState) return 30;
    if (uiState.lifecycleRetentionPreset === "forever") return null;
    if (["7", "14"].includes(uiState.lifecycleRetentionPreset)) {
      return Number(uiState.lifecycleRetentionPreset);
    }
    return Number(uiState.lifecycleRetentionCustomDays);
  }

  async function saveLifecycleSettings() {
    if (!uiState) return;
    const response = await fetch("/api/lifecycle/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        retention_preset: uiState.lifecycleRetentionPreset,
        retention_custom_days: uiState.lifecycleRetentionCustomDays,
      }),
    });
    const data = await parseLifecycleResponse(response, "保存生命周期设置失败");
    uiState.lifecycleRetentionPreset = data.settings.retention_preset;
    uiState.lifecycleRetentionCustomDays = data.settings.retention_custom_days;
  }

  async function setLifecycleRetentionPreset(value) {
    if (!uiState) return;
    uiState.lifecycleRetentionPreset = value;
    uiState.lifecycleStatus = "正在保存保留策略…";
    draw();
    try {
      await saveLifecycleSettings();
      uiState.lifecycleStatus = "保留策略已保存。";
    } catch (error) {
      uiState.lifecycleStatus = `保存失败：${error.message || error}`;
    } finally {
      draw();
    }
  }

  async function saveLifecycleSettingsFromUi() {
    if (!uiState) return;
    uiState.lifecycleStatus = "正在保存保留策略…";
    draw();
    try {
      await saveLifecycleSettings();
      uiState.lifecycleStatus = "保留策略已保存。";
    } catch (error) {
      uiState.lifecycleStatus = `保存失败：${error.message || error}`;
    } finally {
      draw();
    }
  }

  async function restoreLifecycle(trashId) {
    if (!uiState) return;
    uiState.lifecycleStatus = "正在恢复会话…";
    draw();
    try {
      const response = await fetch(`/api/lifecycle/session-trash/${encodeURIComponent(trashId)}/restore`, { method: "POST" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "恢复失败");
      uiState.lifecycleStatus = `已恢复 ${data.summary.restored.length} 个会话文件。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `恢复失败：${error.message || error}`;
      draw();
    }
  }

  async function parseLifecycleResponse(response, fallbackMessage) {
    const text = await response.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      if (response.status === 404) {
        throw new Error("生命周期接口未加载，请重启应用后再试");
      }
      throw new Error(fallbackMessage || `接口返回异常：HTTP ${response.status}`);
    }
    if (!response.ok || !data.ok) {
      throw new Error(data.error || fallbackMessage || `请求失败：HTTP ${response.status}`);
    }
    return data;
  }

  async function recycleUnregisteredArtifact(item) {
    if (!uiState || uiState.lifecycleRecyclingKey) return;
    const filename = item.filename || "未命名产物";
    const accepted = await window.BAA.ui?.confirm?.({
      title: "移入产物回收站？",
      message: `将把历史产物「${filename}」移入受控回收站。它不会立即物理删除，但历史会话或报告里引用它时可能无法打开。`,
      danger: true,
    });
    if (!accepted) return;
    const key = `${item.type || ""}:${item.relative_path || filename}`;
    uiState.lifecycleRecyclingKey = key;
    uiState.lifecycleStatus = "正在移动历史产物…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/artifacts/unregistered/recycle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: item.type, relative_path: item.relative_path }),
      });
      const data = await parseLifecycleResponse(response, "移动历史产物失败");
      uiState.lifecycleStatus = `已将 ${data.summary.filename || filename} 移入产物回收站。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `移动失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleRecyclingKey = "";
      draw();
    }
  }

  async function recycleRegisteredArtifact(item) {
    if (!uiState || uiState.lifecycleRecyclingKey) return;
    const filename = item.filename || item.id || "已登记产物";
    const accepted = await window.BAA.ui?.confirm?.({
      title: "回收已登记产物？",
      message: `将把「${filename}」移入产物回收站。当前只允许未发现引用的 chart/export/report 候选，且不会立即物理删除。`,
      danger: true,
    });
    if (!accepted) return;
    const key = `registered:${item.id || filename}`;
    uiState.lifecycleRecyclingKey = key;
    uiState.lifecycleStatus = "正在移动已登记产物…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/artifacts/registered/recycle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ artifact_id: item.id }),
      });
      const data = await parseLifecycleResponse(response, "移动已登记产物失败");
      uiState.lifecycleStatus = `已将 ${data.summary.filename || filename} 移入产物回收站。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `移动失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleRecyclingKey = "";
      draw();
    }
  }

  async function recycleUploadCandidate(item) {
    if (!uiState || uiState.lifecycleRecyclingKey) return;
    const filename = item.filename || "上传文件";
    const category = item.category || "unknown_uploads";
    const accepted = await window.BAA.ui?.confirm?.({
      title: "移入上传回收站？",
      message: `将把「${filename}」移入上传回收站。仅允许未知上传或 Excel 解析缓存，知识库与已登记上传不会被处理。`,
      danger: true,
    });
    if (!accepted) return;
    const key = `upload:${item.relative_path || filename}`;
    uiState.lifecycleRecyclingKey = key;
    uiState.lifecycleStatus = "正在移动上传文件…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/uploads/recycle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category, relative_path: item.relative_path }),
      });
      const data = await parseLifecycleResponse(response, "移动上传文件失败");
      uiState.lifecycleStatus = `已将 ${data.summary.filename || filename} 移入上传回收站。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `移动失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleRecyclingKey = "";
      draw();
    }
  }

  async function restoreArtifactTrash(trashId) {
    if (!uiState || uiState.lifecycleArtifactBusyKey) return;
    uiState.lifecycleArtifactBusyKey = trashId;
    uiState.lifecycleStatus = "正在恢复产物…";
    draw();
    try {
      const response = await fetch(`/api/lifecycle/artifact-trash/${encodeURIComponent(trashId)}/restore`, { method: "POST" });
      const data = await parseLifecycleResponse(response, "恢复产物失败");
      uiState.lifecycleStatus = `已恢复 ${data.summary.restored.length} 个产物。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `恢复产物失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleArtifactBusyKey = "";
      draw();
    }
  }

  async function reclaimArtifactTrash() {
    if (!uiState || uiState.lifecycleArtifactReclaiming) return;
    const retentionDays = lifecycleRetentionDaysValue();
    if (retentionDays === null) {
      uiState.lifecycleStatus = "当前选择永久保留，产物回收站不会过期清理。";
      draw();
      return;
    }
    if (!Number.isInteger(retentionDays) || retentionDays < 0 || retentionDays > 3650) {
      uiState.lifecycleStatus = "自定义保留天数必须是 0 到 3650 的整数";
      draw();
      return;
    }
    const accepted = await window.BAA.ui?.confirm?.({
      title: "永久清理过期产物",
      message: `将永久清理已在产物回收站保留超过 ${retentionDays} 天的文件。此操作不可恢复。`,
      danger: true,
    });
    if (!accepted) return;
    uiState.lifecycleArtifactReclaiming = true;
    uiState.lifecycleStatus = "正在清理产物回收站…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/artifact-trash/reclaim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ retention_days: retentionDays }),
      });
      const data = await parseLifecycleResponse(response, "清理产物回收站失败");
      uiState.lifecycleStatus = `已清理 ${data.summary.groups || 0} 组、${data.summary.files || 0} 个产物文件。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `清理产物失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleArtifactReclaiming = false;
      draw();
    }
  }

  async function clearSessionTrash() {
    if (!uiState || uiState.lifecycleReclaiming) return;
    const accepted = await window.BAA.ui?.confirm?.({
      title: "永久删除全部已归档对话？",
      message: "将永久删除所有已归档对话及其文件（会话、图表、导出、上传）。此操作不可恢复。",
      danger: true,
    });
    if (!accepted) return;
    uiState.lifecycleReclaiming = true;
    uiState.lifecycleStatus = "正在删除已归档对话…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/session-trash/reclaim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ retention_days: 0 }),
      });
      const data = await parseLifecycleResponse(response, "删除已归档对话失败");
      uiState.lifecycleStatus = `已清空 ${data.summary.groups || 0} 组、${data.summary.files || 0} 个会话文件。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `清空会话失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleReclaiming = false;
      draw();
    }
  }

  async function clearArtifactTrash() {
    if (!uiState || uiState.lifecycleArtifactReclaiming) return;
    const accepted = await window.BAA.ui?.confirm?.({
      title: "清空产物回收站？",
      message: "将永久删除产物回收站中的所有项目，并同步清理已登记产物记录。此操作不可恢复。",
      danger: true,
    });
    if (!accepted) return;
    uiState.lifecycleArtifactReclaiming = true;
    uiState.lifecycleStatus = "正在清空产物回收站…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/artifact-trash/reclaim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ retention_days: 0 }),
      });
      const data = await parseLifecycleResponse(response, "清空产物回收站失败");
      uiState.lifecycleStatus = `已清空 ${data.summary.groups || 0} 组、${data.summary.files || 0} 个产物文件。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `清空产物失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleArtifactReclaiming = false;
      draw();
    }
  }

  async function restoreUploadTrash(trashId) {
    if (!uiState || uiState.lifecycleUploadBusyKey) return;
    uiState.lifecycleUploadBusyKey = trashId;
    uiState.lifecycleStatus = "正在恢复上传文件…";
    draw();
    try {
      const response = await fetch(`/api/lifecycle/upload-trash/${encodeURIComponent(trashId)}/restore`, { method: "POST" });
      const data = await parseLifecycleResponse(response, "恢复上传文件失败");
      uiState.lifecycleStatus = `已恢复 ${data.summary.restored.length} 个上传文件。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `恢复上传失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleUploadBusyKey = "";
      draw();
    }
  }

  async function clearUploadTrash() {
    if (!uiState || uiState.lifecycleUploadReclaiming) return;
    const accepted = await window.BAA.ui?.confirm?.({
      title: "清空上传回收站？",
      message: "将永久删除上传回收站中的所有项目。此操作不可恢复。",
      danger: true,
    });
    if (!accepted) return;
    uiState.lifecycleUploadReclaiming = true;
    uiState.lifecycleStatus = "正在清空上传回收站…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/upload-trash/reclaim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ retention_days: 0 }),
      });
      const data = await parseLifecycleResponse(response, "清空上传回收站失败");
      uiState.lifecycleStatus = `已清空 ${data.summary.groups || 0} 组、${data.summary.files || 0} 个上传文件。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `清空上传失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleUploadReclaiming = false;
      draw();
    }
  }

  async function restoreMemoryTrashFromStorage(trashId) {
    if (!uiState || uiState.lifecycleMemoryBusyKey) return;
    uiState.lifecycleMemoryBusyKey = trashId;
    uiState.lifecycleStatus = "正在恢复记忆…";
    draw();
    try {
      const response = await fetch(`/api/lifecycle/memory-trash/${encodeURIComponent(trashId)}/restore`, { method: "POST" });
      const data = await parseLifecycleResponse(response, "恢复记忆失败");
      uiState.lifecycleStatus = `已恢复记忆「${data.summary.name || data.summary.restored?.[0] || ""}」。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `恢复记忆失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleMemoryBusyKey = "";
      draw();
    }
  }

  async function clearMemoryTrash() {
    if (!uiState || uiState.lifecycleMemoryReclaiming) return;
    const accepted = await window.BAA.ui?.confirm?.({
      title: "清空记忆回收站？",
      message: "将永久删除记忆回收站中的所有归档记忆。此操作不可恢复。",
      danger: true,
    });
    if (!accepted) return;
    uiState.lifecycleMemoryReclaiming = true;
    uiState.lifecycleStatus = "正在清空记忆回收站…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/memory-trash/reclaim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ retention_days: 0 }),
      });
      const data = await parseLifecycleResponse(response, "清空记忆回收站失败");
      uiState.lifecycleStatus = `已清空 ${data.summary.groups || 0} 组、${data.summary.files || 0} 个记忆文件。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `清空记忆失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleMemoryReclaiming = false;
      draw();
    }
  }

  async function pruneMissingArtifacts() {
    if (!uiState || uiState.lifecycleLoading) return;
    const accepted = await window.BAA.ui?.confirm?.({
      title: "清理缺失记录？",
      message: "将移除已登记但文件已不存在的产物登记记录。仅清理登记信息，不影响任何现有文件。",
    });
    if (!accepted) return;
    uiState.lifecycleStatus = "正在清理缺失记录…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/artifacts/prune-missing", { method: "POST" });
      const data = await parseLifecycleResponse(response, "清理缺失记录失败");
      uiState.lifecycleStatus = `已清理 ${data.summary.removed || 0} 条缺失记录。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `清理缺失记录失败：${error.message || error}`;
      draw();
    }
  }

  function formatLifecycleBytes(value) {
    const bytes = Number(value) || 0;
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let size = bytes; let index = -1;
    do { size /= 1024; index += 1; } while (size >= 1024 && index < units.length - 1);
    return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[index]}`;
  }

  function lifecycleAuditLabel(event) {
    return LIFECYCLE_AUDIT_LABELS[event] || event || "unknown";
  }

  function lifecycleAuditDetails(item) {
    const details = [];
    if (item.session_id) details.push(`会话 ${item.session_id}`);
    if (item.artifact_id) details.push(`产物 ${item.artifact_id}`);
    if (item.type) details.push(`类型 ${item.type}`);
    if (item.workspace_id) details.push(`Workspace ${item.workspace_id}`);
    if (Number.isFinite(Number(item.size_bytes))) details.push(formatLifecycleBytes(item.size_bytes));
    if (Number.isFinite(Number(item.files))) details.push(`${item.files} 个文件`);
    if (Number.isFinite(Number(item.bytes))) details.push(formatLifecycleBytes(item.bytes));
    if (Number.isFinite(Number(item.groups))) details.push(`${item.groups} 组`);
    if (Number.isFinite(Number(item.retention_days))) details.push(`保留 ${item.retention_days} 天`);
    if (Array.isArray(item.deleted) && item.deleted.length) details.push(`删除 ${item.deleted.length} 项`);
    if (Array.isArray(item.restored) && item.restored.length) details.push(`恢复 ${item.restored.length} 项`);
    if (Array.isArray(item.failed) && item.failed.length) details.push(`失败 ${item.failed.length} 项`);
    return details.join(" · ");
  }

  function renderStorage() {
    const report = uiState.lifecycleReport || { locations: {}, total_files: 0, total_bytes: 0 };
    const items = uiState.lifecycleTrash || [];
    const artifactTrash = uiState.lifecycleArtifactTrash || [];
    const uploadTrash = uiState.lifecycleUploadTrash || [];
    const memoryTrash = uiState.lifecycleMemoryTrash || [];
    const preview = uiState.lifecyclePreview || { unknown_files: [], unknown_bytes: 0, missing_registered_ids: [] };
    const referencePreview = uiState.lifecycleReferencePreview || { registered: 0, referenced: 0, unreferenced: 0, missing: 0, unreferenced_samples: [], missing_samples: [], reference_sources: 0 };
    const unknownFiles = Array.isArray(preview.unknown_files) ? preview.unknown_files : [];
    const missingRegistered = Array.isArray(preview.missing_registered_ids) ? preview.missing_registered_ids : [];
    const unreferencedRegistered = Array.isArray(referencePreview.unreferenced_samples) ? referencePreview.unreferenced_samples : [];
    const missingRegisteredSamples = Array.isArray(referencePreview.missing_samples) ? referencePreview.missing_samples : [];
    const uploadsPreview = uiState.lifecycleUploadsPreview || { categories: {}, samples: [], cache_samples: [], missing_registered_upload_ids: [] };
    const uploadCategories = uploadsPreview.categories || {};
    const unknownUploadSamples = Array.isArray(uploadsPreview.samples) ? uploadsPreview.samples : [];
    const cacheUploadSamples = Array.isArray(uploadsPreview.cache_samples) ? uploadsPreview.cache_samples : [];
    const workspacePreview = uiState.lifecycleWorkspacePreview || { workspaces: [], total_bytes: 0 };
    const workspaceItems = Array.isArray(workspacePreview.workspaces) ? workspacePreview.workspaces : [];
    const auditAll = Array.isArray(uiState.lifecycleAudit) ? uiState.lifecycleAudit : [];
    const audit = uiState.lifecycleAuditFilter === "all" ? auditAll : auditAll.filter(item => String(item.event || "").includes(uiState.lifecycleAuditFilter));
    const locations = report.locations || {};
    const retentionDays = lifecycleRetentionDaysValue();
    const retentionForever = retentionDays === null;
    const customRetentionInvalid = !retentionForever && (!Number.isInteger(retentionDays) || retentionDays < 0 || retentionDays > 3650);
    const isExpiredTrash = item => {
      if (retentionForever || customRetentionInvalid) return false;
      const deletedAt = Date.parse(item.deleted_at || "");
      if (!Number.isFinite(deletedAt)) return false;
      return Date.now() - deletedAt >= retentionDays * 86400000;
    };
    const expiredArtifactTrash = artifactTrash.filter(isExpiredTrash);
    const expiredArtifactTrashBytes = expiredArtifactTrash.reduce((total, item) => total + Number(item.bytes || 0), 0);
    const trashBytes = items.reduce((total, item) => total + Number(item.bytes || 0), 0);
    const artifactTrashBytes = artifactTrash.reduce((total, item) => total + Number(item.bytes || 0), 0);
    const uploadTrashBytes = uploadTrash.reduce((total, item) => total + Number(item.bytes || 0), 0);
    const memoryTrashBytes = memoryTrash.reduce((total, item) => total + Number(item.bytes || 0), 0);
    const recycleTotalBytes = artifactTrashBytes + uploadTrashBytes + memoryTrashBytes;
    const metricCards = [
      { label: "目录总占用", value: formatLifecycleBytes(report.total_bytes), note: `${report.total_files || 0} 个文件 · 不是可清理量`, tone: "primary" },
      { label: "已归档对话", value: formatLifecycleBytes(trashBytes), note: `${items.length} 组 · 手动管理，不受保留策略影响`, tone: items.length ? "warn" : "muted" },
      { label: "回收站总量", value: formatLifecycleBytes(recycleTotalBytes), note: `产物 ${artifactTrash.length} · 上传 ${uploadTrash.length} · 记忆 ${memoryTrash.length} 项`, tone: recycleTotalBytes ? "warn" : "muted" },
    ];

    const section = (title, description, actions, children, extraClass = "") => Vue.h("section", { class: `lifecycle-card ${extraClass}` }, [
      Vue.h("div", { class: "lifecycle-section-heading" }, [
        Vue.h("div", null, [
          Vue.h("div", { class: "app-settings-section-title" }, title),
          description ? Vue.h("p", { class: "lifecycle-copy lifecycle-card-copy" }, description) : null,
        ]),
        actions ? Vue.h("div", { class: "lifecycle-preview-actions" }, Array.isArray(actions) ? actions : [actions]) : null,
      ]),
      ...(Array.isArray(children) ? children : []),
    ]);

    const previewList = (caption, rows, renderRow, emptyText = "暂无数据") => rows.length
      ? Vue.h("div", { class: "lifecycle-preview-list" }, [
        Vue.h("div", { class: "lifecycle-list-caption" }, caption),
        ...rows.map(renderRow),
      ])
      : Vue.h("div", { class: "lifecycle-empty lifecycle-empty-compact" }, emptyText);

    const uploadCandidateRow = (item, index) => {
      const key = `upload:${item.relative_path || item.filename || index}`;
      const recycling = uiState.lifecycleRecyclingKey === key;
      return Vue.h("div", { class: "lifecycle-preview-item", key }, [
        Vue.h("span", { title: item.relative_path || item.filename || "" }, item.filename || "上传文件"),
        Vue.h("div", { class: "lifecycle-preview-actions" }, [
          Vue.h("small", null, formatLifecycleBytes(item.size_bytes)),
          Vue.h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: recycling || uiState.lifecycleLoading, onClick: () => recycleUploadCandidate(item) }, recycling ? "移动中…" : "删除"),
        ]),
      ]);
    };

    const recycleBinRow = (item, restoreFn, busyKey, labelFallback) => Vue.h("div", { class: "lifecycle-trash-item", key: item.id }, [
      Vue.h("div", null, [
        Vue.h("strong", null, item.filename || item.source_filename || labelFallback),
        Vue.h("small", null, `${item.deleted_at || ""} · ${item.category || item.type || `${item.files || 0} 个`} · ${formatLifecycleBytes(item.bytes)} · ${isExpiredTrash(item) ? "已过期" : "可恢复"}`),
      ]),
      Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: busyKey === item.id, onClick: () => restoreFn(item.id) }, busyKey === item.id ? "恢复中…" : "恢复"),
    ]);

    const recycleBin = (title, rows, clearAction, clearBusy, restoreFn, busyKey, labelFallback, extraActions = []) => section(
      title,
      "可恢复；一键删除会永久清空该回收站。",
      [
        ...extraActions,
        Vue.h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: clearBusy || !rows.length, title: rows.length ? `永久删除${title}全部项目` : `${title}为空`, onClick: clearAction }, "一键删除"),
      ],
      [rows.length ? Vue.h("div", { class: "lifecycle-trash-list" }, rows.map(item => recycleBinRow(item, restoreFn, busyKey, labelFallback))) : Vue.h("div", { class: "lifecycle-empty" }, `${title}为空`)],
      "lifecycle-card-recycle",
    );

    const protectedRows = [
      ...Object.entries(locations).map(([name, value]) => [name, `${value.files || 0} 个 · ${formatLifecycleBytes(value.bytes)}`]),
      ...workspaceItems.map(item => [item.name, item.db_exists ? `${formatLifecycleBytes(item.db_bytes)} · ${item.active_lease_count ? "任务使用中" : "已保护"}` : "未发现 DuckDB 文件"]),
    ];

    const uploadCategoryRows = [
      ["registered_uploads", "已登记上传"],
      ["knowledge", "知识库数据"],
      ["parsed_excel_cache", "Excel 解析缓存"],
      ["unknown_uploads", "未知上传"],
    ];

    return Vue.h("section", { class: "app-settings-panel lifecycle-panel lifecycle-redesigned" }, [
      _renderPanelHead("存储", "本地文件分为可回收、需确认、受保护三类；回收站可恢复，永久删除必须手动确认。", [
        Vue.h("button", { class: "btn-sm btn-sm-primary", type: "button", disabled: uiState.lifecycleLoading, onClick: loadLifecycle }, uiState.lifecycleLoading ? "刷新中…" : "刷新统计"),
      ]),

      Vue.h("div", { class: "lifecycle-metric-grid" }, metricCards.map(card => Vue.h("div", { class: `lifecycle-metric-card lifecycle-metric-${card.tone}`, key: card.label }, [
        Vue.h("span", null, card.label),
        Vue.h("strong", null, card.value),
        Vue.h("small", null, card.note),
      ]))),

      section("保留策略", "控制无注册的产物、上传和记忆回收站的过期清理口径；已归档的对话不受此策略影响，只能手动恢复或删除。", [
        Vue.h("label", { class: "lifecycle-retention-field" }, [
          Vue.h("span", null, "保留策略"),
          Vue.h("select", { class: "lifecycle-retention-select", value: uiState.lifecycleRetentionPreset, onChange: event => setLifecycleRetentionPreset(event.target.value) }, [
            Vue.h("option", { value: "7" }, "7 天"),
            Vue.h("option", { value: "14" }, "14 天"),
            Vue.h("option", { value: "forever" }, "永久"),
            Vue.h("option", { value: "custom" }, "自定义"),
          ]),
          uiState.lifecycleRetentionPreset === "custom" ? Vue.h("input", { class: "lifecycle-days", type: "number", min: 0, max: 3650, value: uiState.lifecycleRetentionCustomDays, onInput: event => { uiState.lifecycleRetentionCustomDays = event.target.value; draw(); }, onChange: saveLifecycleSettingsFromUi }) : null,
        ]),
      ], [uiState.lifecycleStatus ? Vue.h("div", { class: "lifecycle-status" }, uiState.lifecycleStatus) : null]),

      section("已归档的对话", "手动归档的对话及其图表、导出、上传等文件保存在这里，可随时恢复；不受保留策略影响，只由你手动恢复或永久删除。", [
        Vue.h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: uiState.lifecycleReclaiming || !items.length, title: items.length ? "永久删除全部已归档对话（不可恢复）" : "暂无已归档对话", onClick: clearSessionTrash }, "永久删除全部"),
      ], [
        items.length
          ? Vue.h("div", { class: "lifecycle-trash-list" }, items.map(item => Vue.h("div", { class: "lifecycle-trash-item", key: item.id }, [
            Vue.h("div", null, [
              Vue.h("strong", null, item.source_filename || "已归档对话"),
              Vue.h("small", null, `${item.deleted_at || ""} · ${item.files} 个文件${item.artifacts ? ` + ${item.artifacts} 个产物` : ""} · ${formatLifecycleBytes(item.bytes)} · 可恢复`),
            ]),
            Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.lifecycleReclaiming, onClick: () => restoreLifecycle(item.id) }, "恢复"),
          ])))
          : Vue.h("div", { class: "lifecycle-empty" }, "暂无已归档的对话"),
      ], "lifecycle-card-recycle"),

      section("回收站", "回收站里的项目可恢复；一键删除会永久清空对应回收站。", null, [
        Vue.h("div", { class: "lifecycle-recycle-grid" }, [
          recycleBin("上传回收站", uploadTrash, clearUploadTrash, uiState.lifecycleUploadReclaiming, restoreUploadTrash, uiState.lifecycleUploadBusyKey, "已回收上传"),
          recycleBin("产物回收站", artifactTrash, clearArtifactTrash, uiState.lifecycleArtifactReclaiming, restoreArtifactTrash, uiState.lifecycleArtifactBusyKey, "已回收产物", [
            Vue.h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: uiState.lifecycleArtifactReclaiming || retentionForever || customRetentionInvalid || !expiredArtifactTrash.length, onClick: reclaimArtifactTrash }, retentionForever ? "永久保留产物" : `清理过期 · ${formatLifecycleBytes(expiredArtifactTrashBytes)}`),
          ]),
          recycleBin("记忆回收站", memoryTrash, clearMemoryTrash, uiState.lifecycleMemoryReclaiming, restoreMemoryTrashFromStorage, uiState.lifecycleMemoryBusyKey, "已归档记忆"),
        ]),
      ]),

      section("高级", "未登记的产物、上传缓存、目录统计与操作审计；日常无需关注。", [
        Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", onClick: () => { uiState.lifecycleAdvancedOpen = !uiState.lifecycleAdvancedOpen; draw(); } }, uiState.lifecycleAdvancedOpen ? "收起高级" : "展开高级"),
      ], uiState.lifecycleAdvancedOpen ? [
        Vue.h("div", { class: "lifecycle-subsection" }, [
          Vue.h("div", { class: "lifecycle-inline-title" }, "Hook 触发记录"),
          Vue.h("p", { class: "lifecycle-copy" }, "Hook 执行日志保存在本地配置目录，最多保留 500 条；可在 Hooks 页查看详情。"),
          Vue.h("div", { class: "lifecycle-preview-actions" }, [
            Vue.h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: !(uiState.lifecycleHookHistory || []).length, onClick: clearHookHistoryFromStorage }, "清理记录"),
          ]),
        ]),

        Vue.h("div", { class: "lifecycle-two-column" }, [
          section("可清理候选", "这些项目可以手动移入回收站。未知不等于垃圾；删除前请看文件名和来源。", null, [
            Vue.h("div", { class: "lifecycle-subsection" }, [
              Vue.h("div", { class: "lifecycle-inline-title" }, "Uploads"),
              Vue.h("div", { class: "lifecycle-location-list" }, uploadCategoryRows.map(([key, label]) => {
                const value = uploadCategories[key] || { files: 0, bytes: 0 };
                return Vue.h("div", { class: "lifecycle-row", key }, [Vue.h("span", null, label), Vue.h("span", null, `${value.files || 0} 个 · ${formatLifecycleBytes(value.bytes)}`)]);
              })),
              previewList(`未知上传前 ${Math.min(20, unknownUploadSamples.length)} 项`, unknownUploadSamples, uploadCandidateRow, "没有未知上传"),
              previewList(`Excel 解析缓存前 ${Math.min(20, cacheUploadSamples.length)} 项`, cacheUploadSamples, uploadCandidateRow, "没有 Excel 解析缓存"),
            ]),
            Vue.h("div", { class: "lifecycle-subsection" }, [
              Vue.h("div", { class: "lifecycle-inline-title" }, "历史 charts / exports"),
              Vue.h("p", { class: "lifecycle-copy" }, `发现 ${unknownFiles.length} 个未登记历史产物（${formatLifecycleBytes(preview.unknown_bytes)}），已登记但缺失 ${missingRegistered.length} 个。`),
              previewList(`未登记历史产物前 ${Math.min(20, unknownFiles.length)} 项`, unknownFiles.slice(0, 20), (item, index) => {
                const key = `${item.type || "artifact"}:${item.relative_path || item.filename || index}`;
                const recycling = uiState.lifecycleRecyclingKey === key;
                return Vue.h("div", { class: "lifecycle-preview-item", key }, [
                  Vue.h("span", { title: item.relative_path || item.filename || "" }, `${item.type || "unknown"} · ${item.filename || "未命名产物"}`),
                  Vue.h("div", { class: "lifecycle-preview-actions" }, [
                    Vue.h("small", null, formatLifecycleBytes(item.size_bytes)),
                    Vue.h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: recycling || uiState.lifecycleLoading, onClick: () => recycleUnregisteredArtifact(item) }, recycling ? "移动中…" : "删除"),
                  ]),
                ]);
              }, "没有未登记历史产物"),
            ]),
            Vue.h("div", { class: "lifecycle-subsection" }, [
              Vue.h("div", { class: "lifecycle-inline-title" }, "已登记产物引用"),
              Vue.h("p", { class: "lifecycle-copy" }, `扫描 ${referencePreview.reference_sources || 0} 个会话文件：已登记 ${referencePreview.registered || 0} 个，发现引用 ${referencePreview.referenced || 0} 个，未发现引用 ${referencePreview.unreferenced || 0} 个，文件缺失 ${referencePreview.missing || 0} 个。`),
              previewList(`未发现引用前 ${Math.min(20, unreferencedRegistered.length)} 项`, unreferencedRegistered, (item, index) => {
                const key = `registered:${item.id || index}`;
                const recycling = uiState.lifecycleRecyclingKey === key;
                return Vue.h("div", { class: "lifecycle-preview-item", key }, [
                  Vue.h("span", { title: item.id || item.filename || "" }, `${item.type || "artifact"} · ${item.filename || item.id || "未命名产物"}`),
                  Vue.h("div", { class: "lifecycle-preview-actions" }, [
                    Vue.h("small", null, formatLifecycleBytes(item.size_bytes)),
                    ["chart", "export", "report"].includes(item.type) ? Vue.h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: recycling || uiState.lifecycleLoading, onClick: () => recycleRegisteredArtifact(item) }, recycling ? "移动中…" : "回收") : null,
                  ]),
                ]);
              }, "没有未发现引用的已登记产物"),
              missingRegisteredSamples.length ? Vue.h("div", { class: "lifecycle-preview-actions" }, [
                Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.lifecycleLoading, onClick: pruneMissingArtifacts }, `清理缺失记录（${missingRegisteredSamples.length}）`),
              ]) : null,
              previewList(`缺失记录前 ${Math.min(20, missingRegisteredSamples.length)} 项`, missingRegisteredSamples, (item, index) => Vue.h("div", { class: "lifecycle-preview-item", key: `${item.id || index}` }, [
                Vue.h("span", { title: item.id || item.filename || "" }, `${item.type || "artifact"} · ${item.filename || item.id || "缺失产物"}`),
                Vue.h("small", null, formatLifecycleBytes(item.size_bytes)),
              ]), "没有缺失的已登记产物"),
            ]),
          ]),

          section("数据统计", "展示各类本地数据的占用情况。知识库请通过知识库管理删除，Workspace 请通过工作区流程删除。", Vue.h("span", { class: "lifecycle-badge lifecycle-badge-protected" }, "分类统计"), [
            Vue.h("div", { class: "lifecycle-location-list" }, protectedRows.map(([label, value]) => Vue.h("div", { class: "lifecycle-row", key: label }, [Vue.h("span", null, label), Vue.h("span", null, value)]))),
          ], "lifecycle-card-stats"),
        ]),

        section("生命周期记录", "记录最近的登记、回收、恢复和清理操作；API 会隐藏本地绝对路径。", [
          Vue.h("select", { class: "lifecycle-retention-select", value: uiState.lifecycleAuditFilter, onChange: event => { uiState.lifecycleAuditFilter = event.target.value; draw(); } }, [
            Vue.h("option", { value: "all" }, "全部"),
            Vue.h("option", { value: "session" }, "会话"),
            Vue.h("option", { value: "artifact" }, "产物"),
            Vue.h("option", { value: "reclaim" }, "清理"),
          ]),
          Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.lifecycleLoading, onClick: loadLifecycle }, "刷新"),
        ], [audit.length ? Vue.h("div", { class: "lifecycle-audit-list" }, audit.map((item, index) => {
          const detail = lifecycleAuditDetails(item);
          return Vue.h("div", { class: "lifecycle-audit-item", key: `${item.at || ""}-${item.event || index}` }, [
            Vue.h("div", { class: "lifecycle-audit-main" }, [
              Vue.h("span", null, lifecycleAuditLabel(item.event)),
              detail ? Vue.h("em", null, detail) : null,
            ]),
            Vue.h("small", null, item.at || ""),
          ]);
        })) : Vue.h("div", { class: "lifecycle-empty" }, "暂无生命周期记录")]),
      ] : []),
    ]);
  }

  // ── 记忆 (long-term memory) ────────────────────────────────────
  function _memorySid() {
    return state.SID
      || sessionStorage.getItem("baa_session_id")
      || localStorage.getItem("baa_session_id")
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
            Vue.h("time", null, item.ts || ""),
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
  function _memoryCard(r) {
    const scope = r.scope === "workspace" ? "workspace" : "user";
    const typeBadge = Vue.h("span", { class: `memory-type-badge scope-${scope}` }, scope === "workspace" ? "工作区级" : "用户级");
    const updated = r.updated_at || r.created_at || "";
    return Vue.h("div", { class: "memory-card", key: r.name }, [
      Vue.h("div", { class: "memory-card-head" }, [
        typeBadge,
        Vue.h("strong", { class: "memory-title" }, r.title || r.name),
        Vue.h("span", { class: "memory-updated" }, updated),
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

  function renderApp() {
    if (!root) return;
    const tabs = [
      ["general", "通用"],
      ["llm", "LLM模型"],
      ["model", "知识库检索"],
      ["gpu", "GPU算力", "规划中"],
      ["memory", "记忆"],
      ["bots", "机器人"],
      ["hooks", "Hooks"],
      ["storage", "存储"],
    ];
    Vue.render(Vue.h("div", { class: "app-settings-layout" }, [
      Vue.h("aside", { class: "app-settings-nav", "aria-label": "Settings sections" }, tabs.map(([id, label, status]) =>
        Vue.h("button", {
          class: `app-settings-nav-item${uiState.tab === id ? " active" : ""}${status ? " is-planned" : ""}`,
          type: "button",
          disabled: Boolean(status),
          title: status ? `${label}${status}` : label,
          onClick: () => { uiState.tab = id; draw(); if (id === "storage") loadLifecycle(); if (id === "memory") loadMemory(); if (id === "gpu") { loadGpuStatus(); loadGpuConnections(); } if (id === "bots") loadFeishuBot(); },
        }, [
          Vue.h("span", { class: "app-settings-nav-label" }, label),
          status ? Vue.h("span", { class: "app-settings-nav-status" }, status) : null,
        ])
      )),
      uiState.tab === "storage" ? renderStorage()
        : uiState.tab === "hooks" ? renderHooks()
        : uiState.tab === "bots" ? renderBots()
        : uiState.tab === "memory" ? renderMemory()
        : uiState.tab === "model" ? renderModel()
        : uiState.tab === "llm" ? renderLlm()
        : uiState.tab === "gpu" ? renderGpu()
        : renderGeneral(),
    ]), root);
  }

  function init() {
    appState.promptSuggestionEnabled = _enabledFromStorage();
    appState.teamsEnabled = _teamsEnabledFromStorage();
    appState.autoMatchSkill = _autoMatchSkillFromStorage();
    appState.memoryEnabled = _memoryEnabledFromStorage();
    if (!root || !Vue?.h || !Vue?.render || !Vue?.reactive) return;
    uiState = Vue.reactive({
      tab: "general",
      promptSuggestionEnabled: appState.promptSuggestionEnabled,
      teamsEnabled: appState.teamsEnabled,
      autoMatchSkill: appState.autoMatchSkill,
      hooksText: DEFAULT_HOOKS_TEXT,
      hooksStatus: "",
      hooksStatusType: "ok",
      hooksLoading: false,
      hooksRuntime: { enabled: false, active_hooks: [], enabled_count: 0, runnable_count: 0, pending_count: 0, configured_count: 0 },
      hookHistory: [],
      hookHistoryAvailable: true,
      hookHistoryLoading: false,
      customHookOpen: false,
      customHookName: "",
      customHookNames: {},
      testEvent: "turn_start",
      feishuBot: { enabled: false, configured: false, app_id: "", app_id_masked: "", app_secret_configured: false, event_verification_token_configured: false, inbound_transport: "long_connection", receive_id_type: "chat_id", receive_id: "", receive_id_masked: "", updated_at: "" },
      feishuBotAppSecretDraft: "",
      feishuBotVerificationTokenDraft: "",
      feishuBotLoading: false,
      feishuBotChats: [],
      feishuBotChatsLoading: false,
      feishuBotChatsStatus: "",
      feishuBotStatus: "",
      feishuBotStatusType: "ok",
      botChannel: "feishu",
      bgeInstalled: false,
      bgeNeural: false,
      bgeDownloading: false,
      bgeStatus: "",
      bgeStatusType: "ok",
      embedMode: "auto",
      embedActive: "hash",
      embedDim: 384,
      embedModel: "",
      embedCloudUrl: "",
      embedCloudAvailable: false,
      embedCloudConfigured: false,
      embedCloudStatus: "unavailable",
      embedLocalAvailable: false,
      embedSwitching: false,
      embedRebuilding: false,
      embedChecking: false,
      cloudUrl: "https://embed.zafer-liu-product.xyz",
      cloudModel: "bge-large-zh",
      cloudToken: "",
      cloudTokenConfigured: false,
      cloudSaving: false,
      lifecycleReport: null,
      lifecycleTrash: [],
      lifecycleArtifactTrash: [],
      lifecycleUploadTrash: [],
      lifecycleMemoryTrash: [],
      lifecycleLoading: false,
      lifecycleReclaiming: false,
      lifecycleArtifactReclaiming: false,
      lifecycleUploadReclaiming: false,
      lifecycleMemoryReclaiming: false,
      lifecycleArtifactBusyKey: "",
      lifecycleUploadBusyKey: "",
      lifecycleMemoryBusyKey: "",
      lifecycleRecyclingKey: "",
      lifecycleRetentionPreset: "forever",
      lifecycleRetentionCustomDays: 30,
      lifecycleStatus: "",
      lifecyclePreview: null,
      lifecycleReferencePreview: null,
      lifecycleUploadsPreview: null,
      lifecycleWorkspacePreview: null,
      lifecycleAudit: [],
      lifecycleHookHistory: [],
      lifecycleAuditFilter: "all",
      lifecycleAdvancedOpen: false,
      memoryRecords: [],
      memoryWorkspaceMounted: false,
      memoryEnabled: _memoryEnabledFromStorage(),
      memoryLoading: false,
      memoryStatus: "",
      memoryStatusType: "ok",
      memoryEditing: null,
      memoryFormOpen: false,
      memoryAdvancedOpen: false,
      memoryForm: { name: "", scope: "user", title: "", body: "", why: "", how_to_apply: "" },
      memoryFormMsg: { err: "", ok: "" },
      memoryActivity: [],
      // GPU 算力（G1）
      gpuStatus: null,
      gpuOllama: null,
      gpuEnabled: true,
      gpuLoading: false,
      gpuRefreshing: false,
      gpuBusy: false,
      gpuConnections: [],
      gpuConnectionStatus: {},
      gpuConnectionModels: {},
      gpuRemoteBusy: false,
      gpuRemoteMessage: "",
      gpuConnectionForm: { connectionType: "ssh", name: "", host: "", username: "", targetPort: "8000", baseUrl: "", authMethod: "agent", password: "" },
    });
    draw = renderApp;
    draw();
    loadHooks();
    loadEmbedMode();
    loadCloudConfig();
    loadGpuStatus();
    loadGpuConnections();
  }

  document.addEventListener("DOMContentLoaded", init);

  // ── GPU 算力（G1/G2）──────────────────────────────────────────
  async function loadGpuStatus() {
    if (!uiState) return;
    if (uiState.gpuLoading) return;
    uiState.gpuLoading = true;
    draw();
    try {
      const resp = await fetch("/api/gpu/status");
      const data = await resp.json();
      uiState.gpuStatus = data.gpu || null;
      uiState.gpuOllama = data.ollama || null;
      uiState.gpuEnabled = !!data.enabled;
    } catch (err) {
      uiState.gpuStatus = { kind: "none", gpus: [], message: "GPU 状态获取失败" };
    }
    uiState.gpuLoading = false;
    draw();
  }

  async function refreshGpuStatus() {
    if (!uiState || uiState.gpuRefreshing || uiState.gpuBusy) return;
    uiState.gpuRefreshing = true;
    draw();
    try {
      const resp = await fetch("/api/gpu/status");
      const data = await resp.json();
      uiState.gpuStatus = data.gpu || null;
      uiState.gpuOllama = data.ollama || null;
      uiState.gpuEnabled = !!data.enabled;
    } catch (err) {
      /* 保持旧状态 */
    }
    uiState.gpuRefreshing = false;
    draw();
  }

  async function setGpuEnabled(enabled) {
    if (!uiState || uiState.gpuBusy) return;
    uiState.gpuBusy = true;
    draw();
    try {
      await fetch("/api/gpu/enabled", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      uiState.gpuEnabled = !!enabled;
    } catch (err) {
      /* 保持原状态 */
    }
    uiState.gpuBusy = false;
    draw();
  }

  async function _gpuRequest(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.message || "操作失败");
    return data;
  }

  async function loadGpuConnections() {
    if (!uiState) return;
    try {
      const data = await _gpuRequest("/api/gpu/connections");
      uiState.gpuConnections = data.connections || [];
      await Promise.all(uiState.gpuConnections.map(async connection => {
        const status = await _gpuRequest("/api/gpu/connections/" + connection.id + "/status");
        uiState.gpuConnectionStatus[connection.id] = status;
      }));
      uiState.gpuRemoteMessage = "";
    } catch (err) { uiState.gpuRemoteMessage = err.message || "读取连接失败"; }
    draw();
  }

  async function createGpuConnection() {
    if (!uiState || uiState.gpuRemoteBusy) return;
    uiState.gpuRemoteBusy = true;
    try {
      const form = uiState.gpuConnectionForm;
      await _gpuRequest("/api/gpu/connections", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        connection_type: form.connectionType, name: form.name, base_url: form.baseUrl, host: form.host, username: form.username, target_port: form.targetPort,
        auth_method: form.authMethod, password: form.password,
      }) });
      uiState.gpuConnectionForm = { connectionType: "ssh", name: "", host: "", username: "", targetPort: "8000", baseUrl: "", authMethod: "agent", password: "" };
      await loadGpuConnections();
    } catch (err) { uiState.gpuRemoteMessage = err.message || "添加连接失败"; }
    uiState.gpuRemoteBusy = false;
    draw();
  }

  async function connectGpuConnection(id) {
    if (!uiState || uiState.gpuRemoteBusy) return;
    uiState.gpuRemoteBusy = true;
    try {
      try { await _gpuRequest("/api/gpu/connections/" + id + "/connect", { method: "POST" }); }
      catch (err) {
        if (!String(err.message).includes("主机尚未确认")) throw err;
        const inspected = await _gpuRequest("/api/gpu/connections/" + id + "/host-key", { method: "POST" });
        const key = inspected.host_key;
        if (!window.confirm("请核对 SSH 主机指纹后确认：\n" + key.fingerprint)) throw new Error("未确认 SSH 主机指纹");
        await _gpuRequest("/api/gpu/connections/" + id + "/trust-host-key", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key_type: key.type, key_base64: key.base64 }) });
        await _gpuRequest("/api/gpu/connections/" + id + "/connect", { method: "POST" });
      }
      await loadGpuConnections();
    } catch (err) { uiState.gpuRemoteMessage = err.message || "连接失败"; }
    uiState.gpuRemoteBusy = false;
    draw();
  }

  async function disconnectGpuConnection(id) { await _gpuRequest("/api/gpu/connections/" + id + "/disconnect", { method: "POST" }); await loadGpuConnections(); }
  async function deleteGpuConnection(id) { if (window.confirm("删除此远程连接？")) { await _gpuRequest("/api/gpu/connections/" + id, { method: "DELETE" }); await loadGpuConnections(); } }
  async function discoverGpuModels(id) {
    try { const data = await _gpuRequest("/api/gpu/connections/" + id + "/models"); uiState.gpuConnectionModels[id] = data.models || []; }
    catch (err) { uiState.gpuRemoteMessage = err.message || "模型发现失败"; }
    draw();
  }
  async function registerGpuModel(id, model) {
    if (!uiState || uiState.gpuRemoteBusy) return;
    uiState.gpuRemoteBusy = true;
    try {
      const data = await _gpuRequest("/api/gpu/connections/" + id + "/models/register", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }),
      });
      uiState.gpuRemoteMessage = data.message || "模型已注册";
    } catch (err) { uiState.gpuRemoteMessage = err.message || "模型注册失败"; }
    uiState.gpuRemoteBusy = false;
    draw();
  }
  async function testGpuModel(id, model) {
    if (!uiState || uiState.gpuRemoteBusy) return;
    uiState.gpuRemoteBusy = true;
    uiState.gpuRemoteMessage = "正在验证 " + model + " 的实际推理…";
    draw();
    try {
      const data = await _gpuRequest("/api/gpu/connections/" + id + "/models/test", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }),
      });
      uiState.gpuRemoteMessage = data.message + (data.reply ? "：" + data.reply : "");
    } catch (err) { uiState.gpuRemoteMessage = err.message || "模型推理测试失败"; }
    uiState.gpuRemoteBusy = false;
    draw();
  }
  async function preflightTrainingRunner(id) {
    if (!uiState || uiState.gpuRemoteBusy) return;
    uiState.gpuRemoteBusy = true;
    try {
      const data = await _gpuRequest("/api/gpu/connections/" + id + "/training/preflight", { method: "POST" });
      uiState.gpuRemoteMessage = "远程训练器已就绪：" + (data.training_runner.gpu_name || "CUDA GPU");
      await loadGpuConnections();
    } catch (err) { uiState.gpuRemoteMessage = err.message || "远程训练器预检失败"; }
    uiState.gpuRemoteBusy = false;
    draw();
  }

  export {
    init,
    setPromptSuggestionEnabled,
    setTeamsEnabled,
    setAutoMatchSkill,
    loadHooks,
    validateHooks,
    saveHooks,
    testHooks,
    loadEmbedMode,
    checkEmbedStatus,
    setEmbedMode,
    rebuildEmbeddings,
    loadCloudConfig,
    saveCloudConfig,
    clearCloudToken,
    loadGpuStatus,
    refreshGpuStatus,
    setGpuEnabled,
  };
