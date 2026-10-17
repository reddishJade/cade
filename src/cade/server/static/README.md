# Browser frontend

These assets provide the `cade web` interface with native web technologies and no Node.js, npm, or bundling step.

## Assets

FastAPI serves:

- [index.html](index.html): page structure, conversation and status areas, and approval dialogs.
- [styles.css](styles.css): Flexbox/Grid layout, code styles, reasoning details, status badges, and responsive themes without a CSS framework.
- [app.js](app.js): WebSocket connection/reconnection, streamed text and reasoning, tool status and diffs, and approval decisions returned to the backend.

## Invariants

Keep the assets directly distributable in the Python wheel. Do not add framework source requiring npm, webpack, or Vite builds. Bound obsolete nodes and long text during streaming to protect browser memory and responsiveness.
