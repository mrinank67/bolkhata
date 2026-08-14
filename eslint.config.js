import globals from "globals";

// Flat config. Lint only -- there is deliberately no bundler or build step;
// index.html loads app.js as a native ES module and app.js imports js/*.js.
export default [
  {
    ignores: [
      "node_modules/**",
      ".venv/**",
      "graphify-out/**",
      "icons/**",
      ".claude/**",
    ],
  },

  // Browser-side application modules.
  {
    files: ["app.js", "js/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.browser,
        // Loaded from CDN <script> tags in index.html, not imported as modules.
        firebase: "readonly",
        // Injected by Firebase's reCAPTCHA script; used by the phone-auth flow.
        grecaptcha: "readonly",
      },
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "no-const-assign": "error",
      "no-dupe-keys": "error",
      "no-dupe-args": "error",
      "no-unreachable": "error",
      "no-fallthrough": "error",
      "no-self-assign": "error",
      "valid-typeof": "error",
      eqeqeq: ["warn", "smart"],
      "no-var": "warn",
    },
  },

  // Service worker: different global scope (self, clients, caches...).
  {
    files: ["sw.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: globals.serviceworker,
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "no-const-assign": "error",
      eqeqeq: ["warn", "smart"],
    },
  },

  // This config file itself runs on Node.
  {
    files: ["eslint.config.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.node,
    },
  },
];
