// LLM 模型 domain — extracted verbatim from app_settings.js.
// Functions carry over unchanged; closure refs `uiState`/`draw` resolve via ctx.
import { _renderPanelHead } from "./shared.js";

const Vue = window.Vue;

export function createLlmDomain(ctx) {
  const { uiState, draw, state } = ctx;

function renderLlm() {
  /* Full interactive LLM config: built-in providers (expand/collapse, API key,
     save/test/clear) + custom model list + add-custom form.
     Mirrors the quick-settings modal (⚙ LLM模型) but rendered inside the
     app-settings panel, reusing the same /api/models endpoints. */

  /* ── trigger data load (once, with guard) ── */
  if (!uiState._llmLoading && !uiState._llmLoaded) {
    uiState._llmLoading = true;
    loadLlmData().then(function () {
      uiState._llmLoaded = true;
      uiState._llmLoading = false;
      draw();
    });
    // Fall through with whatever data we have (or empty) — draw() will be
    // called again after the data arrives.
  }

  const configs = state.modelConfigs || {};
  const llmDefaults = uiState.modelDefaults || {};  // loaded by loadLlmData()

 const BUILTIN_META = {
    deepseek:   { label: "DeepSeek",         color: "#4D6BFE" },
    openai:     { label: "OpenAI / ChatGPT", color: "#10A37F" },
    atlascloud: { label: "AtlasCloud",       color: "#6366F1" },
    ollama:     { label: "Ollama (本地)",     local: true, color: "#7C3AED" },
 };
  const BUILTIN_ORDER = ["deepseek", "openai", "atlascloud", "ollama"];

  /* ── LLM tab reactive state ────────────────────── */
  if (!uiState._llmProviders) {
    uiState._llmProviders = [];
    uiState._llmCustoms = [];
    uiState._llmFormOpen = false;
    uiState._llmEditingKey = null;
    uiState._llmForm = { name: "", url: "", model: "", key: "", ctx: "", output: "", inputPrice: "", outputPrice: "", think: false, budget: "8000" };
    uiState._llmFormMsg = { err: "", ok: "" };
    uiState._llmMsg = null;
  }

  /* ── build provider state from defaults + configs ── */
  var newProviders = BUILTIN_ORDER.map(function (key) {
    var meta = BUILTIN_META[key] || { label: key, icon: "/static/Images/icon.png" };
    var def = llmDefaults[key] || {};
    var cfg = configs[key] || {};
    var hasKey = !!cfg.has_api_key;
    var existing = (uiState._llmProviders || []).find(function (p) { return p.key === key; });
    var wasCleared = existing && existing.hasKey && !hasKey;
    return {
      key: key, label: meta.label, icon: meta.icon, local: !!meta.local, color: meta.color,
      hasKey: hasKey, defaults: def, cfg: cfg,
      expanded: existing ? existing.expanded : false,
      busy: existing ? existing.busy : null,
      msg: existing ? existing.msg : { type: "", text: "" },
      fields: (existing && !wasCleared) ? existing.fields : {
        apiKey: "", baseUrl: cfg.base_url || def.base_url || "",
        model: cfg.model || def.model || "",
        ctx: cfg.context_window != null ? String(cfg.context_window) : (def.context_window != null ? String(def.context_window) : ""),
        output: cfg.max_output_tokens != null ? String(cfg.max_output_tokens) : (def.max_output_tokens != null ? String(def.max_output_tokens) : ""),
        inputPrice: cfg.input_price_per_million != null ? String(cfg.input_price_per_million) : "",
        outputPrice: cfg.output_price_per_million != null ? String(cfg.output_price_per_million) : "",
        think: !!cfg.enable_thinking,
        budget: cfg.thinking_budget != null ? String(cfg.thinking_budget) : "8000",
      },
    };
  });
  uiState._llmProviders = newProviders;

  /* ── custom models ── */
  uiState._llmCustoms = Object.entries(configs)
    .filter(function (e) { return e[1].is_custom; })
    .map(function (e) { var key = e[0]; var c = e[1]; return { key: key, name: c.name || "", model: c.model || "", baseUrl: c.base_url || "" }; });

  /* ── render the panel ── */
  return Vue.h("section", { class: "app-settings-panel llm-settings-panel" }, [
    _renderPanelHead("LLM模型", "管理聊天、分析、团队协作使用的 LLM 后端。", [
      Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", onClick: function () { loadLlmData().then(draw); } }, "刷新"),
      Vue.h("button", { class: "btn-sm btn-sm-primary", type: "button", onClick: function () { _llmToggleForm(); draw(); } },
        uiState._llmFormOpen ? "收起添加表单" : "＋ 添加自定义模型"),
    ]),

    /* ── global status banner ── */
    uiState._llmMsg ? Vue.h("div", { class: "llm-global-msg " + (uiState._llmMsg.type || "ok") }, uiState._llmMsg.text) : null,

    /* ── built-in providers ── */
    Vue.h("section", { class: "settings-sec" }, [
      Vue.h("div", { class: "settings-sec-title" }, "内置模型提供商"),
      Vue.h("div", null, uiState._llmProviders.map(function (p) { return _llmProviderCard(p); })),
    ]),

    /* ── custom models ── */
    Vue.h("section", { class: "settings-sec" }, [
      Vue.h("div", { class: "settings-sec-title" }, "自定义模型"),
      uiState._llmCustoms.length
        ? Vue.h("div", null, uiState._llmCustoms.map(function (c) { return _llmCustomItem(c); }))
        : Vue.h("div", { class: "custom-empty" }, "暂无自定义模型"),
      _llmFormContent(),
    ]),
  ]);
}

/* ── LLM tab helper functions ──────────────────────── */

function loadLlmData() {
  return Promise.all([
    fetch("/api/models").then(function (r) { return r.json(); }),
    fetch("/api/models/defaults").then(function (r) { return r.json(); }),
  ]).then(function (results) {
    state.modelConfigs = results[0];
    uiState.modelDefaults = results[1];
    return results;
  });
}

function _llmToggleForm() {
  uiState._llmFormOpen = !uiState._llmFormOpen;
  uiState._llmEditingKey = null;
  uiState._llmForm = { name: "", url: "", model: "", key: "", ctx: "", output: "", inputPrice: "", outputPrice: "", think: false, budget: "8000" };
  uiState._llmFormMsg = { err: "", ok: "" };
}

function _llmEditCustom(key) {
  var cfg = state.modelConfigs ? state.modelConfigs[key] : null;
  if (!cfg) return;
  uiState._llmEditingKey = key;
  uiState._llmForm = {
    name: cfg.name || "", url: cfg.base_url || "", model: cfg.model || "", key: "",
    ctx: cfg.context_window != null ? String(cfg.context_window) : "",
    output: cfg.max_output_tokens != null ? String(cfg.max_output_tokens) : "",
    inputPrice: cfg.input_price_per_million != null ? String(cfg.input_price_per_million) : "",
    outputPrice: cfg.output_price_per_million != null ? String(cfg.output_price_per_million) : "",
    think: !!cfg.enable_thinking,
    budget: cfg.thinking_budget != null ? String(cfg.thinking_budget) : "8000",
  };
  uiState._llmFormOpen = true;
  uiState._llmFormMsg = { err: "", ok: "" };
  draw();
}

function _llmFormContent() {
  if (!uiState._llmFormOpen) return null;
  var f = uiState._llmForm;
  var m = uiState._llmFormMsg;
  var editing = !!uiState._llmEditingKey;

  return Vue.h("div", { class: "add-custom-form show" }, [
    Vue.h("div", { class: "acf-section-title" }, editing ? "编辑自定义模型" : "添加自定义模型"),

    /* ── 基本信息 ── */
    Vue.h("div", { class: "acf-field" }, [
      Vue.h("label", null, "供应商名称"),
      Vue.h("input", { type: "text", placeholder: "例如 DeepSeek", value: f.name, onInput: function (e) { f.name = e.target.value; } }),
    ]),
    Vue.h("div", { class: "acf-field" }, [
      Vue.h("label", null, "API Base URL"),
      Vue.h("input", { type: "text", placeholder: "例如 https://api.deepseek.com", value: f.url, onInput: function (e) { f.url = e.target.value; } }),
    ]),
    Vue.h("div", { class: "acf-field" }, [
      Vue.h("label", null, "Model ID"),
      Vue.h("input", { type: "text", placeholder: "例如 deepseek-chat", value: f.model, onInput: function (e) { f.model = e.target.value; } }),
    ]),
    Vue.h("div", { class: "acf-field" }, [
      Vue.h("label", null, "API Key"),
      Vue.h("input", { type: "password", autocomplete: "off", "data-lpignore": "true", placeholder: editing ? "留空保留原 Key" : "输入 API Key", value: f.key, onInput: function (e) { f.key = e.target.value; } }),
    ]),

    /* ── 模型参数 ── */
    Vue.h("div", { class: "acf-section-title" }, "模型参数"),
    Vue.h("div", { class: "acf-row-2" }, [
      Vue.h("div", { class: "acf-field" }, [
        Vue.h("label", null, "上下文窗口"),
        Vue.h("input", { type: "number", placeholder: "1000000", value: f.ctx, onInput: function (e) { f.ctx = e.target.value; } }),
      ]),
      Vue.h("div", { class: "acf-field" }, [
        Vue.h("label", null, "最大输出"),
        Vue.h("input", { type: "number", placeholder: "384000", value: f.output, onInput: function (e) { f.output = e.target.value; } }),
      ]),
    ]),

    /* ── 价格（可选） ── */
    Vue.h("div", { class: "acf-section-title" }, "价格（可选）"),
    Vue.h("div", { class: "acf-row-2" }, [
      Vue.h("div", { class: "acf-field" }, [
        Vue.h("label", null, "输入价格"),
        Vue.h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", placeholder: "每百万 token 价格", value: f.inputPrice, onInput: function (e) { f.inputPrice = e.target.value; } }),
      ]),
      Vue.h("div", { class: "acf-field" }, [
        Vue.h("label", null, "输出价格"),
        Vue.h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", placeholder: "每百万 token 价格", value: f.outputPrice, onInput: function (e) { f.outputPrice = e.target.value; } }),
      ]),
    ]),

    /* ── 思考模式 ── */
    Vue.h("label", { class: "acf-check-row" }, [
      Vue.h("input", { type: "checkbox", checked: f.think, onChange: function (e) { f.think = e.target.checked; draw(); } }),
      Vue.h("span", null, "启用思考模式"),
    ]),
    f.think ? Vue.h("div", { class: "acf-field" }, [
      Vue.h("label", null, "思考预算（tokens）"),
      Vue.h("input", { type: "number", min: "1000", max: "100000", step: "1000", value: f.budget, onInput: function (e) { f.budget = e.target.value; } }),
    ]) : null,

    /* ── 消息 ── */
    m.err ? Vue.h("div", { class: "msg-err" }, m.err) : null,
    m.ok  ? Vue.h("div", { class: "msg-ok" }, m.ok)   : null,

    /* ── 按钮 ── */
    Vue.h("div", { class: "acf-actions" }, [
      Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", onClick: function () { _llmToggleForm(); draw(); } }, "取消"),
      Vue.h("button", { class: "btn-sm btn-sm-primary", type: "button", onClick: function () { _llmSubmitForm(); } }, editing ? "保存修改" : "添加模型"),
    ]),
  ]);
}

function _llmIsLocalUrl(url) {
  if (!url) return false;
  var u = String(url).toLowerCase();
  return ["localhost", "127.0.0.1", "0.0.0.0", "[::1]", "0:0:0:0:0:0:0:1"]
    .some(function (m) { return u.includes(m); });
}

function _llmProviderCard(p) {
  var isBusy = !!p.busy;
  var isExpanded = !!p.expanded;

  var avatarColor = p.color || "var(--color-primary)";
  var firstLetter = (p.label || p.key || "?").charAt(0);
  var header = Vue.h("div", {
    class: "provider-head",
    onClick: function () { p.expanded = !isExpanded; draw(); },
  }, [
    Vue.h("div", { class: "provider-avatar", style: "--avatar-color: " + avatarColor },
      p.icon ? Vue.h("img", { src: p.icon, alt: p.label, class: "provider-logo" }) : firstLetter),
    Vue.h("span", { class: "provider-name" }, p.label),
    Vue.h("span", { class: "provider-status " + (p.hasKey ? "set" : "unset") },
      p.hasKey ? "已配置" : "未配置"),
    Vue.h("span", { class: "provider-toggle " + (isExpanded ? "open" : "") },
      Vue.h("svg", { viewBox: "0 0 24 24" }, [Vue.h("polyline", { points: "6 9 12 15 18 9" })])),
  ]);

  if (!isExpanded) {
    return Vue.h("div", { class: "provider-card collapsed", key: p.key }, [ header,
      p.msg.text ? Vue.h("div", { class: "provider-msg " + p.msg.type }, p.msg.text) : null,
    ]);
  }

  var isLocal = !!(p.local) || _llmIsLocalUrl(p.fields.baseUrl);
  return Vue.h("div", { class: "provider-card expanded", key: p.key }, [
    header,
   Vue.h("div", { class: "provider-fields" }, [
     _llmPfGroup("连接配置", [
       _llmPfField("API Key",
         Vue.h("input", {
           type: isLocal ? "text" : "password", autocomplete: "off", "data-lpignore": "true",
           placeholder: isLocal ? "本地模型无需 API Key，可留空" : "输入 API Key",
           value: p.fields.apiKey,
           onInput: function (e) { p.fields.apiKey = e.target.value; },
         })
       ),
       _llmPfField("Base URL",
         Vue.h("input", { type: "text", autocomplete: "off", placeholder: p.defaults.base_url, value: p.fields.baseUrl, onInput: function (e) { p.fields.baseUrl = e.target.value; } })
       ),
       _llmPfField("模型",
         Vue.h("input", { type: "text", autocomplete: "off", placeholder: p.defaults.model, value: p.fields.model, onInput: function (e) { p.fields.model = e.target.value; } })
       ),
     ]),
     _llmPfGroup("模型参数", [
       Vue.h("div", { class: "pf-grid-2" }, [
         _llmPfField("上下文窗口",
           Vue.h("input", { type: "number", autocomplete: "off", placeholder: "默认 1000000", value: p.fields.ctx, onInput: function (e) { p.fields.ctx = e.target.value; } })
         ),
         _llmPfField("最大输出",
           Vue.h("input", { type: "number", autocomplete: "off", placeholder: "默认 384000", value: p.fields.output, onInput: function (e) { p.fields.output = e.target.value; } })
         ),
       ]),
       Vue.h("label", { class: "pf-check-row" }, [
         Vue.h("input", { type: "checkbox", checked: p.fields.think, onChange: function (e) { p.fields.think = e.target.checked; draw(); } }),
         "启用思考模式",
       ]),
       p.fields.think ? _llmPfField("思考预算（tokens）",
         Vue.h("input", { type: "number", min: "1000", max: "100000", step: "1000", value: p.fields.budget, onInput: function (e) { p.fields.budget = e.target.value; } })
       ) : null,
     ]),
     _llmPfGroup("价格（可选）", [
       Vue.h("div", { class: "pf-grid-2" }, [
         _llmPfField("输入价格",
           Vue.h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", placeholder: "（可选）", value: p.fields.inputPrice, onInput: function (e) { p.fields.inputPrice = e.target.value; } })
         ),
         _llmPfField("输出价格",
           Vue.h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", placeholder: "（可选）", value: p.fields.outputPrice, onInput: function (e) { p.fields.outputPrice = e.target.value; } })
         ),
       ]),
     ]),
   ]),
   Vue.h("div", { class: "provider-actions" }, [
      Vue.h("button", { class: "btn-sm btn-sm-danger", disabled: isBusy, onClick: function (e) { e.stopPropagation(); _llmClearBuiltin(p.key); } }, "清除"),
      Vue.h("button", { class: "btn-sm btn-sm-ghost", disabled: isBusy, onClick: function (e) { e.stopPropagation(); _llmTestModel(p.key); } },
        p.busy === "test" ? "测试中…" : "测试"),
      Vue.h("button", { class: "btn-sm btn-sm-primary", disabled: isBusy, onClick: function (e) { e.stopPropagation(); _llmSaveBuiltin(p.key); } },
        p.busy === "save" ? "保存中…" : "保存"),
    ]),
    p.msg.text ? Vue.h("div", { class: "provider-msg " + p.msg.type }, p.msg.text) : null,
  ]);
}

function _llmPfRow(labelText, inputEl) {
  return Vue.h("div", { class: "pf-row" }, [
    Vue.h("label", null, labelText),
    inputEl,
  ]);
}

 function _llmPfField(labelText, inputEl) {
 return Vue.h("div", { class: "pf-field" }, [
   Vue.h("label", null, labelText),
   inputEl,
 ]);
 }
 
 function _llmPfGroup(labelText, children) {
   var kids = [Vue.h("div", { class: "pf-group-label" }, labelText)];
   children.forEach(function (c) { kids.push(c); });
   return Vue.h("div", { class: "pf-group" }, kids);
 }

function _llmCustomItem(c) {
  return Vue.h("div", { class: "custom-item" }, [
    Vue.h("span", { class: "ci-name" }, c.name || c.model || c.key),
    Vue.h("span", { class: "ci-model" }, c.model || c.baseUrl || ""),
    Vue.h("button", { class: "btn-sm btn-sm-ghost", onClick: function () { _llmTestModel(c.key); } }, "测试"),
    Vue.h("button", { class: "btn-sm btn-sm-ghost", onClick: function () { _llmEditCustom(c.key); } }, "编辑"),
    Vue.h("button", { class: "btn-sm btn-sm-danger", onClick: function () { _llmDeleteCustom(c.key); } }, "删除"),
  ]);
}

/* ── LLM tab API actions ──────────────────────────── */

function _llmSetProviderBusy(key, busy) {
  var p = uiState._llmProviders.find(function (x) { return x.key === key; });
  if (p) { p.busy = busy || null; p.msg = { type: "", text: "" }; }
  uiState._llmMsg = null;
  draw();
}

function _llmSetProviderMsg(key, type, text) {
  var p = uiState._llmProviders.find(function (x) { return x.key === key; });
  if (p) { p.msg = { type: type, text: text }; }
  uiState._llmMsg = null;
  draw();
}

function _llmSaveBuiltin(key) {
  var p = uiState._llmProviders.find(function (x) { return x.key === key; });
  if (!p) return;
  var f = p.fields;
  var apiKey = f.apiKey.trim();
  var isLocalProvider = key === "ollama" || _llmIsLocalUrl(f.baseUrl);
  if (!apiKey && !isLocalProvider) {
    _llmSetProviderMsg(key, "err", "请输入 API Key");
    return;
  }
  _llmSetProviderBusy(key, "save");
  _llmSetProviderMsg(key, "", "保存中…");
  var body = { provider: key, api_key: apiKey, base_url: f.baseUrl.trim(), model: f.model.trim(), enable_thinking: f.think, thinking_budget: f.budget ? parseInt(f.budget) : 8000 };
  if (f.ctx) body.context_window = parseInt(f.ctx);
  if (f.output) body.max_output_tokens = parseInt(f.output);
  body.input_price_per_million = f.inputPrice === "" ? null : Number(f.inputPrice);
  body.output_price_per_million = f.outputPrice === "" ? null : Number(f.outputPrice);
  fetch("/api/models/set-builtin", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      _llmSetProviderBusy(key, null);
      if (d.ok) {
        _llmSetProviderMsg(key, "ok", "保存成功");
        loadLlmData().then(function () {
          // Reset API key field after successful save
          var p2 = uiState._llmProviders.find(function (x) { return x.key === key; });
          if (p2) { p2.fields.apiKey = ""; }
          draw();
        });
      } else {
        _llmSetProviderMsg(key, "err", d.error || "保存失败");
      }
    })
    .catch(function (e) {
      _llmSetProviderBusy(key, null);
      _llmSetProviderMsg(key, "err", "网络错误: " + (e.message || "unknown"));
    });
}

 function _llmClearBuiltin(key) {
 var prov = (uiState._llmProviders || []).find(function (p) { return p.key === key; });
 var label = (prov && prov.label) || key;
 if (!window.BAA.ui || !window.BAA.ui.confirm) {
    fetch("/api/models/clear", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: key }) })
      .then(function () { return loadLlmData(); }).then(draw);
    return;
  }
  window.BAA.ui.confirm({ title: "确认", message: "清除 " + label + " 的 API Key 和配置？此操作不可恢复。", danger: true })
    .then(function (ok) {
      if (!ok) return;
      return fetch("/api/models/clear", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: key }) })
        .then(function () { return loadLlmData(); }).then(draw);
    });
}

function _llmTestModel(key) {
  var p = uiState._llmProviders.find(function (x) { return x.key === key; });
  if (!p) {
    // Test a custom model
    fetch("/api/models/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: key }) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.success) {
          uiState._llmMsg = { type: "ok", text: (d.model || key) + " 连接成功" };
        } else {
          uiState._llmMsg = { type: "err", text: d.message || d.error || "连接失败" };
        }
        draw();
      });
    return;
  }
  _llmSetProviderBusy(key, "test");
  fetch("/api/models/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: key, base_url: p.fields.baseUrl.trim(), model: p.fields.model.trim(), api_key: p.fields.apiKey.trim() }) })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      _llmSetProviderBusy(key, null);
      if (d.success) {
        _llmSetProviderMsg(key, "ok", (d.model || key) + " 连接成功");
      } else {
        _llmSetProviderMsg(key, "err", d.message || d.error || "连接失败");
      }
    })
    .catch(function (e) {
      _llmSetProviderBusy(key, null);
      _llmSetProviderMsg(key, "err", "网络错误: " + (e.message || "unknown"));
    });
}

function _llmDeleteCustom(key) {
  if (!window.BAA.ui || !window.BAA.ui.confirm) {
    fetch("/api/models/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: key }) })
      .then(function () { return loadLlmData(); }).then(draw);
    return;
  }
  window.BAA.ui.confirm({ title: "确认", message: "删除自定义模型 " + key + "？此操作不可恢复。", danger: true })
    .then(function (ok) {
      if (!ok) return;
      return fetch("/api/models/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: key }) })
        .then(function () { return loadLlmData(); }).then(draw);
    });
}

function _llmSubmitForm() {
  var f = uiState._llmForm;
  var editingKey = uiState._llmEditingKey;
  var ctxRaw = (f.ctx || "").trim();
  var outRaw = (f.output || "").trim();
  var budgetRaw = (f.budget || "").trim();
  var inputPriceRaw = (f.inputPrice || "").trim();
  var outputPriceRaw = (f.outputPrice || "").trim();
  uiState._llmFormMsg = { err: "", ok: "" };

  if (editingKey) {
    var body = { provider: editingKey, base_url: f.url.trim(), model_name: f.model.trim(), api_key: f.key.trim(), enable_thinking: f.think, thinking_budget: budgetRaw ? parseInt(budgetRaw) : 8000 };
    if (ctxRaw) body.context_window = parseInt(ctxRaw);
    if (outRaw) body.max_output_tokens = parseInt(outRaw);
    body.input_price_per_million = inputPriceRaw === "" ? null : Number(inputPriceRaw);
    body.output_price_per_million = outputPriceRaw === "" ? null : Number(outputPriceRaw);
    fetch("/api/models/update", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { uiState._llmFormMsg = { err: d.error, ok: "" }; draw(); return; }
        uiState._llmFormMsg = { err: "", ok: d.message || "保存成功" };
        uiState._llmEditingKey = null;
        loadLlmData().then(function () { draw(); });
        setTimeout(function () { uiState._llmFormOpen = false; draw(); }, 1200);
      });
    return;
  }

  var data = { name: f.name.trim(), base_url: f.url.trim(), model_name: f.model.trim(), api_key: f.key.trim(), enable_thinking: f.think, thinking_budget: budgetRaw ? parseInt(budgetRaw) : 8000 };
  if (ctxRaw) data.context_window = parseInt(ctxRaw);
  if (outRaw) data.max_output_tokens = parseInt(outRaw);
  if (inputPriceRaw || outputPriceRaw) {
    data.input_price_per_million = inputPriceRaw === "" ? null : Number(inputPriceRaw);
    data.output_price_per_million = outputPriceRaw === "" ? null : Number(outputPriceRaw);
  }
  fetch("/api/models/add", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.error) { uiState._llmFormMsg = { err: d.error, ok: "" }; draw(); return; }
      uiState._llmFormMsg = { err: "", ok: d.message || "添加成功" };
      loadLlmData().then(function () { draw(); });
      setTimeout(function () { uiState._llmFormOpen = false; draw(); }, 1200);
    });
}

/* ── end LLM tab ─────────────────────────────────── */

  return { renderLlm, loadLlmData };
}
