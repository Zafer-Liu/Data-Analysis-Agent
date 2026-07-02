// Compatibility auto-save: silently saves conversation after each AI reply.
// - Debounced 3s after stream ends
// - One file per session (overwrite), never clutters the manual save list
// - Shows "💾 自动保存 HH:MM" in the sidebar status
// - On page load: if session alive → re-render history; if session dead → offer restore banner
import { appendMsg, updateTokenBar } from "./msg.js";
import { renderMd } from "./markdown.js";
import { loadSavedSession } from "./sessions.js";
import { $, hideWelcome } from "../core/dom.js";

  const state = window.BAA.state;

  let _timer      = null;
  let _loadedName = "";   // display name of the loaded conversation
  let _targetFile = "";   // filename to overwrite (so no new entry appears)

  // ── Core save ──────────────────────────────────────────────────────────────
  async function triggerAutosave() {
    if (!state.SID) return;
    try {
      const body = {};
      if (_loadedName) body.name        = _loadedName;
      if (_targetFile) body.target_file = _targetFile;
      const r = await fetch(`/api/session/${state.SID}/autosave`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (d.ok) _updateStatusLabel(d.saved_at);
    } catch (_) { /* silent */ }
  }

  // Called after every stream completion — debounced 3s
  function scheduleAutosave() {
    if (_timer) clearTimeout(_timer);
    _timer = setTimeout(triggerAutosave, 3000);
  }

  // Called when user loads a saved session — overwrite that exact file going forward
  function setLoadedName(name, filename) {
    _loadedName = name     || "";
    _targetFile = filename || "";
  }

  // ── Status label in sidebar ────────────────────────────────────────────────
  function _updateStatusLabel(isoStr) {
    const el = $("autosave-status");
    if (!el) return;
    const time = isoStr ? isoStr.slice(11, 16) : "";
    el.textContent = time ? `自动保存 ${time}` : "已自动保存";
    el.title = isoStr ? `上次自动保存于 ${isoStr.replace("T", " ")}` : "";
    el.classList.remove('hidden');
  }

  // ── On page load ───────────────────────────────────────────────────────────
  // Called after bootstrap sets state.SID.
  // Two scenarios:
  //   A) Session alive (same SID reused) → re-render history from server
  //   B) Session expired / new SID       → offer restore from autosave file
  async function checkAutosaveOnLoad() {
    if (!state.SID) return;

    try {
      const ping = await fetch(`/api/session/${state.SID}/ping`);
      if (ping.ok) {
        const { alive, msg_count } = await ping.json();
        if (alive && msg_count > 0) {
          // Scenario A: session still in memory — re-render its history
          await _restoreFromSession();
          return;
        }
      }
    } catch (_) {}

    // Scenario B: session gone — look for autosave file
    await _checkAutosaveFile();
  }

  // Re-render history from live session (page refresh case)
  async function _restoreFromSession() {
    try {
      const r = await fetch(`/api/session/${state.SID}/load-current`);
      if (!r.ok) return;
      const d = await r.json();
      if (!d.history || !d.history.length) return;

      _renderHistory(d.history);
      window.BAA.sidebar?.setSessionName?.("当前会话", state.loadedSessionFilename || "");

      // Restore token counters
      state.tokenState = {
        promptTokens:  0,
        totalInput:    d.total_input  || 0,
        totalOutput:   d.total_output || 0,
        contextWindow: state.tokenState?.contextWindow || null,
      };
      updateTokenBar();

      // Restore autosave status label
      const ar = await fetch(`/api/session/${state.SID}/autosave`);
      const ad = await ar.json();
      if (ad.exists && ad.saved_at) _updateStatusLabel(ad.saved_at);
    } catch (_) {}
  }

  // Check for an autosave file (session expired case)
  async function _checkAutosaveFile() {
    try {
      const r = await fetch(`/api/session/${state.SID}/autosave`);
      const d = await r.json();
      if (d.exists && d.msg_count > 0) {
        _showRestoreBanner(d);
      }
    } catch (_) {}
  }

  // Render history messages into the chat area
  function _renderHistory(history, _sessionName) {
    if (!history || !history.length) return;
    // Don't render if chat already has messages (e.g. loaded manually)
    if (document.querySelectorAll(".msg").length > 0) return;

    hideWelcome();

    for (const msg of history) {
      if (msg.role === "user") {
        appendMsg("user", msg.content);
      } else if (msg.role === "assistant" && msg.content) {
        const el = appendMsg("assistant", null);
        const bubble = el.querySelector(".msg-bubble");
        bubble.innerHTML = renderMd(msg.content);
        for (const cid of (msg.chart_ids || [])) {
          const wrap = window.BAA.chatStream.buildChartFrame(cid);
          bubble.before(wrap);
        }
      }
    }
  }

  // Show non-intrusive restore banner above chat
  function _showRestoreBanner(meta) {
    if (document.querySelectorAll(".msg").length > 0) return;
    const existing = document.getElementById("autosave-restore-banner");
    if (existing) existing.remove();

    const time = (meta.saved_at || "").slice(0, 16).replace("T", " ");
    const banner = document.createElement("div");
    banner.id = "autosave-restore-banner";
    banner.className = "autosave-banner";
    banner.innerHTML = `
      <span class="autosave-banner-icon">💾</span>
      <span class="autosave-banner-text">
        发现上次未恢复的自动保存（${time}，共 ${meta.msg_count} 条消息）
      </span>
      <button class="autosave-banner-btn autosave-banner-restore" id="autosave-restore-btn">恢复对话</button>
      <button class="autosave-banner-btn autosave-banner-dismiss" id="autosave-dismiss-btn">忽略</button>
    `;

    const chatArea = document.querySelector(".chat-area")
      || document.querySelector(".messages")
      || document.body;
    chatArea.prepend(banner);

    document.getElementById("autosave-restore-btn").addEventListener("click", () => {
      banner.remove();
      loadSavedSession(meta.filename, `自动保存 ${time}`);
    });
    document.getElementById("autosave-dismiss-btn").addEventListener("click", () => {
      banner.remove();
    });
  }

  export { scheduleAutosave, checkAutosaveOnLoad, triggerAutosave, setLoadedName };
