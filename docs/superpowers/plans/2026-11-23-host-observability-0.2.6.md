# Observability 0.2.6 Implementation Plan (Revision 3 — split)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Revision 3 (2026-11-23):** Revision 2 was rejected because it mixed observability with a deep write/session change (C1/N1, C3/N3, M1, M16). This revision removes `claim_sessions`, `read_receipts`, `WriteContext`, and the gate from 0.2.6. Those move to `2026-11-23-trusted-write-loop-0.3.0.md`. 0.2.6 is read-only observability plus one resolver.

**Goal:** Make the assembled prompt and every hook/injection visible to the user, and make the memory path unambiguous, without changing claim writes or gating.

**Architecture:** One Python path resolver is used by every entry point. The OpenCode plugin records a DOM-position pre-lowering snapshot and a per-process JSONL journal. A read-only Python server exposes the journal, snapshots, claims, and plan to a local browser. No new claim columns, no session authority, no gate.

**Tech Stack:** Node 22 ESM, Python 3.11 stdlib, SQLite read-only, HTML/vanilla JS. No new dependencies.

**Spec:** `docs/concepts/2026-11-23-agent-loop-concept.md`; D-015 (gate is 0.3.0), D-018 (one resolver), D-019 (trusted session, DB receipts), D-020 (split 0.2.6/0.3.0).

## Global Constraints

- No new dependencies. Offline, deterministic runtime.
- Hooks never block the host. `record` and `snapshot` never throw.
- One resolver: `DOM_DIRECTORY` wins over `CLAUDE_PROJECT_DIR`/`OPENCODE_PROJECT_DIR`; every Python entry point uses it; `db_path` realpath is recorded in the journal and shown by health.
- Snapshot is DOM-position, pre-lowering semantic input, not the exact provider request body.
- 0.2.6 makes no schema changes to `claims`, `events`, `reviews`, or `plan_*`. No `claim_sessions`, no `read_receipts`, no gate.
- Shared files are mirrored tree-to-tree; the mirror list is explicit below.
- `VERSION` stays `0.2.4`; release is a separate card with approval.

**Mirror list (0.2.6):**
- Python shared: `opencode/mockup/paths.py`, `claim_entry.py`, `server.py`, `opencode/hooks/plan_hook.py`, `plan_check_hook.py`, `opencode/mockup/watcher.py` → `claude/`.
- JS shared: `opencode/index.js` → `claude/opencode-plugin/index.js`; `opencode/observability.js` → `claude/opencode-plugin/observability.js`.
- OpenCode-only in 0.2.6: `opencode/monitor/**` (the Claude tree does not ship the web monitor). This is an explicit exception, not an omission.

## Review Focus

- **One DB:** junction, case-variant, and Unicode paths must resolve to the same realpath; health fails on mismatch.
- **Snapshot truth:** hash is recomputed by the test, not asserted by truthiness; truncation is tested; secrets in messages are redacted.
- **Journal safety:** per-PID files, retention never deletes the current file, dropped events are counted.
- **Monitor security:** Host check, header token, no CORS, traversal rejected, oversize rejected, `textContent` only.
- **No scope creep:** no claim write path, no session authority, no gate in this release.

---

### Task 0: One memory resolver and preflight

**Status: DONE (2026-11-23).** Evidence: `docs/kanban/runs/2026-11-23-task0-memory-paths.md`. DOM claims: `5d07a08b-b854-4a37-95d9-731b7b328f37` (done), `504bf881-10aa-4839-b0d4-a2142d427bf9` (paths), `ecc1a028-01fa-4c05-a73c-97c6faf641f4` (preflight), `2f035f14-66b0-496c-97db-3d8cdab1c1a4` (evidence). All checks PASS; live MCP still 0.2.4 until release.

**Files:**
- Modify: `opencode/mockup/paths.py`
- Modify: `scripts/dom_server.py`, `opencode/hooks/plan_hook.py`, `opencode/hooks/plan_check_hook.py`, `opencode/mockup/watcher.py`
- Create: `scripts/router/memory_preflight.py`
- Create: `config/memory_registry.json` (harness-level, optional)
- Create: `config/memory_runtime_snapshot.json` (generated)
- Modify: `opencode/index.js` (set `DOM_DIRECTORY`, pass `--directory`)
- Modify: `opencode/claim_entry.py`/`server.py` (`dom_describe` returns `directory`, `db_path`, `profile`, `review_policy`)
- Test: `tests/check_memory_paths.py`, `tests/check_memory_preflight.py`

**Interfaces:**
- `resolve_paths(explicit, env, cwd) -> Paths(project_dir, run_dir, db_path)`; `db_path` is `os.path.realpath`.
- `build_memory_snapshot(registry, project) -> dict` with `schema`, `policy_hash`, `projects`, `selected`, `warnings`.

**Reuse from the harness router:** `capability_preflight.py` is the pattern: registry → deterministic preflight → `policy_hash` → fail-closed states. Do not import `resolve_route.py`; `resolve_bundle.py` remains a build-time source for Director playbooks.

- [ ] **Step 1: Write the failing tests**

```python
def test_dom_directory_wins_over_claude(tmp_path):
    env = {"DOM_DIRECTORY": str(tmp_path / "a"), "CLAUDE_PROJECT_DIR": str(tmp_path / "b")}
    assert resolve_paths(None, env, tmp_path).run_dir == str(tmp_path / "a")

def test_junction_case_and_unicode_realpath(tmp_path):
    target = tmp_path / "Проект"
    target.mkdir()
    junction = tmp_path / "junction"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(target)], check=True)
    assert resolve_paths(None, {"DOM_DIRECTORY": str(junction)}, tmp_path).db_path == resolve_paths(None, {"DOM_DIRECTORY": str(target)}, tmp_path).db_path
    assert resolve_paths(None, {"DOM_DIRECTORY": str(target).upper()}, tmp_path).db_path == resolve_paths(None, {"DOM_DIRECTORY": str(target)}, tmp_path).db_path

def test_preflight_mismatch_is_warning(tmp_path):
    snap = build_memory_snapshot(registry_with_two_projects(tmp_path), "plugin")
    assert "DB_PATH_MISMATCH" in snap["warnings"]

def test_dom_describe_reports_db_path(tmp_path):
    out = describe_in_temp_project(tmp_path)
    assert out["db_path"].endswith("dom.sqlite3")
    assert out["db_path"] == os.path.realpath(out["db_path"])
```

`registry_with_two_projects(tmp_path)` and `describe_in_temp_project(tmp_path)` are defined in `tests/fixtures/memory_paths.py`; create that file in this task.

- [ ] **Step 2: Run tests to verify they fail**

Run: `E:\opencode_harness_portable\.venv\Scripts\python.exe -B tests\check_memory_paths.py`
Expected: FAIL `AttributeError`/`KeyError`.

- [ ] **Step 3: Implement the resolver and preflight**

`resolve_paths` precedence: explicit → `DOM_DIRECTORY` → `CLAUDE_PROJECT_DIR` → `OPENCODE_PROJECT_DIR` → `HERMES_PROJECT_DIR` → cwd. Return `os.path.realpath` for `db_path`. Every Python entry point calls it. `opencode/index.js` sets `DOM_DIRECTORY` in the child env and passes `--directory`; it does not resolve a DB itself.

`memory_preflight.py` reads `memory_registry.json`, resolves each project, opens SQLite read-only, runs `PRAGMA integrity_check` and `verify_chain`, reads counts/profile/review, and writes `memory_runtime_snapshot.json` with `policy_hash` and warnings (`DB_PATH_MISMATCH`, `CHAIN_BROKEN`, `PROFILE_MISMATCH`, `MISSING_DB`). It never writes to the DB.

- [ ] **Step 4: Run tests to verify they pass**

Expected: PASS; `dom_describe.db_path` equals the preflight realpath.

- [ ] **Step 5: Mirror and commit**

Mirror `paths.py`, `dom_server.py`, hooks, `watcher.py`, `claim_entry.py`, `server.py` to `claude/`; SHA256 compare; commit.

---

### Task 1: Observability writer and journal

**Status: DONE (2026-11-23).** Evidence: `docs/kanban/runs/2026-11-23-task1-observability.md`. `node --test tests/check_observability.mjs` → 3/3 pass; mirror SHA256 equal.

**Files:**
- Create: `opencode/observability.js`
- Modify: `opencode/index.js`
- Test: `tests/check_observability.mjs`, `tests/check_observability.py`

**Interfaces:**
- `createObservability({ projectDir, pid, retentionDays = 7, maxPreview = 200 })`
- `record(event) -> void`, `snapshot(event, meta) -> {id, path, sha256_full, truncated}`, `close()`
- Event: `{ts, session_id, hook, phase, duration_ms, source, status, extracted, injected, context, error, dropped}`

- [ ] **Step 1: Write the failing test**

Drive `register(fakeCtx)` from `opencode/index.js`; invoke the registered `prompt` handler with a plain-text prompt and `sessionID`; assert one JSONL line with `hook: "prompt"`, `session_id`, `duration_ms`, `status`, and `db_path` realpath. Also simulate a write failure and assert `dropped` increments and the handler does not throw.

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/check_observability.mjs`
Expected: FAIL (no module/handler).

- [ ] **Step 3: Implement the writer**

Per-PID file `hooks-YYYY-MM-DD-<pid>.jsonl` (UTC). Cap lines at 64 KiB. Retention deletes only files older than the cutoff and never the current PID's file. `record` catches all I/O errors, increments an in-memory `dropped` counter, and never throws. Redact keys matching `authorization|token|secret|apiKey|password`; preview max 200 chars. Include `db_path` from the resolver.

- [ ] **Step 4: Instrument the hooks**

Record `source: dom.plan_check | dom.plan_hook | dom.capture`; `extracted` (plan revision, tasks, claim ids if available); `injected` (notes, chars, sha256); `context` (system chars before/after); `error`. No claim text.

- [ ] **Step 5: Run tests and commit**

Run: `node --test tests/check_observability.mjs` and the Python wrapper. Expected: PASS.

---

### Task 2: DOM-position pre-lowering snapshot

**Status: DONE (2026-11-23).** Evidence: `docs/kanban/runs/2026-11-23-task2-snapshot.md`. `node --test tests/check_prompt_snapshot.mjs` → 3/3 pass; hash recomputed in test; mirror SHA256 equal.

**Files:**
- Modify: `opencode/observability.js`, `opencode/index.js`
- Test: `tests/check_prompt_snapshot.mjs`

**Interfaces:**
- `snapshot(event, { source, notes }) -> { id, path, sha256_full, truncated }`
- Schema: `dom.prompt-snapshot/2` with `system`, `messages`, `tools` (schema-only), `options` (redacted), `dom.notes: [{text, source, claim_ids}]`, `counts`, `sha256_full`, `truncated`, `hook_order: "unknown"`.

- [ ] **Step 1: Write the failing test**

```js
const handler = await registerWithFakeCtx();
const event = {
  sessionID: "s1", agent: "build", kind: "primary",
  system: [{ type: "text", text: "base" }],
  messages: [{ role: "user", content: [{ type: "text", text: "token sk-abc123 and task" }] }],
  tools: { read: { description: "read", execute: () => {} } },
  options: { temperature: 0.2, apiKey: "secret" }
};
await handler.context(event);
const snap = JSON.parse(readFileSync(newestSnapshot(), "utf8"));
assert.equal(snap.schema, "dom.prompt-snapshot/2");
assert.equal(snap.dom.notes.length, 1);
assert.equal(snap.options.apiKey, "[REDACTED]");
assert.doesNotMatch(JSON.stringify(snap.messages), /sk-abc123/);
const canonical = JSON.stringify(snap, Object.keys(snap).sort());
assert.equal(snap.sha256_full, createHash("sha256").update(canonical).digest("hex"));
assert.equal(snap.truncated, false);
```

`registerWithFakeCtx()` and `newestSnapshot()` are defined in `tests/fixtures/prompt_snapshot.mjs`; create it in this task. Add a second case with a 300k message: `truncated === true`, `sha256_full !== sha256(truncatedText)`.

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/check_prompt_snapshot.mjs`
Expected: FAIL (handler/snapshot missing).

- [ ] **Step 3: Implement the snapshot**

Hash the canonical full semantic snapshot before truncation. Project tools to `{name, description, input_schema}`; never serialize executable functions. Redact secret-like strings in `system`, `messages`, and `options` by default; `DOM_SNAPSHOT_FULL=1` is an explicit opt-in. Cap messages at 50 and text at 200k with flags. `hook_order` stays `"unknown"`; do not claim exact final prompt. Write files under `.dom-memory/observability/prompts/` with owner-only permissions where the OS supports it; on Windows use a restrictive ACL or document the limitation.

- [ ] **Step 4: Hook it after injection**

In the existing `context` hook, after `event.system.push`, call `snapshot(...)` inside `try/catch`. The snapshot must not break the request.

- [ ] **Step 5: Run test and manual probe**

Run `node --test tests/check_prompt_snapshot.mjs`; one real prompt; confirm JSON blocks. Document it as DOM-position pre-lowering.

- [ ] **Step 6: Commit**

---

### Task 3: Monitor API

**Status: DONE (2026-11-23).** Evidence: `docs/kanban/runs/2026-11-23-task3-4-monitor.md`. `python -B tests/check_monitor_api.py` → PASS; real probe `CHAIN_OK`, 212 claims, r16.

**Files:**
- Create: `opencode/monitor/server.py`, `opencode/monitor/__init__.py`
- Test: `tests/check_monitor_api.py`

**Interfaces:**
- `serve(project_dir, port=0, token=None) -> (url, thread)`.
- Endpoints: `/api/health`, `/api/claims`, `/api/claim/<id>`, `/api/hooks`, `/api/snapshots`, `/api/snapshot/<id>`, `/api/plan`.

- [ ] **Step 1: Write the failing tests**

Start the server and assert `health.chain_status == "CHAIN_OK"`, `health.db_path` is the realpath, claims return items, counts are unchanged. Negative tests: `Host: evil.example` → 403; no token → 401; wrong token → 401; `OPTIONS` has no `Access-Control-Allow-Origin`; `/api/snapshot/..%2F..` → 400/404; oversize body → 413. Truncated DB → `STORAGE_CORRUPT`, not 500.

- [ ] **Step 2: Run test to verify it fails**

Run: `E:\opencode_harness_portable\.venv\Scripts\python.exe -B tests\check_monitor_api.py`
Expected: FAIL.

- [ ] **Step 3: Implement with `Store(read_only=True)`**

Verify that `Store` accepts `read_only=True` or add the constructor parameter; use it so rows are `sqlite3.Row` for `verify_chain`. Cache chain keyed on count/max seq; map `sqlite3.DatabaseError` to `STORAGE_CORRUPT`. Health reports `dropped_events` from the journal and a `JOURNAL_GAP` warning when it is non-zero. Token: `secrets.token_urlsafe(32)`, print once to stdout or write to a 0600 file; require `Authorization: Bearer`; `hmac.compare_digest`; no CORS; validate ids with `^[A-Fa-f0-9-]{36}$`; resolve snapshot paths inside `prompts/`; tail-read hooks with a limit; cap snapshot size.

- [ ] **Step 4: Run test and commit**

---

### Task 4: Monitor UI

**Status: DONE (2026-11-23).** Evidence: `docs/kanban/runs/2026-11-23-task3-4-monitor.md`. `python -B tests/check_monitor_ui.py` → PASS; real probe UI 200 with tabs.

**Files:**
- Create: `opencode/monitor/static/index.html`, `app.js`, `style.css`
- Test: `tests/check_monitor_ui.py`

- [ ] **Step 1: Write the failing test**

Fetch `/` and `/static/app.js`; assert four tabs, no external `http(s)` asset URLs, a CSP header, and that `app.js` contains `textContent` and no `innerHTML` for claim/snapshot content.

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL 404.

- [ ] **Step 3: Implement the UI**

Vanilla JS, `textContent` only, CSP, token in memory after an exchange. Hooks tab, Claims tab, Plan tab, Health tab, snapshot viewer with `system`/`messages`/`tools`/`options`/`dom.notes`. Native OpenCode Panel adapter is 0.3.1.

- [ ] **Step 4: Run test and manual probe**

---

### Task 5: MCP plan-ingest latency probe

**Status: DONE (2026-11-23).** Evidence: `docs/kanban/runs/2026-11-23-mcp-plan-ingest.md`. 10 runs: p50 236.591 ms, p95 342.362 ms, all `PLAN_INGESTED`; five hook-equivalence fields missing.

**Files:**
- Create: `tests/probe_mcp_plan_ingest.py`
- Evidence: `docs/kanban/runs/2026-11-23-mcp-plan-ingest.md`

- [ ] **Step 1: Write the probe**

Start the existing DOM server over stdio in a temp project; call `dom_plan_ingest` 10 times; assert at least 10 numeric samples, p50/p95, and a non-empty list of missing hook-equivalent fields (`origin`, `session`, `base_revision`, `contexts`, stamp). No production change.

- [ ] **Step 2: Run and record**

Expected: PASS; evidence file exists. Production decision is 0.3.0 with a user-agreed threshold.

---

### Task 6: Docs and release card split

**Status: DONE (2026-11-23).** Release card: `docs/releases/0.2.6.md`. No VERSION bump, dist, install, restart, or push; those are approval-gated.

**Files:**
- Modify: `README.md`, `NOT_DONE.md`, `opencode/skills/dom-memory/SKILL.md`, `claude/skills/dom-memory/SKILL.md`, `docs/kanban/2026-11-20-dom-memory-kanban.md`
- Create: release card in the kanban

- [ ] **Step 1: Update docs** with the pre-lowering snapshot, monitor, resolver, and the fact that session binding/gate are 0.3.0.
- [ ] **Step 2: Create the release card** naming target `0.2.6`, full check suite command, dist/install/restart/push approval gates.
- [ ] **Step 3: Do not bump `VERSION`, build dist, install, restart, or push in this plan.**
- [ ] **Step 4: Commit docs.**

---

## Self-Review

- Coverage: resolver (Task 0), journal (Task 1), snapshot (Task 2), monitor API/UI (Tasks 3–4), MCP probe (Task 5), docs/release split (Task 6).
- No claim writes, no `claim_sessions`, no `read_receipts`, no gate. This is the scope split that answers C1/N1/C3/M16.
- Tests are falsifiable: realpath junction/case/Unicode, hash recomputation, secret in messages, timeout, monitor negatives, truncated DB.
- Open risk: `Store(read_only=True)` constructor support must be verified in Task 3; `ctx.plugin.list` is not used; `hook_order` is `unknown`.
