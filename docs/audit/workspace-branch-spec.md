# Workspace ↔ DOM branch linking — analyst specification

**Status:** proposal, read-only research. No repository file was modified.
**Host:** OpenCode v2.3.0 (`opencode-cli.version` = `2.3.0`).
**DOM plugin under study:** `E:\opencode_harness_portable\projects\opencode-dom-memory-plugin` (v0.3.1, working tree).
**Live DOM install:** `...\omh-baseline-dom-mcp\.claude\worktrees\mcp-llm-server-054018\dist\claude\dom-memory` (v0.2.6).
**Evidence sources:** host state DB (`C:\Users\Arhys\.local\share\opencode\opencode.db`, read-only), live DOM ledger (`...\omh-baseline-dom-mcp\.dom-memory\main\dom.sqlite3`, read-only), plugin/queue/plan sources, `@opencode-ai/sdk` v2 generated types, `@opencode-ai/plugin` types, host log, deployment map.

---

## 0. TL;DR

1. A session is already attached to a **directory**, and a directory is already the operative "workspace". The host's own `workspace` table is empty and `session.workspace_id` is `NULL` for all 1442 sessions in this install. The real identity is `session.directory` + the `worktree` registry row.
2. Subagents **cannot choose a directory**: dispatch creates a child session that inherits the parent's `directory`. Measured: 871 child sessions, **0** with a directory different from the parent. Therefore a subagent runs in a branch worktree only if its parent session is *already* there (or the child is moved after creation).
3. `session_move` is the only exposed tool that re-attaches a session to a directory; the SDK also has `experimental.workspace.warp` (workspace + sync history) and `controlPlane.moveSession`. The move happens "at the next safe boundary", and **children are not moved with the parent**.
4. DOM is already half-way there: one ledger per `projectDir` (pinned in `opencode.json`), plan branches in `plan_branches`, a global `current_task` pin, and a git property (`gits`) per header. What is missing is a **declared binding** between a plan item (`plan_task_id`), a workspace directory, and the sessions that work in it.
5. The step queue is already *content*-shared (pure function of step + index + cards, cached per run directory) but its **state is per session and in memory** (`pending`, `briefs`, `planDigests` in `index.js`; `states` Map in `tau_gate.js`). "Shared across the workspace" therefore means: **shared queue identity and content, per-session delivery cursors** — not one delivery for all sessions.
6. Two blockers must be fixed before any new state is added: the **live install is 0.2.6 while the source is 0.3.1** (no `skillQueueFor`, no producer in the live copy), and the **instruction entry channel is a black hole** (`instruction_entry` has 0 rows; `entry.put` is unverifiable). Reuse `event.system` only with explicit verification.

Recommended shape: **plan item (`plan_task_id`) → 0..1 workspace binding → N sessions**; bind on `plan_task_id` (not on the branch code); record bindings in the DOM ledger; make the queue a persisted, workspace-keyed table with a per-session cursor.

---

## 1. Current state

### 1.1 Host entities (OpenCode 2.3.0, SQLite schema as installed)

| Table | Key columns | Observed |
|---|---|---|
| `project` | `id`, `worktree`, `vcs` | one row per project (worktree path, `vcs` maybe null); 30 rows |
| `project_directory` | `(project_id, directory)` | only 1–2 dirs per project; most worktrees are **not** registered here |
| `worktree` | `(project_id, directory)`, `strategy` | 37 rows; many under `~/.local/share/opencode/worktree/<prefix>/<name>`, `strategy='git'`; **never pruned** |
| `workspace` | `id`, `provider`, `binding` | **0 rows** |
| `session` / `session_v2` | `id`, `project_id`, `workspace_id?`, `parent_id?`, `directory` NOT NULL, `agent`, `title` | 1442 sessions (571 roots, 871 children); **`workspace_id` is NULL for every row** |
| `instruction_entry` | `(session_id, key)`, `value` | **0 rows** |
| `instruction_state` | `session_id`, `current_values` | populated with `core/*` system-prompt hashes only — no `dom.skill-queue` key |

Consequences:

- "Workspace" as a first-class host object exists in the API/schema but is **not used** by this installation. Treat it as an optional future identity, not as ground truth.
- The **directory** is the only reliable workspace identity today. Paths on this host are Windows paths with non-ASCII segments, so normalize with `realpath → NFC → normcase` (the same rule DOM already uses for DB identity, `mockup/session.py:52-60`).

### 1.2 Tool signatures (verbatim from the Code Mode catalog)

```ts
tools.opencode.worktree_create({ name: string }): Promise<{ directory: string }>
tools.opencode.worktree_list(): Promise<{ worktrees: Array<{ directory: string; strategy?: string }> }>
tools.opencode.worktree_remove({ directory: string; force?: boolean }): Promise<{ directory: string; removed: boolean }>
tools.opencode.session_move({ sessionID?: string; directory: string }): Promise<{ sessionID: string; directory: string }>
```

Notes:

- `worktree_create` takes a **name**, returns a **directory**; it does not accept or return a branch name. The git branch created by the host must be read back (`git -C <dir> rev-parse --abbrev-ref HEAD`) and recorded; do not assume it equals `name`.
- `worktree_remove(force)` discards modified and untracked files when `force: true`; only after explicit confirmation.
- `session_move` without `sessionID` moves the current session; the description warns it "moves at the next safe boundary" and that destination-dependent tools must not run in the same `execute` call.
- SDK equivalents (not exposed as tools here): `experimental.controlPlane.moveSession({sessionID, destination:{directory}, moveChanges})`, `experimental.workspace.warp({id, sessionID, copyChanges})`, `experimental.workspace.{list,create,remove,syncList,status}`. `Workspace` carries `{id, type, name, branch?, directory?, projectID, timeUsed}`; `LocationRef` is `{directory, workspaceID?}` (`@opencode-ai/sdk/dist/v2/gen/types.gen.d.ts:2176`, `:2384`, `:2398`). In the installed V2 plugin context type there is **no** `experimental_workspace` and no `serverUrl` (`@opencode-ai/plugin/dist/v2/promise/context.d.ts`), so a V2-context plugin cannot register a workspace adapter or call those endpoints directly.

### 1.3 Sessions and subagent dispatch

- Subagents run in **child sessions** (`session_v2.parent_id`), with fresh context, in the background; the parent is notified when a child goes idle (host docs, `/docs/agents`). Evidence: 871 children of 1442 sessions; `job.child_session_id` exists.
- **A child always starts in the parent's `directory`.** Query `children with directory <> parent.directory` → **0 rows**.
- Sessions rooted in worktrees do exist (e.g. `ses_ec495ad44…` in `.../worktree/62047a/clever-squid`, `ses_e6296f569…` child in `.../witty-forest`), so the supported pattern is: **start the branch-lead session at the worktree directory, then dispatch its subagents there**.
- A session's `directory` is the effective workspace; `session_move` is the supported way to change it mid-life (the harness system block even recommends it: "consider using `execute` to call `tools.opencode.session_move`").

### 1.4 DOM memory today

**Identity and ledger.** The plugin resolves `projectDir` from `options.projectDir || OPENCODE_PROJECT_DIR || ctx.location?.directory || cwd` (`opencode/index.js:238-240`). The configs active for this workspace (`E:\...\Default Project\opencode.json` and `E:\opencode_harness_portable\.opencode\opencode.json`) pin `projectDir` to `.../omh-baseline-dom-mcp`, so *every* location (including worktrees) reads and writes **one ledger**: `.dom-memory/main/dom.sqlite3`. (The plugin repo's own config pins it to the repo — a second, separate ledger.) `domDir` is only set when `options.directory` is given (`:245`), which it is not. This centralization is correct for the plan and must be preserved.

**Plan branches.** `plan_branches` maps `plan_task_id → branch` (`AA`, `AB`, …, `AA100`, `AA200`, …; variants `AB-V1`), allocated once and never moved (`mockup/planning.py:335-397`; tool description of `dom_plan_ingest`). The code is a **display alias of a plan item**, not a VCS branch and not a stable workspace identity across re-planning (new items get new codes; the live plan jumped from `AQ…` to `FZ…` for the current 6 items).

**Current step.** `brief.py` chooses the current task as: root property `current_task` if it resolves to a non-done item, else the first open item in path order (`mockup/brief.py:131-181`). It is **global to the ledger**: every session in every directory is told the same `CURRENT` line. There is no session awareness anywhere in `brief.py` (grep: only `current_task`).

**Session identity in DOM.** Only the hook channel is trusted: `index.js` exports `DOM_SESSION`, `DOM_SESSION_SOURCE=hook`, `DOM_ORIGIN=hook` to each child Python process (`:305-307`); `resolve_session()` maps a missing `DOM_SESSION` to `session='mcp-unverified'` (`mockup/session.py:35-49`). Source v0.3.1 declares `claim_sessions(session_id, branch, …)` and `read_receipts(session_id, branch, …)` (`mockup/storage.py:230-231`, `SCHEMA_VERSION=5`). **The live ledger is schema v4**: `claim_sessions` and `read_receipts` do not exist there (verified: `schema_migrations` has row 4 only; 582 claims, 349 plan branches). So the designed session→branch link is **not deployed**.

**Skill queue path (source 0.3.1).**
- Producer: `queue_producer.py --step <json> [--directory <run dir>]`. Selection is pure (`mockup.skill_select.select_step`); the cache key is `(producer schema, step payload, queue steps, code+data stamp)` and is scoped by `--directory` (`opencode/scripts/queue_producer.py:33-43, 110-140, 201-240`). No session and no workspace in the key — in practice the block is already the same for all sessions reading the same brief.
- Gate: `τ = sha256(step_id, cap_set, category_hint, markers, plan_version, index_digest)` (`gates/tau_gate.js:76-100`); re-emission is decided by a **module-level `Map` keyed by `sessionID`** (`:32, 102-118`).
- Transport: `entry.put("dom.skill-queue", text)` first, then `event.system.push`, then a deferred per-session queue (`index.js:496-520`). `seen()` checks that the pushed text is present in `event.system`, then logs `injected`.
- Per-session state in `index.js`: `pending`, `planDigests`, `briefs`, all keyed by `sessionID` (`:287-289`); plan ingestion is globally deduped by the `<plan>.dom.json` stamp plus a foreign-session check (`:629-635`).

**Delivery reality check.** The live install's brief is delivered through `event.system.push` and observed (`system_blocks_before=5 → after=6`, origin `dom`, snapshot `.../ses_dde70b42…/2026-11-28T09-57-33-060Z-80c3f9dd.json`). The same table shows `instruction_entry` **empty**, so `entry.put` persists nothing here. This matches the open P1: `dom.skill-queue` does not reach the prompt while the log says `injected`. Any shared-queue design that depends on the instruction entry will silently fail.

**Version drift (critical).**
- Live `index.js` = 0.2.6, 369 lines, mtime 2026-11-24, hash `75C988…`; it has **no** `skillQueueFor` and does not call `queue_producer.py`.
- Source `opencode/index.js` = 0.3.1, 723 lines, mtime 2026-11-28, hash `9DD3C5…`.
- `docs/VERSIONS.md` claims 0.3.1 is live; `docs/deployment/map.md` correctly says the harness config uses **0.2.6**. The plugin `id` is `dom-memory.hooks` in every copy, and the host loads the same path repeatedly: log run 2026-11-28T08:41 shows 2 loads of the dist copy and 3 of the repo copy; run 09:45 shows 8 loads of the dist copy (across locations). There is **no idempotency guard** in the plugin.

### 1.5 Gap table

| Requirement | Today | Verdict |
|---|---|---|
| Session knows its workspace | `session.directory` exists; plugin can read `ctx.location.directory` (per-location context) | **exists**, not recorded in DOM |
| DOM plan branch ↔ workspace | none; only a free-text `gits` property ("Repository, branch and last known commit", `mockup/promptizer.py:53`) | **missing** |
| Subagent runs in branch workspace | possible only by inheriting from a parent already there; no dispatch parameter | **partial** |
| Several subagents in one workspace on different sub-tasks | possible physically; DOM gives them all the same `CURRENT` step | **partial** |
| Step queue shared across a workspace | content identical, state per session in memory, no persistence | **partial** |
| Session→branch attribution | designed (`claim_sessions.branch`, v5); live ledger v4; MCP writes are `mcp-unverified` | **not deployed** |
| Honest delivery status | `entry.put` unverifiable; log says `injected` | **broken** |

---

## 2. Desired state

### 2.1 Terms and identities

| Term | Identity | Notes |
|---|---|---|
| DOM project | ledger root (`projectDir`) | one per memory vault; pinned, not per worktree |
| Plan item | `plan_task_id` (`pt-…`) | **stable**; branch code is a display alias |
| DOM branch | `plan_branches.branch` (`AQ200`) | re-allocated on replan; never use as a key |
| Workspace | `workspace_ref = sha256(realpath_nfc_normcase(directory))` | host `workspace.id` stored as an optional alias when present |
| Session | host `sessionID` (`ses_…`) | hook-only trusted identity |
| Queue item | `queue_ref = sha256(workspace_ref + plan_version + fingerprint)` | one per workspace × step × material state |

### 2.2 Mapping (target model)

```
DOM project (one ledger, e.g. omh-baseline-dom-mcp)
├── plan r32
│   ├── default workspace W0 = project worktree (root)
│   │     └── session ses_R (orchestrator)   step_queue: W0 × r32 × fp(P2) = Q2
│   ├── П1 [FZ] ──binding──▶ W1 (worktree)   git branch dom/fz
│   │     ├── session ses_A  (branch lead, primary, moved here)
│   │     │     ├── session ses_A1  (subagent, task = П1.1)
│   │     │     └── session ses_A2  (subagent, task = П1.2)
│   │     └── step_queue: W1 × r32 × fp(П1) = Q1
│   └── П2 [GA]  (no own binding) ── resolves to W0
└── plan r33 … (new plan_task_ids, new bindings)
```

Rules:

1. **Bind by `plan_task_id`.** A binding is `{plan_task_id, workspace_ref, directory, vcs_branch, opencode_workspace_id?, status}`. `branch` is denormalized for display only and refreshed from `plan_branches`.
2. **One active binding per plan item; one active owner per workspace.** Sub-items of a bound item inherit its workspace by walking up (rule 3); two unrelated top-level items in one worktree is an explicit exception and must be flagged in `dom_workspace_status`.
3. **Resolution walks the plan tree**: session task → item binding → parent item binding → root default workspace (the project's main worktree). This mirrors the existing `tools`/`gits` property inheritance in `brief.py:262-272`.
4. **A session belongs to exactly one workspace** at a time; the hook records it on prompt. Its `plan_task_id` (the sub-task it was dispatched for) is a separate, optional field.
5. **The queue is keyed by workspace, not by session.** Sessions share the queue item; each has a cursor. A new session in the workspace receives the current item once; an unchanged item is not re-delivered.

### 2.3 Lifecycle

1. Plan ingested → items and branch codes exist.
2. Orchestrator decides to isolate an item: `worktree_create(name)` → directory; read the real git branch; `dom_workspace_bind({task: "AQ200", directory})`.
3. Orchestrator (or a dedicated branch lead) `session_move({directory})`. Only then dispatch subagents for that item — children inherit the directory.
4. Each subagent's prompt hook writes `session_bindings(session_id, workspace_ref, plan_task_id)`; the brief then shows `WORKSPACE` and `SESSION` and uses the session's task as `CURRENT`.
5. Claims written with `dom_claim(plan_task=…)` land in the item's DOM branch (already supported). Git evidence stays in the `gits` property.
6. On item completion: merge, mark done, `dom_workspace_release`; then `worktree_remove`. Release refuses while bindings or sessions remain.

### 2.4 Shared step queue semantics

- **Shared:** queue identity (`queue_ref`), block text, plan version, fingerprint, delivery count, creator. One row per workspace × step × material state, immutable for that key.
- **Per session:** delivery (`cursor`), acknowledgement, transport used. This is unavoidable: prompts are per session, and a subagent needs the block in *its own* request.
- **Content remains a pure function** of `(step, index, cards)`; no model, no per-session selection. Sharing must not change what the block says, only who has seen it.
- **Change propagation:** any material change (new plan version, new fingerprint) creates a new `queue_ref`; every session in the workspace sees `should_deliver=true` on its next turn and is gated by its own cursor. No in-memory idle counter is authoritative.

### 2.5 Invariants

- `I1` No DOM write without a hook-derived session identity for session-scoped facts (MCP stays `mcp-unverified`).
- `I2` A queue item is never mutated after insert; a change is a new `queue_ref`.
- `I3` A binding is never overwritten; re-bind = new row + old row `released` (append-only, same discipline as claims).
- `I4` `WORKSPACE`/`SESSION` lines are honest: `unbound` / `unverified` when unknown, never guessed.
- `I5` Removal of a workspace requires: status done, no active bindings, clean tree, branch merged (or explicit force with confirmation).

---

## 3. Required changes

### 3.1 `opencode/index.js`

1. **Location resolution helper** (one place):
   ```js
   const locationDir = (ctx, event) =>
     realpathNfcNormcase(event?.directory ?? ctx.location?.directory ?? projectDir)
   ```
   Pass it to every child process as `--session-dir` and `DOM_SESSION_DIR` in addition to `DOM_SESSION`. Today only `projectDir` and `domDir` are exported (`:248-252`), so a hook cannot tell a worktree from the main tree.

2. **Session binding on prompt** (new, cheap): once per prompt (not per model step), call a new `hooks/session_hook.py --dir <dir>` that upserts `session_bindings`. Skip when `(sessionID, dir)` is unchanged (in-memory map is enough; a miss only costs one upsert).

3. **Queue producer call** (`skillQueueFor`, `:409-488`): add `--workspace <ref> --session <sessionID> --plan-task <task>`; honour `should_deliver`; when `should_deliver=false`, return `{status:"idle"}` with the shared queue metadata (no block). Remove `entry.put` from the success path or verify it by read-back; on `event.system.push`, snapshot `event.system.length` **before** the push and require the new block to be *new*, not merely `includes(text)` (the current `seen()` can match an older identical block).

4. **Cursor ack**: after delivery, write the cursor in the same child process (`queue_producer.py --ack <queue_ref> --delivery system|entry|queue`). JS must not open SQLite directly.

5. **State keys**: `pending`, `briefs`, `planDigests` stay keyed by `sessionID` (they are delivery state), but add `workspaceOf(sessionID)` and invalidate `briefs`/plan-digest entries when the location changes for that session. `planDigests` dedup already protects against a re-ingest loop.

6. **Idempotency guard**: a module-level `Set` of `${plugin.id}:${locationDir}` so a double load in one location registers hooks once and logs `duplicate load ignored` (see R1). Cross-location loads must stay independent.

7. **Do not** key τ state by session alone once the queue is persisted; seed `checkReemit` from the cursor and treat it as a cache, never as the truth.

### 3.2 Skill router / producer

- `mockup/skill_select.py`: **no change**. Selection stays pure and session-agnostic (that is what makes sharing safe).
- New `mockup/queue.py` (dom-owned index tables, written like `plan_branches`, not through claims):
  ```python
  ensure_entry(c, workspace_ref, plan_task_id, plan_version, state, block_text) -> queue_ref
  read_cursor(c, session_id, queue_ref) -> {delivered_at, ack_at, delivery} | None
  ack(c, session_id, queue_ref, delivery) -> None
  ```
- `scripts/queue_producer.py`: add `--workspace`, `--session`, `--plan-task`, `--ack`; envelope grows:
  ```json
  {"status":"OK|IDLE","queue_ref":"…","should_deliver":true,
   "state":{…},"block_text":"…","shared":{"delivered_to":3,"created_by":"ses_…"}}
  ```
  Cache key stays `(step, queue, stamp)` — it must **not** include the session, or sharing dies.
- `gates/tau_gate.js`: keep `computeFingerprint` untouched (Python parity is a contract); turn `checkReemit` into a cache over the persisted cursor.

### 3.3 Plan system

- **Schema v6** (bundle with the un-deployed v5): `workspaces`, `session_bindings`, `step_queue`, `queue_cursors` (DDL sketch in §2.2/§2.4; all additive). Keep the v5→v4 downgrade path coherent; the live ledger is v4, so the first migration run must be v4→v6.
- `mockup/planning.py`:
  ```python
  def workspace_of(c, plan_task_id): ...      # walk ancestors, return binding | None
  def bind_workspace(svc, plan_task_id, directory, workspace_id=None, branch=None): ...
  def release_workspace(svc, plan_task_id, reason): ...
  def bind_session(svc, session_id, workspace_ref, plan_task_id=None, parent=None): ...
  ```
  Reuse `resolve_task(c, ref)` (`:469`) so `AQ200`, `П1.2`, and `pt-…` all work. Bindings write an index row **and** a `decision` claim on the item (provenance: who bound it, when, why).
- `mockup/brief.py`: add
  ```
  WORKSPACE: <ref8> <dir> git <branch> [bound by <claim8>]
  SESSION:   <sid8> task <path> [<branch>] parent <sid8|none>
  ```
  and select `CURRENT` as: `session_bindings.plan_task_id` → `workspaces.plan_task_id` → root `current_task` → first open. Emit `WORKSPACE: unbound` rather than the root default when the directory is not the project worktree and no binding exists.
- `mockup/server.py` + `HELP_DATA.py`: read-only `dom_workspace_status` (bindings, sessions, queue cursors) and write `dom_workspace_bind` / `dom_workspace_release` (owner/author; require `plan_task` + `directory`; validate the directory exists, is a git worktree of the project, and is not the project root unless explicit). Session binding stays hook-only; do not expose it as a tool (it would be `mcp-unverified`).
- `dom_claim` needs no change: `plan_task` already routes to the item's branch.

### 3.4 Host operations / config

- Fix the config duplication first: one plugin entry per plugin path for a given location; verify with the host log that each location has exactly one `dom-memory.hooks` load.
- Decide and document the workspace creation path (one of):
  - **A (recommended):** branch-lead session opened at the worktree (or moved there) → dispatch inside it. No host API changes.
  - **B:** parent dispatches, then `session_move(childID, dir)` per child. Racy: the first turn runs in the old directory; only safe for work that starts after the move.
  - **C:** SDK `experimental.workspace.warp` — attaches `workspaceID` + sync history, but is not exposed as a tool in this catalog and is unavailable to V2-context plugins. Defer.
- Add a permission rule for the worktree root (`.local/share/opencode/worktree/**`) if reads/edits there must not prompt (external directory).

### 3.5 Tests

- `tests/check_workspace_bind.py`: bind/release, ancestor resolution, double-bind refusal, branch-code rename does not break the binding.
- `tests/check_session_bind.py`: hook payload → row; MCP path stays `unverified`; relocation updates `workspace_ref`.
- `tests/check_shared_queue.py`: two sessions, one workspace → same `queue_ref`, two cursors, one block text; a new plan version → new `queue_ref`, both `should_deliver`; replay is idempotent.
- `tests/check_queue_ack.mjs`: delivery observed → cursor written; unverified entry → `unverified`, never `injected`.
- Live probe (manual, read-only first): `worktree_create` → record dir → `session_move` → read `session_v2.directory` from the host DB → confirm the brief's `WORKSPACE`/`SESSION` lines changed. No repo writes needed for the probe.

---

## 4. Rollout

1. **Repair the baseline** (blocks everything): install 0.3.1 to the live target (or downgrade the spec to 0.2.6 + queue), de-duplicate the plugin load, fix the `injected` lie.
2. **Schema v6 + bindings** (no behaviour change): tables, `planning` helpers, read-only `dom_workspace_status`, `WORKSPACE` line. Verify on the v4→v6 migration path.
3. **Session binding + session-aware brief**: hook upsert, `SESSION` line, `CURRENT` selection order. This is the first user-visible change; roll back by dropping the two lines.
4. **Persisted shared queue + cursors**: switch the producer, keep the old path behind an option; compare `queue_ref` and cursor counts for a week.
5. **Release/cleanup tooling**: `dom_workspace_release`, worktree TTL report, `worktree_remove` only after checks.

---

## 5. Risks and mitigations

| # | Risk | Evidence / mechanism | Mitigation |
|---|---|---|---|
| R1 | **Duplicate plugin execution** — same `id` loaded several times; hooks fire twice, τ state split, cursors double-advance | log 2026-11-28T08:41 (2× dist + 3× repo), 09:45 (8× dist); `docs/deployment/map.md` warns | idempotency guard `(id, location)`; one config entry per plugin per location; assert in tests |
| R2 | **Live/source drift** — new logic lands in 0.3.1 but the host runs 0.2.6 (no producer at all) | hashes/lines/mtimes; `docs/deployment/map.md` | promote the artifact before verifying; make the deployment map the single source of truth |
| R3 | **Children do not follow a moved parent** — subagents keep the old directory, work splits across trees | 0/871 children differ today; `session_move` moves one session | move the parent **before** dispatch; assert `child.directory == parent.directory`; refuse bind if mismatched; or move each child explicitly (path B) |
| R4 | **Move races** — "next safe boundary"; in-flight tool calls and cached `briefs` still use the old dir | tool description; `pending`/`briefs` maps | flush caches on location change; never run destination-dependent work in the same step; verify via host DB after the move |
| R5 | **Branch code is not an identity** — replan allocates new codes (`AQ200` → `FZ`), variants add `-Vn` | `plan_branches` (349 rows, slots up to 157); tool description | bind by `plan_task_id`; display `branch` only; tolerate rename in resolution |
| R6 | **Write collisions in one workspace** — two subagents edit the same files; DOM claims do not prevent file-level conflicts | architecture (claims are logical, not file locks) | one writer per task; separate worktrees per *task* when edits overlap; branch workspace for read/analysis |
| R7 | **Stale workspaces** — 37 worktree rows, many never pruned; some directories may be gone; removing one breaks a live session | `worktree` table; `worktree_list` shows only the current repo | TTL report; release requires no bindings **and** no sessions with that directory; `git status`/`--merged` checks; `force` only with confirmation |
| R8 | **Path identity** — junction/case/unicode variants look like different workspaces | Windows host; DOM already normalizes for the DB (D-019) | one `realpath → NFC → normcase` helper reused everywhere |
| R9 | **Write amplification** — a hook writes on every model step | 664 context injections vs 18 prompt events in one hook journal (`hooks-2026-11-28-4356.jsonl`) | cursor writes only on prompt/ack; in-memory cache; WAL + short transactions |
| R10 | **MCP cannot bind** — `dom_server.py` has no session; every tool write is `mcp-unverified` | `mockup/session.py:35-49` | hook-only session binding; tool API read-only for sessions; surface `unverified` in the brief |
| R11 | **Instruction entry is a black hole** — `entry.put` persists nothing; the log lies | `instruction_entry` = 0 rows; `entry.put` present but unverified | don't build on it; require an observed `event.system` mutation + ack; report `unverified` otherwise |
| R12 | **`workspace_id` may stay null** | `workspace` table empty; all sessions null | use directory-derived `workspace_ref` as the key; treat host id as an alias |
| R13 | **`worktree_create` branch name unknown** | tool returns only a directory | read `git rev-parse` and store it in the binding + `gits` property; never infer from `name` |
| R14 | **Queue content leak across sub-tasks** — a workspace-shared item could hand a subagent a sibling's skills | shared queue item | item carries `plan_task_id`; a session with a bound task filters; unbound sessions get the workspace's current item only |
| R15 | **Queue schema growth** — unbounded rows | one row per (workspace, step, material) | retention: drop `superseded` older than N plan versions; keep text only while referenced by a cursor |

### Cleanup policy (concrete)

```
release(task):
  require plan_task status in (done, cancelled)
  require no session_bindings with that workspace_ref (or sessions archived)
  require git status --porcelain empty (else keep + report)
  require branch merged OR explicit force (record decision claim)
  mark workspaces.status = released, released_at
worktree_remove(dir, force=false) only after release; force requires human confirmation
weekly: list worktrees with no binding and no session → report; delete only by explicit command
```

---

## 6. Open questions

1. Is the multiple plugin load per **location** (intended) or per **config entry** (bug)? The log alone cannot separate them; a one-line instrumentation in `setup(ctx)` (`id + ctx.location.directory + pid`) would. This decides whether R1 is a real duplicate-run risk.
2. Does `session_move` also insert a `worktree`/`project_directory` row, or is the directory accepted as-is? The SDK endpoint says "another project directory"; the observed `project_directory` table has only 1–2 dirs per project while sessions live in many, so acceptance looks permissive. Verify with one move + DB read.
3. Does `worktree_create` create a **new git branch**, and what is its name? Read it back; do not assume.
4. Are there host events for `session.created` / `session.updated` available to the DOM plugin's legacy `ctx.session.hook(...)` surface? If not, bind on prompt only (cheap, sufficient).
5. Can the host dedupe identical system blocks? The live log injects the same brief many times while the snapshot shows one DOM block; if not, the ack must also count duplicates.

---

## 7. Evidence index

- Tool signatures: Code Mode catalog (`worktree_create/list/remove`, `session_move`); SDK `@opencode-ai/sdk/dist/v2/gen/sdk.gen.d.ts` (`ControlPlane.moveSession`, `Workspace.warp/create/list/remove/syncList/status`), `types.gen.d.ts` (`Workspace`, `MoveSessionDestination`, `LocationRef`).
- Host DB (read-only): `session_v2` 1442 rows / 871 children / 0 workspace_id; `workspace` 0 rows; `worktree` 37 rows; `project_directory` 1–2 per project; `instruction_entry` 0 rows.
- Plugin: `opencode/index.js:238-252` (projectDir/domDir/env), `:287-289` (per-session maps), `:299-307` (DOM_SESSION), `:409-488` (`skillQueueFor`), `:496-520` (`deliverSkillBlock`), `:629-635` (plan stamp dedup); `gates/tau_gate.js:32,76-118`.
- Producer: `opencode/scripts/queue_producer.py:1-43` (contract), `:110-140` (cache), `:201-240` (CLI).
- Plan: `mockup/planning.py:335-397` (branch allocation), `:469-486` (resolve_task), `:775` (status); `mockup/brief.py:131-181` (current task), `:262-272` (property inheritance); `mockup/promptizer.py:53` (`gits`).
- Session/trust: `mockup/session.py:35-60`; `mockup/storage.py:230-231` (`claim_sessions`, `read_receipts`), `:35` (`SCHEMA_VERSION=5`).
- Live ledger: `schema_migrations` = 4; 582 claims; 349 plan branches; current plan r32 with 6 open items (`FZ`…`GE`).
- Deployment: `docs/VERSIONS.md`, `docs/deployment/map.md`; live 0.2.6 hash `75C988…`, source 0.3.1 hash `9DD3C5…`.
- Delivery snapshot: `.../observability/prompts/ses_dde70b42cffex6ErEzk7jd1a7k/2026-11-28T09-57-33-060Z-80c3f9dd.json` (`system` 6 blocks, origin `dom`; `core/worktree` block recommends `session_move`).

ANALYST_DONE
