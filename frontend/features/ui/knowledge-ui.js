import { registerUiIsland } from "../../core/ui-registry.js";
import { requestMessage } from "../../core/api-client.js";

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
  root1.classList.remove("kb-tabs");
  root1.classList.add("kb-tabs-shell");
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
    categories: [],
    activeCategoryId: 1,
    categoryDraft: "",
    categoryErr: "",
    categoryBusy: false,
    categoryMenuOpen: false,
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

  const closeCategoryMenu = () => {
    if (!state.categoryMenuOpen) return;
    state.categoryMenuOpen = false;
    _renderTabs();
  };
  document.addEventListener("click", closeCategoryMenu);

  function _activeCategory() {
    return state.categories.find(c => Number(c.id) === Number(state.activeCategoryId)) || null;
  }

  function _activeCategoryEnabled() {
    const cat = _activeCategory();
    return !cat || !!cat.enabled;
  }

  // ── 渲染分发 ───────────────────────────────────────────────────
  function renderAll() {
    _recalcAllCounts();
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
    const activeCategory = _activeCategory();
    const categoryBar = h("div", { class: "kb-scope-tools" }, [
      h("div", { class: "kb-scope-head" }, [
        h("span", { class: "kb-scope-title" }, "业务分类"),
        h("div", { class: "kb-scope-actions" }, [
          h("div", { class: "kb-category-filter" }, [
            h("button", {
              class: ["kb-category-select", state.categoryMenuOpen ? "open" : ""],
              title: "选择业务分类",
              type: "button",
              "aria-expanded": state.categoryMenuOpen,
              onClick: e => {
                e.stopPropagation();
                state.categoryMenuOpen = !state.categoryMenuOpen;
                _renderTabs();
              },
            }, [
              h("span", { class: "kb-category-select-label" }, activeCategory ? activeCategory.name : "请选择业务分类"),
              activeCategory && !activeCategory.enabled ? h("span", { class: "kb-category-select-off" }, "停用") : null,
              h("span", { class: "kb-category-caret" }, "▾"),
            ]),
            state.categoryMenuOpen ? _renderCategoryMenu() : null,
          ]),
          h("button", {
            class: "btn-sm btn-sm-primary kb-upload-parse-btn",
            onClick: () => callbacks.onOpenImport && callbacks.onOpenImport(),
          }, "上传文档解析"),
        ]),
      ]),
      state.categoryErr ? h("div", { class: "kb-category-err", role: "alert" }, [state.categoryErr,
        h("button", { class: "kb-act-btn", type: "button", onClick: () => callbacks.onSwitchTab?.(state.tab) }, requestMessage("retry"))]) : null,
    ]);

    const tabs = h("div", { class: "kb-tabs" }, TABS.filter(tb => !tb.importTab).map(tb => {
      const active = state.tab === tb.key;
      const cls = ["kb-tab"];
      if (active) cls.push("active");
      return h("button", {
        class: cls,
        onClick: () => callbacks.onSwitchTab && callbacks.onSwitchTab(tb.key),
      }, `${tb.icon} ${tb.label}`);
    }));
    render(h(Fragment, null, [categoryBar, tabs]), root1);
  }

  function _renderCategoryMenu() {
    const submitCategory = () => {
      const name = state.categoryDraft;
      state.categoryDraft = "";
      callbacks.onAddCategory && callbacks.onAddCategory(name);
    };
    return h("div", {
      class: "kb-category-menu",
      onClick: e => e.stopPropagation(),
      onKeydown: e => {
        if (e.key === "Escape") {
          e.stopPropagation();
          closeCategoryMenu();
          root1.querySelector?.(".kb-category-select")?.focus();
        }
      },
    }, [
      h("div", { class: "kb-category-menu-list" }, state.categories.map(cat => {
        const active = Number(cat.id) === Number(state.activeCategoryId);
        const enabled = !!cat.enabled;
        return h("div", {
          class: ["kb-category-option", active ? "active" : "", enabled ? "" : "disabled"],
        }, [
          h("button", {
            class: "kb-category-option-select", type: "button", "aria-pressed": active,
            onClick: () => {
              state.categoryMenuOpen = false;
              callbacks.onSelectCategory?.(cat.id);
              root1.querySelector?.(".kb-category-select")?.focus();
            },
          }, [
            h("span", { class: "kb-category-check", "aria-hidden": "true" }, active ? "✓" : ""),
            h("span", { class: "kb-category-option-name" }, cat.name),
          ]),
          h("button", {
            class: ["kb-category-toggle", enabled ? "on" : "off"],
            type: "button", disabled: !!cat.pending,
            "aria-pressed": enabled,
            title: enabled ? "停用该业务知识库" : "启用该业务知识库",
            onClick: e => {
              e.stopPropagation();
              callbacks.onToggleCategory && callbacks.onToggleCategory(cat.id);
            },
          }, enabled ? "启用" : "停用"),
          h("button", {
            class: "kb-category-delete", type: "button", disabled: !!cat.pending,
            title: `删除业务分类“${cat.name}”`,
            "aria-label": `删除业务分类“${cat.name}”`,
            onClick: e => {
              e.stopPropagation();
              callbacks.onDeleteCategory && callbacks.onDeleteCategory(cat.id);
            },
          }, "×"),
        ]);
      })),
      h("div", { class: "kb-category-menu-add" }, [
        h("input", {
          value: state.categoryDraft,
          placeholder: "新增业务，如外卖业务",
          onInput: e => { state.categoryDraft = e.target.value; },
          onKeydown: e => {
            if (e.key === "Enter") submitCategory();
          },
        }),
        h("button", {
          class: "kb-category-add-btn", type: "button", disabled: state.categoryBusy,
          onClick: submitCategory,
        }, state.categoryBusy ? requestMessage("saving") : "新增分类"),
      ]),
      state.categoryErr ? h("div", { class: "kb-category-err", role: "alert" }, [state.categoryErr,
        h("button", { class: "kb-act-btn", type: "button", onClick: () => callbacks.onSwitchTab?.(state.tab) }, requestMessage("retry"))]) : null,
    ]);
  }

  function _renderPanel(type) {
    const root = { metrics: root2, rules: root3, notes: root4 }[type];
    if (!root) return;
    const L = state.lists[type];
    // 显隐：直接控制 root 元素（#kb-panel-* 本身就是 .kb-panel）
    root.classList.toggle('hidden', state.tab !== type);

    const toolbar = h("div", { class: "kb-toolbar" }, [
      h("span", { class: "kb-count" }, L.count),
      h("div", { class: "kb-toolbar-actions" }, [
        h("button", {
          class: "btn-sm btn-sm-ghost",
          type: "button", title: requestMessage(L.loading ? "cancel" : "refresh"),
          onClick: () => L.loading ? callbacks.onCancelList?.(type) : callbacks.onSwitchTab?.(type),
        }, requestMessage(L.loading ? "cancel" : "refresh")),
        h("button", {
          class: "btn-sm btn-sm-primary",
          onClick: () => callbacks.onOpenForm && callbacks.onOpenForm(type, null),
        }, `＋ 新增${TYPE_LABELS[type]}`),
      ]),
    ]);

    let listContent;
    if (L.loading) {
      listContent = h("div", { class: "kb-empty", role: "status" }, requestMessage("loading"));
    } else if (L.err) {
      listContent = h("div", { class: "kb-empty kb-request-error", role: "alert" }, [L.err,
        h("button", { type: "button", class: "kb-act-btn", onClick: () => callbacks.onSwitchTab?.(type) }, requestMessage("retry")),
      ]);
    } else if (!L.items.length) {
      listContent = h("div", { class: "kb-empty", role: "status" }, requestMessage(`${type}Empty`));
    } else {
      listContent = h(Fragment, null, L.items.map(item => _renderCard(type, item)));
    }

    const list = h("div", { class: "kb-list", id: `kb-list-${type}`, "aria-busy": L.loading }, listContent);
    render(h(Fragment, null, [toolbar, list]), root);
  }

  function _renderCard(type, item) {
    const categoryEnabled = _activeCategoryEnabled();
    const effectiveEnabled = categoryEnabled && !!item.enabled;
    const cardStyle = effectiveEnabled ? {} : { style: "opacity:.45" };
    // Actions row: placed at the bottom so the name row has full width (no squeeze)
    const actions = h("div", { class: "kb-card-actions" }, [
      _renderToggle(effectiveEnabled, () => callbacks.onToggle && callbacks.onToggle(type, item.id), { disabled: !categoryEnabled || item.pending }),
      h("button", {
        class: "kb-act-btn", type: "button", disabled: !!item.pending,
        onClick: () => callbacks.onOpenForm && callbacks.onOpenForm(type, item.id),
      }, "编辑"),
      h("button", {
        class: "kb-act-btn danger", type: "button", disabled: !!item.pending,
        onClick: () => callbacks.onDelete && callbacks.onDelete(type, item.id),
      }, "删除"),
    ]);

    if (type === "metrics") {
      return h("div", Object.assign({ class: "kb-card", id: `kbc-metrics-${item.id}` }, cardStyle), [
        // Name row: badge + name on one line, no actions competing for space
        h("div", { class: "kb-card-name" }, [
          h("span", { class: "kb-badge kb-badge-metric" }, "指标"),
          h("span", { class: "kb-card-name-text" }, item.name || ""),
          item.alias ? h("span", { class: "kb-card-alias" }, ` · ${item.alias}`) : null,
        ]),
        item.definition ? h("div", { class: "kb-card-meta" }, item.definition) : null,
        item.sql_template ? h("div", { class: "kb-card-sql" }, item.sql_template) : null,
        item.notes ? h("div", { class: "kb-card-meta", style: "color:#94a3b8;font-size:11px" }, `备注：${item.notes}`) : null,
        actions,
      ]);
    }

    if (type === "rules") {
      const badgeCls = item.severity === "error" ? "kb-badge kb-badge-rule-error" : "kb-badge kb-badge-rule-warning";
      return h("div", Object.assign({ class: "kb-card", id: `kbc-rules-${item.id}` }, cardStyle), [
        h("div", { class: "kb-card-name" }, [
          h("span", { class: badgeCls }, item.severity || "warning"),
          h("span", { class: "kb-card-name-text" }, item.rule_id || ""),
        ]),
        item.description ? h("div", { class: "kb-card-meta" }, item.description) : null,
        item.condition ? h("div", { class: "kb-card-sql" }, item.condition) : null,
        actions,
      ]);
    }

    // notes
    return h("div", Object.assign({ class: "kb-card", id: `kbc-notes-${item.id}` }, cardStyle), [
      h("div", { class: "kb-card-name" }, [
        h("span", { class: "kb-badge kb-badge-note" }, "背景"),
        h("span", { class: "kb-card-name-text" }, item.topic || ""),
        item.tags ? h("span", { class: "kb-card-alias" }, ` ${item.tags}`) : null,
      ]),
      item.content ? h("div", { class: "kb-card-meta" }, item.content) : null,
      actions,
    ]);
  }

  function _renderToggle(enabled, onClick, opts = {}) {
    const disabled = !!opts.disabled;
    return h("button", {
      type: "button", role: "switch", disabled, "aria-checked": enabled,
      "aria-label": requestMessage("toggleLabel"),
      class: ["kb-toggle", enabled ? "on" : "", disabled ? "disabled" : ""],
      title: disabled ? "当前业务分类已停用，启用业务后此条才生效" : (enabled ? "已启用，点击禁用" : "已禁用，点击启用"),
      onClick: disabled ? undefined : onClick,
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

    const errNode = f.err ? h("div", { class: "msg-err", id: "kb-form-error", role: "alert" }, f.err) : null;
    render(h(Fragment, null, [...fieldNodes, errNode]), root5);
  }

  function _renderField(label, required, inputType, value, onInput, placeholder, rows) {
    const id = `kb-field-${encodeURIComponent(label)}`;
    const labelNode = h("label", { for: id }, required ? [label, " ", h("span", { "aria-hidden": "true" }, "*")] : label);
    const attrs = { id, required, disabled: state.form.busy, placeholder,
      "aria-invalid": required && !!state.form.err, "aria-describedby": state.form.err ? "kb-form-error" : undefined,
      onInput: e => onInput(e.target.value) };
    const inputNode = inputType === "textarea"
      ? h("textarea", { ...attrs, rows: rows || 2, value })
      : h("input", { ...attrs, type: inputType, value });
    return h("div", { class: "f-row" }, [labelNode, inputNode]);
  }

  function _renderSelect(label, value, onChange, options) {
    const id = `kb-field-${encodeURIComponent(label)}`;
    return h("div", { class: "f-row" }, [
      h("label", { for: id }, label),
      h("select", { id, value, disabled: state.form.busy, onChange: e => onChange(e.target.value) },
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


  function setCategories(categories, activeId) {
    state.categories = Array.isArray(categories) ? categories : [];
    state.activeCategoryId = Number(activeId || state.activeCategoryId || state.categories[0]?.id || 1);
    state.categoryErr = "";
    renderAll();
  }

  function setActiveCategory(id) {
    state.activeCategoryId = Number(id) || 1;
    renderAll();
  }

  function getActiveCategoryId() {
    return state.activeCategoryId || 1;
  }

  function setCategoryBusy(busy) {
    state.categoryBusy = !!busy;
    _renderTabs();
  }

  function setCategoryErr(msg) {
    state.categoryErr = msg || "";
    _renderTabs();
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
    const enabled = _activeCategoryEnabled()
      ? L.items.filter(r => r.enabled).length
      : 0;
    L.count = `共 ${L.items.length} 条 · ${enabled} 条已启用`;
  }

  function _recalcAllCounts() {
    _recalcCount("metrics");
    _recalcCount("rules");
    _recalcCount("notes");
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
    setFormBusy(false);
  }

  function closeForm() {
    state.form.err = "";
    setFormBusy(false);
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
    root5.setAttribute("aria-busy", String(!!b));
    const button = document.getElementById("kb-submit-btn");
    if (button) { button.disabled = !!b; button.textContent = requestMessage(b ? "saving" : "save"); }
    _renderForm();
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

  document.addEventListener("langchange", renderAll);
  // 初始化渲染
  renderAll();

  registerUiIsland("knowledge", {
    isAvailable: () => true,
    sync,
    onOpen,
    setTab,
    getTab,
    setCategories,
    setActiveCategory,
    getActiveCategoryId,
    setCategoryErr,
    setCategoryBusy,
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
