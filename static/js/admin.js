(function () {
  "use strict";

  const state = { page: 1, pages: 1, search: "", sort: "created_desc", loading: false };
  const number = new Intl.NumberFormat("zh-CN");
  const compact = new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 });
  const sourceLabels = { desktop: "桌面版", web: "网页版", api: "历史接口" };
  const purposeLabels = {
    chat: "主对话", prompt_suggestion: "下一步建议",
    memory_extraction: "记忆提取", memory_repair: "记忆修复",
    memory_consolidation: "记忆整理", compaction: "自动压缩",
    manual_compaction: "手动压缩", team_delegate: "团队子调用",
    team_delegate_final: "团队总结", knowledge_parse: "知识解析",
    temp_prompt_refine: "提示词润色",
  };
  let searchTimer = null;
  let toastTimer = null;

  const byId = (id) => document.getElementById(id);

  async function api(path) {
    const response = await fetch(path, { headers: { Accept: "application/json" }, credentials: "same-origin" });
    if (response.status === 401) {
      window.location.href = "/login";
      throw new Error("登录已失效");
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.error || `请求失败 (${response.status})`);
    }
    return response.json();
  }

  function showToast(message) {
    const toast = byId("ops-toast");
    toast.textContent = message;
    toast.classList.add("show");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2600);
  }

  function setText(id, value) {
    byId(id).textContent = value;
  }

  function formatTimestamp(value) {
    if (!value) return "尚无记录";
    const normalized = String(value).includes("T") ? String(value) : `${String(value).replace(" ", "T")}Z`;
    const date = new Date(normalized);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(date);
  }

  function renderOverview(data) {
    setText("m-total-users", number.format(data.total_users));
    setText("m-new-users", number.format(data.registrations_today));
    setText("m-active-users", number.format(data.active_users_today));
    setText("m-calls", number.format(data.calls_today));
    setText("m-raw-tokens", compact.format(data.raw_tokens_today));
    setText("m-charged-units", compact.format(data.charged_units_today));
    setText("p-upstream", number.format(data.upstream_calls_today));
    setText("p-user", number.format(data.user_model_calls_today));
    setText("p-platform", number.format(data.platform_model_calls_today));
    setText("p-observed", compact.format(data.observed_tokens_today));
    setText("p-failed", number.format(data.failed_model_calls_today));
    setText("p-missing", number.format(data.missing_usage_calls_today));
    setText("p-unsettled", number.format(data.unsettled_model_calls_today));
    setText("q-free", number.format(data.free_units_today));
    setText("q-persistent", number.format(data.persistent_units_today));
    setText("q-login-users", number.format(data.login_users_today));
    setText("q-failed-logins", number.format(data.failed_logins_today));
    setText("today-date", data.date);
    setText("m-active-note", `其中登录活跃 ${number.format(data.login_users_today)}`);
    setText("m-quota-note", `持久额度 ${compact.format(data.persistent_units_today)}`);
    setText(
      "m-calls-note",
      `桌面 ${number.format(data.desktop_calls_today)} · 网页 ${number.format(data.web_calls_today)}`,
    );
  }

  function renderModelOperations(items) {
    const root = byId("provider-operation-list");
    root.replaceChildren();
    const grouped = new Map();
    (items || []).forEach((item) => {
      const key = `${item.purpose}:${item.billing_scope}`;
      const row = grouped.get(key) || {
        purpose: item.purpose, billing_scope: item.billing_scope,
        logical_calls: 0, upstream_calls: 0, raw_tokens: 0,
        failed: 0, missing: 0, unsettled: 0,
      };
      row.logical_calls += item.logical_calls;
      row.upstream_calls += item.upstream_calls;
      row.raw_tokens += item.raw_tokens;
      if (item.status === "failed") row.failed += item.logical_calls;
      if (item.status === "missing_usage") row.missing += item.logical_calls;
      if (item.status === "started") row.unsettled += item.logical_calls;
      grouped.set(key, row);
    });
    const rows = [...grouped.values()].sort((a, b) => (
      b.raw_tokens - a.raw_tokens || b.upstream_calls - a.upstream_calls
    ));
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "empty-copy";
      empty.textContent = "新调用流水启用后，这里会显示真实供应商请求。";
      root.appendChild(empty);
      return;
    }
    rows.forEach((item) => {
      const row = document.createElement("article");
      row.className = "provider-operation";
      const name = document.createElement("strong");
      name.textContent = purposeLabels[item.purpose] || item.purpose;
      const value = document.createElement("span");
      value.textContent = `${number.format(item.upstream_calls)} 次`;
      const meta = document.createElement("small");
      const scope = item.billing_scope === "user" ? "用户计费" : "平台辅助";
      meta.textContent = `${scope} · ${compact.format(item.raw_tokens)} Token · 失败 ${number.format(item.failed)} · 缺失 ${number.format(item.missing)} · 未结算 ${number.format(item.unsettled)}`;
      row.append(name, value, meta);
      root.appendChild(row);
    });
  }

  function renderTimeline(data) {
    const chart = byId("timeline-chart");
    chart.replaceChildren();
    const maxUsage = Math.max(1, ...data.series.map((item) => item.charged_units));
    const maxActive = Math.max(1, ...data.series.map((item) => item.active_users));
    data.series.forEach((item) => {
      const day = document.createElement("div");
      day.className = "timeline-day";
      day.style.setProperty("--usage-height", `${Math.max(item.charged_units ? 2 : 0, (item.charged_units / maxUsage) * 94)}%`);
      day.style.setProperty("--usage-scale", Math.max(item.charged_units ? 0.02 : 0, item.charged_units / maxUsage));
      day.style.setProperty("--active-height", `${Math.max(item.active_users ? 2 : 0, (item.active_users / maxActive) * 94)}%`);
      day.dataset.tip = `${item.date}\n活跃 ${number.format(item.active_users)} · 请求 ${number.format(item.calls)}\n桌面 ${number.format(item.desktop_calls)} · 网页 ${number.format(item.web_calls)} · API ${number.format(item.api_calls)}\n扣减 ${number.format(item.charged_units)}`;
      const bar = document.createElement("i");
      bar.className = "usage-bar";
      const mark = document.createElement("b");
      mark.className = "active-mark";
      day.append(bar, mark);
      chart.appendChild(day);
    });
    setText("scale-max", compact.format(maxUsage));
    setText("timeline-start", data.start_date.slice(5));
    setText("timeline-end", data.end_date.slice(5));
  }

  function renderModels(models) {
    const root = byId("model-list");
    root.replaceChildren();
    if (!models.length) {
      const empty = document.createElement("p");
      empty.className = "empty-copy";
      empty.textContent = "最近 30 天还没有模型用量记录。";
      root.appendChild(empty);
      return;
    }
    const max = Math.max(1, ...models.map((item) => item.charged_units));
    models.forEach((item) => {
      const row = document.createElement("article");
      row.className = "model-row";
      const name = document.createElement("strong");
      name.textContent = item.model;
      const value = document.createElement("span");
      value.textContent = compact.format(item.charged_units);
      const meta = document.createElement("small");
      meta.textContent = `${item.tier || "未分层"} · ${number.format(item.calls)} 次请求 · 原始 ${compact.format(item.raw_tokens)}`;
      const meter = document.createElement("div");
      meter.className = "model-meter";
      const fill = document.createElement("i");
      fill.style.setProperty("--meter-width", `${(item.charged_units / max) * 100}%`);
      meter.appendChild(fill);
      row.append(name, value, meta, meter);
      root.appendChild(row);
    });
  }

  function renderSources(sources) {
    const bySource = Object.fromEntries((sources || []).map((item) => [item.source, item]));
    ["desktop", "web"].forEach((source) => {
      const item = bySource[source] || { calls: 0, raw_tokens: 0, charged_units: 0 };
      setText(`source-${source}`, number.format(item.calls));
      const root = byId(`source-${source}`).closest("span");
      root.title = `${sourceLabels[source]}：原始 ${number.format(item.raw_tokens)} Token，扣减 ${number.format(item.charged_units)}`;
    });
  }

  function accountCell(item) {
    const cell = document.createElement("div");
    cell.className = "account-cell";
    const email = document.createElement("strong");
    email.textContent = item.email;
    const id = document.createElement("small");
    id.textContent = item.id.slice(0, 12);
    cell.append(email, id);
    return cell;
  }

  function addCell(row, content, className) {
    const cell = document.createElement("td");
    if (className) cell.className = className;
    if (content instanceof Node) cell.appendChild(content);
    else cell.textContent = content;
    row.appendChild(cell);
    return cell;
  }

  function renderUsers(data) {
    const body = byId("users-body");
    body.replaceChildren();
    state.page = data.page;
    state.pages = data.pages;
    if (!data.items.length) {
      const row = document.createElement("tr");
      const cell = addCell(row, state.search ? "没有匹配的注册邮箱。" : "还没有注册用户。", "empty-cell");
      cell.colSpan = 7;
      body.appendChild(row);
    } else {
      data.items.forEach((item) => {
        const row = document.createElement("tr");
        addCell(row, accountCell(item));
        addCell(row, formatTimestamp(item.created_at));
        addCell(row, formatTimestamp(item.last_active_at));
        addCell(row, number.format(item.raw_tokens_today), "num");
        addCell(row, number.format(item.raw_tokens_30d), "num");
        addCell(row, number.format(item.bonus_balance), "num");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "detail-button";
        button.textContent = "查看";
        button.addEventListener("click", () => openUser(item.id));
        addCell(row, button);
        body.appendChild(row);
      });
    }
    setText("users-count", `共 ${number.format(data.total)} 个账户`);
    setText("page-state", `${data.page} / ${data.pages}`);
    byId("prev-page").disabled = data.page <= 1;
    byId("next-page").disabled = data.page >= data.pages;
  }

  async function loadUsers() {
    if (state.loading) return;
    state.loading = true;
    const params = new URLSearchParams({
      page: String(state.page), page_size: "25", sort: state.sort, search: state.search,
    });
    try {
      renderUsers(await api(`/api/admin/users?${params}`));
    } catch (error) {
      showToast(error.message);
    } finally {
      state.loading = false;
    }
  }

  function eventRow(name, value, time) {
    const row = document.createElement("div");
    row.className = "event-row";
    [name, value, formatTimestamp(time)].forEach((text) => {
      const span = document.createElement("span");
      span.textContent = text;
      row.appendChild(span);
    });
    return row;
  }

  async function openUser(userId) {
    const dialog = byId("user-dialog");
    const body = byId("detail-body");
    body.textContent = "正在读取用户账本…";
    dialog.showModal();
    try {
      const user = await api(`/api/admin/users/${encodeURIComponent(userId)}`);
      body.replaceChildren();
      const identity = document.createElement("div");
      identity.className = "detail-identity";
      const email = document.createElement("strong");
      email.textContent = user.email;
      const id = document.createElement("span");
      id.textContent = user.id;
      identity.append(email, id);

      const grid = document.createElement("div");
      grid.className = "detail-grid";
      [
        ["注册时间", formatTimestamp(user.created_at)],
        ["今日原始 Token", number.format(user.raw_tokens_today || 0)],
        ["持久额度余额", number.format(user.bonus_balance || 0)],
      ].forEach(([label, value]) => {
        const item = document.createElement("div");
        const term = document.createElement("span");
        term.textContent = label;
        const metric = document.createElement("strong");
        metric.textContent = value;
        item.append(term, metric);
        grid.appendChild(item);
      });
      body.append(identity, grid);

      const title = document.createElement("h3");
      title.className = "detail-section-title";
      title.textContent = "最近用量事件";
      body.appendChild(title);
      const events = document.createElement("div");
      events.className = "event-list";
      if (!user.recent_usage.length) events.appendChild(eventRow("尚无模型用量", "—", ""));
      user.recent_usage.forEach((item) => events.appendChild(eventRow(
        `${sourceLabels[item.source] || "未知来源"} · ${item.model || "未知模型"}`,
        `${number.format(item.raw_tokens)} / 扣减 ${number.format(item.charged_units)}`,
        item.created_at,
      )));
      body.appendChild(events);

      const auditTitle = document.createElement("h3");
      auditTitle.className = "detail-section-title";
      auditTitle.textContent = "最近供应商调用";
      body.appendChild(auditTitle);
      const audits = document.createElement("div");
      audits.className = "event-list";
      if (!user.recent_model_calls.length) audits.appendChild(eventRow("尚无调用流水", "—", ""));
      user.recent_model_calls.forEach((item) => audits.appendChild(eventRow(
        `${purposeLabels[item.purpose] || item.purpose} · ${item.billing_scope === "user" ? "用户计费" : "平台辅助"}`,
        `${number.format(item.attempt_count)} 次 / ${number.format(item.raw_tokens)} Token / ${item.status}`,
        item.started_at,
      )));
      body.appendChild(audits);
    } catch (error) {
      body.textContent = error.message;
    }
  }

  async function loadDashboard() {
    byId("refresh-btn").disabled = true;
    try {
      const [overview, trends] = await Promise.all([
        api("/api/admin/overview"), api("/api/admin/trends?days=30"),
      ]);
      renderOverview(overview);
      renderTimeline(trends);
      renderModels(trends.models);
      renderSources(trends.sources);
      renderModelOperations(trends.model_operations);
      setText("as-of-time", new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
      }).format(new Date()));
      await loadUsers();
    } catch (error) {
      showToast(error.message);
    } finally {
      byId("refresh-btn").disabled = false;
    }
  }

  byId("refresh-btn").addEventListener("click", loadDashboard);
  byId("prev-page").addEventListener("click", () => { if (state.page > 1) { state.page -= 1; loadUsers(); } });
  byId("next-page").addEventListener("click", () => { if (state.page < state.pages) { state.page += 1; loadUsers(); } });
  byId("user-sort").addEventListener("change", (event) => { state.sort = event.target.value; state.page = 1; loadUsers(); });
  byId("user-search").addEventListener("input", (event) => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => { state.search = event.target.value.trim(); state.page = 1; loadUsers(); }, 250);
  });
  byId("detail-close").addEventListener("click", () => byId("user-dialog").close());
  byId("user-dialog").addEventListener("click", (event) => {
    if (event.target === byId("user-dialog")) byId("user-dialog").close();
  });

  loadDashboard();
}());
