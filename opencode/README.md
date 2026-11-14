# dom-memory — OpenCode plugin

Long-term project memory for OpenCode. On the first task of a project the memory is
created automatically and your request is recorded verbatim as the root claim; from
then on your *plan* becomes a tree of append-only, versioned claims and
decisions/findings are kept in a hash-chained SQLite ledger under
`<project>/.dom-memory`.

## Install

1. **Config:** merge `opencode.json.example` into the project's `opencode.json`
   (MCP server `mcp.servers.dom-memory` + hook plugin `plugins[]`). Replace
   `<python.exe>` and both paths; on Windows the package path looks like
   `file:///C:/tools/dom-memory-plugin/opencode`.
2. **Interpreter:** `options.pythonPath` (or `DOM_PYTHON` / `PYTHON_BIN` / `python`
   on `PATH`, in that order) must point at a Python 3.11+ with the packages:
   `python -m pip install "mcp>=1.30" "anyio>=4.15" "PyYAML>=6.0"`
3. **Restart** the session. First prompt creates the memory under
   `<project>/.dom-memory` and records the request as the root claim.

## What it wires

* `index.js` + `package.json` → the OpenCode hook plugin (dependency-free,
  `{ id, setup }`):
  * `session "prompt"` → `hooks/plan_check_hook.py` (duty cycle),
  * `session "context"` → `hooks/plan_hook.py --now` for a fresh plan file under
    `~/.opencode/plan` when a non-plan agent runs (OpenCode's stand-in for Claude's
    ExitPlanMode hook).
* `scripts/dom_server.py` + `mockup/` → the `dom-memory` stdio MCP server with the
  `dom_*` tools.
* `skills/dom-memory/SKILL.md` → copy into the project's `.opencode/skills/` to make
  the skill load.

## Data

`<project>/.dom-memory/main/dom.sqlite3` — add `.dom-memory/` to the project's
`.gitignore` or commit it, your choice. The hooks never block the session.

See the repo root `README.md`, `GET_STARTED.md` and `NOT_DONE.md` for options and
the honest list of what is not done yet.

## License

MIT — see `LICENSE` in the repo root.
