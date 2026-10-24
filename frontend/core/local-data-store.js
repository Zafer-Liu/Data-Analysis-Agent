// Browser-local persistence for the managed Web edition.
//
// Account, quota and security events remain server-side. Conversation bodies
// and uploaded file blobs are kept in IndexedDB, scoped by the authenticated
// user id so multiple accounts sharing one browser profile do not see each
// other's local data.

const DB_NAME = "baa-local-user-data";
const DB_VERSION = 3;
const SESSION_STORE = "sessions";
const FILE_STORE = "files";
const KNOWLEDGE_STORE = "knowledge";
const SETTINGS_STORE = "settings";
const DIRECTORY_MODE = "directory";
const INDEXEDDB_MODE = "indexeddb";
const BACKUP_FORMAT = "sage-local-backup";
const BACKUP_VERSION = 2;

let ownerPromise = null;

export function isBrowserLocalMode() {
  return document.body?.dataset?.cloudManaged === "1";
}

function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("浏览器本地存储操作失败"));
  });
}

function transactionDone(transaction) {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(transaction.error || new Error("浏览器本地存储事务已取消"));
    transaction.onerror = () => reject(transaction.error || new Error("浏览器本地存储事务失败"));
  });
}

function openDatabase() {
  if (!globalThis.indexedDB) {
    return Promise.reject(new Error("当前浏览器不支持 IndexedDB，无法启用本地数据保存"));
  }
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(SESSION_STORE)) {
        const sessions = db.createObjectStore(SESSION_STORE, { keyPath: "key" });
        sessions.createIndex("owner_id", "owner_id", { unique: false });
        sessions.createIndex("owner_autosave", ["owner_id", "is_autosave"], { unique: false });
      }
      if (!db.objectStoreNames.contains(FILE_STORE)) {
        const files = db.createObjectStore(FILE_STORE, { keyPath: "key" });
        files.createIndex("owner_session", ["owner_id", "session_id"], { unique: false });
      }
      if (!db.objectStoreNames.contains(KNOWLEDGE_STORE)) {
        const knowledge = db.createObjectStore(KNOWLEDGE_STORE, { keyPath: "key" });
        knowledge.createIndex("owner_id", "owner_id", { unique: false });
        knowledge.createIndex("owner_kind", ["owner_id", "kind"], { unique: false });
      }
      if (!db.objectStoreNames.contains(SETTINGS_STORE)) {
        db.createObjectStore(SETTINGS_STORE, { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("无法打开浏览器本地数据库"));
  });
}

async function withStore(storeName, mode, callback) {
  const db = await openDatabase();
  try {
    const tx = db.transaction(storeName, mode);
    const result = await callback(tx.objectStore(storeName));
    await transactionDone(tx);
    return result;
  } finally {
    db.close();
  }
}

export async function getLocalOwnerId() {
  if (!ownerPromise) {
    ownerPromise = fetch("/api/auth/me", { credentials: "same-origin", cache: "no-store" })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok || !data.authenticated || !data.user?.id) {
          throw new Error("登录状态已失效，请重新登录");
        }
        return String(data.user.id);
      })
      .catch((error) => {
        ownerPromise = null;
        throw error;
      });
  }
  return ownerPromise;
}

export async function requestPersistentStorage() {
  try {
    if (navigator.storage?.persist) return Boolean(await navigator.storage.persist());
  } catch (_) {
    // Persistence is best-effort; IndexedDB remains available when denied.
  }
  return false;
}

export async function getBrowserStorageHealth() {
  if (!isBrowserLocalMode()) {
    return { available: false, runMode: document.body?.dataset?.runMode || "local" };
  }
  const preference = await getLocalStoragePreference();
  let persisted = false;
  let usage = null;
  let quota = null;
  try {
    persisted = Boolean(await navigator.storage?.persisted?.());
  } catch (_) {
    // Some privacy modes expose StorageManager but reject status checks.
  }
  try {
    const estimate = await navigator.storage?.estimate?.();
    usage = Number.isFinite(estimate?.usage) ? Number(estimate.usage) : null;
    quota = Number.isFinite(estimate?.quota) ? Number(estimate.quota) : null;
  } catch (_) {
    // Capacity reporting is optional; IndexedDB availability is checked below.
  }
  const db = await openDatabase();
  db.close();
  return {
    available: true,
    runMode: "cloud",
    persisted,
    usage,
    quota,
    preference,
  };
}

export function supportsDirectoryStorage() {
  return Boolean(globalThis.isSecureContext && globalThis.showDirectoryPicker);
}

function storageSettingKey(ownerId) {
  return `storage:${ownerId}`;
}

async function getStorageSetting(ownerId) {
  return withStore(SETTINGS_STORE, "readonly", (store) =>
    requestResult(store.get(storageSettingKey(ownerId))),
  );
}

async function putStorageSetting(ownerId, value) {
  const record = {
    key: storageSettingKey(ownerId),
    owner_id: ownerId,
    updated_at: new Date().toISOString(),
    ...value,
  };
  await withStore(SETTINGS_STORE, "readwrite", (store) => store.put(record));
  return record;
}

async function directoryPermission(handle) {
  if (!handle?.queryPermission) return "denied";
  try {
    return await handle.queryPermission({ mode: "readwrite" });
  } catch (_) {
    return "denied";
  }
}

export async function getLocalStoragePreference() {
  if (!isBrowserLocalMode()) return { mode: "desktop" };
  const ownerId = await getLocalOwnerId();
  const setting = await getStorageSetting(ownerId);
  if (!setting) {
    return {
      mode: "unset",
      directorySupported: supportsDirectoryStorage(),
    };
  }
  if (setting.mode !== DIRECTORY_MODE) {
    return {
      mode: INDEXEDDB_MODE,
      directorySupported: supportsDirectoryStorage(),
    };
  }
  return {
    mode: DIRECTORY_MODE,
    directoryName: setting.directory_name || "Sage",
    permission: await directoryPermission(setting.directory_handle),
    directorySupported: supportsDirectoryStorage(),
  };
}

export async function useIndexedDbStorage() {
  const ownerId = await getLocalOwnerId();
  await requestPersistentStorage();
  await putStorageSetting(ownerId, { mode: INDEXEDDB_MODE });
  return getLocalStoragePreference();
}

async function prepareSageDirectory(selectedHandle) {
  if (String(selectedHandle?.name || "").toLowerCase() === "sage") return selectedHandle;
  return selectedHandle.getDirectoryHandle("Sage", { create: true });
}

export async function chooseLocalDataDirectory() {
  if (!supportsDirectoryStorage()) {
    throw new Error("当前浏览器不支持直接保存到本地文件夹");
  }
  const ownerId = await getLocalOwnerId();
  const selectedHandle = await globalThis.showDirectoryPicker({
    id: "sage-local-data",
    mode: "readwrite",
    startIn: "documents",
  });
  const directoryHandle = await prepareSageDirectory(selectedHandle);
  await accountDirectory(directoryHandle, ownerId, true);
  await putStorageSetting(ownerId, {
    mode: DIRECTORY_MODE,
    directory_handle: directoryHandle,
    directory_name: directoryHandle.name || "Sage",
  });
  const imported = await importDirectoryData(directoryHandle, ownerId);
  return { ...(await getLocalStoragePreference()), imported };
}

function safePathSegment(value, fallback = "item") {
  const normalized = Array.from(String(value || ""), (character) =>
    character.charCodeAt(0) < 32 ? "_" : character,
  )
    .join("")
    .replace(/[<>:"/\\|?*]/g, "_")
    .replace(/[. ]+$/g, "")
    .slice(0, 120);
  return normalized || fallback;
}

async function accountDirectory(rootHandle, ownerId, create = false) {
  const accounts = await rootHandle.getDirectoryHandle("accounts", { create });
  const account = await accounts.getDirectoryHandle(`account-${safePathSegment(ownerId)}`, {
    create,
  });
  if (create) {
    await account.getDirectoryHandle("conversations", { create: true });
    await account.getDirectoryHandle("uploads", { create: true });
    await account.getDirectoryHandle("knowledge", { create: true });
    await account.getDirectoryHandle("charts", { create: true });
    await account.getDirectoryHandle("exports", { create: true });
  }
  return account;
}

async function writableDirectory() {
  const ownerId = await getLocalOwnerId();
  const setting = await getStorageSetting(ownerId);
  if (setting?.mode !== DIRECTORY_MODE || !setting.directory_handle) return null;
  if ((await directoryPermission(setting.directory_handle)) !== "granted") return null;
  return {
    ownerId,
    account: await accountDirectory(setting.directory_handle, ownerId, true),
  };
}

async function writeFile(directory, name, contents) {
  const handle = await directory.getFileHandle(safePathSegment(name), { create: true });
  const writable = await handle.createWritable();
  await writable.write(contents);
  await writable.close();
}

async function importDirectoryData(rootHandle, ownerId) {
  const account = await accountDirectory(rootHandle, ownerId, true);
  const sessions = [];
  const files = [];

  try {
    const conversations = await account.getDirectoryHandle("conversations");
    for await (const entry of conversations.values()) {
      if (entry.kind !== "file" || !entry.name.toLowerCase().endsWith(".json")) continue;
      try {
        const file = await entry.getFile();
        const payload = JSON.parse(await file.text());
        if (!payload || typeof payload !== "object" || !Array.isArray(payload.history)) continue;
        const filename = safePathSegment(entry.name);
        const sessionId = String(payload.session_id || "");
        sessions.push({
          key: sessionKey(ownerId, filename),
          owner_id: ownerId,
          filename,
          name: String(payload.name || "未命名对话"),
          saved_at: String(payload.saved_at || new Date(file.lastModified).toISOString()),
          is_autosave: Boolean(payload.autosave || filename.startsWith("autosave")),
          session_id: sessionId,
          source_session_ids: sessionId ? [sessionId] : [],
          payload,
        });
      } catch (_) {
        // Ignore unrelated or damaged JSON files in a user-managed folder.
      }
    }
  } catch (error) {
    if (error?.name !== "NotFoundError") throw error;
  }

  const knowledgeRecords = [];
  try {
    const knowledge = await account.getDirectoryHandle("knowledge");
    try {
      const snapshotFile = await (await knowledge.getFileHandle("snapshot.json")).getFile();
      const snapshot = JSON.parse(await snapshotFile.text());
      if (snapshot && typeof snapshot === "object") {
        knowledgeRecords.push({
          key: knowledgeSnapshotKey(ownerId),
          owner_id: ownerId,
          kind: "snapshot",
          updated_at: new Date(snapshotFile.lastModified).toISOString(),
          snapshot,
        });
      }
    } catch (error) {
      if (error?.name !== "NotFoundError" && error?.name !== "SyntaxError") throw error;
    }
    try {
      const knowledgeFiles = await knowledge.getDirectoryHandle("files");
      for await (const entry of knowledgeFiles.values()) {
        if (entry.kind !== "file") continue;
        const file = await entry.getFile();
        const separator = entry.name.indexOf("_");
        const id = separator > 0 ? entry.name.slice(0, separator) : randomId();
        const name = separator > 0 ? entry.name.slice(separator + 1) : entry.name;
        knowledgeRecords.push({
          key: knowledgeFileKey(ownerId, id),
          id,
          owner_id: ownerId,
          kind: "file",
          name,
          type: String(file.type || "application/octet-stream"),
          size: Number(file.size || 0),
          last_modified: Number(file.lastModified || Date.now()),
          server_filename: "",
          category_name: "",
          blob: file,
          stored_at: new Date().toISOString(),
        });
      }
    } catch (error) {
      if (error?.name !== "NotFoundError") throw error;
    }
  } catch (error) {
    if (error?.name !== "NotFoundError") throw error;
  }

  try {
    const uploads = await account.getDirectoryHandle("uploads");
    for await (const sessionEntry of uploads.values()) {
      if (sessionEntry.kind !== "directory") continue;
      for await (const entry of sessionEntry.values()) {
        if (entry.kind !== "file") continue;
        try {
          const file = await entry.getFile();
          const separator = entry.name.indexOf("_");
          const id = separator > 0 ? entry.name.slice(0, separator) : randomId();
          const name = separator > 0 ? entry.name.slice(separator + 1) : entry.name;
          files.push({
            key: fileKey(ownerId, sessionEntry.name, id),
            id,
            owner_id: ownerId,
            session_id: sessionEntry.name,
            name,
            type: String(file.type || "application/octet-stream"),
            size: Number(file.size || 0),
            last_modified: Number(file.lastModified || Date.now()),
            blob: file,
            directory_name: entry.name,
            stored_at: new Date().toISOString(),
          });
        } catch (_) {
          // Keep scanning when one user-managed file cannot be read.
        }
      }
    }
  } catch (error) {
    if (error?.name !== "NotFoundError") throw error;
  }

  if (sessions.length) {
    await withStore(SESSION_STORE, "readwrite", async (store) => {
      for (const record of sessions) store.put(record);
    });
  }
  if (files.length) {
    await withStore(FILE_STORE, "readwrite", async (store) => {
      for (const record of files) store.put(record);
    });
  }
  if (knowledgeRecords.length) {
    await withStore(KNOWLEDGE_STORE, "readwrite", async (store) => {
      for (const record of knowledgeRecords) store.put(record);
    });
  }
  return { sessions: sessions.length, files: files.length, knowledge: knowledgeRecords.length };
}

function reportDirectorySyncError(error) {
  console.warn("[local-data] Sage directory sync failed", error);
  globalThis.dispatchEvent(
    new CustomEvent("baa:local-storage-warning", {
      detail: { message: "Sage 文件夹暂时无法写入，数据已回退保存到浏览器存储" },
    }),
  );
}

function reportExportSyncError(error) {
  console.warn("[local-data] Sage export sync failed", error);
  globalThis.dispatchEvent(
    new CustomEvent("baa:local-storage-warning", {
      detail: { message: "输出文件未能写入 Sage 文件夹；请使用对话中的下载链接另存副本" },
    }),
  );
}

function isSameOriginExportUrl(value) {
  try {
    const url = new URL(String(value || ""), globalThis.location?.href || "http://localhost/");
    return (
      url.origin === globalThis.location?.origin &&
      (url.pathname.startsWith("/api/export/") || url.pathname.startsWith("/api/output/"))
    );
  } catch (_) {
    return false;
  }
}

function filenameFromDisposition(value) {
  const header = String(value || "");
  const encoded = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (encoded?.[1]) {
    try {
      return decodeURIComponent(encoded[1]);
    } catch (_) {
      return encoded[1];
    }
  }
  const quoted = header.match(/filename="([^"]+)"/i);
  if (quoted?.[1]) return quoted[1];
  const plain = header.match(/filename=([^;]+)/i);
  return plain?.[1]?.trim() || "";
}

function exportFilename(url, response, fallback) {
  const fromHeader = filenameFromDisposition(response.headers.get("content-disposition"));
  if (fromHeader) return safePathSegment(fromHeader, "export");
  try {
    const fromUrl = decodeURIComponent(
      new URL(url, globalThis.location.href).pathname.split("/").pop() || "",
    );
    if (fromUrl) return safePathSegment(fromUrl, "export");
  } catch (_) {
    // The URL was validated before this helper is called.
  }
  return safePathSegment(fallback, "export");
}

/**
 * Copy a completed server export into the user-authorized Sage folder.
 *
 * Only same-origin export routes are eligible. This prevents a streamed
 * message or artifact from turning an arbitrary external URL into a local
 * file write. The original browser download link remains available whether
 * or not directory sync is configured.
 */
export async function mirrorGeneratedExport({ url, filename = "", sessionId = "" } = {}) {
  if (!isBrowserLocalMode()) return { saved: false, reason: "not_web_mode" };
  if (!isSameOriginExportUrl(url)) return { saved: false, reason: "not_export_url" };

  const target = await writableDirectory().catch(() => null);
  if (!target) return { saved: false, reason: "directory_unavailable" };

  try {
    const response = await fetch(url, { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) throw new Error(`导出文件读取失败（HTTP ${response.status}）`);
    const blob = await response.blob();
    const exportsDir = await target.account.getDirectoryHandle("exports", { create: true });
    const sessionDir = await exportsDir.getDirectoryHandle(
      safePathSegment(sessionId || "unassigned"),
      { create: true },
    );
    const originalName = exportFilename(url, response, filename);
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const localName = safePathSegment(
      `${stamp}_${randomId().slice(0, 8)}_${originalName}`,
      "export",
    );
    await writeFile(sessionDir, localName, blob);
    return {
      saved: true,
      filename: localName,
      size: Number(blob.size || 0),
      type: String(blob.type || response.headers.get("content-type") || "application/octet-stream"),
    };
  } catch (error) {
    reportExportSyncError(error);
    throw error;
  }
}

async function mirrorFilesToDirectory(records) {
  const target = await writableDirectory();
  if (!target) return;
  for (const record of records) {
    const uploads = await target.account.getDirectoryHandle("uploads", { create: true });
    const session = await uploads.getDirectoryHandle(safePathSegment(record.session_id), {
      create: true,
    });
    record.directory_name = `${safePathSegment(record.id)}_${safePathSegment(record.name, "file")}`;
    await writeFile(session, record.directory_name, record.blob);
  }
}

async function mirrorSessionToDirectory(record) {
  const target = await writableDirectory();
  if (!target) return;
  const conversations = await target.account.getDirectoryHandle("conversations", { create: true });
  await writeFile(
    conversations,
    record.filename,
    new Blob([JSON.stringify(record.payload, null, 2)], { type: "application/json" }),
  );
}

function knowledgeSnapshotKey(ownerId) {
  return `${ownerId}:knowledge:snapshot`;
}

function knowledgeFileKey(ownerId, id) {
  return `${ownerId}:knowledge:file:${id}`;
}

async function writableKnowledgeDirectory() {
  const target = await writableDirectory();
  if (!target) return null;
  return target.account.getDirectoryHandle("knowledge", { create: true });
}

async function mirrorKnowledgeSnapshot(record) {
  const directory = await writableKnowledgeDirectory();
  if (!directory) return;
  await writeFile(
    directory,
    "snapshot.json",
    new Blob([JSON.stringify(record.snapshot, null, 2)], { type: "application/json" }),
  );
}

async function mirrorKnowledgeFile(record) {
  const directory = await writableKnowledgeDirectory();
  if (!directory) return;
  const files = await directory.getDirectoryHandle("files", { create: true });
  await writeFile(files, `${record.id}_${record.name}`, record.blob);
}

function sessionKey(ownerId, filename) {
  return `${ownerId}:${filename}`;
}

function fileKey(ownerId, sessionId, fileId) {
  return `${ownerId}:${sessionId}:${fileId}`;
}

function randomId() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export async function rememberUploadedFiles(sessionId, fileList) {
  if (!isBrowserLocalMode() || !sessionId) return [];
  const files = Array.from(fileList || []).filter((file) => file && file.name);
  if (!files.length) return [];
  const ownerId = await getLocalOwnerId();
  await requestPersistentStorage();
  const records = files.map((file) => {
    const id = randomId();
    return {
      key: fileKey(ownerId, sessionId, id),
      id,
      owner_id: ownerId,
      session_id: sessionId,
      name: String(file.name),
      type: String(file.type || "application/octet-stream"),
      size: Number(file.size || 0),
      last_modified: Number(file.lastModified || Date.now()),
      blob: file,
      stored_at: new Date().toISOString(),
    };
  });
  await withStore(FILE_STORE, "readwrite", async (store) => {
    for (const record of records) store.put(record);
  });
  try {
    await mirrorFilesToDirectory(records);
    await withStore(FILE_STORE, "readwrite", async (store) => {
      for (const record of records) store.put(record);
    });
  } catch (error) {
    reportDirectorySyncError(error);
  }
  return records.map(({ id, name, size, type }) => ({ id, name, size, type }));
}

export async function listUploadedFiles(sessionId) {
  if (!isBrowserLocalMode() || !sessionId) return [];
  const ownerId = await getLocalOwnerId();
  return withStore(FILE_STORE, "readonly", async (store) => {
    const index = store.index("owner_session");
    const records = await requestResult(index.getAll(IDBKeyRange.only([ownerId, sessionId])));
    return records.map(
      (record) =>
        new File([record.blob], record.name, {
          type: record.type,
          lastModified: record.last_modified,
        }),
    );
  });
}

export async function saveLocalSession({ filename, name, payload, isAutosave = false }) {
  if (!isBrowserLocalMode()) throw new Error("当前不是网页版本地存储模式");
  const ownerId = await getLocalOwnerId();
  const safeFilename = String(
    filename || `${isAutosave ? "autosave" : "conversation"}_${randomId()}.json`,
  );
  const prior = await getLocalSession(safeFilename);
  const record = {
    key: sessionKey(ownerId, safeFilename),
    owner_id: ownerId,
    filename: safeFilename,
    name: String(name || payload?.name || "未命名对话"),
    saved_at: String(payload?.saved_at || new Date().toISOString()),
    is_autosave: Boolean(isAutosave),
    session_id: String(payload?.session_id || ""),
    source_session_ids: Array.from(
      new Set(
        [...(prior?.source_session_ids || []), String(payload?.session_id || "")].filter(Boolean),
      ),
    ),
    payload,
  };
  await requestPersistentStorage();
  await withStore(SESSION_STORE, "readwrite", (store) => {
    store.put(record);
  });
  try {
    await mirrorSessionToDirectory(record);
  } catch (error) {
    reportDirectorySyncError(error);
  }
  return record;
}

export async function getLocalSession(filename) {
  if (!isBrowserLocalMode() || !filename) return null;
  const ownerId = await getLocalOwnerId();
  return withStore(SESSION_STORE, "readonly", (store) =>
    requestResult(store.get(sessionKey(ownerId, String(filename)))),
  );
}

export async function listLocalSessions() {
  if (!isBrowserLocalMode()) return [];
  const ownerId = await getLocalOwnerId();
  return withStore(SESSION_STORE, "readonly", async (store) => {
    const records = await requestResult(store.index("owner_id").getAll(IDBKeyRange.only(ownerId)));
    return records.sort((a, b) => String(b.saved_at).localeCompare(String(a.saved_at)));
  });
}

export async function latestLocalAutosave() {
  const records = await listLocalSessions();
  return records.find((record) => record.is_autosave) || null;
}

export async function renameLocalSession(filename, name) {
  const record = await getLocalSession(filename);
  if (!record) throw new Error("本地对话不存在");
  record.name = String(name || "").trim();
  record.payload = {
    ...(record.payload || {}),
    name: record.name,
    renamed_at: new Date().toISOString(),
  };
  await withStore(SESSION_STORE, "readwrite", (store) => store.put(record));
  try {
    await mirrorSessionToDirectory(record);
  } catch (error) {
    reportDirectorySyncError(error);
  }
  return record;
}

export async function deleteLocalSession(filename) {
  const ownerId = await getLocalOwnerId();
  const record = await getLocalSession(filename);
  if (!record) return false;
  await withStore(SESSION_STORE, "readwrite", (store) =>
    store.delete(sessionKey(ownerId, filename)),
  );

  // File blobs are shared by every save derived from the same live session.
  // Reclaim them only when no remaining local conversation references it.
  const remaining = await listLocalSessions();
  const referenced = new Set(remaining.flatMap((item) => item.source_session_ids || []));
  const directoryTarget = await writableDirectory().catch(() => null);
  if (directoryTarget) {
    try {
      const conversations = await directoryTarget.account.getDirectoryHandle("conversations");
      await conversations.removeEntry(safePathSegment(filename));
    } catch (error) {
      if (error?.name !== "NotFoundError") reportDirectorySyncError(error);
    }
  }
  for (const sessionId of record.source_session_ids || []) {
    if (referenced.has(sessionId)) continue;
    await withStore(FILE_STORE, "readwrite", async (store) => {
      const index = store.index("owner_session");
      const keys = await requestResult(index.getAllKeys(IDBKeyRange.only([ownerId, sessionId])));
      for (const key of keys) store.delete(key);
    });
    if (directoryTarget) {
      try {
        const uploads = await directoryTarget.account.getDirectoryHandle("uploads");
        await uploads.removeEntry(safePathSegment(sessionId), { recursive: true });
      } catch (error) {
        if (error?.name !== "NotFoundError") reportDirectorySyncError(error);
      }
    }
  }
  return true;
}

export async function filesForLocalSession(record) {
  const sessionIds = Array.from(
    new Set(record?.source_session_ids || [record?.session_id].filter(Boolean)),
  );
  const files = [];
  for (const sessionId of sessionIds) files.push(...(await listUploadedFiles(sessionId)));
  return files;
}

// ── Business knowledge (cloud: browser-owned durable copy) ──────────────────

export async function getLocalKnowledgeSnapshot() {
  if (!isBrowserLocalMode()) return null;
  const ownerId = await getLocalOwnerId();
  const record = await withStore(KNOWLEDGE_STORE, "readonly", (store) =>
    requestResult(store.get(knowledgeSnapshotKey(ownerId))),
  );
  return record?.snapshot && typeof record.snapshot === "object" ? record.snapshot : null;
}

export async function saveLocalKnowledgeSnapshot(snapshot) {
  if (!isBrowserLocalMode()) return null;
  const ownerId = await getLocalOwnerId();
  await requestPersistentStorage();
  const record = {
    key: knowledgeSnapshotKey(ownerId),
    owner_id: ownerId,
    kind: "snapshot",
    updated_at: new Date().toISOString(),
    snapshot,
  };
  await withStore(KNOWLEDGE_STORE, "readwrite", (store) => store.put(record));
  try {
    await mirrorKnowledgeSnapshot(record);
  } catch (error) {
    reportDirectorySyncError(error);
  }
  return record;
}

export async function saveLocalKnowledgeFile({ file, serverFilename, categoryName }) {
  if (!isBrowserLocalMode() || !file?.name) return null;
  const ownerId = await getLocalOwnerId();
  await requestPersistentStorage();
  const record = {
    key: knowledgeFileKey(ownerId, randomId()),
    id: randomId(),
    owner_id: ownerId,
    kind: "file",
    name: String(file.name),
    type: String(file.type || "application/octet-stream"),
    size: Number(file.size || 0),
    last_modified: Number(file.lastModified || Date.now()),
    server_filename: String(serverFilename || ""),
    category_name: String(categoryName || ""),
    blob: file,
    stored_at: new Date().toISOString(),
  };
  // Keep the IndexedDB key and public id identical for straightforward updates.
  record.key = knowledgeFileKey(ownerId, record.id);
  await withStore(KNOWLEDGE_STORE, "readwrite", (store) => store.put(record));
  try {
    await mirrorKnowledgeFile(record);
  } catch (error) {
    reportDirectorySyncError(error);
  }
  return record;
}

export async function listLocalKnowledgeFiles() {
  if (!isBrowserLocalMode()) return [];
  const ownerId = await getLocalOwnerId();
  const records = await withStore(KNOWLEDGE_STORE, "readonly", async (store) =>
    requestResult(store.index("owner_kind").getAll(IDBKeyRange.only([ownerId, "file"]))),
  );
  return Array.isArray(records) ? records : [];
}

export async function updateLocalKnowledgeFile(id, patch) {
  if (!isBrowserLocalMode() || !id) return null;
  const ownerId = await getLocalOwnerId();
  const key = knowledgeFileKey(ownerId, id);
  const record = await withStore(KNOWLEDGE_STORE, "readwrite", async (store) => {
    const current = await requestResult(store.get(key));
    if (!current) return null;
    Object.assign(current, patch || {});
    store.put(current);
    return current;
  });
  return record;
}

export async function deleteLocalKnowledgeFileByServerFilename(serverFilename) {
  if (!isBrowserLocalMode() || !serverFilename) return false;
  const ownerId = await getLocalOwnerId();
  const records = await listLocalKnowledgeFiles();
  const matched = records.filter((record) => record.server_filename === serverFilename);
  if (!matched.length) return false;
  await withStore(KNOWLEDGE_STORE, "readwrite", (store) => {
    for (const record of matched) store.delete(knowledgeFileKey(ownerId, record.id));
  });
  const directory = await writableKnowledgeDirectory().catch(() => null);
  if (directory) {
    const files = await directory.getDirectoryHandle("files").catch(() => null);
    if (files) {
      for (const record of matched) {
        await files.removeEntry(safePathSegment(`${record.id}_${record.name}`)).catch(() => {});
      }
    }
  }
  return true;
}

export async function deleteLocalKnowledgeFilesByCategory(categoryName) {
  if (!isBrowserLocalMode() || !categoryName) return 0;
  const ownerId = await getLocalOwnerId();
  const matched = (await listLocalKnowledgeFiles()).filter(
    (record) => record.category_name === categoryName,
  );
  if (!matched.length) return 0;
  await withStore(KNOWLEDGE_STORE, "readwrite", (store) => {
    for (const record of matched) store.delete(knowledgeFileKey(ownerId, record.id));
  });
  const directory = await writableKnowledgeDirectory().catch(() => null);
  if (directory) {
    const files = await directory.getDirectoryHandle("files").catch(() => null);
    if (files) {
      for (const record of matched) {
        await files.removeEntry(safePathSegment(`${record.id}_${record.name}`)).catch(() => {});
      }
    }
  }
  return matched.length;
}

function blobAsDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("无法读取本地文件"));
    reader.readAsDataURL(blob);
  });
}

async function dataUrlAsBlob(value) {
  if (typeof value !== "string" || !value.startsWith("data:")) {
    throw new Error("备份中的文件内容无效");
  }
  const response = await fetch(value);
  if (!response.ok) throw new Error("无法恢复备份文件");
  return response.blob();
}

async function ownerFileRecords(ownerId) {
  return withStore(FILE_STORE, "readonly", async (store) => {
    const records = await requestResult(store.getAll());
    return records.filter((record) => String(record.owner_id || "") === ownerId);
  });
}

export async function createLocalBackup() {
  if (!isBrowserLocalMode()) throw new Error("当前不是网页版本地存储模式");
  const ownerId = await getLocalOwnerId();
  const sessions = await listLocalSessions();
  const fileRecords = await ownerFileRecords(ownerId);
  const knowledgeSnapshot = await getLocalKnowledgeSnapshot();
  const knowledgeFiles = await listLocalKnowledgeFiles();
  const files = [];
  for (const record of fileRecords) {
    files.push({
      id: String(record.id || randomId()),
      session_id: String(record.session_id || ""),
      name: String(record.name || "file"),
      type: String(record.type || "application/octet-stream"),
      size: Number(record.size || record.blob?.size || 0),
      last_modified: Number(record.last_modified || Date.now()),
      stored_at: String(record.stored_at || ""),
      data_url: await blobAsDataUrl(record.blob),
    });
  }
  return {
    format: BACKUP_FORMAT,
    version: BACKUP_VERSION,
    exported_at: new Date().toISOString(),
    sessions: sessions.map((record) => ({
      filename: String(record.filename || ""),
      name: String(record.name || "未命名对话"),
      saved_at: String(record.saved_at || ""),
      is_autosave: Boolean(record.is_autosave),
      session_id: String(record.session_id || ""),
      source_session_ids: Array.from(record.source_session_ids || []),
      payload: record.payload || {},
    })),
    files,
    knowledge: {
      snapshot: knowledgeSnapshot,
      files: await Promise.all(
        knowledgeFiles.map(async (record) => ({
          id: String(record.id || randomId()),
          name: String(record.name || "knowledge-source"),
          type: String(record.type || "application/octet-stream"),
          size: Number(record.size || record.blob?.size || 0),
          last_modified: Number(record.last_modified || Date.now()),
          server_filename: String(record.server_filename || ""),
          category_name: String(record.category_name || ""),
          data_url: await blobAsDataUrl(record.blob),
        })),
      ),
    },
  };
}

export async function downloadLocalBackup() {
  const backup = await createLocalBackup();
  const blob = new Blob([JSON.stringify(backup, null, 2)], {
    type: "application/json;charset=utf-8",
  });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `sage-local-backup-${stamp}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  return { sessions: backup.sessions.length, files: backup.files.length };
}

export async function importLocalBackup(file) {
  if (!isBrowserLocalMode()) throw new Error("当前不是网页版本地存储模式");
  if (!file?.text) throw new Error("请选择 Sage 本地备份文件");
  const backup = JSON.parse(await file.text());
  if (backup?.format !== BACKUP_FORMAT || ![1, BACKUP_VERSION].includes(Number(backup.version))) {
    throw new Error("不是受支持的 Sage 本地备份文件");
  }
  if (!Array.isArray(backup.sessions) || !Array.isArray(backup.files)) {
    throw new Error("Sage 本地备份结构不完整");
  }
  if (backup.sessions.length > 5000 || backup.files.length > 10000) {
    throw new Error("备份项目数量超过安全上限");
  }
  const knowledgeBackup =
    backup.knowledge && typeof backup.knowledge === "object"
      ? backup.knowledge
      : { snapshot: null, files: [] };
  if (!Array.isArray(knowledgeBackup.files) || knowledgeBackup.files.length > 2000) {
    throw new Error("业务知识库备份结构不完整或超过安全上限");
  }

  const ownerId = await getLocalOwnerId();
  const sessionRecords = backup.sessions.map((item) => {
    const filename = safePathSegment(item.filename, `conversation_${randomId()}.json`);
    const sessionId = String(item.session_id || "");
    return {
      key: sessionKey(ownerId, filename),
      owner_id: ownerId,
      filename,
      name: String(item.name || item.payload?.name || "未命名对话"),
      saved_at: String(item.saved_at || new Date().toISOString()),
      is_autosave: Boolean(item.is_autosave),
      session_id: sessionId,
      source_session_ids: Array.from(
        new Set([...(item.source_session_ids || []), sessionId].filter(Boolean).map(String)),
      ),
      payload: item.payload && typeof item.payload === "object" ? item.payload : {},
    };
  });
  const fileRecords = [];
  for (const item of backup.files) {
    const id = safePathSegment(item.id, randomId());
    const sessionId = String(item.session_id || "");
    const blob = await dataUrlAsBlob(item.data_url);
    fileRecords.push({
      key: fileKey(ownerId, sessionId, id),
      id,
      owner_id: ownerId,
      session_id: sessionId,
      name: String(item.name || "file"),
      type: String(item.type || blob.type || "application/octet-stream"),
      size: Number(item.size || blob.size || 0),
      last_modified: Number(item.last_modified || Date.now()),
      stored_at: String(item.stored_at || new Date().toISOString()),
      blob,
    });
  }
  const knowledgeRecords = [];
  if (knowledgeBackup.snapshot && typeof knowledgeBackup.snapshot === "object") {
    knowledgeRecords.push({
      key: knowledgeSnapshotKey(ownerId),
      owner_id: ownerId,
      kind: "snapshot",
      updated_at: new Date().toISOString(),
      snapshot: knowledgeBackup.snapshot,
    });
  }
  for (const item of knowledgeBackup.files) {
    const id = safePathSegment(item.id, randomId());
    const blob = await dataUrlAsBlob(item.data_url);
    knowledgeRecords.push({
      key: knowledgeFileKey(ownerId, id),
      id,
      owner_id: ownerId,
      kind: "file",
      name: String(item.name || "knowledge-source"),
      type: String(item.type || blob.type || "application/octet-stream"),
      size: Number(item.size || blob.size || 0),
      last_modified: Number(item.last_modified || Date.now()),
      server_filename: String(item.server_filename || ""),
      category_name: String(item.category_name || ""),
      blob,
      stored_at: new Date().toISOString(),
    });
  }

  await requestPersistentStorage();
  if (sessionRecords.length) {
    await withStore(SESSION_STORE, "readwrite", async (store) => {
      for (const record of sessionRecords) store.put(record);
    });
  }
  if (fileRecords.length) {
    await withStore(FILE_STORE, "readwrite", async (store) => {
      for (const record of fileRecords) store.put(record);
    });
  }
  if (knowledgeRecords.length) {
    await withStore(KNOWLEDGE_STORE, "readwrite", async (store) => {
      for (const record of knowledgeRecords) store.put(record);
    });
  }
  try {
    for (const record of sessionRecords) await mirrorSessionToDirectory(record);
    await mirrorFilesToDirectory(fileRecords);
    for (const record of knowledgeRecords) {
      if (record.kind === "snapshot") await mirrorKnowledgeSnapshot(record);
      else await mirrorKnowledgeFile(record);
    }
  } catch (error) {
    reportDirectorySyncError(error);
  }
  return {
    sessions: sessionRecords.length,
    files: fileRecords.length,
    knowledge: knowledgeRecords.filter((record) => record.kind === "file").length,
  };
}
