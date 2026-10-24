// Feishu bot / bots channel domain.
//
// Extracted verbatim from app_settings.js (F5 follow-up, Feishu/Bots domain).
// Pure file move + ctx injection: function bodies carry over unchanged, only
// bare `uiState` / `draw` references resolve via per-function ctx aliases.
// No HTML / CSS / fetch URL / rendering logic changes.
//
// Cross-domain deps: none. `toast` / `_renderPanelHead` / `renderSwitch` come from shared.js.

import { toast, _renderPanelHead, renderSwitch } from "./shared.js";

/**
 * @param {{uiState: object, draw: () => void, appState: object, state: object, root: HTMLElement|null}} ctx
 */
export function createFeishuDomain(ctx) {
  const Vue = window.Vue;

  async function loadFeishuBot() {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
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
    const uiState = ctx.uiState;
    const draw = ctx.draw;
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
    const uiState = ctx.uiState;
    const draw = ctx.draw;
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
      uiState.feishuBotStatus = uiState.feishuBot.inbound_status === "restart_required"
        ? "配置已保存；出站发送已更新。请重启应用，使新的飞书长连接凭据生效。"
        : uiState.feishuBot.configured
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
    const uiState = ctx.uiState;
    const draw = ctx.draw;
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

  function renderFeishuBot() {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    const bot = uiState.feishuBot || {};
    const configured = !!bot.configured;
    const credentialsReady = !!bot.app_id && !!bot.app_secret_configured;
    const inboundStatus = String(bot.inbound_status || "idle");
    const inboundLabel = {
      connected: "入站长连接已建立",
      starting: "正在建立入站长连接",
      paused: "入站长连接已暂停；重启后会关闭旧连接",
      restart_required: "入站配置已更新；请重启应用后生效",
      webhook: "Webhook 入站已启用",
      disabled: "入站与发送均已暂停",
      not_configured: "等待补全应用凭据与目标群",
      unavailable: "长连接运行状态暂不可用",
      error: `入站连接失败${bot.inbound_error ? `：${bot.inbound_error}` : ""}`,
    }[inboundStatus] || "等待建立入站连接";
    const statusLabel = configured
      ? (bot.enabled ? "已配置，发送已启用" : "已配置，发送已暂停")
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
          Vue.h("span", null, inboundLabel),
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
    const uiState = ctx.uiState;
    const draw = ctx.draw;
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
  return { loadFeishuBot, loadFeishuChats, saveFeishuBot, testFeishuBot, renderFeishuBot, renderBots };
}
