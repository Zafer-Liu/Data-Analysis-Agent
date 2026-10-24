import { dom, esc } from "../core/dom.js";
import { installLegacyBridge } from "../core/legacy-bridge.js";
import { theme } from "../core/theme.js";
import {
  overlay,
  closeOutside,
  closeOverlay,
  debugOverlayState,
  openOverlay,
  toast,
} from "../core/overlay.js";
import { slash } from "../features/slash.js";
import { skills } from "../features/skills.js";
import { models } from "../features/models.js";
import { workspace } from "../features/workspace.js";
import { teams } from "../features/teams.js";
import { ensureUiIsland } from "../features/vue-app.js";

installLegacyBridge("chat");

const baa = globalThis.BAA || {};
baa.dom = dom;
baa.theme = theme;
baa.overlay = overlay;
baa.slash = slash;
baa.skills = skills;
baa.models = models;
baa.workspace = workspace;
baa.teams = teams;
globalThis.BAA = baa;

// Temporary compatibility surface for modules not migrated to ESM yet.
globalThis.esc = esc;
const overlayIslands = Object.freeze({
  "ov-settings": "settings",
  "ov-workspace": "workspace",
  // ov-knowledge and ov-mcp no longer use overlay; islands are loaded via openPanel.
});

// Panel-to-island mapping: openPanel() needs to load the Vue island first.
const panelIslands = Object.freeze({
  knowledge: "knowledge",
  mcp: "mcp",
});

globalThis.openOverlay = async (id) => {
  const island = overlayIslands[id];
  if (island) {
    await ensureUiIsland(island);
    // settings island 是按需加载的，首次加载后需要重新拉取数据填充 providers/customs
    if (id === "ov-settings" && globalThis.BAA?.models?.loadBuiltinProviders) {
      globalThis.BAA.models.loadBuiltinProviders();
    }
  }
  return openOverlay(id);
};

// Ensure the Vue island is loaded before opening a side panel.
globalThis.openSidePanel = async (name) => {
  const island = panelIslands[name];
  if (island) {
    await ensureUiIsland(island);
  }
  globalThis.BAA?.sidebar?.openPanel?.(name);
};
globalThis.closeOverlay = closeOverlay;
globalThis.closeOutside = closeOutside;
globalThis.toast = toast;
globalThis.debugOverlayState = debugOverlayState;
globalThis.clearCmd = slash.clearCmd;
globalThis.fillHint = slash.fillHint;

teams.init();
