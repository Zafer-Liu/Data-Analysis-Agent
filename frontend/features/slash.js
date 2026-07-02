// Slash command registry + popup logic + input handlers.
import { $, state } from "../core/runtime.js";

  // Backend /api/commands is the sole public command catalog.
  const COMMANDS = [];
  const COMMAND_DIAGNOSTICS = [];
  const GROUP_KEYS = {
    analysis: "group.analysis", clean: "group.clean",
    export: "group.export", tools: "group.tools", session: "group.tools",
    custom: "group.custom",
  };

  function getCommand(name) {
    const key = String(name || "").toLowerCase();
    return COMMANDS.find(c => c.cmd === key || (c.aliases || []).includes(key));
  }

  function getAvailability(command) {
    if (!command) {
      return { available: false, code: "unknown_command", reason: "未知命令。" };
    }
    if (command.available === false) {
      return {
        available: false,
        code: command.unavailableCode || "command_unavailable",
        reason: command.unavailableReason || "当前命令不可用。",
      };
    }
    if (command.cmd === "stop" && !state.isStreaming) {
      return {
        available: false,
        code: "stream_required",
        reason: "当前没有正在生成的回复。",
      };
    }
    return { available: true, code: "", reason: "" };
  }

  function parseSlashInput(value) {
    const text = String(value || "").trim();
    if (!text.startsWith("/")) {
      return { isCommand: false, name: "", arguments: "", command: null };
    }
    const remainder = text.slice(1).trim();
    if (!remainder) {
      return { isCommand: true, name: "", arguments: "", command: null };
    }
    const match = remainder.match(/^([^\s]+)(?:\s+([\s\S]*))?$/);
    const name = String(match?.[1] || "").toLowerCase();
    return {
      isCommand: true,
      name,
      arguments: String(match?.[2] || "").trim(),
      command: getCommand(name),
    };
  }

  function _description(c) {
    return c.description || t(c.descKey);
  }

  function _appendHighlightedName(parent, text, term) {
    parent.appendChild(document.createTextNode("/"));
    const idx = term ? text.indexOf(term) : -1;
    if (idx < 0) {
      parent.appendChild(document.createTextNode(text));
      return;
    }
    parent.appendChild(document.createTextNode(text.slice(0, idx)));
    const mark = document.createElement("mark");
    mark.textContent = text.slice(idx, idx + term.length);
    parent.appendChild(mark);
    parent.appendChild(document.createTextNode(text.slice(idx + term.length)));
  }

  function buildSlashPopup(filter = "") {
    const pop    = $("slash-popup");
    const scroll = $("slash-popup-scroll");
    scroll.querySelectorAll(
      ".slash-item, .slash-group-label, .slash-empty, .slash-diagnostics"
    ).forEach(el => el.remove());

    const term    = filter.toLowerCase();
    const matched = COMMANDS.filter(c =>
      !term || c.cmd.includes(term) || (c.aliases || []).some(a => a.includes(term))
        || _description(c).toLowerCase().includes(term)
    );

    const header = pop.querySelector(".slash-pop-header");
    if (header) {
      header.textContent = term ? t('slash.searching', { term }) : t('slash.header');
    }

    if (matched.length === 0) {
      const empty = document.createElement("div");
      empty.className = "slash-empty";
      empty.textContent = t('slash.empty', { term });
      scroll.appendChild(empty);
      _appendDiagnostics(scroll);
      return;
    }

    let lastGroup = null;
    matched.forEach((c, i) => {
      const availability = getAvailability(c);
      if (c.groupKey && c.groupKey !== lastGroup) {
        const gl = document.createElement("div");
        gl.className = "slash-group-label";
        gl.textContent = t(c.groupKey);
        scroll.appendChild(gl);
        lastGroup = c.groupKey;
      }
      const div = document.createElement("div");
      div.className = "slash-item"
        + (availability.available ? "" : " disabled")
        + (i === 0 && availability.available ? " active" : "");
      div.dataset.cmd = c.cmd;
      div.title = availability.reason || "";
      const icon = document.createElement("span");
      icon.className = "slash-icon";
      icon.textContent = c.icon;
      const info = document.createElement("div");
      info.className = "slash-info";
      const name = document.createElement("div");
      name.className = "slash-name";
      _appendHighlightedName(name, c.cmd, term);
      if (!availability.available) {
        const unavailable = document.createElement("span");
        unavailable.className = "slash-soon";
        unavailable.textContent = "不可用";
        name.appendChild(unavailable);
      }
      const description = document.createElement("div");
      description.className = "slash-desc";
      description.textContent = _description(c);
      info.append(name, description);
      if (!availability.available) {
        const reason = document.createElement("div");
        reason.className = "slash-unavailable-reason";
        reason.textContent = availability.reason;
        info.appendChild(reason);
      }
      div.append(icon, info);
      if (availability.available) div.addEventListener("click", () => selectCommand(c.cmd));
      scroll.appendChild(div);
    });
    _appendDiagnostics(scroll);
  }

  function _appendDiagnostics(scroll) {
    if (!COMMAND_DIAGNOSTICS.length) return;
    const details = document.createElement("details");
    details.className = "slash-diagnostics";
    const summary = document.createElement("summary");
    summary.textContent = `命令配置问题（${COMMAND_DIAGNOSTICS.length}）`;
    details.appendChild(summary);
    const list = document.createElement("ul");
    for (const item of COMMAND_DIAGNOSTICS) {
      const row = document.createElement("li");
      row.textContent = `${item.source || "custom"} · ${item.path || "command"}：${item.error}`;
      list.appendChild(row);
    }
    details.appendChild(list);
    scroll.appendChild(details);
  }

  function openSlashPopup(filter = "") {
    window.BAA.skills?.close?.();
    buildSlashPopup(filter);
    state.slashPopupIndex = 0;
    updateSlashActive();
    $("slash-popup").classList.add("open");
  }
  function closeSlashPopup() { $("slash-popup").classList.remove("open"); }
  function isSlashOpen()     { return $("slash-popup").classList.contains("open"); }

  function updateSlashActive() {
    const scroll = $("slash-popup-scroll");
    if (!scroll) return;
    const items = [...scroll.querySelectorAll(".slash-item:not(.disabled)")];
    scroll.querySelectorAll(".slash-item").forEach(el => el.classList.remove("active"));
    if (items[state.slashPopupIndex]) {
      items[state.slashPopupIndex].classList.add("active");
      items[state.slashPopupIndex].scrollIntoView({ block: "nearest" });
    }
  }

  function selectCommand(cmd) {
    window.BAA.skills?.clearSkill?.();
    const c = getCommand(cmd) || { cmd, icon: "⌘" };
    if (!getAvailability(c).available) return;
    cmd = c.cmd;
    state.activeCommand = cmd;
    const badge = $("cmd-badge");
    $("cmd-badge-text").textContent = `${c.icon} /${cmd}`;
    badge.classList.add("show");
    const input = $("msg-input");
    input.value = input.value.replace(/^\/\S*\s*/, "");
    closeSlashPopup();
    input.focus();
    window.BAA.chatStream?.syncComposerPlaceholder?.();
    if (window.BAA.chatStream?.syncSendButton) window.BAA.chatStream.syncSendButton();
  }

  function clearCmd() {
    state.activeCommand = "";
    $("cmd-badge").classList.remove("show");
    window.BAA.chatStream?.syncComposerPlaceholder?.();
  }

  function autoResize(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 140) + "px";
  }

  function onInput(e) {
    autoResize(e.target);
    const v = e.target.value;
    if (window.BAA.chatStream?.syncSendButton) window.BAA.chatStream.syncSendButton();

    // "/cmd " (no args) — select command, clear input
    const mFull = v.match(/^\/([\w:?-]+)\s$/);
    if (mFull) {
      const found = getCommand(mFull[1]);
      if (found) {
        selectCommand(found.cmd);
        e.target.value = "";
        autoResize(e.target);
        return;
      }
    }

    // "/cmd args..." — select command, keep args as input text
    const mFullCmd = v.match(/^\/([\w:?-]+)\s+(.+)/);
    if (mFullCmd) {
      const found = getCommand(mFullCmd[1]);
      if (found) {
        selectCommand(found.cmd);
        e.target.value = mFullCmd[2];
        autoResize(e.target);
        return;
      }
    }

    const mSlash = v.match(/^\/([\w:?-]*)$/);
    if (mSlash) {
      const term = mSlash[1];
      if (isSlashOpen()) {
        buildSlashPopup(term);
        state.slashPopupIndex = 0;
        updateSlashActive();
      } else {
        openSlashPopup(term);
      }
      return;
    }

    if (isSlashOpen()) closeSlashPopup();
  }

  function onKeyDown(e) {
    if (isSlashOpen()) {
      const sc = $("slash-popup-scroll");
      const available = sc ? [...sc.querySelectorAll(".slash-item:not(.disabled)")] : [];
      if (e.key === "ArrowDown") {
        e.preventDefault();
        state.slashPopupIndex = Math.min(state.slashPopupIndex + 1, available.length - 1);
        updateSlashActive(); return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        state.slashPopupIndex = Math.max(state.slashPopupIndex - 1, 0);
        updateSlashActive(); return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        const item = available[state.slashPopupIndex];
        if (item) selectCommand(item.dataset.cmd);
        else {
          closeSlashPopup();
          window.BAA.chatStream.sendMessage();
        }
        return;
      }
      if (e.key === "Tab") {
        e.preventDefault();
        if (available.length === 1) {
          selectCommand(available[0].dataset.cmd);
        } else if (available.length > 1) {
          state.slashPopupIndex = (state.slashPopupIndex + 1) % available.length;
          updateSlashActive();
        }
        return;
      }
      if (e.key === "Escape") { closeSlashPopup(); return; }
    }
    if (e.key === "Tab" && /^\/[\w:?-]*$/.test(e.currentTarget.value.trim())) {
      e.preventDefault();
      openSlashPopup(e.currentTarget.value.trim().slice(1));
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      window.BAA.chatStream.sendMessage();
    }
  }

  // Click outside the input area closes the slash popup.
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".input-area")) closeSlashPopup();
  });

  function fillHint(el) {
    const txt = el.textContent;
    const m = txt.match(/^\/([\w:?-]+)\s?(.*)/);
    if (m) {
      const found = getCommand(m[1]);
      if (found) {
        selectCommand(found.cmd);
        $("msg-input").value = m[2];
        return;
      }
    }
    $("msg-input").value = txt;
    window.BAA.chatStream.sendMessage();
  }

  async function loadCommands() {
    try {
      const suffix = state.SID ? `?sid=${encodeURIComponent(state.SID)}` : "";
      const response = await fetch(`/api/commands${suffix}`);
      if (!response.ok) return;
      const payload = await response.json();
      COMMANDS.splice(0, COMMANDS.length, ...(payload.commands || []).map(command => ({
        cmd: command.name,
        aliases: command.aliases || [],
        icon: command.icon || "⌘",
        description: command.description || command.name,
        groupKey: GROUP_KEYS[command.category] || "group.custom",
        available: command.available !== false,
        unavailableCode: command.unavailable_code || "",
        unavailableReason: command.unavailable_reason || "",
        type: command.type,
        usage: command.usage || `/${command.name}`,
        argumentHint: command.argument_hint || "",
        arguments: command.arguments || "none",
        usesModel: !!command.uses_model,
        confirmation: command.confirmation || "none",
        source: command.source || "builtin",
        promptTokensEst: Number(command.prompt_tokens_est) || 0,
        promptSizeWarning: !!command.prompt_size_warning,
        clientAction: command.client_action || "",
      })));
      COMMAND_DIAGNOSTICS.splice(
        0,
        COMMAND_DIAGNOSTICS.length,
        ...(payload.diagnostics || []),
      );
      if (state.activeCommand) {
        const selected = getCommand(state.activeCommand);
        if (!selected || !getAvailability(selected).available) clearCmd();
      }
      buildSlashPopup();
    } catch (err) {
      console.warn("[BAA] slash commands unavailable:", err);
    }
  }

export const slash = Object.freeze({
    COMMANDS, COMMAND_DIAGNOSTICS,
    buildSlashPopup, openSlashPopup, closeSlashPopup, isSlashOpen,
    selectCommand, clearCmd, getCommand, getAvailability, parseSlashInput,
    onInput, onKeyDown, autoResize, fillHint, loadCommands,
});
