// Team-domain business actions for the teams panel.
import { state } from "../../core/runtime.js";
import { isSilentRequestError, requestErrorMessage } from "../../core/api-client.js";
import { teamHasRunningMembers } from "./shared.js";

export function createTeamActions(ctx) {
  const { local, fetchJson, renderPanel, refresh, api } = ctx;

  async function clearTeamMessages(name) {
    if (!name || local.clearing) return;
    const ticket = api.beginAction(`team:${name}`);
    if (!ticket) return;
    local.clearing = true;
    local.error = "";
    renderPanel();
    try {
      const accepted = await window.BAA.ui?.confirm?.({
        danger: true, title: "清空团队沟通记录？",
        message: `将永久清空团队「${name}」的全部沟通记录，但保留团队和成员。`,
        confirmText: "确认清空", cancelText: "取消",
      });
      if (!accepted || !ticket.isCurrent()) return;
      const result = await fetchJson(`/api/session/${encodeURIComponent(state.SID)}/teams/${encodeURIComponent(name)}/messages`, {
        method: "DELETE", body: { confirm: true },
      });
      if (!ticket.isCurrent()) return;
      window.BAA.ui?.toast?.(`已清空 ${result.cleared_messages || 0} 条团队沟通记录`, "ok");
      await refresh({ silent: true });
    } catch (error) {
      if (ticket.isCurrent() && !isSilentRequestError(error)) local.error = requestErrorMessage(error);
    } finally {
      if (ticket.isCurrent()) { local.clearing = false; renderPanel(); }
      ticket.finish();
    }
  }

  function isEvidenceRetentionError(error) {
    return error?.code === "EVIDENCE_RETENTION_REQUIRED" || String(error?.message || error || "").includes("默认保留");
  }

  async function requestTeamDelete(name, force = false) {
    return fetchJson(`/api/session/${encodeURIComponent(state.SID)}/teams/${encodeURIComponent(name)}`, {
      method: "DELETE", body: { confirm: true, force },
    });
  }

  async function dissolveTeam(name) {
    if (!name || local.deleting) return;
    const team = local.teams.find(item => item.name === name);
    if (teamHasRunningMembers(team)) {
      window.BAA.ui?.toast?.("团队成员仍在执行或排队，暂不能解散", "err");
      return;
    }
    const ticket = api.beginAction(`team:${name}`);
    if (!ticket) return;
    local.deleting = name;
    local.error = "";
    renderPanel();
    try {
      const accepted = await window.BAA.ui?.confirm?.({
        danger: true, title: "解散团队？",
        message: `将先检查团队「${name}」是否含有委派结果、错误或质量复核证据；如有证据会要求二次确认后才强制删除。`,
        confirmText: "继续检查", cancelText: "取消",
      });
      if (!accepted || !ticket.isCurrent()) return;
      try { await requestTeamDelete(name, false); }
      catch (error) {
        if (!ticket.isCurrent()) return;
        if (!isEvidenceRetentionError(error)) throw error;
        const forced = await window.BAA.ui?.confirm?.({
          danger: true, title: "团队含复盘证据，确认强制解散？",
          message: `团队「${name}」已有委派结果、错误或质量复核记录。建议先查看 team_status 复盘；若确认不再需要这些证据，可强制删除。`,
          confirmText: "强制解散", cancelText: "保留团队",
        });
        if (!forced || !ticket.isCurrent()) return;
        await requestTeamDelete(name, true);
      }
      if (!ticket.isCurrent()) return;
      if (local.selected === name) { local.selected = ""; local.selectedParticipant = "leader"; local.team = null; }
      window.BAA.ui?.toast?.(`团队「${name}」已解散`, "ok");
      await refresh({ silent: true });
    } catch (error) {
      if (ticket.isCurrent() && !isSilentRequestError(error)) {
        local.error = requestErrorMessage(error);
        window.BAA.ui?.toast?.(local.error, "err");
      }
    } finally {
      if (ticket.isCurrent()) { local.deleting = ""; renderPanel(); }
      ticket.finish();
    }
  }

  return { clearTeamMessages, isEvidenceRetentionError, requestTeamDelete, dissolveTeam };
}
