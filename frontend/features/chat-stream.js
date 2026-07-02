// Chat send / stop + SSE stream + handleEvent (object-table dispatch).
import {
  $,
  appendMsg,
  bindBubbleImages,
  clearCmd,
  clearMessages,
  esc,
  hideWelcome,
  renderMd,
  scrollBottom,
  scrollReset,
  showStatus,
  showWelcome,
  state,
  updateTokenBar,
} from "../core/page-runtime.js";
import { getUiIsland } from "../core/ui-registry.js";
import { scheduleAutosave, setLoadedName } from "../legacy/autosave.js";
import { open as openCheckpoints } from "../legacy/checkpoints.js";
import { commandHandlers } from "../legacy/command_handlers.js";
import { resetSourceState } from "../legacy/datasource.js";
import {
  applyLiveEvent,
  open as openJobHistory,
  switchSession as switchJobHistorySession,
} from "../legacy/job_history.js";
import { openSchemaView } from "../legacy/preview.js";
import { loadSavedList } from "../legacy/sessions.js";

  const clearSkill = () => window.BAA.skills?.clearSkill?.();

  // ── Send / Stop ────────────────────────────────────────────────────
  function onSendOrStop() {
    const hasDraft = _hasComposerDraft();
    if (state.isStreaming && !hasDraft) stopStreaming();
    else sendMessage();
  }

  async function stopStreaming() {
    if (!state.isStreaming || !state.SID) return;
    state._stopRequested = true;
    try { await fetch(`/api/session/${state.SID}/stop`, { method: "POST" }); } catch (_) {}
    if (state._streamReader) {
      try { state._streamReader.cancel(); } catch (_) {}
    }
  }

  function _setSendBtnStopping(stopping) {
    // The button now contains an SVG arrow; the .stopping class swaps it for a
    // stop-square rendered via ::before (CSS only). No textContent mutation —
    // that would wipe out the SVG.
    const btn = $("send-btn");
    btn.classList.toggle("stopping", stopping);
    btn.title    = stopping ? (t('send.stop') || "停止 (Stop)") : t('send.title');
    btn.disabled = false;
  }

  function syncSendButton() {
    const hasDraft = _hasComposerDraft();
    _setSendBtnStopping(state.isStreaming && !hasDraft);
    if (state.isStreaming && hasDraft) {
      $("send-btn").title = t("send.queue") || "加入等待队列";
    }
  }

  function _defaultComposerPlaceholder() {
    const command = state.activeCommand
      ? window.BAA.slash?.getCommand?.(state.activeCommand)
      : null;
    if (command?.argumentHint) return command.argumentHint;
    return window.t ? t("input.placeholder") : "今天帮你做些什么？";
  }

  function _hasPromptSuggestionMarker(input = $("msg-input")) {
    return !!(input?.dataset?.promptSuggestion === "1" && state.promptSuggestionText);
  }

  function _hasComposerDraft() {
    return !!(
      $("msg-input").value.trim()
      || _hasPromptSuggestionMarker()
      || state.activeCommand
      || state.activeSkill
    );
  }

  function syncComposerPlaceholder() {
    const input = $("msg-input");
    if (!input) return;
    const ghost = $("prompt-suggestion-ghost");
    if (_hasPromptSuggestionMarker(input)) {
      input.placeholder = "";
      if (ghost) ghost.textContent = state.promptSuggestionText;
    } else {
      input.placeholder = _defaultComposerPlaceholder();
      if (ghost) ghost.textContent = "";
    }
  }

  function _clearPromptSuggestionMarker() {
    const input = $("msg-input");
    if (!input) return;
    delete input.dataset.promptSuggestion;
    input.classList.remove("prompt-suggestion-active");
    syncComposerPlaceholder();
  }

  function _invalidatePromptSuggestion() {
    state.promptSuggestionRequestId = (Number(state.promptSuggestionRequestId) || 0) + 1;
    state.promptSuggestionText = "";
    _clearPromptSuggestionMarker();
  }

  function onComposerInput(event) {
    if (state._applyingPromptSuggestion) return;
    const input = event?.target || $("msg-input");
    if (input?.dataset?.promptSuggestion === "1") {
      _clearPromptSuggestionMarker();
    }
    state.promptSuggestionText = "";
    state.promptSuggestionRequestId = (Number(state.promptSuggestionRequestId) || 0) + 1;
  }

  function _composerCanAcceptSuggestion() {
    const input = $("msg-input");
    return !!(
      input
      && state.promptSuggestionEnabled
      && !input.value.trim()
      && !state.isStreaming
      && !state.pendingMessages.length
      && !state.activeCommand
      && !state.activeSkill
      && !state.editingQueuedId
    );
  }

  function _applyPromptSuggestion(suggestion, requestId) {
    const input = $("msg-input");
    const text = String(suggestion || "").trim();
    if (!text || requestId !== state.promptSuggestionRequestId || !_composerCanAcceptSuggestion()) return;
    state.promptSuggestionText = text;
    state._applyingPromptSuggestion = true;
    input.dataset.promptSuggestion = "1";
    input.value = "";
    input.classList.add("prompt-suggestion-active");
    syncComposerPlaceholder();
    input.dispatchEvent(new Event("input", { bubbles: true }));
    state._applyingPromptSuggestion = false;
    syncSendButton();
  }

  async function _requestPromptSuggestion() {
    if (!state.SID || !state.promptSuggestionEnabled || !_composerCanAcceptSuggestion()) return;
    const sid = state.SID;
    const requestId = (Number(state.promptSuggestionRequestId) || 0) + 1;
    state.promptSuggestionRequestId = requestId;
    try {
      const resp = await fetch(`/api/session/${sid}/prompt-suggestion`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lang: window.getLang ? window.getLang() : "zh" }),
      });
      if (!resp.ok || sid !== state.SID) return;
      const data = await resp.json().catch(() => ({}));
      if (data?.ok && data?.suggestion) {
        _applyPromptSuggestion(data.suggestion, requestId);
      }
    } catch (_) {
      // Leave the composer untouched when suggestion generation is unavailable.
    }
  }

  function _queueFacade(target, status, position, callbacks) {
    return !!(getUiIsland("chat")?.setTurnQueueState
      && getUiIsland("chat").setTurnQueueState(target, status, position, callbacks));
  }

  function _clonePayload(payload) {
    return JSON.parse(JSON.stringify(payload || {}));
  }

  function _mergeContinuationPayload(activePayload, appendedPayload) {
    const base = _clonePayload(activePayload);
    const original = String(base.message || "").trim();
    const added = String(appendedPayload?.message || "").trim();
    if (!original || !added) return _clonePayload(appendedPayload);
    base.message = [
      original,
      "",
      "[用户在上一轮回答生成中追加的信息]",
      added,
      "[请结合上述追加信息，重新继续完成原任务。]",
    ].join("\n");
    return base;
  }

  function _refreshQueuePositions() {
    state.pendingMessages.forEach((item, index) => {
      _queueFacade(item.assistantId, "queued", index + 1, { onCancel: () => _cancelQueued(item.id) });
    });
    if (getUiIsland("chat")?.renderComposerQueue) {
      getUiIsland("chat").renderComposerQueue(
        state.pendingMessages.map(item => ({ id: item.id, displayText: item.displayText })),
        { onSendNow: _sendQueuedNow, onEdit: _editQueued, onCancel: _cancelQueued },
      );
    }
  }

  function _cancelQueued(queueId) {
    const index = state.pendingMessages.findIndex(item => item.id === queueId);
    if (index < 0) return;
    const [item] = state.pendingMessages.splice(index, 1);
    if (state.editingQueuedId === queueId) state.editingQueuedId = "";
    if (getUiIsland("chat")?.removeMessages) {
      getUiIsland("chat").removeMessages([item.userId, item.assistantId]);
    } else {
      _queueFacade(item.assistantId, "canceled", 0);
    }
    _refreshQueuePositions();
  }

  async function _sendQueuedNow(queueId) {
    const index = state.pendingMessages.findIndex(item => item.id === queueId);
    if (index < 0) return;
    const [item] = state.pendingMessages.splice(index, 1);
    if (state.isStreaming) {
      state.silentContinuation = true;
      _showActiveTurnActivity();
      if (state.activeTurn?.payload) {
        item.payload = _mergeContinuationPayload(state.activeTurn.payload, item.payload);
      }
    }
    state.pendingMessages.unshift(item);
    state.editingQueuedId = "";
    _refreshQueuePositions();
    if (state.isStreaming) await stopStreaming();
    else _drainMessageQueue();
  }

  function _showActiveTurnActivity() {
    const assistantId = state.activeTurn?.assistantId || "";
    const assistant = assistantId
      ? document.querySelector(`[data-vue-msg-id="${assistantId}"]`)
      : null;
    const stepsEl = assistant?.querySelector(".tool-steps");
    if (!stepsEl) return false;
    return _showToolActivity({ stepsEl, text: t("tool.next_step") || "正在思考下一步…" });
  }

  function _editQueued(queueId) {
    const item = state.pendingMessages.find(candidate => candidate.id === queueId);
    const input = $("msg-input");
    if (!item || !input) return;
    state.editingQueuedId = queueId;
    clearCmd();
    clearSkill();
    input.value = item.payload.message;
    if (item.payload.command && window.BAA.slash?.selectCommand) {
      window.BAA.slash.selectCommand(item.payload.command);
    }
    if (item.payload.skill && window.BAA.skills?.selectSkill) {
      window.BAA.skills.selectSkill(item.payload.skill);
    }
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  }

  function _appendTurnShell(displayText, options = {}) {
    const user = appendMsg("user", displayText, options.user || {});
    const assistant = appendMsg("assistant", null);
    return {
      userId: user?.dataset?.vueMsgId || "",
      assistant,
      assistantId: assistant?.dataset?.vueMsgId || "",
    };
  }

  async function _startTurn(payload, assistant, assistantId = "", displayText = "") {
    if (!assistant && assistantId) {
      assistant = document.querySelector(`[data-vue-msg-id="${assistantId}"]`);
    }
    if (!assistant) return;
    _queueFacade(assistantId || assistant, "", 0);
    state.activeTurn = {
      payload: _clonePayload(payload),
      assistantId: assistantId || assistant?.dataset?.vueMsgId || "",
      displayText: displayText || "",
    };
    const stepsEl = assistant.querySelector(".tool-steps");
    const bubbleEl = assistant.querySelector(".msg-bubble");
    const typing = document.createElement("div");
    typing.className = "typing-dots";
    typing.innerHTML = "<span></span><span></span><span></span>";
    bubbleEl.appendChild(typing);
    state.isStreaming = true;
    syncSendButton();
    scrollReset();
    await _streamChat(payload, stepsEl, bubbleEl, typing);
  }

  function _enqueueTurn(payload, displayText) {
    const shell = _appendTurnShell(displayText, { user: { variant: "append" } });
    const item = {
      id: `queued-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      payload,
      displayText,
      userId: shell.userId,
      assistantId: shell.assistantId,
    };
    state.pendingMessages.push(item);
    _refreshQueuePositions();
    scrollBottom(true);
  }

  function _drainMessageQueue() {
    if (state.isStreaming || !state.pendingMessages.length) return;
    const item = state.pendingMessages.shift();
    // Reserve the single active-turn slot before yielding to the event loop;
    // otherwise a click in this small window could start a parallel request.
    state.isStreaming = true;
    syncSendButton();
    _refreshQueuePositions();
    _queueFacade(item.assistantId, "processing", 0);
    setTimeout(() => _startTurn(item.payload, null, item.assistantId, item.displayText), 0);
  }

  function _localReply(markdown) {
    appendMsg("assistant", markdown);
    scrollBottom(true);
  }

  function _setLocalReply(target, markdown) {
    const ui = getUiIsland("chat");
    if (ui?.setMessageText?.(target, markdown)) {
      scrollBottom(true);
      return true;
    }
    const bubble = target?.querySelector?.(".msg-bubble");
    if (!bubble) return false;
    bubble.innerHTML = renderMd(markdown);
    bindBubbleImages(bubble);
    scrollBottom(true);
    return true;
  }

  function _startCompactProgressMessage() {
    const assistant = appendMsg("assistant", null);
    const target = assistant?.dataset?.vueMsgId || assistant;
    const ui = getUiIsland("chat");
    const phases = [
      { progress: 8, detail: "正在准备压缩当前对话上下文…" },
      { progress: 24, detail: "正在整理可压缩历史与保留重点…" },
      { progress: 48, detail: "正在调用模型生成语义摘要…" },
      { progress: 72, detail: "正在校验摘要并保留最近上下文…" },
      { progress: 88, detail: "正在写回压缩后的会话状态…" },
    ];
    let progress = phases[0].progress;
    let phaseIndex = 0;
    let stopped = false;

    const update = (value, detail) => {
      progress = Math.max(progress, Math.min(100, Math.round(Number(value) || progress)));
      ui?.updateToolProgress?.(target, {
        tool: "compaction",
        display: "压缩对话上下文…",
        detail,
        progress,
        progress_label: "对话压缩进度",
      });
      scrollBottom();
    };

    ui?.startTool?.(target, {
      tool: "compaction",
      display: "压缩对话上下文…",
      detail: phases[0].detail,
      progress,
      progress_label: "对话压缩进度",
    });

    const timer = setInterval(() => {
      if (stopped) return;
      const nextPhase = phases[Math.min(phaseIndex + 1, phases.length - 1)];
      if (progress >= nextPhase.progress && phaseIndex < phases.length - 1) {
        phaseIndex += 1;
      }
      const phase = phases[phaseIndex];
      const ceiling = phases[Math.min(phaseIndex + 1, phases.length - 1)].progress;
      const next = Math.min(ceiling, progress + Math.max(1, Math.round((ceiling - progress) * 0.28)));
      update(next, phase.detail);
    }, 650);

    const stop = () => {
      stopped = true;
      clearInterval(timer);
    };

    return {
      target,
      complete(markdown) {
        stop();
        update(100, "压缩完成，正在更新对话上下文…");
        ui?.endTool?.(target, { tool: "compaction" });
        if (!_setLocalReply(target, markdown) && !_setLocalReply(assistant, markdown)) {
          _localReply(markdown);
        }
      },
      fail(markdown) {
        stop();
        update(Math.max(progress, 95), "压缩未完成，请查看错误信息。");
        ui?.endTool?.(target, { tool: "compaction" });
        if (!_setLocalReply(target, markdown) && !_setLocalReply(assistant, markdown)) {
          _localReply(markdown);
        }
      },
    };
  }

  async function _handleClearCommand() {
    if (state.isStreaming) await stopStreaming();
    const response = await fetch(`/api/session/${state.SID}/clear`, { method: "POST" });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || result.ok === false) {
      _localReply(result.error || "清除当前对话失败。");
      return;
    }
    state.pendingMessages.length = 0;
    state.editingQueuedId = "";
    _refreshQueuePositions();
    await window.BAA.slash?.loadCommands?.();
    clearCmd(); clearSkill(); clearMessages();
    state.tokenState = {
      promptTokens: 0, totalInput: 0, totalOutput: 0,
      contextWindow: state.tokenState.contextWindow,
    };
    updateTokenBar();
    showWelcome();
  }

  async function _handleInstructionCommand({ arguments: arg }) {
    if (window.BAA?.tempPrompt) await window.BAA.tempPrompt.tpOpenWithText(arg);
    else window.openOverlay?.("ov-temp-prompt");
  }

  async function _handleSessionCommand({ arguments: arg }) {
    if (arg.toLowerCase() === "new") { await newChat(); return; }
    await loadSavedList();
    _localReply("已刷新左侧的已保存对话。使用 `/sessions new` 可开始新会话。");
  }

  async function _handleSkillCommand({ arguments: arg }) {
    const parts = arg.split(/\s+/).filter(Boolean);
    await window.BAA.skills?.loadSkills?.();
    if (parts[0] === "info" && parts[1]) {
      const skill = window.BAA.skills.SKILLS.find(item => item.name === parts[1]);
      _localReply(skill
        ? `### ${skill.icon || "🧩"} ${skill.name}\n\n${skill.description}\n\n来源：${window.BAA.skills.sourceLabel(skill.source)}`
        : `未找到 Skill：${parts[1]}`);
    } else if (parts[0] === "reload") {
      _localReply(`Skill 已刷新，共 ${window.BAA.skills.SKILLS.length} 个。`);
    } else {
      await window.BAA.skills?.open?.();
    }
  }

  function _handleHelpCommand({ arguments: arg }) {
    const requested = arg.replace(/^\//, "").toLowerCase();
    const commands = window.BAA.slash.COMMANDS;
    const selected = requested ? [window.BAA.slash.getCommand(requested)].filter(Boolean) : commands;
    if (!selected.length) {
      _localReply(`未知命令：/${requested}\n\n输入 \`/help\` 查看可用命令。`);
      return;
    }
    if (requested) {
      const item = selected[0];
      const aliases = (item.aliases || []).map(alias => `\`/${alias}\``).join("、") || "无";
      const availability = window.BAA.slash.getAvailability(item);
      const typeLabels = {
        "local": "本地命令",
        "local-ui": "本地 UI 命令",
        "backend": "后端确定性命令",
        "prompt": "Agent Prompt 命令",
      };
      _localReply(
        `### \`/${item.cmd}\`\n\n${item.description}\n\n`
        + `- 用法：\`${item.usage}\`\n`
        + `- 别名：${aliases}\n`
        + `- 类型：${typeLabels[item.type] || item.type}\n`
        + `- 模型调用：${item.usesModel ? "是" : "否"}\n`
        + `- 当前状态：${availability.available ? "可用" : `不可用（${availability.reason}）`}\n`
        + (item.type === "prompt"
          ? `- Prompt 规模：约 ${Number(item.promptTokensEst || 0).toLocaleString()} tokens`
            + `${item.promptSizeWarning ? "（偏大）" : ""}\n`
          : "")
        + `- 来源：${item.source || "builtin"}`
      );
      return;
    }
    _localReply(
      `### 可用命令\n\n${selected.map(item => {
        const aliases = (item.aliases || []).map(alias => `/${alias}`).join(", ");
        const availability = window.BAA.slash.getAvailability(item);
        const stateText = availability.available ? "" : ` — 暂不可用：${availability.reason}`;
        return `- \`${item.usage}\`${aliases ? `（${aliases}）` : ""} — ${item.description}${stateText}`;
      }).join("\n")}\n\n输入 \`/help <命令名>\` 查看详细用法。`
    );
  }

  function _registerCommandHandlers() {
    commandHandlers.register("clear", _handleClearCommand);
    commandHandlers.register("status", () => showStatus());
    commandHandlers.register("mcp", () => window.BAA?.mcp?.openMcpSettings?.());
    commandHandlers.register("memory", () => window.openOverlay?.("ov-knowledge"));
    commandHandlers.register("permission", () => window.BAA.workspace?.openModal?.());
    commandHandlers.register("plan", _handleInstructionCommand);
    commandHandlers.register("session", _handleSessionCommand);
    commandHandlers.register("skill", _handleSkillCommand);
    commandHandlers.register("help", _handleHelpCommand);
    commandHandlers.register("rewind", () => openCheckpoints());
    commandHandlers.register("new", () => newChat());
    commandHandlers.register("stop", async () => {
      if (state.isStreaming) await stopStreaming();
      else _localReply("当前没有正在生成的回复。");
    });
    commandHandlers.register("data", () => openSchemaView());
    commandHandlers.register("jobs", () => openJobHistory());
    commandHandlers.register("teams", () => window.BAA.teams?.openPanel?.());
  }

  async function _runClientCommand(command, text) {
    const started = Date.now();
    let outcome = "success";
    let errorCode = "";
    try {
      await commandHandlers.execute(command, text);
    } catch (error) {
      outcome = "error";
      errorCode = "client_handler_failed";
      console.error("[BAA] local Command failed:", error);
      _localReply(`命令 /${command.cmd} 暂无可用的 Web 处理器。`);
    } finally {
      fetch(`/api/session/${state.SID}/command-metrics`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          command: command.cmd,
          outcome,
          error_code: errorCode,
          duration_ms: Date.now() - started,
        }),
      }).catch(() => {});
    }
  }

  async function _runBackendCommand(command, text) {
    if (state.isStreaming) await stopStreaming();
    const compactProgress = command.cmd === "compact" ? _startCompactProgressMessage() : null;
    if (command.usesModel && !compactProgress) {
      window.BAA.overlay?.toast?.(`正在执行 /${command.cmd}…`, "info");
    }

    let response;
    let result = {};
    try {
      response = await fetch(
        `/api/session/${state.SID}/commands/${encodeURIComponent(command.cmd)}/execute`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ arguments: String(text || "").trim() }),
        },
      );
      result = await response.json().catch(() => ({}));
    } catch (error) {
      const message = error?.message || `命令 /${command.cmd} 执行失败。`;
      if (compactProgress) compactProgress.fail(message);
      else _localReply(message);
      await window.BAA.slash?.loadCommands?.();
      return;
    }

    if (!response.ok || result.ok === false) {
      const message = result.error || `命令 /${command.cmd} 执行失败。`;
      if (compactProgress) compactProgress.fail(message);
      else _localReply(message);
      await window.BAA.slash?.loadCommands?.();
      return;
    }

    if (command.cmd === "compact") {
      state.tokenState.promptTokens = Number(result.after_tokens) || 0;
      state.tokenState.totalInput = Number(result.session_total_input) || state.tokenState.totalInput;
      state.tokenState.totalOutput = Number(result.session_total_output) || state.tokenState.totalOutput;
      updateTokenBar();
      const usage = result.usage;
      const usageText = usage
        ? `；摘要模型输入 ${Number(usage.input_tokens || 0).toLocaleString()}，输出 ${Number(usage.output_tokens || 0).toLocaleString()} tokens`
        : "";
      const message =
        `上下文已压缩：约 ${Number(result.before_tokens || 0).toLocaleString()} → `
        + `${Number(result.after_tokens || 0).toLocaleString()} tokens；`
        + `历史消息 ${result.before_messages} → ${result.after_messages} 条${usageText}。`;
      if (compactProgress) compactProgress.complete(message);
      else _localReply(message);
    } else {
      _localReply(result.message || `命令 /${command.cmd} 已完成。`);
    }
    await window.BAA.slash?.loadCommands?.();
  }

  _registerCommandHandlers();

  async function sendMessage() {
    const input = $("msg-input");
    const suggestionAccepted = _hasPromptSuggestionMarker(input) && !input.value.trim();
    let text  = suggestionAccepted ? state.promptSuggestionText.trim() : input.value.trim();
    let commandDef = state.activeCommand ? window.BAA.slash.getCommand(state.activeCommand) : null;
    if (!commandDef && !suggestionAccepted && text.startsWith("/")) {
      const parsed = window.BAA.slash.parseSlashInput(text);
      if (!parsed.name) {
        window.BAA.slash.openSlashPopup();
        return;
      }
      if (!parsed.command) {
        input.value = "";
        input.style.height = "auto";
        hideWelcome();
        appendMsg("user", text);
        _localReply(`未知命令：/${parsed.name}\n\n输入 \`/help\` 查看可用命令。`);
        syncSendButton();
        return;
      }
      commandDef = parsed.command;
      text = parsed.arguments;
    }
    if (!text && !commandDef) return;
    _invalidatePromptSuggestion();

    const availability = commandDef
      ? window.BAA.slash.getAvailability(commandDef)
      : { available: true };
    if (!availability.available) {
      _localReply(availability.reason || `命令 \`/${commandDef.cmd}\` 当前不可用。`);
      clearCmd();
      syncComposerPlaceholder();
      return;
    }

    if (commandDef?.arguments === "required" && !text) {
      _localReply(commandDef.argumentHint || `用法：${commandDef.usage}`);
      syncComposerPlaceholder();
      return;
    }
    if (commandDef?.arguments === "none" && text) {
      _localReply(`命令 \`/${commandDef.cmd}\` 不接受参数。用法：\`${commandDef.usage}\``);
      return;
    }

    if (commandDef && ["local", "local-ui", "backend"].includes(commandDef.type)) {
      const display = `/${commandDef.cmd}${text ? ` ${text}` : ""}`;
      input.value = ""; input.style.height = "auto";
      hideWelcome(); clearCmd(); clearSkill();
      appendMsg("user", display);
      if (commandDef.type === "backend") await _runBackendCommand(commandDef, text);
      else await _runClientCommand(commandDef, text);
      syncSendButton();
      return;
    }

    _lastSentInput = text;
    input.value = ""; input.style.height = "auto";
    hideWelcome();

    const selectedCommand = commandDef?.cmd || state.activeCommand;
    const selectedSkill = state.activeSkill;
    const displayText = selectedCommand
      ? `/${selectedCommand} ${text}`
      : selectedSkill ? `[Skill: ${selectedSkill}] ${text}` : text;
    const payload = { message: text, teams_enabled: !!state.teamsEnabled };
    if (selectedCommand) payload.command = selectedCommand;
    if (selectedSkill) payload.skill = selectedSkill;
    clearCmd();
    clearSkill();
    if (state.editingQueuedId) {
      const item = state.pendingMessages.find(candidate => candidate.id === state.editingQueuedId);
      state.editingQueuedId = "";
      if (item) {
        item.payload = payload;
        item.displayText = displayText;
        _refreshQueuePositions();
        getUiIsland("chat")?.setMessageText?.(item.userId, displayText);
        syncSendButton();
        return;
      }
    }
    if (state.isStreaming) {
      _enqueueTurn(payload, displayText);
      syncSendButton();
      return;
    }
    const shell = _appendTurnShell(displayText);
    await _startTurn(payload, shell.assistant, shell.assistantId, displayText);
  }

  // Confirm / revise stream for ppt/excel/report/dashboard outline cards.
  async function sendConfirmStream(payload) {
    if (state.isStreaming) return;
    _invalidatePromptSuggestion();
    hideWelcome();

    appendMsg("user", payload.message || "确认");
    const aEl      = appendMsg("assistant", null);
    const stepsEl  = aEl.querySelector(".tool-steps");
    const bubbleEl = aEl.querySelector(".msg-bubble");

    const typing = document.createElement("div");
    typing.className = "typing-dots";
    typing.innerHTML = "<span></span><span></span><span></span>";
    bubbleEl.appendChild(typing);

    state.isStreaming = true;
    _setSendBtnStopping(true);
    scrollReset();   // reset scroll state for confirm stream

    await _streamChat(payload, stepsEl, bubbleEl, typing);
  }

  async function _streamChat(payload, stepsEl, bubbleEl, typing) {
    if (state.analysisContext && !payload.data_context) {
      payload.data_context = state.analysisContext;
    }
    let reader = null;
    let streamHadIssue = false;
    state._stopRequested = false;
    try {
      const resp = await fetch(`/api/session/${state.SID}/chat`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const failure = await resp.json().catch(() => ({}));
        throw new Error(failure.error || `Chat request failed (${resp.status})`);
      }
      if (!resp.body) throw new Error(`Chat request failed (${resp.status})`);
      reader = resp.body.getReader();
      state._streamReader = reader;
      const dec = new TextDecoder();
      let buf = "";
      const consumeLine = async (line) => {
        if (!line.startsWith("data: ")) return;
        try {
          const event = JSON.parse(line.slice(6));
          if (event.type === "error" || event.type === "stopped") streamHadIssue = true;
          await handleEvent(event, stepsEl, bubbleEl, typing);
        }
        catch (_) {}
      };
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n"); buf = lines.pop();
        for (const line of lines) {
          await consumeLine(line);
        }
      }
      if (buf.trim()) await consumeLine(buf.trim());
    } catch (error) {
      // reader.cancel() throws — expected when stopStreaming() is called.
      if (reader && state._streamReader === reader) {
        // A real HTTP failure should not wedge the FIFO; show it in the turn.
        const message = error?.message || String(error);
        if (message && !/cancel/i.test(message)) {
          await handleEvent({ type: "error", message }, stepsEl, bubbleEl, typing);
        }
      } else if (!reader) {
        await handleEvent({ type: "error", message: error?.message || String(error) }, stepsEl, bubbleEl, typing);
      }
    } finally {
      const continuingAfterAppend = !!state.silentContinuation;
      const stoppedByUser = !!state._stopRequested;
      if (continuingAfterAppend) {
        _cancelTailActivity();
        _showToolActivity({ stepsEl });
      } else {
        _cancelTailActivity();
        if (!(getUiIsland("chat") && getUiIsland("chat").finishAllTools && getUiIsland("chat").finishAllTools(stepsEl))) {
          _tickAllSteps(stepsEl);
        }
        if (getUiIsland("chat") && getUiIsland("chat").hideToolActivity) {
          getUiIsland("chat").hideToolActivity(stepsEl);
        }
      }
      if (typing && typing.parentNode) typing.remove();
      state._streamReader = null;
      state.isStreaming   = false;
      syncSendButton();
      scrollBottom(true);   // force-scroll once stream ends regardless of user position
      // Trigger auto-save after every completed AI reply
      scheduleAutosave();
      state.silentContinuation = false;
      state.activeTurn = null;
      _drainMessageQueue();
      window.BAA.slash?.loadCommands?.().catch(() => {});
      if (!stoppedByUser && !continuingAfterAppend && !streamHadIssue && !state.isStreaming) {
        setTimeout(_requestPromptSuggestion, 120);
      }
      state._stopRequested = false;
    }
  }

  // ── Tool-step ticker helpers ───────────────────────────────────────
  function _finishStep(s) {
    if (s.classList.contains("tool-step-compaction")) {
      s.classList.add("done-compaction");
      const iconEl = s.querySelector(".compaction-spin");
      if (iconEl) { iconEl.classList.remove("compaction-spin"); iconEl.textContent = "✦"; }
    } else {
      s.classList.add("done");
      const spinEl = s.querySelector(".spin");
      if (spinEl) { spinEl.classList.remove("spin"); spinEl.textContent = "✓"; }
    }
  }
  function _tickFinishedSteps(stepsEl) {
    stepsEl.querySelectorAll('.tool-step[data-finished]:not(.done):not(.done-compaction)').forEach(_finishStep);
  }
  function _tickAllSteps(stepsEl) {
    stepsEl.querySelectorAll(".tool-step:not(.done):not(.done-compaction)").forEach(_finishStep);
  }

  function _showToolActivity(ctx) {
    return !!(getUiIsland("chat")
      && getUiIsland("chat").showToolActivity
      && getUiIsland("chat").showToolActivity(ctx.stepsEl, ctx.text, { force: !!ctx.force }));
  }

  let tailActivityTimer = null;
  let _lastSentInput = ""; // tracks last text submitted for retry

  function _cancelTailActivity() {
    if (!tailActivityTimer) return;
    clearTimeout(tailActivityTimer);
    tailActivityTimer = null;
  }

  function _showTailActivity(ctx, text = "") {
    return _showToolActivity({ ...ctx, text, force: true });
  }

  function _scheduleTailActivity(ctx, text = "") {
    _cancelTailActivity();
    _showTailActivity(ctx, text);
    scrollBottom();
    return true;
  }

  function _hideToolActivity(ctx) {
    _cancelTailActivity();
    return !!(getUiIsland("chat")
      && getUiIsland("chat").hideToolActivity
      && getUiIsland("chat").hideToolActivity(ctx.stepsEl));
  }

  function _onJobEvent(ev, _ctx) {
    // JobRunner events are durable task-history state, not part of the inline
    // tool-call transcript. Keep the chat tool flow focused on tool_start/end
    // and final tool results; the history panel remains the place for job
    // progress, artifacts and cancellation.
    applyLiveEvent(ev);
  }

  function _onHookEvent(ev, ctx) {
    if (!ev?.ok && window.BAA.ui?.toast) {
      window.BAA.ui.toast(`Hook ${ev.hook_id || ""} 执行失败`, "err");
    }
    if (getUiIsland("chat")?.showToolActivity && ev?.event === "pre_tool_use") {
      getUiIsland("chat").showToolActivity(ctx.stepsEl, "正在应用 Hooks…");
    }
  }

  function _onAgentActivity(ev, ctx) {
    _scheduleTailActivity(ctx, ev?.message || ev?.text || "");
  }

  function _onTeamEvent(ev, _ctx) {
    const team = ev?.team || "";
    const member = ev?.member || "";
    const event = ev?.event || "";
    if (window.BAA.ui?.toast && event === "member_failed") {
      window.BAA.ui.toast(`团队成员 ${member || team} 执行失败`, "err");
    }
    if (window.BAA.teams?.refresh) {
      window.BAA.teams.refresh({ silent: true }).catch(() => {});
    }
  }

  const _onJobCreated = _onJobEvent;
  const _onJobStarted = _onJobEvent;
  const _onJobProgress = _onJobEvent;
  const _onArtifactCreated = _onJobEvent;
  const _onJobDone = _onJobEvent;
  const _onJobError = _onJobEvent;
  const _onJobCanceled = _onJobEvent;

  // ── SSE event handlers (object-table dispatch) ─────────────────────
  function _onToolStart(ev, ctx) {
    _cancelTailActivity();
    if (ctx.typing && ctx.typing.parentNode) ctx.typing.remove();
    if (getUiIsland("chat") && getUiIsland("chat").startTool) {
      if (getUiIsland("chat").startTool(ctx.stepsEl, ev)) {
        scrollBottom();
        return;
      }
    }
    _hideToolActivity(ctx);
    _tickFinishedSteps(ctx.stepsEl);
    const isCompaction = ev.tool === "compaction";
    const s = document.createElement(isCompaction ? "div" : "details");
    s.className = isCompaction ? "tool-step tool-step-compaction" : "tool-step";
    s.dataset.tool = ev.tool || "";
    if (!isCompaction) s.open = false;
    const shortText = esc(String(ev.display || ev.detail || "").replace(/\s+/g, " ").trim());
    const fullText  = esc(ev.detail || ev.display || "");
    const icon      = isCompaction ? `<span class="compaction-spin">⟳</span>` : `<span class="spin">⟳</span>`;
    s.innerHTML = isCompaction
      ? `${icon}<span class="tool-step-text">${fullText}</span>`
      : `<summary class="tool-step-head">${icon}<span class="tool-step-text">${shortText}</span></summary><div class="tool-step-detail">${fullText}</div>`;
    ctx.stepsEl.appendChild(s);
    scrollBottom();
  }

  function _onKnowledgeRefs(ev, ctx) {
    if (getUiIsland("chat") && getUiIsland("chat").setKnowledgeRefs) {
      if (getUiIsland("chat").setKnowledgeRefs(ctx.stepsEl, ev)) {
        _scheduleTailActivity(ctx);
        scrollBottom();
        return;
      }
    }
    const refs = Array.isArray(ev.refs) ? ev.refs : [];
    const steps = [...ctx.stepsEl.querySelectorAll('.tool-step[data-tool="query_knowledge"]')];
    const step = steps[steps.length - 1];
    if (!step) return;

    const old = step.nextElementSibling;
    if (old && old.classList.contains("knowledge-refs")) old.remove();

    const panel = document.createElement("details");
    panel.className = "knowledge-refs";
    panel.open = false;

    const summary = document.createElement("summary");
    summary.textContent = refs.length
      ? `引用来源（${refs.length} 条）`
      : "引用来源（未命中）";
    panel.appendChild(summary);

    const list = document.createElement("div");
    list.className = "knowledge-ref-list";
    if (!refs.length) {
      const empty = document.createElement("div");
      empty.className = "knowledge-ref-empty";
      empty.textContent = "本次知识库检索没有命中可引用条目。";
      list.appendChild(empty);
    } else {
      refs.forEach(ref => {
        const item = document.createElement("div");
        item.className = "knowledge-ref-item";
        const score = ref.score !== "" && ref.score !== null && ref.score !== undefined
          ? `<span class="knowledge-ref-score">score ${esc(String(ref.score))}</span>`
          : "";
        item.innerHTML = `
          <div class="knowledge-ref-head">
            <span class="knowledge-ref-type">${esc(ref.type || "来源")}</span>
            <span class="knowledge-ref-title">${esc(ref.title || ref.source || "未命名来源")}</span>
            ${score}
          </div>
          ${ref.source ? `<div class="knowledge-ref-source">${esc(ref.source)}</div>` : ""}
          ${ref.snippet ? `<div class="knowledge-ref-snippet">${esc(ref.snippet)}</div>` : ""}`;
        list.appendChild(item);
      });
    }
    panel.appendChild(list);
    step.after(panel);
    _scheduleTailActivity(ctx);
    scrollBottom();
  }

  function _attachPanelAfterStep(ctx, toolName, className, panel) {
    const steps = [...ctx.stepsEl.querySelectorAll(`.tool-step[data-tool="${toolName}"]`)];
    const step = steps[steps.length - 1];
    if (!step) return false;
    const old = step.parentElement.querySelector(`.${className}[data-for-step="${step.dataset.stepId || ""}"]`);
    if (old) old.remove();
    if (!step.dataset.stepId) step.dataset.stepId = `${toolName}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    panel.dataset.forStep = step.dataset.stepId;
    step.after(panel);
    scrollBottom();
    return true;
  }

  function _onDataRefs(ev, ctx) {
    if (getUiIsland("chat") && getUiIsland("chat").setDataRefs) {
      if (getUiIsland("chat").setDataRefs(ctx.stepsEl, ev)) {
        _scheduleTailActivity(ctx);
        scrollBottom();
        return;
      }
    }
    const refs = Array.isArray(ev.refs) ? ev.refs : [];
    if (!refs.length) return;
    const panel = document.createElement("details");
    panel.className = "data-refs";
    panel.open = false;
    const summary = document.createElement("summary");
    summary.textContent = `数据依据（${refs.length} 条）`;
    panel.appendChild(summary);

    const list = document.createElement("div");
    list.className = "knowledge-ref-list";
    refs.forEach(ref => {
      const item = document.createElement("div");
      item.className = "knowledge-ref-item";
      const rows = ref.rows !== null && ref.rows !== undefined
        ? `<span class="knowledge-ref-score">${esc(String(ref.rows))} rows</span>`
        : "";
      item.innerHTML = `
        <div class="knowledge-ref-head">
          <span class="knowledge-ref-type">${esc(ref.type || "数据")}</span>
          <span class="knowledge-ref-title">${esc(ref.title || "SQL 查询")}</span>
          ${rows}
        </div>
        ${ref.source ? `<div class="knowledge-ref-source">${esc(ref.source)}</div>` : ""}
        ${ref.snippet ? `<div class="knowledge-ref-snippet">${esc(ref.snippet)}</div>` : ""}`;
      list.appendChild(item);
    });
    panel.appendChild(list);
    _attachPanelAfterStep(ctx, "query_data", "data-refs", panel)
      || _attachPanelAfterStep(ctx, "create_analysis_table", "data-refs", panel)
      || _attachPanelAfterStep(ctx, "run_analysis", "data-refs", panel)
      || _attachPanelAfterStep(ctx, "generate_chart", "data-refs", panel);
    _scheduleTailActivity(ctx);
  }

  function _onToolAudit(ev, ctx) {
    if (getUiIsland("chat") && getUiIsland("chat").setToolAudit) {
      if (getUiIsland("chat").setToolAudit(ctx.stepsEl, ev)) {
        _scheduleTailActivity(ctx);
        scrollBottom();
        return;
      }
    }
    const tool = ev.tool || "";
    if (!tool) return;
    const panel = document.createElement(ev.content || ev.summary ? "details" : "div");
    panel.className = ev.ok === false ? "tool-audit tool-audit-error" : "tool-audit";
    panel.dataset.tool = tool;
    if (panel.tagName === "DETAILS") panel.open = false;
    const elapsed = ev.elapsed_seconds !== undefined ? `${ev.elapsed_seconds}s` : "";
    const sourceCount = Array.isArray(ev.sources) ? ev.sources.length : 0;
    const artifactCount = Array.isArray(ev.artifacts) ? ev.artifacts.length : 0;
    const bits = [
      ev.parallel ? "并行" : "",
      elapsed && `耗时 ${esc(elapsed)}`,
      sourceCount ? `来源 ${sourceCount}` : "",
      artifactCount ? `产物 ${artifactCount}` : "",
      ev.error ? `错误 ${esc(ev.error)}` : "",
    ].filter(Boolean);
    const statusLine = document.createElement(panel.tagName === "DETAILS" ? "summary" : "span");
    statusLine.className = "tool-audit-status";
    statusLine.textContent = bits.length ? bits.join(" · ") : "工具执行完成";
    panel.appendChild(statusLine);
    const content = ev.content ?? ev.data ?? ev.summary;
    if (content) {
      panel.classList.add("tool-audit-has-summary");
      const body = document.createElement("div");
      body.className = "tool-audit-summary";
      body.textContent = String(content);
      panel.appendChild(body);
    }
    if (ev.args_preview) {
      try { panel.title = JSON.stringify(ev.args_preview, null, 2); } catch (_) {}
    }
    _attachPanelAfterStep(ctx, tool, "tool-audit", panel);
    _onToolEnd({ tool }, ctx);
    _scheduleTailActivity(ctx);
  }

  function _onToolEnd(ev, ctx) {
    if (getUiIsland("chat") && getUiIsland("chat").endTool) {
      if (getUiIsland("chat").endTool(ctx.stepsEl, ev)) {
        _scheduleTailActivity(ctx);
        scrollBottom();
        return;
      }
    }
    const step = ctx.stepsEl.querySelector(".tool-step:not(.done):not([data-finished])");
    if (!step) return;
    step.dataset.finished = "1";
    if (step.classList.contains("tool-step-compaction")) {
      step.classList.add("done-compaction");
      const iconEl = step.querySelector(".compaction-spin");
      if (iconEl) { iconEl.classList.remove("compaction-spin"); iconEl.textContent = "✦"; }
    }
    _scheduleTailActivity(ctx);
  }

  // IntersectionObserver: 统一管理所有图表 iframe 的懒加载。
  // 浏览器原生 loading="lazy" 的问题是：SSE 流结束时第二个图表可能尚未进入
  // 视口，浏览器永远不会发起请求，导致空白。改用 IO 可以精确控制触发时机，
  // 并在 iframe 加载完成后自动断开观察，避免内存泄漏。
  const _chartObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      const iframe = entry.target;
      if (!iframe.src) {
        iframe.src = iframe.dataset.src;
      }
      _chartObserver.unobserve(iframe);
    });
  }, { rootMargin: "200px" }); // 提前 200px 预加载，消除滚动白屏

  function _syncChartFrameHeight(iframe) {
    try {
      const doc = iframe.contentDocument;
      if (!doc?.body) return;

      // Plotly.newPlot() may still be laying out when the iframe load event fires.
      // Keep the frame from collapsing to the title-only height, and repair old
      // saved charts whose graph div used height:100% without a definite parent.
      const plotly = iframe.contentWindow?.Plotly;
      doc.querySelectorAll(".plotly-graph-div").forEach(plot => {
        if (plot.getBoundingClientRect().height < 240) {
          plot.style.minHeight = "360px";
        }
        if (plotly?.Plots?.resize && plot.classList.contains("js-plotly-plot")) {
          plotly.Plots.resize(plot);
        }
      });

      const contentHeight = Math.max(
        doc.body.scrollHeight,
        doc.documentElement?.scrollHeight || 0,
      );
      iframe.style.height = Math.max(420, contentHeight + 20) + "px";
    } catch (_) {}
  }

  function _buildChartFrame(chartId, chartTitle = "", chartType = "") {
    const wrap = document.createElement("div");
    wrap.className = "chart-frame";
    if (chartTitle) {
      const heading = document.createElement("div");
      heading.className = "chart-frame-title";
      heading.textContent = chartType && chartTitle !== chartType
        ? `${chartTitle} · ${chartType}`
        : chartTitle;
      wrap.appendChild(heading);
    }
    const expandBtn = document.createElement("button");
    expandBtn.className = "chart-expand-btn";
    expandBtn.title = "在新标签页打开";
    expandBtn.textContent = "⛶";
    expandBtn.addEventListener("click", () => window.open(`/api/chart/${chartId}`, "_blank"));
    const iframe = document.createElement("iframe");
    iframe.setAttribute("sandbox", "allow-scripts allow-same-origin");
    iframe.setAttribute("referrerpolicy", "no-referrer");
    iframe.setAttribute("title", chartTitle || chartType || "分析图表");
    // 不设置 src，先存入 data-src；由 IntersectionObserver 在进入视口时赋值。
    // 这样视口内的图表立即加载，视口外的在滚动到附近时才发请求，避免同时并发
    // 多个 iframe 请求阻塞浏览器连接池。
    iframe.dataset.src = `/api/chart/${chartId}`;
    iframe.addEventListener("load", () => {
      requestAnimationFrame(() => _syncChartFrameHeight(iframe));
      setTimeout(() => _syncChartFrameHeight(iframe), 250);
    });
    wrap.appendChild(expandBtn);
    wrap.appendChild(iframe);
    _chartObserver.observe(iframe);
    return wrap;
  }

  function _onChartRef(ev, ctx) {
    if (getUiIsland("chat") && getUiIsland("chat").addChartRef) {
      if (getUiIsland("chat").addChartRef(
        ctx.bubbleEl, ev.chart_id, ev.title || "", ev.chart_type || ""
      )) {
        _scheduleTailActivity(ctx);
        scrollBottom();
        return;
      }
    }
    // Insert chart inside the msg-body, just before the text bubble,
    // so it shares the same left-border / background visual context.
    const wrap = _buildChartFrame(
      ev.chart_id, ev.title || "", ev.chart_type || ""
    );
    ctx.bubbleEl.before(wrap);
    _scheduleTailActivity(ctx);
    scrollBottom();
  }

  function _onTextDelta(ev, ctx) {
    _cancelTailActivity();
    _hideToolActivity(ctx);
    if (getUiIsland("chat") && getUiIsland("chat").appendTextDelta) {
      if (getUiIsland("chat").appendTextDelta(ctx.bubbleEl, ev.content, ctx.typing)) {
        scrollBottom();
        return;
      }
    }
    if (ctx.typing.parentNode) ctx.typing.remove();
    ctx.bubbleEl.insertAdjacentText("beforeend", ev.content || "");
    scrollBottom();
  }

  function _buildReasoningBlock(content) {
    const block = document.createElement("div");
    block.className = "reasoning-block";
    const toggle = document.createElement("div");
    toggle.className = "reasoning-toggle";
    toggle.innerHTML = `<span class="reasoning-arrow">▶</span> ${t('reasoning_toggle')}`;
    const body = document.createElement("div");
    body.className = "reasoning-body";
    body.textContent = content || "";
    toggle.addEventListener("click", () => {
      toggle.classList.toggle("open");
      body.classList.toggle("open");
    });
    block.appendChild(toggle);
    block.appendChild(body);
    return block;
  }

  function _onReasoning(ev, ctx) {
    _cancelTailActivity();
    if (getUiIsland("chat") && getUiIsland("chat").addReasoning) {
      if (getUiIsland("chat").addReasoning(ctx.bubbleEl, ev.content, ctx.typing)) {
        scrollBottom();
        return;
      }
    }
    if (ctx.typing.parentNode) ctx.typing.remove();
    const block = _buildReasoningBlock(ev.content);
    ctx.bubbleEl.before(block);
    _showToolActivity(ctx);
    scrollBottom();
  }

  function _onText(ev, ctx) {
    _cancelTailActivity();
    const md = ev.content || "";
    _hideToolActivity(ctx);
    if (!md.trim()) return;
    if (ctx.typing.parentNode) ctx.typing.remove();
    if (!(getUiIsland("chat") && getUiIsland("chat").finishAllTools && getUiIsland("chat").finishAllTools(ctx.stepsEl))) {
      _tickAllSteps(ctx.stepsEl);
    }
    const renderedByVue = getUiIsland("chat") && getUiIsland("chat").setMarkdown
      ? getUiIsland("chat").setMarkdown(ctx.bubbleEl, md, ctx.typing)
      : false;
    if (!renderedByVue) ctx.bubbleEl.innerHTML = renderMd(md);
    // Attach hover-revealed action bar (copy) to the assistant message body.
    // The body persists across the bubble innerHTML rewrite, so we attach there.
    _ensureMsgActions(ctx.bubbleEl, md);
    // 绑定气泡内图片：点击新标签打开原图、加载失败时标注
    bindBubbleImages(ctx.bubbleEl);
    scrollBottom();
  }

  // Build / refresh the "copy" action bar at the bottom of an assistant message body.
  function _ensureMsgActions(bubbleEl, markdownText) {
    const body = bubbleEl.parentNode;
    if (!body) return;
    let bar = body.querySelector(":scope > .msg-actions");
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "msg-actions";
      const copyBtn = document.createElement("button");
      copyBtn.type = "button";
      copyBtn.textContent = t('msg.copy') || "复制";
      copyBtn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(bar._currentText || "");
          copyBtn.textContent = t('msg.copied') || "已复制 ✓";
          copyBtn.classList.add("copied");
          setTimeout(() => {
            copyBtn.textContent = t('msg.copy') || "复制";
            copyBtn.classList.remove("copied");
          }, 1400);
        } catch (_) { /* clipboard blocked — fail silently */ }
      });
      bar.appendChild(copyBtn);
      body.appendChild(bar);
    }
    bar._currentText = markdownText;
  }

  function _onUsage(ev) {
    state.tokenState.promptTokens  = ev.prompt_tokens || 0;
    state.tokenState.totalInput    = ev.session_total_input  || 0;
    state.tokenState.totalOutput   = ev.session_total_output || 0;
    state.tokenState.contextWindow = ev.context_window || state.tokenState.contextWindow;
    updateTokenBar();
  }

  function _onCtxEstimate(ev) {
    state.tokenState.promptTokens  = ev.prompt_tokens || 0;
    state.tokenState.contextWindow = ev.context_window || state.tokenState.contextWindow;
    updateTokenBar();
  }

  function _onError(ev, ctx) {
    _cancelTailActivity();
    _hideToolActivity(ctx);
    if (getUiIsland("chat") && getUiIsland("chat").setError) {
      if (getUiIsland("chat").setError(ctx.bubbleEl, ev.message, ctx.typing)) return;
    }
    if (ctx.typing.parentNode) ctx.typing.remove();
    const span = document.createElement("span");
    span.className = "stream-error";
    span.textContent = `⚠ ${ev.message}`;
    ctx.bubbleEl.appendChild(span);
  }

  function _onStopped(_ev, ctx) {
    _cancelTailActivity();
    if (state.silentContinuation) {
      _showToolActivity(ctx);
      if (ctx.typing?.parentNode) ctx.typing.remove();
      return;
    }
    _hideToolActivity(ctx);
    if (!(getUiIsland("chat") && getUiIsland("chat").finishAllTools && getUiIsland("chat").finishAllTools(ctx.stepsEl))) {
      _tickAllSteps(ctx.stepsEl);
    }
    if (getUiIsland("chat") && getUiIsland("chat").markStopped) {
      if (getUiIsland("chat").markStopped(ctx.bubbleEl, t('stop_note'), ctx.typing)) return;
    }
    if (ctx.typing.parentNode) ctx.typing.remove();
    const stopNote = document.createElement("div");
    stopNote.className = "stop-note";
    stopNote.textContent = t('stop_note');
    ctx.bubbleEl.before(stopNote);
    if (!ctx.bubbleEl.textContent.trim()) ctx.bubbleEl.remove();
  }

  function _outlineMeta(ev) {
    let icon, confirmCmd, reviseCmd, confirmPayload, headerTitle;
    if (ev.type === "ppt_outline") {
      icon = "🎯"; confirmCmd = "ppt_confirm"; reviseCmd = "ppt_revise";
      headerTitle = esc(ev.title || "PPT 大纲");
      confirmPayload = { ppt_title: ev.title, ppt_slides: ev.slides };
    } else if (ev.type === "excel_outline") {
      icon = "📥"; confirmCmd = "excel_confirm"; reviseCmd = "excel_revise";
      headerTitle = esc(ev.filename || "Excel 导出");
      confirmPayload = { excel_tables: ev.tables, excel_filename: ev.filename };
    } else if (ev.type === "dashboard_outline") {
      icon = "📊"; confirmCmd = "dashboard_confirm"; reviseCmd = "dashboard_revise";
      headerTitle = esc(ev.name || "数据看板");
      confirmPayload = { dashboard_name: ev.name, dashboard_widgets: ev.widgets };
    } else { // report_outline
      icon = "📄"; confirmCmd = "report_confirm"; reviseCmd = "report_revise";
      headerTitle = esc(ev.title || "分析报告");
      confirmPayload = { report_title: ev.title, report_sections: ev.sections };
    }
    return { icon, confirmCmd, reviseCmd, confirmPayload, headerTitle };
  }

  function _onOutline(ev, ctx) {
    _cancelTailActivity();
    _hideToolActivity(ctx);
    if (ctx.typing.parentNode) ctx.typing.remove();
    if (!(getUiIsland("chat") && getUiIsland("chat").finishAllTools && getUiIsland("chat").finishAllTools(ctx.stepsEl))) {
      _tickAllSteps(ctx.stepsEl);
    }

    const meta = _outlineMeta(ev);

    if (getUiIsland("chat") && getUiIsland("chat").addOutlineCard) {
      if (getUiIsland("chat").addOutlineCard(ctx.bubbleEl, {
        icon: meta.icon,
        headerTitle: meta.headerTitle,
        markdown: ev.markdown || "",
      }, {
        onConfirm: () => sendConfirmStream({ internal_action: meta.confirmCmd, message: "确认", ...meta.confirmPayload }),
        onRevise: (editText) => {
          let message = String(editText || "");
          if (meta.reviseCmd === "ppt_revise" && meta.confirmPayload.ppt_slides)
            message = `${message}\n\n[CURRENT_SLIDES_JSON]\n${JSON.stringify(meta.confirmPayload.ppt_slides)}`;
          else if (meta.reviseCmd === "report_revise" && meta.confirmPayload.report_sections)
            message = `${message}\n\n[CURRENT_REPORT_JSON]\n${JSON.stringify({ title: meta.confirmPayload.report_title, sections: meta.confirmPayload.report_sections })}`;
          else if (meta.reviseCmd === "dashboard_revise" && meta.confirmPayload.dashboard_widgets)
            message = `${message}\n\n[CURRENT_DASHBOARD_JSON]\n${JSON.stringify({ name: meta.confirmPayload.dashboard_name, widgets: meta.confirmPayload.dashboard_widgets })}`;
          sendConfirmStream({ internal_action: meta.reviseCmd, message });
        },
        onCancel: () => {},
      })) {
        scrollBottom();
        return;
      }
    }

    _legacyOutlineBody(ev, ctx, meta);
  }

  function _legacyOutlineBody(ev, ctx, meta) {
    const { icon, confirmCmd, reviseCmd, confirmPayload, headerTitle } = meta;

    const card = document.createElement("div");
    card.className = "ppt-outline-card";
    card.innerHTML = `
      <div class="ppt-outline-header">
        <span class="ppt-outline-icon">${icon}</span>
        <span>${headerTitle}</span>
      </div>
      <div class="ppt-outline-content">${renderMd(ev.markdown || "")}</div>
      <div class="ppt-outline-edit-wrap hidden">
        <div class="ppt-outline-edit-hint">请说明希望如何修改：</div>
        <textarea class="ppt-outline-edit" rows="3" placeholder="例如：把第3张换成双栏文字，增加一张市场份额环形图…"></textarea>
      </div>
      <div class="ppt-outline-btns">
        <button class="ppt-btn ppt-btn-confirm">✅ 确认生成</button>
        <button class="ppt-btn ppt-btn-revise">✏️ 修改大纲</button>
        <button class="ppt-btn ppt-btn-cancel">✕ 取消</button>
      </div>`;
    ctx.bubbleEl.appendChild(card);
    scrollBottom();

    const editWrap   = card.querySelector(".ppt-outline-edit-wrap");
    const btnConfirm = card.querySelector(".ppt-btn-confirm");
    const btnRevise  = card.querySelector(".ppt-btn-revise");
    const btnCancel  = card.querySelector(".ppt-btn-cancel");
    const editTA     = card.querySelector(".ppt-outline-edit");

    function _lockCard() {
      [btnConfirm, btnRevise, btnCancel].forEach(b => b.disabled = true);
      editTA.disabled = true;
    }

    btnConfirm.addEventListener("click", () => {
      _lockCard();
      sendConfirmStream({ internal_action: confirmCmd, message: "确认", ...confirmPayload });
    });

    btnRevise.addEventListener("click", () => {
      const open = !editWrap.classList.contains('hidden');
      editWrap.classList.toggle('hidden', open);
      if (!open) editTA.focus();
    });

    btnCancel.addEventListener("click", () => {
      _lockCard();
      card.querySelector(".ppt-outline-btns").remove();
      const note = document.createElement("div");
      note.className = "ppt-cancelled-note";
      note.textContent = "已取消。";
      card.appendChild(note);
    });

    editTA.addEventListener("keydown", e => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const txt = editTA.value.trim();
        if (!txt) return;
        _lockCard();
        let revisePayload = { internal_action: reviseCmd, message: txt };
        if (reviseCmd === "ppt_revise" && confirmPayload.ppt_slides)
          revisePayload.message = `${txt}\n\n[CURRENT_SLIDES_JSON]\n${JSON.stringify(confirmPayload.ppt_slides)}`;
        else if (reviseCmd === "report_revise" && confirmPayload.report_sections)
          revisePayload.message = `${txt}\n\n[CURRENT_REPORT_JSON]\n${JSON.stringify({ title: confirmPayload.report_title, sections: confirmPayload.report_sections })}`;
        else if (reviseCmd === "dashboard_revise" && confirmPayload.dashboard_widgets)
          revisePayload.message = `${txt}\n\n[CURRENT_DASHBOARD_JSON]\n${JSON.stringify({ name: confirmPayload.dashboard_name, widgets: confirmPayload.dashboard_widgets })}`;
        sendConfirmStream(revisePayload);
      }
    });
  }

  function _onAskUser(ev, ctx) {
    _cancelTailActivity();
    _hideToolActivity(ctx);
    if (ctx.typing.parentNode) ctx.typing.remove();
    if (!(getUiIsland("chat") && getUiIsland("chat").finishAllTools && getUiIsland("chat").finishAllTools(ctx.stepsEl))) {
      _tickAllSteps(ctx.stepsEl);
    }

    if (getUiIsland("chat") && getUiIsland("chat").addAskUserCard) {
      if (getUiIsland("chat").addAskUserCard(ctx.bubbleEl, ev, {
        onSubmit: (answer) => sendConfirmStream({ message: answer }),
      })) {
        scrollBottom();
        return;
      }
    }

    _legacyAskUserBody(ev, ctx);
  }

  function _legacyAskUserBody(ev, ctx) {
    const multiSelect = !!ev.multi_select;
    const options = (Array.isArray(ev.options) ? ev.options : [])
      .map(option => {
        if (typeof option === "string") return option.trim();
        if (!option || typeof option !== "object") return "";
        for (const key of ["label", "text", "title", "name", "value"]) {
          if (typeof option[key] === "string" && option[key].trim()) {
            return option[key].trim();
          }
        }
        return "";
      })
      .filter((option, index, all) => option && all.indexOf(option) === index)
      .slice(0, 6);

    const card = document.createElement("div");
    card.className = "ask-user-card";

    const qEl = document.createElement("div");
    qEl.className = "ask-user-question";
    qEl.textContent = ev.question || "";
    card.appendChild(qEl);

    const chipsEl = document.createElement("div");
    chipsEl.className = "ask-user-chips";
    card.appendChild(chipsEl);

    const selected = new Set();

    function _renderChips() {
      chipsEl.innerHTML = "";
      [...options, "__other__"].forEach(opt => {
        const chip = document.createElement("button");
        chip.className = "ask-user-chip";
        chip.type = "button";
        if (opt === "__other__") {
          chip.textContent = t('ask_user.other') || "其他…";
          chip.dataset.other = "1";
        } else {
          chip.textContent = opt;
          chip.dataset.value = opt;
        }
        if (selected.has(opt)) chip.classList.add("selected");
        chip.addEventListener("click", () => {
          if (locked) return;
          if (chip.dataset.other) {
            otherWrap.classList.toggle('hidden');
            if (!otherWrap.classList.contains('hidden')) otherInput.focus();
            return;
          }
          if (multiSelect) {
            if (selected.has(opt)) selected.delete(opt);
            else selected.add(opt);
            chip.classList.toggle("selected", selected.has(opt));
          } else {
            _submit(opt);
          }
        });
        chipsEl.appendChild(chip);
      });
    }

    const otherWrap = document.createElement("div");
    otherWrap.className = "ask-user-other-wrap";
    otherWrap.classList.add('hidden');
    const otherInput = document.createElement("input");
    otherInput.type = "text";
    otherInput.className = "ask-user-other-input";
    otherInput.placeholder = t('ask_user.other_placeholder') || "请输入您的回答…";
    const otherBtn = document.createElement("button");
    otherBtn.type = "button";
    otherBtn.className = "ask-user-other-btn";
    otherBtn.textContent = t('ask_user.submit') || "提交";
    otherWrap.appendChild(otherInput);
    otherWrap.appendChild(otherBtn);
    card.appendChild(otherWrap);

    let submitBtn = null;
    if (multiSelect) {
      submitBtn = document.createElement("button");
      submitBtn.type = "button";
      submitBtn.className = "ask-user-submit-btn";
      submitBtn.textContent = t('ask_user.confirm') || "确认选择";
      card.appendChild(submitBtn);
    }

    ctx.bubbleEl.appendChild(card);
    scrollBottom();

    let locked = false;
    function _lock() {
      locked = true;
      card.querySelectorAll("button, input").forEach(el => { el.disabled = true; });
    }

    function _submit(answer) {
      _lock();
      sendConfirmStream({ message: answer });
    }

    otherBtn.addEventListener("click", () => {
      if (locked) return;
      const val = otherInput.value.trim();
      if (val) _submit(val);
    });
    otherInput.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); otherBtn.click(); }
    });

    if (submitBtn) {
      submitBtn.addEventListener("click", () => {
        if (locked) return;
        const vals = [...selected];
        const otherVal = !otherWrap.classList.contains('hidden') ? otherInput.value.trim() : "";
        if (otherVal) vals.push(otherVal);
        if (!vals.length) return;
        _submit(vals.join("、"));
      });
    }

    _renderChips();
  }

  const SSE_HANDLERS = {
    tool_start:         _onToolStart,
    tool_end:           _onToolEnd,
    knowledge_refs:     _onKnowledgeRefs,
    data_refs:          _onDataRefs,
    tool_audit:         _onToolAudit,
    hook_event:         _onHookEvent,
    agent_activity:     _onAgentActivity,
    team_event:         _onTeamEvent,
    chart_ref:          _onChartRef,
    text_delta:         _onTextDelta,
    reasoning:          _onReasoning,
    text:               _onText,
    usage:              _onUsage,
    context_estimate:   _onCtxEstimate,
    error:              _onError,
    stopped:            _onStopped,
    ppt_outline:        _onOutline,
    excel_outline:      _onOutline,
    report_outline:     _onOutline,
    dashboard_outline:  _onOutline,
    ask_user:           _onAskUser,
    job_created:        _onJobCreated,
    job_started:        _onJobStarted,
    job_progress:       _onJobProgress,
    artifact_created:   _onArtifactCreated,
    job_done:           _onJobDone,
    job_error:          _onJobError,
    job_canceled:       _onJobCanceled,
  };

  const PAINT_BREAK_EVENTS = new Set(["tool_start", "tool_end", "knowledge_refs", "data_refs", "tool_audit", "agent_activity"]);
  const STREAM_PAINT_EVENTS = new Set(["text_delta"]);
  let lastStreamPaintAt = 0;

  function _nextPaint() {
    return new Promise(resolve => {
      if (window.requestAnimationFrame) {
        requestAnimationFrame(() => setTimeout(resolve, 0));
      } else {
        setTimeout(resolve, 0);
      }
    });
  }

  async function handleEvent(ev, stepsEl, bubbleEl, typing) {
    const fn = SSE_HANDLERS[ev.type];
    if (fn) fn(ev, { stepsEl, bubbleEl, typing });
    if (PAINT_BREAK_EVENTS.has(ev.type)) await _nextPaint();
    else if (STREAM_PAINT_EVENTS.has(ev.type) && Date.now() - lastStreamPaintAt > 50) {
      lastStreamPaintAt = Date.now();
      await _nextPaint();
    }
  }

  // ── New chat ───────────────────────────────────────────────────────
  async function newChat() {
    _invalidatePromptSuggestion();
    state.pendingMessages.length = 0;
    state.editingQueuedId = "";
    _refreshQueuePositions();
    try {
      const r = await fetch("/api/session/new", { method: "POST" });
      const data = await r.json();
      state.SID = data.session_id;
      state.sessionName = "新会话";
      state.loadedSessionFilename = "";
      localStorage.setItem("baa_session_id", state.SID);
    sessionStorage.setItem("baa_session_id", state.SID);
    await switchJobHistorySession(state.SID);
    } catch (_) {
      // Front-end resets either way; backend will rebuild on next send.
    }

    // 新 session 创建后立即将前端当前选中的模型同步给后端，
    // 否则后端 session 会用默认模型（deepseek）响应第一条消息。
    const currentProvider = $("model-sel")?.value;
    if (currentProvider && state.SID) {
      fetch(`/api/session/${state.SID}/model`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: currentProvider }),
      }).catch(() => {});
    }

    clearCmd();
    clearSkill();
    await Promise.all([
      window.BAA.slash?.loadCommands?.(),
      window.BAA.skills?.loadSkills?.(),
    ]);
    resetSourceState();
    setLoadedName("", "");
    window.BAA.sidebar?.setSessionName?.("新会话", "");
    clearMessages();
    state.tokenState = { promptTokens: 0, totalInput: 0, totalOutput: 0, contextWindow: null };
    updateTokenBar();
    showWelcome();
  }

  function retryLast() {
    if (!_lastSentInput || state.isStreaming) return;
    const input = $("msg-input");
    if (!input) return;
    input.value = _lastSentInput;
    input.style.height = "auto";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    sendMessage();
  }

export const chatStream = Object.freeze({
    onSendOrStop, sendMessage, sendConfirmStream, stopStreaming,
    onComposerInput, syncComposerPlaceholder,
    clearPromptSuggestion: _invalidatePromptSuggestion,
    handleEvent, newChat, syncSendButton, buildChartFrame: _buildChartFrame,
    buildReasoningBlock: _buildReasoningBlock,
    retryLast,
});
