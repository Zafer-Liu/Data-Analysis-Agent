import { $ } from "./dom.js";
import { eventBus } from "./event-bus.js";

const overlayStack = [];
const focusOrigins = new WeakMap();
const FOCUSABLE = [
  "button:not([disabled])",
  "a[href]",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function appState() {
  return globalThis.BAA?.state;
}

function visibleFocusable(dialog) {
  return [...dialog.querySelectorAll(FOCUSABLE)].filter((element) => {
    const style = globalThis.getComputedStyle(element);
    return !element.hidden && style.display !== "none" && style.visibility !== "hidden";
  });
}

function prepareDialog(overlay) {
  const dialog = overlay.querySelector(".modal");
  if (!dialog) return null;

  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("tabindex", "-1");
  if (!dialog.hasAttribute("aria-label") && !dialog.hasAttribute("aria-labelledby")) {
    const title = dialog.querySelector(".modal-title");
    if (title) {
      if (!title.id) title.id = `${overlay.id || "overlay"}-title`;
      dialog.setAttribute("aria-labelledby", title.id);
    }
  }
  return dialog;
}

function syncBackgroundState() {
  const layout = document.querySelector(".layout");
  if (!layout) return;

  layout.inert = overlayStack.length > 0;
  layout.setAttribute("aria-hidden", overlayStack.length > 0 ? "true" : "false");
}

function focusDialog(overlay) {
  const dialog = prepareDialog(overlay);
  if (!dialog) return;

  const target = dialog.querySelector("[autofocus]") || visibleFocusable(dialog)[0] || dialog;
  requestAnimationFrame(() => target.focus({ preventScroll: true }));
}

export function openOverlay(id) {
  const overlay = $(id);
  if (!overlay) return;

  const existingIndex = overlayStack.indexOf(overlay);
  if (existingIndex >= 0) overlayStack.splice(existingIndex, 1);
  focusOrigins.set(overlay, document.activeElement);
  overlayStack.push(overlay);
  overlay.setAttribute("aria-hidden", "false");
  prepareDialog(overlay);
  overlay.classList.add("open");
  syncBackgroundState();
  focusDialog(overlay);

  if (id === "ov-settings" && globalThis.BAA.models) {
    globalThis.BAA.models.loadBuiltinProviders();
  }
  eventBus.emit("overlay:open", { id });
}

export function closeOverlay(id) {
  const overlay = $(id);
  if (!overlay) return;

  overlay.classList.remove("open");
  overlay.setAttribute("aria-hidden", "true");
  const index = overlayStack.lastIndexOf(overlay);
  if (index >= 0) overlayStack.splice(index, 1);
  syncBackgroundState();

  const origin = focusOrigins.get(overlay);
  focusOrigins.delete(overlay);
  if (origin && document.contains(origin) && !origin.closest('[aria-hidden="true"]')) {
    requestAnimationFrame(() => origin.focus({ preventScroll: true }));
  } else if (overlayStack.length > 0) {
    focusDialog(overlayStack.at(-1));
  }
}

document.addEventListener("mousedown", (event) => {
  if (event.target.closest?.(".modal") && appState()) {
    appState()._modalResizing = true;
  }
});

document.addEventListener("mouseup", () => {
  setTimeout(() => {
    if (appState()) appState()._modalResizing = false;
  }, 50);
});

export function closeOutside(event, id) {
  if (appState()?._modalResizing) return;
  if (event.target.id === id) closeOverlay(id);
}

document.addEventListener(
  "keydown",
  (event) => {
    const overlay = overlayStack.at(-1);
    if (!overlay) return;

    if (event.key === "Escape" && overlay.dataset.escapeClose !== "false") {
      event.preventDefault();
      closeOverlay(overlay.id);
      return;
    }
    if (event.key !== "Tab") return;

    const dialog = overlay.querySelector(".modal");
    if (!dialog) return;
    const focusable = visibleFocusable(dialog);
    if (focusable.length === 0) {
      event.preventDefault();
      dialog.focus();
      return;
    }

    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  },
  true,
);

export function toast(message, type = "") {
  if (typeof globalThis.BAA?.ui?.toast === "function") {
    return globalThis.BAA.ui.toast(message, type);
  }

  const element = $("toast");
  if (!element) return undefined;
  element.textContent = message;
  element.className = `toast show${type ? ` ${type}` : ""}`;
  setTimeout(() => {
    element.className = "toast";
  }, 2800);
  return undefined;
}

document.querySelectorAll(".overlay").forEach((overlay) => {
  overlay.setAttribute("aria-hidden", overlay.classList.contains("open") ? "false" : "true");
  prepareDialog(overlay);
});

export const overlay = Object.freeze({
  openOverlay,
  closeOverlay,
  closeOutside,
  toast,
});
