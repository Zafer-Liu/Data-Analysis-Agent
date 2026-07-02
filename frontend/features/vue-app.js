import { mountChatUi } from "./ui/chat-ui.js";
import { mountGlobalUi } from "./ui/global-ui.js";
import { getUiIsland } from "../core/ui-registry.js";

const islandLoaders = Object.freeze({
  jobHistory: async () => {
    const { mountJobHistoryUi } = await import("./ui/job-history-ui.js");
    mountJobHistoryUi();
  },
  knowledge: async () => {
    const { mountKnowledgeUi } = await import("./ui/knowledge-ui.js");
    mountKnowledgeUi();
  },
  mcp: async () => {
    const { mountMcpUi } = await import("./ui/mcp-ui.js");
    mountMcpUi();
  },
  settings: async () => {
    const { mountSettingsUi } = await import("./ui/settings-ui.js");
    mountSettingsUi();
  },
  workspace: async () => {
    const { mountWorkspaceUi } = await import("./ui/workspace-ui.js");
    mountWorkspaceUi();
  },
});

const pendingIslands = new Map();

export async function ensureUiIsland(name) {
  const mounted = getUiIsland(name);
  if (mounted) return mounted;
  const loader = islandLoaders[name];
  if (!loader) return null;

  if (!pendingIslands.has(name)) {
    pendingIslands.set(name, loader().finally(() => pendingIslands.delete(name)));
  }
  await pendingIslands.get(name);
  return getUiIsland(name);
}

// Only first-paint surfaces mount eagerly. Modal and history islands are
// loaded by ensureUiIsland() on first use and become independent Vite chunks.
export function mountVueApp() {
  mountGlobalUi();
  mountChatUi();
}
