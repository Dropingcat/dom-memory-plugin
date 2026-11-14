# opencode-dom-memory

Long-term project memory for **OpenCode**, ported one-for-one from the Claude Code
plugin (`Dom memory`). When the plugin is connected and the first task runs, it
**creates the memory of that project automatically and records the request
verbatim** — no init step. Every subsequent task is kept:

* your plan becomes a **tree of append-only, versioned claims**, one branch per
  plan item (auto-allocated `П1`, `П1.1`, …),
* decisions and findings are recorded as claims (`dom_claim`, `dom_update`),
* everything lives in a **hash-chained SQLite ledger** under
  `<project>/.dom-memory` (reads are always read-only; writes keep the chain).

Same engine, same hooks as the Claude Code plugin — this repo is the OpenCode
side of the pair.

## What you get (mirrors the Claude Code package)

| file | role |
|---|---|
| `index.js` | the OpenCode hook plugin (dependency-free, `{id, setup}`) |
| `scripts/dom_server.py` | stdio MCP server launcher |
| `mockup/` | the DOM engine (`paths`, `admission`, `claim_entry`, `planning`, `storage`, `server`, …) |
| `hooks/plan_hook.py`, `hooks/plan_check_hook.py` | the same hook programs Claude runs |
| `skills/dom-memory/SKILL.md` | tells the agent how to use the memory |
| `opencode.json.example` | a ready config fragment (MCP server + hook plugin) |

## Requirements

* **Python 3.11+** with `mcp`, `anyio`, `PyYAML`:
  `python -m pip install -e .` (from this repo) or
  `python -m pip install "mcp>=1.30" "anyio>=4.15" "PyYAML>=6.0"`.
* A project you want a memory for (any OpenCode workspace).

## Install

1. **Copy this repo** somewhere on the machine, e.g. `C:\tools\opencode-dom-memory`.
2. **Wire it into `opencode.json`** of the project. Merge `opencode.json.example`,
   filling in `<python.exe>` and both project paths:

   ```json
   "mcp": { "servers": { "dom-memory": {
       "type": "local",
       "command": ["C:/tools/python.exe", "C:/tools/opencode-dom-memory/scripts/dom_server.py"],
       "environment": { "OPENCODE_PROJECT_DIR": "C:/tools/my-project" }, "disabled": false } } },
   "plugins": [ { "package": "file:///C:/tools/opencode-dom-memory",
       "options": { "pythonPath": "C:/tools/python.exe", "projectDir": "C:/tools/my-project" } } ]
   ```

   > `file:///…/opencode-dom-memory` points at the repo root (it contains
   > `package.json`; `index.js` is the plugin entry). The MCP server is shown in
   > `/mcps` and can be toggled there too.
3. **Reopen the session** (or let the config watcher reload it). On the first
   prompt the memory is created under `<project>/.dom-memory` and the request is
   recorded as the root claim.

## How it works

* `session "prompt"` → `hooks/plan_check_hook.py`: **starts the memory on the
  first task** (records the request verbatim) and runs the duty cycle — it takes
  in queued plans and asks once for a plan check when due (`DOM_CHECK_EVERY`, default 10).
* `session "context"` → `hooks/plan_hook.py --now`: when a **non-plan** agent
  makes its first model call after the plan-agent wrote a fresh plan file under
  `~/.opencode/plan`, the plan is ingested into the memory (the OpenCode stand-in
  for Claude's `ExitPlanMode` hook).
* The MCP server serves `dom_*` tools (`dom_plan_ingest`, `dom_plan_tree`,
  `dom_find`, `dom_claim`, `dom_update`, `dom_plan_status`, …) and `skills/dom-memory`
  teaches the agent to use them. Copy `skills/dom-memory/` into the project's
  `.opencode/skills/` to make the skill load there.

Both hooks **never block the session**: every failure is reported to the model
or the server log instead of thrown.

## Options (`plugins[].options`)

| option | default | meaning |
|---|---|---|
| `pythonPath` | `DOM_PYTHON` / `PYTHON_BIN` / `python` | interpreter with the deps |
| `projectDir` | `OPENCODE_PROJECT_DIR`, then the plugin location | DOM project directory |
| `directory` | `<project>/.dom-memory/main` | explicit run dir (with its runtime root) |
| `planDir` | `~/.opencode/plan` | where the plan agent writes plan files |
| `principal` / `role` | — / `owner` | `DOM_PRINCIPAL` / `DOM_ROLE` of this host session |
| `checkEvery` | 10 | plan-check period (`DOM_CHECK_EVERY`) |
| `autostart` | true | create the memory on the first task; `false` = silent until it exists |
| `timeoutMs` | 60000 | per hook-program timeout |
| `enabled` | true | `false` turns both hooks off |

Environment used by the hooks/server: `OPENCODE_PROJECT_DIR`, `DOM_RUNTIME_ROOT`,
`DOM_PRINCIPAL`, `DOM_ROLE`, `DOM_REVIEW` (auto | manual | independent), `DOM_CHECK_EVERY`,
`DOM_LLM_PROVIDER` (deterministic | client | anthropic | command), `PYTHONUTF8=1`.

Data layout: `<project>/.dom-memory/main/dom.sqlite3` (the append-only ledger).
Add `.dom-memory/` to the project's `.gitignore` or commit it — your choice.

## Verify

`python tests/smoke_cold_start.py` — runs the real hooks against a throw-away
project and asserts that the first task creates the memory and the plan is
ingested. Or in OpenCode: `/mcps` shows `dom-memory`, and `dom_describe`
answers `READY` after the first prompt.

## Maintain / release

The upstream DOM engine is the single source of truth; this repo is a curated
mirror so it stays easy to keep current and to hand out:

```
python tools/sync_from_upstream.py --upstream <path-to-worktree>   # refresh files
python tools/build_zip.py                                          # dist/opencode-dom-memory-<VERSION>.zip
```

Bump `VERSION` (and `package.json` / `pyproject.toml`) on every release.

## License

Not yet licensed for public reuse; for the moment this is the author's
experimental tooling. Contact the author before redistributing beyond your own use.