(function () {
  "use strict";

  /* ── i18n ────────────────────────────────────────────────────── */
  const _I18N = {
    zh: {
      back: "返回",
      manual_refresh: "手动刷新",
      seconds_30: "30 秒",
      minutes_1: "1 分钟",
      minutes_5: "5 分钟",
      minutes_10: "10 分钟",
      refresh: "刷新数据",
      save_layout: "💾 保存布局",
      saved: "💾 已保存",
      countdown: (s) => `${s}s 后刷新`,
      no_id: "未找到看板 ID",
      net_error: (m) => "网络错误：" + m,
      not_found: (s) => `看板不存在或已删除 (${s})`,
      created: (d) => "创建于 " + d,
      refreshed: (d) => "刷新于 " + d,
      n_charts: (n) => `${n} 个图表`,
      n_kpis: (n) => `${n} 个KPI`,
      chart: "图表",
      refresh_widget: "刷新此图表",
      expand_widget: "全屏查看",
      hide_widget: "隐藏图表",
      show_widget: "显示图表",
      loading: "加载中…",
      chart_not_ready: "图表尚未生成",
      chart_iframe_title: "Dashboard 图表",
      refresh_fail: "刷新失败",
      refresh_error: (t) => `"${t}" 刷新出错`,
      refreshed_ok: (t) => `"${t}" 已刷新`,
      timeout_single: "刷新超时（30s）",
      timeout_all: "刷新超时（60s）",
      layout_saved: "布局已保存 ✓",
      save_fail: (m) => "保存失败：" + m,
      session_invalid: "Session 无效",
      session_re_enter: " — 请重新输入 Session ID",
      refresh_fail_prefix: (m) => "刷新失败：" + m,
      data_refreshed: "数据已刷新 ✓",
      errors_count: (n) => `刷新完成，${n} 个图表出错`,
      close_fs: "关闭全屏",
      empty_title: "看板为空",
      empty_desc: "该看板没有任何图表组件",
      modal_title: "🔗 连接数据源",
      modal_desc: "刷新看板需要有效的对话 Session ID（聊天页面地址栏可找到），以便重新查询数据。",
      modal_placeholder: "输入 Session ID…",
      modal_cancel: "取消",
      modal_ok: "确认",
    },
    en: {
      back: "Back",
      manual_refresh: "Manual",
      seconds_30: "30s",
      minutes_1: "1 min",
      minutes_5: "5 min",
      minutes_10: "10 min",
      refresh: "Refresh",
      save_layout: "💾 Save Layout",
      saved: "💾 Saved",
      countdown: (s) => `Refresh in ${s}s`,
      no_id: "Dashboard ID not found",
      net_error: (m) => "Network error: " + m,
      not_found: (s) => `Dashboard not found or deleted (${s})`,
      created: (d) => "Created " + d,
      refreshed: (d) => "Refreshed " + d,
      n_charts: (n) => `${n} chart${n === 1 ? "" : "s"}`,
      n_kpis: (n) => `${n} KPI${n === 1 ? "" : "s"}`,
      chart: "Chart",
      refresh_widget: "Refresh this chart",
      expand_widget: "Fullscreen",
      hide_widget: "Hide chart",
      show_widget: "Show chart",
      loading: "Loading…",
      chart_not_ready: "Chart not yet generated",
      chart_iframe_title: "Dashboard chart",
      refresh_fail: "Refresh failed",
      refresh_error: (t) => `"${t}" refresh error`,
      refreshed_ok: (t) => `"${t}" refreshed`,
      timeout_single: "Refresh timeout (30s)",
      timeout_all: "Refresh timeout (60s)",
      layout_saved: "Layout saved ✓",
      save_fail: (m) => "Save failed: " + m,
      session_invalid: "Session invalid",
      session_re_enter: " — Please re-enter Session ID",
      refresh_fail_prefix: (m) => "Refresh failed: " + m,
      data_refreshed: "Data refreshed ✓",
      errors_count: (n) => `Refresh done, ${n} chart${n === 1 ? "" : "s"} with errors`,
      close_fs: "Close fullscreen",
      empty_title: "Dashboard empty",
      empty_desc: "This dashboard has no chart widgets",
      modal_title: "🔗 Connect Data Source",
      modal_desc: "A valid Session ID is needed to re-query data for this dashboard. You can find it in the chat page URL.",
      modal_placeholder: "Enter Session ID…",
      modal_cancel: "Cancel",
      modal_ok: "OK",
    },
  };
  function _detectLang() {
    try { return (localStorage.getItem("baa_lang") || navigator.language || "zh").startsWith("en") ? "en" : "zh"; }
    catch { return "zh"; }
  }
  const _lang = _detectLang();
  const T = _I18N[_lang] || _I18N.zh;

  /* ── helpers ─────────────────────────────────────────────────── */
  const $ = id => document.getElementById(id);
  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fmtDate(iso) {
    if (!iso) return "";
    try {
      const locale = _lang === "en" ? "en-US" : "zh-CN";
      return new Date(iso)
        .toLocaleString(locale, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
        .replace(/\//g, "-");
    } catch { return iso; }
  }
  function showToast(msg, type = "") {
    const e = $("db-toast");
    e.textContent = msg;
    e.className = "db-toast" + (type ? " " + type : "");
    e.classList.add("show");
    clearTimeout(e._timer);
    e._timer = setTimeout(() => e.classList.remove("show"), 2800);
  }

  /* ── state ───────────────────────────────────────────────────── */
  const dashboardId = location.pathname.split("/dashboard/")[1];
  const _mq = window.matchMedia("(max-width: 768px)");
  let compactDashboard = _mq.matches;
  let dashboard = null, grid = null;
  const _urlSid = new URLSearchParams(location.search).get("sid") || "";
  let sessionId = _urlSid || sessionStorage.getItem("baa_session_id") || "";
  if (_urlSid) sessionStorage.setItem("baa_session_id", _urlSid);

  let isDirty = false, isRefreshing = false, pendingRefreshResolve = null;

  /* ── Auto-refresh ────────────────────────────────────────────── */
  let _autoTimer = null, _autoRemaining = 0, _autoInterval = 0, _countdownTimer = null;

  function startAutoRefresh(seconds) {
    stopAutoRefresh();
    if (seconds <= 0) return;
    _autoInterval = seconds;
    _autoRemaining = seconds;
    const countdown = $("refresh-countdown");
    countdown.style.display = "";
    countdown.classList.add("active");

    _countdownTimer = setInterval(() => {
      _autoRemaining--;
      countdown.textContent = T.countdown(_autoRemaining);
      if (_autoRemaining <= 0) {
        _autoRemaining = _autoInterval;
        handleRefresh();
      }
    }, 1000);
  }

  function stopAutoRefresh() {
    clearInterval(_autoTimer);
    clearInterval(_countdownTimer);
    _autoTimer = _countdownTimer = null;
    _autoInterval = _autoRemaining = 0;
    const countdown = $("refresh-countdown");
    if (countdown) { countdown.style.display = "none"; countdown.classList.remove("active"); }
  }

  /* ── Apply i18n to static DOM elements ────────────────────────── */
  function applyStaticI18n() {
    const s = (id, text) => { const el = $(id); if (el) el.textContent = text; };
    const p = (id, text) => { const el = $(id); if (el) el.placeholder = text; };
    s("btn-back", "←"); $("btn-back").title = T.back;
    s("nav-brand-label", _lang === "en" ? "Analytics Agent" : "智析Agent");
    s("opt-manual", T.manual_refresh);
    s("opt-30s", T.seconds_30);
    s("opt-1m", T.minutes_1);
    s("opt-5m", T.minutes_5);
    s("opt-10m", T.minutes_10);
    s("btn-refresh-label", T.refresh);
    s("btn-save-layout", T.save_layout);
    s("empty-title", T.empty_title);
    s("empty-desc", T.empty_desc);
    s("sid-modal-title", T.modal_title);
    s("sid-modal-desc", T.modal_desc);
    p("sid-input", T.modal_placeholder);
    s("sid-cancel", T.modal_cancel);
    s("sid-ok", T.modal_ok);
  }

  /* ── DOM ready ───────────────────────────────────────────────── */
  window.addEventListener("DOMContentLoaded", async () => {
    applyStaticI18n();
    $("btn-back").addEventListener("click", () => {
      document.referrer && !document.referrer.includes("/dashboard/")
        ? history.back() : location.href = "/";
    });
    $("btn-refresh").addEventListener("click", handleRefresh);
    $("btn-save-layout").addEventListener("click", handleSaveLayout);
    $("sid-cancel").addEventListener("click", () => {
      $("sid-modal").style.display = "none";
      pendingRefreshResolve && (pendingRefreshResolve(null), pendingRefreshResolve = null);
    });
    $("sid-ok").addEventListener("click", () => {
      const val = $("sid-input").value.trim();
      $("sid-modal").style.display = "none";
      pendingRefreshResolve && (pendingRefreshResolve(val || null), pendingRefreshResolve = null);
    });
    $("auto-refresh-sel").addEventListener("change", e => {
      const secs = parseInt(e.target.value, 10);
      if (secs > 0) startAutoRefresh(secs); else stopAutoRefresh();
    });
    await loadDashboard();
  });

  /* ── Load dashboard ──────────────────────────────────────────── */
  async function loadDashboard() {
    if (!dashboardId) { showEmptyState(T.no_id); return; }
    let resp;
    try { resp = await fetch(`/api/dashboard/${dashboardId}`); }
    catch (e) { showEmptyState(T.net_error(e.message)); return; }
    if (!resp.ok) { showEmptyState(T.not_found(resp.status)); return; }
    dashboard = await resp.json();

    const brandLabel = _lang === "en" ? "Analytics Agent" : "智析Agent";
    $("db-name").textContent = dashboard.name || dashboardId;
    document.title = `${dashboard.name || "Dashboard"} — ${brandLabel}`;

    const meta = [];
    if (dashboard.created_at) meta.push(T.created(fmtDate(dashboard.created_at)));
    if (dashboard.refreshed_at) meta.push(T.refreshed(fmtDate(dashboard.refreshed_at)));
    $("db-meta").textContent = meta.join("  ·  ");
    $("db-title-main").textContent = dashboard.name || dashboardId;

    const subParts = [];
    if (dashboard.created_at) subParts.push(T.created(fmtDate(dashboard.created_at)));
    if (dashboard.refreshed_at) subParts.push(T.refreshed(fmtDate(dashboard.refreshed_at)));
    const widgets = dashboard.widgets || [];
    // Separate KPI cards from chart widgets
    const kpis = widgets.filter(w => w.chart_type === "KPI_Card");
    const charts = widgets.filter(w => w.chart_type !== "KPI_Card");
    subParts.push(T.n_charts(charts.length));
    if (kpis.length) subParts.push(T.n_kpis(kpis.length));
    $("db-title-sub").textContent = subParts.join("  ·  ");
    $("db-title-bar").style.display = "block";

    if (widgets.length === 0) { $("empty-state").style.display = "flex"; return; }

    // Build KPI strip
    buildKpiStrip(kpis);
    // Build chart grid
    if (charts.length > 0) buildGrid(charts);
    else if (kpis.length === 0) $("empty-state").style.display = "flex";
  }

  /* ── KPI Strip ───────────────────────────────────────────────── */
  function buildKpiStrip(kpis) {
    const strip = $("kpi-strip");
    if (!kpis.length) { strip.innerHTML = ""; return; }
    strip.innerHTML = kpis.map(w => {
      const v = w.kpi_value ?? "";
      const label = w.title || "";
      const sub = w.kpi_sub || "";
      const trend = w.kpi_trend ?? null;  // positive / negative number or null
      let trendHtml = "";
      if (trend !== null && trend !== "") {
        const up = parseFloat(trend) >= 0;
        trendHtml = `<div class="kpi-trend ${up ? "up" : "down"}">${up ? "▲" : "▼"} ${Math.abs(trend)}%</div>`;
      }
      return `<div class="kpi-card">
        <div class="kpi-label">${esc(label)}</div>
        <div class="kpi-value">${esc(String(v))}</div>
        ${sub ? `<div class="kpi-sub">${esc(sub)}</div>` : ""}
        ${trendHtml}
      </div>`;
    }).join("");
  }

  /* ── Grid ────────────────────────────────────────────────────── */
  const _hiddenWidgets = new Map();

  function buildGrid(widgets) {
    const wrap = $("db-grid-wrap");
    wrap.style.display = "block";
    const gridEl = $("grid");

    grid = GridStack.init({
      column: compactDashboard ? 1 : 12,
      cellHeight: compactDashboard ? 320 : 120,
      margin: compactDashboard ? 8 : 10,
      animate: !compactDashboard,
      float: false,
      staticGrid: compactDashboard,
      draggable: { handle: ".widget-header", scroll: false },
      resizable: { handles: "se" },
      disableOneColumnMode: true,
    }, gridEl);

    // Use addWidget({content, x, y, w, h, id}).
    // GridStack wraps content in: .grid-stack-item > .grid-stack-item-content > {content}
    // So content here is just the inner HTML (header + body), not another wrapper div.
    widgets.forEach(w => {
      grid.addWidget({
        id:      w.id,
        x:       compactDashboard ? 0 : (w.grid?.x ?? 0),
        y:       compactDashboard ? undefined : (w.grid?.y ?? 0),
        w:       compactDashboard ? 1 : (w.grid?.w ?? 6),
        h:       compactDashboard ? 1 : (w.grid?.h ?? 4),
        content: buildWidgetInnerHTML(w),
      });
      // Tag the .grid-stack-item-content so event delegation can find widget-id
      // GridStack sets gs-id on the outer .grid-stack-item; mirror it inward too.
      const outer = gridEl.querySelector(`.grid-stack-item[gs-id="${CSS.escape(w.id)}"]`);
      if (outer) {
        const inner = outer.querySelector(".grid-stack-item-content");
        if (inner) inner.setAttribute("data-widget-id", w.id);
      }
    });

    // Load chart iframes after DOM is fully built
    widgets.forEach(w => loadWidgetChart(w));

    grid.on("dragstop resizestop", () => setDirty(true));
    requestAnimationFrame(resizeWidgetCharts);

    // Dynamically switch between compact (1-col) and desktop (12-col) on resize
    _mq.addEventListener("change", e => {
      if (e.matches === compactDashboard) return;
      compactDashboard = e.matches;
      if (!grid || !dashboard) return;
      grid.column(compactDashboard ? 1 : 12, compactDashboard ? "moveScale" : "moveScale");
      grid.setStatic(compactDashboard);
      if (compactDashboard) grid.cellHeight(320);
      else grid.cellHeight(120);
    });
  }

  // Returns only the inner HTML (header + body) to be placed inside .grid-stack-item-content
  function buildWidgetInnerHTML(w) {
    const badge = (w.chart_type || "").replace(/_/g, " ");
    return `
      <div class="widget-header">
        <span class="widget-title">${esc(w.title || T.chart)}</span>
        <span class="widget-badge">${esc(badge)}</span>
        <div class="widget-actions">
          <button class="widget-btn" title="${esc(T.refresh_widget)}"
            data-action="refreshWidget" data-wid="${esc(w.id)}">↻</button>
          <button class="widget-btn" title="${esc(T.expand_widget)}" ${w.chart_id ? "" : "disabled"}
            data-action="expandWidget" data-wid="${esc(w.id)}"
            data-title="${esc(w.title || T.chart)}" data-cid="${esc(w.chart_id || "")}">⛶</button>
          <button class="widget-btn" title="${esc(T.hide_widget)}"
            data-action="toggleHide" data-wid="${esc(w.id)}">🙈</button>
        </div>
      </div>
      <div class="widget-body" id="wb-${esc(w.id)}">${w.error ? buildErrorHTML(w.error) : buildLoadingHTML()}</div>`;
  }

  function buildLoadingHTML() {
    return `<div class="widget-loading"><span class="spin">↻</span> ${esc(T.loading)}</div>`;
  }
  function buildErrorHTML(msg) {
    return `<div class="widget-error"><div class="widget-error-icon">⚠️</div><div class="widget-error-msg">${esc(msg)}</div></div>`;
  }
  function buildIframeHTML(chartId) {
    return `<iframe class="widget-iframe" src="/api/chart/${esc(chartId)}" loading="lazy" title="${esc(T.chart_iframe_title)}" sandbox="allow-scripts allow-same-origin" referrerpolicy="no-referrer"></iframe>`;
  }
  function loadWidgetChart(w) {
    const body = $(`wb-${w.id}`);
    if (!body) return;
    if (w.error) { body.innerHTML = buildErrorHTML(w.error); return; }
    if (!w.chart_id) { body.innerHTML = buildErrorHTML(T.chart_not_ready); return; }
    body.innerHTML = buildIframeHTML(w.chart_id);
  }
  function showEmptyState(msg) {
    const el = $("empty-state");
    if (msg) el.querySelector(".db-empty-title").textContent = msg;
    el.style.display = "flex";
  }
  function setDirty(val) {
    isDirty = val;
    const btn = $("btn-save-layout");
    btn.disabled = !val;
    if (val) { btn.classList.add("dirty"); btn.textContent = T.save_layout; }
    else { btn.classList.remove("dirty"); btn.textContent = T.saved; setTimeout(() => { btn.textContent = T.save_layout; }, 1800); }
  }

  /* ── Event delegation for widget buttons ─────────────────────── */
  document.addEventListener("click", e => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    const wid = btn.dataset.wid;
    if (action === "toggleHide") toggleHideWidget(wid);
    else if (action === "refreshWidget") refreshSingleWidget(wid);
    else if (action === "expandWidget") expandWidget(wid, btn.dataset.title, btn.dataset.cid);
  });

  /* ── Hide / show widget ───────────────────────────────────────── */
  function toggleHideWidget(widgetId) {
    const w = dashboard?.widgets?.find(x => x.id === widgetId);
    if (!w || !grid) return;
    const el = document.querySelector(`.grid-stack-item[gs-id="${CSS.escape(widgetId)}"]`);
    if (!el) return;
    if (!_hiddenWidgets.has(widgetId)) {
      const node = el.gridstackNode;
      _hiddenWidgets.set(widgetId, { x: node.x, y: node.y, w: node.w, h: node.h });
      grid.removeWidget(el, false);
      el.style.display = "none"; w._hidden = true; setDirty(true);
      const btn = el.querySelector("[data-action=toggleHide]");
      if (btn) { btn.textContent = "👁️"; btn.title = T.show_widget; }
    } else {
      const saved = _hiddenWidgets.get(widgetId);
      el.style.display = "";
      grid.addWidget(el, { x: saved.x, y: saved.y, w: saved.w, h: saved.h, id: widgetId });
      _hiddenWidgets.delete(widgetId); w._hidden = false; setDirty(true);
      const btn = el.querySelector("[data-action=toggleHide]");
      if (btn) { btn.textContent = "🙈"; btn.title = T.hide_widget; }
    }
  }

  /* ── Single widget refresh ────────────────────────────────────── */
  async function refreshSingleWidget(widgetId) {
    let sid = sessionId;
    if (!sid) { sid = await promptSessionId(); if (!sid) return; sessionId = sid; sessionStorage.setItem("baa_session_id", sid); }
    const w = dashboard?.widgets?.find(x => x.id === widgetId);
    if (!w) return;
    const body = $(`wb-${w.id}`);
    if (body) body.innerHTML = buildLoadingHTML();
    // Spin the widget's refresh button
    const btn = document.querySelector(`[data-action="refreshWidget"][data-wid="${CSS.escape(widgetId)}"]`);
    if (btn) { btn.style.animation = "spin .8s linear infinite"; btn.disabled = true; }
    try {
      const ctrl = new AbortController();
      const tid = setTimeout(() => ctrl.abort(), 30000);
      const resp = await fetch(`/api/dashboard/${dashboardId}/widget/${widgetId}/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid }),
        signal: ctrl.signal,
      });
      clearTimeout(tid);
      const data = await resp.json();
      if (!resp.ok) { showToast((data.error || T.refresh_fail), "err"); if (body) body.innerHTML = buildErrorHTML(data.error || T.refresh_fail); return; }
      if (data.error) {
        w.error = data.error;
        if (body) body.innerHTML = buildErrorHTML(data.error);
        showToast(T.refresh_error(w.title), "err");
      } else if (data.kpi_value !== undefined) {
        // KPI_Card widget
        w.kpi_value = data.kpi_value; w.kpi_sub = data.kpi_sub; w.kpi_trend = data.kpi_trend; w.error = null;
        buildKpiStrip(dashboard.widgets.filter(ww => ww.chart_type === "KPI_Card"));
        showToast(T.refreshed_ok(w.title), "ok");
      } else if (data.chart_id) {
        w.chart_id = data.chart_id; w.error = null;
        if (body) body.innerHTML = buildIframeHTML(data.chart_id);
        // Update expand button data-cid
        const expBtn = document.querySelector(`[data-action="expandWidget"][data-wid="${CSS.escape(widgetId)}"]`);
        if (expBtn) { expBtn.dataset.cid = data.chart_id; expBtn.disabled = false; }
        showToast(T.refreshed_ok(w.title), "ok");
      }
    } catch (e) {
      const msg = e.name === "AbortError" ? T.timeout_single : T.net_error(e.message);
      showToast(msg, "err");
      if (body) body.innerHTML = buildErrorHTML(msg);
    } finally {
      if (btn) { btn.style.animation = ""; btn.disabled = false; }
    }
  }

  /* ── Fullscreen expand ────────────────────────────────────────── */
  function expandWidget(widgetId, title, chartId) {
    if (!chartId) return;
    let fs = $("db-fullscreen");
    if (!fs) {
      fs = document.createElement("div");
      fs.id = "db-fullscreen"; fs.className = "db-fullscreen";
      fs.innerHTML = `<div class="db-fullscreen-header">
        <span class="db-fullscreen-title" id="fs-title"></span>
        <button class="btn-fs-close" id="fs-close" title="${esc(T.close_fs)}">✕</button>
      </div><div class="db-fullscreen-body" id="fs-body"></div>`;
      document.body.appendChild(fs);
      document.getElementById("fs-close").addEventListener("click", closeFullscreen);
      document.addEventListener("keydown", e => { if (e.key === "Escape") closeFullscreen(); });
    }
    document.getElementById("fs-title").textContent = title;
    document.getElementById("fs-body").innerHTML =
      `<iframe src="/api/chart/${esc(chartId)}" title="${esc(title || T.chart_iframe_title)}" sandbox="allow-scripts allow-same-origin" referrerpolicy="no-referrer"></iframe>`;
    fs.style.display = "flex";
    document.body.style.overflow = "hidden";
  }
  function closeFullscreen() {
    const fs = $("db-fullscreen");
    if (fs) { fs.style.display = "none"; document.getElementById("fs-body").innerHTML = ""; }
    document.body.style.overflow = "";
  }

  /* ── Save layout ──────────────────────────────────────────────── */
  async function handleSaveLayout() {
    if (!grid || !dashboard) return;
    const items = grid.getGridItems().map(el => {
      const node = el.gridstackNode;
      // gs-id is set directly on the grid-stack-item element
      const id = el.getAttribute("gs-id") || el.querySelector("[data-widget-id]")?.dataset?.widgetId || "";
      return { id, grid: { x: node.x, y: node.y, w: node.w, h: node.h } };
    });
    const containerW = $("grid").parentElement.getBoundingClientRect().width;
    try {
      const resp = await fetch(`/api/dashboard/${dashboardId}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ widgets: items, container_width: containerW }),
      });
      if (!resp.ok) throw new Error(resp.statusText);
      setDirty(false); showToast(T.layout_saved, "ok");
    } catch (e) { showToast(T.save_fail(e.message), "err"); }
  }

  /* ── Full refresh (all widgets) ───────────────────────────────── */
  async function handleRefresh() {
    if (isRefreshing) return;
    let sid = sessionId;
    if (!sid) { sid = await promptSessionId(); if (!sid) return; sessionId = sid; sessionStorage.setItem("baa_session_id", sid); }
    setRefreshingUI(true);
    // Reset auto-refresh countdown
    if (_autoInterval > 0) { _autoRemaining = _autoInterval; }
    try {
      const ctrl = new AbortController();
      const tid = setTimeout(() => ctrl.abort(), 60000);
      const resp = await fetch(`/api/dashboard/${dashboardId}/refresh`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid }),
        signal: ctrl.signal,
      });
      clearTimeout(tid);
      const data = await resp.json();
      if (!resp.ok) {
        if (resp.status === 404 || resp.status === 400) {
          showToast((data.error || T.session_invalid) + T.session_re_enter, "err");
          sessionId = ""; sessionStorage.removeItem("baa_session_id");
        } else showToast(T.refresh_fail_prefix(data.error || resp.statusText), "err");
        return;
      }
      const resultMap = {};
      for (const w of (data.widgets || [])) resultMap[w.id] = w;
      for (const w of (dashboard.widgets || [])) {
        if (w.chart_type === "KPI_Card") continue;  // handled separately
        const body = $(`wb-${w.id}`);
        if (!body) continue;
        const res = resultMap[w.id];
        if (!res) continue;
        if (res.error) { body.innerHTML = buildErrorHTML(res.error); }
        else if (res.chart_id) {
          body.innerHTML = buildIframeHTML(res.chart_id);
          w.chart_id = res.chart_id;
          const expBtn = document.querySelector(`[data-action="expandWidget"][data-wid="${CSS.escape(w.id)}"]`);
          if (expBtn) { expBtn.dataset.cid = res.chart_id; expBtn.disabled = false; }
        }
        w.error = res.error || null;
      }
      // Re-render KPI strip if backend returned updated KPI values
      const kpiResults = (data.kpi_widgets || []);
      if (kpiResults.length) {
        kpiResults.forEach(kw => {
          const w = dashboard.widgets.find(x => x.id === kw.id);
          if (w) { w.kpi_value = kw.kpi_value; w.kpi_sub = kw.kpi_sub; w.kpi_trend = kw.kpi_trend; }
        });
        buildKpiStrip(dashboard.widgets.filter(w => w.chart_type === "KPI_Card"));
      }
      const locale = _lang === "en" ? "en-US" : "zh-CN";
      const now = new Date().toLocaleString(locale, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).replace(/\//g, "-");
      const meta = [];
      if (dashboard.created_at) meta.push(T.created(fmtDate(dashboard.created_at)));
      meta.push(T.refreshed(now));
      $("db-meta").textContent = meta.join("  ·  ");
      const errors = (data.widgets || []).filter(w => w.error).length;
      if (errors > 0) showToast(T.errors_count(errors), "err");
      else showToast(T.data_refreshed, "ok");
    } catch (e) {
      showToast(e.name === "AbortError" ? T.timeout_all : T.net_error(e.message), "err");
    } finally {
      setRefreshingUI(false);
    }
  }

  function setRefreshingUI(on) {
    isRefreshing = on;
    const btn = $("btn-refresh"), icon = $("refresh-icon");
    btn.disabled = on;
    if (on) { icon.className = "spin"; icon.textContent = "↻"; }
    else { icon.className = ""; icon.textContent = "↻"; }
  }

  function promptSessionId() {
    return new Promise(resolve => {
      pendingRefreshResolve = resolve;
      $("sid-input").value = ""; $("sid-modal").style.display = "flex"; $("sid-input").focus();
      $("sid-input").onkeydown = e => { if (e.key === "Enter") $("sid-ok").click(); };
    });
  }

  /* ── Resize iframes after grid events ─────────────────────────── */
  function resizeWidgetCharts() {
    document.querySelectorAll(".grid-stack-item").forEach(item => {
      const iframe = item.querySelector(".widget-body iframe");
      if (iframe) { iframe.style.width = "100%"; iframe.style.height = "100%"; }
    });
  }
  // Bind after grid is potentially initialized
  window.addEventListener("load", () => {
    grid?.on("change resized dragstop added", () => requestAnimationFrame(resizeWidgetCharts));
  });
  window.addEventListener("resize", () => requestAnimationFrame(resizeWidgetCharts));

  /* ── Expose for inline data-action dispatch ───────────────────── */
  window._dbExpandWidget = expandWidget;
})();
