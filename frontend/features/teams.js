// Teams panel for session-scoped analyst teams and communication history.
import { state } from "../core/runtime.js";
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

  const api = createTeamApi({ local, renderPanel });
  const fetchJson = (...a) => api.fetchJson(...a);
  const fetchTeams = (...a) => api.fetchTeams(...a);
  const fetchTeamPlans = (...a) => api.fetchTeamPlans(...a);
  const fetchTeam = (...a) => api.fetchTeam(...a);
  const fetchWorkflows = (...a) => api.fetchWorkflows(...a);
  const fetchWorkflowRuns = (...a) => api.fetchWorkflowRuns(...a);
  const fetchWorkflowMetrics = (...a) => api.fetchWorkflowMetrics(...a);
  const fetchWorkflowRun = (...a) => api.fetchWorkflowRun(...a);

  const wActions = createWorkflowActions({ local, state, api, renderPanel, refreshWorkflows });
  const decideWorkflowApproval = (...a) => wActions.decideWorkflowApproval(...a);

  async function openWorkflowJob(jobId) {
    if (!jobId) return;
    closePanelState();
    await openJobHistory(jobId);
  }
  const tActions = createTeamActions({ local, fetchJson, renderPanel, refresh });
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
  });
  const renderHeader = (...a) => views.renderHeader(...a);
  const renderTeamList = (...a) => views.renderTeamList(...a);
  const renderDetail = (...a) => views.renderDetail(...a);

  function renderPlainFallback() {
    if (!root) return;
    root.textContent = local.error || "团队面板正在加载...";
  }

  function renderPanel() {
    if (!hasVue) {
      renderPlainFallback();
      return;
    }
    const { h, render } = Vue;

    render(h("div", { class: "teams-panel" }, [
      renderHeader(),
      local.error && local.teams.length
        ? h("div", { class: "teams-inline-error" }, local.error)
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
    if (!local.isOpen || (!hasRunningMembers() && !hasActiveWorkflow)) return;
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
    if (!runId) return;
    local.selectedRun = runId;
    local.workflowsError = "";
    renderPanel();
    try {
      local.runDetail = await fetchWorkflowRun(runId);
    } catch (error) {
      local.workflowsError = String(error.message || error);
    }
    renderPanel();
  }

  async function refreshWorkflows(options = {}) {
    if (!state.SID) return;
    if (!options.silent) {
      local.workflowsLoading = true;
      local.workflowMetricsLoading = true;
      local.workflowsError = "";
      renderPanel();
    }
    try {
      const [workflowData, runData, metricData] = await Promise.all([
        fetchWorkflows(),
        fetchWorkflowRuns(),
        fetchWorkflowMetrics(),
      ]);
      local.workflows = workflowData.workflows || [];
      local.runs = runData.runs || [];
      local.workflowMetrics = metricData.metrics || null;
      local.workflowSuggestions = metricData.suggestions || [];
      if (!options.keepSelection || !local.runs.some(run => run.id === local.selectedRun)) {
        local.selectedRun = local.runs[0]?.id || "";
      }
      local.runDetail = local.selectedRun ? await fetchWorkflowRun(local.selectedRun) : null;
      local.workflowsError = "";
    } catch (error) {
      local.workflowsError = String(error.message || error);
    } finally {
      local.workflowsLoading = false;
      local.workflowMetricsLoading = false;
      renderPanel();
      schedulePoll();
    }
  }

  async function selectTeam(name) {
    if (!name) return;
    if (local.selected !== name) local.selectedParticipant = "leader";
    local.selected = name;
    local.error = "";
    renderPanel();
    try {
      const [data, planData] = await Promise.all([fetchTeam(name), fetchTeamPlans(name)]);
      local.team = data.team || null;
      local.teamPlans = planData.plans || [];
      const memberNames = new Set((local.team?.members || []).map(member => member.name));
      if (!isLeaderId(local.selectedParticipant) && !memberNames.has(local.selectedParticipant)) {
        local.selectedParticipant = "leader";
      }
    } catch (error) {
      local.error = String(error.message || error);
    }
    renderPanel();
  }

  async function refresh(options = {}) {
    if (!state.SID) return;
    if (!options.silent) {
      local.loading = true;
      local.error = "";
      renderPanel();
    }
    try {
      const data = await fetchTeams();
      local.teams = data.teams || [];
      if (!local.teams.some(team => team.name === local.selected)) {
        local.selected = local.teams[0]?.name || "";
        local.selectedParticipant = "leader";
      }
      if (local.selected) {
        const [status, planData] = await Promise.all([
          fetchTeam(local.selected),
          fetchTeamPlans(local.selected),
        ]);
        local.team = status.team || null;
        local.teamPlans = planData.plans || [];
        const memberNames = new Set((local.team?.members || []).map(member => member.name));
        if (!isLeaderId(local.selectedParticipant) && !memberNames.has(local.selectedParticipant)) {
          local.selectedParticipant = "leader";
        }
      } else {
        local.team = null;
        local.teamPlans = [];
        local.selectedParticipant = "leader";
      }
      local.error = "";
    } catch (error) {
      local.error = String(error.message || error);
      if (!options.silent) {
        local.teams = [];
        local.team = null;
        local.selected = "";
      }
    } finally {
      local.loading = false;
      renderPanel();
      schedulePoll();
    }
  }

  async function openPanel() {
    local.isOpen = true;
    window.BAA.overlay.openOverlay("ov-teams");
    await Promise.allSettled([
      refresh(),
      refreshWorkflows({ silent: true, keepSelection: true }),
    ]);
  }

  function closePanelState() {
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
