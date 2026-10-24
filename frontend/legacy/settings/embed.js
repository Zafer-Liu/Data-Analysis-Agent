// Embedding / knowledge-retrieval model domain.
//
// Extracted verbatim from app_settings.js (F5 follow-up, Embed/Model domain).
// Pure file move + ctx injection: function bodies carry over unchanged, only
// bare `uiState` / `draw` references switch to `ctx.uiState` / `ctx.draw`.
// No HTML / CSS / fetch URL / rendering logic changes.
//
// Cross-domain deps: none. `toast` / `_renderPanelHead` come from shared.js.

import { toast, _renderPanelHead } from "./shared.js";

/**
 * @param {{uiState: object, draw: () => void, appState: object, state: object, root: HTMLElement|null}} ctx
 */
export function createEmbedDomain(ctx) {
  const Vue = window.Vue;

  function applyEmbedInfo(data) {
    const uiState = ctx.uiState;
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

  async function downloadBgeModel() {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
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

  async function loadEmbedMode(probe = false) {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState) return;
    try {
      const resp = await fetch(`/api/system/embed-mode${probe ? "?probe=1" : ""}`);
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "\u5d4c\u5165\u6a21\u5f0f\u52a0\u8f7d\u5931\u8d25");
      applyEmbedInfo(data);
      if (data.init_error) {
        uiState.bgeStatus = `\u6a21\u578b\u521d\u59cb\u5316\u5931\u8d25\uff1a${data.init_error}`;
        uiState.bgeStatusType = "error";
      }
    } catch (error) {
      uiState.bgeStatus = `\u6a21\u5f0f\u52a0\u8f7d\u5931\u8d25\uff1a${error.message || error}`;
      uiState.bgeStatusType = "error";
    } finally {
      draw();
    }
  }

  async function loadCloudConfig() {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState) return;
    try {
      const resp = await fetch("/api/system/embed-cloud-config");
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "\u4e91\u7aef\u914d\u7f6e\u52a0\u8f7d\u5931\u8d25");
      uiState.cloudUrl = data.url || "";
      uiState.cloudModel = data.model || "bge-large-zh";
      uiState.cloudTokenConfigured = !!data.token_configured;
    } catch (error) {
      uiState.bgeStatus = `\u4e91\u7aef\u914d\u7f6e\u52a0\u8f7d\u5931\u8d25\uff1a${error.message || error}`;
      uiState.bgeStatusType = "error";
    } finally {
      draw();
    }
  }

  async function saveCloudConfig(test = false, clearToken = false) {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.cloudSaving) return;
    const url = String(uiState.cloudUrl || "").trim();
    const model = String(uiState.cloudModel || "").trim();
    const token = String(uiState.cloudToken || "").trim();
    if (!url || !model) {
      uiState.bgeStatus = "\u8bf7\u586b\u5199\u4e91\u7aef URL \u548c\u6a21\u578b\u540d\u3002";
      uiState.bgeStatusType = "error";
      draw();
      return;
    }
    if (test && !token && !uiState.cloudTokenConfigured && !clearToken) {
      uiState.bgeStatus = "\u6d4b\u8bd5\u8fde\u63a5\u524d\u8bf7\u586b\u5199 Bearer Token\u3002";
      uiState.bgeStatusType = "error";
      draw();
      return;
    }
    uiState.cloudSaving = true;
    uiState.bgeStatus = test ? "\u6b63\u5728\u4fdd\u5b58\u5e76\u6d4b\u8bd5\u4e91\u7aef\u8fde\u63a5\u2026" : "\u6b63\u5728\u4fdd\u5b58\u4e91\u7aef\u914d\u7f6e\u2026";
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
      if (!resp.ok || !data.ok) throw new Error(data.error || "\u4e91\u7aef\u914d\u7f6e\u4fdd\u5b58\u5931\u8d25");
      uiState.cloudUrl = data.url || url;
      uiState.cloudModel = data.model || model;
      uiState.cloudToken = "";
      uiState.cloudTokenConfigured = !!data.token_configured;
      uiState.embedCloudAvailable = !!data.test?.available;
      uiState.bgeStatus = data.test?.available
        ? `\u4e91\u7aef\u8fde\u63a5\u6210\u529f\uff1a${data.test.model}\uff0c${data.test.dim} \u7ef4\u3002`
        : clearToken
          ? "\u4e91\u7aef\u51ed\u636e\u5df2\u6e05\u9664\u3002"
          : "\u4e91\u7aef\u914d\u7f6e\u5df2\u4fdd\u5b58\u3002";
      uiState.bgeStatusType = "ok";
      if (test) await loadEmbedMode();
      toast(data.test?.available ? "\u4e91\u7aef Embedding \u8fde\u63a5\u6210\u529f" : "\u4e91\u7aef\u914d\u7f6e\u5df2\u4fdd\u5b58");
    } catch (error) {
      uiState.embedCloudAvailable = false;
      uiState.bgeStatus = `${test ? "\u8fde\u63a5\u6d4b\u8bd5" : "\u4fdd\u5b58"}\u5931\u8d25\uff1a${error.message || error}`;
      uiState.bgeStatusType = "error";
      // Failed test marks cloud unavailable server-side; refresh "active" badge.
      if (test) await loadEmbedMode();
    } finally {
      uiState.cloudSaving = false;
      draw();
    }
  }

  async function clearCloudToken() {
    const uiState = ctx.uiState;
    if (!uiState?.cloudTokenConfigured) return;
    const accepted = await window.BAA.ui?.confirm?.({
      title: "\u6e05\u9664\u4e91\u7aef\u51ed\u636e",
      message: "\u6e05\u9664\u540e\u4e91\u7aef Embedding \u5c06\u4e0d\u53ef\u7528\uff0c\u81ea\u52a8\u6a21\u5f0f\u4f1a\u964d\u7ea7\u5230\u672c\u5730\u6a21\u578b\u3002",
      danger: true,
    });
    if (accepted) await saveCloudConfig(false, true);
  }

  async function checkEmbedStatus() {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.embedChecking) return;
    uiState.embedChecking = true;
    uiState.bgeStatus = "\u6b63\u5728\u68c0\u67e5\u540e\u7aef\u72b6\u6001\u2026";
    uiState.bgeStatusType = "ok";
    draw();
    try {
      await loadEmbedMode(true);
      const s = uiState.embedCloudStatus;
      uiState.bgeStatus = uiState.embedCloudConfigured
        ? (s === "available" ? "\u72b6\u6001\u5df2\u5237\u65b0\uff1a\u4e91\u7aef\u8fde\u63a5\u6b63\u5e38\u3002" : "\u72b6\u6001\u5df2\u5237\u65b0\uff1a\u4e91\u7aef\u4e0d\u53ef\u8fbe\uff0c\u5df2\u6309\u5b9e\u9645\u540e\u7aef\u663e\u793a\u3002")
        : "\u72b6\u6001\u5df2\u5237\u65b0\u3002";
      uiState.bgeStatusType = uiState.embedCloudConfigured && s !== "available" ? "error" : "ok";
    } finally {
      uiState.embedChecking = false;
      draw();
    }
  }

  async function setEmbedMode(mode) {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.embedSwitching) return;
    uiState.embedSwitching = true;
    uiState.bgeStatus = "\u6b63\u5728\u5207\u6362\u5d4c\u5165\u6a21\u5f0f\u2026";
    uiState.bgeStatusType = "ok";
    draw();
    try {
      const resp = await fetch("/api/system/embed-mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "\u5d4c\u5165\u6a21\u5f0f\u5207\u6362\u5931\u8d25");
      applyEmbedInfo(data);
      const modeNames = { auto: "\u81ea\u52a8", cloud: "\u4e91\u7aef", local: "\u672c\u5730", hash: "\u57fa\u7840" };
      const fallback = data.active === "hash" && mode !== "hash";
      uiState.bgeStatus = fallback
        ? `\u5df2\u9009\u62e9${modeNames[mode] || mode}\uff0c\u4f46\u5f53\u524d\u56de\u9000\u5230\u57fa\u7840\u5339\u914d\uff1b\u8bf7\u68c0\u67e5\u6a21\u578b\u6216\u4e91\u7aef\u8fde\u63a5\u3002`
        : `\u5df2\u5207\u6362\u4e3a${modeNames[mode] || mode}\u6a21\u5f0f\uff0c\u8bf7\u91cd\u5efa\u5411\u91cf\u5e93\u4ee5\u7edf\u4e00\u5df2\u6709\u6587\u6863\u5411\u91cf\u3002`;
      uiState.bgeStatusType = fallback ? "error" : "ok";
      toast("\u5d4c\u5165\u6a21\u5f0f\u5df2\u5207\u6362");
    } catch (error) {
      uiState.bgeStatus = `\u5207\u6362\u5931\u8d25\uff1a${error.message || error}`;
      uiState.bgeStatusType = "error";
      await loadEmbedMode();
    } finally {
      uiState.embedSwitching = false;
      draw();
    }
  }

  async function rebuildEmbeddings() {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.embedRebuilding) return;
    const accepted = await window.BAA.ui?.confirm?.({
      title: "\u91cd\u5efa\u77e5\u8bc6\u5e93\u5411\u91cf",
      message: "\u5c06\u68c0\u67e5\u6587\u6863\u3001\u7ed3\u6784\u5316\u77e5\u8bc6\u548c Skill\uff0c\u4ec5\u91cd\u5efa\u5185\u5bb9\u6216\u6a21\u578b\u53d1\u751f\u53d8\u5316\u7684\u5411\u91cf\u3002",
      danger: false,
    });
    if (!accepted) return;
    uiState.embedRebuilding = true;
    uiState.bgeStatus = "\u6b63\u5728\u91cd\u5efa\u77e5\u8bc6\u5e93\u5411\u91cf\u2026";
    uiState.bgeStatusType = "ok";
    draw();
    try {
      const resp = await fetch("/api/system/embed-rebuild", { method: "POST" });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "\u5411\u91cf\u91cd\u5efa\u5931\u8d25");
      uiState.bgeStatus = `\u5411\u91cf\u7f13\u5b58\u5df2\u540c\u6b65\uff1a\u6587\u6863\u5206\u5757 ${Number(data.document_chunks || 0)}\u3001\u7ed3\u6784\u5316\u77e5\u8bc6 ${Number(data.structured_records || 0)}\u3001Skill ${Number(data.skills || 0)}\u3002`;
      uiState.bgeStatusType = "ok";
      toast("\u77e5\u8bc6\u5e93\u5411\u91cf\u91cd\u5efa\u5b8c\u6210");
    } catch (error) {
      uiState.bgeStatus = `\u91cd\u5efa\u5931\u8d25\uff1a${error.message || error}`;
      uiState.bgeStatusType = "error";
    } finally {
      uiState.embedRebuilding = false;
      draw();
    }
  }

  function renderModel() {
    const uiState = ctx.uiState;
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
    const cloudLabel = cloudOk ? "\u8fde\u63a5\u6b63\u5e38" : cloudConfigured ? "\u5df2\u914d\u7f6e" : "\u672a\u8fde\u63a5";
    const localOk = uiState.embedLocalAvailable;

    const modeOptions = [
      { key: "auto", label: "\u81ea\u52a8", detail: "\u4e91\u7aef\u4f18\u5148\uff0c\u5931\u8d25\u540e\u4f7f\u7528\u672c\u5730\uff0c\u518d\u56de\u9000\u57fa\u7840\u5339\u914d" },
      { key: "cloud", label: "\u4e91\u7aef", detail: "\u5f3a\u5236\u4f7f\u7528 BGE-large-zh 1024\u7ef4" },
      { key: "local", label: "\u672c\u5730", detail: "\u5f3a\u5236\u4f7f\u7528 BGE-small-zh 512\u7ef4" },
      { key: "hash", label: "\u57fa\u7840", detail: "\u5173\u952e\u8bcd\u5339\u914d\uff0c\u65e0\u9700\u6a21\u578b\u4e0e\u7f51\u7edc\uff1b\u8bed\u4e49\u7406\u89e3\u80fd\u529b\u6709\u9650" },
    ];
    const activeLabel = active === "cloud" ? "\u4e91\u7aef" : active === "local" ? "\u672c\u5730" : "\u57fa\u7840";
    const btnLabel = downloading ? "\u4e0b\u8f7d\u4e2d\u2026" : installed ? "\u91cd\u65b0\u4e0b\u8f7d" : "\u4e0b\u8f7d\u672c\u5730\u6a21\u578b";
    const tokenPlaceholder = uiState.cloudTokenConfigured
      ? "\u5df2\u914d\u7f6e\uff1b\u7559\u7a7a\u5c06\u4fdd\u7559\u539f Token"
      : "\u8f93\u5165 Bearer Token";

    return Vue.h("section", { class: "app-settings-panel model-settings-panel" }, [
      _renderPanelHead("\u77e5\u8bc6\u5e93\u68c0\u7d22\u6a21\u578b", "\u914d\u7f6e\u4e91\u7aef\u4e0e\u672c\u5730 Embedding\uff0c\u5e76\u9009\u62e9\u77e5\u8bc6\u5e93\u548c Skill \u68c0\u7d22\u4f7f\u7528\u7684\u540e\u7aef\u3002", [
        Vue.h("div", { class: "embed-active-state", title: embedModel }, [
          Vue.h("span", null, "\u5f53\u524d\u8fd0\u884c"),
          Vue.h("strong", null, `${activeLabel} \u00b7 ${dim}\u7ef4`),
        ]),
        Vue.h("button", {
          class: "btn-sm btn-sm-ghost embed-check-btn",
          type: "button",
          disabled: uiState.embedChecking,
          title: "\u624b\u52a8\u63a2\u6d4b\u4e91\u7aef\u8fde\u901a\u6027\u5e76\u5237\u65b0\u72b6\u6001\uff08\u4e0d\u4f1a\u81ea\u52a8\u8f6e\u8be2\uff09",
          onClick: checkEmbedStatus,
        }, uiState.embedChecking ? "\u68c0\u67e5\u4e2d\u2026" : "\u68c0\u67e5\u72b6\u6001"),
      ]),

      Vue.h("div", { class: "embed-provider-grid" }, [
        Vue.h("section", { class: "embed-provider-section" }, [
          Vue.h("div", { class: "embed-provider-head" }, [
            Vue.h("div", null, [
              Vue.h("strong", null, "\u672c\u5730\u6a21\u578b"),
              Vue.h("span", null, "BGE-small-zh-v1.5 \u00b7 512\u7ef4"),
            ]),
            Vue.h("span", { class: `bge-badge ${localOk ? "bge-badge-ok" : "bge-badge-warn"}` },
              localOk ? "\u53ef\u7528" : "\u4e0d\u53ef\u7528"),
          ]),
          Vue.h("p", { class: "embed-provider-copy" }, "\u79bb\u7ebf\u8fd0\u884c\uff0c\u65e0\u9700\u7f51\u7edc\uff1b\u9996\u6b21\u4f7f\u7528\u9700\u4e0b\u8f7d\u7ea6 91 MB \u6a21\u578b\u6587\u4ef6\u3002"),
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
              Vue.h("strong", null, "\u4e91\u7aef\u670d\u52a1"),
              Vue.h("span", null, "OpenAI \u517c\u5bb9 /v1/embeddings \u00b7 1024\u7ef4"),
            ]),
            Vue.h("span", { class: `bge-badge ${cloudOk ? "bge-badge-ok" : "bge-badge-warn"}` },
              cloudLabel),
          ]),
          Vue.h("form", {
            class: "embed-cloud-form",
            onSubmit: event => { event.preventDefault(); saveCloudConfig(false); },
          }, [
            Vue.h("label", { class: "embed-config-field embed-config-field-wide" }, [
              Vue.h("span", null, "\u670d\u52a1 URL"),
              Vue.h("input", {
                type: "url",
                value: uiState.cloudUrl,
                placeholder: "https://embed.example.com",
                onInput: event => { uiState.cloudUrl = event.target.value; },
              }),
            ]),
            Vue.h("label", { class: "embed-config-field" }, [
              Vue.h("span", null, "\u6a21\u578b\u540d"),
              Vue.h("input", {
                type: "text",
                value: uiState.cloudModel,
                placeholder: "bge-large-zh",
                onInput: event => { uiState.cloudModel = event.target.value; },
              }),
            ]),
            Vue.h("label", { class: "embed-config-field" }, [
              Vue.h("span", null, uiState.cloudTokenConfigured ? "Bearer Token \u00b7 \u5df2\u914d\u7f6e" : "Bearer Token"),
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
              }, "\u6e05\u9664\u51ed\u636e") : null,
              Vue.h("span", { class: "embed-actions-spacer" }),
              Vue.h("button", {
                class: "btn-sm btn-sm-ghost",
                type: "submit",
                disabled: cloudBusy,
              }, cloudBusy ? "\u5904\u7406\u4e2d\u2026" : "\u4fdd\u5b58"),
              Vue.h("button", {
                class: "btn-sm btn-sm-primary",
                type: "button",
                disabled: cloudBusy,
                onClick: () => saveCloudConfig(true),
              }, cloudBusy ? "\u6d4b\u8bd5\u4e2d\u2026" : "\u4fdd\u5b58\u5e76\u6d4b\u8bd5"),
            ]),
          ]),
        ]),
      ]),

      Vue.h("section", { class: "embed-mode-section" }, [
        Vue.h("div", { class: "embed-section-heading" }, [
          Vue.h("strong", null, "\u8fd0\u884c\u6a21\u5f0f"),
          Vue.h("span", null, "\u5207\u6362\u540e\u5efa\u8bae\u91cd\u5efa\u5df2\u6709\u6587\u6863\u5411\u91cf"),
        ]),
        Vue.h("div", { class: "embed-mode-control", role: "group", "aria-label": "\u5d4c\u5165\u6a21\u5f0f" },
          modeOptions.map(option => Vue.h("button", {
            class: `embed-mode-option${mode === option.key ? " active" : ""}`,
            type: "button",
            disabled: switching,
            title: option.detail,
            onClick: () => setEmbedMode(option.key),
          }, [
            Vue.h("strong", null, option.label),
            Vue.h("span", null, option.key === "auto" ? "\u63a8\u8350" : option.key === "cloud" ? "1024\u7ef4" : option.key === "local" ? "512\u7ef4" : "384\u7ef4"),
          ]))
        ),
        Vue.h("p", { class: "embed-mode-description" },
          modeOptions.find(option => option.key === mode)?.detail || ""),
        Vue.h("div", { class: "embed-capability-row" }, [
          Vue.h("span", { class: `bge-badge ${active !== "hash" ? "bge-badge-ok" : "bge-badge-warn"}` }, `${activeLabel} ${dim}\u7ef4`),
          Vue.h("span", { class: `bge-badge ${cloudOk ? "bge-badge-ok" : "bge-badge-warn"}` }, cloudOk ? "\u4e91\u7aef\u53ef\u7528" : uiState.embedCloudStatus === "configured" ? "\u4e91\u7aef\u672a\u9a8c\u8bc1" : cloudConfigured ? "\u4e91\u7aef\u4e0d\u53ef\u7528" : "\u4e91\u7aef\u672a\u914d\u7f6e"),
          Vue.h("span", { class: `bge-badge ${localOk ? "bge-badge-ok" : "bge-badge-warn"}` }, localOk ? "\u672c\u5730\u53ef\u7528" : "\u672c\u5730\u4e0d\u53ef\u7528"),
        ]),
      ]),

      Vue.h("section", { class: "embed-rebuild-section" }, [
        Vue.h("div", { class: "embed-section-heading" }, [
          Vue.h("strong", null, "\u5411\u91cf\u5e93"),
          Vue.h("span", null, "\u4ec5\u540c\u6b65\u5185\u5bb9\u6216\u6a21\u578b\u53d1\u751f\u53d8\u5316\u7684\u77e5\u8bc6\u4e0e Skill"),
        ]),
        Vue.h("button", {
          class: "btn-sm btn-sm-ghost",
          type: "button",
          disabled: rebuilding,
          onClick: rebuildEmbeddings,
        }, rebuilding ? "\u91cd\u5efa\u4e2d\u2026" : "\u91cd\u5efa\u5411\u91cf\u5e93"),
      ]),

      statusText
        ? Vue.h("div", { class: `embed-status-message embed-status-${statusType}`, role: "status" }, statusText)
        : null,
    ]);
  }

  return {
    applyEmbedInfo,
    downloadBgeModel,
    loadEmbedMode,
    loadCloudConfig,
    saveCloudConfig,
    clearCloudToken,
    checkEmbedStatus,
    setEmbedMode,
    rebuildEmbeddings,
    renderModel,
  };
}
