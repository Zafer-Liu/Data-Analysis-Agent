import js from "@eslint/js";
import globals from "globals";

export default [
  {
    ignores: [
      "node_modules/**",
      "static/dist/**",
      "static/vendor/**",
      "tmp/**",
      "uploads/**",
      "outputs/**",
    ],
  },
  js.configs.recommended,
  {
    files: ["frontend/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.node,
        SID: "readonly",
        closeOverlay: "readonly",
        openOverlay: "readonly",
        showToast: "readonly",
        t: "readonly",
      },
    },
    rules: {
      "no-empty": ["error", { allowEmptyCatch: true }],
      "no-constant-condition": ["error", { checkLoops: false }],
      "no-unused-vars": [
        "error",
        {
          args: "all",
          argsIgnorePattern: "^_",
          caughtErrors: "all",
          caughtErrorsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
        },
      ],
      // FE1 / 03-conventions.md §「禁止浏览器系统弹窗」: ban native dialogs.
      // Notifications → window.BAA.ui.toast(), confirmations → await
      // window.BAA.ui.confirm({...}). The 3 remaining bare confirm() calls are
      // intentional defensive fallbacks (when BAA.ui.confirm is unavailable)
      // and are individually eslint-disable'd with a reason. See F4 in the
      // frontend audit memory.
      "no-restricted-globals": [
        "error",
        { name: "confirm", message: "Use window.BAA.ui.confirm() instead of native confirm()." },
        { name: "alert", message: "Use window.BAA.ui.toast() instead of native alert()." },
        { name: "prompt", message: "Use an in-app Vue form instead of native prompt()." },
      ],
      "no-restricted-syntax": [
        "error",
        {
          selector: "CallExpression[callee.object.name='window'][callee.property.name='confirm']",
          message: "Use window.BAA.ui.confirm() instead of window.confirm().",
        },
        {
          selector: "CallExpression[callee.object.name='window'][callee.property.name='alert']",
          message: "Use window.BAA.ui.toast() instead of window.alert().",
        },
        {
          selector: "CallExpression[callee.object.name='window'][callee.property.name='prompt']",
          message: "Use an in-app Vue form instead of window.prompt().",
        },
      ],
      // NOTE on innerHTML / fetch / document.createElement: 03-conventions.md
      // bans these for NEW dynamic UI, but the legacy codebase has ~93
      // innerHTML / ~190 fetch / many createElement sites that are legitimate
      // (root.innerHTML="" clears, renderMd() sanitization, api-client itself).
      // A blanket no-restricted-syntax rule would produce ~150 false positives
      // and red the gate. These constraints are enforced by code review against
      // the Vue-island checklist in frontend/README.md instead.
    },
  },
  {
    // static/js/ modules are classic browser scripts (IIFE, no ES module
    // imports). Lint them as scripts so violations like window.confirm() /
    // alert() regressions and unused vars are caught by the gate.
    files: ["static/js/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "script",
      globals: {
        ...globals.browser,
        t: "readonly",
        getLang: "readonly",
        setLang: "readonly",
        applyI18n: "readonly",
        // Vendor globals injected by external <script> tags (templates/*.html).
        GridStack: "readonly",
      },
    },
    rules: {
      "no-empty": ["error", { allowEmptyCatch: true }],
      "no-constant-condition": ["error", { checkLoops: false }],
      "no-unused-vars": [
        "error",
        {
          args: "all",
          argsIgnorePattern: "^_",
          caughtErrors: "all",
          caughtErrorsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
        },
      ],
    },
  },
];
