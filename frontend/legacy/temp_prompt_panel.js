/* Temporary Prompt compatibility UI
 *
 * A free-form instruction the user sets for the CURRENT conversation only.
 * When enabled, the backend appends it to the system prompt on every turn of
 * this session (see api/chat.py + agent/prompts.py:build_temp_prompt_section).
 *
 * Depends on:  openOverlay / closeOverlay / toast  (dist/core.js)
 *              window.BAA.state.SID                 (modules/state.js)
 * API routes:  /api/session/<sid>/temp-prompt*      (api/knowledge.py)
 */

// ── State ─────────────────────────────────────────────────────────────────────

const _tp = {
  enabled:   false,
  maxChars:  4000,
  loaded:    false,
};

function _tpSID() {
  return (window.BAA && window.BAA.state && window.BAA.state.SID) || "";
}

function _tpToast(msg, kind) {
  if (window.toast) window.toast(msg, kind);
}

// ── Load current state from the session ───────────────────────────────────────

async function tpLoad() {
  const sid = _tpSID();
  const statusEl = document.getElementById("tp-status");
  if (!sid) {
    if (statusEl) statusEl.textContent = "尚未建立会话，发送一条消息后再设置。";
    return;
  }
  try {
    const data = await fetch(`/api/session/${sid}/temp-prompt`).then(r => r.json());
    _tp.enabled  = !!data.enabled;
    _tp.maxChars = data.max_chars || 4000;
    _tp.loaded   = true;

    const ta = document.getElementById("tp-textarea");
    if (ta) {
      ta.value = data.temp_prompt || "";
      ta.maxLength = _tp.maxChars;
    }
    tpRenderStatus();
    tpUpdateCount();
  } catch (e) {
    if (statusEl) statusEl.textContent = `加载失败：${e.message}`;
  }
}

// ── Status bar + char counter ─────────────────────────────────────────────────

function tpRenderStatus() {
  const statusEl = document.getElementById("tp-status");
  const toggleBtn = document.getElementById("tp-toggle-btn");
  const ta = document.getElementById("tp-textarea");
  const hasText = !!(ta && ta.value.trim());

  if (statusEl) {
    if (_tp.enabled && hasText) {
      statusEl.innerHTML =
        '<span class="tp-dot tp-dot-on"></span>已启用 · 本次会话每轮对话都会带上此临时指令';
    } else if (hasText) {
      statusEl.innerHTML =
        '<span class="tp-dot tp-dot-off"></span>已保存但未启用 · 点击「启用」生效';
    } else {
      statusEl.innerHTML =
        '<span class="tp-dot tp-dot-off"></span>未设置 · 输入指令并保存后可启用';
    }
  }
  if (toggleBtn) {
    toggleBtn.textContent = _tp.enabled ? "停用" : "启用";
    toggleBtn.disabled = !hasText;
  }
  // Reflect enabled state on the sidebar entry button so the user notices it.
  const entry = document.getElementById("temp-prompt-entry");
  if (entry) entry.classList.toggle("tp-entry-active", _tp.enabled && hasText);
}

function tpUpdateCount() {
  const ta = document.getElementById("tp-textarea");
  const counter = document.getElementById("tp-count");
  if (ta && counter) counter.textContent = `${ta.value.length} / ${_tp.maxChars}`;
  // Live status refresh (e.g. when the box is emptied)
  tpRenderStatus();
}

// ── Save (raw or LLM-refined) ─────────────────────────────────────────────────

async function tpSave(refine) {
  const sid = _tpSID();
  if (!sid) { _tpToast("尚未建立会话", "err"); return; }

  const ta = document.getElementById("tp-textarea");
  const text = (ta && ta.value) || "";
  const provider = _tpCurrentProvider();

  const saveBtn   = document.getElementById("tp-save-btn");
  const refineBtn = document.getElementById("tp-refine-btn");
  if (saveBtn)   saveBtn.disabled = true;
  if (refineBtn) refineBtn.disabled = true;
  const statusEl = document.getElementById("tp-status");
  if (refine && statusEl) statusEl.textContent = "正在用模型整理指令…";

  try {
    const res = await fetch(`/api/session/${sid}/temp-prompt`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ text, raw: !refine, provider }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "保存失败");

    _tp.enabled = !!data.enabled;
    if (ta && typeof data.temp_prompt === "string") ta.value = data.temp_prompt;
    tpRenderStatus();
    tpUpdateCount();

    if (data.warning) _tpToast(data.warning, "err");
    else _tpToast(text.trim() ? "已保存并启用" : "已清空", "ok");
  } catch (e) {
    _tpToast(`保存失败：${e.message}`, "err");
    tpRenderStatus();
  } finally {
    if (saveBtn)   saveBtn.disabled = false;
    if (refineBtn) refineBtn.disabled = false;
  }
}

// Best-effort: grab the model-selector value so the refine pass uses the same
// provider the user picked for chat. Falls back to backend default if absent.
function _tpCurrentProvider() {
  const sel = document.getElementById("model-sel");
  return sel ? (sel.value || "") : "";
}

// ── Toggle enabled ────────────────────────────────────────────────────────────

async function tpToggle() {
  const sid = _tpSID();
  if (!sid) { _tpToast("尚未建立会话", "err"); return; }
  try {
    const res  = await fetch(`/api/session/${sid}/temp-prompt/toggle`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "操作失败");
    _tp.enabled = !!data.enabled;
    tpRenderStatus();
    if (data.warning) _tpToast(data.warning, "err");
    else _tpToast(_tp.enabled ? "临时指令已启用" : "临时指令已停用", "ok");
  } catch (e) {
    _tpToast(`操作失败：${e.message}`, "err");
  }
}

// ── Clear ─────────────────────────────────────────────────────────────────────

function tpClear() {
  const ta = document.getElementById("tp-textarea");
  if (ta) ta.value = "";
  tpUpdateCount();
  // Persist the cleared state immediately (raw save of empty string disables it).
  tpSave(false);
}

// ── Init: load state when the modal opens ─────────────────────────────────────

const _tpOrigOpenOverlay = window.openOverlay;
window.openOverlay = function (id, ...rest) {
  if (id === "ov-temp-prompt") tpLoad();
  if (_tpOrigOpenOverlay) _tpOrigOpenOverlay(id, ...rest);
};

async function tpOpenWithText(text = "") {
  await tpLoad();
  if (_tpOrigOpenOverlay) _tpOrigOpenOverlay("ov-temp-prompt");
  const ta = document.getElementById("tp-textarea");
  if (ta && text) ta.value = text;
  tpUpdateCount();
}

// Expose on BAA namespace; window.tp* aliases kept for any HTML handlers.
const baa = globalThis.BAA || {};
baa.tempPrompt = Object.freeze({ tpSave, tpToggle, tpClear, tpUpdateCount, tpOpenWithText });
globalThis.BAA = baa;
