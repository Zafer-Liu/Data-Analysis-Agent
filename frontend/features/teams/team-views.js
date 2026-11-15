// Team-domain render functions for the teams panel (lifted from renderPanel).
import { state } from "../../core/runtime.js";
import { isSilentRequestError, requestErrorMessage, requestMessage } from "../../core/api-client.js";
import {
  formatTime,
  statusLabel,
  memberStatusClass,
  participantLabel,
  isLeaderId,
  renderMarkdown,
  teamHasRunningMembers,
} from "./shared.js";

const Vue = window.Vue;

export function createTeamViews(ctx) {
  const {
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
  } = ctx;
  const { h } = Vue || {};

  function renderHeader() {
    const loading = local.activeView === "workflow" ? local.workflowsLoading || local.runDetailLoading || !!local.workflowArtifactLoading : local.loading || local.detailLoading;
    const error = local.activeView === "workflow" ? local.workflowsError : local.error;
    return h("div", { class: "teams-head" }, [
      h("div", { class: "teams-title-block" }, [
        h("div", { class: "modal-title" }, requestMessage("teamsTitle")),
        h("div", { class: "teams-sub" }, requestMessage("teamsSubtitle")),
        h("div", { class: "team-tabs", role: "tablist", "aria-label": requestMessage("teamsViews") }, [
          h("button", {
            class: local.activeView === "teams" ? "team-tab active" : "team-tab",
            type: "button",
            role: "tab",
            "aria-selected": local.activeView === "teams" ? "true" : "false",
            onClick: () => switchView("teams"),
          }, requestMessage("teamMembers")),
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
          onClick: () => loading ? cancelReads() : local.activeView === "workflow"
            ? refreshWorkflows({ keepSelection: true }) : refresh(),
        }, requestMessage(loading ? "cancel" : error ? "retry" : "refresh")),
        h("button", {
          class: "teams-close",
          type: "button",
          title: requestMessage("close"), "aria-label": requestMessage("close"),
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
      return h("div", { class: "teams-empty", role: local.error ? "alert" : "status" },
        local.error || requestMessage(local.loading ? "loading" : "teamsEmpty"));
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
      member.execution?.cancel_requested && member.execution?.exit_acknowledged === false
        ? h("div", { class: "team-member-intro", role: "status" }, requestMessage("cancelPending")) : null,
      member.quality_review && ["rework", "escalate"].includes(member.quality_review.decision)
        ? h("div", { class: "team-plan-review-blocked", role: "status" }, [
          requestMessage("reviewBlocked"),
          h("ul", null, [...(member.quality_review.issues || []), ...(member.quality_review.missing_evidence || [])].map(item => h("li", null, String(item)))),
        ]) : null,
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
    const ticket = api.beginAction(`plan:${plan.id}`);
    if (!ticket) return;
    const selected = local.selected;
    const selection = local.teamSelection;
    local.teamPlanActing = `${plan.id}:${action}`;
    renderPanel();
    try {
      const suffix = action === "workflow-draft" ? "workflow-draft" : `actions/${action}`;
      const data = await fetchJson(`/api/session/${encodeURIComponent(state.SID)}/team-plans/${encodeURIComponent(plan.id)}/${suffix}`, {
        method: "POST", body: { created_by: "teams_panel" },
      });
      if (!ticket.isCurrent()) return;
      const plans = await fetchTeamPlans(selected);
      if (!ticket.isCurrent()) return;
      if (local.selected === selected && local.teamSelection === selection) local.teamPlans = plans.plans || [];
      if (action === "workflow-draft") {
        await refreshWorkflows({ silent: true, keepSelection: true });
        if (ticket.isCurrent()) window.BAA.ui?.toast?.(`已创建 Workflow 草稿：${data.workflow?.name || plan.id}`, "ok");
      }
    } catch (error) {
      if (ticket.isCurrent() && !isSilentRequestError(error)) {
        local.error = requestErrorMessage(error);
        window.BAA.ui?.toast?.(local.error, "err");
      }
    } finally {
      if (ticket.isCurrent()) { local.teamPlanActing = ""; renderPanel(); }
      ticket.finish();
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
    const usedTokens = Number(budget.used_tokens || inputTokens + outputTokens);
    const maxTokens = Number(budget.max_total_tokens || plan?.max_total_tokens || 0);
    const toolCalls = Number(budget.tool_calls || 0);
    const jobs = Number(budget.child_job_count || 0);
    const cap = maxTokens
      ? ` · Token ${usedTokens.toLocaleString()} / ${maxTokens.toLocaleString()}${budget.exhausted ? "（已达上限）" : ""}`
      : "";
    if (!inputTokens && !outputTokens && !toolCalls && !jobs) {
      return maxTokens ? `等待成员执行用量 · Token 上限 ${maxTokens.toLocaleString()}` : "等待成员执行用量";
    }
    return `输入 ${inputTokens.toLocaleString()} · 输出 ${outputTokens.toLocaleString()}${cap} · ${toolCalls} 次工具 · ${jobs} 个子 Job · 成本未配置`;
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
      plan.review_status === "blocked" || plan.status === "needs_review" ? h("div", { class: "team-plan-review-blocked", role: "status" }, [
        requestMessage("reviewBlocked"), plan.review_summary ? `：${plan.review_summary}` : "",
        ...(plan.quality_review?.missing_evidence || []).map(item => h("p", null, String(item))),
      ]) : null,
      h("div", { class: "team-plan-tasks" }, (plan.tasks || []).map(task => h("div", { class: "team-plan-task", key: task.id }, [
        h("span", { class: `team-plan-task-dot ${task.status}` }),
        h("div", null, [
          h("strong", null, task.title || task.id),
          h("small", null, `${task.member_name}${task.profile?.key && task.profile.key !== "inherit" ? ` · ${task.profile.name || task.profile.key}` : ""}${task.depends_on?.length ? ` · 依赖 ${task.depends_on.join(", ")}` : ""}`),
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
    if ((local.loading || local.detailLoading) && !local.team) return h("div", { class: "teams-empty", role: "status" }, requestMessage("loading"));
    if (local.error && !local.teams.length) return h("div", { class: "teams-error", role: "alert" }, local.error);
    if (!local.team) return h("div", { class: "teams-empty", role: "status" }, requestMessage("chooseTeam"));
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


  return {
    renderHeader,
    renderTeamList,
    setParticipant,
    renderToolEvents,
    renderLeaderCard,
    renderMembers,
    renderMessages,
    controlTeamPlan,
    requestTeamPlanExecution,
    requestTeamPlanRevision,
    requestTeamTaskRetry,
    dynamicPlanBudgetText,
    renderDynamicPlans,
    renderDetail,
  };
}
