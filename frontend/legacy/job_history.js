// Compatibility Job history, event replay and browser-side sequence de-duplication.
import { toast } from "../core/overlay.js";
import { getUiIsland, uiRegistry } from "../core/ui-registry.js";
import { ensureUiIsland } from "../features/vue-app.js";

  let sid = "";
  let lastSequence = 0;
  let pollTimer = null;
  let replaying = null;
  const seenSequences = new Set();

  function cursorKey(value) { return `baa_job_sequence:${value}`; }

  function loadCursor(value) {
    const parsed = Number(sessionStorage.getItem(cursorKey(value)) || 0);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
  }

  function saveCursor() {
    if (!sid) return;
    sessionStorage.setItem(cursorKey(sid), String(lastSequence));
  }

  async function cancelJob(jobId) {
    const response = await fetch(`/api/session/${encodeURIComponent(sid)}/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || window.t("job.cancel_failed"));
    await replay();
    return data;
  }

  async function clearCompleted() {
    if (!sid) return;
    const accepted = await uiRegistry.confirm?.({
      title: window.t("job.history.clear_confirm_title"),
      message: window.t("job.history.clear_confirm_message"),
      confirmText: window.t("job.history.clear_confirm_action"),
      cancelText: window.t("common.cancel"),
      danger: true,
    });
    if (!accepted) return;
    const response = await fetch(`/api/session/${encodeURIComponent(sid)}/jobs`, { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "清除任务历史失败");
    lastSequence = Number(data.latest_sequence || lastSequence);
    seenSequences.clear();
    saveCursor();
    await refresh();
    toast(`已清除 ${data.deleted || 0} 条任务记录`, "ok");
  }

  function callbacks() {
    return { onCancel: cancelJob, onRefresh: refresh, onClearCompleted: clearCompleted };
  }

  async function fetchJobs(targetSid = sid, limit = 100) {
    if (!targetSid) return [];
    const response = await fetch(`/api/session/${encodeURIComponent(targetSid)}/jobs?limit=${limit}`);
    if (!response.ok) throw new Error(`Job history request failed (${response.status})`);
    const data = await response.json();
    return Array.isArray(data.jobs) ? data.jobs : [];
  }

  async function hasHistory(targetSid) {
    if (!targetSid) return false;
    try { return (await fetchJobs(targetSid, 1)).length > 0; }
    catch (_) { return false; }
  }

  function acceptEvent(event) {
    const sequence = Number(event?.sequence || 0);
    if (sequence > 0) {
      if (seenSequences.has(sequence) || sequence <= lastSequence) return false;
      seenSequences.add(sequence);
      lastSequence = sequence;
      saveCursor();
      if (seenSequences.size > 2000) {
        for (const item of [...seenSequences].slice(0, 1000)) seenSequences.delete(item);
      }
    }
    getUiIsland("jobHistory")?.applyEvent(event);
    return true;
  }

  async function _replay() {
    if (!sid) return;
    let pages = 0;
    while (pages++ < 25) {
      const response = await fetch(
        `/api/session/${encodeURIComponent(sid)}/jobs/events?after_sequence=${lastSequence}&limit=1000`,
      );
      if (!response.ok) throw new Error(`Job event replay failed (${response.status})`);
      const data = await response.json();
      if (Number(data.latest_sequence || 0) < lastSequence) {
        lastSequence = Math.max(0, Number(data.oldest_sequence || 1) - 1);
        seenSequences.clear();
        saveCursor();
        continue;
      }
      if (data.replay_truncated) {
        const oldest = Math.max(1, Number(data.oldest_sequence) || 1);
        lastSequence = oldest - 1;
        seenSequences.clear();
        saveCursor();
        getUiIsland("jobHistory")?.setJobs(await fetchJobs(), callbacks());
        continue;
      }
      for (const event of (data.events || [])) acceptEvent(event);
      const next = Number(data.next_sequence || lastSequence);
      if (next > lastSequence) {
        lastSequence = next;
        saveCursor();
      }
      if (!data.events?.length || lastSequence >= Number(data.latest_sequence || 0)) break;
    }
  }

  async function replay() {
    if (replaying) return replaying;
    replaying = _replay().finally(() => { replaying = null; });
    return replaying;
  }

  async function refresh() {
    const vue = getUiIsland("jobHistory");
    if (!sid || !vue) return;
    vue.setLoading(true);
    vue.setError("");
    try {
      vue.setJobs(await fetchJobs(), callbacks());
      await replay();
    } catch (error) {
      vue.setError(error?.message || String(error));
    } finally {
      vue.setLoading(false);
    }
  }

  function schedulePoll() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => {
      if (!document.hidden && sid) replay().catch(() => {});
    }, 2000);
  }

  async function init(targetSid) {
    sid = targetSid || "";
    lastSequence = loadCursor(sid);
    seenSequences.clear();
    getUiIsland("jobHistory")?.reset();
    await refresh();
    schedulePoll();
  }

  async function switchSession(targetSid) {
    if (targetSid === sid) return refresh();
    return init(targetSid);
  }

  function applyLiveEvent(event) {
    // Live SSE and polling may deliver the same durable event. Sequence is the
    // sole idempotency key, so the second delivery is ignored.
    acceptEvent(event);
  }

  async function open() {
    const vue = await ensureUiIsland("jobHistory");
    vue?.setOpen(true);
    await refresh();
  }

  export {
    init, switchSession, open, refresh, replay, hasHistory, applyLiveEvent, clearCompleted,
  };

  export function getLastSequence() {
    return lastSequence;
  }
