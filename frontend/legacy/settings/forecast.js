// Optional model setup and compatibility settings inside Local services.
const tr = (zh, en) => window.getLang?.() === "en" ? en : zh;

export function createForecastDomain({ draw, onConfigured = () => {} }) {
  const { h, reactive } = window.Vue;
  const state = reactive({ config: null, token: "", clearToken: false, loading: false,
    busy: false, dirty: false, message: "", error: false, unavailable: false, setupChoice: "download", setupChosen: false,
    runtime: { status: "idle", managed_running: false } });
  let pollTimer = null;

  function message(text, error = false) {
    const parts = String(text || "").split(" / ");
    state.message = parts.length > 1 ? tr(parts[0], parts.slice(1).join(" / ")) : String(text || "");
    state.error = error;
  }
  async function request(path, body, method = "POST") {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch("/api/forecast/" + path, {
        method: body === undefined ? "GET" : method, signal: controller.signal,
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const value = await response.json().catch(() => ({}));
      if (!response.ok || value.ok === false) {
        if (response.status === 404) state.unavailable = true;
        throw new Error(value.message || tr("预测服务请求失败", "Forecast service request failed"));
      }
      return value;
    } finally { clearTimeout(timer); }
  }
  function accept(data, replace = false) {
    state.runtime = data.runtime || state.runtime;
    if (data.config && (!state.dirty || replace)) {
      state.config = data.config;
      state.dirty = false;
      if (!state.setupChosen) {
        if (data.config.mode === "managed" && data.config.model_dir) state.setupChoice = "existing";
        state.setupChosen = true;
      }
      onConfigured(data.config, state.runtime);
    }
  }
  async function load() {
    if (state.loading) return;
    state.loading = true;
    try { accept(await request("config")); }
    catch (err) { message(err.message, true); }
    finally { state.loading = false; draw(); }
    if (state.runtime.status === "running") schedulePoll();
  }
  function schedulePoll() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(async () => {
      try {
        const data = await request("runtime");
        accept(data, data.runtime.status === "succeeded");
        message(data.runtime.message, data.runtime.status === "failed");
        draw();
        if (data.runtime.status === "running") schedulePoll();
      } catch (err) { message(err.message, true); draw(); }
    }, 2000);
  }
  function payload() {
    return { ...state.config, ...(state.token ? { token: state.token } : {}), clear_token: state.clearToken };
  }
  async function save({ disabled = false, localSetup = false } = {}) {
    const body = payload();
    if (localSetup) {
      body.mode = "managed";
      body.service_id = "";
      if (state.config.mode !== "managed") body.base_url = "";
      if (state.setupChoice === "download") body.model_dir = "";
      else if (!body.runtime_dir) body.runtime_dir = body.model_dir.replace(/[\\/]+$/, "") + "/.sage-runtime";
    }
    if (disabled) body.enabled = false;
    const data = await request("config", body, "PUT");
    accept(data, true);
    state.token = "";
    state.clearToken = false;
  }
  async function act(action, localSetup = false) {
    if (state.busy || state.runtime.status === "running") return;
    state.busy = true;
    message("");
    draw();
    try {
      if (action === "save") {
        await save();
        message(tr("配置已保存", "Configuration saved"));
      } else if (action === "test" || action === "inference") {
        const value = await request("test", { ...payload(), inference: action === "inference" });
        message(action === "test"
          ? tr(`连接成功 · ${value.health.device || "unknown"}`, `Connected · ${value.health.device || "unknown"}`)
          : tr(`试运行成功 · ${value.inference.elapsed_seconds ?? "—"} 秒`, `Inference succeeded · ${value.inference.elapsed_seconds ?? "—"} s`));
      } else {
        if (action === "download") {
          const accepted = await window.BAA.ui?.confirm?.({
            title: tr("下载 TimesFM-3", "Download TimesFM-3"),
            message: tr("将下载约 1.32 GB 模型，并安装所需组件（可能额外占用数 GB）。完成后自动启用预测。模型仅限非商业、非生产研究。是否继续？", "Download the ~1.32 GB model and install required components (potentially several additional GB). Forecasting will be enabled when ready. Non-commercial, non-production research only. Continue?"),
          });
          if (!accepted) return;
        }
        if (action !== "stop") await save({ disabled: true, localSetup });
        const value = await request("runtime", { action, confirm_download: action === "download", license_accepted: action === "download" });
        accept(value);
        message(tr("操作已开始，可在这里查看进度", "Operation started; progress appears here"));
        schedulePoll();
      }
    } catch (err) { message(err.message || tr("操作失败，请重试", "Operation failed; retry"), true); }
    finally { state.busy = false; draw(); }
  }
  function field(key, label, hint = "", type = "text", help = "") {
    const id = "forecast-" + key;
    return h("div", { class: "forecast-field" }, [
      h("label", { for: id }, label),
      h("input", { id, type, value: state.config[key] ?? "", disabled: state.busy || state.runtime.status === "running",
        placeholder: hint, autocomplete: "off", spellcheck: false,
        onInput: e => { state.config[key] = type === "number" ? Number(e.target.value) : e.target.value; state.dirty = true; draw(); },
        ...(type === "number" ? { min: key === "port" ? 1024 : 5, max: key === "port" ? 65535 : 600 } : {}),
      }),
      help ? h("p", { class: "forecast-note" }, help) : null,
    ]);
  }
  function renderSetup() {
    const c = state.config;
    const running = state.runtime.status === "running";
    const disabled = state.busy || running;
    const installed = c?.mode === "managed" && c.enabled && state.runtime.managed_running;
    const downloading = state.setupChoice === "download";
    const stages = {
      environment: tr("准备运行组件", "Preparing components"), downloading: tr("正在下载模型", "Downloading model"),
      verifying: tr("检查模型文件", "Checking model files"), starting: tr("启动模型", "Starting model"),
      testing: tr("测试预测", "Testing prediction"), stopping: tr("停止模型", "Stopping model"),
    };
    const button = (action, zh, en, primary = false, missing = false) => h("button", {
      type: "button", class: "btn-sm " + (primary ? "btn-sm-primary" : "btn-sm-ghost"),
      disabled: disabled || missing || (["start", "download"].includes(action) && state.runtime.managed_running), onClick: () => act(action, true),
    }, tr(zh, en));
    return h("section", { class: "forecast-service forecast-setup", "aria-label": tr("本机模型", "Local model") }, [
      state.unavailable ? h("p", { role: "status" }, tr("请在桌面版或本地部署中安装模型。", "Use the desktop app or a local deployment to install models."))
        : !c ? h("p", { role: "status", class: "forecast-note" }, tr("读取中…", "Loading…"))
        : h("div", { class: "forecast-content" }, [
          h("p", { class: "forecast-model-name" }, tr("TimesFM-3 · 销量与需求预测", "TimesFM-3 · Sales and demand forecasting")),
          installed ? h("p", { role: "status", class: "forecast-note" }, tr("已在本机运行", "Running on this computer")) : h("fieldset", { class: "forecast-choices", disabled }, [
            h("legend", null, tr("使用方式", "Setup method")),
            ...[["download", tr("下载模型", "Download model")], ["existing", tr("使用已有模型", "Use existing model")]].map(([value, label]) =>
              h("label", null, [h("input", { type: "radio", name: "forecast-setup-choice", value, checked: state.setupChoice === value,
                onChange: () => { state.setupChoice = value; message(""); draw(); } }), label])),
          ]),
          !installed && downloading ? field("runtime_dir", tr("保存到", "Save to"), tr("填写文件夹路径", "Enter a folder path")) : null,
          !installed && !downloading ? field("model_dir", tr("模型文件夹", "Model folder"), tr("填写已下载模型的文件夹路径", "Folder containing the downloaded model")) : null,
          !installed ? h("details", { class: "forecast-advanced" }, [
            h("summary", null, tr("高级设置", "Advanced settings")),
            h("div", { class: "forecast-grid" }, [
              field("python_executable", tr("Python 路径（可选）", "Python path (optional)"), tr("留空使用默认环境", "Leave empty to use the default environment"), "text",
                downloading ? "" : tr("需要已安装 TimesFM 运行组件的 Python 环境。", "Requires a Python environment with the TimesFM runtime components installed.")),
              ...(!downloading ? [field("runtime_dir", tr("运行文件保存到（可选）", "Runtime folder (optional)"), tr("默认保存在模型文件夹内", "Defaults to a folder inside the model folder"))] : []),
              field("port", tr("端口", "Port"), "8787", "number"),
              h("div", { class: "forecast-field" }, [h("label", { for: "forecast-device" }, tr("运行设备", "Device")),
                h("select", { id: "forecast-device", value: c.device, disabled,
                  onChange: e => { c.device = e.target.value; state.dirty = true; draw(); } }, [
                  h("option", { value: "auto" }, tr("自动", "Auto")), h("option", { value: "cpu" }, "CPU"), h("option", { value: "cuda" }, "CUDA GPU"),
                ])]),
            ]),
            h("label", { class: "forecast-check" }, [h("input", { type: "checkbox", checked: c.auto_start, disabled,
              onChange: e => { c.auto_start = e.target.checked; state.dirty = true; draw(); } }), tr("打开智析时启动模型", "Start the model with the app")]),
          ]) : null,
          h("div", { class: "forecast-actions" }, installed ? [
            button("inference", "试运行", "Test prediction"), button("stop", "停止模型", "Stop model"),
          ] : [downloading
            ? button("download", "下载并启用 · 1.32 GB", "Download and enable · 1.32 GB", true, !c.runtime_dir?.trim())
            : button("start", "启动并使用", "Start and use", true, !c.model_dir?.trim()),
          ...(state.runtime.managed_running ? [button("stop", "停止模型", "Stop model")] : [])]),
          running ? h("p", { role: "status", class: "forecast-note" }, (stages[state.runtime.stage] || tr("处理中…", "Working…"))
            + (state.runtime.stage === "downloading" ? ` ${state.runtime.progress || 0}%` : "")) : null,
          h("p", { class: "forecast-note" }, [tr("仅限非商业研究 · ", "Non-commercial research only · "),
            h("a", { href: "https://huggingface.co/google/timesfm-3.0-pytorch/blob/main/LICENSE", target: "_blank", rel: "noopener noreferrer" }, tr("使用许可", "License"))]),
        ]),
      state.message ? h("p", { role: state.error ? "alert" : "status", class: "forecast-message" + (state.error ? " is-error" : "") }, state.message) : null,
    ]);
  }
  function render() {
    const running = state.runtime.status === "running";
    const disabled = state.busy || running;
    const button = (action, zh, en, primary = false, extraDisabled = false) => h("button", {
      type: "button", class: "btn-sm " + (primary ? "btn-sm-primary" : "btn-sm-ghost"),
      disabled: disabled || extraDisabled, onClick: () => act(action),
    }, tr(zh, en));
    const c = state.config;
    return h("section", { class: "forecast-service", "aria-label": tr("预测连接", "Forecast connection") }, [
      state.unavailable ? h("p", { role: "status" }, tr("当前为托管网页，本地服务需在桌面版或本地部署中连接。", "Use the desktop app or a local deployment to connect local model services."))
        : !c ? h("button", { type: "button", class: "btn-sm btn-sm-ghost", disabled: state.loading, onClick: load }, state.loading ? tr("读取配置中…", "Loading…") : tr("读取配置", "Load settings"))
        : h("div", { class: "forecast-content" }, [
          h("div", { class: "forecast-field" }, [h("label", { for: "forecast-mode" }, tr("接入方式", "Connection method")),
            h("select", { id: "forecast-mode", value: c.mode, disabled,
              onChange: e => { c.mode = e.target.value; c.enabled = false; c.auto_start = false; state.dirty = true; draw(); } }, [
              h("option", { value: "api" }, tr("已有 HTTP API", "Existing HTTP API")),
              h("option", { value: "docker" }, tr("已有 Docker 服务端口", "Existing Docker service port")),
              h("option", { value: "managed" }, tr("本地模型目录 / 主动下载", "Local model directory / optional download")),
            ]),
          ]),
          c.mode !== "managed" ? field("base_url", tr("服务地址", "Service URL"), "http://127.0.0.1:8787") : h("div", { class: "forecast-grid" }, [
            field("runtime_dir", tr("独立运行目录", "Runtime directory"), tr("填写此电脑的绝对路径", "Absolute path on this computer")),
            field("model_dir", tr("已有模型目录（可选）", "Existing model directory (optional)"), tr("留空则下载到运行目录 / model", "Empty: runtime directory / model")),
            field("python_executable", tr("已有 Python 解释器（可选）", "Existing Python executable (optional)"), tr("已有模型启用时指定已具备依赖的解释器", "Use an interpreter with model dependencies")),
            field("port", tr("首选端口", "Preferred port"), "8787", "number"),
            h("div", { class: "forecast-field" }, [h("label", { for: "forecast-device" }, tr("运行设备", "Device")),
              h("select", { id: "forecast-device", value: c.device, disabled, onChange: e => { c.device = e.target.value; state.dirty = true; } },
                [h("option", { value: "auto" }, tr("自动（遵循本机 GPU 开关）", "Auto (respects local GPU switch)")), h("option", { value: "cpu" }, "CPU"), h("option", { value: "cuda" }, "CUDA GPU")])]),
          ]),
          c.mode === "docker" ? h("p", { class: "forecast-note" }, tr("填写宿主机映射端口；智析也在容器中时使用可达的服务名。此入口不会拉取镜像或下载权重。", "Use the host-mapped port, or a reachable service name when the app is containerized. No images or weights are downloaded here.")) : null,
          h("details", { class: "forecast-advanced" }, [h("summary", null, tr("认证与连接选项", "Authentication and connection options")),
            h("div", { class: "forecast-grid" }, [field("timeout", tr("请求超时（秒）", "Request timeout (seconds)"), "180", "number"),
              h("div", { class: "forecast-field" }, [h("label", { for: "forecast-token" }, tr("认证密钥（可选）", "Authentication token (optional)")),
                h("input", { id: "forecast-token", type: "password", autocomplete: "new-password", value: state.token, disabled,
                  placeholder: c.token_configured ? tr("已保存；留空保留", "Saved; leave empty to keep") : "Bearer token",
                  onInput: e => { state.token = e.target.value; state.dirty = true; } })])]),
            c.token_configured ? h("label", { class: "forecast-check" }, [h("input", { type: "checkbox", checked: state.clearToken, disabled, onChange: e => { state.clearToken = e.target.checked; state.dirty = true; } }), tr("移除已保存密钥", "Remove saved token")]) : null,
          ]),
          h("label", { class: "forecast-check" }, [h("input", { type: "checkbox", checked: c.enabled, disabled,
            onChange: e => { c.enabled = e.target.checked; state.dirty = true; } }), tr("启用预测（保存时检查服务）", "Enable forecasting (service checked on save)")]),
          c.mode === "managed" ? h("label", { class: "forecast-check" }, [h("input", { type: "checkbox", checked: c.auto_start, disabled,
            onChange: e => { c.auto_start = e.target.checked; state.dirty = true; } }), tr("随智析启动已配置的本地服务", "Start the configured local service with the app")]) : null,
          h("div", { class: "forecast-actions" }, [
            button("save", "保存配置", "Save settings", true),
            button("test", "测试连接", "Test connection", false, !c.base_url),
            button("inference", "合成数据试运行", "Test with synthetic data", false, !c.base_url),
            ...(c.mode === "managed" ? [button("start", "验证并启用已有模型", "Verify and start existing model", false, !c.runtime_dir),
              button("download", "下载并配置", "Download and configure", false, !c.runtime_dir)] : []),
            ...(state.runtime.managed_running ? [button("stop", "停止应用托管实例", "Stop app-managed instance")] : []),
          ]),
          c.mode === "managed" && c.base_url ? h("p", { class: "forecast-note" }, tr("已配置地址：", "Configured URL: ") + c.base_url) : null,
          running ? h("p", { role: "status", class: "forecast-note" }, `${state.runtime.stage} · ${state.runtime.progress || 0}% · ${state.runtime.message || ""}`) : null,
          h("p", { class: "forecast-note" }, [tr("模型仅限非商业、非生产研究。", "Model is for non-commercial, non-production research. "), h("a", { href: "https://huggingface.co/google/timesfm-3.0-pytorch/blob/main/LICENSE", target: "_blank", rel: "noopener noreferrer" }, tr("查看许可", "License"))]),
        ]),
      state.message ? h("p", { role: state.error ? "alert" : "status", class: "forecast-message" + (state.error ? " is-error" : "") }, state.message) : null,
    ]);
  }
  return { render, renderSetup, load };
}
