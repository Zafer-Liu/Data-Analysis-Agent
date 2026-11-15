// Shared request/error contract; endpoint facade names stay compatible.
import { state } from "../../core/runtime.js";
import { ApiError, apiClient, isSilentRequestError, requestErrorMessage, requestMessage } from "../../core/api-client.js";
import { createRequestOwner } from "../../core/request-state.js";

export function createTeamApi(ctx) {
  const { local, renderPanel } = ctx;
  const requests = ctx.requests || createRequestOwner({ getScope: () => state.SID || "" });
  const client = ctx.client || apiClient;
  const sessionPath = (suffix) => `/api/session/${encodeURIComponent(state.SID || "")}/${suffix}`;
  const stale = () => new ApiError(requestMessage("cancelled"), { code: "STALE_REQUEST", kind: "abort" });

  async function fetchJson(url, options = {}) {
    const scope = requests.capture();
    const method = String(options.method || "GET").toUpperCase();
    const write = !["GET", "HEAD", "OPTIONS"].includes(method);
    const ticket = write ? requests.beginWrite(`http:${method}:${url}`) : null;
    if (write && !ticket) throw new ApiError(requestMessage("busy"), { code: "REQUEST_BUSY", kind: "request", method, url });
    try {
      const data = await client.request(url, { ...options, responseType: "json", requireOk: true });
      if (!scope.isCurrent()) throw stale();
      return data;
    } finally { ticket?.finish(); }
  }

  const fetchTeams = (options) => fetchJson(sessionPath("teams"), options);
  const fetchTeamPlans = (teamName = "", options) => fetchJson(sessionPath(`team-plans${teamName ? `?team_name=${encodeURIComponent(teamName)}` : ""}`), options);
  const fetchTeam = (name, options) => fetchJson(sessionPath(`teams/${encodeURIComponent(name)}`), options);
  const fetchWorkflows = (options) => fetchJson(sessionPath("workflows"), options);
  const fetchWorkflowRuns = (options) => fetchJson(sessionPath("workflow-runs"), options);
  const fetchWorkflowMetrics = (options) => fetchJson(sessionPath("workflow-metrics"), options);
  const fetchWorkflowRun = (runId, options) => fetchJson(sessionPath(`workflow-runs/${encodeURIComponent(runId)}`), options);
  const fetchWorkflowRunCosts = (runId, options) => fetchJson(sessionPath(`workflow-runs/${encodeURIComponent(runId)}/costs`), options);

  async function loadWorkflowArtifact(runId, artifactId) {
    if (!runId || !artifactId || local.workflowArtifactLoading) return;
    const ticket = requests.beginRead("artifact");
    const selection = local.runSelection;
    const currentRun = () => local.selectedRun === runId && local.runSelection === selection;
    local.workflowArtifactLoading = artifactId;
    local.workflowsError = "";
    renderPanel();
    try {
      const result = await fetchJson(sessionPath(`workflow-runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}`), { signal: ticket.signal });
      if (ticket.isCurrent() && currentRun()) local.workflowArtifactContents[artifactId] = result.content;
    } catch (error) {
      if (ticket.isCurrent() && currentRun() && !isSilentRequestError(error)) local.workflowsError = requestErrorMessage(error);
    } finally {
      if (ticket.isCurrent()) { local.workflowArtifactLoading = ""; renderPanel(); }
      ticket.finish();
    }
  }

  const createAgentProfile = (profile) => fetchJson(sessionPath("agent-profiles"), { method: "POST", body: profile });
  const createWorkflowDraft = (payload) => fetchJson(sessionPath("workflows"), { method: "POST", body: payload });
  const validateWorkflow = (workflowId) => fetchJson(sessionPath(`workflows/${encodeURIComponent(workflowId)}/validate`), { method: "POST" });
  const publishWorkflow = (workflowId) => fetchJson(sessionPath(`workflows/${encodeURIComponent(workflowId)}/publish`), { method: "POST", body: { published_by: "teams_panel" } });

  function beginAction(key) {
    const ticket = requests.beginWrite(`action:${key}`);
    if (!ticket) window.BAA?.ui?.toast?.(requestMessage("busy"));
    return ticket;
  }

  return { requests, beginAction, fetchJson, fetchTeams, fetchTeamPlans, fetchTeam,
    fetchWorkflows, fetchWorkflowRuns, fetchWorkflowMetrics, fetchWorkflowRun, fetchWorkflowRunCosts,
    loadWorkflowArtifact, createAgentProfile, createWorkflowDraft, validateWorkflow, publishWorkflow };
}
