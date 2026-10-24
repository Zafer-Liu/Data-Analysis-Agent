// Workflow-domain business actions for the teams panel.
import { state } from "../../core/runtime.js";

export const WORKFLOW_TEMPLATES = Object.freeze({
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

export function createWorkflowActions(ctx) {
  const { local, renderPanel, refreshWorkflows } = ctx;
  const {
    fetchJson,
    createAgentProfile,
    createWorkflowDraft,
    validateWorkflow,
    publishWorkflow,
    fetchWorkflowRun,
  } = ctx.api;

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

  return {
    workflowModeLabel,
    normalizedWorkflowCreate,
    updateWorkflowCreate,
    getApprovalForm,
    updateApprovalForm,
    manifestItemName,
    manifestItemEditableValue,
    parseEditableValue,
    revisionFieldsToOutputs,
    approvalManifest,
    seedApprovalRevisionFields,
    updateApprovalRevisionField,
    addApprovalRevisionField,
    removeApprovalRevisionField,
    buildApprovalDecisionPayload,
    buildCustomWorkflow,
    buildWorkflowTemplate,
    createWorkflowFromTemplate,
    cancelWorkflowRun,
    resumeWorkflowRun,
    decodeWorkflowEscapes,
    selectWorkflowTemplate,
    normalizedCustomAgents,
    updateCustomAgent,
    addCustomAgent,
    removeCustomAgent,
    retryWorkflowNode,
    forkWorkflowRunFromCheckpoint,
    saveWorkflowTemplate,
    focusWorkflowKnowledgeCandidates,
    generateWorkflowKnowledgeCandidates,
    decideWorkflowKnowledgeCandidate,
    publishSavedWorkflowDraft,
    createWorkflowOptimizationDraft,
    defaultWorkflowInputs,
    workflowInputStorageKey,
    readWorkflowInputRaw,
    saveWorkflowInputRaw,
    workflowInputValues,
    updateWorkflowInputValue,
    workflowInputLabel,
    startWorkflow,
    deleteWorkflow,
    workflowForRun,
    deleteWorkflowRun,
    decideWorkflowApproval,
  };
}
