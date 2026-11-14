// ESLint: código do painel (navegador) e testes do painel (Node).
import js from "@eslint/js";
import globals from "globals";

export default [
  { ignores: [".venv/**", "node_modules/**"] },
  js.configs.recommended,
  {
    files: ["app/panel/static/js/**/*.js"],
    languageOptions: { ecmaVersion: 2024, sourceType: "module", globals: globals.browser },
  },
  {
    files: ["tests/panel/**/*.mjs", "eslint.config.js"],
    languageOptions: { ecmaVersion: 2024, sourceType: "module", globals: globals.node },
  },
  {
    rules: {
      eqeqeq: ["error", "always", { null: "ignore" }], // `x == null` cobre null e undefined de propósito
      "no-shadow": "error",
      "no-var": "error",
      "prefer-const": "error",
      "no-console": ["error", { allow: ["error"] }],
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "object-shorthand": "error",
      "prefer-template": "error",
    },
  },
];
