import { registerUiIsland } from "../../core/ui-registry.js";

// Progressive Vue island #5: Knowledge base modal (tabs + 3 list panels + form body).
// Mount points: #kb-tabs, #kb-panel-metrics, #kb-panel-rules, #kb-panel-notes, #kb-form-body.
// Import panel (#kb-panel-import) is NOT managed by Vue — it keeps legacy DOM.
// Registers the knowledge island. Falls back to the legacy panel when unavailable.
export function mountKnowledgeUi() {
  window.BAA = window.BAA || {};
  const Vue = window.Vue;
  const root1 = document.getElementById("kb-tabs");
  const root2 = document.getElementById("kb-panel-metrics");
  const root3 = document.getElementById("kb-panel-rules");
  const root4 = document.getElementById("kb-panel-notes");
  const root5 = document.getElementById("kb-form-body");
  if (!Vue || !Vue.h || !Vue.render || !Vue.Fragment ||
      !root1 || !root2 || !root3 || !root4 || !root5) return;

  // 清空 5 个挂载点的静态 HTML（#kb-panel-import 不清空，保留旧 DOM）
  root1.innerHTML = "";
  root2.innerHTML = "";
  root3.innerHTML = "";
  root4.innerHTML = "";
  root5.innerHTML = "";

  const { h, render, Fragment, reactive } = Vue;

  const TABS = [
    { key: "metrics", icon: "📐", label: "指标定义" },
    { key: "rules",   icon: "🛡", label: "业务规则" },
    { key: "notes",   icon: "📝", label: "背景知识" },
    { key: "import",  icon: "⬆", label: "导入文件", importTab: true },
  ];

  const TYPE_LABELS = {
    metrics: "指标", rules: "规则", notes: "背景知识",
  };

  const state = reactive({
    tab: "metrics",
    lists: {
      metrics: { items: [], count: "—", loading: false, err: "" },
      rules:   { items: [], count: "—", loading: false, err: "" },
      notes:   { items: [], count: "—", loading: false, err: "" },
    },
    form: {
      mode: "add",       // add | edit
      type: "metrics",   // metrics | rules | notes
      editId: null,
      fields: {
        name: "", alias: "", definition: "", sql_template: "", notes: "",
        rule_id: "", description: "", condition: "", severity: "warning",
        topic: "", content: "", tags: "",
      },
      err: "", busy: false,
    },
  });

  let callbacks = {};  // { onSwitchTab, onToggle, onOpenForm, onSubmitForm, onCancelForm, onDelete }

  // ── 渲染分发 ───────────────────────────────────────────────────
  function renderAll() {
    _renderTabs();
    _renderPanel("metrics");
    _renderPanel("rules");
    _renderPanel("notes");
    _renderForm();
    // import panel 不由 Vue 渲染，但显隐由 Vue 管（切到 import tab 时显示）
    const importPanel = document.getElementById("kb-panel-import");
    if (importPanel) importPanel.classList.toggle('hidden', state.tab !== "import");
  }

  function _renderTabs() {
    render(h("div", { class: "kb-tabs" }, TABS.map(tb => {
      const active = state.tab === tb.key;
      const cls = ["kb-tab"];
      if (active) cls.push("active");
      if (tb.importTab) cls.push("kb-tab-import");
      return h("button", {
        class: cls,
        onClick: () => callbacks.onSwitchTab && callbacks.onSwitchTab(tb.key),
      }, `${tb.icon} ${tb.label}`);
    })), root1);
  }

  function _renderPanel(type) {
    const root = { metrics: root2, rules: root3, notes: root4 }[type];
    if (!root) return;
    const L = state.lists[type];
    // 显隐：直接控制 root 元素（#kb-panel-* 本身就是 .kb-panel）
    root.classList.toggle('hidden', state.tab !== type);

    const toolbar = h("div", { class: "kb-toolbar" }, [
      h("span", { class: "kb-count" }, L.count),
      h("div", { style: "display:flex;gap:6px" }, [
        h("button", {
          class: "btn-sm btn-sm-ghost",
          title: "刷新列表",
          onClick: () => callbacks.onSwitchTab && callbacks.onSwitchTab(type),
        }, "↻ 刷新"),
        h("button", {
          class: "btn-sm btn-sm-primary",
          onClick: () => callbacks.onOpenForm && callbacks.onOpenForm(type, null),
        }, `＋ 新增${TYPE_LABELS[type]}`),
      ]),
    ]);

    let listContent;
    if (L.loading) {
      listContent = h("div", { class: "kb-empty" }, "加载中…");
    } else if (L.err) {
      listContent = h("div", { class: "kb-empty", style: "color:#ef4444" }, `加载失败: ${L.err}`);
    } else if (!L.items.length) {
      const emptyText = `暂无${TYPE_LABELS[type]}${type === "metrics" ? "定义" : ""}`;
      listContent = h("div", { class: "kb-empty" }, emptyText);
    } else {
      listContent = h(Fragment, null, L.items.map(item => _renderCard(type, item)));
    }

    const list = h("div", { class: "kb-list", id: `kb-list-${type}` }, listContent);
    render(h(Fragment, null, [toolbar, list]), root);
  }

  function _renderCard(type, item) {
    const cardStyle = item.enabled ? {} : { style: "opacity:.45" };
    const actions = h("div", { class: "kb-card-actions" }, [
      _renderToggle(item.enabled, () => callbacks.onToggle && callbacks.onToggle(type, item.id)),
      h("button", {
        class: "kb-act-btn",
        onClick: () => callbacks.onOpenForm && callbacks.onOpenForm(type, item.id),
      }, "编辑"),
      h("button", {
        class: "kb-act-btn danger",
        onClick: () => callbacks.onDelete && callbacks.onDelete(type, item.id),
      }, "删除"),
    ]);

    if (type === "metrics") {
      return h("div", Object.assign({ class: "kb-card", id: `kbc-metrics-${item.id}` }, cardStyle), [
        h("div", { class: "kb-card-head" }, [
          h("div", { class: "kb-card-name" }, [
            h("span", { class: "kb-badge kb-badge-metric" }, "指标"),
            ` ${item.name || ""}`,
            item.alias ? h("span", { style: "font-size:12px;color:#94a3b8;font-weight:400" }, ` · ${item.alias}`) : null,
          ]),
          actions,
        ]),
        item.definition ? h("div", { class: "kb-card-meta" }, item.definition) : null,
        item.sql_template ? h("div", { class: "kb-card-sql" }, item.sql_template) : null,
        item.notes ? h("div", { class: "kb-card-meta", style: "color:#94a3b8;font-size:11px" }, `备注：${item.notes}`) : null,
      ]);
    }

    if (type === "rules") {
      const badgeCls = item.severity === "error" ? "kb-badge kb-badge-rule-error" : "kb-badge kb-badge-rule-warning";
      return h("div", Object.assign({ class: "kb-card", id: `kbc-rules-${item.id}` }, cardStyle), [
        h("div", { class: "kb-card-head" }, [
          h("div", { class: "kb-card-name" }, [
            h("span", { class: badgeCls }, item.severity || "warning"),
            ` ${item.rule_id || ""}`,
          ]),
          actions,
        ]),
        item.description ? h("div", { class: "kb-card-meta" }, item.description) : null,
        item.condition ? h("div", { class: "kb-card-sql" }, item.condition) : null,
      ]);
    }

    // notes
    return h("div", Object.assign({ class: "kb-card", id: `kbc-notes-${item.id}` }, cardStyle), [
      h("div", { class: "kb-card-head" }, [
        h("div", { class: "kb-card-name" }, [
          h("span", { class: "kb-badge kb-badge-note" }, "背景"),
          ` ${item.topic || ""}`,
          item.tags ? h("span", { style: "font-size:11px;color:#94a3b8;font-weight:400" }, ` ${item.tags}`) : null,
        ]),
        actions,
      ]),
      item.content ? h("div", { class: "kb-card-meta" }, item.content) : null,
    ]);
  }

  function _renderToggle(enabled, onClick) {
    return h("div", {
      class: `kb-toggle ${enabled ? "on" : ""}`,
      title: enabled ? "已启用，点击禁用" : "已禁用，点击启用",
      onClick,
    }, [h("div", { class: "kb-toggle-knob" })]);
  }

  function _renderForm() {
    const f = state.form;
    const type = f.type;
    const fields = f.fields;

    let fieldNodes;
    if (type === "metrics") {
      fieldNodes = [
        _renderField("指标名称", true, "text", fields.name, v => { fields.name = v; }, "例如：DAU"),
        _renderField("别名（逗号分隔）", false, "text", fields.alias, v => { fields.alias = v; }, "日活, 日活跃用户"),
        _renderField("业务定义", false, "textarea", fields.definition, v => { fields.definition = v; }, "当日启动游戏一次及以上的独立设备数", 2),
        _renderField("SQL 模板", false, "textarea", fields.sql_template, v => { fields.sql_template = v; }, "SELECT COUNT(DISTINCT device_id) FROM events WHERE date='{date}'", 3),
        _renderField("口径备注", false, "textarea", fields.notes, v => { fields.notes = v; }, "剔除机器人流量；iOS/Android 分开统计", 2),
      ];
    } else if (type === "rules") {
      fieldNodes = [
        _renderField("规则 ID", true, "text", fields.rule_id, v => { fields.rule_id = v; }, "例如：retention_sanity"),
        _renderField("描述", false, "text", fields.description, v => { fields.description = v; }, "次日留存不能超过首日 DAU"),
        _renderField("违反条件", false, "textarea", fields.condition, v => { fields.condition = v; }, "day2_retention > day1_dau", 2),
        _renderSelect("严重程度", fields.severity, v => { fields.severity = v; }, [
          { value: "warning", label: "warning" },
          { value: "error", label: "error" },
        ]),
      ];
    } else {
      // notes
      fieldNodes = [
        _renderField("主题", true, "text", fields.topic, v => { fields.topic = v; }, "例如：流失分析"),
        _renderField("内容", false, "textarea", fields.content, v => { fields.content = v; }, "分析流失时需检查：版本更新、服务器波动、竞品上线…", 4),
        _renderField("标签（逗号分隔）", false, "text", fields.tags, v => { fields.tags = v; }, "流失, churn, 留存"),
      ];
    }

    const errNode = f.err ? h("div", { class: "msg-err" }, f.err) : null;
    render(h(Fragment, null, [...fieldNodes, errNode]), root5);
  }

  function _renderField(label, required, inputType, value, onInput, placeholder, rows) {
    const labelNode = required
      ? h("label", null, [label, " ", h("span", { style: "color:#ef4444" }, "*")])
      : h("label", null, label);
    const inputNode = inputType === "textarea"
      ? h("textarea", { rows: rows || 2, placeholder, onInput: e => onInput(e.target.value) }, value)
      : h("input", { type: inputType, value, placeholder, onInput: e => onInput(e.target.value) });
    return h("div", { class: "f-row" }, [labelNode, inputNode]);
  }

  function _renderSelect(label, value, onChange, options) {
    return h("div", { class: "f-row" }, [
      h("label", null, label),
      h("select", { value, onChange: e => onChange(e.target.value) },
        options.map(o => h("option", { value: o.value }, o.label))),
    ]);
  }

  // ── facade API ────────────────────────────────────────────────
  function sync(cbs) {
    callbacks = cbs || {};
  }

  function onOpen() {
    callbacks.onSwitchTab && callbacks.onSwitchTab(state.tab);
  }

  function setTab(tab) {
    state.tab = tab;
    renderAll();
  }

  function getTab() {
    return state.tab;
  }

  function _recalcCount(type) {
    const L = state.lists[type];
    if (!L) return;
    const enabled = L.items.filter(r => r.enabled).length;
    L.count = `共 ${L.items.length} 条 · ${enabled} 条已启用`;
  }

  function setItems(type, items) {
    if (!state.lists[type]) return;
    const L = state.lists[type];
    L.items = items || [];
    L.loading = false;
    L.err = "";
    _recalcCount(type);
    _renderPanel(type);
  }

  function setListStatus(type, opts) {
    if (!state.lists[type]) return;
    const L = state.lists[type];
    if (!opts) return;
    if (opts.loading !== undefined) L.loading = opts.loading;
    if (opts.err !== undefined) L.err = opts.err;
    if (opts.count !== undefined) L.count = opts.count;
    _renderPanel(type);
  }

  function getItem(type, id) {
    const L = state.lists[type];
    if (!L) return null;
    return L.items.find(x => x.id === id) || null;
  }

  function updateItem(type, id, patch) {
    const L = state.lists[type];
    if (!L) return;
    const item = L.items.find(x => x.id === id);
    if (!item) return;
    Object.assign(item, patch);
    _recalcCount(type);
    _renderPanel(type);
  }

  function removeItem(type, id) {
    const L = state.lists[type];
    if (!L) return;
    const idx = L.items.findIndex(x => x.id === id);
    if (idx < 0) return;
    L.items.splice(idx, 1);
    _recalcCount(type);
    _renderPanel(type);
  }

  function openForm(opts) {
    const f = state.form;
    const type = opts.type;
    const mode = opts.mode || "add";
    const editId = opts.editId != null ? opts.editId : null;
    const rec = opts.rec;

    f.type = type;
    f.mode = mode;
    f.editId = editId;
    f.err = "";
    f.busy = false;
    // 重置 fields
    f.fields = {
      name: "", alias: "", definition: "", sql_template: "", notes: "",
      rule_id: "", description: "", condition: "", severity: "warning",
      topic: "", content: "", tags: "",
    };
    // 编辑模式预填
    if (rec) {
      if (type === "metrics") {
        f.fields.name = rec.name || "";
        f.fields.alias = rec.alias || "";
        f.fields.definition = rec.definition || "";
        f.fields.sql_template = rec.sql_template || "";
        f.fields.notes = rec.notes || "";
      } else if (type === "rules") {
        f.fields.rule_id = rec.rule_id || "";
        f.fields.description = rec.description || "";
        f.fields.condition = rec.condition || "";
        f.fields.severity = rec.severity || "warning";
      } else if (type === "notes") {
        f.fields.topic = rec.topic || "";
        f.fields.content = rec.content || "";
        f.fields.tags = rec.tags || "";
      }
    }
    // 设置 form title（#kb-form-title 在 #kb-form-body 外，由 Vue island 代管）
    const titleEl = document.getElementById("kb-form-title");
    if (titleEl) {
      titleEl.textContent = (mode === "edit" ? "编辑" : "新增") + TYPE_LABELS[type];
    }
    _renderForm();
  }

  function closeForm() {
    state.form.err = "";
    state.form.busy = false;
  }

  function setFormField(key, val) {
    state.form.fields[key] = val;
  }

  function setFormErr(msg) {
    state.form.err = msg || "";
    _renderForm();
  }

  function setFormBusy(b) {
    state.form.busy = !!b;
  }

  function getFormValues() {
    const f = state.form;
    const fields = f.fields;
    if (f.type === "metrics") {
      return {
        type: f.type, mode: f.mode, editId: f.editId,
        body: {
          name: (fields.name || "").trim(),
          alias: (fields.alias || "").trim(),
          definition: (fields.definition || "").trim(),
          sql_template: (fields.sql_template || "").trim(),
          notes: (fields.notes || "").trim(),
        },
      };
    }
    if (f.type === "rules") {
      return {
        type: f.type, mode: f.mode, editId: f.editId,
        body: {
          rule_id: (fields.rule_id || "").trim(),
          description: (fields.description || "").trim(),
          condition: (fields.condition || "").trim(),
          severity: fields.severity || "warning",
        },
      };
    }
    return {
      type: f.type, mode: f.mode, editId: f.editId,
      body: {
        topic: (fields.topic || "").trim(),
        content: (fields.content || "").trim(),
        tags: (fields.tags || "").trim(),
      },
    };
  }

  // 初始化渲染
  renderAll();

  registerUiIsland("knowledge", {
    isAvailable: () => true,
    sync,
    onOpen,
    setTab,
    getTab,
    setItems,
    setListStatus,
    getItem,
    updateItem,
    removeItem,
    openForm,
    closeForm,
    setFormField,
    setFormErr,
    setFormBusy,
    getFormValues,
  });
}
