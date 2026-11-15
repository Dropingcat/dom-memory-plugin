# dom-memory — Claude Code plugin

Long-term project memory for Claude Code. On the first task of a project the
memory is created automatically and your request is recorded verbatim as the root
claim; from then on your *plan* becomes a tree of append-only, versioned claims and
decisions/findings are kept in a hash-chained SQLite ledger under
`<project>/.dom-memory`.

## Install

1. **Plugins:** install this folder as a custom plugin — either upload the
   `dom-memory.plugin` archive (from `dist/claude/`), or add the plugin by its
   `.claude-plugin/plugin.json` and start a **new session**.
2. **Interpreter:** the manifests use `${DOM_PYTHON:-python}`. If `python` on
   `PATH` is your interpreter with the packages below — done. Otherwise set
   `DOM_PYTHON` before launching and restart the session.
3. **Packages** (Python 3.11+):
   `python -m pip install "mcp>=1.30" "anyio>=4.15" "PyYAML>=6.0"`

## What it wires

* `hooks/hooks.json` → `UserPromptSubmit` (plan-check duty cycle) +
  `PostToolUse(ExitPlanMode)` (approved plan → memory). The plan hook reads the
  session transcript and distils the plan discussion into a context per item.
* `.mcp.json` → the `dom-memory` stdio MCP server (`scripts/dom_server.py`,
  `mockup/` engine), tools: `dom_plan_ingest`, `dom_plan_tree`, `dom_claim`,
  `dom_update`, `dom_find`, `dom_describe`, …
* `skills/dom-memory/SKILL.md` → teaches the agent how to use the memory.

## Data

`<project>/.dom-memory/main/dom.sqlite3` — add `.dom-memory/` to the project's
`.gitignore` or commit it, your choice. Next to it: `inbox/` (plans queued by the
hooks, with `done/` and `failed/` results) and `watcher.log` (one JSON line per
queued/drained job). Both hooks never block the session.

See the repo root `README.md`, `GET_STARTED.md` and `NOT_DONE.md` for details,
options and the honest list of what is not done yet.

## License

MIT — see `LICENSE` in the repo root.
