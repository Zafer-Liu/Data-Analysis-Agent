// Compatibility data-preview modal with lazy table loading.
// Multi-source aware: tables grouped by source with sheet-count badge.
import { esc } from "../core/dom.js";
import { $, state } from "../core/runtime.js";
import { closeOverlay, openOverlay, toast } from "../core/overlay.js";

  let _currentTableMeta = null;
  let selectedTables = new Map();

  function _tableKey(tableMeta) {
    return `${tableMeta.source_id || ""}:${tableMeta.name}`;
  }

  function _contextTables() {
    const ctx = state.analysisContext;
    if (!ctx) return [];
    if (Array.isArray(ctx.tables)) return ctx.tables;
    return ctx.table ? [ctx] : []; // backward compatibility with single-table context
  }

  function _hydrateSelectionFromContext() {
    selectedTables = new Map();
    if (!state._previewData?.requires_table_selection) return;
    for (const ctxTable of _contextTables()) {
      const meta = state._previewData?.tables?.find(
        tb => tb.source_id === ctxTable.source_id && tb.name === ctxTable.table
      );
      if (meta) selectedTables.set(_tableKey(meta), meta);
    }
    if (!selectedTables.size) {
      for (const meta of state._previewData?.tables || []) {
        if (meta.selected_for_analysis) selectedTables.set(_tableKey(meta), meta);
      }
    }
  }

  // ── Cache invalidation ────────────────────────────────────────────────────
  function invalidate() {
    state._previewData  = null;
    state._previewCache = {};
    state._previewSid   = null;
    _currentTableMeta = null;
  }

  function _syncContextControls(tableMeta) {
    if (tableMeta !== undefined) _currentTableMeta = tableMeta || null;
    const btn = $("preview-use-table");
    const status = $("preview-context-status");
    const requiresSelection = Boolean(state._previewData?.requires_table_selection);
    if (state._previewData?.requires_table_selection === false) {
      selectedTables.clear();
      state.analysisContext = null;
    }
    if (btn) btn.classList.toggle('hidden', !requiresSelection);
    if (status) status.classList.toggle('hidden', !requiresSelection);
    if (!requiresSelection) return;
    const count = selectedTables.size;
    if (btn) {
      btn.disabled = count === 0;
      btn.textContent = count ? `使用已选 ${count} 张表分析` : "请先选择表";
    }
    if (!status) return;
    status.textContent = count
      ? `已选择 ${count} 张表，可继续切换预览并多选`
      : "点击表名前的方框选择一张或多张分析表";
  }

  async function useSelectedTablesForAnalysis() {
    if (!selectedTables.size) return;
    const tables = [...selectedTables.values()].map(tb => ({
      source_id: tb.source_id || "",
      source_name: tb.source_name || "",
      table: tb.name,
    }));
    const sqlSourceIds = [...new Set((state._previewData?.tables || [])
      .filter(tb => tb.selectable_for_analysis).map(tb => tb.source_id).filter(Boolean))];
    try {
      await Promise.all(sqlSourceIds.map(async sourceId => {
        const selected = tables.filter(item => item.source_id === sourceId).map(item => item.table);
        const response = await fetch(`/api/session/${state.SID}/sources/${sourceId}/analysis-tables`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tables: selected }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || "保存 SQL 分析表失败");
      }));
    } catch (error) {
      toast(error.message || "保存 SQL 分析表失败", "error");
      return;
    }
    state.analysisContext = { tables };
    _syncContextControls();
    toast(`已限定后续分析仅使用选中的 ${tables.length} 张表`, "ok");
    closeOverlay("ov-schema");
  }

  function _toggleTableSelection(tableMeta, selectBtn) {
    const key = _tableKey(tableMeta);
    if (selectedTables.has(key)) selectedTables.delete(key);
    else selectedTables.set(key, tableMeta);
    const selected = selectedTables.has(key);
    selectBtn.classList.toggle("selected", selected);
    selectBtn.setAttribute("aria-pressed", String(selected));
    selectBtn.title = selected ? "取消选择" : "选择用于分析";
    _syncContextControls();
  }

  // ── Drag-to-resize splitter ───────────────────────────────────────────────
  function _initResizeHandle() {
    const handle  = $("preview-resize-handle");
    const sidebar = $("preview-sidebar");
    if (!handle || !sidebar) return;

    let dragging = false, startX = 0, startW = 0;

    handle.addEventListener("mousedown", (e) => {
      dragging = true;
      startX = e.clientX;
      startW = sidebar.getBoundingClientRect().width;
      handle.classList.add("dragging");
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      e.preventDefault();
    });

    document.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const delta = e.clientX - startX;
      const newW  = Math.max(120, Math.min(420, startW + delta));
      sidebar.style.width = newW + "px";
    });

    document.addEventListener("mouseup", () => {
      if (!dragging) return;
      dragging = false;
      handle.classList.remove("dragging");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    });
  }

  // ── Open preview ──────────────────────────────────────────────────────────
  function openSchemaView() {
    openOverlay("ov-schema");
    _initResizeHandle();
    _hydrateSelectionFromContext();
    _syncContextControls(null);

    if (state._previewData && state._previewSid === state.SID && state._previewData.tables?.length) {
      _renderSidebar(state._previewData.tables);
      const first = state._previewData.tables[0];
      _syncContextControls(first);
      const cacheKey = _cacheKey(first);
      if (state._previewCache[cacheKey]) {
        _renderTable(state._previewCache[cacheKey]);
      } else {
        _renderSkeleton(first);
        _loadTable(first);
      }
      return;
    }
    _loadPreview();
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function _cacheKey(tb) {
    return `${tb.source_id || ""}:${tb.name}`;
  }

  // ── Render left sidebar ───────────────────────────────────────────────────
  function _renderSidebar(tables) {
    const tabs  = $("preview-tabs");
    const title = $("preview-title");

    // Title in header — always show sheet count
    const sourceNames = [...new Set(tables.map(t => t.source_name).filter(Boolean))];
    const sourceName  = state._previewData.source_name || sourceNames[0] || "";
    title.textContent = sourceNames.length > 1
      ? `数据预览 · ${sourceNames.length} 个数据源 · 共 ${tables.length} 张表`
      : `数据预览 · ${sourceName} · 共 ${tables.length} 张表`;

    tabs.innerHTML = "";
    const multiSource = sourceNames.length > 1;
    let currentSource = null;
    let sourceCount   = 0;   // sheets in current source group
    let groupEl       = null;

    tables.forEach((tb, i) => {
      // New source group — close old, open new
      if (multiSource && tb.source_name !== currentSource) {
        // Backfill badge into previous group header
        if (groupEl && sourceCount > 0) {
          const badge = groupEl.querySelector(".preview-tab-group-badge");
          if (badge) badge.textContent = `${sourceCount} 张表`;
        }

        currentSource = tb.source_name;
        sourceCount   = 0;

        groupEl = document.createElement("div");
        groupEl.className = "preview-tab-group";
        groupEl.innerHTML = `
          <span>${esc(tb.source_name)}</span>
          <span class="preview-tab-group-badge">…</span>`;
        tabs.appendChild(groupEl);
      }

      sourceCount++;

      const row = document.createElement("div");
      row.className = "preview-tab-row";

      const tab = document.createElement("button");
      tab.className = "preview-tab" + (i === 0 ? " active" : "");
      tab.dataset.idx = i;
      const rowHint = tb.total_rows != null
        ? tb.total_rows.toLocaleString()
        : "";
      tab.innerHTML = `
        <span style="overflow:hidden;text-overflow:ellipsis;flex:1">${esc(tb.name)}</span>
        ${rowHint ? `<span class="preview-tab-rows">${rowHint}</span>` : ""}`;
      tab.title = tb.name + (rowHint ? ` (${rowHint} 行)` : "");
      tab.addEventListener("click", () => _switchTab(i, tab));
      if (tb.selectable_for_analysis) {
        const selectBtn = document.createElement("button");
        const selected = selectedTables.has(_tableKey(tb));
        selectBtn.className = "preview-tab-select" + (selected ? " selected" : "");
        selectBtn.type = "button";
        selectBtn.setAttribute("aria-pressed", String(selected));
        selectBtn.setAttribute("aria-label", `选择 ${tb.name} 用于分析`);
        selectBtn.title = selected ? "取消选择" : "选择用于分析";
        selectBtn.innerHTML = '<span aria-hidden="true">✓</span>';
        selectBtn.addEventListener("click", () => _toggleTableSelection(tb, selectBtn));
        row.append(selectBtn);
      }
      row.append(tab);
      tabs.appendChild(row);
    });

    // Backfill last group badge
    if (groupEl && sourceCount > 0) {
      const badge = groupEl.querySelector(".preview-tab-group-badge");
      if (badge) badge.textContent = `${sourceCount} 张表`;
    }

    // (sheet count is already shown in the title for both single and multi-source)
  }

  // ── Load all previews ─────────────────────────────────────────────────────
  async function _loadPreview() {
    const wrap = $("preview-table-wrap");
    const foot = $("preview-footer");
    wrap.innerHTML = `<div class="preview-loading">加载中…</div>`;
    if (foot) foot.textContent = "";
    invalidate();

    const r = await fetch(`/api/session/${state.SID}/preview`);
    if (!r.ok) {
      wrap.innerHTML = `<div class="preview-loading" style="color:#ef4444">暂无可预览数据，请先连接或激活数据源</div>`;
      return;
    }
    state._previewData = await r.json();
    state._previewSid  = state.SID;
    _hydrateSelectionFromContext();

    const tables = state._previewData.tables || [];
    if (!tables.length) {
      wrap.innerHTML = `<div class="preview-loading">暂无数据</div>`;
      return;
    }

    _renderSidebar(tables);
    await _loadTable(tables[0]);
  }

  // ── Switch tab ────────────────────────────────────────────────────────────
  function _switchTab(idx, clickedBtn) {
    $("preview-tabs").querySelectorAll(".preview-tab")
      .forEach(b => b.classList.toggle("active", b === clickedBtn));
    const tableMeta = state._previewData.tables[idx];
    _syncContextControls(tableMeta);
    _loadTable(tableMeta);
  }

  // ── Load one table ────────────────────────────────────────────────────────
  async function _loadTable(tableMeta) {
    const wrap = $("preview-table-wrap");
    const key  = _cacheKey(tableMeta);
    _syncContextControls(tableMeta);
    if (state._previewCache[key]) { _renderTable(state._previewCache[key]); return; }

    _renderSkeleton(tableMeta);

    const params = new URLSearchParams({ table: tableMeta.name });
    if (tableMeta.source_id) params.set("source_id", tableMeta.source_id);

    const r = await fetch(`/api/session/${state.SID}/preview-table?${params}`);
    if (!r.ok) {
      wrap.innerHTML = `<div class="preview-loading" style="color:#ef4444">加载失败</div>`;
      return;
    }
    const data = await r.json();
    state._previewCache[key] = data;
    _renderTable(data);
  }

  // ── Skeleton (while loading) ──────────────────────────────────────────────
  function _renderSkeleton(tableMeta) {
    const wrap = $("preview-table-wrap");
    const foot = $("preview-footer");
    const cols = tableMeta.columns || [];
    let html = '<table class="preview-table"><thead><tr>';
    html += '<th class="preview-rn">#</th>';
    html += cols.map(c => `<th title="${esc(c)}">${esc(c)}</th>`).join("");
    html += `</tr></thead><tbody><tr>
      <td colspan="${cols.length + 1}" style="text-align:center;padding:24px;color:#999">
        加载中…
      </td></tr></tbody></table>`;
    wrap.innerHTML = html;
    if (foot) foot.textContent = "";
  }

  // ── Render data table ─────────────────────────────────────────────────────
  function _renderTable(table) {
    const wrap  = $("preview-table-wrap");
    const foot  = $("preview-footer");
    const shown = (table.rows || []).length;
    const total = table.total_rows ?? shown;

    let html = '<table class="preview-table"><thead><tr>';
    html += '<th class="preview-rn">#</th>';
    html += (table.columns || []).map(c => `<th title="${esc(c)}">${esc(c)}</th>`).join("");
    html += "</tr></thead><tbody>";
    (table.rows || []).forEach((row, i) => {
      html += `<tr><td class="preview-rn">${i + 1}</td>`;
      html += row.map(cell => {
        const s = esc(String(cell ?? ""));
        return `<td title="${s}">${s}</td>`;
      }).join("");
      html += "</tr>";
    });
    html += "</tbody></table>";
    wrap.innerHTML = html;

    if (foot) {
      const cols = (table.columns || []).length;
      foot.textContent = total > shown
        ? `${cols} 列 · 显示 ${shown} / ${total.toLocaleString()} 行`
        : `${cols} 列 · ${total.toLocaleString()} 行`;
    }
  }

  const useBtn = $("preview-use-table");
  if (useBtn) useBtn.addEventListener("click", useSelectedTablesForAnalysis);

  export { invalidate, openSchemaView, useSelectedTablesForAnalysis };
