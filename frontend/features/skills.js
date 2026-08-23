// Independent Skill picker. Skills never enter the slash-command catalog.
import { $, state } from "../core/runtime.js";
  const SKILLS = [];

  // ── Available tools (loaded from API) ──
  let AVAILABLE_TOOLS = [];
  const _selectedTools = new Set();

  async function _loadTools() {
    if (AVAILABLE_TOOLS.length) return AVAILABLE_TOOLS;
    try {
      const r = await fetch("/api/skills/tools");
      const d = await r.json();
      if (d.ok && Array.isArray(d.tools)) AVAILABLE_TOOLS = d.tools;
    } catch (_) { /* fallback: empty list */ }
    return AVAILABLE_TOOLS;
  }

  function sourceLabel(source) {
    return ({ builtin: "内置", user: "个人", workspace: "工作目录", workflow: "Workflow" })[source] || source || "内置";
  }

  function displaySkillName(skill) {
    const raw = skill?.display_name || skill?.name || "Skill";
    // Capitalize each word: "ab-test-analysis" → "Ab-Test-Analysis"
    return raw.replace(/(^|[-\s])([a-z])/g, (_, sep, ch) => sep + ch.toUpperCase());
  }

  function esc(value) {
    return String(value || "").replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[ch]);
  }

  function render(filter = "") {
    const list = $("skill-picker-list");
    if (!list) return;
    const term = String(filter || "").trim().toLowerCase();
    const matched = SKILLS.filter(skill => !term
      || skill.name.toLowerCase().includes(term)
      || String(skill.description || "").toLowerCase().includes(term));
    list.innerHTML = "";
    if (!matched.length) {
      list.innerHTML = `<div class="skill-picker-empty">${esc(t("skills.empty"))}</div>`;
      return;
    }
    matched.forEach((skill, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `skill-picker-item${index === state.skillPickerIndex ? " active" : ""}`;
      button.dataset.skill = skill.name;
      button.innerHTML = `
        <span class="skill-picker-icon skill-picker-icon--letter">${esc((skill.display_name || skill.name || "S")[0].toUpperCase())}</span>
        <span class="skill-picker-copy">
          <strong title="${esc(displaySkillName(skill))}">${esc(displaySkillName(skill))}</strong>
          <small>${esc(skill.description || displaySkillName(skill))}</small>
        </span>
        <span class="skill-picker-actions">
          <span class="skill-picker-source">${esc(sourceLabel(skill.source))}</span>
          <span class="skill-picker-view" data-skill-view="${esc(skill.name)}" title="查看/编辑">📝</span>
        </span>`;
      button.addEventListener("click", (e) => {
        if (e.target.closest("[data-skill-view]")) {
          e.preventDefault();
          e.stopPropagation();
          openSkillModal(skill.name);
          return;
        }
        selectSkill(skill.name);
      });
      list.appendChild(button);
    });
  }

  async function loadSkills() {
    try {
      const suffix = state.SID ? `?sid=${encodeURIComponent(state.SID)}` : "";
      const response = await fetch(`/api/skills${suffix}`);
      if (!response.ok) throw new Error(`Skill catalog failed (${response.status})`);
      const payload = await response.json();
      SKILLS.splice(0, SKILLS.length, ...(payload.skills || []));
      render($("skill-picker-search")?.value || "");
    } catch (error) {
      console.warn("[BAA] skills unavailable:", error);
      SKILLS.splice(0, SKILLS.length);
      render();
    }
  }

  async function open() {
    window.BAA.slash?.closeSlashPopup?.();
    await loadSkills();
    if (window.BAA?.sidebar?.openPanel) {
      window.BAA.sidebar.openPanel("skills");
    }
    const search = $("skill-picker-search");
    if (search) {
      search.value = "";
      render();
      requestAnimationFrame(() => search.focus());
    }
  }

  function close() {
    // Close skill drawer first if open
    const overlay = document.getElementById("skill-modal-overlay");
    if (overlay && !overlay.classList.contains("hidden")) {
      closeSkillModal();
    }
    if (window.BAA?.sidebar?.closePanel) {
      window.BAA.sidebar.closePanel("skills");
    }
  }
  function isOpen() {
    const el = document.getElementById("sb-panel-skills");
    return el ? !el.classList.contains("collapsed") : false;
  }

  function selectSkill(name) {
    const skill = SKILLS.find(item => item.name === name) || { name, icon: "🧩" };
    window.BAA.slash?.clearCmd?.();
    state.activeSkill = skill.name;
    $("skill-badge-text").textContent = displaySkillName(skill);
    $("skill-badge")?.classList.add("show");
    if (window.BAA?.sidebar?.closePanel) window.BAA.sidebar.closePanel("skills");
    $("msg-input")?.focus();
    window.BAA.chatStream?.syncSendButton?.();
  }

  function clearSkill() {
    state.activeSkill = "";
    $("skill-badge")?.classList.remove("show");
  }

  /**
   * Show matched skills from auto-RAG retrieval as a brief toast.
   * Called when backend yields `skill_matched` SSE event.
   */
  function showMatchedSkills(skills) {
    if (!Array.isArray(skills) || skills.length === 0) return;
    const names = skills.map(displaySkillName).join(", ");
    window.BAA?.ui?.toast?.(`匹配到 Skill: ${names}`, "info");
  }

  /**
   * Activate a skill badge from backend SSE `skill_activated` event.
   * This is called when the LLM auto-loads a skill via load_analysis_skill tool.
   */
  function activateSkill(name) {
    if (!name) return;
    const skill = SKILLS.find(item => item.name === name) || { name, icon: "🧩" };
    state.activeSkill = skill.name;
    $("skill-badge-text").textContent = displaySkillName(skill);
    $("skill-badge")?.classList.add("show");
  }

  function onSearch(event) {
    state.skillPickerIndex = 0;
    render(event.target.value);
  }

  function onKeyDown(event) {
    const items = [...document.querySelectorAll("#skill-picker-list .skill-picker-item")];
    if (event.key === "Escape") { event.preventDefault(); close(); return; }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      state.skillPickerIndex = Math.max(0, Math.min(items.length - 1, state.skillPickerIndex + delta));
      render(event.currentTarget.value);
      document.querySelectorAll("#skill-picker-list .skill-picker-item")[state.skillPickerIndex]
        ?.scrollIntoView({ block: "nearest" });
      return;
    }
    if (event.key === "Enter" && items[state.skillPickerIndex]) {
      event.preventDefault();
      selectSkill(items[state.skillPickerIndex].dataset.skill);
    }
  }

  // ── Skill detail / CRUD modal ──────────────────────────────────

  let _editingSkill = null;  // null = creating new; string = editing existing name

  async function openSkillModal(name) {
    _editingSkill = name || null;
    const overlay = $("skill-modal-overlay");
    const title = $("skill-modal-title");
    const nameInput = $("skill-form-name");
    const descInput = $("skill-form-desc");
    const iconInput = $("skill-form-icon");
    const toolsInput = $("skill-form-tools");
    const toolsTrigger = $("skill-form-tools-trigger");
    const promptArea = $("skill-form-prompt");
    const rawView = $("skill-form-raw-view");
    const msgEl = $("skill-form-msg");
    const saveBtn = $("skill-save-btn");
    const deleteBtn = $("skill-delete-btn");
    msgEl.textContent = "";

    // Move drawer inside sidebar-hub and position it at panel's right edge
    const hub = document.getElementById("app-sidebar");
    const panel = document.getElementById("sb-panel-skills");
    if (hub && overlay.parentElement !== hub) {
      hub.appendChild(overlay);
    }
    // Dynamically set left to sidebar width + panel width (340px)
    if (panel) {
      const sidebarWidth = hub ? hub.querySelector(".sb-main")?.offsetWidth || 280 : 280;
      overlay.style.left = (sidebarWidth + 340) + "px";
    }

    if (name) {
      // Fetch skill detail
      try {
        const suffix = state.SID ? `?sid=${encodeURIComponent(state.SID)}` : "";
        const r = await fetch(`/api/skills/${encodeURIComponent(name)}${suffix}`);
        const d = await r.json();
        if (!r.ok || !d.ok) throw new Error(d.error || "Failed to load skill");
        const sk = d.skill;
        title.textContent = sk.readonly ? `Skill: ${sk.name}` : `编辑 Skill: ${sk.name}`;
        nameInput.value = sk.name;
        nameInput.disabled = false;
        descInput.value = sk.description;
        iconInput.value = sk.icon || "";
        setToolsValue(sk.allowed_tools || []);
        promptArea.value = sk.raw || "";
        if (sk.readonly) {
          // Builtin: show raw content, disable editing
          nameInput.disabled = true;
          descInput.disabled = true;
          iconInput.disabled = true;
          toolsTrigger.style.pointerEvents = "none";
          toolsTrigger.style.opacity = ".5";
          promptArea.disabled = true;
          saveBtn.classList.add("hidden");
          deleteBtn.classList.add("hidden");
        } else {
          nameInput.disabled = false;
          descInput.disabled = false;
          iconInput.disabled = false;
          toolsTrigger.style.pointerEvents = "";
          toolsTrigger.style.opacity = "";
          promptArea.disabled = false;
          saveBtn.classList.remove("hidden");
          deleteBtn.classList.remove("hidden");
        }
      } catch (err) {
        msgEl.textContent = String(err.message || err);
        msgEl.className = "skill-form-msg err";
      }
    } else {
      // New skill
      title.textContent = "新建自定义 Skill";
      nameInput.value = "";
      nameInput.disabled = false;
      descInput.value = "";
      descInput.disabled = false;
      iconInput.value = "";
      iconInput.disabled = false;
      toolsTrigger.style.pointerEvents = "";
      toolsTrigger.style.opacity = "";
      setToolsValue([]);
      promptArea.value = "";
      promptArea.disabled = false;
      saveBtn.classList.remove("hidden");
      deleteBtn.classList.add("hidden");
    }

    overlay.classList.remove("hidden");
    // Auto-size prompt textarea to fill remaining drawer space
    _resizePromptTextarea();
    // Refresh counters after values are set
    requestAnimationFrame(() => {
      updateCounter($("skill-form-desc"));
      updateCounter($("skill-form-prompt"));
    });
  }

  function closeSkillModal() {
    const overlay = $("skill-modal-overlay");
    overlay?.classList.add("hidden");
    _editingSkill = null;
    // Move drawer back to body and clear inline style
    if (overlay && overlay.parentElement !== document.body) {
      overlay.style.left = "";
      document.body.appendChild(overlay);
    }
  }

  // ── Tools multiselect ──
  function _renderToolOptions() {
    const container = $("skill-form-tools-options");
    container.innerHTML = "";
    AVAILABLE_TOOLS.forEach(t => {
      const div = document.createElement("div");
      div.className = "skill-tool-option" + (_selectedTools.has(t.name) ? " checked" : "");
      div.dataset.tool = t.name;
      div.innerHTML = `<span class="skill-tool-checkbox"><svg width="11" height="11" viewBox="0 0 11 11" fill="none"><path d="M2 5.5L4.5 8L9 3" stroke="#fff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span class="skill-tool-name">${t.name}</span><span class="skill-tool-cat">${t.cat}</span>`;
      div.addEventListener("click", () => {
        if (_selectedTools.has(t.name)) _selectedTools.delete(t.name);
        else _selectedTools.add(t.name);
        div.classList.toggle("checked");
        _updateToolsTrigger();
      });
      container.appendChild(div);
    });
  }

  function _updateToolsTrigger() {
    const label = $("skill-form-tools-label");
    const n = _selectedTools.size;
    if (n === 0) {
      label.textContent = "全部允许";
    } else if (n <= 3) {
      label.textContent = [..._selectedTools].join(", ");
    } else {
      label.textContent = `已选 ${n} 项`;
    }
    // sync hidden input
    $("skill-form-tools").value = [..._selectedTools].join(", ");
  }

  async function setToolsValue(toolsArr) {
    _selectedTools.clear();
    (toolsArr || []).forEach(t => _selectedTools.add(t));
    await _loadTools();
    _renderToolOptions();
    _updateToolsTrigger();
  }

  function _wireToolsMultiselect() {
    const wrapper = $("skill-form-tools-wrapper");
    const trigger = $("skill-form-tools-trigger");
    const dropdown = $("skill-form-tools-dropdown");
    const search = $("skill-form-tools-search");

    trigger.addEventListener("click", (e) => {
      e.stopPropagation();
      const isOpen = !dropdown.classList.contains("hidden");
      if (isOpen) { _closeToolsDropdown(); }
      else {
        // Move dropdown to <body> to escape drawer's transform/overflow
        document.body.appendChild(dropdown);
        dropdown.classList.remove("hidden");
        wrapper.classList.add("open");
        search.value = "";
        _filterToolOptions("");
        // Position dropdown below the trigger using fixed coordinates
        const r = trigger.getBoundingClientRect();
        dropdown.style.left = r.left + "px";
        dropdown.style.top = (r.bottom + 4) + "px";
        dropdown.style.width = r.width + "px";
        search.focus();
      }
    });
    trigger.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); trigger.click(); }
      if (e.key === "Escape") _closeToolsDropdown();
    });
    search.addEventListener("input", () => _filterToolOptions(search.value));
    // Prevent clicks inside dropdown from bubbling to document handlers
    dropdown.addEventListener("click", (e) => e.stopPropagation());
    search.addEventListener("click", (e) => e.stopPropagation());
    document.addEventListener("click", (e) => {
      if (!wrapper.contains(e.target) && !dropdown.contains(e.target)) _closeToolsDropdown();
    });
  }

  function _closeToolsDropdown() {
    const dd = $("skill-form-tools-dropdown");
    dd.classList.add("hidden");
    $("skill-form-tools-wrapper").classList.remove("open");
    // Move dropdown back into wrapper for clean state
    $("skill-form-tools-wrapper").appendChild(dd);
  }

  function _filterToolOptions(query) {
    const q = query.trim().toLowerCase();
    $("skill-form-tools-options").querySelectorAll(".skill-tool-option").forEach(el => {
      const name = el.dataset.tool.toLowerCase();
      el.classList.toggle("hidden", q && !name.includes(q));
    });
  }

  // ── Auto-size prompt textarea to fill remaining drawer space ──
  function _resizePromptTextarea() {
    const body = document.querySelector('.skill-drawer-body');
    const textarea = document.getElementById('skill-form-prompt');
    const promptSection = document.querySelector('.skill-form-section-prompt');
    if (!body || !textarea || !promptSection) return;
    const bodyStyle = getComputedStyle(body);
    const padTop = parseFloat(bodyStyle.paddingTop) || 0;
    const padBottom = parseFloat(bodyStyle.paddingBottom) || 0;
    // Sum height of all siblings before the prompt section
    let usedHeight = padTop;
    for (const child of body.children) {
      if (child === promptSection) break;
      usedHeight += child.offsetHeight;
      const cs = getComputedStyle(child);
      usedHeight += (parseFloat(cs.marginTop) || 0) + (parseFloat(cs.marginBottom) || 0);
    }
    // The prompt section itself: title + counter consume space
    const title = promptSection.querySelector('.skill-form-section-title');
    const counter = document.querySelector('[data-counter-for="skill-form-prompt"]');
    const sectionTitleH = title ? title.offsetHeight : 17;
    const counterH = counter ? counter.offsetHeight : 18;
    const promptSectionPadBottom = parseFloat(getComputedStyle(promptSection).paddingBottom) || 10;
    const rowGap = 6;
    const available = body.offsetHeight - usedHeight - padBottom - sectionTitleH - promptSectionPadBottom - counterH - rowGap;
    textarea.style.height = Math.max(80, Math.floor(available)) + 'px';
    textarea.style.flex = 'none';
  }

  // ── Character counter ──
  function updateCounter(input) {
    const counter = document.querySelector(`[data-counter-for="${input.id}"]`);
    if (!counter) return;
    const max = input.maxLength;
    const len = [...input.value].length;
    if (input.tagName === "TEXTAREA") {
      counter.textContent = `${len} 字`;
      counter.classList.toggle("warn", len > 2000);
    } else if (max > 0) {
      counter.textContent = `${len} / ${max}`;
      counter.classList.toggle("warn", len > max * 0.8);
    }
  }

  function _wireCounters() {
    ["skill-form-desc", "skill-form-prompt"].forEach(id => {
      const el = $(id);
      el?.addEventListener("input", () => updateCounter(el));
    });
  }

  async function saveSkill() {
    const msgEl = $("skill-form-msg");
    msgEl.textContent = "";
    const name = $("skill-form-name").value.trim();
    const description = $("skill-form-desc").value.trim();
    const icon = $("skill-form-icon").value.trim() || "🧩";
    const allowed_tools = [..._selectedTools];
    const prompt = $("skill-form-prompt").value.trim();

    if (!name || !description || !prompt) {
      msgEl.textContent = "名称、描述和提示词不能为空。";
      msgEl.className = "skill-form-msg err";
      return;
    }

    const body = { name, description, icon, prompt, allowed_tools };
    const suffix = state.SID ? `?sid=${encodeURIComponent(state.SID)}` : "";

    try {
      let r, d;
      if (_editingSkill) {
        r = await fetch(`/api/skills/${encodeURIComponent(_editingSkill)}${suffix}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } else {
        r = await fetch(`/api/skills`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }
      d = await r.json();
      if (!r.ok || !d.ok) throw new Error(d.error || "Save failed");
      closeSkillModal();
      await loadSkills();
    } catch (err) {
      msgEl.textContent = String(err.message || err);
      msgEl.className = "skill-form-msg err";
    }
  }

  async function deleteSkill() {
    if (!_editingSkill) return;
    const msgEl = $("skill-form-msg");
    if (!window.BAA?.ui?.confirm) {
      if (!confirm(`确定删除 Skill "${_editingSkill}" 吗?`)) return;
    } else {
      const ok = await window.BAA.ui.confirm({
        title: "删除 Skill",
        message: `确定删除 "${_editingSkill}" 吗? 此操作不可撤销。`,
        confirmText: "删除",
        cancelText: "取消",
      });
      if (!ok) return;
    }
    const suffix = state.SID ? `?sid=${encodeURIComponent(state.SID)}` : "";
    try {
      const r = await fetch(`/api/skills/${encodeURIComponent(_editingSkill)}${suffix}`, { method: "DELETE" });
      const d = await r.json();
      if (!r.ok || !d.ok) throw new Error(d.error || "Delete failed");
      closeSkillModal();
      await loadSkills();
    } catch (err) {
      msgEl.textContent = String(err.message || err);
      msgEl.className = "skill-form-msg err";
    }
  }

  // closeSkillModal 由 app.js 的 ACTIONS 分发器统一处理（data-action="closeSkillModal"），
  // 此处不再挂 document 委托监听，避免与 ACTIONS 表双触发。

  // Wire up buttons after DOM ready
  function _wireButtons() {
    $("skill-new-btn")?.addEventListener("click", () => openSkillModal(null));
    $("skill-save-btn")?.addEventListener("click", saveSkill);
    $("skill-delete-btn")?.addEventListener("click", deleteSkill);
    _wireCounters();
    _wireToolsMultiselect();
    _loadTools(); // pre-fetch tool list for instant dropdown rendering
    // ESC to close skill drawer
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("skill-modal-overlay").classList.contains("hidden")) {
        closeSkillModal();
      }
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _wireButtons);
  } else {
    _wireButtons();
  }

  const search = $("skill-picker-search");
  search?.addEventListener("input", onSearch);
  search?.addEventListener("keydown", onKeyDown);

export const skills = Object.freeze({
    SKILLS, open, close, isOpen, render, loadSkills, selectSkill, clearSkill,
    showMatchedSkills, activateSkill, sourceLabel,
    onSearch, onKeyDown, openSkillModal, closeSkillModal, saveSkill, deleteSkill,
});
