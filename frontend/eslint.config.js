// no-undef is this project's JS equivalent of the backend's pyflakes check
// (CLAUDE.md) - it's what actually catches a missed import/typo after
// moving code between src/js/*.js's real ES modules, since neither esbuild
// nor node --check verify that a bare identifier resolves to anything (a
// bundler only follows import/export specifiers, it doesn't scope-check
// the code inside a module). Browser globals are listed explicitly rather
// than pulled from a package like `globals` - this project has no other
// npm runtime dependencies, and the list rarely changes.
module.exports = [
  {
    files: ['src/js/**/*.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        document: 'readonly', window: 'writable', console: 'readonly', fetch: 'readonly',
        localStorage: 'readonly', sessionStorage: 'readonly', WebSocket: 'readonly',
        setTimeout: 'readonly', clearTimeout: 'readonly', setInterval: 'readonly', clearInterval: 'readonly',
        location: 'readonly', history: 'readonly', navigator: 'readonly', URL: 'readonly',
        URLSearchParams: 'readonly', FormData: 'readonly', Blob: 'readonly', AbortController: 'readonly',
        requestAnimationFrame: 'readonly', performance: 'readonly', structuredClone: 'readonly',
        alert: 'readonly', confirm: 'readonly', prompt: 'readonly', Event: 'readonly', CustomEvent: 'readonly',
      },
    },
    rules: { 'no-undef': 'error' },
  },
];
