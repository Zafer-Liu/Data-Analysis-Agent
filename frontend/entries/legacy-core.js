import { dom, esc } from "../core/dom.js";
import { installLegacyBridge } from "../core/legacy-bridge.js";
import { theme } from "../core/theme.js";
import { overlay, closeOutside, closeOverlay, openOverlay, toast } from "../core/overlay.js";
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
  "ov-knowledge": "knowledge",
  "ov-mcp": "mcp",
  "ov-workspace": "workspace",
});

globalThis.openOverlay = async (id) => {
  const island = overlayIslands[id];
  if (island) await ensureUiIsland(island);
  return openOverlay(id);
};
globalThis.closeOverlay = closeOverlay;
globalThis.closeOutside = closeOutside;
globalThis.toast = toast;
globalThis.clearCmd = slash.clearCmd;
globalThis.fillHint = slash.fillHint;

teams.init();
