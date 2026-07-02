import { uiRegistry } from "../../core/ui-registry.js";

// Global toast, loading and confirmation surface.
export function mountGlobalUi() {
  window.BAA = window.BAA || {};

  const Vue = window.Vue;
  const root = document.getElementById("global-ui-root");
  const hasVue = root && Vue && Vue.h && Vue.render;
  const toastTimers = new Map();

  function legacyToast(message, type = "") {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = message;
    el.className = "toast show" + (type ? " " + type : "");
    setTimeout(() => { el.className = "toast"; }, 2800);
  }

  function legacyShowLoading(options = {}) {
    const mask = document.getElementById(options.legacyId || "session-load-mask");
    if (!mask) return null;
    const nameEl = document.getElementById("session-load-name");
    const elapsedEl = document.getElementById("session-load-elapsed");
    if (nameEl) nameEl.textContent = options.name || "";
    if (elapsedEl) elapsedEl.textContent = options.elapsed || "0s";
    mask.hidden = false;
    mask.classList.add("open");
    return options.id || "legacy-loading";
  }

  function legacyHideLoading() {
    const mask = document.getElementById("session-load-mask");
    if (!mask) return;
    mask.classList.remove("open");
    mask.hidden = true;
  }

  if (!hasVue) {
    Object.assign(uiRegistry, {
      isVue: false,
      toast: legacyToast,
      showLoading: legacyShowLoading,
      hideLoading: legacyHideLoading,
      updateLoading() {},
      confirm() {
        legacyToast("当前界面尚未就绪，请稍后重试", "err");
        return Promise.resolve(false);
      },
    });
    return;
  }

  const { h, render } = Vue;
  const state = {
    toasts: [],
    loading: {
      visible: false,
      id: "",
      title: "",
      name: "",
      message: "",
      elapsedLabel: "",
      elapsed: "",
      cancelText: "",
      cancellable: false,
      onCancel: null,
      startedAt: 0,
      timer: null,
    },
    confirm: {
      visible: false,
      title: "",
      message: "",
      confirmText: "",
      cancelText: "",
      danger: false,
      resolve: null,
    },
  };
  let toastSeq = 0;

  function elapsedText(startedAt) {
    if (!startedAt) return "";
    const seconds = Math.floor((Date.now() - startedAt) / 1000);
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    return `${minutes}m ${String(seconds % 60).padStart(2, "0")}s`;
  }

  function clearLoadingTimer() {
    if (state.loading.timer) {
      clearInterval(state.loading.timer);
      state.loading.timer = null;
    }
  }

  function removeToast(id) {
    const idx = state.toasts.findIndex(item => item.id === id);
    if (idx >= 0) state.toasts.splice(idx, 1);
    const timer = toastTimers.get(id);
    if (timer) clearTimeout(timer);
    toastTimers.delete(id);
    renderUi();
  }

  function renderToast(item) {
    const classes = ["global-toast"];
    if (item.type) classes.push(item.type);
    return h("div", { key: item.id, class: classes.join(" ") }, [
      h("div", { class: "global-toast-icon", "aria-hidden": "true" }, item.type === "err" ? "!" : "✓"),
      h("div", { class: "global-toast-text" }, item.message),
      h("button", {
        class: "global-toast-close",
        type: "button",
        title: "Close",
        onClick: () => removeToast(item.id),
      }, "×"),
    ]);
  }

  function renderLoading() {
    const loading = state.loading;
    if (!loading.visible) return null;
    const meta = loading.elapsedLabel && loading.elapsed
      ? h("div", { class: "global-loading-meta" }, [
          h("span", null, loading.elapsedLabel),
          h("strong", null, loading.elapsed),
        ])
      : null;
    const cancel = loading.cancellable
      ? h("button", {
          class: "btn-sm btn-sm-ghost global-loading-cancel",
          type: "button",
          onClick: () => {
            if (typeof loading.onCancel === "function") loading.onCancel();
          },
        }, loading.cancelText || "Cancel")
      : null;

    return h("div", { class: "global-loading-mask", role: "status", "aria-live": "polite" }, [
      h("div", { class: "global-loading-panel" }, [
        h("div", { class: "global-loading-spinner", "aria-hidden": "true" }),
        h("div", { class: "global-loading-copy" }, [
          h("div", { class: "global-loading-title" }, loading.title),
          loading.name ? h("div", { class: "global-loading-name" }, loading.name) : null,
          loading.message ? h("div", { class: "global-loading-sub" }, loading.message) : null,
          meta,
        ]),
        cancel,
      ]),
    ]);
  }

  function settleConfirm(accepted) {
    const resolve = state.confirm.resolve;
    Object.assign(state.confirm, {
      visible: false,
      title: "",
      message: "",
      confirmText: "",
      cancelText: "",
      danger: false,
      resolve: null,
    });
    renderUi();
    if (typeof resolve === "function") resolve(Boolean(accepted));
  }

  function renderConfirm() {
    const dialog = state.confirm;
    if (!dialog.visible) return null;
    return h("div", {
      class: "global-confirm-mask",
      role: "presentation",
      onClick: () => settleConfirm(false),
      onKeydown: event => {
        if (event.key === "Escape") settleConfirm(false);
      },
    }, [
      h("section", {
        class: "global-confirm-panel",
        role: "alertdialog",
        "aria-modal": "true",
        "aria-labelledby": "global-confirm-title",
        "aria-describedby": "global-confirm-message",
        onClick: event => event.stopPropagation(),
      }, [
        h("div", { class: "global-confirm-copy" }, [
          h("h3", { id: "global-confirm-title", class: "global-confirm-title" }, dialog.title),
          h("p", { id: "global-confirm-message", class: "global-confirm-message" }, dialog.message),
        ]),
        h("div", { class: "global-confirm-actions" }, [
          h("button", {
            class: "btn-sm btn-sm-ghost",
            type: "button",
            onClick: () => settleConfirm(false),
          }, dialog.cancelText || "取消"),
          h("button", {
            class: dialog.danger ? "btn-sm global-confirm-danger" : "btn-sm btn-sm-primary",
            type: "button",
            autofocus: true,
            onClick: () => settleConfirm(true),
          }, dialog.confirmText || "确认"),
        ]),
      ]),
    ]);
  }

  function renderUi() {
    render(h("div", { class: "global-ui" }, [
      h("div", { class: "global-toast-stack", "aria-live": "polite" }, state.toasts.map(renderToast)),
      renderLoading(),
      renderConfirm(),
    ]), root);
  }

  function toast(message, type = "") {
    const id = ++toastSeq;
    state.toasts.push({ id, message: String(message || ""), type });
    renderUi();
    toastTimers.set(id, setTimeout(() => removeToast(id), 3200));
    return id;
  }

  function showLoading(options = {}) {
    clearLoadingTimer();
    const startedAt = options.startedAt || Date.now();
    Object.assign(state.loading, {
      visible: true,
      id: options.id || "global-loading",
      title: options.title || "",
      name: options.name || "",
      message: options.message || "",
      elapsedLabel: options.elapsedLabel || "",
      elapsed: options.elapsed || elapsedText(startedAt),
      cancelText: options.cancelText || "",
      cancellable: Boolean(options.cancellable),
      onCancel: options.onCancel || null,
      startedAt,
      timer: null,
    });
    if (state.loading.elapsedLabel) {
      state.loading.timer = setInterval(() => {
        state.loading.elapsed = elapsedText(state.loading.startedAt);
        renderUi();
      }, 500);
    }
    renderUi();
    return state.loading.id;
  }

  function hideLoading(id) {
    if (id && state.loading.id && id !== state.loading.id) return;
    clearLoadingTimer();
    Object.assign(state.loading, {
      visible: false,
      id: "",
      title: "",
      name: "",
      message: "",
      elapsedLabel: "",
      elapsed: "",
      cancelText: "",
      cancellable: false,
      onCancel: null,
      startedAt: 0,
    });
    renderUi();
  }

  function updateLoading(options = {}) {
    Object.assign(state.loading, options);
    if (state.loading.startedAt && state.loading.elapsedLabel) {
      state.loading.elapsed = elapsedText(state.loading.startedAt);
    }
    renderUi();
  }

  function confirm(options = {}) {
    if (state.confirm.visible) settleConfirm(false);
    return new Promise(resolve => {
      Object.assign(state.confirm, {
        visible: true,
        title: String(options.title || "请确认操作"),
        message: String(options.message || ""),
        confirmText: String(options.confirmText || "确认"),
        cancelText: String(options.cancelText || "取消"),
        danger: Boolean(options.danger),
        resolve,
      });
      renderUi();
    });
  }

  Object.assign(uiRegistry, {
    isVue: true,
    toast,
    showLoading,
    hideLoading,
    updateLoading,
    confirm,
  });
  renderUi();
}
