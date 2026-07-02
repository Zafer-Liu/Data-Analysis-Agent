// Check for updates via GitHub Releases.
import { $, esc } from "../core/dom.js";

function _fmtSize(bytes) {
  if (!bytes) return "";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export async function runUpdate() {
  const btn       = $("update-btn");
  const stateEl   = $("update-state");
  const versionEl = $("update-version-info");
  const assetsEl  = $("update-assets");
  const outEl     = $("update-output");
  const dlBtn     = $("update-download-btn");

  btn.disabled = true;
  versionEl.classList.add("hidden");
  assetsEl.classList.add("hidden");
  outEl.classList.add("hidden");
  dlBtn.classList.add("hidden");
  stateEl.className = "update-state update-loading";
  stateEl.innerHTML = `<span class="update-spinner"></span><span class="update-state-text">${t("update.checking")}</span>`;

  try {
    const r = await fetch("/api/system/check-update", { signal: AbortSignal.timeout(20000) });
    const d = await r.json();

    if (!d.ok) {
      stateEl.className = "update-state update-err";
      stateEl.innerHTML = `<span class="update-state-icon">❌</span><span class="update-state-text">${t("update.check_fail")}</span>`;
      outEl.textContent = d.error || "";
      outEl.classList.remove("hidden");
      return;
    }

    // Show version comparison
    versionEl.innerHTML = `<div class="update-ver-row"><span>${t("update.current")}</span><strong>${esc(d.current_version)}</strong></div>`
      + `<div class="update-ver-row"><span>${t("update.latest")}</span><strong>${esc(d.latest_version)}</strong></div>`;
    versionEl.classList.remove("hidden");

    if (!d.has_update) {
      stateEl.className = "update-state update-ok";
      stateEl.innerHTML = `<span class="update-state-icon">✅</span><span class="update-state-text">${t("update.ok_latest")}</span>`;
    } else {
      stateEl.className = "update-state update-ok";
      stateEl.innerHTML = `<span class="update-state-icon">🆕</span><span class="update-state-text">${t("update.new_version")}</span>`;

      // Show download assets
      if (d.assets && d.assets.length) {
        assetsEl.innerHTML = d.assets.map(a =>
          `<a class="update-asset-card" href="${esc(a.download_url)}" target="_blank" rel="noopener">`
          + `<span class="update-asset-name">📦 ${esc(a.name)}</span>`
          + `<span class="update-asset-size">${_fmtSize(a.size)}</span></a>`
        ).join("");
        assetsEl.classList.remove("hidden");
      }

      // Show release notes
      if (d.release_notes) {
        outEl.textContent = d.release_notes;
        outEl.classList.remove("hidden");
      }

      // Show "Go to Releases" button
      dlBtn.href = d.release_url || "";
      dlBtn.classList.remove("hidden");
    }
  } catch (e) {
    const isTimeout = e.name === "AbortError";
    const msg = isTimeout
      ? (t("update.req_timeout") || "Request timed out")
      : (t("update.req_fail") || "Request failed: ") + esc(String(e));
    stateEl.className = "update-state update-err";
    stateEl.innerHTML = `<span class="update-state-icon">❌</span><span class="update-state-text">${msg}</span>`;
  } finally {
    btn.disabled = false;
  }
}
