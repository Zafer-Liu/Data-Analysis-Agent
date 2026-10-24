// Shared helpers and context contract for the app_settings domain modules.
//
// app_settings.js is being split by domain (GPU / Memory / Hooks / …). Each
// extracted module receives a `ctx` object created in init() holding the
// module-scope shared state (`uiState`, `draw`, `appState`, …) and imports the
// pure helpers below directly. This keeps the extraction a verbatim move:
// function bodies carry over unchanged, only their closure references switch
// from bare `uiState`/`draw` to `ctx.uiState`/`ctx.draw`.

import { uiRegistry } from "../../core/ui-registry.js";

const Vue = window.Vue;

/**
 * Build the shared context handed to each domain factory.
 * Called once from app_settings.js init() after `uiState` is created.
 *
 * @param {{uiState: object, draw: () => void, appState: object, state: object, root: HTMLElement|null}} deps
 */
export function makeSettingsCtx(deps) {
  return Object.freeze(deps);
}

// ---- Pure helpers (no uiState dependency) --------------------

export function toast(message, type = "") {
  uiRegistry.toast?.(message, type);
}

export function _readLocalSetting(key, fallback = null) {
  try {
    const value = globalThis.localStorage?.getItem(key);
    return value == null ? fallback : value;
  } catch (_) {
    return fallback;
  }
}

export function _writeLocalSetting(key, value) {
  try {
    globalThis.localStorage?.setItem(key, value);
    return true;
  } catch (_) {
    toast("浏览器阻止了设置存储，本次修改仅在当前页面有效", "err");
    return false;
  }
}

export function renderSwitch(checked, onChange) {
  return Vue.h("span", { class: "app-setting-switch" }, [
    Vue.h("input", {
      type: "checkbox",
      checked,
      onChange: event => onChange(event.target.checked),
    }),
    Vue.h("span", { "aria-hidden": "true" }),
  ]);
}

export function _renderPanelHead(title, description, actions) {
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
