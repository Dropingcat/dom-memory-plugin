# Get started

This guide installs the DOM project-memory plugin for **Claude Code** and for
**OpenCode**. Both versions share the same engine and the same data layout, so a
memory created by one host can be read by the other.

Prerequisites (both hosts):

* **Python 3.11+** that is allowed to spawn an MCP server process.
* The Python packages the server needs:
  `python -m pip install "mcp>=1.30" "anyio>=4.15" "PyYAML>=6.0"`
* A project directory you want a memory for (any normal project folder).

> The memory is stored *inside the project* at `<project>/.dom-memory` — it travels
> with the project, not with the chat. Add `.dom-memory/` to the project's
> `.gitignore`, or commit it. Your choice.

---

## Option A — Claude Code

### A1. Get the plugin files

Do one of the following:

* **Ready archive:** download `dom-memory.plugin` from
  [release v0.3.1](https://github.com/Dropingcat/dom-memory-plugin/releases/tag/v0.3.1).
* **From the tree:** copy the `claude/` folder somewhere on your machine, e.g.
  `C:\tools\dom-memory-plugin\claude`.

### A2. Install

Install the packaged plugin archive:

* **Desktop app:** open **Settings → Plugins → Install from file** (or your app's
  custom-plugin import) and select `dom-memory.plugin`.
* **CLI:** `claude plugin install /path/to/dom-memory.plugin`

Then **start a new session** in your project. The plugin registers two hooks:

* `UserPromptSubmit` → `hooks/plan_check_hook.py` — the duty cycle;
* `PostToolUse(ExitPlanMode)` → `hooks/plan_hook.py --now` — an approved plan is
  handed to the memory.

### A3. Interpreter

Each manifest references the interpreter **portably** as `${DOM_PYTHON:-python}`:

* if the machine has `python` on `PATH` pointing to the interpreter with the
  packages installed, nothing else is needed;
* otherwise set `DOM_PYTHON` to your interpreter (e.g. `C:\Python311\python.exe`)
  **before the app launches**, then restart the session.

To pin an interpreter permanently, set `DOM_PYTHON` before the app launches or
edit the interpreter in `.mcp.json` and `hooks/hooks.json`. `tools/build_zip.py`
only repackages the trees; it does not rewrite the interpreter.

### A4. Verify

Run in a new session, or check the app's plugins screen. On the first prompt the
plugin should report that the project memory started and record your request as
the root claim. Then use `/mcps` (or the MCP status UI) — the `dom-memory` server
answers `READY`.

---

## Option B — OpenCode

### B1. Get the plugin files

Do one of the following:

* **Ready archive:** download `opencode-dom-memory-0.3.1.zip` from
  [release v0.3.1](https://github.com/Dropingcat/dom-memory-plugin/releases/tag/v0.3.1),
  unzip it anywhere (e.g. `C:\tools\opencode-dom-memory`).
* **From the tree:** the `opencode/` folder *is* the plugin package — you can point
  at it directly.

Either way the plugin folder contains `package.json` + `index.js` at its root.

### B2. Wire it into the project config

Merge `opencode/opencode.json.example` into the project's `opencode.json`, filling
in the interpreter and both project paths:

```jsonc
{
  "mcp": {
    "servers": {
      "dom-memory": {
        "type": "local",
        "command": [
          "C:/Python311/python.exe",
          "C:/tools/opencode-dom-memory/scripts/dom_server.py"
        ],
        "environment": { "OPENCODE_PROJECT_DIR": "C:/path/to/your/project" },
        "disabled": false
      }
    }
  },
  "plugins": [
    {
      "package": "file:///C:/tools/opencode-dom-memory",
      "options": {
        "pythonPath": "C:/Python311/python.exe",
        "projectDir": "C:/path/to/your/project",
        "autostart": true
      }
    }
  ]
}
```

Notes:

* `plugins[].package` points at the plugin folder root (it contains the
  `package.json`). On Windows use `file:///C:/…` (three slashes).
* `options.pythonPath` is optional when `python` is on `PATH`; it may also be set
  per machine via the `DOM_PYTHON` / `PYTHON_BIN` environment variables.
* The MCP server also appears under `/mcps` and can be toggled there.

### B3. Restart

**Reopen the session** (or let the config watcher reload it). On the first prompt
the memory is created under `<project>/.dom-memory` and the request is recorded as
the root claim.

Optionally copy `skills/dom-memory/` into the project's `.opencode/skills/` so the
skill loads, teaching the agent how to use the memory.

### B4. Verify

`/mcps` shows `dom-memory`; `dom_describe` answers `READY`. Ask for the plan status
after your plan agent has written a plan — it should arrive as branches of claims.

### B5. Monitor (optional)

Start the read-only monitor with a persistent local token:

```powershell
$py = 'E:\opencode_harness_portable\.venv\Scripts\python.exe'
& $py opencode/monitor/start.py `
  --project E:\path\to\your\project `
  --port 8765 `
  --open
```

The script creates/reuses `<project>/.dom-memory/observability/monitor.token`,
prints the URL and token, and opens the browser. The UI shows hooks, prompt
snapshots (system blocks with origin), claims with versions/properties, the plan
tree, and health. The token is local; do not put it in the URL or commit it.

---

## Options (OpenCode `plugins[].options`)

| option | default | meaning |
|---|---|---|
| `pythonPath` | `DOM_PYTHON` / `PYTHON_BIN` / `python` | interpreter with the dependencies |
| `projectDir` | `OPENCODE_PROJECT_DIR`, then the plugin location | DOM project directory |
| `directory` | `<project>/.dom-memory/main` | explicit run dir (with its runtime root) |
| `planDir` | `~/.opencode/plan` | where the plan agent writes plan files |
| `principal` / `role` | — / `owner` | `DOM_PRINCIPAL` / `DOM_ROLE` of the host session |
| `checkEvery` | 10 | plan-check period (`DOM_CHECK_EVERY`) |
| `autostart` | true | create the memory on the first task; `false` = silence until it exists |
| `timeoutMs` | 60000 | per hook-program timeout |
| `enabled` | true | `false` turns both hooks off |

## Environment

The hooks and server share these variables:

| variable | values | meaning |
|---|---|---|
| `OPENCODE_PROJECT_DIR` | path | DOM project directory |
| `DOM_RUNTIME_ROOT` | path | explicit memory root (normally derived) |
| `DOM_PRINCIPAL` / `DOM_ROLE` | owner \| author \| reviewer | identity of the session |
| `DOM_REVIEW` | auto \| manual \| independent | review policy for new claims |
| `DOM_CHECK_EVERY` | integer (default 10) | how often to run the plan-check duty cycle |
| `DOM_LLM_PROVIDER` | deterministic \| client \| anthropic \| command | claim-extraction provider (deterministic by default) |
| `DOM_PYTHON` | interpreter path | per-machine override for the interpreter (both hosts) |
| `DOM_SESSION` | session id | attributed to `plan_checks`/claims (set by the OpenCode plugin) |

Both hooks **never block** the session: every failure is reported to the model or
the server log instead of thrown.

---

## Troubleshooting

* **"the `mcp` package is missing"** → install the requirements into the interpreter
  the server is launched with (`python -m pip install "mcp>=1.30" "anyio>=4.15" "PyYAML>=6.0"`),
  or point `DOM_PYTHON` / `pythonPath` at that interpreter.
* **Memory not created on first prompt** → check that the plugin actually loaded
  (settings/mcps), that `projectDir` / `OPENCODE_PROJECT_DIR` points at the project,
  and that `autostart` was not set to `false`.
* **Plan not arriving** → in OpenCode the plan is read from a fresh plan file under
  `~/.opencode/plan` when a non-plan agent makes its first call after the plan agent
  wrote it; make sure the plan agent really writes the file (or ingest manually with
  `dom_plan_ingest`).
* **`dom_describe` answers not READY** → the server may be failing to start; run
  `scripts/dom_server.py` from a console to see the error (needs the packages).

---

## Data

Everything lives in `<project>/.dom-memory/main/dom.sqlite3` (append-only,
hash-chained). Inspect it read-only:

```bash
sqlite3 <project>/.dom-memory/main/dom.sqlite3 ".tables"
```

Backups, removal, and multi-project isolation are plain filesystem operations.
