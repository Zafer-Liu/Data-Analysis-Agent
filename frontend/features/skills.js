// Independent Skill picker. Skills never enter the slash-command catalog.
import { $, state } from "../core/runtime.js";
  const SKILLS = [];

  function sourceLabel(source) {
    return ({ builtin: "内置", user: "个人", workspace: "工作目录" })[source] || source || "内置";
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
        <span class="skill-picker-icon">${esc(skill.icon || "🧩")}</span>
        <span class="skill-picker-copy">
          <strong>${esc(skill.name)}</strong>
          <small>${esc(skill.description || skill.name)}</small>
        </span>
        <span class="skill-picker-source">${esc(sourceLabel(skill.source))}</span>`;
      button.addEventListener("click", () => selectSkill(skill.name));
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
    const picker = $("skill-picker");
    const search = $("skill-picker-search");
    state.skillPickerIndex = 0;
    picker?.classList.add("open");
    if (search) {
      search.value = "";
      render();
      search.focus();
    }
  }

  function close() { $("skill-picker")?.classList.remove("open"); }
  function isOpen() { return Boolean($("skill-picker")?.classList.contains("open")); }

  function selectSkill(name) {
    const skill = SKILLS.find(item => item.name === name) || { name, icon: "🧩" };
    window.BAA.slash?.clearCmd?.();
    state.activeSkill = skill.name;
    $("skill-badge-text").textContent = `${skill.icon || "🧩"} ${skill.name}`;
    $("skill-badge")?.classList.add("show");
    close();
    $("msg-input")?.focus();
    window.BAA.chatStream?.syncSendButton?.();
  }

  function clearSkill() {
    state.activeSkill = "";
    $("skill-badge")?.classList.remove("show");
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

  document.addEventListener("click", event => {
    if (!event.target.closest(".input-area")) close();
  });
  const search = $("skill-picker-search");
  search?.addEventListener("input", onSearch);
  search?.addEventListener("keydown", onKeyDown);

export const skills = Object.freeze({
    SKILLS, open, close, isOpen, render, loadSkills, selectSkill, clearSkill, sourceLabel,
    onSearch, onKeyDown,
});
