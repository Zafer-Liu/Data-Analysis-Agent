// Compatibility application settings rendered as a Vue island.
import { state as appState, state } from "../core/runtime.js";
import { makeSettingsCtx } from "./settings/shared.js";
import { createGpuDomain } from "./settings/gpu.js";
import { createLocalServicesDomain } from "./settings/local_services.js";
import { createMemoryDomain } from "./settings/memory.js";
import { createHooksDomain, DEFAULT_HOOKS_TEXT } from "./settings/hooks.js";
import { createLlmDomain } from "./settings/llm.js";
import { createEmbedDomain } from "./settings/embed.js";
import { createFeishuDomain } from "./settings/feishu.js";
import { createStorageDomain } from "./settings/storage.js";
import {
  createGeneralDomain,
  _enabledFromStorage,
  _teamsEnabledFromStorage,
  _autoMatchSkillFromStorage,
  _memoryEnabledFromStorage,
  MEMORY_ENABLED_KEY,
} from "./settings/general.js";

  const Vue = window.Vue;
  const root = document.getElementById("app-settings-root");


  let uiState = null;
  let draw = () => {};

  let gpu;
  let localServices;
  let memory;
  let hooks;
  let llm;
  let embed;
  let feishu;
  let storage;
  let general;


  function renderApp() {
    if (!root) return;
    const tabs = [
      ["general", "通用"],
      ["llm", "LLM模型"],
      ["model", "知识库检索"],
      ["services", "本地服务"],
      ["gpu", "GPU算力"],
      ["memory", "记忆"],
      ["bots", "机器人"],
      ["hooks", "Hooks"],
      ["storage", "存储"],
    ];
    Vue.render(Vue.h("div", { class: "app-settings-layout" }, [
      Vue.h("aside", { class: "app-settings-nav", "aria-label": "Settings sections" }, tabs.map(([id, label, status]) =>
        Vue.h("button", {
          class: `app-settings-nav-item${uiState.tab === id ? " active" : ""}${status ? " is-planned" : ""}`,
          type: "button",
          disabled: Boolean(status),
          title: status ? `${label}${status}` : label,
          onClick: () => { uiState.tab = id; draw(); if (id === "services") localServices.load(); if (id === "storage") storage.loadStorage(); if (id === "memory") memory.loadMemory(); if (id === "gpu") { gpu.loadGpuStatus(); gpu.loadGpuConnections(); } if (id === "bots") feishu.loadFeishuBot(); },
        }, [
          Vue.h("span", { class: "app-settings-nav-label" }, label),
          status ? Vue.h("span", { class: "app-settings-nav-status" }, status) : null,
        ])
      )),
      uiState.tab === "storage" ? storage.renderStorage()
        : uiState.tab === "hooks" ? hooks.renderHooks()
        : uiState.tab === "bots" ? feishu.renderBots()
        : uiState.tab === "memory" ? memory.renderMemory()
        : uiState.tab === "model" ? embed.renderModel()
        : uiState.tab === "llm" ? llm.renderLlm()
        : uiState.tab === "services" ? localServices.render()
        : uiState.tab === "gpu" ? gpu.renderGpu()
        : general.renderGeneral(),
    ]), root);
  }

  function init() {
    appState.promptSuggestionEnabled = _enabledFromStorage();
    appState.teamsEnabled = _teamsEnabledFromStorage();
    appState.autoMatchSkill = _autoMatchSkillFromStorage();
    appState.memoryEnabled = _memoryEnabledFromStorage();
    if (!root || !Vue?.h || !Vue?.render || !Vue?.reactive) return;
    uiState = Vue.reactive({
      tab: "general",
      promptSuggestionEnabled: appState.promptSuggestionEnabled,
      teamsEnabled: appState.teamsEnabled,
      autoMatchSkill: appState.autoMatchSkill,
      hooksText: DEFAULT_HOOKS_TEXT,
      hooksStatus: "",
      hooksStatusType: "ok",
      hooksLoading: false,
      hooksRuntime: { enabled: false, active_hooks: [], enabled_count: 0, runnable_count: 0, pending_count: 0, configured_count: 0 },
      hookHistory: [],
      hookHistoryAvailable: true,
      hookHistoryLoading: false,
      customHookOpen: false,
      customHookName: "",
      customHookNames: {},
      testEvent: "turn_start",
      feishuBot: { enabled: false, configured: false, app_id: "", app_id_masked: "", app_secret_configured: false, event_verification_token_configured: false, inbound_transport: "long_connection", receive_id_type: "chat_id", receive_id: "", receive_id_masked: "", updated_at: "" },
      feishuBotAppSecretDraft: "",
      feishuBotVerificationTokenDraft: "",
      feishuBotLoading: false,
      feishuBotChats: [],
      feishuBotChatsLoading: false,
      feishuBotChatsStatus: "",
      feishuBotStatus: "",
      feishuBotStatusType: "ok",
      botChannel: "feishu",
      bgeInstalled: false,
      bgeNeural: false,
      bgeDownloading: false,
      bgeStatus: "",
      bgeStatusType: "ok",
      embedMode: "auto",
      embedActive: "hash",
      embedDim: 384,
      embedModel: "",
      embedCloudUrl: "",
      embedCloudAvailable: false,
      embedCloudConfigured: false,
      embedCloudStatus: "unavailable",
      embedLocalAvailable: false,
      embedSwitching: false,
      embedRebuilding: false,
      embedChecking: false,
      cloudUrl: "https://embed.zafer-liu-product.xyz",
      cloudModel: "bge-large-zh",
      cloudToken: "",
      cloudTokenConfigured: false,
      cloudSaving: false,
      lifecycleReport: null,
      lifecycleTrash: [],
      lifecycleArtifactTrash: [],
      lifecycleUploadTrash: [],
      lifecycleMemoryTrash: [],
      lifecycleLoading: false,
      lifecycleReclaiming: false,
      lifecycleArtifactReclaiming: false,
      lifecycleUploadReclaiming: false,
      lifecycleMemoryReclaiming: false,
      lifecycleArtifactBusyKey: "",
      lifecycleUploadBusyKey: "",
      lifecycleMemoryBusyKey: "",
      lifecycleRecyclingKey: "",
      lifecycleRetentionPreset: "forever",
      lifecycleRetentionCustomDays: 30,
      lifecycleStatus: "",
      lifecyclePreview: null,
      lifecycleReferencePreview: null,
      lifecycleUploadsPreview: null,
      lifecycleWorkspacePreview: null,
      lifecycleAudit: [],
      lifecycleHookHistory: [],
      lifecycleAuditFilter: "all",
      lifecycleAdvancedOpen: false,
      browserStorage: null,
      browserStorageLoading: false,
      browserStorageError: "",
      memoryRecords: [],
      memoryWorkspaceMounted: false,
      memoryEnabled: _memoryEnabledFromStorage(),
      memoryLoading: false,
      memoryStatus: "",
      memoryStatusType: "ok",
      memoryEditing: null,
      memoryFormOpen: false,
      memoryAdvancedOpen: false,
      memoryForm: { name: "", scope: "user", title: "", body: "", why: "", how_to_apply: "" },
      memoryFormMsg: { err: "", ok: "" },
      memoryActivity: [],
      // GPU 算力（G1）
      gpuStatus: null,
      webGpu: null,
      gpuOllama: null,
      gpuEnabled: true,
      gpuLoading: false,
      webGpuLoading: false,
      webGpuBenchmarking: false,
      gpuRefreshing: false,
      gpuBusy: false,
      gpuConnections: [],
      gpuConnectionStatus: {},
      gpuConnectionModels: {},
      gpuRemoteBusy: false,
      gpuRemoteMessage: "",
      gpuConnectionForm: { connectionType: "ssh", name: "", host: "", username: "", targetPort: "8000", baseUrl: "", authMethod: "agent", password: "" },
    });
    draw = renderApp;
    gpu = createGpuDomain(makeSettingsCtx({ uiState, draw, appState, state, root }));
    memory = createMemoryDomain(makeSettingsCtx({ uiState, draw, appState, state, root, memoryEnabledKey: MEMORY_ENABLED_KEY }));
    hooks = createHooksDomain(makeSettingsCtx({ uiState, draw, appState, state, root }));
    llm = createLlmDomain(makeSettingsCtx({ uiState, draw, appState, state, root }));
    embed = createEmbedDomain(makeSettingsCtx({ uiState, draw, appState, state, root }));
    feishu = createFeishuDomain(makeSettingsCtx({ uiState, draw, appState, state, root }));
    storage = createStorageDomain(makeSettingsCtx({ uiState, draw, appState, state, root, hooks }));
    general = createGeneralDomain(makeSettingsCtx({ uiState, draw, appState, state, root, memory }));
    localServices = createLocalServicesDomain({ draw, onModelsChanged: () => { void llm.loadLlmData().then(draw).catch(() => {}); } });
    draw();
    hooks.loadHooks();
    embed.loadEmbedMode();
    embed.loadCloudConfig();
    gpu.loadGpuStatus();
    gpu.detectWebGpu();
    gpu.loadGpuConnections();
  }

  document.addEventListener("DOMContentLoaded", init);

  // ── GPU 算力（G1/G2）──────────────────────────────────────────

  // Forwarders for extracted domain functions (preserves named export surface).
  const loadGpuStatus = (...a) => gpu?.loadGpuStatus(...a);
  const refreshGpuStatus = (...a) => gpu?.refreshGpuStatus(...a);
  const setGpuEnabled = (...a) => gpu?.setGpuEnabled(...a);
  const loadHooks = (...a) => hooks?.loadHooks(...a);
  const validateHooks = (...a) => hooks?.validateHooks(...a);
  const saveHooks = (...a) => hooks?.saveHooks(...a);
  const testHooks = (...a) => hooks?.testHooks(...a);
  const loadEmbedMode = (...a) => embed?.loadEmbedMode(...a);
  const checkEmbedStatus = (...a) => embed?.checkEmbedStatus(...a);
  const setEmbedMode = (...a) => embed?.setEmbedMode(...a);
  const rebuildEmbeddings = (...a) => embed?.rebuildEmbeddings(...a);
  const loadCloudConfig = (...a) => embed?.loadCloudConfig(...a);
  const saveCloudConfig = (...a) => embed?.saveCloudConfig(...a);
  const clearCloudToken = (...a) => embed?.clearCloudToken(...a);
  const setPromptSuggestionEnabled = (...a) => general?.setPromptSuggestionEnabled(...a);
  const setTeamsEnabled = (...a) => general?.setTeamsEnabled(...a);
  const setAutoMatchSkill = (...a) => general?.setAutoMatchSkill(...a);

  export {
    init,
    setPromptSuggestionEnabled,
    setTeamsEnabled,
    setAutoMatchSkill,
    loadHooks,
    validateHooks,
    saveHooks,
    testHooks,
    loadEmbedMode,
    checkEmbedStatus,
    setEmbedMode,
    rebuildEmbeddings,
    loadCloudConfig,
    saveCloudConfig,
    clearCloudToken,
    loadGpuStatus,
    refreshGpuStatus,
    setGpuEnabled,
  };
