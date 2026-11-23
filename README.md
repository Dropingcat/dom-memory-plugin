# dom-memory-plugin

**Long-term project memory for AI coding agents** — a single engine shipped as
**two host plugins**: one for **Claude Code** (`claude/`) and one for **OpenCode**
(`opencode/`).

The memory is created **automatically on the first task** of a project and then
grows with it, in the background, without init steps:

* your **plan becomes a tree of append-only, versioned claims** — one auto-allocated
  branch per plan item (`П1`, `П1.1`, …), so "what was agreed" is never lost in chat;
* **decisions and findings** are recorded as claims (`dom_claim`, `dom_update`) and
  carry provenance, identity and dates;
* everything lives in a **hash-chained SQLite ledger** under
  `<project>/.dom-memory` — reads are read-only, every write keeps the chain intact,
  so the memory is auditable and safe to commit or ignore, your choice.

> **Honest status.** This is experimental tooling. A number of things are *not* done
> yet — see **[`NOT_DONE.md`](NOT_DONE.md)** before relying on it.

---

## What it does / what it is for

AI agents (Claude Code, OpenCode) are stateless across sessions, and their plan is
usually lost the moment the conversation moves on. This plugin gives a **project** a
*long-term memory* with structure:

| Capability | How the agent uses it |
|---|---|
| Plan is remembered | the plan-agent's accepted plan is ingested into claim branches; `dom_plan_status`, `dom_plan_tree` answer "what is open". |
| Decisions are kept | `dom_claim` / `dom_update` record what was decided and why, with a version history. |
| Facts are findable | `dom_find` and `dom_context` answer "where does this come from". |
| Duty cycle | a hook reminds the agent once in a while to update the plan, instead of forgetting it. |
| Audit trail | the ledger is hash-chained; every admission references its review and its predecessor. |
| Review policy | `DOM_REVIEW` auto \| manual \| independent governs whether claims are admitted directly or by a review. |

It is **not** a chat-memory exporter, a "notes" database, or a general-purpose RAG.

## Two versions, one engine

| Host | Folder | Install form | Entry |
|---|---|---|---|
| **Claude Code** | [`claude/`](claude/README.md) | import `dist/claude/dom-memory.plugin`, or use `mockup/`, `hooks/`, `scripts/` directly | `.claude-plugin/plugin.json`, `hooks/hooks.json`, `.mcp.json` |
| **OpenCode** | [`opencode/`](opencode/README.md) | `file:///.../opencode` plugin package, or unzip `dist/opencode/opencode-dom-memory-<VERSION>.zip` | `opencode/index.js` (+ `package.json`), `hooks/*.py`, `scripts/dom_server.py` |

Both trees share the **same engine** (`mockup/`, the hook programs under `hooks/`,
the server under `scripts/`, the agent skill under `skills/dom-memory/`). A change
in the engine is synced into both trees by one command
([`tools/sync_from_upstream.py`](tools/sync_from_upstream.py)).

## Quick start

Full instructions with screenshots-style detail: **[`GET_STARTED.md`](GET_STARTED.md)**.

```bash
# 0) requirements (Python 3.11+ that can run an MCP server)
python -m pip install "mcp>=1.30" "anyio>=4.15" "PyYAML>=6.0"

# 1) Claude Code: upload dist/claude/dom-memory.plugin as a custom plugin,
#    then start a new session. Done.

# 2) OpenCode: merge opencode/opencode.json.example into the project's opencode.json,
#    replace <python.exe> and both paths, then reopen the session. Done.
```

On the first prompt the memory is created under `<project>/.dom-memory` and the
request is recorded verbatim as the root claim — no manual init.

## Release 0.2.6 assets

Download from **[GitHub release v0.2.6](https://github.com/Dropingcat/dom-memory-plugin/releases/tag/v0.2.6)**.

| Asset | Host | Size | SHA256 |
|---|---|---:|---|
| `opencode-dom-memory-0.2.6.zip` | OpenCode | 179 665 B | `75097314546c175e95a07041d4966d360359ee92d71b1cb637b908349ab9a56b` |
| `dom-memory.plugin` | Claude Code | 170 404 B | `927bd8914e2888d8bcddb751f75fd824a76e47c391c04b4447c84073cc67c6ba` |

`B` means bytes, not a version. Both assets are ZIP archives; `dom-memory.plugin`
is a ZIP whose first entry is `.claude-plugin/plugin.json`, the layout the Claude
desktop app expects. Sizes differ because the OpenCode archive also ships the
read-only monitor (`opencode/monitor/`) and `package.json`.

Verify a download:

```powershell
Get-FileHash .\opencode-dom-memory-0.2.6.zip -Algorithm SHA256
Get-FileHash .\dom-memory.plugin -Algorithm SHA256
```

```bash
sha256sum opencode-dom-memory-0.2.6.zip dom-memory.plugin
```

Install:

* **OpenCode:** unzip, then point `plugins[].package` at the folder (it contains
  `package.json`). Full steps: [`GET_STARTED.md`](GET_STARTED.md) Option B.
* **Claude Code:** install `dom-memory.plugin` via **Settings → Plugins → Install
  from file** (or `claude plugin install /path/to/dom-memory.plugin`). Full steps:
  [`GET_STARTED.md`](GET_STARTED.md) Option A.
* **Monitor:** `python opencode/monitor/start.py --project <project> --port 8765 --open`.
  The local token is created once at
  `<project>/.dom-memory/observability/monitor.token` and reused on later starts;
  the script prints the URL and token. Open `http://127.0.0.1:8765` and enter the token.

Release notes and known limitations: [`docs/releases/0.2.6-notes.md`](docs/releases/0.2.6-notes.md).

## Repository layout

```
.
├── README.md              this file
├── GET_STARTED.md         detailed installation for both hosts
├── NOT_DONE.md            what is not implemented / not finalised (read this)
├── LICENSE                MIT
├── VERSION                current plugin version (0.2.6)
├── claude/                self-contained Claude Code plugin tree
│   ├── .claude-plugin/plugin.json   plugin manifest
│   ├── .mcp.json                    MCP server (stdio), ${DOM_PYTHON:-python}
│   ├── hooks/hooks.json             UserPromptSubmit + ExitPlanMode hooks
│   ├── hooks/*.py                   the shared hook programs
│   ├── mockup/                      the DOM engine (same in both trees)
│   ├── scripts/dom_server.py        MCP server launcher
│   ├── skills/dom-memory/SKILL.md   agent skill
│   └── opencode-plugin/index.js      the host-independent hook plugin (also here)
├── opencode/              self-contained OpenCode plugin
│   ├── index.js           the OpenCode hook plugin (session.prompt / session.context)
│   ├── package.json       plugin package metadata
│   ├── opencode.json.example         config fragment (MCP server + plugins[])
│   └── (engine files like claude/)
├── tools/
│   ├── sync_from_upstream.py  refresh both trees from the upstream engine source
│   └── build_zip.py           emit both distributable archives into dist/
├── tests/
│   ├── smoke_cold_start.py    host-free self-check (first task creates memory, plan ingress)
│   ├── check_plan_context.py  plan discussion -> per-item context, context updates without a revision
│   ├── check_inbox_status.py  the plan queue is counted in dom_plan_status and logged
│   └── check_plan_identity.py new root = new plan; a rename is a revision; partial mode unaffected
└── dist/                  ready-made archives
    ├── claude/dom-memory.plugin
    └── opencode/opencode-dom-memory-<VERSION>.zip
```

## Maintain / release

The upstream engine is the single source of truth; this repo is a curated mirror
that stays easy to keep current and to hand out:

```bash
python tools/sync_from_upstream.py --upstream <path-to-engine-checkout>  # refresh claude/ + opencode/
python tools/build_zip.py                                                # rebuild dist/ archives
python tests/smoke_cold_start.py                                         # self-check both trees
python tests/check_plan_context.py                                       # self-check the plan context
python tests/check_inbox_status.py                                       # self-check the queue counters/log
python tests/check_plan_identity.py                                      # self-check plan identity (new root = new plan)
```

Bump `VERSION` and the manifests on every release.

## License

[MIT](LICENSE) © 2026 dom-memory-plugin contributors.
