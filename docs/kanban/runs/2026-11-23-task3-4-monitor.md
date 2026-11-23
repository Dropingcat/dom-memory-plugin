# Task 3–4 — read-only monitor API and UI (2026-11-23)

## Scope

- `opencode/monitor/server.py`: read-only HTTP API, token auth, Host check, no CORS, JSON errors, static UI.
- `opencode/monitor/static/`: `index.html`, `app.js`, `style.css`; `textContent` only.
- `opencode/mockup/storage.py`: `Store(directory, read_only=False)` so `verify_chain` gets `sqlite3.Row`.
- Tests: `tests/check_monitor_api.py`, `tests/check_monitor_ui.py`.

## API

`/api/health`, `/api/claims`, `/api/claim/<id>`, `/api/hooks`, `/api/snapshots`, `/api/snapshot/<id>`, `/api/plan`.

Security checks:

- no token → 401; wrong token → 401;
- `Host: evil.example` → 403;
- `OPTIONS` → 405 and no `Access-Control-Allow-Origin`;
- traversal `/api/snapshot/..%2F..` → 404;
- corrupt DB → `STORAGE_CORRUPT`, not a 500;
- CSP on every response; `Cache-Control: no-store`.

## UI

Four tabs: Hooks & injections, DOM claims, Plan, Health. Snapshot selector renders `system`, `messages`, `tools`, `options`, `dom` as separate blocks. Token is kept in `sessionStorage`, sent as `Authorization: Bearer`; it is never placed in the URL. No external assets; `app.js` uses `textContent`, not `innerHTML`.

## Checks

| Check | Command | Result |
|---|---|---|
| API | `python -B tests/check_monitor_api.py` | PASS: health, claims, detail, hooks, snapshots, auth, host, cors, traversal, corrupt |
| UI | `python -B tests/check_monitor_ui.py` | PASS: tabs, CSP, no external assets, `textContent` only |

## Real probe

`probe_monitor.py` against `omh-baseline-dom-mcp`:

```json
{
  "health_status": 200,
  "chain": "CHAIN_OK",
  "db_path": "...\\omh-baseline-dom-mcp\\.dom-memory\\main\\dom.sqlite3",
  "claims": 212,
  "plan": "r16"
}
```

The UI returned 200 and all three expected tabs. The URL was ephemeral (`127.0.0.1:<random>`); the token was not printed in the URL.

## Mirror

`opencode/mockup/storage.py` = `claude/mockup/storage.py` (SHA256 equal). Monitor is OpenCode-only in 0.2.6; native OpenCode Panel is 0.3.1.

## Open

- Token delivery: CLI prints `***` unless `--print-token`; the user enters it in the UI.
- The live plugin process is not restarted; new snapshots/journal lines appear after reload.
