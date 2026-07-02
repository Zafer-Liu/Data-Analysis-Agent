// Centralized mutable state shared across compatibility modules.
// Other modules import via window.BAA.state; setters are exposed where cross-module writes are needed.
(function () {
  const state = {
    SID: null,
    sessionName: "新会话",
    loadedSessionFilename: "",
    srcConnected: false,
    srcName: "",
    srcHintKey: 'sidebar.hint.noconn',
    schemaText: "",
    // Multi-source: [{id, name, type, active}]
    sources: [],
    isStreaming: false,
    _stopRequested: false,
    activeTurn: null,
    silentContinuation: false,
    pendingMessages: [],
    editingQueuedId: "",
    promptSuggestionEnabled: localStorage.getItem("baa_prompt_suggestion_enabled") !== "0",
    teamsEnabled: localStorage.getItem("baa_teams_enabled") === "1",
    promptSuggestionRequestId: 0,
    promptSuggestionText: "",
    _applyingPromptSuggestion: false,
    activeCommand: "",
    activeSkill: "",
    slashPopupIndex: 0,
    skillPickerIndex: 0,
    tokenState: { promptTokens: 0, totalInput: 0, totalOutput: 0, contextWindow: null },
    modelConfigs: {},
    _streamReader: null,
    _editingCustomProvider: null,
    _previewData: null,
    _previewCache: {},
    _previewSid: null,
    // Table explicitly selected from Data Preview for subsequent Agent turns.
    analysisContext: null,
    _modalResizing: false,
  };

  window.BAA = window.BAA || {};
  window.BAA.state = state;
})();
