// General settings domain (prompt suggestion / teams / auto-match skill toggles).
//
// Extracted verbatim from app_settings.js (F5 follow-up, General domain).
// Pure file move + ctx injection: function bodies carry over unchanged, only
// bare `appState` / `uiState` / `draw` references resolve via ctx aliases.
// No HTML / CSS / fetch URL / rendering logic changes.
//
// Cross-domain deps: ctx.memory (the long-term memory toggle forwards to the
// Memory domain). The fromStorage helpers + MEMORY_ENABLED_KEY are module-level
// exports: app_settings init() runs them before any domain instance exists.

import { chatStream } from "../../features/chat-stream.js";
import { _readLocalSetting, _writeLocalSetting, renderSwitch, _renderPanelHead } from "./shared.js";

const PROMPT_SUGGESTION_KEY = "baa_prompt_suggestion_enabled";
const TEAMS_KEY = "baa_teams_enabled";
const AUTO_MATCH_SKILL_KEY = "baa_auto_match_skill";
export const MEMORY_ENABLED_KEY = "baa_memory_enabled";

export function _enabledFromStorage() {
    return _readLocalSetting(PROMPT_SUGGESTION_KEY, "1") !== "0";
  }

export function _teamsEnabledFromStorage() {
    return _readLocalSetting(TEAMS_KEY, "0") === "1";
  }

export function _autoMatchSkillFromStorage() {
    return _readLocalSetting(AUTO_MATCH_SKILL_KEY, "1") !== "0";
  }

export function _memoryEnabledFromStorage() {
    return _readLocalSetting(MEMORY_ENABLED_KEY, "1") !== "0";
  }

/**
 * @param {{uiState: object, draw: () => void, appState: object, state: object, root: HTMLElement|null, memory: object}} ctx
 */
export function createGeneralDomain(ctx) {
  const Vue = window.Vue;

  function setPromptSuggestionEnabled(enabled) {
    const appState = ctx.appState;
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    appState.promptSuggestionEnabled = !!enabled;
    _writeLocalSetting(PROMPT_SUGGESTION_KEY, appState.promptSuggestionEnabled ? "1" : "0");
    if (!appState.promptSuggestionEnabled) {
      chatStream.clearPromptSuggestion();
    }
    if (uiState) {
      uiState.promptSuggestionEnabled = appState.promptSuggestionEnabled;
      draw();
    }
  }

  function setTeamsEnabled(enabled) {
    const appState = ctx.appState;
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    appState.teamsEnabled = !!enabled;
    _writeLocalSetting(TEAMS_KEY, appState.teamsEnabled ? "1" : "0");
    if (uiState) {
      uiState.teamsEnabled = appState.teamsEnabled;
      draw();
    }
  }

  function setAutoMatchSkill(enabled) {
    const appState = ctx.appState;
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    appState.autoMatchSkill = !!enabled;
    _writeLocalSetting(AUTO_MATCH_SKILL_KEY, appState.autoMatchSkill ? "1" : "0");
    if (uiState) {
      uiState.autoMatchSkill = appState.autoMatchSkill;
      draw();
    }
  }

  // Unified panel header for all settings tabs: title + description on the
  // left, action buttons on the right. Replaces the previously divergent
  // settings-primary-title / embed-settings-head / lifecycle-hero styles.

  function renderGeneral() {
    const uiState = ctx.uiState;
    const memory = ctx.memory;
    return Vue.h("section", { class: "app-settings-panel" }, [
      _renderPanelHead("通用", "提示建议、团队协作与记忆功能的总开关。", null),
      Vue.h("label", { class: "app-setting-row" }, [
        Vue.h("span", { class: "app-setting-copy" }, [
          Vue.h("strong", null, "Prompt Suggestion"),
          Vue.h("span", null, "AI 回复完成后，在输入框中以浅色提示下一步可能要问的问题；按 Tab 自动填充。"),
        ]),
        renderSwitch(uiState.promptSuggestionEnabled, setPromptSuggestionEnabled),
      ]),
      Vue.h("label", { class: "app-setting-row" }, [
        Vue.h("span", { class: "app-setting-copy" }, [
          Vue.h("strong", null, "Teams"),
          Vue.h("span", null, "开启后 Agent 会自动构建轻量分析团队并委派子任务，以更多 token 换取准确度和速度。默认关闭。"),
        ]),
        renderSwitch(uiState.teamsEnabled, setTeamsEnabled),
      ]),
      Vue.h("label", { class: "app-setting-row" }, [
        Vue.h("span", { class: "app-setting-copy" }, [
          Vue.h("strong", null, "自动匹配 Skill"),
          Vue.h("span", null, "开启后 Agent 会根据用户提问自动检索并激活匹配的分析 Skill（SWOT、漏斗等）。关闭后仅可通过 / 命令手动激活。默认开启。"),
        ]),
        renderSwitch(uiState.autoMatchSkill, setAutoMatchSkill),
      ]),
      Vue.h("label", { class: "app-setting-row" }, [
        Vue.h("span", { class: "app-setting-copy" }, [
          Vue.h("strong", null, "长期记忆"),
          Vue.h("span", null, "自动记录你的偏好、纠正与口径结论，并在新会话开始时注入。关闭后不再提取、注入与整理记忆。默认开启。"),
        ]),
        renderSwitch(uiState.memoryEnabled, (v) => memory.setMemoryEnabled(v)),
      ]),
    ]);
  }

  return {
    setPromptSuggestionEnabled, setTeamsEnabled, setAutoMatchSkill, renderGeneral,
  };
}
