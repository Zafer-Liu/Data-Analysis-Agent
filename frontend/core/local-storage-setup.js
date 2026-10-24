import {
  chooseLocalDataDirectory,
  downloadLocalBackup,
  getLocalStoragePreference,
  importLocalBackup,
  isBrowserLocalMode,
  useIndexedDbStorage,
} from "./local-data-store.js";
import { closeOverlay, openOverlay, toast } from "./overlay.js";
import { loadSavedList } from "../legacy/sessions.js";

function element(id) {
  return document.getElementById(id);
}

function renderPreference(preference) {
  const status = element("local-storage-status");
  const bannerButton = element("cloud-storage-button");
  const folderButton = element("local-storage-folder-btn");
  if (!status || !bannerButton || !folderButton) return;

  status.className = "local-storage-status";
  folderButton.disabled = !preference.directorySupported;
  if (preference.mode === "directory" && preference.permission === "granted") {
    status.textContent = `当前保存位置：${preference.directoryName || "Sage"} 文件夹`;
    status.classList.add("ok");
    bannerButton.textContent = "Sage 文件夹";
    folderButton.textContent = "更换文件夹";
  } else if (preference.mode === "directory") {
    status.textContent = "原文件夹授权已失效，请重新选择；恢复授权前将使用浏览器存储。";
    status.classList.add("err");
    bannerButton.textContent = "重新授权文件夹";
    folderButton.textContent = "重新选择文件夹";
  } else if (preference.mode === "indexeddb") {
    status.textContent = preference.directorySupported
      ? "当前保存位置：IndexedDB 浏览器本地数据库"
      : "当前浏览器不支持文件夹授权，已自动使用 IndexedDB 浏览器本地数据库。";
    bannerButton.textContent = "浏览器存储（可更改）";
    folderButton.textContent = preference.directorySupported ? "改用本地文件夹" : "浏览器不支持";
  } else {
    status.textContent = preference.directorySupported
      ? "尚未选择保存位置。"
      : "当前浏览器不支持文件夹授权，将使用 IndexedDB 浏览器本地数据库。";
    bannerButton.textContent = "选择本地文件夹";
    folderButton.textContent = preference.directorySupported ? "选择文件夹" : "浏览器不支持";
  }
  globalThis.dispatchEvent(
    new CustomEvent("baa:local-storage-changed", { detail: { preference } }),
  );
}

async function refreshPreference({ promptIfUnset = false } = {}) {
  if (!isBrowserLocalMode()) return null;
  let preference = await getLocalStoragePreference();
  if (preference.mode === "unset" && !preference.directorySupported) {
    preference = await useIndexedDbStorage();
  }
  renderPreference(preference);
  if (
    promptIfUnset &&
    (preference.mode === "unset" ||
      (preference.mode === "directory" && preference.permission !== "granted"))
  ) {
    openOverlay("ov-local-storage");
  }
  return preference;
}

async function chooseFolder() {
  const button = element("local-storage-folder-btn");
  const status = element("local-storage-status");
  if (button) button.disabled = true;
  if (status) {
    status.textContent = "正在等待您选择目录…";
    status.className = "local-storage-status";
  }
  try {
    const preference = await chooseLocalDataDirectory();
    renderPreference(preference);
    closeOverlay("ov-local-storage");
    const restored = Number(preference.imported?.sessions || 0);
    toast(
      restored ? `已启用 Sage 本地文件夹，并恢复 ${restored} 条对话` : "已启用 Sage 本地文件夹保存",
      "ok",
    );
    await loadSavedList();
  } catch (error) {
    const preference = await useIndexedDbStorage();
    renderPreference(preference);
    closeOverlay("ov-local-storage");
    if (error?.name === "AbortError") {
      toast("未选择文件夹，已自动使用浏览器本地存储");
    } else {
      toast(`文件夹无法使用，已回退到浏览器本地存储：${error.message || error}`, "err");
    }
  } finally {
    if (button) button.disabled = false;
  }
}

async function useBrowserStorage() {
  const preference = await useIndexedDbStorage();
  renderPreference(preference);
  closeOverlay("ov-local-storage");
  toast("已使用浏览器本地存储");
}

async function exportBackup() {
  const button = element("local-storage-export-btn");
  if (button) button.disabled = true;
  try {
    const result = await downloadLocalBackup();
    toast(`本地备份已导出：${result.sessions} 条对话，${result.files} 个文件`, "ok");
  } catch (error) {
    toast(`本地备份导出失败：${error.message || error}`, "err");
  } finally {
    if (button) button.disabled = false;
  }
}

async function importBackup(event) {
  const input = event.currentTarget;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  try {
    const result = await importLocalBackup(file);
    toast(`本地备份已恢复：${result.sessions} 条对话，${result.files} 个文件`, "ok");
    await loadSavedList();
  } catch (error) {
    toast(`本地备份恢复失败：${error.message || error}`, "err");
  }
}

function bindControls() {
  const bannerButton = element("cloud-storage-button");
  const folderButton = element("local-storage-folder-btn");
  const browserButton = element("local-storage-browser-btn");
  const exportButton = element("local-storage-export-btn");
  const importInput = element("local-storage-import-input");
  if (bannerButton && !bannerButton.dataset.storageBound) {
    bannerButton.dataset.storageBound = "1";
    bannerButton.addEventListener("click", async () => {
      await refreshPreference();
      openOverlay("ov-local-storage");
    });
  }
  if (folderButton && !folderButton.dataset.storageBound) {
    folderButton.dataset.storageBound = "1";
    folderButton.addEventListener("click", chooseFolder);
  }
  if (browserButton && !browserButton.dataset.storageBound) {
    browserButton.dataset.storageBound = "1";
    browserButton.addEventListener("click", useBrowserStorage);
  }
  if (exportButton && !exportButton.dataset.storageBound) {
    exportButton.dataset.storageBound = "1";
    exportButton.addEventListener("click", exportBackup);
  }
  if (importInput && !importInput.dataset.storageBound) {
    importInput.dataset.storageBound = "1";
    importInput.addEventListener("change", importBackup);
  }
}

export async function initializeLocalStorageSetup() {
  if (!isBrowserLocalMode()) return;
  bindControls();
  if (!globalThis.__baaLocalStorageWarningBound) {
    globalThis.__baaLocalStorageWarningBound = true;
    globalThis.addEventListener("baa:local-storage-warning", (event) => {
      toast(event.detail?.message || "本地文件夹写入失败，已回退到浏览器存储", "err");
      refreshPreference().catch(() => {});
    });
  }
  await refreshPreference({ promptIfUnset: true });
}
