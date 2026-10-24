// Ordered compatibility entry for the complete chat application.
// Keep side-effect imports aligned with the former template script order.

// Guard against double-loading: if the browser somehow executes a stale cached
// copy alongside the versioned copy (e.g. Service Worker race, browser cache),
// the second execution is silently skipped to avoid duplicated event listeners
// and overlayStack corruption.
// NOTE: This guard only covers pure top-level module code. All listener
// registration and state manipulation in imported modules runs BEFORE the
// guard can fire, so this is purely a last-resort belt-and-suspenders defence.
// The real fix is Cache-Control: no-store on the HTML entry page (api/__init__.py).
if (globalThis.__baaAppLoaded) {
  throw new Error("[chat-app] duplicate load detected — skipping");
}
globalThis.__baaAppLoaded = true;

import "../legacy/i18n.js";
import "../legacy/state.js";
import "./legacy-core.js";
import "../legacy/markdown.js";
import "./legacy-ui.js";
import "../legacy/msg.js";
import "../legacy/command_handlers.js";
import "../legacy/datasource.js";
import "../legacy/preview.js";
import "./legacy-stream.js";
import "../legacy/app_settings.js";
import "../legacy/job_history.js";
import "../legacy/sessions.js";
import "../legacy/autosave.js";
import "../legacy/update.js";
import "../legacy/checkpoints.js";
import "./legacy-panels.js";
import "../legacy/temp_prompt_panel.js";
import "../legacy/app.js";
