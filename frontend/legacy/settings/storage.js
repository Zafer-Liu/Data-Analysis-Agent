// Lifecycle / storage domain (server storage stats, trash bins, browser storage).
//
// Extracted verbatim from app_settings.js (F5 follow-up, Lifecycle/Storage domain).
// Pure file move + ctx injection: function bodies carry over unchanged, only
// bare `uiState` / `draw` references resolve via per-function ctx aliases.
// No HTML / CSS / fetch URL / rendering logic changes.
//
// Cross-domain deps: ctx.hooks (Hook history clear, from the Hooks domain).
// The `baa:local-storage-changed` listener moves here from app_settings init().

import { getBrowserStorageHealth, isBrowserLocalMode } from "../../core/local-data-store.js";
import { _renderPanelHead } from "./shared.js";

/**
 * @param {{uiState: object, draw: () => void, appState: object, state: object, root: HTMLElement|null, hooks: object}} ctx
 */
export function createStorageDomain(ctx) {
  const Vue = window.Vue;

  async function loadBrowserStorage() {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.browserStorageLoading) return;
    uiState.browserStorageLoading = true;
    uiState.browserStorageError = "";
    draw();
    try {
      uiState.browserStorage = await getBrowserStorageHealth();
    } catch (error) {
      uiState.browserStorage = null;
      uiState.browserStorageError = error.message || String(error);
    } finally {
      uiState.browserStorageLoading = false;
      draw();
    }
  }

  function openBrowserStorageManager() {
    const open = globalThis.BAA?.overlay?.openOverlay || globalThis.openOverlay;
    if (typeof open === "function") open("ov-local-storage");
  }

  function loadStorage() {
    return isBrowserLocalMode() ? loadBrowserStorage() : loadLifecycle();
  }

  async function loadLifecycle() {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.lifecycleLoading) return;
    uiState.lifecycleLoading = true;
    uiState.lifecycleStatus = "正在读取存储信息…";
    draw();
    try {
      const [settingsResponse, reportResponse, trashResponse, artifactTrashResponse, uploadTrashResponse, memoryTrashResponse, previewResponse, referencesResponse, uploadsResponse, workspaceResponse, auditResponse, hookHistoryResponse] = await Promise.all([
        fetch("/api/lifecycle/settings"),
        fetch("/api/lifecycle/report"),
        fetch("/api/lifecycle/session-trash"),
        fetch("/api/lifecycle/artifact-trash"),
        fetch("/api/lifecycle/upload-trash"),
        fetch("/api/lifecycle/memory-trash"),
        fetch("/api/lifecycle/artifacts/preview"),
        fetch("/api/lifecycle/artifacts/references/preview"),
        fetch("/api/lifecycle/uploads/preview"),
        fetch("/api/lifecycle/workspaces/preview"),
        fetch("/api/lifecycle/audit?limit=50"),
        fetch("/api/hooks/history?limit=50"),
      ]);
      const settingsData = await settingsResponse.json();
      const reportData = await reportResponse.json();
      const trashData = await trashResponse.json();
      const artifactTrashData = await artifactTrashResponse.json();
      const uploadTrashData = await uploadTrashResponse.json();
      const memoryTrashData = await memoryTrashResponse.json();
      const previewData = await previewResponse.json();
      const referencesData = await referencesResponse.json();
      const uploadsData = await uploadsResponse.json();
      const workspaceData = await workspaceResponse.json();
      const auditData = await auditResponse.json();
      const hookHistoryData = await hookHistoryResponse.json().catch(() => ({}));
      if (!settingsResponse.ok || !settingsData.ok) throw new Error(settingsData.error || "读取生命周期设置失败");
      if (!reportResponse.ok || !reportData.ok) throw new Error(reportData.error || "读取存储统计失败");
      if (!trashResponse.ok || !trashData.ok) throw new Error(trashData.error || "读取已归档对话失败");
      if (!artifactTrashResponse.ok || !artifactTrashData.ok) throw new Error(artifactTrashData.error || "读取产物回收站失败");
      if (!uploadTrashResponse.ok || !uploadTrashData.ok) throw new Error(uploadTrashData.error || "读取上传回收站失败");
      if (!memoryTrashResponse.ok || !memoryTrashData.ok) throw new Error(memoryTrashData.error || "读取记忆回收站失败");
      if (!previewResponse.ok || !previewData.ok) throw new Error(previewData.error || "读取产物扫描失败");
      if (!referencesResponse.ok || !referencesData.ok) throw new Error(referencesData.error || "读取产物引用失败");
      if (!uploadsResponse.ok || !uploadsData.ok) throw new Error(uploadsData.error || "读取上传分类失败");
      if (!workspaceResponse.ok || !workspaceData.ok) throw new Error(workspaceData.error || "读取工作区存储失败");
      if (!auditResponse.ok || !auditData.ok) throw new Error(auditData.error || "读取生命周期审计失败");
      uiState.lifecycleRetentionPreset = settingsData.settings?.retention_preset || uiState.lifecycleRetentionPreset;
      uiState.lifecycleRetentionCustomDays = settingsData.settings?.retention_custom_days ?? uiState.lifecycleRetentionCustomDays;
      uiState.lifecycleReport = reportData.report;
      uiState.lifecycleTrash = trashData.items || [];
      uiState.lifecycleArtifactTrash = artifactTrashData.items || [];
      uiState.lifecycleUploadTrash = uploadTrashData.items || [];
      uiState.lifecycleMemoryTrash = memoryTrashData.items || [];
      uiState.lifecyclePreview = previewData.preview || null;
      uiState.lifecycleReferencePreview = referencesData.preview || null;
      uiState.lifecycleUploadsPreview = uploadsData.preview || null;
      uiState.lifecycleWorkspacePreview = workspaceData.preview || null;
      uiState.lifecycleAudit = auditData.items || [];
      uiState.lifecycleHookHistory = hookHistoryResponse.ok && hookHistoryData.ok ? (hookHistoryData.items || []) : [];
      uiState.lifecycleStatus = "";
    } catch (error) {
      uiState.lifecycleStatus = `读取失败：${error.message || error}`;
    } finally {
      uiState.lifecycleLoading = false;
      draw();
    }
  }

  function lifecycleRetentionDaysValue() {
    const uiState = ctx.uiState;
    if (!uiState) return 30;
    if (uiState.lifecycleRetentionPreset === "forever") return null;
    if (["7", "14"].includes(uiState.lifecycleRetentionPreset)) {
      return Number(uiState.lifecycleRetentionPreset);
    }
    return Number(uiState.lifecycleRetentionCustomDays);
  }

  async function saveLifecycleSettings() {
    const uiState = ctx.uiState;
    if (!uiState) return;
    const response = await fetch("/api/lifecycle/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        retention_preset: uiState.lifecycleRetentionPreset,
        retention_custom_days: uiState.lifecycleRetentionCustomDays,
      }),
    });
    const data = await parseLifecycleResponse(response, "保存生命周期设置失败");
    uiState.lifecycleRetentionPreset = data.settings.retention_preset;
    uiState.lifecycleRetentionCustomDays = data.settings.retention_custom_days;
  }

  async function setLifecycleRetentionPreset(value) {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState) return;
    uiState.lifecycleRetentionPreset = value;
    uiState.lifecycleStatus = "正在保存保留策略…";
    draw();
    try {
      await saveLifecycleSettings();
      uiState.lifecycleStatus = "保留策略已保存。";
    } catch (error) {
      uiState.lifecycleStatus = `保存失败：${error.message || error}`;
    } finally {
      draw();
    }
  }

  async function saveLifecycleSettingsFromUi() {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState) return;
    uiState.lifecycleStatus = "正在保存保留策略…";
    draw();
    try {
      await saveLifecycleSettings();
      uiState.lifecycleStatus = "保留策略已保存。";
    } catch (error) {
      uiState.lifecycleStatus = `保存失败：${error.message || error}`;
    } finally {
      draw();
    }
  }

  async function restoreLifecycle(trashId) {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState) return;
    uiState.lifecycleStatus = "正在恢复会话…";
    draw();
    try {
      const response = await fetch(`/api/lifecycle/session-trash/${encodeURIComponent(trashId)}/restore`, { method: "POST" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "恢复失败");
      uiState.lifecycleStatus = `已恢复 ${data.summary.restored.length} 个会话文件。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `恢复失败：${error.message || error}`;
      draw();
    }
  }

  async function parseLifecycleResponse(response, fallbackMessage) {
    const text = await response.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      if (response.status === 404) {
        throw new Error("生命周期接口未加载，请重启应用后再试");
      }
      throw new Error(fallbackMessage || `接口返回异常：HTTP ${response.status}`);
    }
    if (!response.ok || !data.ok) {
      throw new Error(data.error || fallbackMessage || `请求失败：HTTP ${response.status}`);
    }
    return data;
  }

  async function recycleUnregisteredArtifact(item) {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.lifecycleRecyclingKey) return;
    const filename = item.filename || "未命名产物";
    const accepted = await window.BAA.ui?.confirm?.({
      title: "移入产物回收站？",
      message: `将把历史产物「${filename}」移入受控回收站。它不会立即物理删除，但历史会话或报告里引用它时可能无法打开。`,
      danger: true,
    });
    if (!accepted) return;
    const key = `${item.type || ""}:${item.relative_path || filename}`;
    uiState.lifecycleRecyclingKey = key;
    uiState.lifecycleStatus = "正在移动历史产物…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/artifacts/unregistered/recycle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: item.type, relative_path: item.relative_path }),
      });
      const data = await parseLifecycleResponse(response, "移动历史产物失败");
      uiState.lifecycleStatus = `已将 ${data.summary.filename || filename} 移入产物回收站。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `移动失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleRecyclingKey = "";
      draw();
    }
  }

  async function recycleRegisteredArtifact(item) {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.lifecycleRecyclingKey) return;
    const filename = item.filename || item.id || "已登记产物";
    const accepted = await window.BAA.ui?.confirm?.({
      title: "回收已登记产物？",
      message: `将把「${filename}」移入产物回收站。当前只允许未发现引用的 chart/export/report 候选，且不会立即物理删除。`,
      danger: true,
    });
    if (!accepted) return;
    const key = `registered:${item.id || filename}`;
    uiState.lifecycleRecyclingKey = key;
    uiState.lifecycleStatus = "正在移动已登记产物…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/artifacts/registered/recycle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ artifact_id: item.id }),
      });
      const data = await parseLifecycleResponse(response, "移动已登记产物失败");
      uiState.lifecycleStatus = `已将 ${data.summary.filename || filename} 移入产物回收站。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `移动失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleRecyclingKey = "";
      draw();
    }
  }

  async function recycleUploadCandidate(item) {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.lifecycleRecyclingKey) return;
    const filename = item.filename || "上传文件";
    const category = item.category || "unknown_uploads";
    const accepted = await window.BAA.ui?.confirm?.({
      title: "移入上传回收站？",
      message: `将把「${filename}」移入上传回收站。仅允许未知上传或 Excel 解析缓存，知识库与已登记上传不会被处理。`,
      danger: true,
    });
    if (!accepted) return;
    const key = `upload:${item.relative_path || filename}`;
    uiState.lifecycleRecyclingKey = key;
    uiState.lifecycleStatus = "正在移动上传文件…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/uploads/recycle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category, relative_path: item.relative_path }),
      });
      const data = await parseLifecycleResponse(response, "移动上传文件失败");
      uiState.lifecycleStatus = `已将 ${data.summary.filename || filename} 移入上传回收站。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `移动失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleRecyclingKey = "";
      draw();
    }
  }

  async function restoreArtifactTrash(trashId) {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.lifecycleArtifactBusyKey) return;
    uiState.lifecycleArtifactBusyKey = trashId;
    uiState.lifecycleStatus = "正在恢复产物…";
    draw();
    try {
      const response = await fetch(`/api/lifecycle/artifact-trash/${encodeURIComponent(trashId)}/restore`, { method: "POST" });
      const data = await parseLifecycleResponse(response, "恢复产物失败");
      uiState.lifecycleStatus = `已恢复 ${data.summary.restored.length} 个产物。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `恢复产物失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleArtifactBusyKey = "";
      draw();
    }
  }

  async function reclaimArtifactTrash() {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.lifecycleArtifactReclaiming) return;
    const retentionDays = lifecycleRetentionDaysValue();
    if (retentionDays === null) {
      uiState.lifecycleStatus = "当前选择永久保留，产物回收站不会过期清理。";
      draw();
      return;
    }
    if (!Number.isInteger(retentionDays) || retentionDays < 0 || retentionDays > 3650) {
      uiState.lifecycleStatus = "自定义保留天数必须是 0 到 3650 的整数";
      draw();
      return;
    }
    const accepted = await window.BAA.ui?.confirm?.({
      title: "永久清理过期产物",
      message: `将永久清理已在产物回收站保留超过 ${retentionDays} 天的文件。此操作不可恢复。`,
      danger: true,
    });
    if (!accepted) return;
    uiState.lifecycleArtifactReclaiming = true;
    uiState.lifecycleStatus = "正在清理产物回收站…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/artifact-trash/reclaim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ retention_days: retentionDays }),
      });
      const data = await parseLifecycleResponse(response, "清理产物回收站失败");
      uiState.lifecycleStatus = `已清理 ${data.summary.groups || 0} 组、${data.summary.files || 0} 个产物文件。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `清理产物失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleArtifactReclaiming = false;
      draw();
    }
  }

  async function clearSessionTrash() {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.lifecycleReclaiming) return;
    const accepted = await window.BAA.ui?.confirm?.({
      title: "永久删除全部已归档对话？",
      message: "将永久删除所有已归档对话及其文件（会话、图表、导出、上传）。此操作不可恢复。",
      danger: true,
    });
    if (!accepted) return;
    uiState.lifecycleReclaiming = true;
    uiState.lifecycleStatus = "正在删除已归档对话…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/session-trash/reclaim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ retention_days: 0 }),
      });
      const data = await parseLifecycleResponse(response, "删除已归档对话失败");
      uiState.lifecycleStatus = `已清空 ${data.summary.groups || 0} 组、${data.summary.files || 0} 个会话文件。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `清空会话失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleReclaiming = false;
      draw();
    }
  }

  async function clearArtifactTrash() {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.lifecycleArtifactReclaiming) return;
    const accepted = await window.BAA.ui?.confirm?.({
      title: "清空产物回收站？",
      message: "将永久删除产物回收站中的所有项目，并同步清理已登记产物记录。此操作不可恢复。",
      danger: true,
    });
    if (!accepted) return;
    uiState.lifecycleArtifactReclaiming = true;
    uiState.lifecycleStatus = "正在清空产物回收站…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/artifact-trash/reclaim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ retention_days: 0 }),
      });
      const data = await parseLifecycleResponse(response, "清空产物回收站失败");
      uiState.lifecycleStatus = `已清空 ${data.summary.groups || 0} 组、${data.summary.files || 0} 个产物文件。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `清空产物失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleArtifactReclaiming = false;
      draw();
    }
  }

  async function restoreUploadTrash(trashId) {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.lifecycleUploadBusyKey) return;
    uiState.lifecycleUploadBusyKey = trashId;
    uiState.lifecycleStatus = "正在恢复上传文件…";
    draw();
    try {
      const response = await fetch(`/api/lifecycle/upload-trash/${encodeURIComponent(trashId)}/restore`, { method: "POST" });
      const data = await parseLifecycleResponse(response, "恢复上传文件失败");
      uiState.lifecycleStatus = `已恢复 ${data.summary.restored.length} 个上传文件。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `恢复上传失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleUploadBusyKey = "";
      draw();
    }
  }

  async function clearUploadTrash() {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.lifecycleUploadReclaiming) return;
    const accepted = await window.BAA.ui?.confirm?.({
      title: "清空上传回收站？",
      message: "将永久删除上传回收站中的所有项目。此操作不可恢复。",
      danger: true,
    });
    if (!accepted) return;
    uiState.lifecycleUploadReclaiming = true;
    uiState.lifecycleStatus = "正在清空上传回收站…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/upload-trash/reclaim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ retention_days: 0 }),
      });
      const data = await parseLifecycleResponse(response, "清空上传回收站失败");
      uiState.lifecycleStatus = `已清空 ${data.summary.groups || 0} 组、${data.summary.files || 0} 个上传文件。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `清空上传失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleUploadReclaiming = false;
      draw();
    }
  }

  async function restoreMemoryTrashFromStorage(trashId) {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.lifecycleMemoryBusyKey) return;
    uiState.lifecycleMemoryBusyKey = trashId;
    uiState.lifecycleStatus = "正在恢复记忆…";
    draw();
    try {
      const response = await fetch(`/api/lifecycle/memory-trash/${encodeURIComponent(trashId)}/restore`, { method: "POST" });
      const data = await parseLifecycleResponse(response, "恢复记忆失败");
      uiState.lifecycleStatus = `已恢复记忆「${data.summary.name || data.summary.restored?.[0] || ""}」。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `恢复记忆失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleMemoryBusyKey = "";
      draw();
    }
  }

  async function clearMemoryTrash() {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.lifecycleMemoryReclaiming) return;
    const accepted = await window.BAA.ui?.confirm?.({
      title: "清空记忆回收站？",
      message: "将永久删除记忆回收站中的所有归档记忆。此操作不可恢复。",
      danger: true,
    });
    if (!accepted) return;
    uiState.lifecycleMemoryReclaiming = true;
    uiState.lifecycleStatus = "正在清空记忆回收站…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/memory-trash/reclaim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ retention_days: 0 }),
      });
      const data = await parseLifecycleResponse(response, "清空记忆回收站失败");
      uiState.lifecycleStatus = `已清空 ${data.summary.groups || 0} 组、${data.summary.files || 0} 个记忆文件。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `清空记忆失败：${error.message || error}`;
      draw();
    } finally {
      uiState.lifecycleMemoryReclaiming = false;
      draw();
    }
  }

  async function pruneMissingArtifacts() {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    if (!uiState || uiState.lifecycleLoading) return;
    const accepted = await window.BAA.ui?.confirm?.({
      title: "清理缺失记录？",
      message: "将移除已登记但文件已不存在的产物登记记录。仅清理登记信息，不影响任何现有文件。",
    });
    if (!accepted) return;
    uiState.lifecycleStatus = "正在清理缺失记录…";
    draw();
    try {
      const response = await fetch("/api/lifecycle/artifacts/prune-missing", { method: "POST" });
      const data = await parseLifecycleResponse(response, "清理缺失记录失败");
      uiState.lifecycleStatus = `已清理 ${data.summary.removed || 0} 条缺失记录。`;
      await loadLifecycle();
    } catch (error) {
      uiState.lifecycleStatus = `清理缺失记录失败：${error.message || error}`;
      draw();
    }
  }

  function formatLifecycleBytes(value) {
    const bytes = Number(value) || 0;
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let size = bytes; let index = -1;
    do { size /= 1024; index += 1; } while (size >= 1024 && index < units.length - 1);
    return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[index]}`;
  }

  const LIFECYCLE_AUDIT_LABELS = {
    session_registered: "会话登记",
    session_soft_deleted: "会话软删除",
    session_trash_reclaimed: "回收站清理",
    session_trash_restored: "会话恢复",
    artifact_registered: "产物登记",
  };

  function lifecycleAuditLabel(event) {
    return LIFECYCLE_AUDIT_LABELS[event] || event || "unknown";
  }

  function lifecycleAuditDetails(item) {
    const details = [];
    if (item.session_id) details.push(`会话 ${item.session_id}`);
    if (item.artifact_id) details.push(`产物 ${item.artifact_id}`);
    if (item.type) details.push(`类型 ${item.type}`);
    if (item.workspace_id) details.push(`Workspace ${item.workspace_id}`);
    if (Number.isFinite(Number(item.size_bytes))) details.push(formatLifecycleBytes(item.size_bytes));
    if (Number.isFinite(Number(item.files))) details.push(`${item.files} 个文件`);
    if (Number.isFinite(Number(item.bytes))) details.push(formatLifecycleBytes(item.bytes));
    if (Number.isFinite(Number(item.groups))) details.push(`${item.groups} 组`);
    if (Number.isFinite(Number(item.retention_days))) details.push(`保留 ${item.retention_days} 天`);
    if (Array.isArray(item.deleted) && item.deleted.length) details.push(`删除 ${item.deleted.length} 项`);
    if (Array.isArray(item.restored) && item.restored.length) details.push(`恢复 ${item.restored.length} 项`);
    if (Array.isArray(item.failed) && item.failed.length) details.push(`失败 ${item.failed.length} 项`);
    return details.join(" · ");
  }

  function renderStorage() {
    const uiState = ctx.uiState;
    const draw = ctx.draw;
    const hooks = ctx.hooks;
    if (isBrowserLocalMode()) return renderBrowserStorage();
    const report = uiState.lifecycleReport || { locations: {}, total_files: 0, total_bytes: 0 };
    const items = uiState.lifecycleTrash || [];
    const artifactTrash = uiState.lifecycleArtifactTrash || [];
    const uploadTrash = uiState.lifecycleUploadTrash || [];
    const memoryTrash = uiState.lifecycleMemoryTrash || [];
    const preview = uiState.lifecyclePreview || { unknown_files: [], unknown_bytes: 0, missing_registered_ids: [] };
    const referencePreview = uiState.lifecycleReferencePreview || { registered: 0, referenced: 0, unreferenced: 0, missing: 0, unreferenced_samples: [], missing_samples: [], reference_sources: 0 };
    const unknownFiles = Array.isArray(preview.unknown_files) ? preview.unknown_files : [];
    const missingRegistered = Array.isArray(preview.missing_registered_ids) ? preview.missing_registered_ids : [];
    const unreferencedRegistered = Array.isArray(referencePreview.unreferenced_samples) ? referencePreview.unreferenced_samples : [];
    const missingRegisteredSamples = Array.isArray(referencePreview.missing_samples) ? referencePreview.missing_samples : [];
    const uploadsPreview = uiState.lifecycleUploadsPreview || { categories: {}, samples: [], cache_samples: [], missing_registered_upload_ids: [] };
    const uploadCategories = uploadsPreview.categories || {};
    const unknownUploadSamples = Array.isArray(uploadsPreview.samples) ? uploadsPreview.samples : [];
    const cacheUploadSamples = Array.isArray(uploadsPreview.cache_samples) ? uploadsPreview.cache_samples : [];
    const workspacePreview = uiState.lifecycleWorkspacePreview || { workspaces: [], total_bytes: 0 };
    const workspaceItems = Array.isArray(workspacePreview.workspaces) ? workspacePreview.workspaces : [];
    const auditAll = Array.isArray(uiState.lifecycleAudit) ? uiState.lifecycleAudit : [];
    const audit = uiState.lifecycleAuditFilter === "all" ? auditAll : auditAll.filter(item => String(item.event || "").includes(uiState.lifecycleAuditFilter));
    const locations = report.locations || {};
    const retentionDays = lifecycleRetentionDaysValue();
    const retentionForever = retentionDays === null;
    const customRetentionInvalid = !retentionForever && (!Number.isInteger(retentionDays) || retentionDays < 0 || retentionDays > 3650);
    const isExpiredTrash = item => {
      if (retentionForever || customRetentionInvalid) return false;
      const deletedAt = Date.parse(item.deleted_at || "");
      if (!Number.isFinite(deletedAt)) return false;
      return Date.now() - deletedAt >= retentionDays * 86400000;
    };
    const expiredArtifactTrash = artifactTrash.filter(isExpiredTrash);
    const expiredArtifactTrashBytes = expiredArtifactTrash.reduce((total, item) => total + Number(item.bytes || 0), 0);
    const trashBytes = items.reduce((total, item) => total + Number(item.bytes || 0), 0);
    const artifactTrashBytes = artifactTrash.reduce((total, item) => total + Number(item.bytes || 0), 0);
    const uploadTrashBytes = uploadTrash.reduce((total, item) => total + Number(item.bytes || 0), 0);
    const memoryTrashBytes = memoryTrash.reduce((total, item) => total + Number(item.bytes || 0), 0);
    const recycleTotalBytes = artifactTrashBytes + uploadTrashBytes + memoryTrashBytes;
    const metricCards = [
      { label: "目录总占用", value: formatLifecycleBytes(report.total_bytes), note: `${report.total_files || 0} 个文件 · 不是可清理量`, tone: "primary" },
      { label: "已归档对话", value: formatLifecycleBytes(trashBytes), note: `${items.length} 组 · 手动管理，不受保留策略影响`, tone: items.length ? "warn" : "muted" },
      { label: "回收站总量", value: formatLifecycleBytes(recycleTotalBytes), note: `产物 ${artifactTrash.length} · 上传 ${uploadTrash.length} · 记忆 ${memoryTrash.length} 项`, tone: recycleTotalBytes ? "warn" : "muted" },
    ];

    const section = (title, description, actions, children, extraClass = "") => Vue.h("section", { class: `lifecycle-card ${extraClass}` }, [
      Vue.h("div", { class: "lifecycle-section-heading" }, [
        Vue.h("div", null, [
          Vue.h("div", { class: "app-settings-section-title" }, title),
          description ? Vue.h("p", { class: "lifecycle-copy lifecycle-card-copy" }, description) : null,
        ]),
        actions ? Vue.h("div", { class: "lifecycle-preview-actions" }, Array.isArray(actions) ? actions : [actions]) : null,
      ]),
      ...(Array.isArray(children) ? children : []),
    ]);

    const previewList = (caption, rows, renderRow, emptyText = "暂无数据") => rows.length
      ? Vue.h("div", { class: "lifecycle-preview-list" }, [
        Vue.h("div", { class: "lifecycle-list-caption" }, caption),
        ...rows.map(renderRow),
      ])
      : Vue.h("div", { class: "lifecycle-empty lifecycle-empty-compact" }, emptyText);

    const uploadCandidateRow = (item, index) => {
      const key = `upload:${item.relative_path || item.filename || index}`;
      const recycling = uiState.lifecycleRecyclingKey === key;
      return Vue.h("div", { class: "lifecycle-preview-item", key }, [
        Vue.h("span", { title: item.relative_path || item.filename || "" }, item.filename || "上传文件"),
        Vue.h("div", { class: "lifecycle-preview-actions" }, [
          Vue.h("small", null, formatLifecycleBytes(item.size_bytes)),
          Vue.h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: recycling || uiState.lifecycleLoading, onClick: () => recycleUploadCandidate(item) }, recycling ? "移动中…" : "删除"),
        ]),
      ]);
    };

    const recycleBinRow = (item, restoreFn, busyKey, labelFallback) => Vue.h("div", { class: "lifecycle-trash-item", key: item.id }, [
      Vue.h("div", null, [
        Vue.h("strong", null, item.filename || item.source_filename || labelFallback),
        Vue.h("small", null, `${item.deleted_at || ""} · ${item.category || item.type || `${item.files || 0} 个`} · ${formatLifecycleBytes(item.bytes)} · ${isExpiredTrash(item) ? "已过期" : "可恢复"}`),
      ]),
      Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: busyKey === item.id, onClick: () => restoreFn(item.id) }, busyKey === item.id ? "恢复中…" : "恢复"),
    ]);

    const recycleBin = (title, rows, clearAction, clearBusy, restoreFn, busyKey, labelFallback, extraActions = []) => section(
      title,
      "可恢复；一键删除会永久清空该回收站。",
      [
        ...extraActions,
        Vue.h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: clearBusy || !rows.length, title: rows.length ? `永久删除${title}全部项目` : `${title}为空`, onClick: clearAction }, "一键删除"),
      ],
      [rows.length ? Vue.h("div", { class: "lifecycle-trash-list" }, rows.map(item => recycleBinRow(item, restoreFn, busyKey, labelFallback))) : Vue.h("div", { class: "lifecycle-empty" }, `${title}为空`)],
      "lifecycle-card-recycle",
    );

    const protectedRows = [
      ...Object.entries(locations).map(([name, value]) => [name, `${value.files || 0} 个 · ${formatLifecycleBytes(value.bytes)}`]),
      ...workspaceItems.map(item => [item.name, item.db_exists ? `${formatLifecycleBytes(item.db_bytes)} · ${item.active_lease_count ? "任务使用中" : "已保护"}` : "未发现 DuckDB 文件"]),
    ];

    const uploadCategoryRows = [
      ["registered_uploads", "已登记上传"],
      ["knowledge", "知识库数据"],
      ["parsed_excel_cache", "Excel 解析缓存"],
      ["unknown_uploads", "未知上传"],
    ];

    const runMode = document.body?.dataset?.runMode || "local";
    const runtimeLabel = runMode === "desktop" ? "桌面端" : "命令行 / 本地端";
    const runtimeCopy = runMode === "desktop"
      ? "数据保存在当前用户的应用数据目录，关闭桌面应用后仍会保留。"
      : "数据保存在启动此服务的本机文件系统；通过 BAA_DATA_DIR 可指定独立数据目录。";

    return Vue.h("section", { class: "app-settings-panel lifecycle-panel lifecycle-redesigned" }, [
      _renderPanelHead("存储", "本地文件分为可回收、需确认、受保护三类；回收站可恢复，永久删除必须手动确认。", [
        Vue.h("button", { class: "btn-sm btn-sm-primary", type: "button", disabled: uiState.lifecycleLoading, onClick: loadLifecycle }, uiState.lifecycleLoading ? "刷新中…" : "刷新统计"),
      ]),

      Vue.h("div", { class: "storage-runtime-card" }, [
        Vue.h("span", { class: "storage-runtime-kicker" }, "当前运行环境"),
        Vue.h("strong", null, runtimeLabel),
        Vue.h("p", null, runtimeCopy),
      ]),

      Vue.h("div", { class: "lifecycle-metric-grid" }, metricCards.map(card => Vue.h("div", { class: `lifecycle-metric-card lifecycle-metric-${card.tone}`, key: card.label }, [
        Vue.h("span", null, card.label),
        Vue.h("strong", null, card.value),
        Vue.h("small", null, card.note),
      ]))),

      section("保留策略", "控制无注册的产物、上传和记忆回收站的过期清理口径；已归档的对话不受此策略影响，只能手动恢复或删除。", [
        Vue.h("label", { class: "lifecycle-retention-field" }, [
          Vue.h("span", null, "保留策略"),
          Vue.h("select", { class: "lifecycle-retention-select", value: uiState.lifecycleRetentionPreset, onChange: event => setLifecycleRetentionPreset(event.target.value) }, [
            Vue.h("option", { value: "7" }, "7 天"),
            Vue.h("option", { value: "14" }, "14 天"),
            Vue.h("option", { value: "forever" }, "永久"),
            Vue.h("option", { value: "custom" }, "自定义"),
          ]),
          uiState.lifecycleRetentionPreset === "custom" ? Vue.h("input", { class: "lifecycle-days", type: "number", min: 0, max: 3650, value: uiState.lifecycleRetentionCustomDays, onInput: event => { uiState.lifecycleRetentionCustomDays = event.target.value; draw(); }, onChange: saveLifecycleSettingsFromUi }) : null,
        ]),
      ], [uiState.lifecycleStatus ? Vue.h("div", { class: "lifecycle-status" }, uiState.lifecycleStatus) : null]),

      section("已归档的对话", "手动归档的对话及其图表、导出、上传等文件保存在这里，可随时恢复；不受保留策略影响，只由你手动恢复或永久删除。", [
        Vue.h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: uiState.lifecycleReclaiming || !items.length, title: items.length ? "永久删除全部已归档对话（不可恢复）" : "暂无已归档对话", onClick: clearSessionTrash }, "永久删除全部"),
      ], [
        items.length
          ? Vue.h("div", { class: "lifecycle-trash-list" }, items.map(item => Vue.h("div", { class: "lifecycle-trash-item", key: item.id }, [
            Vue.h("div", null, [
              Vue.h("strong", null, item.source_filename || "已归档对话"),
              Vue.h("small", null, `${item.deleted_at || ""} · ${item.files} 个文件${item.artifacts ? ` + ${item.artifacts} 个产物` : ""} · ${formatLifecycleBytes(item.bytes)} · 可恢复`),
            ]),
            Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.lifecycleReclaiming, onClick: () => restoreLifecycle(item.id) }, "恢复"),
          ])))
          : Vue.h("div", { class: "lifecycle-empty" }, "暂无已归档的对话"),
      ], "lifecycle-card-recycle"),

      section("回收站", "回收站里的项目可恢复；一键删除会永久清空对应回收站。", null, [
        Vue.h("div", { class: "lifecycle-recycle-grid" }, [
          recycleBin("上传回收站", uploadTrash, clearUploadTrash, uiState.lifecycleUploadReclaiming, restoreUploadTrash, uiState.lifecycleUploadBusyKey, "已回收上传"),
          recycleBin("产物回收站", artifactTrash, clearArtifactTrash, uiState.lifecycleArtifactReclaiming, restoreArtifactTrash, uiState.lifecycleArtifactBusyKey, "已回收产物", [
            Vue.h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: uiState.lifecycleArtifactReclaiming || retentionForever || customRetentionInvalid || !expiredArtifactTrash.length, onClick: reclaimArtifactTrash }, retentionForever ? "永久保留产物" : `清理过期 · ${formatLifecycleBytes(expiredArtifactTrashBytes)}`),
          ]),
          recycleBin("记忆回收站", memoryTrash, clearMemoryTrash, uiState.lifecycleMemoryReclaiming, restoreMemoryTrashFromStorage, uiState.lifecycleMemoryBusyKey, "已归档记忆"),
        ]),
      ]),

      section("高级", "未登记的产物、上传缓存、目录统计与操作审计；日常无需关注。", [
        Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", onClick: () => { uiState.lifecycleAdvancedOpen = !uiState.lifecycleAdvancedOpen; draw(); } }, uiState.lifecycleAdvancedOpen ? "收起高级" : "展开高级"),
      ], uiState.lifecycleAdvancedOpen ? [
        Vue.h("div", { class: "lifecycle-subsection" }, [
          Vue.h("div", { class: "lifecycle-inline-title" }, "Hook 触发记录"),
          Vue.h("p", { class: "lifecycle-copy" }, "Hook 执行日志保存在本地配置目录，最多保留 500 条；可在 Hooks 页查看详情。"),
          Vue.h("div", { class: "lifecycle-preview-actions" }, [
            Vue.h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: !(uiState.lifecycleHookHistory || []).length, onClick: () => hooks.clearHookHistoryFromStorage() }, "清理记录"),
          ]),
        ]),

        Vue.h("div", { class: "lifecycle-two-column" }, [
          section("可清理候选", "这些项目可以手动移入回收站。未知不等于垃圾；删除前请看文件名和来源。", null, [
            Vue.h("div", { class: "lifecycle-subsection" }, [
              Vue.h("div", { class: "lifecycle-inline-title" }, "Uploads"),
              Vue.h("div", { class: "lifecycle-location-list" }, uploadCategoryRows.map(([key, label]) => {
                const value = uploadCategories[key] || { files: 0, bytes: 0 };
                return Vue.h("div", { class: "lifecycle-row", key }, [Vue.h("span", null, label), Vue.h("span", null, `${value.files || 0} 个 · ${formatLifecycleBytes(value.bytes)}`)]);
              })),
              previewList(`未知上传前 ${Math.min(20, unknownUploadSamples.length)} 项`, unknownUploadSamples, uploadCandidateRow, "没有未知上传"),
              previewList(`Excel 解析缓存前 ${Math.min(20, cacheUploadSamples.length)} 项`, cacheUploadSamples, uploadCandidateRow, "没有 Excel 解析缓存"),
            ]),
            Vue.h("div", { class: "lifecycle-subsection" }, [
              Vue.h("div", { class: "lifecycle-inline-title" }, "历史 charts / exports"),
              Vue.h("p", { class: "lifecycle-copy" }, `发现 ${unknownFiles.length} 个未登记历史产物（${formatLifecycleBytes(preview.unknown_bytes)}），已登记但缺失 ${missingRegistered.length} 个。`),
              previewList(`未登记历史产物前 ${Math.min(20, unknownFiles.length)} 项`, unknownFiles.slice(0, 20), (item, index) => {
                const key = `${item.type || "artifact"}:${item.relative_path || item.filename || index}`;
                const recycling = uiState.lifecycleRecyclingKey === key;
                return Vue.h("div", { class: "lifecycle-preview-item", key }, [
                  Vue.h("span", { title: item.relative_path || item.filename || "" }, `${item.type || "unknown"} · ${item.filename || "未命名产物"}`),
                  Vue.h("div", { class: "lifecycle-preview-actions" }, [
                    Vue.h("small", null, formatLifecycleBytes(item.size_bytes)),
                    Vue.h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: recycling || uiState.lifecycleLoading, onClick: () => recycleUnregisteredArtifact(item) }, recycling ? "移动中…" : "删除"),
                  ]),
                ]);
              }, "没有未登记历史产物"),
            ]),
            Vue.h("div", { class: "lifecycle-subsection" }, [
              Vue.h("div", { class: "lifecycle-inline-title" }, "已登记产物引用"),
              Vue.h("p", { class: "lifecycle-copy" }, `扫描 ${referencePreview.reference_sources || 0} 个会话文件：已登记 ${referencePreview.registered || 0} 个，发现引用 ${referencePreview.referenced || 0} 个，未发现引用 ${referencePreview.unreferenced || 0} 个，文件缺失 ${referencePreview.missing || 0} 个。`),
              previewList(`未发现引用前 ${Math.min(20, unreferencedRegistered.length)} 项`, unreferencedRegistered, (item, index) => {
                const key = `registered:${item.id || index}`;
                const recycling = uiState.lifecycleRecyclingKey === key;
                return Vue.h("div", { class: "lifecycle-preview-item", key }, [
                  Vue.h("span", { title: item.id || item.filename || "" }, `${item.type || "artifact"} · ${item.filename || item.id || "未命名产物"}`),
                  Vue.h("div", { class: "lifecycle-preview-actions" }, [
                    Vue.h("small", null, formatLifecycleBytes(item.size_bytes)),
                    ["chart", "export", "report"].includes(item.type) ? Vue.h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: recycling || uiState.lifecycleLoading, onClick: () => recycleRegisteredArtifact(item) }, recycling ? "移动中…" : "回收") : null,
                  ]),
                ]);
              }, "没有未发现引用的已登记产物"),
              missingRegisteredSamples.length ? Vue.h("div", { class: "lifecycle-preview-actions" }, [
                Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.lifecycleLoading, onClick: pruneMissingArtifacts }, `清理缺失记录（${missingRegisteredSamples.length}）`),
              ]) : null,
              previewList(`缺失记录前 ${Math.min(20, missingRegisteredSamples.length)} 项`, missingRegisteredSamples, (item, index) => Vue.h("div", { class: "lifecycle-preview-item", key: `${item.id || index}` }, [
                Vue.h("span", { title: item.id || item.filename || "" }, `${item.type || "artifact"} · ${item.filename || item.id || "缺失产物"}`),
                Vue.h("small", null, formatLifecycleBytes(item.size_bytes)),
              ]), "没有缺失的已登记产物"),
            ]),
          ]),

          section("数据统计", "展示各类本地数据的占用情况。知识库请通过知识库管理删除，Workspace 请通过工作区流程删除。", Vue.h("span", { class: "lifecycle-badge lifecycle-badge-protected" }, "分类统计"), [
            Vue.h("div", { class: "lifecycle-location-list" }, protectedRows.map(([label, value]) => Vue.h("div", { class: "lifecycle-row", key: label }, [Vue.h("span", null, label), Vue.h("span", null, value)]))),
          ], "lifecycle-card-stats"),
        ]),

        section("生命周期记录", "记录最近的登记、回收、恢复和清理操作；API 会隐藏本地绝对路径。", [
          Vue.h("select", { class: "lifecycle-retention-select", value: uiState.lifecycleAuditFilter, onChange: event => { uiState.lifecycleAuditFilter = event.target.value; draw(); } }, [
            Vue.h("option", { value: "all" }, "全部"),
            Vue.h("option", { value: "session" }, "会话"),
            Vue.h("option", { value: "artifact" }, "产物"),
            Vue.h("option", { value: "reclaim" }, "清理"),
          ]),
          Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.lifecycleLoading, onClick: loadLifecycle }, "刷新"),
        ], [audit.length ? Vue.h("div", { class: "lifecycle-audit-list" }, audit.map((item, index) => {
          const detail = lifecycleAuditDetails(item);
          return Vue.h("div", { class: "lifecycle-audit-item", key: `${item.at || ""}-${item.event || index}` }, [
            Vue.h("div", { class: "lifecycle-audit-main" }, [
              Vue.h("span", null, lifecycleAuditLabel(item.event)),
              detail ? Vue.h("em", null, detail) : null,
            ]),
            Vue.h("small", null, item.at || ""),
          ]);
        })) : Vue.h("div", { class: "lifecycle-empty" }, "暂无生命周期记录")]),
      ] : []),
    ]);
  }

  function renderBrowserStorage() {
    const uiState = ctx.uiState;
    const health = uiState.browserStorage;
    const preference = health?.preference || {};
    const mode = preference.mode === "directory" && preference.permission === "granted"
      ? `${preference.directoryName || "Sage"} 文件夹`
      : "IndexedDB 浏览器数据库";
    const modeNote = preference.mode === "directory" && preference.permission !== "granted"
      ? "文件夹授权已失效，当前自动回退到 IndexedDB。"
      : preference.mode === "directory"
        ? "对话和上传文件同时写入授权文件夹，并保留浏览器数据库副本。"
        : "数据只属于当前浏览器、当前网站和当前登录账户。";
    const persistedLabel = health?.persisted ? "已获持久保护" : "可能被浏览器清理";
    const persistedNote = health?.persisted
      ? "浏览器已承诺不会因空间压力自动回收这些数据。"
      : "建议下载备份；无痕模式、清理站点数据或卸载浏览器仍会删除数据。";
    const usage = health?.usage == null ? "暂不可读取" : formatLifecycleBytes(health.usage);
    const quota = health?.quota == null ? "" : ` / ${formatLifecycleBytes(health.quota)}`;

    return Vue.h("section", { class: "app-settings-panel lifecycle-panel lifecycle-redesigned" }, [
      _renderPanelHead("存储", "网页版的对话与上传原件保存在你的浏览器或授权文件夹中，不长期写入云端服务器。", [
        Vue.h("button", { class: "btn-sm btn-sm-primary", type: "button", onClick: openBrowserStorageManager }, "管理保存位置"),
      ]),
      Vue.h("div", { class: "storage-runtime-card storage-runtime-web" }, [
        Vue.h("span", { class: "storage-runtime-kicker" }, "当前运行环境"),
        Vue.h("strong", null, "网页版 · 浏览器本地存储"),
        Vue.h("p", null, "登录账户只用于隔离数据；浏览器不会把本地对话同步到另一台设备。"),
      ]),
      uiState.browserStorageError
        ? Vue.h("div", { class: "lifecycle-status lifecycle-status-error" }, `存储不可用：${uiState.browserStorageError}`)
        : null,
      Vue.h("div", { class: "lifecycle-metric-grid" }, [
        Vue.h("div", { class: "lifecycle-metric-card lifecycle-metric-primary" }, [
          Vue.h("span", null, "当前保存方式"),
          Vue.h("strong", null, uiState.browserStorageLoading ? "检测中…" : mode),
          Vue.h("small", null, modeNote),
        ]),
        Vue.h("div", { class: `lifecycle-metric-card ${health?.persisted ? "lifecycle-metric-muted" : "lifecycle-metric-warn"}` }, [
          Vue.h("span", null, "浏览器持久性"),
          Vue.h("strong", null, uiState.browserStorageLoading ? "检测中…" : persistedLabel),
          Vue.h("small", null, persistedNote),
        ]),
        Vue.h("div", { class: "lifecycle-metric-card lifecycle-metric-muted" }, [
          Vue.h("span", null, "本站占用"),
          Vue.h("strong", null, uiState.browserStorageLoading ? "检测中…" : `${usage}${quota}`),
          Vue.h("small", null, "该数值由浏览器估算，包含本站 IndexedDB、缓存等数据。"),
        ]),
      ]),
      Vue.h("section", { class: "lifecycle-card" }, [
        Vue.h("div", { class: "lifecycle-section-heading" }, [
          Vue.h("div", null, [
            Vue.h("div", { class: "app-settings-section-title" }, "可靠保存建议"),
            Vue.h("p", { class: "lifecycle-copy lifecycle-card-copy" }, "优先授权 Sage 文件夹；不支持文件夹授权的浏览器会自动使用 IndexedDB。无论使用哪种方式，都可以定期下载浏览器数据备份。"),
          ]),
          Vue.h("div", { class: "lifecycle-preview-actions" }, [
            Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.browserStorageLoading, onClick: loadBrowserStorage }, "重新检测"),
            Vue.h("button", { class: "btn-sm btn-sm-primary", type: "button", onClick: openBrowserStorageManager }, "备份与恢复"),
          ]),
        ]),
      ]),
    ]);
  }

  // ── 记忆 (long-term memory) ────────────────────────────────────
  globalThis.addEventListener("baa:local-storage-changed", () => {
    if (ctx.uiState?.tab === "storage" && isBrowserLocalMode()) loadBrowserStorage();
  });

  return {
    loadStorage, loadLifecycle, loadBrowserStorage, renderStorage, renderBrowserStorage,
  };
}
