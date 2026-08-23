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

  // 立即清空 providers 和 customs 的原始静态 HTML（这两个用 Vue render 管理没问题）
  root1.innerHTML = "";
  root2.innerHTML = "";
  // ⚠️ 不清空 root3！#add-custom-form 保留模板中的静态 HTML，
  //    用 class 切换控制显隐，避免 Vue render 到该容器时的不可见问题。

  const { h, render, Fragment, reactive } = Vue;

  const COMMON_ICON = "/static/Images/icon.png";
  const BUILTIN_META = {
    deepseek:   { label: "DeepSeek",         icon: COMMON_ICON },
    openai:     { label: "OpenAI / ChatGPT", icon: COMMON_ICON },
    atlascloud: { label: "AtlasCloud",       icon: COMMON_ICON },
    ollama:     { label: "Ollama (本地)",     icon: COMMON_ICON, local: true },
  };
  // 内置提供商排序：Ollama 作为本地模型放在最后，与普通在线提供商区分开。
  const BUILTIN_ORDER = ["deepseek", "openai", "atlascloud", "ollama"];

  // 判断 base_url 是否本地地址（与后端 _is_local_base_url 保持一致）
  function _isLocalUrl(url) {
    if (!url) return false;
    const u = String(url).toLowerCase();
    return ["localhost", "127.0.0.1", "0.0.0.0", "[::1]", "0:0:0:0:0:0:0:1"]
      .some(m => u.includes(m));
  }

  const state = reactive({
    providers: [],       // { key, label, icon, hasKey, defaults, cfg, fields, msg, busy, expanded }
    customs: [],         // { key, name, model, baseUrl }
    formOpen: false,     // add-custom-form 展开
    editingKey: null,    // 正在编辑的 custom key（null = 添加模式）
    form: {              // add + edit 共用表单
      name: "", url: "", model: "", key: "",
      ctx: "", output: "",
      inputPrice: "", outputPrice: "",
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

  // #add-custom-form 不用 Vue render，直接操作静态 DOM。
  // 原因：Vue 3 render() 到该容器时子元素虽然存在于 DOM 但视觉不可见（已用 v34 排查确认），
  // 改为 class 切换 + 直接读写静态 input 元素值，100% 可靠。
  function _renderForm() {
    root3.classList.toggle("show", state.formOpen);

    if (!state.formOpen) return;

    // 同步 reactive state → 静态 input 元素（模板中已有 id="ac-*" 的元素）
    const _v = (id) => {
      const el = document.getElementById(id);
      return el || null;
    };
    const _set = (id, val) => { const el = _v(id); if (el) el.value = val; };
    const _chk = (id, checked) => { const el = _v(id); if (el) el.checked = checked; };

    _set("ac-name",   state.form.name);
    _set("ac-url",    state.form.url);
    _set("ac-model",  state.form.model);
    _set("ac-key",    state.form.key);
    _set("ac-ctx",    state.form.ctx);
    _set("ac-output", state.form.output);
    _set("ac-input-price", state.form.inputPrice);
    _set("ac-output-price", state.form.outputPrice);
    _set("ac-budget", state.form.budget);
    _chk("ac-think",  state.form.think);

    // 思考预算行显隐
    const budgetRow = _v("ac-budget-row");
    if (budgetRow) budgetRow.classList.toggle("hidden", !state.form.think);

    // 消息
    const errEl = _v("ac-err");
    const okEl  = _v("ac-ok");
    if (errEl) errEl.textContent = state.formMsg.err;
    if (okEl)  okEl.textContent  = state.formMsg.ok;
  }

  // ── provider 卡片 ──────────────────────────────────────────────
  function _renderProviderCard(p) {
    const isBusy = !!p.busy;
    const isExpanded = !!p.expanded;
    const header = h("div", {
      class: "provider-head",
      onClick: () => {
        p.expanded = !isExpanded;
        _renderProviders();
      },
    }, [
      h("img", { class: "provider-icon", src: p.icon, alt: p.label }),
      h("span", { class: "provider-name" }, p.label),
      h("span", {
        class: `provider-status ${p.hasKey ? "set" : "unset"}`,
      }, p.hasKey ? t('settings.configured') : t('settings.not_configured')),
      h("span", { class: `provider-toggle ${isExpanded ? "open" : ""}` },
        isExpanded ? "\u25BE" : "\u25B8"),  /* ▾ / ▸ */
    ]);

    if (!isExpanded) {
      return h("div", { class: "provider-card collapsed", key: p.key }, [
        header,
        p.msg.text ? h("div", { class: `provider-msg ${p.msg.type}` }, p.msg.text) : null,
      ]);
    }

    return h("div", { class: "provider-card", key: p.key }, [
      header,
      h("div", { class: "provider-fields" }, [
        (() => {
          const isLocal = !!(BUILTIN_META[p.key] && BUILTIN_META[p.key].local)
                        || _isLocalUrl(p.fields.baseUrl);
          return _pfRow(t('settings.api_key'),
            h("input", {
              type: isLocal ? "text" : "password",
              autocomplete: "off",
              "data-lpignore": "true",  /* 阻止 LastPass 等密码管理器自动填充 */
              placeholder: isLocal
                ? (t('settings.api_key_local_ph') || "本地模型（如 Ollama）无需 API Key，可留空")
                : t('settings.api_key_ph'),
              value: p.fields.apiKey,
              onInput: e => { p.fields.apiKey = e.target.value; },
            })
          );
        })(),
        _pfRow(t('settings.base_url'),
          h("input", {
            type: "text",
            autocomplete: "off",
            placeholder: p.defaults.base_url,
            value: p.fields.baseUrl,
            onInput: e => { p.fields.baseUrl = e.target.value; },
          })
        ),
        _pfRow(t('settings.model'),
          h("input", {
            type: "text",
            autocomplete: "off",
            placeholder: p.defaults.model,
            value: p.fields.model,
            onInput: e => { p.fields.model = e.target.value; },
          })
        ),
        _pfRow(t('settings.ctx_window'),
          h("input", {
            type: "number",
            autocomplete: "off",
            placeholder: t('settings.ctx_ph'),
            value: p.fields.ctx,
            onInput: e => { p.fields.ctx = e.target.value; },
          })
        ),
        _pfRow(t('settings.max_output'),
          h("input", {
            type: "number",
            autocomplete: "off",
            placeholder: t('settings.out_ph'),
            value: p.fields.output,
            onInput: e => { p.fields.output = e.target.value; },
          })
        ),
        _pfRow("输入价格（可选）",
          h("input", {
            type: "number", min: "0", step: "any", inputmode: "decimal",
            placeholder: "输入价格", value: p.fields.inputPrice,
            onInput: e => { p.fields.inputPrice = e.target.value; },
          })
        ),
        _pfRow("输出价格（可选）",
          h("input", {
            type: "number", min: "0", step: "any", inputmode: "decimal",
            placeholder: "输出价格", value: p.fields.outputPrice,
            onInput: e => { p.fields.outputPrice = e.target.value; },
          })
        ),
        h("div", { class: "pf-row pf-row-left" }, [
          h("label", {
            style: "display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;color:#475569;width:auto;flex-shrink:0",
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
          onClick: e => { e.stopPropagation(); callbacks.onClear && callbacks.onClear(p.key); },
        }, t('settings.clear')),
        h("button", {
          class: "btn-sm btn-sm-ghost",
          disabled: isBusy,
          onClick: e => { e.stopPropagation(); callbacks.onTest && callbacks.onTest(p.key); },
        }, p.busy === "test" ? (t('settings.testing') || "测试中…") : (t('settings.test') || "测试")),
        h("button", {
          class: "btn-sm btn-sm-primary",
          disabled: isBusy,
          onClick: e => { e.stopPropagation(); callbacks.onSave && callbacks.onSave(p.key); },
        }, p.busy === "save" ? (t('settings.saving') || "保存中…") : t('settings.save')),
      ]),
      p.msg.text ? h("div", { class: `provider-msg ${p.msg.type}` }, p.msg.text) : null,
    ]);
  }

  function _pfRow(labelText, inputEl, hintEl) {
    const kids = [h("label", null, labelText), inputEl];
    if (hintEl) kids.push(h("div", { class: "pf-hint" }, hintEl));
    return h("div", { class: "pf-row" }, kids);
  }

  // 本地模型 API Key 提示语（label 为 t('settings.local_no_key') 或 fallback）
  function _localKeyHint() {
    return t('settings.local_no_key') || "本地模型无需 API Key，可留空";
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
      inputPrice: cfg.input_price_per_million != null ? String(cfg.input_price_per_million) : "",
      outputPrice: cfg.output_price_per_million != null ? String(cfg.output_price_per_million) : "",
      think:   !!cfg.enable_thinking,
      budget:  cfg.thinking_budget != null ? String(cfg.thinking_budget) : "8000",
    };
  }

  function setProviders(configs, defaults) {
    // 保留现有 fields（用户正在输入的未保存值），仅刷新 hasKey/cfg。
    // 例外：hasKey 从 true→false（刚清除）时重置 fields 为 defaults。
    const newProviders = Object.entries(defaults).map(([key, def]) => {
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
        expanded: existing ? existing.expanded : false,
      };
    });
    // 按 BUILTIN_ORDER 排序，确保 Ollama 始终在最底部。
    state.providers = newProviders.sort((a, b) => {
      const ia = BUILTIN_ORDER.indexOf(a.key);
      const ib = BUILTIN_ORDER.indexOf(b.key);
      if (ia !== -1 && ib !== -1) return ia - ib;
      if (ia !== -1) return -1;
      if (ib !== -1) return 1;
      return a.label.localeCompare(b.label);
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
        inputPrice: cfg.input_price_per_million != null ? String(cfg.input_price_per_million) : "",
        outputPrice: cfg.output_price_per_million != null ? String(cfg.output_price_per_million) : "",
        think: !!cfg.enable_thinking,
        budget: cfg.thinking_budget != null ? String(cfg.thinking_budget) : "8000",
      };
    } else {
      // 添加模式：清空
      state.form = { name: "", url: "", model: "", key: "", ctx: "", output: "", inputPrice: "", outputPrice: "", think: false, budget: "8000" };
    }
    state.formOpen = true;
    _renderForm();
    // 表单展开后自动滚动到 modal 底部 — modal 有 max-height:88vh; overflow-y:auto，
    // 4 个 provider 卡片可能已撑满视口，不滚动的话用户看不到底部展开的表单。
    // 用 scrollTop 而非 scrollIntoView：更可靠地滚动到 .modal 容器底部。
    requestAnimationFrame(() => {
      const modal = root3.closest(".modal");
      if (modal && modal.scrollHeight > modal.clientHeight) {
        modal.scrollTop = modal.scrollHeight;
      }
    });
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
    // 直接从静态 DOM 元素读值（不再依赖 Vue reactive state 同步）
    const _v = (id) => { const el = document.getElementById(id); return el ? el.value : ""; };
    const _chk = (id) => { const el = document.getElementById(id); return el ? el.checked : false; };
    return {
      name:       _v("ac-name"),
      url:        _v("ac-url"),
      model:      _v("ac-model"),
      key:        _v("ac-key"),
      ctx:        _v("ac-ctx"),
      output:     _v("ac-output"),
      inputPrice: _v("ac-input-price"),
      outputPrice:_v("ac-output-price"),
      think:      _chk("ac-think"),
      budget:     _v("ac-budget"),
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
