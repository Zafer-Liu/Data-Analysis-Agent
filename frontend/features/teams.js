// Teams panel for session-scoped analyst teams and communication history.
import { state } from "../core/runtime.js";
import { renderMd } from "../legacy/markdown.js";

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
    isOpen: false,
    pollTimer: null,
    clearing: false,
    deleting: "",
  };

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

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  async function fetchTeams() {
    return fetchJson(`/api/session/${state.SID}/teams`);
  }

  async function fetchTeam(name) {
    return fetchJson(`/api/session/${state.SID}/teams/${encodeURIComponent(name)}`);
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
      message: `将永久删除团队「${name}」、全部成员定义和沟通记录。此操作不可撤销。`,
      confirmText: "确认解散",
      cancelText: "取消",
    });
    if (!accepted) return;
    local.deleting = name;
    local.error = "";
    renderPanel();
    try {
      await fetchJson(
        `/api/session/${state.SID}/teams/${encodeURIComponent(name)}`,
        {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: true }),
        },
      );
      if (local.selected === name) {
        local.selected = "";
        local.selectedParticipant = "leader";
        local.team = null;
      }
      window.BAA.ui?.toast?.(`团队「${name}」已解散`, "ok");
      await refresh({ silent: true });
    } catch (error) {
      local.error = String(error.message || error);
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
        h("div", null, [
          h("div", { class: "modal-title" }, "团队"),
          h("div", { class: "teams-sub" }, "查看团队成员介绍和成员响应。"),
        ]),
        h("div", { class: "teams-actions" }, [
          h("button", {
            class: "btn-sm btn-sm-ghost",
            type: "button",
            disabled: local.loading,
            onClick: () => refresh(),
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
        h("div", { class: "team-section-title" }, "成员"),
        renderMembers(),
        h("div", { class: "team-section-title" }, `${participantLabel(local.selectedParticipant)} 响应`),
        renderMessages(),
      ]);
    }

    render(h("div", { class: "teams-panel" }, [
      renderHeader(),
      local.error && local.teams.length
        ? h("div", { class: "teams-inline-error" }, local.error)
        : null,
      h("div", { class: "teams-grid" }, [
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
    if (!local.isOpen || !hasRunningMembers()) return;
    local.pollTimer = setTimeout(() => {
      local.pollTimer = null;
      refresh({ silent: true }).catch(() => {});
    }, 2500);
  }

  async function selectTeam(name) {
    if (!name) return;
    if (local.selected !== name) local.selectedParticipant = "leader";
    local.selected = name;
    local.error = "";
    renderPanel();
    try {
      const data = await fetchTeam(name);
      local.team = data.team || null;
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
        const status = await fetchTeam(local.selected);
        local.team = status.team || null;
        const memberNames = new Set((local.team?.members || []).map(member => member.name));
        if (!isLeaderId(local.selectedParticipant) && !memberNames.has(local.selectedParticipant)) {
          local.selectedParticipant = "leader";
        }
      } else {
        local.team = null;
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
    await refresh();
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
    isOpen: () => local.isOpen,
    isAvailable: () => !!hasVue,
});
