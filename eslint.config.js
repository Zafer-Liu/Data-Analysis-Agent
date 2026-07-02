import js from "@eslint/js";
import globals from "globals";

export default [
  {
    ignores: [
      "node_modules/**",
      "static/dist/**",
      "static/vendor/**",
      "static/js/**",
      "tmp/**",
      "uploads/**",
      "outputs/**",
    ],
  },
  js.configs.recommended,
  {
    files: ["frontend/**/*.js", "vite*.config.js"],
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
    },
  },
];
