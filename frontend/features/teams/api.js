// API layer for the teams panel: fetchJson + endpoint wrappers.
import { state } from "../../core/runtime.js";

export function createTeamApi(ctx) {
  const { local, renderPanel } = ctx;

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  async function fetchTeams() {
    return fetchJson(`/api/session/${state.SID}/teams`);
  }

  async function fetchTeamPlans(teamName = "") {
    const query = teamName ? `?team_name=${encodeURIComponent(teamName)}` : "";
    return fetchJson(`/api/session/${state.SID}/team-plans${query}`);
  }

  async function fetchTeam(name) {
    return fetchJson(`/api/session/${state.SID}/teams/${encodeURIComponent(name)}`);
  }

  async function fetchWorkflows() {
    return fetchJson(`/api/session/${state.SID}/workflows`);
  }

  async function fetchWorkflowRuns() {
    return fetchJson(`/api/session/${state.SID}/workflow-runs`);
  }

  async function fetchWorkflowMetrics() {
    return fetchJson(`/api/session/${state.SID}/workflow-metrics`);
  }

  async function fetchWorkflowRun(runId) {
    return fetchJson(`/api/session/${state.SID}/workflow-runs/${encodeURIComponent(runId)}`);
  }

  async function loadWorkflowArtifact(runId, artifactId) {
    if (!runId || !artifactId || local.workflowArtifactLoading) return;
    local.workflowArtifactLoading = artifactId;
    local.workflowsError = "";
    renderPanel();
    try {
      const result = await fetchJson(
        `/api/session/${state.SID}/workflow-runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}`,
      );
      local.workflowArtifactContents[artifactId] = result.content;
    } catch (error) {
      local.workflowsError = String(error.message || error);
    } finally {
      local.workflowArtifactLoading = "";
      renderPanel();
    }
  }

  async function createAgentProfile(profile) {
    return fetchJson(`/api/session/${state.SID}/agent-profiles`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile),
    });
  }

  async function createWorkflowDraft(payload) {
    return fetchJson(`/api/session/${state.SID}/workflows`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  async function validateWorkflow(workflowId) {
    return fetchJson(`/api/session/${state.SID}/workflows/${encodeURIComponent(workflowId)}/validate`, {
      method: "POST",
    });
  }

  async function publishWorkflow(workflowId) {
    return fetchJson(`/api/session/${state.SID}/workflows/${encodeURIComponent(workflowId)}/publish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ published_by: "teams_panel" }),
    });
  }

  return {
    fetchJson,
    fetchTeams,
    fetchTeamPlans,
    fetchTeam,
    fetchWorkflows,
    fetchWorkflowRuns,
    fetchWorkflowMetrics,
    fetchWorkflowRun,
    loadWorkflowArtifact,
    createAgentProfile,
    createWorkflowDraft,
    validateWorkflow,
    publishWorkflow,
  };
}
