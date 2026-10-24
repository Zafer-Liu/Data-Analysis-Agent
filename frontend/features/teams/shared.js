// Shared pure helpers for the teams panel (no local/renderPanel deps).
import { renderMd } from "../../legacy/markdown.js";

export function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

export function statusLabel(status) {
  const map = {
    idle: "空闲",
    queued: "待处理",
    running: "运行中",
    completed: "已完成",
    failed: "失败",
  };
  return map[status] || status || "未知";
}

export function memberStatusClass(status) {
  return `team-status team-status-${status || "unknown"}`;
}

export function participantLabel(id) {
  if (id === "leader" || id === "lead") return "Leader";
  return id || "成员";
}

export function isLeaderId(id) {
  return id === "leader" || id === "lead";
}

export function renderMarkdown(text) {
  return renderMd(text || "");
}

export function workflowStatusLabel(status) {
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

export function workflowStatusClass(status) {
  return `workflow-status workflow-status-${status || "unknown"}`;
}

export function workflowEdgeLabel(type) {
  const map = {
    auto: "自动",
    approval: "审批",
    retry_loop: "返工",
  };
  return map[type] || type || "边";
}

export function workflowEdgeClass(type) {
  return `workflow-dag-edge-chip workflow-dag-edge-${type || "auto"}`;
}

export function latestNodeRunsById(nodes) {
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

export function workflowDagLevels(graph) {
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

export function isPendingApprovalNode(detail, nodeId) {
  return (detail?.approvals || []).some(
    approval => approval.status === "pending" && approval.node_id === nodeId,
  );
}

export function isRetryTarget(graph, nodeId) {
  return (graph?.edges || []).some(
    edge => edge.type === "retry_loop" && edge.to_node === nodeId,
  );
}

export function isWorkflowActive(status) {
  return ["created", "running", "waiting_approval", "paused", "canceling"].includes(status);
}

export function teamHasRunningMembers(team) {
  return (team?.members || []).some(
    member => member.status === "running" || member.status === "queued"
  );
}
