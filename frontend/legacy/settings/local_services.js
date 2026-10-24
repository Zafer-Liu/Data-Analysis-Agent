// Local service center. Connections and protocol adapters are separate choices.
import { createForecastDomain } from "./forecast.js";
import { _renderPanelHead } from "./shared.js";

const tr = (zh, en) => window.getLang?.() === "en" ? en : zh;
const fresh = () => ({ name: "", transport: "http", protocol: "http", base_url: "",
  health_path: "/health", request_path: "/predict", method: "POST", request_body: {}, timeout: 30 });
const DRAFT_KEY = "baa_local_service_draft_v1";
const DRAFT_FIELDS = ["name", "transport", "protocol", "base_url", "health_path",
  "request_path", "method", "timeout"];
const protocolName = key => ({ http: tr("自定义 HTTP", "Custom HTTP"), openai: tr("OpenAI 兼容", "OpenAI compatible"), timesfm: "TimesFM" })[key];

export function createLocalServicesDomain({ draw, onModelsChanged = () => {} }) {
  const { h, reactive } = window.Vue;
  const state = reactive({ services: [], forecast: {}, loaded: false, loading: false, busy: "",
    editor: null, body: "{}", token: "", clearToken: false, dirty: false,
    message: "", error: false, unavailable: false, models: {}, result: null, environment: false, connectionSettings: false });
  const forecast = createForecastDomain({ draw, onConfigured: (config, runtime) => {
    state.forecast = { ...config, can_stop: !!runtime.managed_running && runtime.instance?.base_url === config.base_url };
  } });

  function notify(text, error = false) {
    const parts = String(text || "").split(" / ");
    state.message = parts.length > 1 ? tr(parts[0], parts.slice(1).join(" / ")) : String(text || "");
    state.error = error;
  }
  function draftStorage() {
    try { return window.localStorage || null; } catch { return null; }
  }
  function persistDraft() {
    if (!state.editor) return;
    const storage = draftStorage();
    if (!storage) return;
    const editor = Object.fromEntries(DRAFT_FIELDS.map(key => [key, state.editor[key]]));
    if (state.editor.id) editor.id = state.editor.id;
    if (state.editor.token_configured) editor.token_configured = true;
    try {
      storage.setItem(DRAFT_KEY, JSON.stringify({
        editor, body: state.body, savedAt: new Date().toISOString(),
      }));
    } catch {
      // The service can still be saved even when browser storage is unavailable.
    }
  }
  function clearDraft() {
    try { draftStorage()?.removeItem(DRAFT_KEY); } catch { /* Storage is optional. */ }
  }
  function restoreDraft() {
    if (state.editor) return false;
    try {
      const parsed = JSON.parse(draftStorage()?.getItem(DRAFT_KEY) || "null");
      const source = parsed?.editor;
      if (!source || typeof source !== "object") return false;
      const editor = fresh();
      for (const key of DRAFT_FIELDS) {
        if (source[key] !== undefined) editor[key] = source[key];
      }
      if (typeof source.id === "string" && source.id) editor.id = source.id;
      if (source.token_configured === true) editor.token_configured = true;
      state.editor = editor;
      state.body = typeof parsed.body === "string" ? parsed.body : "{}";
      state.token = "";
      state.clearToken = false;
      state.dirty = true;
      state.result = null;
      notify(tr("已恢复未保存的服务草稿", "Restored an unsaved service draft"));
      return true;
    } catch {
      clearDraft();
      return false;
    }
  }
  function markDirty() {
    state.dirty = true;
    persistDraft();
  }
  async function request(path = "", body, method = "POST") {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 190000);
    try {
      const response = await fetch("/api/local-services" + path, {
        method: body === undefined ? "GET" : method, signal: controller.signal,
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const value = await response.json().catch(() => ({}));
      if (!response.ok || value.ok === false) {
        if (response.status === 404 && path === "") state.unavailable = true;
        throw new Error(value.message || tr("服务请求失败，请刷新后重试", "Service request failed; refresh and retry"));
      }
      return value;
    } catch (err) {
      if (err.name === "AbortError") throw new Error(tr("请求超时，请检查服务日志后重试", "Request timed out; check service logs before retrying"));
      throw err;
    } finally { clearTimeout(timer); }
  }
  async function refresh() {
    const data = await request();
    state.services = data.services;
    state.forecast = data.forecast;
    state.loaded = true;
  }
  async function load() {
    if (state.loading) return;
    state.loading = true;
    try {
      await refresh();
      restoreDraft();
    }
    catch (err) { notify(err.message, true); }
    finally { state.loading = false; draw(); }
  }
  function edit(service = null) {
    state.editor = service ? { ...service } : fresh();
    state.body = JSON.stringify(service?.request_body || {}, null, 2);
    state.token = ""; state.clearToken = false; state.dirty = false; state.result = null;
    notify(""); draw();
    queueMicrotask(() => document.getElementById("service-name")?.focus());
  }
  async function discardEditor() {
    if (state.dirty) {
      const ok = await window.BAA.ui?.confirm?.({ title: tr("放弃未保存修改", "Discard unsaved changes"),
        message: tr("当前服务的修改尚未保存，确认放弃？", "Discard the unsaved changes to this service?") });
      if (!ok) return;
    }
    clearDraft();
    state.editor = null; state.result = null; state.dirty = false; draw();
  }
  function bodyValue() {
    let value;
    try { value = JSON.parse(state.body); }
    catch { throw new Error(tr("请求内容不是有效 JSON，请检查后保存", "Request body is not valid JSON; correct it before saving")); }
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(tr("请求内容必须是 JSON 对象", "Request body must be a JSON object"));
    return value;
  }
  async function check(service) {
    const data = await request(`/${service.id}/test`, {});
    if (data.models) state.models[service.id] = data.models;
    notify(data.models ? tr(`连接成功 · 发现 ${data.models.length} 个模型`, `Connected · ${data.models.length} models found`)
      : tr(`连接成功 · ${data.elapsed_ms} ms`, `Connected · ${data.elapsed_ms} ms`));
    await refresh();
  }
  async function act(action, service = null, model = null) {
    if (state.busy) return;
    state.busy = `${action}:${service?.id || "editor"}`; notify(""); draw();
    try {
      if (action === "save" || action === "save-test") {
        const c = state.editor;
        const value = await request(c.id ? `/${c.id}` : "", {
          ...c, request_body: c.protocol === "http" ? bodyValue() : {},
          ...(state.token ? { token: state.token } : {}), clear_token: state.clearToken,
        }, c.id ? "PUT" : "POST");
        state.editor = value.service; state.token = ""; state.clearToken = false; state.dirty = false;
        state.body = JSON.stringify(value.service.request_body, null, 2);
        delete state.models[c.id];
        clearDraft();
        await refresh();
        notify(tr("服务已保存", "Service saved"));
        if (action === "save-test") await check(value.service);
      } else if (action === "test") {
        await check(service);
      } else if (action === "remove") {
        const activeForecast = state.forecast.enabled && state.forecast.service_id === service.id;
        const ok = await window.BAA.ui?.confirm?.({ title: tr("移除服务连接", "Remove service connection"),
          message: activeForecast
            ? tr(`移除「${service.name}」？当前预测连接会一并停用，已经运行的服务或容器不会停止。`, `Remove “${service.name}”? Its active forecast connection will be disabled; running services and containers keep running.`)
            : tr(`移除「${service.name}」？已经运行的服务或容器不会停止。`, `Remove “${service.name}”? Running services and containers keep running.`), danger: true });
        if (!ok) return;
        await request(`/${service.id}`, {}, "DELETE");
        if (state.editor?.id === service.id) {
          state.editor = null;
          clearDraft();
        }
        delete state.models[service.id]; state.result = null;
        await refresh(); await forecast.load(); notify(tr("服务连接已移除", "Service connection removed"));
      } else if (action === "activate") {
        await request(`/${service.id}/activate`, {});
        await refresh(); await forecast.load(); notify(tr("已设为当前预测服务", "Selected as the current forecast service"));
      } else if (action === "stop") {
        const ok = await window.BAA.ui?.confirm?.({ title: tr("停止模型", "Stop model"),
          message: tr(`停止「${service.name}」并释放其占用的显存？正在执行的预测可能中断，模型文件和连接配置会保留。`, `Stop “${service.name}” and release its GPU memory? Active predictions may be interrupted. Model files and connection settings are kept.`), danger: true });
        if (!ok) return;
        await request(`/${service.id}/stop`, { confirmed: true });
        await refresh(); await forecast.load();
        notify(tr("模型已停止，模型文件和连接配置已保留", "Model stopped; model files and connection settings kept"));
      } else if (action === "register") {
        const data = await request(`/${service.id}/models/register`, { model });
        notify(data.message || tr("模型已加入 LLM 列表", "Model added to the LLM list"));
        onModelsChanged();
      } else if (action === "invoke") {
        state.result = null;
        const data = await request(`/${service.id}/invoke`, { body: bodyValue() });
        const text = typeof data.response === "string" ? data.response : JSON.stringify(data.response, null, 2);
        state.result = { status: data.status_code, elapsed: data.elapsed_ms, text: text.slice(0, 12000), truncated: text.length > 12000 };
        notify(tr("请求已完成", "Request completed"));
      }
    } catch (err) {
      notify(err.message, true);
      if (action === "test" || action === "save-test") await refresh().catch(() => {});
    } finally { state.busy = ""; draw(); }
  }
  function control(key, zh, en, options = {}) {
    const c = state.editor;
    const props = { id: "service-" + key, value: c[key], disabled: !!state.busy,
      onInput: e => { c[key] = options.type === "number" ? Number(e.target.value) : e.target.value; markDirty(); draw(); } };
    const input = options.choices ? h("select", { ...props, onChange: props.onInput }, options.choices.map(([value, label]) => h("option", { value }, label)))
      : h("input", { ...props, type: options.type || "text", autocomplete: "off", spellcheck: false,
        ...(options.type === "number" ? { min: 5, max: 180 } : {}), placeholder: options.placeholder || "" });
    return h("div", { class: "local-service-field" }, [h("label", { for: props.id }, tr(zh, en)), input]);
  }
  function button(label, action, service, primary = false, extra = false, model = null) {
    return h("button", { type: "button", class: "btn-sm " + (primary ? "btn-sm-primary" : "btn-sm-ghost"),
      disabled: !!state.busy || extra, onClick: () => act(action, service, model) },
    state.busy === `${action}:${service?.id || "editor"}` ? tr("处理中…", "Working…") : label);
  }
  function renderEditor() {
    const c = state.editor;
    if (!c) return null;
    const saved = !!c.id && !state.dirty;
    return h("section", { class: "local-service-editor", "aria-labelledby": "service-editor-title" }, [
      h("div", { class: "local-service-section-head" }, [h("h4", { id: "service-editor-title" }, c.id ? tr("编辑服务", "Edit service") : tr("添加服务", "Add service")),
        h("button", { type: "button", class: "btn-sm btn-sm-ghost", disabled: !!state.busy, onClick: discardEditor }, tr("收起", "Close editor"))]),
      h("p", { class: "local-service-note" }, tr("编辑内容会自动保存为浏览器草稿；点击“仅保存”后才写入应用配置。认证密钥不会缓存。",
        "Edits are kept as a browser draft; choose Save only to write the app configuration. Authentication tokens are never cached.")),
      h("div", { class: "local-service-grid" }, [control("name", "服务名称", "Service name", { placeholder: tr("例如：本地 OCR", "e.g. Local OCR") }),
        control("protocol", "服务协议", "Service protocol", { choices: [["http", protocolName("http")], ["openai", protocolName("openai")], ["timesfm", tr("TimesFM 预测", "TimesFM forecasting")]] }),
        control("transport", "连接方式", "Connection type", { choices: [["http", "HTTP / HTTPS"], ["docker", tr("Docker 映射端口 / 服务名", "Docker port / service name")]] }),
        control("base_url", "服务地址", "Service URL", { placeholder: "http://127.0.0.1:8000" })]),
      h("p", { class: "local-service-note" }, c.protocol === "http"
        ? tr("可接入 OCR、向量检索或其他 HTTP 服务。先测试连通性，再手动发送请求；接入 Agent 自动调用仍需对应工具适配。", "Connect OCR, retrieval or other HTTP services. Test connectivity, then send a request manually; automatic Agent use requires a matching tool adapter.")
        : c.protocol === "openai" ? tr("支持提供 OpenAI 兼容接口的 Ollama、vLLM 等服务。测试后选择模型加入 LLM 列表。地址可包含 /v1。", "Connect an OpenAI-compatible endpoint such as Ollama or vLLM. Test, then add a discovered model to the LLM list. The URL may include /v1.")
          : tr("验证 TimesFM 接口后，可将此连接设为当前预测服务。", "Verify the TimesFM endpoint, then select it for forecasting.")),
      c.transport === "docker" ? h("p", { class: "local-service-note" }, tr("服务须已启动。填写宿主机映射端口；智析也在容器中时可填写同网络服务名。", "Start the service first. Use the host-mapped port, or a service name on the same Docker network.")) : null,
      c.protocol === "http" ? h("div", { class: "local-service-grid" }, [control("health_path", "健康检查路径（GET）", "Health-check path (GET)"),
        control("request_path", "调用路径", "Request path"),
        control("method", "请求方式", "Request method", { choices: [["POST", "POST"], ["GET", "GET"]] })]) : null,
      c.protocol === "http" && c.method === "POST" ? h("div", { class: "local-service-field" }, [
        h("label", { for: "service-body" }, tr("请求 JSON", "Request JSON")),
        h("textarea", { id: "service-body", rows: 5, value: state.body, disabled: !!state.busy, spellcheck: false,
          onInput: e => { state.body = e.target.value; markDirty(); draw(); } })]) : null,
      h("details", { class: "local-service-advanced" }, [h("summary", null, tr("认证与超时", "Authentication and timeout")),
        h("div", { class: "local-service-grid" }, [control("timeout", "超时（秒）", "Timeout (seconds)", { type: "number" }),
          h("div", { class: "local-service-field" }, [h("label", { for: "service-token" }, tr("Bearer 密钥（可选）", "Bearer token (optional)")),
            h("input", { id: "service-token", type: "password", autocomplete: "new-password", value: state.token,
              disabled: !!state.busy, placeholder: c.token_configured ? tr("已保存，留空保留", "Saved; leave empty to keep") : "",
              onInput: e => { state.token = e.target.value; markDirty(); draw(); } })])]),
        c.token_configured ? h("label", { class: "forecast-check" }, [h("input", { type: "checkbox", checked: state.clearToken,
          disabled: !!state.busy, onChange: e => { state.clearToken = e.target.checked; state.dirty = true; draw(); } }), tr("移除已保存密钥", "Remove saved token")]) : null]),
      h("div", { class: "local-service-actions" }, [button(tr("保存并测试", "Save and test"), "save-test", null, true, !c.name.trim() || !c.base_url.trim()),
        button(tr("仅保存", "Save only"), "save", null, false, !c.name.trim() || !c.base_url.trim()),
        c.protocol === "http" ? button(tr("发送请求", "Send request"), "invoke", c, false, !saved) : null]),
      c.protocol === "http" ? h("p", { class: "local-service-note" }, saved ? tr("发送请求会调用已保存的接口。", "Send request calls the saved endpoint.") : tr("保存修改后可发送请求。", "Save changes before sending a request.")) : null,
      state.result ? h("div", { class: "local-service-response" }, [h("p", null, `HTTP ${state.result.status} · ${state.result.elapsed} ms`),
        h("pre", { tabindex: 0, "aria-label": tr("服务响应", "Service response") }, state.result.text),
        state.result.truncated ? h("p", { class: "local-service-note" }, tr("响应展示已截断为 12000 字符。", "Response display truncated to 12,000 characters.")) : null]) : null,
    ]);
  }
  function render() {
    const active = state.forecast.enabled ? state.forecast.service_id : "";
    const legacy = state.forecast.base_url && !state.forecast.service_id;
    return h("section", { class: "app-settings-panel local-services-panel" }, [
      _renderPanelHead(tr("本地模型与服务", "Local models and services"), tr("请填写已运行服务的地址；智析不会自动扫描本机端口。", "Enter the address of a running service; the app does not scan local ports."), [
        h("button", { type: "button", class: "btn-sm btn-sm-primary", disabled: !!state.busy || !!state.editor || state.unavailable,
          onClick: () => edit() }, tr("添加服务", "Add service"))]),
      state.unavailable ? h("p", { role: "status", class: "local-service-note" }, tr("请在桌面版或本地部署中管理本机服务。", "Manage local services in the desktop app or a local deployment.")) : h("div", { class: "local-services-content" }, [
        h("div", { class: "local-service-section-head" }, [h("h4", null, tr("服务连接", "Saved services")),
          h("button", { type: "button", class: "btn-sm btn-sm-ghost", disabled: state.loading || !!state.busy, onClick: load }, state.loading ? tr("读取中…", "Loading…") : tr("读取已保存服务", "Reload saved services"))]),
        !state.loaded ? h("p", { role: "status", class: "local-service-note" }, tr("正在读取配置…", "Loading settings…")) : null,
        ...state.services.map(service => h("article", { class: "local-service-row", key: service.id }, [
          h("div", { class: "local-service-row-main" }, [h("div", { class: "local-service-title" }, [h("strong", null, service.name),
            h("span", { class: "local-service-status" + (service.last_check?.status === "online" && !service.control?.stopped ? " is-online" : "") },
              service.control?.stopped || service.last_check?.status === "stopped" ? tr("已停止", "Stopped") : service.last_check?.status === "online" ? tr("上次可访问", "Last check passed") : service.last_check?.status === "error" ? tr("上次连接失败", "Last check failed") : tr("未测试", "Not tested")),
            active === service.id ? h("span", { class: "local-service-active" }, tr("当前预测", "Active forecast")) : null]),
          h("p", { class: "local-service-endpoint" }, `${protocolName(service.protocol)} · ${service.transport === "docker" ? "Docker" : "HTTP"} · ${service.base_url}`),
          service.last_check ? h("p", { class: "local-service-note" }, (service.last_check.status === "stopped" ? tr("停止于 ", "Stopped ") : tr("检测于 ", "Checked ")) + new Date(service.last_check.checked_at).toLocaleString() + (service.last_check.elapsed_ms != null ? ` · ${service.last_check.elapsed_ms} ms` : "")) : null]),
          h("div", { class: "local-service-actions" }, [button(service.protocol === "openai" ? tr("发现模型", "Discover models") : tr("测试连接", "Test connection"), "test", service),
            service.protocol === "timesfm" ? button(tr("用于预测", "Use for forecasting"), "activate", service, false, active === service.id || service.control?.stopped) : null,
            service.control?.supported ? button(tr("停止模型", "Stop model"), "stop", service, false, !service.control.can_stop || service.control.stopping) : null,
            h("button", { type: "button", class: "btn-sm btn-sm-ghost", disabled: !!state.busy || !!state.editor, onClick: () => edit(service) }, tr("配置", "Configure")),
            button(tr("移除", "Remove"), "remove", service)]),
          state.models[service.id] ? h("div", { class: "local-service-models" }, state.models[service.id].length ? state.models[service.id].map(model => h("div", { class: "local-service-model" }, [h("span", null, model), button(tr("加入 LLM 列表", "Add to LLM list"), "register", service, false, false, model)]))
            : [h("p", { class: "local-service-note" }, tr("服务在线，但尚未提供模型。", "Service is reachable, but no models are available."))]) : null,
        ])),
        legacy ? h("article", { class: "local-service-row" }, [h("div", { class: "local-service-row-main" }, [h("div", { class: "local-service-title" }, [h("strong", null, "TimesFM-3"),
          h("span", { class: "local-service-active" }, state.forecast.enabled ? tr("当前预测", "Active forecast") : tr("未启用", "Disabled"))]),
        h("p", { class: "local-service-endpoint" }, `${state.forecast.mode === "managed" ? tr("应用托管", "App managed") : "TimesFM"} · ${state.forecast.base_url}`)]),
        h("div", { class: "local-service-actions" }, [
          state.forecast.can_stop ? button(tr("停止模型", "Stop model"), "stop", { id: "forecast", name: "TimesFM-3" }) : null,
          h("button", { type: "button", class: "btn-sm btn-sm-ghost", disabled: !!state.busy, onClick: () => { state.connectionSettings = state.forecast.mode !== "managed"; state.environment = true; void forecast.load(); draw(); } }, tr("配置", "Configure")),
        ])]) : null,
        state.loaded && !state.services.length && !legacy ? h("div", { class: "local-service-empty" }, [h("strong", null, tr("还没有服务连接", "No service connections yet")),
          h("p", null, tr("添加本机端口或 Docker 服务地址，即可测试和使用。", "Add a local port or Docker service URL to test and use it."))]) : null,
        state.message ? h("p", { role: state.error ? "alert" : "status", class: "local-service-message" + (state.error ? " is-error" : "") }, state.message) : null,
        renderEditor(),
        h("details", { class: "local-service-environment", open: state.environment,
          onToggle: e => { if (e.target.open !== state.environment) { state.environment = e.target.open; if (state.environment) void forecast.load(); else state.connectionSettings = false; } } }, [
          h("summary", null, state.connectionSettings ? tr("预测连接设置", "Forecast connection settings") : tr("下载或使用已有模型", "Download or use an existing model")),
          state.environment ? state.connectionSettings ? forecast.render() : forecast.renderSetup() : null]),
      ]),
    ]);
  }
  return { render, load };
}
