// Teams panel for session-scoped analyst teams and communication history.
import { state } from "../core/runtime.js";
import { renderMd } from "../legacy/markdown.js";
import { open as openJobHistory } from "../legacy/job_history.js";

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

  const WORKFLOW_TEMPLATES = Object.freeze({
    analysis: {
      label: "经营分析",
      name: "经营分析 Workflow",
      description: "检查数据、分析指标与异常、复核发现并生成经营报告。",
      hint: "5 个节点；适合需要交叉验证与正式经营报告的任务。",
    },
    insight: {
      label: "数据查询与洞察",
      name: "数据查询与洞察 Workflow",
      description: "检查数据后分析关键指标，输出可追溯的数据洞察。",
      hint: "2 个节点；适合探索性查询和指标洞察。",
    },
    report: {
      label: "数据报告",
      name: "数据报告 Workflow",
      description: "检查输入数据并直接生成基于证据的数据报告。",
      hint: "2 个节点；适合已有清晰数据源的快速报告。",
    },
    cleaning_approval: {
      label: "受控数据清洗",
      name: "受控数据清洗 Workflow",
      description: "先提出可审计的清洗方案；审批通过后真实执行，并生成基于实际结果的报告。原始数据不会被覆盖。",
      hint: "4 个节点；批准后写入 cleaned_data 派生表；补充要求会重新生成方案并再次审批。",
    },
  });

  function formatTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString();
  }

  function statusLabel(status) {
    const map = {
      idle: "空闲",
      queued: "待处理",
      running: "运行中",
      completed: "已完成",
      failed: "失败",
    };
    return map[status] || status || "未知";
  }

  function memberStatusClass(status) {
    return `team-status team-status-${status || "unknown"}`;
  }

  function participantLabel(id) {
    if (id === "leader" || id === "lead") return "Leader";
    return id || "成员";
  }

  function isLeaderId(id) {
    return id === "leader" || id === "lead";
  }

  function renderMarkdown(text) {
    return renderMd(text || "");
  }

  function workflowStatusLabel(status) {
    const map = {
      created: "已创建",
      running: "运行中",
      waiting_approval: "待审批",
      paused: "已暂停",
      canceling: "取消中",
      canceled: "已取消",
      succeeded: "已成功",
      failed: "失败",
      pending: "待处理",
      ready: "就绪",
      queued: "排队中",
      output_ready: "产出就绪",
      skipped: "已跳过",
    };
    return map[status] || status || "未知";
  }

  function workflowStatusClass(status) {
    return `workflow-status workflow-status-${status || "unknown"}`;
  }

  function workflowEdgeLabel(type) {
    const map = {
      auto: "自动",
      approval: "审批",
      retry_loop: "返工",
    };
    return map[type] || type || "边";
  }

  function workflowEdgeClass(type) {
    return `workflow-dag-edge-chip workflow-dag-edge-${type || "auto"}`;
  }

  function latestNodeRunsById(nodes) {
    const result = new Map();
    for (const node of nodes || []) {
      const id = node?.node_id || "";
      if (!id) continue;
      const current = result.get(id);
      const score = (Number(node.iteration) || 1) * 1000 + (Number(node.attempt) || 1);
      const currentScore = current
        ? (Number(current.iteration) || 1) * 1000 + (Number(current.attempt) || 1)
        : -1;
      if (!current || score >= currentScore) result.set(id, node);
    }
    return result;
  }

  function workflowDagLevels(graph) {
    const nodes = graph?.nodes || [];
    const levels = new Map(nodes.map(node => [String(node.node_id || ""), 0]));
    const entryIds = new Set((graph?.entry_node_ids || []).map(String));
    entryIds.forEach(id => levels.set(id, 0));
    const forwardEdges = (graph?.edges || []).filter(edge => edge.type !== "retry_loop");
    for (let pass = 0; pass < nodes.length + 1; pass += 1) {
      let changed = false;
      for (const edge of forwardEdges) {
        const from = String(edge.from_node || "");
        const to = String(edge.to_node || "");
        if (!from || !to || !levels.has(to)) continue;
        const nextLevel = (levels.get(from) || 0) + 1;
        if (nextLevel > (levels.get(to) || 0)) {
          levels.set(to, nextLevel);
          changed = true;
        }
      }
      if (!changed) break;
    }
    return levels;
  }

  function isPendingApprovalNode(detail, nodeId) {
    return (detail?.approvals || []).some(
      approval => approval.status === "pending" && approval.node_id === nodeId,
    );
  }

  function isRetryTarget(graph, nodeId) {
    return (graph?.edges || []).some(
      edge => edge.type === "retry_loop" && edge.to_node === nodeId,
    );
  }

  function isWorkflowActive(status) {
    return ["created", "running", "waiting_approval", "paused", "canceling"].includes(status);
  }

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

  function workflowModeLabel(mode) {
    const map = {
      full_auto: "全自动",
      key_approval: "关键审批",
      exception_review: "异常复核",
    };
    return map[mode] || mode || "全自动";
  }

  function normalizedWorkflowCreate() {
    const form = local.workflowCreate || {};
    const sourceKey = String(form.sourceKey || "source_snapshot").trim() || "source_snapshot";
    return {
      name: String(form.name || "经营分析 Workflow").trim() || "经营分析 Workflow",
      description: String(form.description || "").trim(),
      mode: ["full_auto", "key_approval", "exception_review"].includes(form.mode)
        ? form.mode
        : "full_auto",
      sourceKey,
      template: Object.prototype.hasOwnProperty.call(WORKFLOW_TEMPLATES, form.template)
        ? form.template
        : "analysis",
      creationMode: form.creationMode === "custom" ? "custom" : "template",
      customAgents: Array.isArray(form.customAgents) ? form.customAgents : [],
    };
  }

  function updateWorkflowCreate(key, value) {
    local.workflowCreate = {
      ...local.workflowCreate,
      [key]: value,
    };
  }

  function getApprovalForm(approval) {
    const id = approval?.id || "";
    if (!id) return { comment: "", revisedSummary: "", revisedOutputs: "{}" };
    if (!local.workflowApprovalForms[id]) {
      local.workflowApprovalForms[id] = {
        comment: "",
        revisedSummary: "",
        revisedOutputs: "{}",
        revisionFields: [],
        seededManifestId: "",
      };
    }
    return local.workflowApprovalForms[id];
  }

  function updateApprovalForm(approval, key, value) {
    const id = approval?.id || "";
    if (!id) return;
    local.workflowApprovalForms[id] = {
      ...getApprovalForm(approval),
      [key]: value,
    };
  }

  function manifestItemName(item) {
    return String(item?.logical_name || item?.name || item?.artifact_id || "");
  }

  function manifestItemEditableValue(item) {
    if (!item) return "";
    const value = Object.prototype.hasOwnProperty.call(item, "data")
      ? item.data
      : item.data_preview || item.uri || "";
    if (typeof value === "string") return decodeWorkflowEscapes(value);
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value ?? "");
    }
  }

  function parseEditableValue(value) {
    const raw = String(value ?? "").trim();
    if (!raw) return "";
    try {
      return JSON.parse(raw);
    } catch {
      return String(value ?? "");
    }
  }

  function revisionFieldsToOutputs(fields) {
    const outputs = {};
    for (const field of fields || []) {
      const key = String(field.key || "").trim();
      if (!key) continue;
      outputs[key] = parseEditableValue(field.value);
    }
    return outputs;
  }

  function approvalManifest(detail, approval) {
    const manifestId = approval?.artifact_manifest_id || "";
    if (!manifestId) return null;
    return (detail?.manifests || []).find(manifest => manifest.id === manifestId) || null;
  }

  function seedApprovalRevisionFields(approval, manifest, force = false) {
    const form = getApprovalForm(approval);
    if (!manifest || (!force && (form.seededManifestId === manifest.id || form.revisionFields.length))) return;
    const revisionFields = (manifest.items || []).map(item => ({
      key: manifestItemName(item),
      value: manifestItemEditableValue(item),
      source: item.artifact_id || item.uri || "",
    })).filter(field => field.key);
    local.workflowApprovalForms[approval.id] = {
      ...form,
      revisionFields,
      seededManifestId: manifest.id,
      revisedOutputs: JSON.stringify(revisionFieldsToOutputs(revisionFields), null, 2),
    };
  }

  function updateApprovalRevisionField(approval, index, key, value) {
    const form = getApprovalForm(approval);
    const revisionFields = [...(form.revisionFields || [])];
    if (!revisionFields[index]) return;
    revisionFields[index] = {
      ...revisionFields[index],
      [key]: value,
    };
    local.workflowApprovalForms[approval.id] = {
      ...form,
      revisionFields,
      revisedOutputs: JSON.stringify(revisionFieldsToOutputs(revisionFields), null, 2),
    };
  }

  function addApprovalRevisionField(approval) {
    const form = getApprovalForm(approval);
    const revisionFields = [...(form.revisionFields || []), { key: "", value: "", source: "manual" }];
    local.workflowApprovalForms[approval.id] = {
      ...form,
      revisionFields,
      revisedOutputs: JSON.stringify(revisionFieldsToOutputs(revisionFields), null, 2),
    };
  }

  function removeApprovalRevisionField(approval, index) {
    const form = getApprovalForm(approval);
    const revisionFields = (form.revisionFields || []).filter((_, itemIndex) => itemIndex !== index);
    local.workflowApprovalForms[approval.id] = {
      ...form,
      revisionFields,
      revisedOutputs: JSON.stringify(revisionFieldsToOutputs(revisionFields), null, 2),
    };
  }

  function buildApprovalDecisionPayload(approval, decision) {
    const form = getApprovalForm(approval);
    const comment = String(form.comment || "").trim();
    const revisedSummary = String(form.revisedSummary || "").trim();
    if (decision === "reject_and_retry" && !comment) {
      throw new Error("请填写需要补充或修改的要求；系统会据此重新生成方案并再次发起审批。");
    }
    const payload = {
      decision,
      decided_by: "teams_panel",
    };
    const comments = {};
    if (comment) {
      payload.comment = comment;
      comments.review_note = comment;
    }
    if (revisedSummary) comments.revised_summary = revisedSummary;
    if (decision === "approve_with_changes") {
      const raw = String(form.revisedOutputs || "").trim();
      let revisedOutputs = {};
      try {
        revisedOutputs = raw ? JSON.parse(raw) : {};
      } catch (error) {
        throw new Error(`修订输出 JSON 无效：${error.message || error}`);
      }
      if (!revisedOutputs || typeof revisedOutputs !== "object" || Array.isArray(revisedOutputs)) {
        throw new Error("修订输出必须是 JSON object");
      }
      payload.revised_outputs = revisedOutputs;
      payload.revised_summary = revisedSummary || "团队面板人工修订";
      comments.revised_summary = payload.revised_summary;
    }
    if (Object.keys(comments).length) payload.comments = comments;
    return payload;
  }

  function buildCustomWorkflow(profileIds, form, agents) {
    const outputKey = "workflow_result";
    const agentIndexByName = new Map(agents.map((agent, index) => [agent.name, index]));
    const nodes = agents.map((agent, index) => {
      const isLast = index === agents.length - 1;
      const dependencies = agent.dependsOn.map(name => agentIndexByName.get(name));
      return {
        node_id: `custom_agent_${index + 1}`,
        type: "agent",
        agent_profile_id: profileIds[`custom_${index}`],
        input_contract: dependencies.length === 0
          ? [form.sourceKey, "business_context"]
          : dependencies.map(dependency => `custom_agent_${dependency + 1}_output`),
        output_contract: [isLast ? outputKey : `custom_agent_${index + 1}_output`],
        output_artifacts: {
          [isLast ? outputKey : `custom_agent_${index + 1}_output`]: isLast ? "report" : "insight",
        },
        side_effects: agent.allowedTools.length ? ["read_data"] : [],
        limits: { max_tokens: 384000, max_run_seconds: 900, max_tool_calls: agent.allowedTools.length ? 200 : 0 },
      };
    });
    const edges = agents.flatMap((agent, index) => agent.dependsOn.map(name => {
      const dependency = agentIndexByName.get(name);
      return {
        edge_id: `custom-${dependency + 1}-to-${index + 1}`,
        from_node: `custom_agent_${dependency + 1}`,
        to_node: `custom_agent_${index + 1}`,
        type: "auto",
      };
    }));
    return {
      outputKey,
      graph: {
        run_policy: { mode: form.mode },
        entry_node_ids: nodes.filter((_, index) => agents[index].dependsOn.length === 0).map(node => node.node_id),
        nodes,
        edges,
        limits: { max_run_minutes: 120, max_total_node_runs: Math.max(8, agents.length * 3) },
      },
    };
  }

  function buildWorkflowTemplate(profileIds, form) {
    const approvalEdgeType = form.mode === "key_approval" ? "approval" : "auto";
    const inspectNode = {
      node_id: "inspect_data",
      type: "agent",
      agent_profile_id: profileIds.inspect,
      input_contract: [form.sourceKey, "business_context"],
      output_contract: ["data_quality_report"],
      output_artifacts: { data_quality_report: "validation" },
      side_effects: ["read_data"],
      limits: { max_tokens: 384000, max_run_seconds: 900, max_tool_calls: 200 },
    };
    if (form.template === "insight") {
      return {
        outputKey: "metric_analysis",
        graph: {
          run_policy: { mode: form.mode }, entry_node_ids: ["inspect_data"],
          nodes: [inspectNode, {
            node_id: "analyze_metrics", type: "agent", agent_profile_id: profileIds.metrics,
            input_contract: ["data_quality_report"], output_contract: ["metric_analysis"],
            output_artifacts: { metric_analysis: "insight" }, side_effects: ["read_data"],
            limits: { max_tokens: 384000, max_run_seconds: 900, max_tool_calls: 200 },
          }],
          edges: [{ edge_id: "inspect-to-metrics", from_node: "inspect_data", to_node: "analyze_metrics", type: "auto" }],
          limits: { max_run_minutes: 30, max_total_node_runs: 8 },
        },
      };
    }
    if (form.template === "report") {
      return {
        outputKey: "operating_report",
        graph: {
          run_policy: { mode: form.mode }, entry_node_ids: ["inspect_data"],
          nodes: [inspectNode, {
            node_id: "generate_report", type: "agent", agent_profile_id: profileIds.cleaning_reporter,
            input_contract: ["data_quality_report"], output_contract: ["operating_report"],
            output_artifacts: { operating_report: "report" },
            output_validation: { forbidden_substrings: ["empty / not provided", "no upstream findings"] },
            limits: { max_tokens: 384000, max_run_seconds: 900, max_tool_calls: 0 },
          }],
          edges: [{ edge_id: "inspect-to-report", from_node: "inspect_data", to_node: "generate_report", type: approvalEdgeType }],
          limits: { max_run_minutes: 45, max_total_node_runs: 8 },
        },
      };
    }
    if (form.template === "cleaning_approval") {
      return {
        outputKey: "operating_report",
        graph: {
          run_policy: { mode: "key_approval" }, entry_node_ids: ["validate_input"],
          nodes: [{
            node_id: "validate_input", type: "validation", input_contract: [form.sourceKey],
            output_contract: ["validated_input"], output_artifacts: { validated_input: "validation" },
            validation: { required: [form.sourceKey], non_empty: [form.sourceKey] },
          }, {
            node_id: "propose_cleaning", type: "agent", agent_profile_id: profileIds.cleaning,
            input_contract: ["validated_input", "business_context", "revision_request"], output_contract: ["cleaning_plan"],
            output_artifacts: { cleaning_plan: "insight" }, side_effects: ["read_data"],
            limits: { max_tokens: 384000, max_run_seconds: 900, max_tool_calls: 200, max_iterations: 3 },
          }, {
            node_id: "verify_cleaning", type: "verifier", agent_profile_id: profileIds.cleaning_verifier,
            input_contract: ["cleaning_plan"], output_contract: ["decision", "issues", "evidence"],
            output_artifacts: { decision: "validation", issues: "validation", evidence: "validation" },
            verifier: { standards: [
              "The plan names exactly one existing source table and never overwrites it",
              "Every proposed operation is fill_na, winsorize, or trimming with explicit fields and parameters",
              "The plan includes expected impact and a rollback path",
            ] },
            limits: { max_tokens: 12000, max_run_seconds: 300, max_tool_calls: 0 },
          }, {
            node_id: "apply_cleaning", type: "agent", agent_profile_id: profileIds.cleaning_executor,
            input_contract: ["cleaning_plan"], output_contract: ["cleaning_execution"],
            output_artifacts: { cleaning_execution: "dataset" }, side_effects: ["write_data"],
            limits: { max_tokens: 12000, max_run_seconds: 900, max_tool_calls: 3 },
          }, {
            node_id: "generate_report", type: "agent", agent_profile_id: profileIds.cleaning_reporter,
            input_contract: ["cleaning_plan", "cleaning_execution"], output_contract: ["operating_report"],
            output_artifacts: { operating_report: "report" },
            limits: { max_tokens: 384000, max_run_seconds: 900, max_tool_calls: 0 },
          }],
          edges: [
            { edge_id: "validate-to-plan", from_node: "validate_input", to_node: "propose_cleaning", type: "auto" },
            { edge_id: "plan-to-verify", from_node: "propose_cleaning", to_node: "verify_cleaning", type: "auto" },
            { edge_id: "plan-to-clean", from_node: "propose_cleaning", to_node: "apply_cleaning", type: "auto" },
            { edge_id: "verify-to-clean-approval", from_node: "verify_cleaning", to_node: "apply_cleaning", type: "approval" },
            { edge_id: "clean-to-report", from_node: "apply_cleaning", to_node: "generate_report", type: "auto" },
            { edge_id: "plan-to-report", from_node: "propose_cleaning", to_node: "generate_report", type: "auto" },
            { edge_id: "verify-rework", from_node: "verify_cleaning", to_node: "propose_cleaning", type: "retry_loop", max_iterations: 3 },
          ],
          limits: { max_run_minutes: 60, max_total_node_runs: 14 },
        },
      };
    }
    return { outputKey: "operating_report", graph: {
      run_policy: { mode: form.mode },
      entry_node_ids: ["inspect_data"],
      nodes: [
        inspectNode,
        {
          node_id: "analyze_metrics",
          type: "agent",
          agent_profile_id: profileIds.metrics,
          input_contract: ["data_quality_report"],
          output_contract: ["metric_analysis"],
          limits: { max_tokens: 384000, max_run_seconds: 900, max_tool_calls: 200 },
        },
        {
          node_id: "analyze_anomalies",
          type: "agent",
          agent_profile_id: profileIds.anomalies,
          input_contract: ["data_quality_report"],
          output_contract: ["anomaly_analysis"],
          limits: { max_tokens: 384000, max_run_seconds: 900, max_tool_calls: 200 },
        },
        {
          node_id: "verify_findings",
          type: "agent",
          agent_profile_id: profileIds.reviewer,
          join_policy: "all_success",
          input_contract: ["metric_analysis", "anomaly_analysis"],
          output_contract: ["verification_report"],
          output_artifacts: { verification_report: "validation" },
          limits: { max_tokens: 384000, max_run_seconds: 900, max_tool_calls: 0 },
        },
        {
          node_id: "generate_report",
          type: "agent",
          agent_profile_id: profileIds.reporter,
          input_contract: ["metric_analysis", "anomaly_analysis", "verification_report"],
          output_contract: ["operating_report"],
          output_validation: {
            forbidden_substrings: ["empty / not provided", "no verification report content", "no upstream findings"],
          },
          limits: { max_tokens: 384000, max_run_seconds: 900, max_tool_calls: 0 },
        },
      ],
      edges: [
        { edge_id: "inspect-to-metrics", from_node: "inspect_data", to_node: "analyze_metrics", type: "auto" },
        { edge_id: "inspect-to-anomalies", from_node: "inspect_data", to_node: "analyze_anomalies", type: "auto" },
        { edge_id: "metrics-to-verify", from_node: "analyze_metrics", to_node: "verify_findings", type: "auto" },
        { edge_id: "anomalies-to-verify", from_node: "analyze_anomalies", to_node: "verify_findings", type: "auto" },
        { edge_id: "verify-to-report", from_node: "verify_findings", to_node: "generate_report", type: approvalEdgeType },
        { edge_id: "metrics-to-report", from_node: "analyze_metrics", to_node: "generate_report", type: "auto" },
        { edge_id: "anomalies-to-report", from_node: "analyze_anomalies", to_node: "generate_report", type: "auto" },
        { edge_id: "verify-retry", from_node: "verify_findings", to_node: "analyze_metrics", type: "retry_loop", max_iterations: 2 },
      ],
      limits: {
        max_run_minutes: 120,
        max_total_node_runs: 30,
      },
    }};
  }

  async function createWorkflowFromTemplate() {
    if (local.workflowCreating) return;
    const form = normalizedWorkflowCreate();
    local.workflowCreating = true;
    local.workflowsError = "";
    renderPanel();
    try {
      const customAgents = form.creationMode === "custom" ? normalizedCustomAgents(form) : [];
      const suffix = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
      // Workflow materials are injected through declared contracts.  Do not
      // advertise read_tool_result here: delegated Workflow agents cannot read
      // arbitrary session artifacts, and that stale permission caused empty
      // downstream reviews in older templates.
      const baseTools = ["get_schema", "query_data"];
      const templateSpecs = [
        ["inspect", "数据检查员", "data_inspector", "识别数据表、字段质量和可用指标范围，直接输出一份非空的 data_quality_report。报告必须包含“数据质量”和“可用指标范围”两节；可使用 Markdown。", baseTools],
        ["metrics", "指标分析师", "metric_analyst", "围绕业务目标执行 SQL/指标分析，输出 metric_analysis。", baseTools],
        ["anomalies", "异常分析师", "anomaly_analyst", "发现波动、异常与可解释原因，输出 anomaly_analysis。", baseTools],
        ["reviewer", "结论复核员", "finding_reviewer", "只使用上游 metric_analysis 与 anomaly_analysis 交叉复核。必须直接输出非空的 verification_report：先写复核结论，再列出已证实证据、待验证项和风险。材料不足时也必须明确写出材料不足、不能确认的非空结论；不得只输出思考过程、工具调用或空白。", []],
        ["reporter", "报告编辑", "report_editor", "输出完整、可供管理层使用的经营分析报告：管理摘要、经营表现与关键指标、结构表现与业务驱动、风险与数据可信度、管理建议与行动计划。把技术复核压缩为一小节，直接解释业务影响；不得输出 artifact ID、预览截断说明、表清单、原始计算、SQL 或字段级审计细节。", []],
        ["cleaning", "数据清洗方案员", "data_cleaning_planner", "只提出待审批的可审计数据清洗方案，不得写入数据。方案必须列出源表、仅可执行的操作（fill_na、winsorize 或 trimming）、精确字段与参数、预期影响和回滚方式。若提供 revision_request，必须针对该要求重新制定方案。", baseTools],
        ["cleaning_verifier", "清洗方案复核员", "data_cleaning_verifier", "独立复核清洗方案，不得修改方案或写入数据。只返回 JSON：decision（pass、rework 或 escalate）、issues（字符串数组）和 evidence（字符串数组）。只有方案满足全部安全标准时才返回 pass。", []],
        ["cleaning_executor", "数据清洗执行员", "data_cleaning_executor", "仅在上游 cleaning_plan 已获审批后执行。必须调用 clean_data 恰好一次，并且只执行获批方案中一个支持的操作：fill_na、winsorize 或 trimming。不得覆盖源表，output_table 固定为 cleaned_data；工具返回后如实输出 cleaning_execution，包含源表、操作、参数、结果表、实际影响与失败信息。", ["clean_data"]],
        ["cleaning_reporter", "清洗执行报告员", "data_cleaning_reporter", "仅依据获批方案和 cleaning_execution 写执行报告。必须清楚区分审批的计划与实际执行结果；只可陈述工具返回证实的写入。说明源表未覆盖、清洗结果保存在 cleaned_data，以及任何未执行或失败项。", []],
      ];
      const specs = form.creationMode === "custom"
        ? customAgents.map((agent, index) => [
          `custom_${index}`, agent.name, agent.role, agent.instructions, agent.allowedTools,
        ])
        : templateSpecs;
      const profileIds = {};
      for (const [id, name, role, instructions, allowedTools] of specs) {
        const result = await createAgentProfile({
          key: `workflow_${id}_${suffix}`,
          name,
          role,
          instructions,
          allowed_tools: allowedTools,
          model_policy: "inherit",
          created_by: "teams_panel",
        });
        profileIds[id] = result.profile?.id;
      }
      const template = form.creationMode === "custom"
        ? buildCustomWorkflow(profileIds, form, customAgents)
        : buildWorkflowTemplate(profileIds, form);
      const workflow = await createWorkflowDraft({
        name: form.name,
        description: form.description || (form.creationMode === "custom"
          ? `${workflowModeLabel(form.mode)}模式的自定义 Agent 工作流`
          : `${workflowModeLabel(form.mode)}模式的${WORKFLOW_TEMPLATES[form.template].label}模板`),
        graph: template.graph,
        input_schema: {
          type: "object",
          properties: {
            [form.sourceKey]: { type: "string", title: "数据来源或范围", description: "例如：当前工作区数据、2026 年 7 月销售表" },
            business_context: { type: "string", title: "业务补充说明", description: "可填写字段单位、口径、业务目标或需要重点核查的问题" },
            revision_request: { type: "string", title: "方案重做要求", description: "仅在审批时要求重做后由系统自动写入，无需首次启动时填写" },
          },
          required: [form.sourceKey],
        },
        output_schema: {
          type: "object",
          properties: {
            [template.outputKey]: { type: "string" },
          },
          required: [template.outputKey],
        },
        created_by: "teams_panel",
      });
      const workflowId = workflow.workflow?.id;
      await validateWorkflow(workflowId);
      const published = await publishWorkflow(workflowId);
      saveWorkflowInputRaw(workflowId, JSON.stringify({ [form.sourceKey]: "当前工作区数据" }, null, 2));
      local.selectedRun = "";
      local.runDetail = null;
      await refreshWorkflows({ silent: true, keepSelection: true });
      local.workflowCreateOpen = false;
      window.BAA.ui?.toast?.(`Workflow 已创建并发布 v${String(published.version?.id || "").slice(-6)}`, "ok");
    } catch (error) {
      local.workflowsError = String(error.message || error);
      window.BAA.ui?.toast?.(local.workflowsError, "err");
    } finally {
      local.workflowCreating = false;
      renderPanel();
    }
  }

  async function cancelWorkflowRun(runId) {
    if (!runId || local.workflowCanceling) return;
    local.workflowCanceling = runId;
    local.workflowsError = "";
    renderPanel();
    try {
      const detail = await fetchJson(
        `/api/session/${state.SID}/workflow-runs/${encodeURIComponent(runId)}/cancel`,
        { method: "POST" },
      );
      local.runDetail = detail;
      await refreshWorkflows({ silent: true, keepSelection: true });
      window.BAA.ui?.toast?.("Workflow Run 已取消", "ok");
    } catch (error) {
      local.workflowsError = String(error.message || error);
      window.BAA.ui?.toast?.(local.workflowsError, "err");
    } finally {
      local.workflowCanceling = "";
      renderPanel();
    }
  }

  async function resumeWorkflowRun(runId) {
    if (!runId || local.workflowResuming) return;
    local.workflowResuming = runId;
    local.workflowsError = "";
    renderPanel();
    try {
      const detail = await fetchJson(
        `/api/session/${state.SID}/workflow-runs/${encodeURIComponent(runId)}/resume`,
        { method: "POST" },
      );
      local.runDetail = detail;
      await refreshWorkflows({ silent: true, keepSelection: true });
      window.BAA.ui?.toast?.("Workflow Run 已恢复", "ok");
    } catch (error) {
      local.workflowsError = String(error.message || error);
      window.BAA.ui?.toast?.(local.workflowsError, "err");
    } finally {
      local.workflowResuming = "";
      renderPanel();
    }
  }

  function decodeWorkflowEscapes(value) {
    let text = String(value ?? "");
    for (let pass = 0; pass < 2; pass += 1) {
      const trimmed = text.trim();
      if (trimmed.startsWith('"') && trimmed.endsWith('"')) {
        try {
          const parsed = JSON.parse(trimmed);
          if (typeof parsed === "string") {
            text = parsed;
            continue;
          }
        } catch {
          // Legacy model output can contain invalid Markdown escapes such as
          // "\\#". Repair only display-safe formatting escapes below.
        }
      }
      const decoded = text
        .replace(/\\\\r\\\\n/g, "\n")
        .replace(/\\\\n/g, "\n")
        .replace(/\\\\r/g, "\n")
        .replace(/\\\\t/g, "\t")
        .replace(/\\r\\n/g, "\n")
        .replace(/\\n/g, "\n")
        .replace(/\\r/g, "\n")
        .replace(/\\t/g, "\t")
        .replace(/\\\\([`#])/g, "$1")
        .replace(/\\([`#])/g, "$1");
      if (decoded === text) break;
      text = decoded;
    }
    return text;
  }

  function selectWorkflowTemplate(template) {
    const selected = WORKFLOW_TEMPLATES[template] || WORKFLOW_TEMPLATES.analysis;
    local.workflowCreate = {
      ...local.workflowCreate,
      template,
      name: selected.name,
      description: selected.description,
      mode: template === "cleaning_approval" ? "key_approval" : local.workflowCreate.mode,
    };
  }

  function normalizedCustomAgents(form) {
    const allowedTools = new Set(["get_schema", "query_data"]);
    const agents = (form.customAgents || []).map((agent, index) => {
      const name = String(agent?.name || "").trim() || `Agent ${index + 1}`;
      const role = String(agent?.role || "").trim() || "workflow_specialist";
      const instructions = String(agent?.instructions || "").trim();
      const requestedTools = String(agent?.tools || "").split(",")
        .map(item => item.trim()).filter(Boolean);
      const invalidTools = requestedTools.filter(tool => !allowedTools.has(tool));
      if (!instructions) throw new Error(`${name} 缺少 Agent 指令`);
      if (invalidTools.length) throw new Error(`${name} 包含不支持的工具：${invalidTools.join(", ")}`);
      const dependsOn = String(agent?.dependsOn || "").split(",")
        .map(item => item.trim()).filter(Boolean);
      return { name, role, instructions, allowedTools: [...new Set(requestedTools)], dependsOn: [...new Set(dependsOn)] };
    });
    if (agents.length < 1) throw new Error("至少需要定义一个 Agent");
    if (agents.length > 8) throw new Error("自定义工作流最多支持 8 个 Agent");
    const names = new Set();
    agents.forEach((agent, index) => {
      if (names.has(agent.name)) throw new Error(`Agent 名称重复：${agent.name}`);
      names.add(agent.name);
      const priorNames = new Set(agents.slice(0, index).map(item => item.name));
      const invalidDependencies = agent.dependsOn.filter(name => !priorNames.has(name));
      if (invalidDependencies.length) {
        throw new Error(`${agent.name} 的依赖必须是前面已定义的 Agent：${invalidDependencies.join("、")}`);
      }
    });
    return agents;
  }

  function updateCustomAgent(index, key, value) {
    const customAgents = [...(local.workflowCreate.customAgents || [])];
    if (!customAgents[index]) return;
    customAgents[index] = { ...customAgents[index], [key]: value };
    updateWorkflowCreate("customAgents", customAgents);
  }

  function addCustomAgent() {
    const customAgents = [...(local.workflowCreate.customAgents || [])];
    if (customAgents.length >= 8) return;
    customAgents.push({ name: `Agent ${customAgents.length + 1}`, role: "workflow_specialist", instructions: "", tools: "", dependsOn: "" });
    updateWorkflowCreate("customAgents", customAgents);
  }

  function removeCustomAgent(index) {
    const customAgents = (local.workflowCreate.customAgents || []).filter((_, itemIndex) => itemIndex !== index);
    updateWorkflowCreate("customAgents", customAgents);
  }

  async function retryWorkflowNode(node) {
    const runId = local.runDetail?.run?.id || "";
    if (!runId || !node?.id || local.workflowRetrying) return;
    local.workflowRetrying = node.id;
    local.workflowsError = "";
    renderPanel();
    try {
      const detail = await fetchJson(
        `/api/session/${state.SID}/workflow-runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(node.id)}/retry`,
        { method: "POST" },
      );
      local.runDetail = detail;
      await refreshWorkflows({ silent: true, keepSelection: true });
      window.BAA.ui?.toast?.(`${node.node_id || "节点"}已重新派发`, "ok");
    } catch (error) {
      local.workflowsError = String(error.message || error);
      window.BAA.ui?.toast?.(local.workflowsError, "err");
    } finally {
      local.workflowRetrying = "";
      renderPanel();
    }
  }

  async function forkWorkflowRunFromCheckpoint(node) {
    const runId = local.runDetail?.run?.id || "";
    if (!runId || !node?.id || local.workflowForking) return;
    local.workflowForking = node.id;
    local.workflowsError = "";
    renderPanel();
    try {
      const detail = await fetchJson(
        `/api/session/${state.SID}/workflow-runs/${encodeURIComponent(runId)}/fork`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            checkpoint_node_run_id: node.id,
            started_by: "teams_panel",
          }),
        },
      );
      local.selectedRun = detail.run?.id || "";
      local.runDetail = detail;
      await refreshWorkflows({ silent: true, keepSelection: true });
      window.BAA.ui?.toast?.(`已从 ${node.node_id || "检查点"} 创建分叉 Run`, "ok");
    } catch (error) {
      local.workflowsError = String(error.message || error);
      window.BAA.ui?.toast?.(local.workflowsError, "err");
    } finally {
      local.workflowForking = "";
      renderPanel();
    }
  }

  async function openWorkflowJob(jobId) {
    if (!jobId) return;
    closePanelState();
    await openJobHistory(jobId);
  }

  async function saveWorkflowTemplate(run) {
    if (!run?.id || local.workflowSavingTemplate) return;
    local.workflowSavingTemplate = run.id;
    local.workflowsError = "";
    renderPanel();
    try {
      await fetchJson(
        `/api/session/${state.SID}/workflow-runs/${encodeURIComponent(run.id)}/template`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: `${workflowForRun(run)?.name || "Workflow"} 成功模板`,
            created_by: "teams_panel",
          }),
        },
      );
      local.runDetail = await fetchWorkflowRun(run.id);
      window.BAA.ui?.toast?.("成功 Run 已保存为模板", "ok");
    } catch (error) {
      local.workflowsError = String(error.message || error);
      window.BAA.ui?.toast?.(local.workflowsError, "err");
    } finally {
      local.workflowSavingTemplate = "";
      renderPanel();
    }
  }

  function focusWorkflowKnowledgeCandidates() {
    requestAnimationFrame(() => {
      document.querySelector(".workflow-knowledge-candidates")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    });
  }

  async function generateWorkflowKnowledgeCandidates(run) {
    if (!run?.id || local.workflowGeneratingCandidates) return;
    local.workflowGeneratingCandidates = run.id;
    local.workflowsError = "";
    renderPanel();
    try {
      await fetchJson(
        `/api/session/${state.SID}/workflow-runs/${encodeURIComponent(run.id)}/knowledge-candidates`,
        { method: "POST" },
      );
      local.runDetail = await fetchWorkflowRun(run.id);
      focusWorkflowKnowledgeCandidates();
      window.BAA.ui?.toast?.("入库候选已生成，请在详情顶部接受或拒绝", "ok");
    } catch (error) {
      local.workflowsError = String(error.message || error);
      window.BAA.ui?.toast?.(local.workflowsError, "err");
    } finally {
      local.workflowGeneratingCandidates = "";
      renderPanel();
    }
  }

  async function decideWorkflowKnowledgeCandidate(candidate, decision) {
    if (!candidate?.id || local.workflowCandidateDeciding) return;
    local.workflowCandidateDeciding = candidate.id;
    local.workflowsError = "";
    renderPanel();
    try {
      await fetchJson(
        `/api/session/${state.SID}/workflow-knowledge-candidates/${encodeURIComponent(candidate.id)}/decide`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision, decided_by: "teams_panel" }),
        },
      );
      local.runDetail = await fetchWorkflowRun(candidate.run_id);
      window.BAA.ui?.toast?.(
        decision === "accept" ? "候选已写入业务知识库" : "候选已拒绝",
        "ok",
      );
    } catch (error) {
      local.workflowsError = String(error.message || error);
      window.BAA.ui?.toast?.(local.workflowsError, "err");
    } finally {
      local.workflowCandidateDeciding = "";
      renderPanel();
    }
  }

  async function publishSavedWorkflowDraft(workflow) {
    if (!workflow?.id || local.workflowCreating) return;
    local.workflowCreating = true;
    renderPanel();
    try {
      await validateWorkflow(workflow.id);
      await publishWorkflow(workflow.id);
      await refreshWorkflows({ silent: true, keepSelection: true });
      await window.BAA.skills?.loadSkills?.();
      window.BAA.ui?.toast?.("Workflow 草稿已发布", "ok");
    } catch (error) {
      local.workflowsError = String(error.message || error);
      window.BAA.ui?.toast?.(local.workflowsError, "err");
    } finally {
      local.workflowCreating = false;
      renderPanel();
    }
  }

  async function createWorkflowOptimizationDraft(suggestion) {
    if (!suggestion?.id || local.workflowCreatingDraft) return;
    local.workflowCreatingDraft = suggestion.id;
    local.workflowsError = "";
    renderPanel();
    try {
      const result = await fetchJson(
        `/api/session/${state.SID}/workflow-optimization-suggestions/${encodeURIComponent(suggestion.id)}/draft`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ created_by: "teams_panel" }),
        },
      );
      await refreshWorkflows({ silent: true, keepSelection: true });
      window.BAA.ui?.toast?.(
        `已创建「${result.workflow?.name || "优化草稿"}」，发布前请人工检查`,
        "ok",
      );
    } catch (error) {
      local.workflowsError = String(error.message || error);
      window.BAA.ui?.toast?.(local.workflowsError, "err");
    } finally {
      local.workflowCreatingDraft = "";
      renderPanel();
    }
  }

  function defaultWorkflowInputs(schema) {
    const required = Array.isArray(schema?.required) ? schema.required : [];
    return Object.fromEntries(required.map(key => [
      key,
      schema?.properties?.[key]?.type === "string" ? "当前工作区数据" : {},
    ]));
  }

  function workflowInputStorageKey(workflowId) {
    return `baa_workflow_inputs:${state.SID || "default"}:${workflowId}`;
  }

  function readWorkflowInputRaw(workflowId) {
    if (Object.prototype.hasOwnProperty.call(local.workflowInputs, workflowId)) {
      return String(local.workflowInputs[workflowId] || "");
    }
    try {
      const saved = localStorage.getItem(workflowInputStorageKey(workflowId)) || "";
      if (saved) local.workflowInputs[workflowId] = saved;
      return saved;
    } catch {
      return "";
    }
  }

  function saveWorkflowInputRaw(workflowId, raw) {
    const value = String(raw || "");
    local.workflowInputs[workflowId] = value;
    local.workflowInputSavedAt[workflowId] = Date.now();
    try {
      localStorage.setItem(workflowInputStorageKey(workflowId), value);
    } catch {
      // Input remains usable for this page even if browser storage is disabled.
    }
  }

  function workflowInputValues(workflowId, schema) {
    const fallback = defaultWorkflowInputs(schema);
    const raw = readWorkflowInputRaw(workflowId).trim();
    if (!raw) return fallback;
    try {
      const value = JSON.parse(raw);
      return value && typeof value === "object" && !Array.isArray(value) ? { ...fallback, ...value } : fallback;
    } catch {
      return fallback;
    }
  }

  function updateWorkflowInputValue(workflowId, schema, key, value) {
    saveWorkflowInputRaw(workflowId, JSON.stringify({
      ...workflowInputValues(workflowId, schema),
      [key]: value,
    }, null, 2));
  }

  function workflowInputLabel(key, property = {}) {
    if (property.title) return property.title;
    const labels = { source_snapshot: "数据来源或范围", business_context: "业务补充说明" };
    return labels[key] || key;
  }

  async function startWorkflow(workflow) {
    if (!workflow?.current_version_id || local.workflowStarting) return;
    let inputs = {};
    const schema = workflow.current_version?.input_schema || workflow.draft_input_schema || {};
    const raw = JSON.stringify(workflowInputValues(workflow.id, schema));
    try {
      inputs = raw ? JSON.parse(raw) : {};
      if (!inputs || typeof inputs !== "object" || Array.isArray(inputs)) {
        throw new Error("输入必须是 JSON object");
      }
    } catch (error) {
      local.workflowsError = `运行输入无效：${error.message || error}`;
      renderPanel();
      return;
    }

    local.workflowStarting = workflow.id;
    local.workflowsError = "";
    renderPanel();
    try {
      const detail = await fetchJson(`/api/session/${state.SID}/workflow-runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workflow_version_id: workflow.current_version_id,
          inputs,
          started_by: "teams_panel",
        }),
      });
      local.selectedRun = detail.run?.id || "";
      local.runDetail = detail;
      await refreshWorkflows({ silent: true, keepSelection: true });
      window.BAA.ui?.toast?.("Workflow 已启动", "ok");
    } catch (error) {
      local.workflowsError = String(error.message || error);
    } finally {
      local.workflowStarting = "";
      renderPanel();
    }
  }

  async function deleteWorkflow(workflow) {
    if (!workflow?.id || local.workflowDeleting) return;
    const accepted = await window.BAA.ui?.confirm?.({
      danger: true,
      title: "永久删除 Workflow？",
      message: `将彻底删除「${workflow.name || workflow.id}」的全部版本、运行记录、节点、审批、事件、材料、关联 Job，以及未被其他流程复用的专属角色。原始数据源不会删除。此操作不可恢复。`,
      confirmText: "永久删除",
      cancelText: "取消",
    });
    if (!accepted) return;
    local.workflowDeleting = workflow.id;
    local.workflowsError = "";
    renderPanel();
    try {
      const result = await fetchJson(
        `/api/session/${state.SID}/workflows/${encodeURIComponent(workflow.id)}`,
        { method: "DELETE" },
      );
      delete local.workflowInputs[workflow.id];
      delete local.workflowInputSavedAt[workflow.id];
      try { localStorage.removeItem(workflowInputStorageKey(workflow.id)); } catch { /* browser storage unavailable */ }
      delete local.workflowExpanded[workflow.id];
      local.selectedRun = "";
      local.runDetail = null;
      await refreshWorkflows({ silent: true, keepSelection: false });
      const deletedRuns = result.deleted?.runs || 0;
      const deletedJobs = result.deleted?.jobs || 0;
      window.BAA.ui?.toast?.(
        `Workflow 已永久删除，同时清理 ${deletedRuns} 次运行和 ${deletedJobs} 个 Job`,
        "ok",
      );
    } catch (error) {
      local.workflowsError = String(error.message || error);
      window.BAA.ui?.toast?.(local.workflowsError, "err");
    } finally {
      local.workflowDeleting = "";
      renderPanel();
    }
  }

  function workflowForRun(run) {
    return local.workflows.find(
      workflow => workflow.current_version_id === run?.workflow_version_id
    ) || null;
  }

  async function deleteWorkflowRun(run) {
    if (!run?.id || local.workflowRunDeleting) return;
    const workflow = workflowForRun(run);
    const accepted = await window.BAA.ui?.confirm?.({
      danger: true,
      title: "永久删除运行记录？",
      message: `将彻底删除「${workflow?.name || run.id}」本次运行的节点输出、审批、事件、材料、Manifest 和关联 Job。Workflow 定义与原始数据源会保留。此操作不可恢复。`,
      confirmText: "永久删除",
      cancelText: "取消",
    });
    if (!accepted) return;
    local.workflowRunDeleting = run.id;
    local.workflowsError = "";
    renderPanel();
    try {
      const result = await fetchJson(
        `/api/session/${state.SID}/workflow-runs/${encodeURIComponent(run.id)}`,
        { method: "DELETE" },
      );
      if (local.selectedRun === run.id) {
        local.selectedRun = "";
        local.runDetail = null;
      }
      await refreshWorkflows({ silent: true, keepSelection: false });
      window.BAA.ui?.toast?.(
        `运行记录已永久删除，同时清理 ${result.deleted?.jobs || 0} 个 Job`,
        "ok",
      );
    } catch (error) {
      local.workflowsError = String(error.message || error);
      window.BAA.ui?.toast?.(local.workflowsError, "err");
    } finally {
      local.workflowRunDeleting = "";
      renderPanel();
    }
  }

  async function decideWorkflowApproval(approval, decision) {
    if (!approval?.id || !approval?.run_id || local.workflowApproving) return;
    let payload = {};
    try {
      payload = buildApprovalDecisionPayload(approval, decision);
    } catch (error) {
      local.workflowsError = String(error.message || error);
      renderPanel();
      return;
    }
    local.workflowApproving = approval.id;
    local.workflowsError = "";
    renderPanel();
    try {
      const detail = await fetchJson(
        `/api/session/${state.SID}/workflow-runs/${encodeURIComponent(approval.run_id)}/approvals/${encodeURIComponent(approval.id)}/decide`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      local.selectedRun = detail.run?.id || approval.run_id;
      local.runDetail = detail;
      delete local.workflowApprovalForms[approval.id];
      await refreshWorkflows({ silent: true, keepSelection: true });
      const label = {
        approve: "已批准",
        approve_with_changes: "已带修改批准",
        reject_and_retry: "已要求重做",
        reject_and_stop: "已驳回终止",
      }[decision] || "已处理";
      window.BAA.ui?.toast?.(`审批${label}`, "ok");
    } catch (error) {
      local.workflowsError = String(error.message || error);
      window.BAA.ui?.toast?.(local.workflowsError, "err");
    } finally {
      local.workflowApproving = "";
      renderPanel();
    }
  }

  async function clearTeamMessages(name) {
    if (!name || local.clearing) return;
    const accepted = await window.BAA.ui?.confirm?.({
      danger: true,
      title: "清空团队沟通记录？",
      message: `将永久清空团队「${name}」的全部沟通记录，但保留团队和成员。`,
      confirmText: "确认清空",
      cancelText: "取消",
    });
    if (!accepted) return;
    local.clearing = true;
    local.error = "";
    renderPanel();
    try {
      const result = await fetchJson(
        `/api/session/${state.SID}/teams/${encodeURIComponent(name)}/messages`,
        {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: true }),
        },
      );
      window.BAA.ui?.toast?.(`已清空 ${result.cleared_messages || 0} 条团队沟通记录`, "ok");
      await refresh({ silent: true });
    } catch (error) {
      local.error = String(error.message || error);
    } finally {
      local.clearing = false;
      renderPanel();
    }
  }

  function teamHasRunningMembers(team) {
    return (team?.members || []).some(
      member => member.status === "running" || member.status === "queued"
    );
  }

  function isEvidenceRetentionError(error) {
    return String(error?.message || error || "").includes("默认保留");
  }

  async function requestTeamDelete(name, force = false) {
    return fetchJson(
      `/api/session/${state.SID}/teams/${encodeURIComponent(name)}`,
      {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, force }),
      },
    );
  }

  async function dissolveTeam(name) {
    if (!name || local.deleting) return;
    const team = local.teams.find(item => item.name === name);
    if (teamHasRunningMembers(team)) {
      window.BAA.ui?.toast?.("团队成员仍在执行或排队，暂不能解散", "err");
      return;
    }
    const accepted = await window.BAA.ui?.confirm?.({
      danger: true,
      title: "解散团队？",
      message: `将先检查团队「${name}」是否含有委派结果、错误或质量复核证据；如有证据会要求二次确认后才强制删除。`,
      confirmText: "继续检查",
      cancelText: "取消",
    });
    if (!accepted) return;
    local.deleting = name;
    local.error = "";
    renderPanel();
    try {
      try {
        await requestTeamDelete(name, false);
      } catch (error) {
        if (!isEvidenceRetentionError(error)) throw error;
        local.deleting = "";
        renderPanel();
        const forced = await window.BAA.ui?.confirm?.({
          danger: true,
          title: "团队含复盘证据，确认强制解散？",
          message: `团队「${name}」已有委派结果、错误或质量复核记录。建议先查看 team_status 复盘；若确认不再需要这些证据，可强制删除。`,
          confirmText: "强制解散",
          cancelText: "保留团队",
        });
        if (!forced) return;
        local.deleting = name;
        local.error = "";
        renderPanel();
        await requestTeamDelete(name, true);
      }
      if (local.selected === name) {
        local.selected = "";
        local.selectedParticipant = "leader";
        local.team = null;
      }
      window.BAA.ui?.toast?.(`团队「${name}」已解散`, "ok");
      await refresh({ silent: true });
    } catch (error) {
      local.error = String(error.message || error);
      window.BAA.ui?.toast?.(local.error, "err");
    } finally {
      local.deleting = "";
      renderPanel();
    }
  }

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

    function renderHeader() {
      return h("div", { class: "teams-head" }, [
        h("div", { class: "teams-title-block" }, [
          h("div", { class: "modal-title" }, "团队"),
          h("div", { class: "teams-sub" }, "查看成员协作、Workflow 运行状态和材料交接。"),
          h("div", { class: "team-tabs", role: "tablist", "aria-label": "团队协作视图" }, [
            h("button", {
              class: local.activeView === "teams" ? "team-tab active" : "team-tab",
              type: "button",
              role: "tab",
              "aria-selected": local.activeView === "teams" ? "true" : "false",
              onClick: () => switchView("teams"),
            }, "团队成员"),
            h("button", {
              class: local.activeView === "workflow" ? "team-tab active" : "team-tab",
              type: "button",
              role: "tab",
              "aria-selected": local.activeView === "workflow" ? "true" : "false",
              onClick: () => switchView("workflow"),
            }, "Workflow"),
          ]),
        ]),
        h("div", { class: "teams-actions" }, [
          h("button", {
            class: "btn-sm btn-sm-ghost",
            type: "button",
            disabled: local.loading || local.workflowsLoading,
            onClick: () => local.activeView === "workflow"
              ? refreshWorkflows({ keepSelection: true })
              : refresh(),
          }, "刷新"),
          h("button", {
            class: "teams-close",
            type: "button",
            title: "关闭",
            onClick: () => {
              closePanelState();
              window.BAA.overlay.closeOverlay("ov-teams");
            },
          }, "×"),
        ]),
      ]);
    }

    function renderTeamList() {
      if (!local.teams.length) {
        return h("div", { class: "teams-empty" }, local.error || "还没有团队。可以让 Agent 创建一个 team 来拆分分析任务。");
      }
      return h("div", { class: "teams-list" }, local.teams.map(team => h("div", {
        key: team.name,
        class: local.selected === team.name ? "team-card active" : "team-card",
      }, [
        h("button", {
          class: "team-card-select",
          type: "button",
          onClick: () => selectTeam(team.name),
        }, [
          h("div", { class: "team-card-main" }, [
            h("strong", null, team.name),
            h("span", null, team.description || "无描述"),
          ]),
          h("div", { class: "team-card-meta" }, [
            h("span", null, `${team.member_count || 0} 成员`),
            h("span", null, `${team.message_count || 0} 消息`),
          ]),
        ]),
        h("button", {
          class: "team-card-dissolve",
          type: "button",
          disabled: local.deleting === team.name || teamHasRunningMembers(team),
          title: teamHasRunningMembers(team)
            ? "团队成员仍在执行或排队，暂不能解散"
            : `解散团队「${team.name}」`,
          onClick: () => dissolveTeam(team.name),
        }, local.deleting === team.name ? "解散中…" : "解散团队"),
      ])));
    }

    function setParticipant(id) {
      local.selectedParticipant = id || "leader";
      renderPanel();
    }

    function renderToolEvents(message) {
      const events = Array.isArray(message.tool_events) ? message.tool_events : [];
      if (!events.length) return null;
      return h("details", { class: "team-tool-flow" }, [
        h("summary", null, `工具调用流程 (${events.length})`),
        h("div", { class: "team-tool-list" }, events.map((event, index) => h("div", {
          key: `${event.tool || "tool"}-${index}`,
          class: event.status === "error" ? "team-tool-item error" : "team-tool-item",
        }, [
          h("div", { class: "team-tool-head" }, [
            h("span", null, event.status === "error" ? "✕" : "✓"),
            h("strong", null, event.tool || "tool"),
            event.elapsed_seconds != null ? h("small", null, `${event.elapsed_seconds}s`) : null,
          ]),
          Object.keys(event.args || {}).length
            ? h("pre", { class: "team-tool-args" }, JSON.stringify(event.args, null, 2))
            : null,
          event.result
            ? h("div", {
                class: "team-tool-result team-markdown",
                innerHTML: renderMarkdown(String(event.result)),
              })
            : null,
        ]))),
      ]);
    }

    function renderLeaderCard() {
      const unread = local.team?.lead_unread_messages || 0;
      return h("button", {
        key: "leader",
        class: isLeaderId(local.selectedParticipant) ? "team-member team-member-select active" : "team-member team-member-select",
        type: "button",
        onClick: () => setParticipant("leader"),
      }, [
        h("div", { class: "team-member-top" }, [
          h("strong", null, "Leader"),
          h("span", { class: "team-status team-status-lead" }, "负责人"),
        ]),
        h("div", { class: "team-member-role" }, "Team Leader"),
        h("div", { class: "team-member-intro" }, "团队负责人，接收成员交付结果、错误和关键进展。"),
        unread
          ? h("div", { class: "team-member-unread" }, `未读 ${unread}`)
          : null,
      ]);
    }

    function renderMembers() {
      const members = local.team?.members || [];
      if (!members.length) {
        return h("div", { class: "team-members" }, [renderLeaderCard()]);
      }
      return h("div", { class: "team-members" }, [
        renderLeaderCard(),
        ...members.map(member => h("button", {
        key: member.name,
        class: local.selectedParticipant === member.name ? "team-member team-member-select active" : "team-member team-member-select",
        type: "button",
        onClick: () => setParticipant(member.name),
      }, [
        h("div", { class: "team-member-top" }, [
          h("strong", null, member.name),
          h("span", { class: memberStatusClass(member.status) }, statusLabel(member.status)),
        ]),
        h("div", { class: "team-member-role" }, member.role || member.agent_id || "analyst"),
        member.instructions
          ? h("div", {
              class: "team-member-intro team-markdown",
              innerHTML: renderMarkdown(member.instructions),
            })
          : null,
        member.unread_messages
          ? h("div", { class: "team-member-unread" }, `未读 ${member.unread_messages}`)
          : null,
        member.last_active_at
          ? h("div", { class: "team-member-time" }, formatTime(member.last_active_at))
          : null,
        ])),
      ]);
    }

    function renderMessages() {
      const selected = local.selectedParticipant || "leader";
      const messages = (local.team?.recent_messages || []).filter(message => {
        if (isLeaderId(selected)) return isLeaderId(message.recipient) || isLeaderId(message.sender);
        return message.sender === selected || message.recipient === selected;
      });
      if (!messages.length) {
        return h("div", { class: "teams-empty compact" }, `${participantLabel(selected)} 暂无响应`);
      }
      return h("div", { class: "team-messages" }, messages.slice().reverse().map(message => h("div", {
        key: message.id || `${message.sender}-${message.created_at}`,
        class: [
          "team-message",
          message.read ? "read" : "",
          message.message_type === "assignment" ? "team-message-assignment" : "",
          message.message_type === "error" ? "team-message-error" : "",
        ].filter(Boolean).join(" "),
      }, [
        h("div", { class: "team-message-head" }, [
          h("span", null, `${participantLabel(message.sender)} → ${participantLabel(message.recipient)}`),
          h("small", null, formatTime(message.created_at)),
        ]),
        renderToolEvents(message),
        h("div", {
          class: "team-message-body team-markdown",
          innerHTML: renderMarkdown(message.message || ""),
        }),
      ])));
    }

    async function controlTeamPlan(plan, action) {
      if (!plan?.id || local.teamPlanActing) return;
      local.teamPlanActing = `${plan.id}:${action}`;
      renderPanel();
      try {
        const suffix = action === "workflow-draft" ? "workflow-draft" : `actions/${action}`;
        const data = await fetchJson(
          `/api/session/${state.SID}/team-plans/${encodeURIComponent(plan.id)}/${suffix}`,
          { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ created_by: "teams_panel" }) },
        );
        const plans = await fetchTeamPlans(local.selected);
        local.teamPlans = plans.plans || [];
        if (action === "workflow-draft") {
          await refreshWorkflows({ silent: true, keepSelection: true });
          window.BAA.ui?.toast?.(`已创建 Workflow 草稿：${data.workflow?.name || plan.id}`, "ok");
        }
      } catch (error) {
        local.error = String(error.message || error);
        window.BAA.ui?.toast?.(local.error, "err");
      } finally {
        local.teamPlanActing = "";
        renderPanel();
      }
    }

    function requestTeamPlanExecution(plan) {
      const input = document.getElementById("msg-input");
      if (!input || !plan?.id) return;
      input.value = `执行动态计划 ${plan.id}。仅执行该已创建计划，不要新建或重复任务。`;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
      window.BAA.ui?.toast?.("已填入执行请求，发送后由 Lead 启动已确认计划", "ok");
    }

    function requestTeamPlanRevision(plan) {
      const input = document.getElementById("msg-input");
      if (!input || !plan?.id) return;
      input.value = `根据动态计划 ${plan.id} 的质量复核意见，选择受影响任务并调用 team_delegate 的 review_plan_id 与 review_task_ids 定向重跑；保留已通过且不受依赖影响的任务，不要保存为 Workflow 草稿。`;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
      window.BAA.ui?.toast?.("已填入补全请求，发送后由 Lead 依据复核意见重新派发", "ok");
    }

    function requestTeamTaskRetry(plan, task) {
      const input = document.getElementById("msg-input");
      if (!input || !plan?.id || !task?.id) return;
      input.value = `重试动态计划 ${plan.id} 中失败的任务 ${task.id}。仅重试该任务，不要重复已成功任务。`;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
      window.BAA.ui?.toast?.("已填入重试请求，发送后由 Lead 重新派发该任务", "ok");
    }

    function dynamicPlanBudgetText(plan) {
      const budget = plan?.budget || {};
      const inputTokens = Number(budget.input_tokens || 0);
      const outputTokens = Number(budget.output_tokens || 0);
      const toolCalls = Number(budget.tool_calls || 0);
      const jobs = Number(budget.child_job_count || 0);
      if (!inputTokens && !outputTokens && !toolCalls && !jobs) return "等待成员执行用量";
      return `输入 ${inputTokens.toLocaleString()} · 输出 ${outputTokens.toLocaleString()} · ${toolCalls} 次工具 · ${jobs} 个子 Job · 成本未配置`;
    }

    function renderDynamicPlans() {
      const plans = local.teamPlans || [];
      if (!plans.length) return h("div", { class: "teams-empty compact" }, "对话中触发团队并行委派后，动态任务计划会显示在这里。");
      return h("div", { class: "team-plan-list" }, plans.slice(0, 10).map(plan => h("article", { class: "team-plan", key: plan.id }, [
        h("div", { class: "team-plan-head" }, [
          h("div", null, [h("strong", null, plan.goal || plan.id), h("span", null, `${plan.tasks?.length || 0} 个任务 · ${formatTime(plan.created_at)}`)]),
          h("span", { class: `team-plan-status ${plan.status}` }, plan.status),
        ]),
        h("div", { class: "team-plan-budget" }, dynamicPlanBudgetText(plan)),
        plan.review_status === "blocked" ? h("div", { class: "team-plan-review-blocked" }, `质量复核待修正${plan.review_summary ? `：${plan.review_summary}` : ""}`) : null,
        h("div", { class: "team-plan-tasks" }, (plan.tasks || []).map(task => h("div", { class: "team-plan-task", key: task.id }, [
          h("span", { class: `team-plan-task-dot ${task.status}` }),
          h("div", null, [
            h("strong", null, task.title || task.id),
            h("small", null, `${task.member_name}${task.depends_on?.length ? ` · 依赖 ${task.depends_on.join(", ")}` : ""}`),
            task.error ? h("small", { class: "team-plan-task-error" }, task.error) : null,
          ]),
          task.job_id ? h("div", { class: "team-plan-task-links" }, [
            h("button", {
              class: "workflow-job-link",
              type: "button",
              title: "在 Job 历史中查看此动态任务与交付物",
              onClick: () => openWorkflowJob(task.job_id),
            }, `Job ${String(task.job_id).slice(-6)}`),
            task.artifacts?.length ? h("span", null, `${task.artifacts.length} 个交付物`) : null,
          ]) : h("span", null, task.status),
          task.status === "failed" ? h("button", {
            class: "btn-sm btn-sm-ghost",
            type: "button",
            onClick: () => requestTeamTaskRetry(plan, task),
          }, "在对话中重试") : null,
        ]))),
        h("div", { class: "team-plan-actions" }, [
          plan.status === "planned" ? h("button", { class: "btn-sm btn-sm-primary", type: "button", onClick: () => requestTeamPlanExecution(plan) }, "在对话中执行") : null,
          plan.status === "needs_review" ? h("button", { class: "btn-sm btn-sm-primary", type: "button", onClick: () => requestTeamPlanRevision(plan) }, "按复核意见补全") : null,
          plan.status === "running" ? h("button", { class: "btn-sm btn-sm-danger", type: "button", onClick: () => controlTeamPlan(plan, "cancel") }, "请求终止") : null,
          plan.status === "completed" && !plan.workflow_draft_id ? h("button", { class: "btn-sm btn-sm-primary", type: "button", onClick: () => controlTeamPlan(plan, "workflow-draft") }, "保存为 Workflow 草稿") : null,
          plan.workflow_draft_id ? h("span", { class: "team-plan-saved" }, "已保存为草稿") : null,
        ]),
      ])));
    }

    function renderDetail() {
      if (local.loading && !local.team) return h("div", { class: "teams-empty" }, "正在读取团队状态...");
      if (local.error && !local.teams.length) return h("div", { class: "teams-error" }, local.error);
      if (!local.team) return h("div", { class: "teams-empty" }, "选择一个团队查看状态。");
      return h("div", { class: "team-detail" }, [
        h("div", { class: "team-detail-head" }, [
          h("div", null, [
            h("h3", null, local.team.name),
            h("p", null, local.team.description || "无描述"),
          ]),
          h("div", { class: "team-detail-actions" }, [
            h("div", { class: "team-lead-unread" }, `Leader 未读 ${local.team.lead_unread_messages || 0}`),
            h("button", {
              class: "btn-sm btn-sm-danger",
              type: "button",
              disabled: local.clearing || hasRunningMembers(),
              title: hasRunningMembers() ? "团队成员仍在执行或排队，暂不能清空" : "清空当前团队全部沟通记录",
              onClick: () => clearTeamMessages(local.team.name),
            }, local.clearing ? "清空中..." : "清空沟通记录"),
          ]),
        ]),
        h("div", { class: "team-section-title" }, "动态任务计划"),
        renderDynamicPlans(),
        h("div", { class: "team-section-title" }, "成员"),
        renderMembers(),
        h("div", { class: "team-section-title" }, `${participantLabel(local.selectedParticipant)} 响应`),
        renderMessages(),
      ]);
    }

    function renderWorkflowCreate() {
      const form = normalizedWorkflowCreate();
      if (!local.workflowCreateOpen) {
        return h("button", {
          class: "workflow-create-toggle",
          type: "button",
          onClick: () => {
            local.workflowCreateOpen = true;
            renderPanel();
          },
        }, "新建 Workflow");
      }
      return h("div", { class: "workflow-create" }, [
        h("div", { class: "workflow-create-head" }, [
          h("strong", null, "从模板创建"),
          h("button", {
            class: "btn-sm btn-sm-ghost",
            type: "button",
            disabled: local.workflowCreating,
            onClick: () => {
              local.workflowCreateOpen = false;
              renderPanel();
            },
          }, "收起"),
        ]),
        h("div", { class: "workflow-create-mode", role: "group", "aria-label": "创建方式" }, [
          h("button", {
            type: "button", class: form.creationMode === "template" ? "is-active" : "",
            disabled: local.workflowCreating,
            onClick: () => { updateWorkflowCreate("creationMode", "template"); renderPanel(); },
          }, "使用标准模板"),
          h("button", {
            type: "button", class: form.creationMode === "custom" ? "is-active" : "",
            disabled: local.workflowCreating,
            onClick: () => { updateWorkflowCreate("creationMode", "custom"); renderPanel(); },
          }, "自定义 Agent"),
        ]),
        form.creationMode === "template"
          ? h("div", { class: "workflow-create-template" }, [
            h("label", null, [
              h("span", null, "标准模板"),
              h("select", {
                value: form.template,
                disabled: local.workflowCreating,
                onChange: event => { selectWorkflowTemplate(event.target.value); renderPanel(); },
              }, Object.entries(WORKFLOW_TEMPLATES).map(([key, template]) =>
                h("option", { value: key }, template.label),
              )),
            ]),
            h("p", { class: "workflow-create-hint" }, WORKFLOW_TEMPLATES[form.template].hint),
          ])
          : h("div", { class: "workflow-custom-agents" }, [
            h("div", { class: "workflow-custom-agents-head" }, [
              h("strong", null, "自定义 Agent 链路"),
              h("button", {
                type: "button", class: "btn-sm btn-sm-ghost", disabled: local.workflowCreating || form.customAgents.length >= 8,
                onClick: () => { addCustomAgent(); renderPanel(); },
              }, "添加 Agent"),
            ]),
            h("p", { class: "workflow-create-hint" }, "未填写依赖的 Agent 会并行执行；复核/汇总 Agent 可依赖多个前序 Agent。依赖名称必须来自前面已定义的 Agent。工具仅支持 get_schema、query_data。"),
            ...form.customAgents.map((agent, index) => h("fieldset", { class: "workflow-custom-agent", key: `custom-agent-${index}` }, [
              h("legend", null, `Agent ${index + 1}`),
              h("label", null, [h("span", null, "名称"), h("input", { value: agent.name, disabled: local.workflowCreating, onInput: event => updateCustomAgent(index, "name", event.target.value) })]),
              h("label", null, [h("span", null, "角色"), h("input", { value: agent.role, disabled: local.workflowCreating, onInput: event => updateCustomAgent(index, "role", event.target.value) })]),
              h("label", null, [h("span", null, "Agent 指令"), h("textarea", { rows: 3, value: agent.instructions, disabled: local.workflowCreating, onInput: event => updateCustomAgent(index, "instructions", event.target.value) })]),
              h("label", null, [h("span", null, "允许工具（可留空）"), h("input", { value: agent.tools, placeholder: "get_schema, query_data", disabled: local.workflowCreating, onInput: event => updateCustomAgent(index, "tools", event.target.value) })]),
              h("label", null, [h("span", null, "依赖上游 Agent（可留空，以逗号分隔）"), h("input", { value: agent.dependsOn || "", placeholder: "例如：数据分析员, 异常分析员", disabled: local.workflowCreating, onInput: event => updateCustomAgent(index, "dependsOn", event.target.value) })]),
              h("button", { type: "button", class: "btn-sm btn-sm-ghost", disabled: local.workflowCreating || form.customAgents.length <= 1, onClick: () => { removeCustomAgent(index); renderPanel(); } }, "移除 Agent"),
            ])),
          ]),
        h("label", null, [
          h("span", null, "名称"),
          h("input", {
            value: form.name,
            disabled: local.workflowCreating,
            onInput: event => updateWorkflowCreate("name", event.target.value),
          }),
        ]),
        h("label", null, [
          h("span", null, "运行模式"),
          h("select", {
            value: form.mode,
            disabled: local.workflowCreating,
            onChange: event => {
              updateWorkflowCreate("mode", event.target.value);
              renderPanel();
            },
          }, [
            h("option", { value: "full_auto" }, "全自动"),
            h("option", { value: "key_approval" }, "关键审批"),
            h("option", { value: "exception_review" }, "异常复核"),
          ]),
        ]),
        h("p", { class: "workflow-create-hint" },
          form.mode === "key_approval"
            ? "复核完成后暂停，人工批准后生成报告。"
            : form.mode === "exception_review"
              ? "正常节点自动推进，失败时进入人工复核。"
              : "所有节点自动推进，审批边也自动通过。",
        ),
        h("label", null, [
          h("span", null, "输入字段"),
          h("input", {
            value: form.sourceKey,
            disabled: local.workflowCreating,
            spellcheck: "false",
            onInput: event => updateWorkflowCreate("sourceKey", event.target.value),
          }),
        ]),
        h("label", null, [
          h("span", null, "描述"),
          h("textarea", {
            rows: 3,
            value: form.description,
            disabled: local.workflowCreating,
            onInput: event => updateWorkflowCreate("description", event.target.value),
          }),
        ]),
        h("div", { class: "workflow-create-actions" }, [
          h("button", {
            class: "btn-sm btn-sm-primary",
            type: "button",
            disabled: local.workflowCreating,
            onClick: createWorkflowFromTemplate,
          }, local.workflowCreating ? "创建中..." : "创建并发布"),
        ]),
      ]);
    }

    function renderWorkflowList() {
      if (local.workflowsLoading && !local.workflows.length) {
        return h("div", { class: "teams-empty" }, "正在读取 Workflow...");
      }
      if (!local.workflows.length) {
        return h("div", { class: "teams-empty" }, "还没有 Workflow。创建或保存动态协作路径后会显示在这里。");
      }
      return h("div", { class: "workflow-list" }, local.workflows.map((workflow, index) => {
        const version = workflow.current_version || {};
        const graph = version.graph || workflow.draft_graph || {};
        const schema = version.input_schema || workflow.draft_input_schema || {};
        const requiredInputs = Array.isArray(schema.required) ? schema.required : [];
        const inputValues = workflowInputValues(workflow.id, schema);
        const inputProperties = schema.properties && typeof schema.properties === "object"
          ? schema.properties
          : {};
        const displayInputProperties = {
          ...inputProperties,
          business_context: inputProperties.business_context || {
            type: "string",
            title: "业务补充说明",
            description: "可填写字段单位、口径、业务目标或需要重点核查的问题",
          },
        };
        const published = Boolean(workflow.current_version_id);
        const expanded = Object.prototype.hasOwnProperty.call(local.workflowExpanded, workflow.id)
          ? local.workflowExpanded[workflow.id]
          : index === 0;
        const nodes = graph.nodes || [];
        const edges = graph.edges || [];
        const mode = graph.run_policy?.mode || "full_auto";
        const versionLabel = version.version_number
          ? `版本 ${version.version_number}`
          : published ? `版本 ${workflow.current_version_id.slice(-6)}` : "草稿";
        return h("article", {
          key: workflow.id,
          class: expanded ? "workflow-card expanded" : "workflow-card",
        }, [
          h("div", { class: "workflow-card-head" }, [
            h("div", { class: "workflow-card-title" }, [
              h("strong", null, workflow.name || workflow.id),
              h("div", { class: "workflow-card-meta" }, [
                h("span", { class: "workflow-mode" }, workflowModeLabel(mode)),
                h("span", null, versionLabel),
                h("span", null, `${nodes.length} 节点`),
                h("span", null, `${edges.length} 连线`),
              ]),
            ]),
            h("div", { class: "workflow-card-controls" }, [
              h("span", {
                class: published ? "workflow-status workflow-status-succeeded" : "workflow-status",
              }, published ? "已发布" : "未发布"),
              h("div", { class: "workflow-card-secondary" }, [
                h("button", {
                  class: "workflow-expand-btn",
                  type: "button",
                  title: expanded ? "收起流程详情" : "展开流程详情",
                  "aria-expanded": String(expanded),
                  onClick: () => {
                    local.workflowExpanded[workflow.id] = !expanded;
                    renderPanel();
                  },
                }, expanded ? "收起" : "展开"),
                h("button", {
                  class: "workflow-delete-btn",
                  type: "button",
                  title: "永久删除 Workflow 及其运行数据",
                  disabled: local.workflowDeleting === workflow.id,
                  onClick: () => deleteWorkflow(workflow),
                }, local.workflowDeleting === workflow.id ? "删除中" : "删除"),
              ]),
            ]),
          ]),
          expanded ? h("div", { class: "workflow-card-body" }, [
            workflow.description
              ? h("p", { class: "workflow-card-desc" }, workflow.description)
              : null,
            renderWorkflowBlueprint(workflow),
            h("div", { class: "workflow-input-head" }, [
              h("strong", null, "运行输入（JSON）"),
              h("small", null, requiredInputs.length
                ? `必填：${requiredInputs.join("、")} · 自动保存`
                : "无必填字段 · 自动保存"),
            ]),
            h("div", { class: "workflow-input-form" }, Object.entries(displayInputProperties).map(([key, property]) => {
              const value = inputValues[key] == null ? "" : String(inputValues[key]);
              const isLongText = key === "business_context" || value.length > 80;
              return h("label", { key }, [
                h("span", null, `${workflowInputLabel(key, property)}${requiredInputs.includes(key) ? " *" : ""}`),
                isLongText
                  ? h("textarea", {
                    rows: 3, value, disabled: !published || local.workflowStarting === workflow.id,
                    placeholder: property.description || "可选补充说明",
                    onInput: event => updateWorkflowInputValue(workflow.id, schema, key, event.target.value),
                  })
                  : h("input", {
                    value, disabled: !published || local.workflowStarting === workflow.id,
                    placeholder: property.description || "请输入",
                    onInput: event => updateWorkflowInputValue(workflow.id, schema, key, event.target.value),
                  }),
                property.description ? h("small", null, property.description) : null,
              ]);
            })),
            h("details", {
              class: "workflow-input-advanced",
              open: Boolean(local.workflowInputAdvanced[workflow.id]),
              onToggle: event => { local.workflowInputAdvanced[workflow.id] = event.currentTarget.open; },
            }, [
              h("summary", null, "高级：编辑 JSON 输入"),
              h("textarea", {
                class: "workflow-inputs", rows: 4, spellcheck: "false",
                value: JSON.stringify(inputValues, null, 2),
                disabled: !published || local.workflowStarting === workflow.id,
                onInput: event => { saveWorkflowInputRaw(workflow.id, event.target.value); },
              }),
            ]),
            !published ? h("button", {
              class: "btn-sm btn-sm-primary workflow-start",
              type: "button",
              disabled: local.workflowCreating,
              onClick: () => publishSavedWorkflowDraft(workflow),
            }, local.workflowCreating ? "发布中..." : "审核后发布") : null,
            h("button", {
              class: "btn-sm btn-sm-primary workflow-start",
              type: "button",
              disabled: !published || local.workflowStarting === workflow.id,
              onClick: () => startWorkflow(workflow),
            }, local.workflowStarting === workflow.id ? "启动中..." : "启动 Workflow"),
          ]) : null,
        ]);
      }));
    }

    function renderWorkflowBlueprint(workflow) {
      const version = workflow?.current_version || {};
      const graph = version.graph || workflow?.draft_graph || {};
      const nodes = graph.nodes || [];
      const edges = graph.edges || [];
      if (!nodes.length) {
        return h("div", { class: "workflow-blueprint-empty" }, "未读取到流程定义");
      }
      const levels = workflowDagLevels(graph);
      const maxLevel = Math.max(0, ...[...levels.values()]);
      const stages = Array.from({ length: maxLevel + 1 }, (_, level) =>
        nodes.filter(node => (levels.get(String(node.node_id || "")) || 0) === level)
      ).filter(stage => stage.length);
      const labels = {
        inspect_data: "数据检查",
        analyze_metrics: "指标分析",
        analyze_anomalies: "异常分析",
        verify_findings: "结论复核",
        generate_report: "报告生成",
      };
      const edgeCounts = edges.reduce((counts, edge) => {
        const type = edge.type || "auto";
        counts[type] = (counts[type] || 0) + 1;
        return counts;
      }, {});
      const specialRules = edges.filter(edge => edge.type !== "auto");
      return h("section", { class: "workflow-blueprint" }, [
        h("div", { class: "workflow-blueprint-head" }, [
          h("strong", null, "流程结构"),
          h("small", null, `${stages.length} 个阶段`),
        ]),
        h("div", { class: "workflow-blueprint-stages" }, stages.map((stage, stageIndex) => h("div", {
          key: `stage-${stageIndex}`,
          class: "workflow-blueprint-stage",
        }, stage.map(node => {
          const nodeId = String(node.node_id || "");
          const outputs = Array.isArray(node.output_contract) ? node.output_contract : [];
          return h("div", {
            key: nodeId,
            class: "workflow-blueprint-node",
            title: `${nodeId}${outputs.length ? ` · 输出 ${outputs.join("、")}` : ""}`,
          }, [
            h("strong", null, labels[nodeId] || nodeId),
            h("span", null, outputs.length ? `${outputs.length} 项输出` : node.type || "agent"),
          ]);
        })))),
        h("div", { class: "workflow-blueprint-rules" }, [
          h("span", { class: "workflow-rule workflow-rule-primary" },
            `${workflowModeLabel(graph.run_policy?.mode)}执行`),
          edgeCounts.auto
            ? h("span", { class: "workflow-rule" }, `${edgeCounts.auto} 条自动流转`)
            : null,
          ...specialRules.map(edge => h("span", {
            key: edge.edge_id || `${edge.from_node}-${edge.to_node}-${edge.type}`,
            class: `workflow-rule workflow-rule-${edge.type}`,
          }, edge.type === "retry_loop"
            ? `返工至 ${labels[edge.to_node] || edge.to_node} · 最多 ${edge.max_iterations || 1} 次`
            : `${workflowEdgeLabel(edge.type)}后进入 ${labels[edge.to_node] || edge.to_node}`)),
        ]),
      ]);
    }

    function renderRunList() {
      if (local.workflowsLoading && !local.runs.length) {
        return h("div", { class: "teams-empty compact" }, "正在读取运行记录...");
      }
      if (!local.runs.length) {
        return h("div", { class: "teams-empty compact" }, "暂无 Workflow Run。");
      }
      return h("div", { class: "workflow-run-list" }, local.runs.map(run => {
        const workflow = workflowForRun(run);
        const active = local.selectedRun === run.id;
        return h("div", {
          key: run.id,
          class: active ? "workflow-run-row active" : "workflow-run-row",
        }, [
          h("button", {
            class: active ? "workflow-run-card active" : "workflow-run-card",
            type: "button",
            onClick: () => selectWorkflowRun(run.id),
          }, [
            h("div", { class: "workflow-run-card-main" }, [
              h("strong", null, workflow?.name || run.workflow_version_id || run.id),
              h("span", null, run.id),
            ]),
            h("div", { class: "workflow-run-card-meta" }, [
              h("span", { class: workflowStatusClass(run.status) }, workflowStatusLabel(run.status)),
              h("small", null, formatTime(run.started_at)),
            ]),
          ]),
          h("button", {
            class: "workflow-run-delete",
            type: "button",
            title: "永久删除本次运行及其数据",
            disabled: local.workflowRunDeleting === run.id,
            onClick: () => deleteWorkflowRun(run),
          }, local.workflowRunDeleting === run.id ? "删除中" : "删除"),
        ]);
      }));
    }

    function workflowOutputText(value) {
      if (typeof value === "string") return decodeWorkflowEscapes(value);
      try {
        return JSON.stringify(value, null, 2);
      } catch {
        return String(value ?? "");
      }
    }

    function unwrapWorkflowMarkdownFence(text) {
      const source = String(text || "");
      const match = source.trim().match(/^```(?:markdown|md)?\s*\r?\n([\s\S]*?)\r?\n?```\s*$/i);
      return match ? match[1] : source;
    }

    async function copyWorkflowOutput(value) {
      try {
        await navigator.clipboard.writeText(workflowOutputText(value));
        window.BAA.ui?.toast?.("结果已复制", "ok");
      } catch (error) {
        window.BAA.ui?.toast?.(`复制失败：${error.message || error}`, "err");
      }
    }

    function renderWorkflowOutputValue(value, compact = false) {
      const text = workflowOutputText(value);
      if (typeof value === "string") {
        return h("div", {
          class: compact
            ? "workflow-output-content team-markdown compact"
            : "workflow-output-content team-markdown",
          innerHTML: renderMarkdown(unwrapWorkflowMarkdownFence(text)),
        });
      }
      return h("pre", {
        class: compact ? "workflow-output-json compact" : "workflow-output-json",
      }, text);
    }

    function renderRunOutputs(detail) {
      const entries = Object.entries(detail?.outputs || {});
      if (!entries.length) {
        return detail?.run?.status === "succeeded"
          ? h("section", { class: "workflow-final-output empty" }, [
              h("strong", null, "运行已完成，但流程未声明最终输出。"),
            ])
          : null;
      }
      const labels = {
        operating_report: "经营分析报告",
        report: "分析报告",
      };
      const cleaningWorkflow = (detail?.graph?.nodes || []).some(node =>
        node.node_id === "propose_cleaning"
          && Array.isArray(node.output_contract)
          && node.output_contract.includes("cleaning_plan")
      );
      const executionNode = (detail?.nodes || []).filter(node => node.node_id === "apply_cleaning")
        .sort((left, right) => (right.iteration || 1) - (left.iteration || 1) || (right.attempt || 1) - (left.attempt || 1))[0];
      const supportsControlledCleaning = !!executionNode || (detail?.graph?.nodes || []).some(node => node.node_id === "apply_cleaning");
      const executionStatus = String(executionNode?.status || "pending").toLowerCase();
      const executionCopy = executionStatus === "succeeded"
        ? ["执行状态：已完成受控清洗", "已按获批方案生成 cleaned_data 派生表；原始源数据未被覆盖。最终报告仅依据实际工具执行结果生成。"]
        : executionStatus === "failed"
          ? ["执行状态：清洗未完成", "清洗节点执行失败，最终报告应列出失败原因；原始源数据未被覆盖。"]
          : ["执行状态：等待审批后执行", "清洗方案尚未获批。获批后系统才会创建 cleaned_data 派生表，原始源数据不会被覆盖。"];
      return h("section", { class: "workflow-final-output" }, [
        h("div", { class: "workflow-output-section-head" }, [
          h("div", null, [
            h("strong", null, "最终输出"),
            h("span", null, `${entries.length} 项结果`),
          ]),
        ]),
        cleaningWorkflow ? h("div", { class: "workflow-execution-status planning-only" }, [
          h("strong", null, supportsControlledCleaning ? executionCopy[0] : "旧版本：未执行数据清洗"),
          h("span", null, supportsControlledCleaning ? executionCopy[1] : "此运行来自旧版仅审批方案的 Workflow；没有执行真实清洗。请新建“受控数据清洗”版本后再运行。"),
        ]) : null,
        ...entries.map(([key, value]) => h("div", {
          key,
          class: "workflow-output-field",
        }, [
          h("div", { class: "workflow-output-field-head" }, [
            h("div", null, [
              h("strong", null, labels[key] || key),
              h("small", null, key),
            ]),
            h("button", {
              class: "btn-sm btn-sm-ghost workflow-output-copy",
              type: "button",
              onClick: () => copyWorkflowOutput(value),
            }, "复制"),
          ]),
          renderWorkflowOutputValue(value),
        ])),
      ]);
    }

    function renderRunNodes(detail) {
      const nodes = detail?.nodes || [];
      const latestNodes = latestNodeRunsById(nodes);
      const definitions = new Map((detail?.graph?.nodes || []).map(item => [item.node_id, item]));
      if (!nodes.length) {
        return h("div", { class: "teams-empty compact" }, "该 Run 暂无节点记录。");
      }
      const nodeDuration = node => {
        if (!node?.started_at || !node?.finished_at) return "";
        const started = new Date(node.started_at).getTime();
        const finished = new Date(node.finished_at).getTime();
        if (!Number.isFinite(started) || !Number.isFinite(finished) || finished < started) return "";
        return formatWorkflowDuration((finished - started) / 1000);
      };
      const nodeCost = value => {
        if (value === null || value === undefined || value === "") return "";
        const amount = Number(value);
        if (!Number.isFinite(amount)) return "";
        return `成本 $${amount.toLocaleString(undefined, { maximumFractionDigits: 6 })}`;
      };
      return h("div", { class: "workflow-run-nodes" }, nodes.map(node => {
        const outputs = Object.entries(node.output || {});
        const definition = definitions.get(node.node_id) || {};
        const limits = definition.limits || {};
        const usage = [
          node.model_name ? `模型 ${node.model_name}` : "",
          Number.isFinite(Number(node.input_tokens)) || Number.isFinite(Number(node.output_tokens))
            ? `输入 ${Number(node.input_tokens || 0).toLocaleString()} · 输出 ${Number(node.output_tokens || 0).toLocaleString()} Token`
            : "",
          node.tool_calls != null ? `工具调用 ${node.tool_calls} 次` : "",
          nodeDuration(node) ? `运行时长 ${nodeDuration(node)}` : "",
          nodeCost(node.estimated_cost),
          limits.max_tokens ? `Token 预算 ${Number(limits.max_tokens).toLocaleString()}` : "",
        ].filter(Boolean);
        const canRetry = detail?.run?.status === "failed"
          && node.status === "failed"
          && latestNodes.get(node.node_id)?.id === node.id;
        const canFork = ["succeeded", "failed", "canceled"].includes(detail?.run?.status)
          && node.status === "succeeded";
        return h("div", {
          key: node.id,
          class: "workflow-run-node",
        }, [
          h("div", { class: "workflow-run-node-head" }, [
            h("strong", null, node.node_id || node.id),
            h("span", { class: workflowStatusClass(node.status) }, workflowStatusLabel(node.status)),
          ]),
          h("div", { class: "workflow-node-meta" }, [
            h("span", null, definition.type ? `类型 ${definition.type}` : `Agent ${node.agent_profile_id || "-"}`),
            node.job_id ? h("button", {
              class: "workflow-job-link",
              type: "button",
              title: "在 Job 历史中查看",
              onClick: () => openWorkflowJob(node.job_id),
            }, `Job ${node.job_id}`) : h("span", null, "Job -"),
            h("span", { title: "同一节点因重试或返工产生的新执行次数" }, `第 ${node.attempt || 1} 次尝试`),
          ]),
          usage.length ? h("div", { class: "workflow-node-observability" }, usage.map(item =>
            h("span", { key: item }, item)
          )) : null,
          node.error ? h("div", { class: "workflow-node-error" }, node.error) : null,
          canRetry || canFork ? h("div", { class: "workflow-node-actions" }, [
            canRetry ? h("button", {
              class: "btn-sm btn-sm-primary",
              type: "button",
              disabled: Boolean(local.workflowRetrying || local.workflowForking),
              onClick: () => retryWorkflowNode(node),
            }, local.workflowRetrying === node.id ? "重新派发中..." : "重试节点") : null,
            canFork ? h("button", {
              class: "btn-sm btn-sm-ghost",
              type: "button",
              title: "复用该节点及其已成功的上游输出，创建新的可审计 Run",
              disabled: Boolean(local.workflowRetrying || local.workflowForking),
              onClick: () => forkWorkflowRunFromCheckpoint(node),
            }, local.workflowForking === node.id ? "创建分叉中..." : "从此检查点分叉") : null,
          ]) : null,
          outputs.length ? h("details", { class: "workflow-node-output" }, [
            h("summary", null, `查看节点输出 · ${outputs.length} 项`),
            h("div", { class: "workflow-node-output-fields" }, outputs.map(([key, value]) =>
              h("div", { key, class: "workflow-node-output-field" }, [
                h("div", { class: "workflow-node-output-head" }, [
                  h("strong", null, key),
                  h("button", {
                    class: "btn-sm btn-sm-ghost",
                    type: "button",
                    onClick: event => {
                      event.preventDefault();
                      copyWorkflowOutput(value);
                    },
                  }, "复制"),
                ]),
                renderWorkflowOutputValue(value, true),
              ]),
            )),
          ]) : null,
        ]);
      }));
    }

    function workflowNodeLabel(nodeId) {
      const labels = {
        inspect_data: "数据检查",
        analyze_metrics: "指标分析",
        analyze_anomalies: "异常分析",
        verify_findings: "结论复核",
        generate_report: "报告生成",
        propose_cleaning: "清洗方案",
        verify_cleaning: "清洗方案复核",
        apply_cleaning: "执行清洗",
      };
      return labels[nodeId] || nodeId || "节点";
    }

    function workflowEventSummary(event) {
      const type = String(event?.type || "");
      const node = workflowNodeLabel(String(event?.node_id || ""));
      if (type === "workflow_run_created") {
        return { title: "已创建运行", detail: "已创建一条独立的 Workflow 运行记录。", tone: "info" };
      }
      if (type === "workflow_run_status") {
        return {
          title: `运行状态：${workflowStatusLabel(event?.status)}`,
          detail: event?.status === "succeeded" ? "全部节点已完成，运行已结算。" : "Workflow 状态已更新。",
          tone: event?.status || "info",
        };
      }
      if (type === "workflow_node_status") {
        return {
          title: `${node}：${workflowStatusLabel(event?.status)}`,
          detail: "节点执行状态已更新。",
          tone: event?.status || "info",
        };
      }
      if (type === "workflow_node_usage") {
        return {
          title: `${node}：已记录模型用量`,
          detail: `输入 ${Number(event?.input_tokens || 0).toLocaleString()} Token，输出 ${Number(event?.output_tokens || 0).toLocaleString()} Token。`,
          tone: "info",
        };
      }
      if (type === "workflow_checkpoint_node_reused") {
        return {
          title: `复用节点：${node}`,
          detail: "复用了来源 Run 的成功结果，未重新执行该节点。",
          tone: "succeeded",
        };
      }
      if (type === "workflow_run_checkpoint_forked") {
        const reused = Array.isArray(event?.reused_node_ids) ? event.reused_node_ids : [];
        return {
          title: "已从检查点创建分叉运行",
          detail: reused.length ? `已复用 ${reused.length} 个节点：${reused.map(workflowNodeLabel).join("、")}。` : "已复用检查点前的成功节点。",
          tone: "succeeded",
        };
      }
      if (type === "workflow_policy_denied") {
        return {
          title: `${node}：策略已拒绝`,
          detail: String(event?.reason || "当前权限策略不允许执行此节点。"),
          tone: "failed",
        };
      }
      return { title: "Workflow 审计事件", detail: type || "系统记录了一条运行事件。", tone: "info" };
    }

    function renderRunEvents(detail) {
      const events = detail?.events || [];
      if (!events.length) return null;
      return h("details", { class: "workflow-events-panel workflow-traceability-panel" }, [
        h("summary", { class: "workflow-traceability-summary" }, [
          h("span", null, `运行事件（${events.length} 条）`),
          h("small", null, "查看状态变化、复用和策略记录"),
        ]),
        h("div", { class: "workflow-events" }, events.slice(-80).reverse().map(event => {
          const summary = workflowEventSummary(event);
          return h("details", {
            key: `${event.sequence}-${event.type}`,
            class: `workflow-event workflow-event-${summary.tone}`,
          }, [
            h("summary", { class: "workflow-event-head" }, [
              h("div", { class: "workflow-event-summary" }, [
                h("strong", null, summary.title),
                h("span", null, summary.detail),
              ]),
              h("small", null, formatTime(event.created_at)),
            ]),
            h("details", { class: "workflow-event-audit" }, [
              h("summary", null, "查看审计详情"),
              h("pre", null, JSON.stringify(event, null, 2)),
            ]),
          ]);
        })),
      ]);
    }

    function renderWorkflowDag(detail) {
      const graph = detail?.graph || {};
      const nodes = graph.nodes || [];
      const edges = graph.edges || [];
      if (!nodes.length) return null;
      const latestRuns = latestNodeRunsById(detail?.nodes || []);
      const levels = workflowDagLevels(graph);
      const maxLevel = Math.max(0, ...[...levels.values()]);
      const columns = Math.min(maxLevel + 1, 6);
      return h("section", { class: "workflow-dag" }, [
        h("div", { class: "team-section-title" }, "流程图"),
        h("div", {
          class: "workflow-dag-map",
          style: { gridTemplateColumns: `repeat(${columns}, minmax(150px, 1fr))` },
        }, nodes.map(node => {
          const nodeId = String(node.node_id || "");
          const runNode = latestRuns.get(nodeId) || {};
          const status = runNode.status || "pending";
          const badges = [
            isPendingApprovalNode(detail, nodeId) ? "待审批" : "",
            isRetryTarget(graph, nodeId) ? "返工目标" : "",
            node.join_policy === "all_terminal" ? "分支汇合" : "",
            node.on_reject === "close_branch" ? "可关闭分支" : "",
          ].filter(Boolean);
          return h("div", {
            key: nodeId,
            class: `workflow-dag-node workflow-dag-node-${status}`,
            style: { gridColumn: String(Math.min((levels.get(nodeId) || 0) + 1, columns)) },
          }, [
            h("div", { class: "workflow-dag-node-head" }, [
              h("strong", null, nodeId),
              h("span", { class: workflowStatusClass(status) }, workflowStatusLabel(status)),
            ]),
            h("div", { class: "workflow-dag-node-meta" }, [
              h("span", null, node.type || "agent"),
              h("span", null, runNode.job_id ? `job ${String(runNode.job_id).slice(-6)}` : "未派发"),
              h("span", null, `i${runNode.iteration || 1}/a${runNode.attempt || 1}`),
            ]),
            badges.length ? h("div", { class: "workflow-dag-badges" }, badges.map(label => h("span", { key: label }, label))) : null,
          ]);
        })),
        edges.length ? h("div", { class: "workflow-dag-edges" }, edges.map(edge => h("div", {
          key: edge.edge_id || `${edge.from_node}-${edge.to_node}-${edge.type}`,
          class: "workflow-dag-edge-row",
        }, [
          h("span", null, edge.from_node || "-"),
          h("span", { class: workflowEdgeClass(edge.type) }, workflowEdgeLabel(edge.type)),
          h("span", null, edge.to_node || "-"),
          edge.max_iterations ? h("small", null, `max ${edge.max_iterations}`) : null,
        ]))) : null,
      ]);
    }

    function renderRunApprovals(detail) {
      const approvals = detail?.approvals || [];
      if (!approvals.length) return null;
      return h("section", { class: "workflow-approvals" }, [
        h("div", { class: "team-section-title" }, "审批任务"),
        ...approvals.map(approval => {
          const pending = approval.status === "pending";
          const sourceManifest = approvalManifest(detail, approval);
          if (pending) seedApprovalRevisionFields(approval, sourceManifest);
          const form = getApprovalForm(approval);
          const revisionFields = form.revisionFields || [];
          const sourceNode = (detail?.nodes || []).find(node => node.id === approval.node_run_id) || {};
          const cleaningApproval = approval.node_id === "propose_cleaning"
            || approval.node_id === "verify_cleaning"
            || (sourceManifest?.items || []).some(item => manifestItemName(item) === "cleaning_plan");
          const supportsControlledCleaning = (detail?.graph?.nodes || []).some(node => node.node_id === "apply_cleaning");
          const planNode = (detail?.nodes || []).filter(node => node.node_id === "propose_cleaning")
            .sort((left, right) => (right.iteration || 1) - (left.iteration || 1) || (right.attempt || 1) - (left.attempt || 1))[0] || {};
          const planValue = sourceNode?.output?.cleaning_plan ?? planNode?.output?.cleaning_plan;
          const commentRows = [
            approval.comment ? `意见：${approval.comment}` : "",
            approval.comments && Object.keys(approval.comments).length
              ? `结构化意见：${JSON.stringify(approval.comments)}`
              : "",
            approval.revised_artifact_manifest_id
              ? `修订 Manifest：${approval.revised_artifact_manifest_id}`
              : "",
          ].filter(Boolean);
          return h("div", {
            key: approval.id,
            class: pending ? "workflow-approval pending" : "workflow-approval",
          }, [
            h("div", { class: "workflow-approval-head" }, [
              h("div", null, [
                h("strong", null, approval.reason === "exception_review" ? "异常触发审批" : "关键节点审批"),
                h("span", null, approval.node_id || approval.node_run_id),
              ]),
              h("span", {
                class: pending
                  ? "workflow-status workflow-status-waiting_approval"
                  : "workflow-status workflow-status-succeeded",
              }, pending ? "待处理" : `已${approval.decision || "处理"}`),
            ]),
            h("div", { class: "workflow-approval-meta" }, [
              h("span", null, `mode ${approval.mode || "-"}`),
              h("span", null, `requested ${formatTime(approval.requested_at) || "-"}`),
              approval.artifact_manifest_id
                ? h("span", null, `manifest ${approval.artifact_manifest_id}`)
                : null,
            ]),
            !pending && commentRows.length
              ? h("div", { class: "workflow-approval-note" }, commentRows.join("\n"))
              : null,
            pending ? h("div", { class: "workflow-approval-form" }, [
              cleaningApproval ? h("div", { class: "workflow-approval-scope" }, [
                h("strong", null, supportsControlledCleaning ? "本次审批授权执行清洗" : "旧版本仅审批方案，不执行清洗"),
                h("span", null, supportsControlledCleaning
                  ? "方案已通过独立复核。批准后系统会按获批方案执行一次 clean_data，并将结果写入 cleaned_data 派生表；原始源数据不会被覆盖。"
                  : "此运行来自旧版仅审批方案的 Workflow；批准后只会生成说明，不会执行数据清洗。"),
              ]) : null,
              planValue != null ? h("details", { class: "workflow-approval-source", open: true }, [
                h("summary", null, "查看待审批方案"),
                renderWorkflowOutputValue(planValue, true),
              ]) : null,
              h("label", null, [
                h("span", null, "审批意见"),
                h("textarea", {
                  rows: 2,
                  value: form.comment,
                  disabled: local.workflowApproving === approval.id,
                  placeholder: "说明批准依据、重做要求或终止原因",
                  onInput: event => updateApprovalForm(approval, "comment", event.target.value),
                }),
              ]),
              !cleaningApproval ? h("details", { class: "workflow-approval-revision-details" }, [
                h("summary", null, "需要人工改写方案时，再展开修订草稿"),
                h("div", { class: "workflow-approval-revision-head" }, [
                h("strong", null, "修订草稿"),
                h("div", { class: "workflow-approval-revision-actions" }, [
                  h("button", {
                    class: "btn-sm btn-sm-ghost",
                    type: "button",
                    disabled: !sourceManifest || local.workflowApproving === approval.id,
                    onClick: () => {
                      seedApprovalRevisionFields(approval, sourceManifest, true);
                      renderPanel();
                    },
                  }, "从 Manifest 重置"),
                  h("button", {
                    class: "btn-sm btn-sm-ghost",
                    type: "button",
                    disabled: local.workflowApproving === approval.id,
                    onClick: () => {
                      addApprovalRevisionField(approval);
                      renderPanel();
                    },
                  }, "添加字段"),
                ]),
              ]),
              revisionFields.length ? h("div", { class: "workflow-approval-fields" }, revisionFields.map((field, index) => h("div", {
                key: `${approval.id}-${index}-${field.source || field.key}`,
                class: "workflow-approval-field",
              }, [
                h("input", {
                  value: field.key,
                  disabled: local.workflowApproving === approval.id,
                  placeholder: "字段名",
                  onInput: event => updateApprovalRevisionField(approval, index, "key", event.target.value),
                }),
                h("textarea", {
                  rows: 2,
                  value: field.value,
                  disabled: local.workflowApproving === approval.id,
                  spellcheck: "false",
                  placeholder: "字段值，支持 JSON 或文本",
                  onInput: event => updateApprovalRevisionField(approval, index, "value", event.target.value),
                }),
                h("button", {
                  class: "btn-sm btn-sm-ghost",
                  type: "button",
                  disabled: local.workflowApproving === approval.id,
                  onClick: () => {
                    removeApprovalRevisionField(approval, index);
                    renderPanel();
                  },
                }, "移除"),
              ]))) : h("div", { class: "teams-empty compact" }, "该审批没有可内联的 Manifest 字段，可直接编辑 JSON。"),
              h("div", { class: "workflow-approval-revision" }, [
                h("label", null, [
                  h("span", null, "修订摘要"),
                  h("input", {
                    value: form.revisedSummary,
                    disabled: local.workflowApproving === approval.id,
                    placeholder: "用于 approve_with_changes 的修订说明",
                    onInput: event => updateApprovalForm(approval, "revisedSummary", event.target.value),
                  }),
                ]),
                h("label", null, [
                  h("span", null, "修订输出 JSON"),
                  h("textarea", {
                    rows: 4,
                    value: form.revisedOutputs,
                    disabled: local.workflowApproving === approval.id,
                    spellcheck: "false",
                    placeholder: '{"verification_report":"人工修订后的结论"}',
                    onInput: event => updateApprovalForm(approval, "revisedOutputs", event.target.value),
                  }),
                ]),
              ]),
              ]) : null,
            ]) : null,
            pending ? h("div", { class: "workflow-approval-actions" }, [
              h("button", {
                class: "btn-sm btn-sm-primary",
                type: "button",
                disabled: local.workflowApproving === approval.id,
                onClick: () => decideWorkflowApproval(approval, "approve"),
              }, cleaningApproval && supportsControlledCleaning ? "批准方案并执行清洗" : cleaningApproval ? "批准方案并生成说明" : "批准继续"),
              !cleaningApproval ? h("button", {
                class: "btn-sm btn-sm-primary",
                type: "button",
                disabled: local.workflowApproving === approval.id,
                onClick: () => decideWorkflowApproval(approval, "approve_with_changes"),
              }, "带修改批准") : null,
              h("button", {
                class: "btn-sm btn-sm-ghost",
                type: "button",
                disabled: local.workflowApproving === approval.id,
                onClick: () => decideWorkflowApproval(approval, "reject_and_retry"),
              }, cleaningApproval ? "补充要求并重新生成方案" : "要求重做"),
              h("button", {
                class: "btn-sm btn-sm-danger",
                type: "button",
                disabled: local.workflowApproving === approval.id,
                onClick: () => decideWorkflowApproval(approval, "reject_and_stop"),
              }, "驳回终止"),
            ]) : null,
          ]);
        }),
      ]);
    }

    function renderRunMaterials(detail) {
      const manifests = detail?.manifests || [];
      const consumptions = detail?.consumptions || [];
      const lineage = detail?.lineage || [];
      if (!manifests.length && !lineage.length && !consumptions.length) return [];
      const nodesByRunId = new Map((detail?.nodes || []).map(node => [node.id, node]));
      const artifactsById = new Map(manifests.flatMap(manifest => (manifest.items || []).map(item => [item.artifact_id, item])));
      const sourceLabel = nodeRunId => workflowNodeLabel(nodesByRunId.get(nodeRunId)?.node_id || "");
      const artifactLabel = artifactId => artifactsById.get(artifactId)?.logical_name || artifactsById.get(artifactId)?.name || "上游材料";
      return [
        lineage.length ? h("details", { class: "workflow-traceability-panel" }, [
          h("summary", null, [
            h("span", null, `最终输出来源（${lineage.length} 项）`),
            h("small", null, "查看报告由哪些节点和证据生成"),
          ]),
          h("p", { class: "workflow-traceability-hint" }, "每项最终输出都可追溯到生成节点、质量检查和证据引用。"),
          ...lineage.map(item => h("div", { class: "workflow-consumption", key: `${item.output}:${item.artifact_id}` }, [
            h("strong", null, item.output),
            h("span", null, `由${workflowNodeLabel(item.producer_node_id)}生成`),
            item.quality?.status ? h("small", null, `质量 ${item.quality.status}`) : null,
            item.evidence?.length ? h("small", null, `证据 ${item.evidence.length} 条`) : null,
          ])),
        ]) : null,
        manifests.length ? h("details", { class: "workflow-traceability-panel" }, [
          h("summary", null, [
            h("span", null, `节点输出与材料（${manifests.length} 份）`),
            h("small", null, "查看节点产生的可追溯结果"),
          ]),
          h("div", { class: "workflow-materials" }, manifests.map(manifest => h("details", {
            key: manifest.id,
            class: "workflow-material",
          }, [
            h("summary", null, h("span", null, `${sourceLabel(manifest.node_run_id)}的输出 · ${manifest.items?.length || 0} 项`)),
            h("div", { class: "workflow-material-items" }, (manifest.items || []).map(item => h("div", {
              key: item.artifact_id || item.uri,
              class: "workflow-material-item",
            }, [
              h("strong", null, item.logical_name || item.name || item.artifact_id),
              item.type ? h("small", { class: "workflow-material-type" }, `类型 ${item.type} · ${item.schema_version || "v1"}`) : null,
              item.quality?.status ? h("small", { class: `workflow-material-quality ${item.quality.status}` }, `质量 ${item.quality.status}`) : null,
              item.evidence?.length ? h("small", { class: "workflow-material-evidence" }, `证据 ${item.evidence.length} 条`) : null,
              item.content_available ? h("button", {
                class: "btn-sm btn-sm-ghost workflow-artifact-open",
                type: "button",
                disabled: local.workflowArtifactLoading === item.artifact_id,
                onClick: () => loadWorkflowArtifact(detail?.run?.id, item.artifact_id),
              }, local.workflowArtifactLoading === item.artifact_id ? "读取中..." : "查看完整内容") : null,
              Object.prototype.hasOwnProperty.call(local.workflowArtifactContents, item.artifact_id)
                ? h("details", { class: "workflow-artifact-content", open: true }, [
                  h("summary", null, "完整 Artifact 内容"),
                  renderWorkflowOutputValue(local.workflowArtifactContents[item.artifact_id], true),
                ]) : null,
            ]))),
          ]))),
        ]) : null,
        consumptions.length ? h("details", { class: "workflow-traceability-panel" }, [
          h("summary", null, [
            h("span", null, `材料传递（${consumptions.length} 条）`),
            h("small", null, "查看下游节点使用了哪些上游结果"),
          ]),
          h("div", { class: "workflow-consumptions" }, consumptions.map(item => h("div", {
            key: item.id,
            class: "workflow-consumption",
          }, [
            h("strong", null, `${sourceLabel(item.consumer_node_run_id)} 使用了“${artifactLabel(item.artifact_id)}”`),
            h("span", null, `来源：${sourceLabel(item.producer_node_run_id)}`),
          ]))),
        ]) : null,
      ].filter(Boolean);
    }

    function renderWorkflowTraceability(detail) {
      const materialPanels = renderRunMaterials(detail);
      const events = renderRunEvents(detail);
      if (!materialPanels.length && !events) return null;
      return h("details", { class: "workflow-traceability" }, [
        h("summary", { class: "team-section-title" }, [
          h("span", null, "追溯与审计"),
          h("small", null, `${(detail?.manifests || []).length} 份材料 · ${(detail?.events || []).length} 条事件`),
        ]),
        h("p", { class: "workflow-traceability-intro" }, "用于核对最终输出的来源、节点间材料传递及完整运行记录；日常查看结果时无需展开。"),
        h("div", { class: "workflow-traceability-sections" }, [
          ...materialPanels,
          events,
        ]),
      ]);
    }

    function renderWorkflowKnowledgeCandidates(detail) {
      const candidates = detail?.knowledge_candidates || [];
      if (!candidates.length) return null;
      const typeLabel = {
        report_template: "报告模板",
        metric_sql: "指标 SQL",
      };
      return h("section", { class: "workflow-knowledge-candidates" }, [
        h("div", { class: "team-section-title" }, "知识入库候选"),
        ...candidates.map(candidate => {
          const pending = candidate.status === "pending";
          const deciding = local.workflowCandidateDeciding === candidate.id;
          return h("article", {
            class: `workflow-knowledge-candidate ${candidate.status || "pending"}`,
            key: candidate.id,
          }, [
            h("div", { class: "workflow-knowledge-candidate-head" }, [
              h("div", null, [
                h("strong", null, candidate.title || candidate.id),
                h("span", null, typeLabel[candidate.candidate_type] || candidate.candidate_type),
              ]),
              h("span", {
                class: candidate.status === "accepted"
                  ? "workflow-status workflow-status-succeeded"
                  : candidate.status === "rejected"
                    ? "workflow-status workflow-status-failed"
                    : "workflow-status workflow-status-waiting_approval",
              }, candidate.status === "accepted" ? "已入库" : candidate.status === "rejected" ? "已拒绝" : "待确认"),
            ]),
            h("div", { class: "workflow-knowledge-candidate-meta" }, [
              h("span", null, `Version ${String(candidate.workflow_version_id || "").slice(-8)}`),
              h("span", null, `Manifest ${String(candidate.source_manifest_id || "-").slice(-8)}`),
            ]),
            pending ? h("div", { class: "workflow-knowledge-candidate-actions" }, [
              h("button", {
                class: "btn-sm btn-sm-primary",
                type: "button",
                disabled: Boolean(local.workflowCandidateDeciding),
                onClick: () => decideWorkflowKnowledgeCandidate(candidate, "accept"),
              }, deciding ? "处理中..." : "接受入库"),
              h("button", {
                class: "btn-sm btn-sm-ghost",
                type: "button",
                disabled: Boolean(local.workflowCandidateDeciding),
                onClick: () => decideWorkflowKnowledgeCandidate(candidate, "reject"),
              }, "拒绝"),
            ]) : null,
          ]);
        }),
      ]);
    }

    function formatWorkflowDuration(seconds) {
      const value = Number(seconds) || 0;
      if (value < 60) return `${Math.round(value)} 秒`;
      if (value < 3600) return `${Math.round(value / 60)} 分钟`;
      return `${(value / 3600).toFixed(1)} 小时`;
    }

    function renderWorkflowMetricsDashboard() {
      const metrics = local.workflowMetrics;
      if (local.workflowMetricsLoading && !metrics) {
        return h("section", { class: "workflow-metrics" }, "正在汇总运行指标...");
      }
      if (!metrics) return null;
      const summary = metrics.summary || {};
      const versions = metrics.versions || [];
      const suggestions = local.workflowSuggestions || [];
      return h("section", { class: "workflow-metrics" }, [
        h("div", { class: "workflow-metrics-head" }, [
          h("div", null, [
            h("strong", null, "运行看板"),
            h("span", null, `${summary.workflow_version_count || 0} 个版本 · ${summary.run_count || 0} 次运行`),
          ]),
          h("span", { class: "workflow-metrics-audit" }, "基于 Run / Node / Artifact 审计数据"),
        ]),
        h("div", { class: "workflow-metric-grid" }, [
          h("div", null, [h("span", null, "成功率"), h("strong", null, `${Math.round((summary.success_rate || 0) * 100)}%`)]),
          h("div", null, [h("span", null, "输入 Token"), h("strong", null, Number(summary.input_tokens || 0).toLocaleString())]),
          h("div", null, [h("span", null, "输出 Token"), h("strong", null, Number(summary.output_tokens || 0).toLocaleString())]),
          h("div", null, [h("span", null, "成本"), h("strong", null, summary.estimated_cost == null ? "未配置价格" : String(summary.estimated_cost))]),
        ]),
        versions.length ? h("div", { class: "workflow-metric-versions" }, versions.map(version =>
          h("div", { class: "workflow-metric-version", key: version.workflow_version_id }, [
            h("div", null, [
              h("strong", null, `${version.workflow_name} · v${version.version_number || "-"}`),
              h("span", null, `${version.run_count} 次 · 平均 ${formatWorkflowDuration(version.avg_duration_seconds)}`),
            ]),
            h("span", { class: version.success_rate >= 0.9 ? "good" : "warn" }, `${Math.round((version.success_rate || 0) * 100)}% 成功`),
          ]),
        )) : h("div", { class: "workflow-metrics-empty" }, "运行后将显示版本成功率、时长和 Token 用量。"),
        suggestions.length ? h("details", { class: "workflow-suggestions" }, [
          h("summary", null, [
            h("span", { class: "team-section-title" }, "规则化优化建议"),
            h("small", null, `${suggestions.length} 项`),
          ]),
          ...suggestions.map(suggestion => h("div", { class: "workflow-suggestion", key: suggestion.id }, [
            h("div", null, [
              h("strong", null, suggestion.title),
              h("span", null, suggestion.rationale),
            ]),
            h("button", {
              class: "btn-sm btn-sm-ghost",
              type: "button",
              disabled: Boolean(local.workflowCreatingDraft),
              onClick: () => createWorkflowOptimizationDraft(suggestion),
            }, local.workflowCreatingDraft === suggestion.id ? "创建中..." : "创建优化草稿"),
          ])),
        ]) : null,
      ]);
    }

    function renderWorkflowDetail() {
      const detail = local.runDetail;
      if (!local.selectedRun) {
        return h("div", { class: "teams-empty" }, "选择一个 Workflow Run 查看节点、事件和材料。");
      }
      if (local.workflowsLoading && !detail) {
        return h("div", { class: "teams-empty" }, "正在读取运行详情...");
      }
      if (!detail?.run) {
        return h("div", { class: "teams-empty" }, "未找到运行详情。");
      }
      const run = detail.run;
      const pendingCandidateCount = (detail.knowledge_candidates || []).filter(
        item => item.status === "pending",
      ).length;
      return h("div", { class: "workflow-detail" }, [
        h("div", { class: "team-detail-head" }, [
          h("div", null, [
            h("h3", null, workflowForRun(run)?.name || "Workflow Run"),
            h("p", null, run.id),
          ]),
          h("div", { class: "team-detail-actions" }, [
            h("span", { class: workflowStatusClass(run.status) }, workflowStatusLabel(run.status)),
            run.status === "succeeded" && !(detail.templates || []).length ? h("button", {
              class: "btn-sm btn-sm-ghost",
              type: "button",
              disabled: local.workflowSavingTemplate === run.id,
              onClick: () => saveWorkflowTemplate(run),
            }, local.workflowSavingTemplate === run.id ? "保存中..." : "保存运行模板") : null,
            run.status === "succeeded" ? h("button", {
              class: "btn-sm btn-sm-ghost",
              type: "button",
              disabled: local.workflowGeneratingCandidates === run.id,
              onClick: () => pendingCandidateCount
                ? focusWorkflowKnowledgeCandidates()
                : generateWorkflowKnowledgeCandidates(run),
            }, local.workflowGeneratingCandidates === run.id
              ? "生成中..."
              : pendingCandidateCount
                ? `待入库 ${pendingCandidateCount} 项`
                : "生成入库候选") : null,
            run.status === "paused" ? h("button", {
              class: "btn-sm btn-sm-primary",
              type: "button",
              disabled: local.workflowResuming === run.id,
              onClick: () => resumeWorkflowRun(run.id),
            }, local.workflowResuming === run.id ? "恢复中..." : "恢复 Run") : null,
            h("button", {
              class: "btn-sm btn-sm-danger",
              type: "button",
              disabled: !isWorkflowActive(run.status) || local.workflowCanceling === run.id,
              onClick: () => cancelWorkflowRun(run.id),
            }, local.workflowCanceling === run.id ? "取消中..." : "取消 Run"),
          ]),
        ]),
        h("div", { class: "workflow-run-summary" }, [
          h("span", null, `Started ${formatTime(run.started_at) || "-"}`),
          h("span", null, `Finished ${formatTime(run.finished_at) || "-"}`),
          h("span", null, `By ${run.started_by || "-"}`),
        ]),
        renderRunOutputs(detail),
        h("div", { class: "workflow-detail-tabs" }, [
          renderWorkflowKnowledgeCandidates(detail),
          renderRunApprovals(detail),
          renderWorkflowDag(detail),          h("section", null, [
            h("div", { class: "team-section-title" }, "节点"),
            renderRunNodes(detail),
          ]),
          renderWorkflowTraceability(detail),
        ]),
      ]);
    }

    function renderWorkflowPanel() {
      return h("div", { class: "workflow-panel" }, [
        local.workflowsError
          ? h("div", { class: "teams-inline-error" }, local.workflowsError)
          : null,
        h("section", { class: "workflow-sidebar" }, [
          h("div", { class: "team-section-title" }, "创建 Workflow"),
          renderWorkflowCreate(),
          h("div", { class: "team-section-title" }, "已发布 Workflow"),
          renderWorkflowList(),
          h("div", { class: "team-section-title" }, "运行记录"),
          renderRunList(),
        ]),
        h("section", { class: "workflow-main" }, [
          renderWorkflowMetricsDashboard(),
          renderWorkflowDetail(),
        ]),
      ]);
    }

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
