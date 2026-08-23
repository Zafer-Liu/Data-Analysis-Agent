import { $, state } from "../core/runtime.js";
import * as datasource from "../legacy/datasource.js";

function text(key, fallback) {
  const value = window.t?.(key);
  return value && value !== key ? value : fallback;
}

// ── Active panel singleton ─────────────────────────────────────────
// Only one of sb-panel-skills / sb-panel-knowledge / sb-panel-mcp can be
// open at any time. Tracked here so openPanel / closePanel can close
// the currently open one first without needing an argument.
let _activePanel = null; // "skills" | "knowledge" | "mcp" | null

// ── Sidebar nav highlight ──────────────────────────────────────────
function setSidebarNav(nav = "agent") {
  document.querySelectorAll(".sb-nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.sidebarNav === nav);
  });
}

// ── Open / close independent panel (skills / knowledge / mcp) ─────

function openPanel(name) {
  // Close the current panel if it is a different one.
  if (_activePanel && _activePanel !== name) {
    _closePanel(_activePanel, { restoreNav: false });
  }
  // If clicking the same panel that is already open, close it (toggle).
  if (_activePanel === name) {
    _closePanel(name);
    return;
  }
  const el = document.getElementById(`sb-panel-${name}`);
  if (!el) return;
  _activePanel = name;
  el.classList.remove("collapsed");
  setSidebarNav(name);
}

function _closePanel(name, { restoreNav = true } = {}) {
  const el = document.getElementById(`sb-panel-${name}`);
  if (el) el.classList.add("collapsed");
  if (_activePanel === name) _activePanel = null;
  if (restoreNav) setSidebarNav("agent");
  // Cascade-close skill drawer when skills panel closes
  if (name === "skills") {
    const drawer = document.getElementById("skill-modal-overlay");
    if (drawer && !drawer.classList.contains("hidden")) {
      drawer.classList.add("hidden");
      // Move drawer back to body and clear inline style
      if (drawer.parentElement !== document.body) {
        drawer.style.left = "";
        document.body.appendChild(drawer);
      }
    }
  }
}

function closePanel(name) {
  _closePanel(name);
}

function initPanelKeyClose() {
  if (globalThis.__baaPanelKeyCloseRegistered) return;
  globalThis.__baaPanelKeyCloseRegistered = true;
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && _activePanel) {
      _closePanel(_activePanel);
    }
  });
}

// ── KB inline form open / close ────────────────────────────────────

function openKbInlineForm() {
  const form = document.getElementById("kb-inline-form");
  if (form) form.classList.remove("collapsed");
}

function closeKbInlineForm() {
  const form = document.getElementById("kb-inline-form");
  if (form) form.classList.add("collapsed");
}

// ── Sessions / Sources drawer (legacy) ────────────────────────────

function setDrawerTab(tab = "sessions") {
  const drawer = $("sb-drawer");
  if (!drawer) return;

  drawer.dataset.drawerPanel = tab;
  const title = $("sb-drawer-title");
  if (title) title.textContent = tab === "sources" ? text("sidebar.drawer.sources", "数据链接") : text("sidebar.drawer.sessions", "会话文件");

  document.querySelectorAll(".sb-drawer-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.drawerTab === tab);
  });
  document.querySelectorAll(".sb-drawer-page").forEach((page) => {
    page.classList.toggle("active", page.dataset.drawerPage === tab);
  });

  if (tab === "sources") datasource.loadWarehouseList();
}

function openSidebarDrawer(tab = "sessions") {
  const drawer = $("sb-drawer");
  if (!drawer) return;
  // Collapse any open panel first.
  if (_activePanel) _closePanel(_activePanel, { restoreNav: false });
  setDrawerTab(tab);
  drawer.classList.remove("collapsed");
  if (tab === "sessions") setSidebarNav("history");
}

function closeSidebarDrawer() {
  const drawer = $("sb-drawer");
  if (!drawer) return;
  drawer.classList.add("collapsed");
}

// ── Focus mode ────────────────────────────────────────────────────

function toggleFocusMode() {
  const enabled = !document.body.classList.contains("focus-mode");
  document.body.classList.toggle("focus-mode", enabled);
  const button = $("btn-focus-mode");
  if (!button) return;
  button.classList.toggle("active", enabled);
  button.textContent = enabled ? text("sidebar.focus.exit", "↩ 退出专注") : text("sidebar.focus.enter", "⛶ 专注对话");
  button.title = enabled ? text("sidebar.focus.restore_title", "恢复完整界面") : text("sidebar.focus.enter_title", "隐藏左侧面板，专注对话");
}

document.addEventListener("langchange", () => {
  const drawer = $("sb-drawer");
  if (drawer && !drawer.classList.contains("collapsed")) {
    setDrawerTab(drawer.dataset.drawerPanel || "sessions");
  }
  const focusButton = $("btn-focus-mode");
  if (focusButton) {
    const enabled = document.body.classList.contains("focus-mode");
    focusButton.textContent = enabled ? text("sidebar.focus.exit", "↩ 退出专注") : text("sidebar.focus.enter", "⛶ 专注对话");
    focusButton.title = enabled ? text("sidebar.focus.restore_title", "恢复完整界面") : text("sidebar.focus.enter_title", "隐藏左侧面板，专注对话");
  }
});
// ── Add-source dropdown ───────────────────────────────────────────

function closeAddSrcDropdown() {
  const dropdown = $("sb-add-src");
  if (!dropdown) return;
  dropdown.classList.remove("open");
  const button = dropdown.querySelector(".sb-btn-primary");
  if (button) button.setAttribute("aria-expanded", "false");
}

function toggleAddSrc() {
  const dropdown = $("sb-add-src");
  if (!dropdown) return;
  const button = dropdown.querySelector(".sb-btn-primary");
  const open = dropdown.classList.toggle("open");
  if (button) button.setAttribute("aria-expanded", String(open));
}

function openDataSource() {
  openSidebarDrawer("sources");
  if (state.srcConnected) return;

  const dropdown = $("sb-add-src");
  if (!dropdown || dropdown.classList.contains("open")) return;
  dropdown.classList.add("open");
  const button = dropdown.querySelector(".sb-btn-primary");
  if (button) button.setAttribute("aria-expanded", "true");
}

function initAddSourceDropdown() {
  if (globalThis.__baaAddSourceDropdownRegistered) return;
  globalThis.__baaAddSourceDropdownRegistered = true;

  document.addEventListener("click", (event) => {
    const dropdown = $("sb-add-src");
    if (!dropdown || !dropdown.classList.contains("open")) return;

    if (event.target.closest(".sb-dropdown-item")) {
      setTimeout(closeAddSrcDropdown, 0);
      return;
    }
    if (!dropdown.contains(event.target)) closeAddSrcDropdown();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeAddSrcDropdown();
  });
}

export const sidebar = Object.freeze({
  closeAddSrcDropdown,
  closePanel,
  closeSidebarDrawer,
  closeKbInlineForm,
  initAddSourceDropdown,
  initPanelKeyClose,
  openDataSource,
  openKbInlineForm,
  openPanel,
  openSidebarDrawer,
  setDrawerTab,
  setSidebarNav,
  toggleAddSrc,
  toggleFocusMode,
});
