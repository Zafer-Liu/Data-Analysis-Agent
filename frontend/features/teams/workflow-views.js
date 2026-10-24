// Workflow-domain render functions for the teams panel (lifted from renderPanel).
import {
  formatTime,
  renderMarkdown,
  workflowStatusLabel,
  workflowStatusClass,
  workflowEdgeLabel,
  workflowEdgeClass,
  latestNodeRunsById,
  workflowDagLevels,
  isPendingApprovalNode,
  isRetryTarget,
  isWorkflowActive,
} from "./shared.js";
import { WORKFLOW_TEMPLATES } from "./workflow-actions.js";

const Vue = window.Vue;

export function createWorkflowViews(ctx) {
  const { local, renderPanel, selectWorkflowRun, openWorkflowJob } = ctx;
  const { loadWorkflowArtifact } = ctx.api;
  const {
    workflowModeLabel,
    normalizedWorkflowCreate,
    updateWorkflowCreate,
    getApprovalForm,
    updateApprovalForm,
    manifestItemName,
    approvalManifest,
    seedApprovalRevisionFields,
    updateApprovalRevisionField,
    addApprovalRevisionField,
    removeApprovalRevisionField,
    createWorkflowFromTemplate,
    cancelWorkflowRun,
    resumeWorkflowRun,
    decodeWorkflowEscapes,
    selectWorkflowTemplate,
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
    saveWorkflowInputRaw,
    workflowInputValues,
    updateWorkflowInputValue,
    workflowInputLabel,
    startWorkflow,
    deleteWorkflow,
    workflowForRun,
    deleteWorkflowRun,
    decideWorkflowApproval,
  } = ctx.wActions;

  const { h } = Vue;

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

  return {
    renderWorkflowCreate,
    renderWorkflowList,
    renderWorkflowBlueprint,
    renderRunList,
    workflowOutputText,
    unwrapWorkflowMarkdownFence,
    copyWorkflowOutput,
    renderWorkflowOutputValue,
    renderRunOutputs,
    renderRunNodes,
    workflowNodeLabel,
    workflowEventSummary,
    renderRunEvents,
    renderWorkflowDag,
    renderRunApprovals,
    renderRunMaterials,
    renderWorkflowTraceability,
    renderWorkflowKnowledgeCandidates,
    formatWorkflowDuration,
    renderWorkflowMetricsDashboard,
    renderWorkflowDetail,
    renderWorkflowPanel,
  };
}
