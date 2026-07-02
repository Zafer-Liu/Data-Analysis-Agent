import { appStore, state } from "./app-store.js";
import {
  appendMsg,
  bindBubbleImages,
  clearMessages,
  showStatus,
  updateTokenBar,
} from "../legacy/msg.js";
import { renderMd } from "../legacy/markdown.js";

const baa = globalThis.BAA || {};

if (!baa.dom || !baa.slash) {
  throw new Error("Chat stream runtime dependencies are not ready");
}

export { appStore, state };
export { appendMsg, bindBubbleImages, clearMessages, renderMd, showStatus, updateTokenBar };
export const { $, esc, scrollBottom, scrollReset, hideWelcome, showWelcome } = baa.dom;
export const clearCmd = baa.slash.clearCmd;
