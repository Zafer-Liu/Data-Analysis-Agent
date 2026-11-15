// Teams panel for session-scoped analyst teams and communication history.
import { state } from "../core/runtime.js";
import { eventBus } from "../core/event-bus.js";
import { createRequestOwner } from "../core/request-state.js";
import { isSilentRequestError, requestErrorMessage, requestMessage } from "../core/api-client.js";
import { open as openJobHistory } from "../legacy/job_history.js";
import {
  isLeaderId,
  isWorkflowActive,
  teamHasRunningMembers,
} from "./teams/shared.js";
import { createTeamApi } from "./teams/api.js";
import { createWorkflowActions } from "./teams/workflow-actions.js";
import { createTeamActions } from "./teams/team-actions.js";
import { createWorkflowViews } from "./teams/workflow-views.js";
import { createTeamViews } from "./teams/team-views.js";

  const Vue = window.Vue;
  const root = document.getElementById("teams-panel-root");
  const hasVue = root && Vue && Vue.h && Vue.render;
  const local = {
    loading: false,
    detailLoading: false,
    runDetailLoading: false,
    runSelection: 0,
    teamSelection: 0,
    readsCancelled: false,
    error: "",
    teams: [],
    selected: "",
    selectedParticipant: "leader",
    team: null,
    teamPlans: [],
    teamPlanActing: "",
    isOpen: false,
    pollTimer: null,
    clearing: false,
    deleting: "",
    activeView: "teams",
    workflowsLoading: false,
    workflowsError: "",
    workflowMetrics: null,
    workflowSuggestions: [],
    workflowMetricsLoading: false,
    workflowCreatingDraft: "",
    workflows: [],
    runs: [],
    selectedRun: "",
    runDetail: null,
    runCosts: null,
    workflowInputs: {},
    workflowInputSavedAt: {},
    workflowInputAdvanced: {},
    workflowExpanded: {},
    workflowStarting: "",
    workflowDeleting: "",
    workflowRunDeleting: "",
    workflowCanceling: "",
    workflowResuming: "",
    workflowRetrying: "",
    workflowForking: "",
    workflowSavingTemplate: "",
    workflowGeneratingCandidates: "",
    workflowCandidateDeciding: "",
    workflowApproving: "",
    workflowApprovalForms: {},
    workflowArtifactLoading: "",
    workflowArtifactContents: {},
    workflowCreating: false,
    workflowCreateOpen: false,
    workflowCreate: {
      creationMode: "template",
      name: "经营分析 Workflow",
      description: "自动检查数据、分析关键指标、复核发现并生成报告。",
      mode: "full_auto",
      sourceKey: "source_snapshot",
      maxTotalTokens: "",
      template: "analysis",
      customAgents: [
        {
          name: "数据分析员",
          role: "data_analyst",
          instructions: "检查输入数据并给出可追溯的分析结论。",
          tools: "get_schema, query_data",
          dependsOn: "",
        },
        {
          name: "结论编辑",
          role: "report_editor",
          instructions: "仅依据上游产出整理最终结论，明确证据与不确定性。",
          tools: "",
          dependsOn: "数据分析员",
        },
      ],
    },
  };

  const initialLocal = structuredClone(local);
  let workspaceGeneration = 0;
  const requests = createRequestOwner({ getScope: () => JSON.stringify([state.SID || "", workspaceGeneration]) });
  const api = createTeamApi({ local, renderPanel, requests });
  const fetchJson = (...a) => api.fetchJson(...a);
  const fetchTeams = (...a) => api.fetchTeams(...a);
  const fetchTeamPlans = (...a) => api.fetchTeamPlans(...a);
  const fetchTeam = (...a) => api.fetchTeam(...a);
  const fetchWorkflows = (...a) => api.fetchWorkflows(...a);
  const fetchWorkflowRuns = (...a) => api.fetchWorkflowRuns(...a);
  const fetchWorkflowMetrics = (...a) => api.fetchWorkflowMetrics(...a);
  const fetchWorkflowRun = (...a) => api.fetchWorkflowRun(...a);
  const fetchWorkflowRunCosts = (...a) => api.fetchWorkflowRunCosts(...a);

  const wActions = createWorkflowActions({ local, state, api, renderPanel, refreshWorkflows });
  const decideWorkflowApproval = (...a) => wActions.decideWorkflowApproval(...a);

  async function openWorkflowJob(jobId) {
    if (!jobId) return;
    closePanelState();
    await openJobHistory(jobId);
  }
  const tActions = createTeamActions({ local, api, fetchJson, renderPanel, refresh });
  const clearTeamMessages = (...a) => tActions.clearTeamMessages(...a);
  const dissolveTeam = (...a) => tActions.dissolveTeam(...a);

  const wViews = createWorkflowViews({
    local,
    renderPanel,
    selectWorkflowRun,
    openWorkflowJob,
    api,
    wActions,
  });
  const renderWorkflowPanel = (...a) => wViews.renderWorkflowPanel(...a);

  const views = createTeamViews({
    local,
    renderPanel,
    refresh,
    refreshWorkflows,
    switchView,
    closePanelState,
    selectTeam,
    openWorkflowJob,
    dissolveTeam,
    clearTeamMessages,
    hasRunningMembers,
    fetchJson,
    fetchTeamPlans,
    api,
    cancelReads,
  });
  const renderHeader = (...a) => views.renderHeader(...a);
  const renderTeamList = (...a) => views.renderTeamList(...a);
  const renderDetail = (...a) => views.renderDetail(...a);

  function renderPlainFallback() {
    if (!root) return;
    root.textContent = local.error || requestMessage(local.loading ? "loading" : "teamsEmpty");
  }

  function renderPanel() {
    if (!hasVue) {
      renderPlainFallback();
      return;
    }
    const { h, render } = Vue;

    render(h("div", { class: "teams-panel" }, [
      renderHeader(),
      local.error && local.teams.length && local.activeView === "teams"
        ? h("div", { class: "teams-inline-error", role: "alert" }, local.error)
        : null,
      local.activeView === "workflow" ? renderWorkflowPanel() : h("div", { class: "teams-grid" }, [
        h("section", { class: "teams-sidebar" }, renderTeamList()),
        h("section", { class: "teams-main" }, renderDetail()),
      ]),
    ]), root);
  }

  function hasRunningMembers() {
    return teamHasRunningMembers(local.team);
  }

  function schedulePoll() {
    if (local.pollTimer) {
      clearTimeout(local.pollTimer);
      local.pollTimer = null;
    }
    const hasActiveWorkflow = local.runs.some(run => isWorkflowActive(run.status));
    if (!local.isOpen || local.readsCancelled || local.error || local.workflowsError || (!hasRunningMembers() && !hasActiveWorkflow)) return;
    local.pollTimer = setTimeout(() => {
      local.pollTimer = null;
      Promise.allSettled([
        hasRunningMembers() ? refresh({ silent: true }) : Promise.resolve(),
        hasActiveWorkflow ? refreshWorkflows({ silent: true, keepSelection: true }) : Promise.resolve(),
      ]).catch(() => {});
    }, 2500);
  }

  function switchView(view) {
    local.activeView = view === "workflow" ? "workflow" : "teams";
    renderPanel();
    if (local.activeView === "workflow" && !local.workflows.length && !local.workflowsLoading) {
      refreshWorkflows({ keepSelection: true }).catch(() => {});
    }
  }

  async function selectWorkflowRun(runId) {
    if (!runId || !state.SID) return;
    const ticket = requests.beginRead("run-detail");
    local.runSelection += 1;
    requests.cancelRead("artifact");
    local.workflowArtifactLoading = "";
    local.workflowArtifactContents = {};
    if (local.selectedRun !== runId) local.runDetail = null;
    local.selectedRun = runId;
    local.runCosts = null;
    local.runDetailLoading = true;
    local.workflowsError = "";
    local.readsCancelled = false;
    renderPanel();
    try {
      const [detail, costs] = await Promise.all([
        fetchWorkflowRun(runId, { signal: ticket.signal }), fetchWorkflowRunCosts(runId, { signal: ticket.signal }),
      ]);
      if (!ticket.isCurrent() || local.selectedRun !== runId) return;
      local.runDetail = detail;
      local.runCosts = costs;
    } catch (error) {
      if (ticket.isCurrent() && !isSilentRequestError(error)) local.workflowsError = requestErrorMessage(error);
    } finally {
      if (ticket.isCurrent()) { local.runDetailLoading = false; renderPanel(); }
      ticket.finish();
    }
  }

  async function refreshWorkflows(options = {}) {
    if (!state.SID) return;
    const ticket = requests.beginRead("workflows");
    const selected = local.selectedRun;
    local.readsCancelled = false;
    if (!options.silent) {
      local.workflowsLoading = true;
      local.workflowMetricsLoading = true;
      local.workflowsError = "";
      renderPanel();
    }
    try {
      const init = { signal: ticket.signal };
      const [workflowData, runData, metricData] = await Promise.all([
        fetchWorkflows(init), fetchWorkflowRuns(init), fetchWorkflowMetrics(init),
      ]);
      if (!ticket.isCurrent()) return;
      local.workflows = workflowData.workflows || [];
      local.runs = runData.runs || [];
      local.workflowMetrics = metricData.metrics || null;
      local.workflowSuggestions = metricData.suggestions || [];
      // A selection made while the lists loaded owns its own detail request.
      if (local.selectedRun === selected && !local.runDetailLoading) {
        const runId = options.keepSelection && local.runs.some(run => run.id === selected) ? selected : local.runs[0]?.id || "";
        if (runId) await selectWorkflowRun(runId);
        else { requests.cancelRead("run-detail"); local.selectedRun = ""; local.runDetail = null; local.runCosts = null; }
      }
    } catch (error) {
      if (ticket.isCurrent() && !isSilentRequestError(error)) local.workflowsError = requestErrorMessage(error);
    } finally {
      if (ticket.isCurrent()) {
        local.workflowsLoading = false;
        local.workflowMetricsLoading = false;
        renderPanel();
        schedulePoll();
      }
      ticket.finish();
    }
  }

  async function selectTeam(name) {
    if (!name || !state.SID) return;
    const ticket = requests.beginRead("team-detail");
    local.teamSelection += 1;
    if (local.selected !== name) { local.selectedParticipant = "leader"; local.team = null; local.teamPlans = []; }
    local.selected = name;
    local.detailLoading = true;
    local.error = "";
    local.readsCancelled = false;
    renderPanel();
    try {
      const [data, planData] = await Promise.all([
        fetchTeam(name, { signal: ticket.signal }), fetchTeamPlans(name, { signal: ticket.signal }),
      ]);
      if (!ticket.isCurrent() || local.selected !== name) return;
      local.team = data.team || null;
      local.teamPlans = planData.plans || [];
      const memberNames = new Set((local.team?.members || []).map(member => member.name));
      if (!isLeaderId(local.selectedParticipant) && !memberNames.has(local.selectedParticipant)) local.selectedParticipant = "leader";
    } catch (error) {
      if (ticket.isCurrent() && !isSilentRequestError(error)) local.error = requestErrorMessage(error);
    } finally {
      if (ticket.isCurrent()) { local.detailLoading = false; renderPanel(); schedulePoll(); }
      ticket.finish();
    }
  }

  async function refresh(options = {}) {
    if (!state.SID) return;
    const ticket = requests.beginRead("teams");
    const selected = local.selected;
    local.readsCancelled = false;
    if (!options.silent) { local.loading = true; local.error = ""; renderPanel(); }
    try {
      const data = await fetchTeams({ signal: ticket.signal });
      if (!ticket.isCurrent()) return;
      local.teams = data.teams || [];
      if (local.selected === selected && !local.detailLoading) {
        const name = local.teams.some(team => team.name === selected) ? selected : local.teams[0]?.name || "";
        if (name) await selectTeam(name);
        else {
          requests.cancelRead("team-detail");
          local.selected = ""; local.team = null; local.teamPlans = []; local.selectedParticipant = "leader";
          local.error = "";
        }
      }
    } catch (error) {
      if (ticket.isCurrent() && !isSilentRequestError(error)) local.error = requestErrorMessage(error);
    } finally {
      if (ticket.isCurrent()) { local.loading = false; renderPanel(); schedulePoll(); }
      ticket.finish();
    }
  }

  function cancelReads() {
    for (const key of ["teams", "team-detail", "workflows", "run-detail", "artifact"]) requests.cancelRead(key);
    local.loading = false;
    local.detailLoading = false;
    local.workflowsLoading = false;
    local.workflowMetricsLoading = false;
    local.runDetailLoading = false;
    local.workflowArtifactLoading = "";
    local.readsCancelled = true;
    if (local.pollTimer) { clearTimeout(local.pollTimer); local.pollTimer = null; }
    if (local.activeView === "workflow") local.workflowsError = requestMessage("cancelled");
    else local.error = requestMessage("cancelled");
    renderPanel();
  }

  function resetScope() {
    requests.reset();
    if (local.pollTimer) clearTimeout(local.pollTimer);
    const { isOpen, activeView } = local;
    Object.assign(local, structuredClone(initialLocal), { isOpen, activeView });
    renderPanel();
    if (isOpen && state.SID) Promise.allSettled([refresh(), refreshWorkflows({ keepSelection: true })]);
  }
  document.addEventListener("langchange", renderPanel);
  eventBus.on("state:SID", resetScope);
  eventBus.on("knowledge:scope-changed", () => { workspaceGeneration += 1; resetScope(); });

  async function openPanel() {
    local.isOpen = true;
    window.BAA.overlay.openOverlay("ov-teams");
    await Promise.allSettled([
      refresh(),
      refreshWorkflows({ silent: true, keepSelection: true }),
    ]);
  }

  function closePanelState() {
    requests.reset();
    for (const [key, value] of Object.entries(initialLocal)) {
      if (/Loading$|^loading$|^clearing$|^deleting$|^teamPlanActing$|^workflow(?:Starting|Deleting|RunDeleting|Canceling|Resuming|Retrying|Forking|SavingTemplate|GeneratingCandidates|CandidateDeciding|Approving|Creating|CreatingDraft)$/.test(key)) local[key] = value;
    }
    local.isOpen = false;
    if (local.pollTimer) {
      clearTimeout(local.pollTimer);
      local.pollTimer = null;
    }
  }

  function init() {
    renderPanel();
  }

export const teams = Object.freeze({
    init,
    openPanel,
    closePanelState,
    refresh,
    selectTeam,
    refreshWorkflows,
    selectWorkflowRun,
    decideWorkflowApproval,
    switchView,
    isOpen: () => local.isOpen,
    isAvailable: () => !!hasVue,
});
