import { state } from "../core/runtime.js";
import { ensureUiIsland } from "./vue-app.js";
import { getUiIsland } from "../core/ui-registry.js";

function text(key, fallback) {
  const value = window.t?.(key);
  return value && value !== key ? value : fallback;
}

let _loaded       = false;
let _templates    = [];
let _projects     = [];
let _currentProject = null;

function _base() {
  return `/api/session/${encodeURIComponent(state.SID)}/business-canvas`;
}

async function _jsonFetch(url, options = {}) {
  const res  = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function _ui()   { return getUiIsland("businessCanvas"); }
function _sync(extra = {}) {
  _ui()?.setState?.({
    templates:      _templates,
    projects:       _projects,
    currentProject: _currentProject,
    ...extra,
  });
}
async function _ensureUi() {
  await ensureUiIsland("businessCanvas");
  return _ui();
}

// ── load all data ──────────────────────────────────────────────

async function loadAll({ force = false } = {}) {
  if (_loaded && !force) { _sync(); return; }
  _sync({ loading: true, error: "" });
  try {
    const [tmpl, proj] = await Promise.all([
      _jsonFetch(`${_base()}/templates`),
      _jsonFetch(`${_base()}/projects`),
    ]);
    _templates = tmpl.templates || [];
    _projects  = proj.projects  || [];
    if (_projects.length) {
      const detail = await _jsonFetch(`${_base()}/projects/${encodeURIComponent(_projects[0].id)}`);
      _currentProject = detail.project || null;
    } else {
      _currentProject = null;
    }
    _loaded = true;
    _sync({ loading: false, error: "" });
    // Auto-load diagram XML if current project has one
    _loadCurrentDiagram();
  } catch (err) {
    _sync({ loading: false, error: String(err.message || err) });
  }
}

function _loadCurrentDiagram() {
  const xml = _currentProject?.diagram_xml || "";
  if (xml) {
    _lastAppliedXml = xml;
    _ui()?.loadXml?.(xml);
  }
}

// ── public: open / close ───────────────────────────────────────

async function open() {
  const ui = await _ensureUi();
  document.body.classList.add("business-canvas-open");
  ui?.setOpen?.(true);
  await loadAll();
  retryPendingSave();
}

function close() {
  document.body.classList.remove("business-canvas-open", "business-canvas-fullscreen");
  _ui()?.setOpen?.(false);
  _ui()?.setFullscreen?.(false);
}

function setFullscreen(enabled) {
  document.body.classList.toggle("business-canvas-fullscreen", !!enabled);
  _ui()?.setFullscreen?.(!!enabled);
}

// ── public: projects ───────────────────────────────────────────

async function createProject(templateId) {
  const template = _templates.find(t => t.id === templateId);
  const lang = (window.getLang?.() || "zh").toLowerCase();
  const fallbackName = lang.startsWith("en")
    ? template?.name_en || template?.name || "Diagram"
    : template?.name || "图表";
  const title    = fallbackName;
  _sync({ saving: true, error: "" });
  try {
    const data = await _jsonFetch(`${_base()}/projects`, {
      method: "POST",
      body:   JSON.stringify({ template_id: templateId, title }),
    });
    _currentProject = data.project || null;
    const proj = await _jsonFetch(`${_base()}/projects`);
    _projects = proj.projects || [];
    _sync({ saving: false, error: "" });
    _loadCurrentDiagram();
  } catch (err) {
    _sync({ saving: false, error: String(err.message || err) });
  }
}

async function selectProject(projectId) {
  _sync({ loading: true, error: "" });
  try {
    const data = await _jsonFetch(`${_base()}/projects/${encodeURIComponent(projectId)}`);
    _currentProject = data.project || null;
    _sync({ loading: false, error: "" });
    _loadCurrentDiagram();
  } catch (err) {
    _sync({ loading: false, error: String(err.message || err) });
  }
}

async function refresh() {
  _loaded = false;
  await loadAll({ force: true });
}

async function deleteProject(projectId) {
  if (!projectId) return;
  try {
    await _jsonFetch(`${_base()}/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
    _projects = _projects.filter(p => p.id !== projectId);
    if (_currentProject?.id === projectId) {
      _currentProject = null;
    }
    _sync();
  } catch (err) {
    _sync({ error: String(err.message || err) });
  }
}

async function renameProject(projectId, newTitle) {
  if (!projectId || !newTitle) return;
  try {
    const data = await _jsonFetch(`${_base()}/projects/${encodeURIComponent(projectId)}`, {
      method: "PATCH",
      body: JSON.stringify({ title: newTitle }),
    });
    const updated = data.project;
    if (updated) {
      _projects = _projects.map(p => p.id === projectId ? { ...p, title: updated.title, updated_at: updated.updated_at } : p);
      if (_currentProject?.id === projectId) {
        _currentProject = { ..._currentProject, title: updated.title };
      }
    }
    _sync();
  } catch (err) {
    _sync({ error: String(err.message || err) });
  }
}

// ── public: diagram XML ────────────────────────────────────────

// Debounced autosave: draw.io fires autosave on every change; large diagrams
// can be hundreds of KB, so coalesce rapid edits into one PATCH.
const AUTOSAVE_DEBOUNCE_MS = 1500;
let _saveTimer = null;
let _pendingSave = null; // { projectId, xml }
let _lastAppliedXml = "";
let _saveFailed = false;

function _scheduleSave(projectId, xml) {
  _pendingSave = { projectId, xml };
  if (_saveTimer) clearTimeout(_saveTimer);
  _saveTimer = setTimeout(_flushSave, AUTOSAVE_DEBOUNCE_MS);
}

async function _flushSave() {
  _saveTimer = null;
  const pending = _pendingSave;
  if (!pending) return;
  _pendingSave = null;
  try {
    await _jsonFetch(
      `${_base()}/projects/${encodeURIComponent(pending.projectId)}/diagram`,
      { method: "PATCH", body: JSON.stringify({ diagram_xml: pending.xml, actor_type: "user" }) },
    );
    if (_saveFailed) {
      _saveFailed = false;
      _sync({ error: "" });
    }
  } catch (err) {
    _saveFailed = true;
    // Keep the newest xml so a later successful save/flush wins.
    _pendingSave = pending;
    _sync({
      error: text(
        "canvas.autosave_failed",
        "画布自动保存失败，最新内容仍保留在编辑器中，重新打开画布时会自动重试。"
      ) + (err?.message ? `（${String(err.message)}）` : ""),
    });
  }
}

// Called by the draw.io iframe onAutosave to persist changes
function saveDiagramXml(projectId, xml) {
  if (!projectId || !xml) return;
  _lastAppliedXml = xml;
  _scheduleSave(projectId, xml);
}

// Retry a failed autosave as soon as the canvas panel is interacted with again.
async function retryPendingSave() {
  if (_saveFailed && _pendingSave) await _flushSave();
}

// Called by SSE canvas_event → loadDiagramXml
async function loadDiagramXml(xml) {
  // Skip redundant reloads of identical content (also avoids clobbering
  // in-flight edits with the same xml that was just autosaved back).
  if (!xml || xml === _lastAppliedXml) return;
  // Never overwrite the editor while an autosave failed and the newer
  // local content is still unsaved — the user's edits win over the push.
  if (_saveFailed && _pendingSave) {
    _sync({
      error: text("canvas.push_skipped", "画布有未保存的本地编辑，已跳过远端更新。"),
    });
    return;
  }
  // Ensure island is mounted (open() may not have been called yet)
  const ui = await _ensureUi();
  _lastAppliedXml = xml;
  ui?.loadXml?.(xml);
}

export const businessCanvas = Object.freeze({
  open,
  close,
  setFullscreen,
  loadAll,
  refresh,
  createProject,
  selectProject,
  deleteProject,
  renameProject,
  saveDiagramXml,
  loadDiagramXml,
  retryPendingSave,
});
