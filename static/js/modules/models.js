// Model selector + Settings panel (built-in providers + custom models).
(function () {
  const { $ } = window.BAA.dom;
  const state = window.BAA.state;

  const COMMON_ICON = "/static/Images/icon.png";
  const BUILTIN_META = {
    deepseek:   { label: "DeepSeek",         icon: COMMON_ICON },
    openai:     { label: "OpenAI / ChatGPT", icon: COMMON_ICON },
    atlascloud: { label: "AtlasCloud",       icon: COMMON_ICON },
  };

  // 首次加载标志 — loadModels 第一次运行时为 true，此后为 false。
  // 只有首次才允许自动选中第一个模型；后续刷新（保存配置、删除模型等触发）
  // 必须保留用户当前的选择，绝不重置。
  let _firstLoad = true;

  async function loadModels() {
    const r = await fetch("/api/models");
    const models = await r.json();
    state.modelConfigs = models;
    const sel = $("model-sel");
    const prevValue = sel.value;   // 刷新前用户选中的值
    sel.innerHTML = `<option value="">${t('sidebar.model_placeholder')}</option>`;
    for (const [key, cfg] of Object.entries(models)) {
      if (!cfg.has_api_key) continue;
      const opt = document.createElement("option");
      opt.value = key;
      // 自定义模型：优先显示供应商名称（ac-name），fallback 到 Model ID，再 fallback 到 key
      // 内置模型：显示 BUILTIN_META 的 label（DeepSeek / OpenAI 等）
      opt.textContent = cfg.is_custom
        ? (cfg.name || cfg.model || key)
        : (BUILTIN_META[key]?.label || cfg.model || key);
      sel.appendChild(opt);
    }

    if (prevValue && [...sel.options].some(o => o.value === prevValue)) {
      // 列表刷新后仍有之前选中的模型 → 恢复，不触发 onModelChange
      sel.value = prevValue;
    } else if (_firstLoad && sel.options.length > 1) {
      // 仅首次加载且之前没有选中值时，才自动选第一个并通知后端
      sel.selectedIndex = 1;
      onModelChange();
    }
    // 后续刷新时若 prevValue 已不存在（模型被删除），保持空选择，不强制切换
    _firstLoad = false;
  }

  async function onModelChange() {
    const v = $("model-sel").value;
    // Switching the model invalidates any previous "tested OK" indicator.
    _resetModelDot();
    if (!v || !state.SID) return;
    await fetch(`/api/session/${state.SID}/model`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: v }),
    });
    // Auto-test the freshly selected model. Fire-and-forget — the function
    // owns its own UI feedback (dot colour + failure modal).
    testModel(v);
  }

  // ── Model connection test ─────────────────────────────────────────
  // Sidebar model-dot states:
  //   default → blue (.sb-status-dot--info)  "model selected, not yet tested"
  //   testing → blue + pulsing aura via .testing class
  //   success → green (.on)                  "tested OK in this session"
  //   failure → blue (.--info) + failure modal with full error
  //
  // Test entry points:
  //   1. onModelChange()        — auto-runs after the user picks a model in the sidebar
  //   2. testProvider(key) action — manual "测试" button in the Settings modal
  //
  // Both go through `testModel(provider)`. The sidebar dot only updates when
  // the provider being tested matches the one currently selected in the sidebar.
  function _setDotState(provider, st) {
    const dot = $("model-dot");
    if (!dot) return;
    // Only mutate sidebar dot if the test is for the currently-selected provider.
    const current = $("model-sel")?.value;
    if (provider !== current) return;
    dot.classList.toggle("testing", st === "testing");
    dot.classList.toggle("on",      st === "ok");
  }
  function _resetModelDot() {
    const dot = $("model-dot");
    if (dot) dot.classList.remove("on", "testing");
  }

  function _setProviderRowState(provider, state, message) {
    // Settings modal — show the test outcome inline below the provider card,
    // reusing the .provider-msg element saveBuiltin() also writes to.
    const msgEl = $(`pmsg-${provider}`);
    if (!msgEl) return;
    if (state === "testing") {
      msgEl.className = "provider-msg";
      msgEl.textContent = t('settings.testing') || "测试中…";
    } else if (state === "ok") {
      msgEl.className = "provider-msg ok";
      msgEl.textContent = message || (t('settings.test_ok') || "连接成功");
    } else if (state === "fail") {
      msgEl.className = "provider-msg err";
      msgEl.textContent = message || (t('settings.test_fail') || "连接失败");
    }
  }

  async function testModel(provider) {
    provider = provider || $("model-sel").value;
    if (!provider) {
      window.BAA.overlay.toast(t('sidebar.model_test_no_select') || "请先选择模型", "err");
      return;
    }
    _setDotState(provider, "testing");
    _setProviderRowState(provider, "testing");

    let data;
    try {
      const r = await fetch("/api/models/test", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider }),
      });
      data = await r.json();
    } catch (e) {
      data = { success: false, message: String(e), provider };
    }

    if (data.success) {
      _setDotState(provider, "ok");
      _setProviderRowState(provider, "ok",
        t('settings.test_ok_with_model', { model: data.model || provider })
          || `连接成功 · ${data.model || provider}`);
      window.BAA.overlay.toast(
        t('sidebar.model_test_ok', { model: data.model || provider })
          || `${data.model || provider} 连接成功`,
        "ok"
      );
    } else {
      _setDotState(provider, "default");   // back to blue
      _setProviderRowState(provider, "fail",
        (t('settings.test_fail') || "连接失败"));
      // Show a modal so the user can read the full error (LLM API errors are long).
      const meta = $("model-test-meta");
      const err  = $("model-test-error");
      if (meta) {
        const modelLabel = data.model || provider;
        meta.textContent = `${t('sidebar.model') || '模型'}: ${modelLabel}`;
      }
      if (err) err.textContent = data.message || "Unknown error";
      window.openOverlay("ov-model-test");
    }
  }

  async function loadBuiltinProviders() {
    const [cfgR, defR] = await Promise.all([
      fetch("/api/models"), fetch("/api/models/defaults"),
    ]);
    const configs  = await cfgR.json();
    const defaults = await defR.json();
    renderBuiltinProviders(configs, defaults);
    renderCustomList(configs);
  }

  function renderBuiltinProviders(configs, defaults) {
    const container = $("builtin-providers");
    container.innerHTML = "";
    for (const [key, def] of Object.entries(defaults)) {
      const meta   = BUILTIN_META[key] || { label: key, icon: "/Images/icon.png" };
      const cfg    = configs[key] || {};
      const hasKey = cfg.has_api_key;
      container.innerHTML += `
        <div class="provider-card">
          <div class="provider-head">
            <img class="provider-icon" src="${meta.icon}" alt="${meta.label}">
            <span class="provider-name">${meta.label}</span>
            <span class="provider-status ${hasKey ? "set" : "unset"}" id="ps-${key}">
              ${hasKey ? t('settings.configured') : t('settings.not_configured')}
            </span>
          </div>
          <div class="provider-fields">
            <div class="pf-row">
              <label>${t('settings.api_key')}</label>
              <input type="password" id="pk-${key}" placeholder="${t('settings.api_key_ph')}">
            </div>
            <div class="pf-row">
              <label>${t('settings.base_url')}</label>
              <input type="text" id="pu-${key}" value="${cfg.base_url || def.base_url}" placeholder="${def.base_url}">
            </div>
            <div class="pf-row">
              <label>${t('settings.model')}</label>
              <input type="text" id="pm-${key}" value="${cfg.model || def.model}" placeholder="${def.model}">
            </div>
            <div class="pf-row">
              <label>${t('settings.ctx_window')}</label>
              <input type="number" id="pctx-${key}" value="${cfg.context_window ?? def.context_window ?? ''}" placeholder="${t('settings.ctx_ph')}">
            </div>
            <div class="pf-row">
              <label>${t('settings.max_output')}</label>
              <input type="number" id="pout-${key}" value="${cfg.max_output_tokens ?? def.max_output_tokens ?? ''}" placeholder="${t('settings.out_ph')}">
            </div>
            <div class="pf-row" style="align-items:center">
              <label>${t('settings.thinking')}</label>
              <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;color:#475569">
                <input type="checkbox" id="pthink-${key}" ${cfg.enable_thinking ? "checked" : ""}
                  data-action="toggleThinkBudget:${key}">
                ${t('settings.thinking_label')}
              </label>
            </div>
            <div class="pf-row" id="pbudget-row-${key}" style="display:${cfg.enable_thinking ? 'flex' : 'none'};align-items:center">
              <label>${t('settings.budget') || '思考预算（tokens）'}</label>
              <input type="number" id="pbudget-${key}" value="${cfg.thinking_budget ?? 8000}" min="1000" max="100000" step="1000">
            </div>
          </div>
          <div class="provider-actions">
            <button class="btn-sm btn-sm-danger"  data-action="clearBuiltin:${key}">${t('settings.clear')}</button>
            <button class="btn-sm btn-sm-ghost"   data-action="testProvider:${key}">${t('settings.test') || '测试'}</button>
            <button class="btn-sm btn-sm-primary" data-action="saveBuiltin:${key}">${t('settings.save')}</button>
          </div>
          <div class="provider-msg" id="pmsg-${key}"></div>
        </div>`;
    }
  }

  function renderCustomList(configs) {
    const list    = $("custom-list");
    const customs = Object.entries(configs).filter(([, v]) => v.is_custom);
    if (!customs.length) {
      list.innerHTML = `<div class="custom-empty">${t('custom_empty')}</div>`;
      return;
    }
    list.innerHTML = customs.map(([key, cfg]) => `
      <div class="custom-item">
        <span class="ci-name">${cfg.name || cfg.model || key}</span>
        <span class="ci-model">${cfg.model || cfg.base_url || ""}</span>
        <button class="btn-sm btn-sm-ghost"  data-action="testProvider:${key}">${t('settings.test') || '测试'}</button>
        <button class="btn-sm btn-sm-ghost"  data-action="editCustom:${key}">${t('settings.edit_custom') || '编辑'}</button>
        <button class="btn-sm btn-sm-danger" data-action="deleteCustom:${key}">${t('settings.del_custom')}</button>
      </div>`).join("");
  }

  function editCustomModel(provider) {
    state._editingCustomProvider = provider;
    const f = $("add-custom-form");
    if (!f.classList.contains("show")) f.classList.add("show");

    fetch("/api/models").then(r => r.json()).then(configs => {
      const cfg = configs[provider];
      if (!cfg) return;
      $("ac-name").value   = (cfg.name || "");    // 供应商名称（显示用）
      $("ac-url").value    = (cfg.base_url || "");
      $("ac-model").value  = (cfg.model || "");   // Model ID（传入 API）
      $("ac-key").value    = "";
      $("ac-ctx").value    = cfg.context_window != null ? cfg.context_window : "";
      $("ac-output").value = cfg.max_output_tokens != null ? cfg.max_output_tokens : "";
      $("ac-think").checked = !!cfg.enable_thinking;
      $("ac-budget").value  = cfg.thinking_budget ?? 8000;
      $("ac-budget-row").style.display = cfg.enable_thinking ? "flex" : "none";
      $("ac-err").textContent = "";
      $("ac-ok").textContent  = t('settings.editing_hint') || `编辑中：${provider}`;
      f.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }

  async function addCustomModel() {
    const ctxRaw    = $("ac-ctx").value.trim();
    const outRaw    = $("ac-output").value.trim();
    const budgetRaw = $("ac-budget").value.trim();
    const data = {
      name:            $("ac-name").value.trim(),
      base_url:        $("ac-url").value.trim(),
      model_name:      $("ac-model").value.trim(),
      api_key:         $("ac-key").value.trim(),
      enable_thinking: $("ac-think").checked,
      thinking_budget: budgetRaw ? parseInt(budgetRaw) : 8000,
      ...(ctxRaw ? { context_window:    parseInt(ctxRaw) } : {}),
      ...(outRaw ? { max_output_tokens: parseInt(outRaw) } : {}),
    };
    $("ac-err").textContent = "";
    $("ac-ok").textContent  = "";

    const resetForm = () => {
      ["ac-name", "ac-url", "ac-model", "ac-key", "ac-ctx", "ac-output", "ac-budget"]
        .forEach(id => $(id).value = "");
      $("ac-think").checked = false;
      $("ac-budget-row").style.display = "none";
    };

    if (state._editingCustomProvider) {
      const body = {
        provider:        state._editingCustomProvider,
        base_url:        data.base_url,
        model_name:      data.model_name,
        api_key:         data.api_key,
        enable_thinking: data.enable_thinking,
        thinking_budget: data.thinking_budget,
        ...(ctxRaw ? { context_window:    parseInt(ctxRaw) } : {}),
        ...(outRaw ? { max_output_tokens: parseInt(outRaw) } : {}),
      };
      const r = await fetch("/api/models/update", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (d.error) {
        $("ac-err").textContent = d.error;
      } else {
        $("ac-ok").textContent = d.message || t('settings.save_ok');
        state._editingCustomProvider = null;
        resetForm();
        await Promise.all([loadModels(), loadBuiltinProviders()]);
        setTimeout(toggleAddCustom, 1200);
      }
      return;
    }

    const r = await fetch("/api/models/add", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const d = await r.json();
    if (d.error) {
      $("ac-err").textContent = d.error;
    } else {
      $("ac-ok").textContent = d.message;
      resetForm();
      await Promise.all([loadModels(), loadBuiltinProviders()]);
      setTimeout(toggleAddCustom, 1200);
    }
  }

  function toggleAddCustom() {
    state._editingCustomProvider = null;
    const f = $("add-custom-form");
    f.classList.toggle("show");
    if (f.classList.contains("show")) $("ac-name").focus();
  }

  async function saveBuiltin(key) {
    const apiKey  = $(`pk-${key}`).value.trim();
    const baseUrl = $(`pu-${key}`).value.trim();
    const model   = $(`pm-${key}`).value.trim();
    const ctxRaw  = $(`pctx-${key}`).value.trim();
    const outRaw  = $(`pout-${key}`).value.trim();
    const msgEl   = $(`pmsg-${key}`);
    if (!apiKey) { msgEl.className = "provider-msg err"; msgEl.textContent = t('settings.api_key_empty'); return; }
    msgEl.textContent = t('settings.saving');
    const budgetRaw = $(`pbudget-${key}`)?.value.trim();
    const body = {
      provider: key, api_key: apiKey, base_url: baseUrl, model,
      enable_thinking: $(`pthink-${key}`).checked,
      thinking_budget: budgetRaw ? parseInt(budgetRaw) : 8000,
    };
    if (ctxRaw) body.context_window    = parseInt(ctxRaw);
    if (outRaw) body.max_output_tokens = parseInt(outRaw);
    const r = await fetch("/api/models/set-builtin", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (d.ok) {
      msgEl.className = "provider-msg ok"; msgEl.textContent = t('settings.save_ok');
      $(`ps-${key}`).className = "provider-status set";
      $(`ps-${key}`).textContent = t('settings.configured');
      $(`pk-${key}`).value = "";
      await loadModels();
    } else {
      msgEl.className = "provider-msg err"; msgEl.textContent = d.error || t('update.fail');
    }
  }

  async function clearBuiltin(key) {
    if (!confirm(t('confirm.clear_builtin', { label: BUILTIN_META[key]?.label || key }))) return;
    const r = await fetch("/api/models/clear-builtin", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: key }),
    });
    const d = await r.json();
    if (d.ok) {
      $(`ps-${key}`).className   = "provider-status unset";
      $(`ps-${key}`).textContent = t('settings.not_configured');
      const msgEl = $(`pmsg-${key}`);
      msgEl.className = "provider-msg ok"; msgEl.textContent = t('settings.cleared');
      await loadModels();
    }
  }

  async function deleteCustom(provider) {
    if (!confirm(t('confirm.delete_custom'))) return;
    await fetch("/api/models/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    });
    await Promise.all([loadModels(), loadBuiltinProviders()]);
  }

  function toggleThinkBudget(key) {
    const cb  = $(`pthink-${key}`);
    const row = $(`pbudget-row-${key}`);
    if (cb && row) row.style.display = cb.checked ? "flex" : "none";
  }

  window.BAA.models = {
    loadModels, onModelChange, loadBuiltinProviders, renderBuiltinProviders, renderCustomList,
    editCustomModel, addCustomModel, toggleAddCustom, saveBuiltin, clearBuiltin, deleteCustom,
    toggleThinkBudget, testModel,
  };
})();
