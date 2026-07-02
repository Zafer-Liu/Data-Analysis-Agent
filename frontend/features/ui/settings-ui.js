import { registerUiIsland } from "../../core/ui-registry.js";

// Progressive Vue island #4: Settings modal (built-in providers + custom models + add-custom form).
// Mount points: #builtin-providers, #custom-list, #add-custom-form (three roots, one state).
// Registers the settings island. Falls back to models.js legacy innerHTML when unavailable.
export function mountSettingsUi() {
  window.BAA = window.BAA || {};
  const Vue = window.Vue;
  const root1 = document.getElementById("builtin-providers");
  const root2 = document.getElementById("custom-list");
  const root3 = document.getElementById("add-custom-form");
  if (!Vue || !Vue.h || !Vue.render || !root1 || !root2 || !root3) return;

  // 立即清空三个挂载点的原始静态 HTML，防止 Vue 渲染与静态内容叠加
  root1.innerHTML = "";
  root2.innerHTML = "";
  root3.innerHTML = "";

  const { h, render, Fragment, reactive } = Vue;

  const COMMON_ICON = "/static/Images/icon.png";
  const BUILTIN_META = {
    deepseek:   { label: "DeepSeek",         icon: COMMON_ICON },
    openai:     { label: "OpenAI / ChatGPT", icon: COMMON_ICON },
    atlascloud: { label: "AtlasCloud",       icon: COMMON_ICON },
  };

  const state = reactive({
    providers: [],       // { key, label, icon, hasKey, defaults, cfg, fields, msg, busy }
    customs: [],         // { key, name, model, baseUrl }
    formOpen: false,     // add-custom-form 展开
    editingKey: null,    // 正在编辑的 custom key（null = 添加模式）
    form: {              // add + edit 共用表单
      name: "", url: "", model: "", key: "",
      ctx: "", output: "",
      think: false, budget: "8000",
    },
    formMsg: { err: "", ok: "" },
  });

  let callbacks = {};    // { onSave, onTest, onClear, onFieldChange, onSubmitForm, onCancelForm, onEditCustom, onDeleteCustom, onTestCustom }

  // ── 渲染分发 ───────────────────────────────────────────────────
  function renderAll() {
    _renderProviders();
    _renderCustoms();
    _renderForm();
  }

  function _renderProviders() {
    if (!state.providers.length) { render(null, root1); return; }
    render(h(Fragment, null, state.providers.map(_renderProviderCard)), root1);
  }

  function _renderCustoms() {
    if (!state.customs.length) {
      render(h("div", { class: "custom-empty" }, t('custom_empty')), root2);
      return;
    }
    render(h(Fragment, null, state.customs.map(_renderCustomItem)), root2);
  }

  function _renderForm() {
    // .show class 控制显隐，由 toggleAddCustom/openForm/closeForm 管理
    root3.classList.toggle("show", state.formOpen);
    if (!state.formOpen) { render(null, root3); return; }
    render(h(Fragment, null, [
      h("input", {
        type: "text", placeholder: t('add_custom.name_ph') || "供应商名称（显示用），例如 DeepSeek",
        value: state.form.name,
        onInput: e => { state.form.name = e.target.value; },
      }),
      h("input", {
        type: "text", placeholder: t('add_custom.url_ph') || "API Base URL，例如 https://api.deepseek.com",
        value: state.form.url,
        onInput: e => { state.form.url = e.target.value; },
      }),
      h("input", {
        type: "text", placeholder: t('add_custom.model_ph') || "Model ID（传入 API 的模型名），例如 deepseek-chat",
        value: state.form.model,
        onInput: e => { state.form.model = e.target.value; },
      }),
      h("input", {
        type: "password", placeholder: t('add_custom.key_ph') || "API Key",
        value: state.form.key,
        onInput: e => { state.form.key = e.target.value; },
      }),
      h("div", { style: "display:flex;gap:8px;" }, [
        h("input", {
          type: "number",
          placeholder: t('add_custom.ctx_ph') || "上下文窗口（tokens，选填）",
          style: "flex:1",
          value: state.form.ctx,
          onInput: e => { state.form.ctx = e.target.value; },
        }),
        h("input", {
          type: "number",
          placeholder: t('add_custom.out_ph') || "最大输出（tokens，选填）",
          style: "flex:1",
          value: state.form.output,
          onInput: e => { state.form.output = e.target.value; },
        }),
      ]),
      h("label", {
        style: "display:flex;align-items:center;gap:6px;font-size:13px;color:#475569;cursor:pointer;padding:2px 0",
      }, [
        h("input", {
          type: "checkbox",
          checked: state.form.think,
          onChange: e => { state.form.think = e.target.checked; _renderForm(); },
        }),
        h("span", null, t('add_custom.think') || "启用思考模式"),
      ]),
      state.form.think ? h("div", {
        style: "display:flex;align-items:center;gap:8px;font-size:13px;color:#475569",
      }, [
        h("label", { style: "white-space:nowrap" }, t('add_custom.budget') || "思考预算（tokens）"),
        h("input", {
          type: "number", min: "1000", max: "100000", step: "1000",
          style: "flex:1",
          value: state.form.budget,
          onInput: e => { state.form.budget = e.target.value; },
        }),
      ]) : null,
      h("div", { class: "msg-err" }, state.formMsg.err),
      h("div", { class: "msg-ok" }, state.formMsg.ok),
      h("div", { style: "display:flex;gap:7px;justify-content:flex-end;" }, [
        h("button", {
          class: "btn-sm btn-sm-ghost",
          onClick: () => callbacks.onCancelForm && callbacks.onCancelForm(),
        }, t('modal.cancel') || "取消"),
        h("button", {
          class: "btn-sm btn-sm-primary",
          onClick: () => callbacks.onSubmitForm && callbacks.onSubmitForm(),
        }, t('modal.save_btn') || "保存"),
      ]),
    ]), root3);
  }

  // ── provider 卡片 ──────────────────────────────────────────────
  function _renderProviderCard(p) {
    const isBusy = !!p.busy;
    return h("div", { class: "provider-card" }, [
      h("div", { class: "provider-head" }, [
        h("img", { class: "provider-icon", src: p.icon, alt: p.label }),
        h("span", { class: "provider-name" }, p.label),
        h("span", {
          class: `provider-status ${p.hasKey ? "set" : "unset"}`,
        }, p.hasKey ? t('settings.configured') : t('settings.not_configured')),
      ]),
      h("div", { class: "provider-fields" }, [
        _pfRow(t('settings.api_key'),
          h("input", {
            type: "password",
            placeholder: t('settings.api_key_ph'),
            value: p.fields.apiKey,
            onInput: e => { p.fields.apiKey = e.target.value; },
          })
        ),
        _pfRow(t('settings.base_url'),
          h("input", {
            type: "text",
            placeholder: p.defaults.base_url,
            value: p.fields.baseUrl,
            onInput: e => { p.fields.baseUrl = e.target.value; },
          })
        ),
        _pfRow(t('settings.model'),
          h("input", {
            type: "text",
            placeholder: p.defaults.model,
            value: p.fields.model,
            onInput: e => { p.fields.model = e.target.value; },
          })
        ),
        _pfRow(t('settings.ctx_window'),
          h("input", {
            type: "number",
            placeholder: t('settings.ctx_ph'),
            value: p.fields.ctx,
            onInput: e => { p.fields.ctx = e.target.value; },
          })
        ),
        _pfRow(t('settings.max_output'),
          h("input", {
            type: "number",
            placeholder: t('settings.out_ph'),
            value: p.fields.output,
            onInput: e => { p.fields.output = e.target.value; },
          })
        ),
        h("div", { class: "pf-row", style: "align-items:center" }, [
          h("label", null, t('settings.thinking')),
          h("label", {
            style: "display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;color:#475569",
          }, [
            h("input", {
              type: "checkbox",
              checked: p.fields.think,
              onChange: e => {
                p.fields.think = e.target.checked;
                _renderProviders();
              },
            }),
            t('settings.thinking_label'),
          ]),
        ]),
        p.fields.think ? h("div", { class: "pf-row", style: "align-items:center" }, [
          h("label", null, t('settings.budget') || "思考预算（tokens）"),
          h("input", {
            type: "number", min: "1000", max: "100000", step: "1000",
            value: p.fields.budget,
            onInput: e => { p.fields.budget = e.target.value; },
          }),
        ]) : null,
      ]),
      h("div", { class: "provider-actions" }, [
        h("button", {
          class: "btn-sm btn-sm-danger",
          disabled: isBusy,
          onClick: () => callbacks.onClear && callbacks.onClear(p.key),
        }, t('settings.clear')),
        h("button", {
          class: "btn-sm btn-sm-ghost",
          disabled: isBusy,
          onClick: () => callbacks.onTest && callbacks.onTest(p.key),
        }, p.busy === "test" ? (t('settings.testing') || "测试中…") : (t('settings.test') || "测试")),
        h("button", {
          class: "btn-sm btn-sm-primary",
          disabled: isBusy,
          onClick: () => callbacks.onSave && callbacks.onSave(p.key),
        }, p.busy === "save" ? (t('settings.saving') || "保存中…") : t('settings.save')),
      ]),
      p.msg.text ? h("div", { class: `provider-msg ${p.msg.type}` }, p.msg.text) : null,
    ]);
  }

  function _pfRow(labelText, inputEl) {
    return h("div", { class: "pf-row" }, [
      h("label", null, labelText),
      inputEl,
    ]);
  }

  // ── custom 列表项 ─────────────────────────────────────────────
  function _renderCustomItem(c) {
    return h("div", { class: "custom-item" }, [
      h("span", { class: "ci-name" }, c.name || c.model || c.key),
      h("span", { class: "ci-model" }, c.model || c.baseUrl || ""),
      h("button", {
        class: "btn-sm btn-sm-ghost",
        onClick: () => callbacks.onTestCustom && callbacks.onTestCustom(c.key),
      }, t('settings.test') || "测试"),
      h("button", {
        class: "btn-sm btn-sm-ghost",
        onClick: () => callbacks.onEditCustom && callbacks.onEditCustom(c.key),
      }, t('settings.edit_custom') || "编辑"),
      h("button", {
        class: "btn-sm btn-sm-danger",
        onClick: () => callbacks.onDeleteCustom && callbacks.onDeleteCustom(c.key),
      }, t('settings.del_custom')),
    ]);
  }

  // ── state 操作 API ────────────────────────────────────────────
  function _initFields(cfg, def) {
    return {
      apiKey:  "",
      baseUrl: cfg.base_url || def.base_url || "",
      model:   cfg.model || def.model || "",
      ctx:     cfg.context_window != null ? String(cfg.context_window) : (def.context_window != null ? String(def.context_window) : ""),
      output:  cfg.max_output_tokens != null ? String(cfg.max_output_tokens) : (def.max_output_tokens != null ? String(def.max_output_tokens) : ""),
      think:   !!cfg.enable_thinking,
      budget:  cfg.thinking_budget != null ? String(cfg.thinking_budget) : "8000",
    };
  }

  function setProviders(configs, defaults) {
    // 保留现有 fields（用户正在输入的未保存值），仅刷新 hasKey/cfg。
    // 例外：hasKey 从 true→false（刚清除）时重置 fields 为 defaults。
    state.providers = Object.entries(defaults).map(([key, def]) => {
      const meta = BUILTIN_META[key] || { label: key, icon: COMMON_ICON };
      const cfg = configs[key] || {};
      const newHasKey = !!cfg.has_api_key;
      const existing = state.providers.find(p => p.key === key);
      const wasCleared = existing && existing.hasKey && !newHasKey;
      return {
        key,
        label: meta.label,
        icon: meta.icon,
        hasKey: newHasKey,
        defaults: def,
        cfg,
        fields: (existing && !wasCleared) ? existing.fields : _initFields(cfg, def),
        msg: existing ? existing.msg : { text: "", type: "" },
        busy: existing ? existing.busy : null,
      };
    });
    _renderProviders();
  }

  function clearProviderApiKey(key) {
    const p = state.providers.find(x => x.key === key);
    if (!p) return;
    p.fields.apiKey = "";
    p.hasKey = true;
    _renderProviders();
  }

  function setCustoms(configs) {
    state.customs = Object.entries(configs)
      .filter(([, v]) => v.is_custom)
      .map(([key, cfg]) => ({
        key,
        name: cfg.name || "",
        model: cfg.model || "",
        baseUrl: cfg.base_url || "",
      }));
    _renderCustoms();
  }

  function setProviderStatus(key, type, text) {
    const p = state.providers.find(x => x.key === key);
    if (!p) return;
    p.msg = { type, text };
    _renderProviders();
  }

  function setProviderBusy(key, busy) {
    const p = state.providers.find(x => x.key === key);
    if (!p) return;
    p.busy = busy || null;
    _renderProviders();
  }

  function openForm(editingKey, cfg) {
    state.editingKey = editingKey || null;
    state.formMsg = { err: "", ok: "" };
    if (editingKey && cfg) {
      // 编辑模式：用完整 cfg 预填
      state.form = {
        name: cfg.name || "",
        url: cfg.base_url || "",
        model: cfg.model || "",
        key: "",
        ctx: cfg.context_window != null ? String(cfg.context_window) : "",
        output: cfg.max_output_tokens != null ? String(cfg.max_output_tokens) : "",
        think: !!cfg.enable_thinking,
        budget: cfg.thinking_budget != null ? String(cfg.thinking_budget) : "8000",
      };
    } else {
      // 添加模式：清空
      state.form = { name: "", url: "", model: "", key: "", ctx: "", output: "", think: false, budget: "8000" };
    }
    state.formOpen = true;
    _renderForm();
  }

  function closeForm() {
    state.formOpen = false;
    state.editingKey = null;
    state.formMsg = { err: "", ok: "" };
    _renderForm();
  }

  function toggleForm() {
    if (state.formOpen) closeForm();
    else openForm(null);
  }

  function setFormField(name, value) {
    state.form[name] = value;
    // think 切换需要重渲染（budget row 显隐）
    if (name === "think") _renderForm();
  }

  function setFormMsg(err, ok) {
    state.formMsg = { err: err || "", ok: ok || "" };
    _renderForm();
  }

  function getFormValues() {
    return {
      name: state.form.name,
      url: state.form.url,
      model: state.form.model,
      key: state.form.key,
      ctx: state.form.ctx,
      output: state.form.output,
      think: state.form.think,
      budget: state.form.budget,
      editingKey: state.editingKey,
    };
  }

  function getProviderFields(key) {
    const p = state.providers.find(x => x.key === key);
    if (!p) return null;
    return { ...p.fields };
  }

  function refreshCustoms(configs) {
    setCustoms(configs);
  }

  function sync(configs, defaults, cbs) {
    callbacks = cbs || {};
    setProviders(configs, defaults);
    setCustoms(configs);
  }

  // 初始化时立即清空三个挂载点的原始静态 HTML（否则 Vue 渲染会叠加在静态内容上）
  renderAll();

  registerUiIsland("settings", {
    isAvailable: () => true,
    sync,
    setProviders,
    setCustoms,
    refreshCustoms,
    setProviderStatus,
    setProviderBusy,
    clearProviderApiKey,
    openForm,
    closeForm,
    toggleForm,
    setFormField,
    setFormMsg,
    getFormValues,
    getProviderFields,
  });
}
