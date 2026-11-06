import { registerUiIsland } from "../../core/ui-registry.js";

// Progressive Vue island #6: MCP settings modal (server list + form fields).
// Mount points: #mcp-server-list, #mcp-form-fields.
// Smart-fill area (.mcp-smart-area) is NOT managed by Vue — it keeps legacy DOM.
// Registers the MCP island. Falls back to the legacy panel when unavailable.
export function mountMcpUi() {
  window.BAA = window.BAA || {};
  const Vue = window.Vue;
  const root1 = document.getElementById("mcp-server-list");
  const root2 = document.getElementById("mcp-form-fields");
  const hasVue = root1 && root2 && Vue && Vue.h && Vue.render;
  if (!hasVue) { registerUiIsland("mcp", null); return; }

  const { h, render, reactive, Fragment } = Vue;
  const STATUS_ICON = {
    connected: "🟢", connecting: "🟡", disconnected: "⚪", error: "🔴",
  };

  const state = reactive({
    listStatus: { loading: false, err: "" },
    servers: [],
    form: {
      open: false,
      mode: "add",
      editId: null,
      fields: {
        label: "", id: "", desc: "",
        transport: "stdio",
        command: "", args: "", env: "",
        url: "", headers: "",
      },
      err: "", ok: "", busy: false,
    },
  });
  let callbacks = {};

  // ── 初始化：一次性清空静态占位 HTML，让 Vue 接管 ────────────────
  // 注意：这里只在 mountMcpUi() 首次执行时清空一次。
  // 后续 renderAll() 不再使用 innerHTML=""，避免销毁 Vue 已管理的真实 DOM
  // 节点后 Vue _vnode 缓存失效，导致 patch 时 nextSibling 读到 null 崩溃。
  root1.innerHTML = "";
  root2.innerHTML = "";

  // ── 渲染分发 ───────────────────────────────────────────────────
  function renderAll() {
    _renderList();
    _renderForm();
  }

  function _renderList() {
    const L = state.listStatus;
    let content;
    if (L.loading) {
      content = h("div", { style: "font-size:12px;color:var(--color-text-mute);padding:4px 0" }, "加载中…");
    } else if (L.err) {
      content = h("div", { style: "font-size:12px;color:#dc2626;padding:4px 0" }, `加载失败: ${L.err}`);
    } else if (!state.servers.length) {
      content = h("div", { style: "font-size:12px;color:var(--color-sidebar-fg-dim);padding:4px 0" }, "暂无配置的服务器");
    } else {
      content = h(Fragment, null, state.servers.map(s => _renderServerCard(s)));
    }
    render(content, root1);
  }

  function _renderServerCard(s) {
    const icon = STATUS_ICON[s.status] || "⚪";
    const toolCount = s.tool_count != null ? `${s.tool_count} 个工具` : "";
    const canShowTools = s.status === "connected" && s.tool_count > 0;
    const showConnect = s.status !== "connected" && s.status !== "connecting";

    // ── Row 1: icon + name + id badge + transport ──────────────────
    const metaRow = h("div", { style: "display:flex;align-items:center;gap:6px;flex-wrap:wrap;min-width:0" }, [
      h("span", { style: "font-size:16px;flex-shrink:0" }, icon),
      h("strong", { style: "font-size:13px;min-width:0;word-break:break-all" }, s.label || ""),
      h("code", { style: "font-size:10px;padding:1px 5px;border-radius:4px;flex-shrink:0" }, s.server_id || ""),
      h("span", { style: "font-size:11px;color:var(--color-text-mute);flex-shrink:0" }, s.transport || ""),
      toolCount
        ? h("span", { style: "font-size:11px;color:#059669;flex-shrink:0" }, toolCount)
        : null,
    ].filter(Boolean));

    // ── Row 2 (optional): description / error ─────────────────────
    const extras = [];
    if (s.description) {
      extras.push(h("div", { style: "font-size:12px;color:var(--color-text-mute)" }, s.description));
    }
    if (s.last_error) {
      extras.push(h("div", { style: "font-size:11px;color:#dc2626" }, s.last_error));
    }

    // ── Row 3: actions (all in one flex row, auto-wrap) ────────────
    const actionChildren = [
      h("label", {
        style: "display:flex;align-items:center;gap:4px;font-size:12px;color:var(--color-text-soft);cursor:pointer;white-space:nowrap",
        title: "启用/禁用",
      }, [
        h("input", {
          type: "checkbox",
          checked: s.enabled,
          onChange: (e) => callbacks.onToggleEnabled && callbacks.onToggleEnabled(s.server_id, e.target.checked),
        }),
        "启用",
      ]),
    ];
    if (canShowTools) {
      actionChildren.push(h("button", {
        class: "btn-sm btn-sm-ghost",
        style: "padding:2px 8px;font-size:11px;white-space:nowrap",
        onClick: () => callbacks.onToggleTools && callbacks.onToggleTools(s.server_id),
      }, s.toolsOpen ? "收起工具 ▴" : "查看工具 ▾"));
    }
    actionChildren.push(h("button", {
      class: "btn-sm btn-sm-ghost",
      style: "padding:2px 8px;font-size:11px;white-space:nowrap",
      onClick: () => callbacks.onOpenEdit && callbacks.onOpenEdit(s.server_id),
    }, "编辑"));
    if (showConnect) {
      actionChildren.push(h("button", {
        class: "btn-sm btn-sm-ghost",
        style: "padding:2px 8px;font-size:11px;white-space:nowrap",
        onClick: () => callbacks.onConnect && callbacks.onConnect(s.server_id),
      }, "连接"));
    }
    actionChildren.push(h("button", {
      class: "btn-sm",
      style: "padding:2px 8px;font-size:11px;white-space:nowrap;background:transparent;border:1px solid rgba(239,68,68,.45);color:#dc2626;border-radius:5px;cursor:pointer",
      onClick: () => callbacks.onRemove && callbacks.onRemove(s.server_id),
    }, "删除"));

    const cardChildren = [
      metaRow,
      ...extras,
      h("div", {
        style: "display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:6px;padding-top:6px;border-top:1px solid var(--color-border)",
      }, actionChildren),
    ];

    // 工具展开区（嵌套）
    if (s.toolsOpen) {
      cardChildren.push(_renderTools(s));
    }

    return h("div", {
      class: "custom-model-item mcp-server-card",
      style: "display:flex;flex-direction:column;gap:0;padding:8px 10px",
    }, cardChildren);
  }

  function _renderTools(s) {
    let content;
    if (s.toolsLoading) {
      content = h("div", { style: "font-size:11px;color:#64748b" }, "加载中…");
    } else if (s.toolsErr) {
      content = h("div", { style: "font-size:11px;color:#ef4444" }, `加载失败: ${s.toolsErr}`);
    } else if (!s.tools || !s.tools.length) {
      content = h("div", { style: "font-size:11px;color:#94a3b8" }, "暂无工具");
    } else {
      content = h(Fragment, null, s.tools.map(t => {
        const schema = t.inputSchema || {};
        const props = schema.properties || {};
        const required = new Set(schema.required || []);
        const params = Object.entries(props).map(([k, v]) => {
          const cls = required.has(k) ? "mcp-tool-param required" : "mcp-tool-param";
          const attrs = {};
          if (v.description) attrs.title = v.description;
          return h("span", { class: cls, ...attrs }, `${k}${required.has(k) ? "*" : ""}`);
        });
        const toolChildren = [
          h("div", { class: "mcp-tool-name" }, t.name),
        ];
        if (t.description) {
          toolChildren.push(h("div", { class: "mcp-tool-desc" }, t.description));
        }
        if (params.length) {
          toolChildren.push(h("div", { class: "mcp-tool-params" }, params));
        }
        return h("div", { class: "mcp-tool-item" }, toolChildren);
      }));
    }
    return h("div", { class: "mcp-tool-list", style: "display:flex" }, content);
  }

  function _renderForm() {
    // 控制 #mcp-add-form（父容器）和 #mcp-add-toggle（兄弟）显隐
    // .add-custom-form 默认 display:none；展开时需加 .show（display:flex），
    // 不能只靠移除 .hidden（移除后仍是 display:none）
    const formWrap = document.getElementById("mcp-add-form");
    const toggleEl = document.getElementById("mcp-add-toggle");
    if (formWrap) {
      formWrap.classList.toggle('hidden', false);   // 始终移除 hidden 避免 !important 干扰
      formWrap.classList.toggle('show', state.form.open);
    }
    if (toggleEl) toggleEl.textContent = state.form.open ? "▲ 折叠" : "＋ 添加 MCP 服务器";

    if (!state.form.open) {
      render(null, root2);
      return;
    }

    const F = state.form.fields;
    const isEdit = state.form.mode === "edit";
    const title = isEdit ? `编辑：${F.label}` : "添加服务器";

    // 命令预览（computed）
    const cmd = (F.command || "").trim();
    const args = (F.args || "").trim();
    const cmdParts = cmd ? [cmd, ...args.split(/\s+/).filter(Boolean)] : [];
    const showPreview = F.transport === "stdio" && cmd;

    const children = [
      h("div", { class: "f-form-title", id: "mcp-form-title" }, title),
      h("input", {
        type: "text", id: "mcp-label",
        placeholder: "服务器名称（显示用）",
        value: F.label,
        onInput: (e) => { F.label = e.target.value; },
      }),
    ];

    // id-row（edit 模式隐藏）
    if (!isEdit) {
      children.push(h("div", { id: "mcp-id-row" },
        h("input", {
          type: "text", id: "mcp-id",
          placeholder: "服务器 ID（字母/数字/下划线，唯一）",
          style: "width:100%",
          value: F.id,
          onInput: (e) => { F.id = e.target.value; },
        })
      ));
    }

    children.push(
      h("input", {
        type: "text", id: "mcp-desc",
        placeholder: "描述（可选）",
        value: F.desc,
        onInput: (e) => { F.desc = e.target.value; },
      }),
      // transport selector
      h("div", { class: "f-row-gap16" }, [
        h("label", { class: "f-label-inline" }, [
          h("input", {
            type: "radio", name: "mcp-transport", value: "stdio",
            checked: F.transport === "stdio",
            onChange: () => { F.transport = "stdio"; _renderForm(); },
          }),
          "stdio（本地命令）",
        ]),
        h("label", { class: "f-label-inline" }, [
          h("input", {
            type: "radio", name: "mcp-transport", value: "sse",
            checked: F.transport === "sse",
            onChange: () => { F.transport = "sse"; _renderForm(); },
          }),
          "SSE（远程 HTTP）",
        ]),
      ])
    );

    // stdio fields
    if (F.transport === "stdio") {
      const stdioChildren = [
        h("div", { class: "f-warn-chip" },
          "⚠️ 安全提示：仅允许运行 uvx / uv / npx / node / python / python3 / deno 命令。args 和 env 中不得含有 Shell 元字符或危险环境变量。"),
        h("input", {
          type: "text", id: "mcp-command",
          placeholder: "命令，例如 npx 或 uvx",
          value: F.command,
          onInput: (e) => { F.command = e.target.value; },
        }),
        h("input", {
          type: "text", id: "mcp-args",
          placeholder: "参数（空格分隔），例如 -y @modelcontextprotocol/server-filesystem /tmp",
          value: F.args,
          onInput: (e) => { F.args = e.target.value; },
        }),
        h("input", {
          type: "text", id: "mcp-env",
          placeholder: "环境变量（变量名=值，逗号分隔），例如：MY_API_KEY=apikey-xxx, OTHER_KEY=yyy",
          value: F.env,
          onInput: (e) => { F.env = e.target.value; },
        }),
      ];
      if (showPreview) {
        stdioChildren.push(h("div", { class: "mcp-cmd-preview", style: "display:block" }, [
          h("div", { class: "cmd-label" }, "命令预览"),
          h("span", {}, cmdParts.join(" ")),
        ]));
      }
      children.push(h("div", { id: "mcp-stdio-fields", style: "display:flex;flex-direction:column;gap:8px" }, stdioChildren));
    }

    // sse fields
    if (F.transport === "sse") {
      children.push(h("div", { id: "mcp-sse-fields", style: "display:flex;flex-direction:column;gap:8px" }, [
        h("input", {
          type: "text", id: "mcp-url",
          placeholder: "SSE 端点 URL，例如 http://localhost:8000/sse",
          value: F.url,
          onInput: (e) => { F.url = e.target.value; },
        }),
        h("input", {
          type: "text", id: "mcp-headers",
          placeholder: "HTTP 头（KEY:VALUE，逗号分隔，可选）",
          value: F.headers,
          onInput: (e) => { F.headers = e.target.value; },
        }),
        h("div", { class: "f-hint", style: "font-size:11px;color:#64748b;margin-top:-4px" },
          "示例：Authorization:Bearer sk-xxx, X-Custom:value"),
      ]));
    }

    // err / ok
    if (state.form.err) {
      children.push(h("div", { class: "msg-err", id: "mcp-add-err" }, state.form.err));
    } else {
      children.push(h("div", { class: "msg-err", id: "mcp-add-err" }));
    }
    if (state.form.ok) {
      children.push(h("div", { class: "msg-ok", id: "mcp-add-ok" }, state.form.ok));
    } else {
      children.push(h("div", { class: "msg-ok", id: "mcp-add-ok" }));
    }

    render(h(Fragment, null, children), root2);
  }

  // ── lifecycle ──────────────────────────────────────────────────
  function isAvailable() { return true; }

  function sync(cbs) {
    callbacks = cbs || {};
    renderAll();
  }

  function onOpen() {
    if (callbacks.onOpen) callbacks.onOpen();
  }

  // ── list API ───────────────────────────────────────────────────
  function setServers(servers) {
    // 保留已有 toolsOpen/tools/toolsLoading 状态（如果 server 还在列表里）
    const oldMap = {};
    state.servers.forEach(s => { oldMap[s.server_id] = s; });
    state.servers = (servers || []).map(s => {
      const old = oldMap[s.server_id];
      return {
        ...s,
        toolsOpen: old ? old.toolsOpen : false,
        tools: old ? old.tools : [],
        toolsLoading: old ? old.toolsLoading : false,
        toolsErr: old ? old.toolsErr : "",
        busy: false,
      };
    });
    _renderList();
  }

  function setListStatus(opts) {
    if (opts.loading != null) state.listStatus.loading = opts.loading;
    if (opts.err != null) state.listStatus.err = opts.err;
    _renderList();
  }

  function updateServer(id, patch) {
    const s = state.servers.find(x => x.server_id === id);
    if (!s) return null;
    Object.assign(s, patch);
    _renderList();
    return s;
  }

  function removeServer(id) {
    const idx = state.servers.findIndex(x => x.server_id === id);
    if (idx === -1) return;
    state.servers.splice(idx, 1);
    _renderList();
  }

  function getServer(id) {
    return state.servers.find(x => x.server_id === id) || null;
  }

  // ── tools API ──────────────────────────────────────────────────
  function setTools(id, tools) {
    updateServer(id, { tools: tools || [], toolsLoading: false, toolsErr: "" });
  }

  function setToolsLoading(id, b) {
    updateServer(id, { toolsLoading: b });
  }

  function setToolsErr(id, err) {
    updateServer(id, { toolsErr: err, toolsLoading: false });
  }

  function toggleToolsOpen(id) {
    const s = getServer(id);
    if (!s) return;
    s.toolsOpen = !s.toolsOpen;
    _renderList();
  }

  // ── form API ───────────────────────────────────────────────────
  function openForm(opts) {
    opts = opts || {};
    state.form.mode = opts.mode || "add";
    state.form.editId = opts.editId || null;
    state.form.err = "";
    state.form.ok = "";
    state.form.busy = false;
    if (opts.server) {
      const s = opts.server;
      const transport = s.transport || "stdio";
      state.form.fields = {
        label: s.label || "",
        id: s.server_id || "",
        desc: s.description || "",
        transport,
        command: s.command || "",
        args: (s.args || []).join(" "),
        env: Object.entries(s.env || {}).map(([k, v]) => `${k}=${v}`).join(", "),
        url: s.url || "",
        headers: Object.entries(s.headers || {}).map(([k, v]) => `${k}:${v}`).join(", "),
      };
    } else {
      state.form.fields = {
        label: "", id: "", desc: "",
        transport: "stdio",
        command: "", args: "", env: "",
        url: "", headers: "",
      };
    }
    state.form.open = true;
    _renderForm();
  }

  function closeForm() {
    state.form.open = false;
    state.form.editId = null;
    state.form.err = "";
    state.form.ok = "";
    _renderForm();
  }

  function toggleForm() {
    if (state.form.open) {
      closeForm();
      if (callbacks.onCancel) callbacks.onCancel();
    } else {
      openForm({ mode: "add" });
    }
  }

  function setFields(cfg) {
    // 桥接：smart-fill 区写入 Vue state
    cfg = cfg || {};
    if (cfg.transport) state.form.fields.transport = cfg.transport;
    if (cfg.label != null) state.form.fields.label = cfg.label;
    if (cfg.description != null) state.form.fields.desc = cfg.description;
    if (cfg.server_id != null && state.form.mode === "add") {
      if (!state.form.fields.id) state.form.fields.id = cfg.server_id;
    }
    if (cfg.transport === "stdio") {
      if (cfg.command != null) state.form.fields.command = cfg.command;
      if (cfg.args != null) state.form.fields.args = (cfg.args || []).join(" ");
      if (cfg.env != null) state.form.fields.env = Object.entries(cfg.env || {}).map(([k, v]) => `${k}=${v}`).join(", ");
    } else {
      if (cfg.url != null) state.form.fields.url = cfg.url;
      if (cfg.headers != null) state.form.fields.headers = Object.entries(cfg.headers || {}).map(([k, v]) => `${k}:${v}`).join(", ");
    }
    _renderForm();
  }

  function setField(key, val) {
    if (key in state.form.fields) {
      state.form.fields[key] = val;
      _renderForm();
    }
  }

  function setTransport(t) {
    state.form.fields.transport = t;
    _renderForm();
  }

  function setFormErr(msg) {
    state.form.err = msg || "";
    _renderForm();
  }

  function setFormOk(msg) {
    state.form.ok = msg || "";
    _renderForm();
  }

  function setFormBusy(b) {
    state.form.busy = !!b;
    // busy 禁用保存按钮（按钮在 root2 外，由 data-action 委托，需手动操作）
    const btns = document.querySelectorAll("#mcp-add-form button[data-action='addMcpServer']");
    btns.forEach(b2 => { b2.disabled = state.form.busy; });
  }

  function getFormValues() {
    const F = state.form.fields;
    return {
      label: (F.label || "").trim(),
      id: (F.id || "").trim(),
      desc: (F.desc || "").trim(),
      transport: F.transport,
      command: (F.command || "").trim(),
      args: (F.args || "").trim(),
      env: (F.env || "").trim(),
      url: (F.url || "").trim(),
      headers: (F.headers || "").trim(),
    };
  }

  function getFormState() {
    return { mode: state.form.mode, editId: state.form.editId, open: state.form.open };
  }

  function resetForm() {
    state.form.fields = {
      label: "", id: "", desc: "",
      transport: "stdio",
      command: "", args: "", env: "",
      url: "", headers: "",
    };
    state.form.err = "";
    state.form.ok = "";
    state.form.busy = false;
    _renderForm();
  }

  // ── 暴露 facade ────────────────────────────────────────────────
  registerUiIsland("mcp", {
    isAvailable,
    sync,
    onOpen,
    setServers,
    setListStatus,
    updateServer,
    removeServer,
    getServer,
    setTools,
    setToolsLoading,
    setToolsErr,
    toggleToolsOpen,
    openForm,
    closeForm,
    toggleForm,
    setFields,
    setField,
    setTransport,
    setFormErr,
    setFormOk,
    setFormBusy,
    getFormValues,
    getFormState,
    resetForm,
  });
}
