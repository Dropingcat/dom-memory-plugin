# Audit A–E — DOM Memory / opencode-dom-memory-plugin

**Repo under audit:** `E:\opencode_harness_portable\projects\opencode-dom-memory-plugin` @ `cb51800` (tag `v0.3.1`, lightweight tag)
**Audit mode:** READ-ONLY. No repo files edited. Temporary read-only query scripts written to `C:\Temp\opencode\` only.
**Date:** 2026-11-28
**Working tree state:** **DIRTY** — 14 files changed (+459 / −210) plus untracked files. `HEAD cb51800` is not what is on disk.

> ## ⚠️ Scope finding that governs every section below
> The DOM server answering this session is **not** the audited repo.
>
> | | Audited repo | Actually running |
> |---|---|---|
> | Version | 0.3.1 (`cb51800`) | **0.2.6** (`dist/claude/dom-memory/VERSION`) |
> | Script | `opencode/scripts/dom_server.py` | `…omh-baseline-dom-mcp\.claude\worktrees\mcp-llm-server-054018\dist\claude\dom-memory\scripts\dom_server.py` |
> | Project dir / DB | `opencode-dom-memory-plugin\.dom-memory` (207 claims, 139 branches, `main` rev 9) | `omh-baseline-dom-mcp\.dom-memory` (582 claims, 350 branches, `main` rev 207) |
> | Schema | v5 (`claim_sessions`, `read_receipts`) | **v4 — those tables do not exist** |
> | Tool count | 35 (incl. `dom_help`, `dom_skill_select`) | **29 — neither tool exists** |
>
> Source of truth: `E:\барахло\Documents\Default Project\opencode.json` lines 5–16 point `dom-memory` at the 0.2.6 dist build with `OPENCODE_PROJECT_DIR=…omh-baseline-dom-mcp`.
> Consequence: all ledger measurements below describe the **0.2.6 baseline ledger**, not claims produced by the 0.3.1 code under review. Section E turns this into a first-class defect.

---

## A. TRACEABILITY — план → claim → step → skill → commit

### A.1 Current plan (`dom_plan_tree({root:"П1",depth:10})`)

Plan revision **r32**, ingested 2026-11-28T09:04:25Z, `stale=false`, `integrity=CHAIN_OK`.

| Path | Branch | Ver | Status | Title (truncated) |
|---|---|---|---|---|
| П1 | FZ | 1 | pending | ``В `index.js` → `skillQueueFor()`:`` |
| П2 | GA | 1 | pending | `Исправить лог: писать … injected FAIL` |
| П3 | GB | 1 | pending | `Добавить проверку: после put, прочитать entry обратно` |
| П4 | GC | 1 | pending | ``Проверить, что `brief.py` действительно вызывается`` |
| П5 | GD | 1 | pending | ``Если вызывается — добавить отладку: console.log("brief CAP:")`` |
| П6 | GE | 1 | pending | `Если не вызывается — контракт 1.3 не завершён` |

**`depth:10` returns zero children.** The plan is 6 flat depth-1 items. Every title is a fragment of a code-review checklist, not a project plan. All are `version:1`, all `source_revision:r32` — i.e. r32 created a brand-new 6-item plan and archived the previous one.

Plan history in the DB (`plan_task_versions`):

- 346 plan task ids, **776 versions**, **31 revisions** (`r1, r2, r4…r32` — `r3` is absent).
- Live at head: **6**. Archived at head: **340**.
- r32: 6 new items, **23 archived**. r31: 23 live / 42 archived. r30: 42 / 32. The plan has been rewritten almost every revision (`plan_checks`: 58 ingests, 309 checks, 9 requests).
- Live items are **all depth 1** (`{"1": 6}`). There is no П1.2, П2.1, or any sub-structure in the current projection.

**Contradiction found:** `dom_task_graph({task:"П1.2"})` still resolves `П1.2 → branch FI200, plan_task pt-a0d56626cd9feb4d`, but `dom_plan_tree(П1)` shows no children. FI200 belongs to r30/r31 (v2 archived at r31). So an archived path is still resolvable while the live tree denies it — stale projection residue, not an error, but it makes "current plan" ambiguous to a reader.

Also: `dom_task_graph({task:"П1.2", include_history:true})` returned **`chains: []`, `agents: []`** even though branch FI200 has 1 claim (`9e3fef22`, decision) linked with role `defines` to `pt-a0d56626cd9feb4d`. **The task graph drops links of archived items.** That is a genuine stitching defect: `dom_task_claims` finds the claim, `dom_task_graph` does not.

### A.2 Claim ↔ plan linkage (authoritative DB counts)

| Metric | Value |
|---|---|
| Claim versions stored | **582** |
| Superseded (replacement events) | **40** |
| Current claims (`dom_find`, `current_only`) | **540** |
| `task_claims` link rows | **408** |
| Distinct claims with ≥1 plan link | **384** |
| Linked to a **live** plan item | **6** |
| Linked **only** to archived plan items | **378** |
| **No plan_task link at all** | **198** |
| Distinct plan tasks ever linked | 282 (of 346; **276 of them are archived**) |

Live-plan links are exactly the 6 header claims (`defines`): `8508a22f` (GB), `ee849545` (FZ), `ffe0072e` (GA), `c6dc3da0` (GE), `1dfe1acd` (GC), `d3ed66bd` (GD). Each live item has exactly **1** claim — its own header. No implementation, validation or dependency claim is attached to any live item.

Role distribution overall: `defines 303`, `documents 81`, `implements 15`, `justifies 1`, `validates 8`. "implements" and "validates" (the roles that would carry code↔plan traceability) are **23 of 408 = 5.6%**.

### A.3 Commit ↔ claim

`git log v0.2.6..v0.3.1` → **47 commits** (`v0.3.0..v0.3.1` = 17).

- Commits whose message contains a `Claim:` reference: **1 / 47 (2.1%)** — only `eca09b6`, body `Claim: 5693405b (AQ200, text, findings record)`.
- The referenced claim `5693405b-fcaa-4152-b395-c998437340d7` exists (AQ200, `text`), but has **zero `task_claims` links** — it is not attached to П1.2 or any other item.
- Whole history: 72 commits, **1** mentions a claim (1.4%).
- 11/47 commits reference an issue id (`BUG-n`, `FAIL-n`, `L1..L8`, `E4/E6`, `C1`, `P0/P1`) — issue ids are the *de facto* trace key, not claims.

### A.4 Verdict A

| Check | Result |
|---|---|
| Commits without a claim | **46 / 47 (97.9%)** |
| Claims without any plan_task | **198 / 582 (34.0%)** |
| Claims with no **live** plan_task | **576 / 582 (99.0%)** |
| Current plan items with real sub-structure | **0 / 6** |
| Plan items with >1 attached claim | **0 / 6** |

**Severity: CRITICAL.** End-to-end traceability does not exist. The plan projection was replaced at r32 by six flat code-review fragments; 340 plan items were archived, orphaning 378 claim links and 276 plan-task anchors. Commits are essentially never stitched to claims (1/47), and the single stitched claim is itself unlinked from the plan. The chain план→claim→step→skill→commit is broken at **every** hop.

---

## B. GIT + CLAIM STITCHING

### B.1 Last 30 commits (`HEAD~30..HEAD`)

| Commit | Claim ref | Issue refs | Subject |
|---|---|---|---|
| `cb51800` | — | BUG-1 | v0.3.1 post-test: help data strict-only markers + test schema fix |
| `f230782` | — | BUG-1, BUG-4, C1 | test: align schema assertions with optional step/tool |
| `eca09b6` | **5693405b** | — | test: full-cycle test reports (12 issues found) |
| `b7d0729` | — | — | 0.3.1 hotfix: step_cap in render_block, describe budget 15000 |
| `abec628` | — | — | 0.3.1 Batch 2: gate fixes + policy declared |
| `adb9adf` | — | — | 0.3.1 Batch 1: card merge, fingerprint, sigma-envelope, tracker |
| `e864fde` | — | BUG-1, BUG-4, BUG-5, FAIL-1b | 0.3.1 Batch 0 |
| `bffc495` | — | P1 | 0.3.1 P1: skill queue producer with tau-gate and flashback |
| `6f697bd` | — | P0 | 0.3.1 P0: card-only merge + cap/mention separation |
| `39c3817` | — | — | cards: pubchem category corrected; test threshold relaxed |
| `2c3b26b` | — | — | help docs: 71 codes, 35 tool cards |
| `3cf022d` | — | — | help layers: error help, dom_help, error gate |
| `5bf7a42` | — | — | dev config: final accuracy pass |
| `70282c8` | — | — | dev config: OPENCODE_CONFIG layering semantics |
| `9f793f9` | — | — | infra: dev config, inventory map, help data |
| `1fc6b79` | — | — | release: 0.3.1 version bump, README, VERSIONS |
| `0b4f31c` | — | — | skill router: pipeline implementation complete |
| `75a7731` | — | L1–L8 | release 0.3.0: trusted write loop complete |
| `0c435cd` | — | L2 | correction: claim_sessions mutable (read_receipts immutable) |
| `3b9616a` | — | L2 | V5 migration: claim_sessions + read_receipts |
| `388e88f` | — | L1 | WriteContext + writer_inventory |
| `83d3568` | — | — | PORTABILITY: audit, capability data, portable runner |
| `8b94d75` | — | — | ROUTER: step_state `awaiting_definition` |
| `b76f872` | — | — | ROUTER: goal variable, needs from accept, fit matrix |
| `dd644cb` | — | — | ROUTER: capability needs/provides; empty queue |
| `bf4886f` | — | — | ROUTER: step→skill selector prototype |
| `1ff6156` | — | — | CONCEPT: router heuristic on default agent |
| `6b57fa5` | — | E6 | dashboard skill + permissions → 575 |
| `6df1c21` | — | E4 | catalog filtering unavailable; stays additive |
| `dac3bf8` | — | — | CONCEPT: measured prompt layout, sensing contract |

### B.2 Claims ↔ commit_ref

| Metric | Value |
|---|---|
| Claims carrying `commit_ref` in metadata | **1 / 582 (0.17%)** |
| Of `code` claims (42) with `commit_ref` | **1 / 42 (2.4%)** |
| The one `commit_ref` | claim `f312156f` (AQ100, code) → `3b9616ad948a0df2…`, message "L2 V5 migration…" |
| Claims whose text contains a hex token ≥7 chars | 15 |
| Distinct tokens in claim text | 16 |
| **Tokens that are not commits in this repo** | **9 / 16** |

Unverifiable tokens in claim bodies: `b11c3ac`, `e9d4bb9`, `6f1c9a7`, `6178d57`, `d402b5b`, `7f757d7e`, `a99d4b07`, `a1b2c3d4`, `e5f6a7b8`. Real commits: `b48034c`, `c3639be`, `388e88f`, `0c435cd`, `1fc6b79`, `5bf7a42`, `3b9616a`.
(`a1b2c3d4`/`e5f6a7b8` in claim `fb2f4048` are illustrative placeholders; `7f757d7e`/`a99d4b07` look like claim-id prefixes pasted into a quote. Either way, a reader cannot tell a real commit from a fake one.)

**Metadata coverage** (`claims.payload.metadata`): 486 have `rationale`/`assumptions`/`attribution`, 93 `locator`, 42 `relative_path`/`repository_ref`/`snapshot_digest`, 3 `artifact_ref` — **1** `commit_ref`. The schema supports stitching; the discipline does not use it.

### B.3 Working tree

`git status --porcelain`: 10 modified + 4 untracked code/docs entries; `git diff --stat`: **14 files, +459/−210** (e.g. `opencode/index.js`, `opencode/mockup/{brief,claim_entry,planning,server,skill_index}.py`, `tests/check_brief_cap.py`, `tests/check_skill_producer.mjs`).
Therefore the code a reader sees is **not commit `cb51800`** and cannot be referenced by any `commit_ref`. Post-`cb51800` work is entirely untraced.

### B.4 Verdict B

| Coverage | Value |
|---|---|
| Commits with a claim (last 30) | **1 / 30 (3.3%)** |
| Commits with a claim (v0.2.6..v0.3.1) | **1 / 47 (2.1%)** |
| Claims with `commit_ref` | **1 / 582 (0.17%)** |
| Code claims with `commit_ref` | **1 / 42 (2.4%)** |
| Commit hashes in claim text that actually exist | **7 / 16 (44%)** |
| HEAD matches working tree | **No** |

**Severity: MAJOR.** Git→claim stitching is a single lucky reference. Claim→commit is one `commit_ref` in the whole ledger. Worse, over half of the commit-looking tokens in claim bodies are fabricated or mis-pasted, so the reverse direction is actively misleading if trusted.

---

## C. PARALLEL BRANCHES

### C.1 Snapshots (`dom_query`)

| Branch | Rev | Claims | Types | Plan anchor | Anchor rev | Anchor live? |
|---|---|---|---|---|---|---|
| **AQ200** | 8 | **7** | 5 decision, 2 text | П1.2 `pt-a6d652f4…` | r24 | **NO — archived** |
| **AP200** | 30 | **24** | 10 decision, 9 code, 5 text | П16.2 `pt-d78c7c12…` | r23 | **NO — archived** |
| **AN300** | 6 | **4** | 1 code, 1 decision, 2 text | П14.3 `pt-9999fab7…` | r23 | **NO — archived** |
| AQ (parent) | 2 | — | — | П1 (r24) | r24 | NO — archived |
| AP (parent) | 2 | — | — | П16 (r23) | r23 | NO — archived |
| AN (parent) | 3 | — | — | П14 v3 (r23) | r23 | NO — archived |
| main | 207 | — | 28 code, 29 decision, 46 quote, 77 text | *none* (root branch) | — | n/a |

### C.2 Intersections

- Shared claim ids: **AQ200∩AP200 = 0**, **AQ200∩AN300 = 0**, **AP200∩AN300 = 0**.
  No claim is duplicated across the three branches; each branch is an isolated line of work. (Branches cannot share a claim by construction, so 0 is the expected result — it is not evidence of coordination.)
- Cross-branch references: none found via `task_claims` (each linked only to its own archived item).
- `plan_conflicts`: **0 rows**. `dom_reconcile` has nothing to converge.
- Claims that mention another branch's topic (e.g. AQ200's `5693405b` full-cycle findings vs AP200's router code) are **not linked to each other** — no shared basis, no dependency edge.

### C.3 Plan-tree conflicts

`dom_plan_status` reports `conflicts: []`, but the projection itself is contradictory:

1. Three live branches (AQ=П1, AP=П16, AN=П14 at r23/r24) point at items that the current r32 plan **does not contain**; r32's П1 is a different item on branch FZ.
2. `dom_plan_tree(П1)` → FZ, no children. `dom_task_graph(П1.2)` → FI200, archived. Both claim to answer "П1.x".
3. Branch codes are consumed monotonically: 350 branches exist, 64 have `revision=0` (allocated, never written), 1 (`main`) has no plan item. Re-ingesting the plan each revision burns codes and leaves a large dead branch space.

### C.4 Verdict C

| Question | Answer |
|---|---|
| Branch health | **Data-healthy, anchor-dead.** All three branches have claims, clean hash chain, 0 conflicts, 0 cross-contamination. |
| Intersections | **0 shared claim ids** across AQ200/AP200/AN300. |
| Conflicts | **0** `plan_conflicts`; but every branch is anchored to an item archived at r23–r24, so "which plan item does this branch serve?" has no live answer. |
| Most active non-plan branch | AP200 (24 claims, rev 30) — it carries the skill-router work yet has no live plan anchor. |

**Severity: MAJOR.** Parallel branches are technically isolated (good) but collectively orphaned by the r32 plan wipe. Any reader following `dom_plan_tree` will conclude the AP200/AN300/AQ200 work does not exist.

---

## D. SKILL COVERAGE

### D.1 `opencode/mockup/skills.card.json`

| Metric | Value |
|---|---|
| Cards | **20** |
| `state: verified` | **20 / 20** |
| Categories | databases 4, coding 3, review 3, research 3, physics 3, writing 2, chemistry 2 |
| `provides` entries | 41 |
| **Unique provides tokens** | **18** |
| Duplicate ids | none (file 5,083 bytes, mtime 2026-11-27 21:21) |

Cards: `dom-memory, sqlite-migration, claim-ledger, test-driven-development, systematic-debugging, verification-before-completion, code-review-and-quality, doubt-driven-development, verify, writing-plans, writer-core, sources, brainstorming, literature-review, pde-solver, fluid-dynamics, dynamical-systems, rdkit, pubchem-database, admet-prediction`.

### D.2 `skill_index.py` collect (executed read-only, temp cache)

| Metric | Value |
|---|---|
| Records collected | **155** |
| `card_only` (card with no SKILL.md on disk) | **7** |
| Records with `verified_provides` | **20** |
| Unique verified tokens | **18** |
| Records with no category | **38 / 155** |
| Records with no `mentions` | **17 / 155** |
| Fingerprint | `0edbf39784fecd6cdd540b9f4c282ec20ed8706eec58df54f1702fed434a4a4f` |

13 of the 20 cards resolve to an on-disk SKILL.md and are enriched; only 7 are virtual.

### D.3 `python -B tests/check_skill_index.py`

```
PASS skill_index: records, parsing, mentions, precedence, BM25 proposer, cache, card merge, card fingerprint
exit 0
```
Test verifies mechanics only. It does **not** assert that card tokens exist in the capability dictionary.

### D.4 Coverage against the closed capability dictionary

`opencode/mockup/capabilities.json` (v1) defines a **closed** vocabulary of **14** capabilities: `testing, sqlite, migration, ledger, docs, ops, runtime, security, router, prompt, writer, review, git, panel`. `skill_router.py:44` states "A capability that is not here cannot be routed."

| Verdict | Tokens |
|---|---|
| Covered by ≥1 verified card (9/14 = 64%) | testing, sqlite, migration, ledger, docs, runtime, prompt, writer, review |
| **Uncovered — no card can ever satisfy** (5/14) | **ops, security, router, git, panel** |
| Card tokens **outside** the dictionary — unroutable (9) | planning, debugging, research, physics, simulation, chemistry, molecule, databases, toxicity |

**Consequence:** an `accept` item that maps to `router`, `git`, `panel`, `ops` or `security` produces `blocked_capability` no matter how many skills are installed. `bffc495`/`0b4f31c` implement a router whose own capability (`router`) no card provides.

### D.5 Verdict D

| Check | Result |
|---|---|
| Card count | 20 (as expected) |
| Verified provides coverage | 9 / 14 closed capabilities |
| Structural gaps | 5 capabilities unroutable; 9 card tokens dead; 7 card-only skills unmatchable by `mentions`; 38/155 skills uncategorised; 17/155 without mentions |
| Test status | PASS (mechanics) — does not catch any of the above |

**Severity: MAJOR.** The card layer is consistent and tested, but it is wired to a vocabulary it only 64%-covers, and it advertises 9 tokens the router silently ignores.

---

## E. ORCHESTRATOR SELF-CHECK

### E.1 `dom_describe()`

- `status: DESCRIBED`, `profile: lite`, `simulated: true`, `state: READY`
- `work_ref: lite:0b82d8fac1079e33eff6`
- **29 tools**: `dom_describe, dom_start, dom_claim, dom_update, dom_find, dom_resume, dom_query, dom_trace, dom_export, dom_sync_status, dom_sync, dom_plan_ingest, dom_plan_tree, dom_task_claims, dom_plan_status, dom_task_graph, dom_context, dom_extract, dom_extract_submit, dom_annotate, dom_accept, dom_claim_versions, dom_recall, dom_diff, dom_reconcile, dom_candidate, dom_review, dom_admit, dom_distillate`
- **Missing vs 0.3.1:** `dom_help`, `dom_skill_select` (both present in `opencode/mockup/server.py:193` and `:220`).
- No `read_gate` field is advertised. `claim_sessions` / `read_receipts` tables **do not exist** in the live DB (schema v4), so the read-gate layer added by `3b9616a`/`0c435cd` is inert.
- Health: `integrity_status: CHAIN_OK`, 350 branches, 0 pending conflicts, `inbox_processed/queued/failed = 0`.

### E.2 `dom_plan_status()`

- `has_plan: true`, `project_closed: false`, `source_revision: r32`, `items: 6`, `open_items: 6`
- `iterations_since_change: 10`, `check_every: 10` → **`plan_check_due: true`**
- `request.kind: plan_check` — "has the project plan changed since r32? … otherwise resend it unchanged"
- `last_request_at: 2026-11-28T09:46:08Z`
- **Read-only audit does not re-ingest.** Flagging for the parent: the memory is asking for a plan resubmission, and the plan it holds is the 6-item fragment from §A.

### E.3 `dom_help({tool:"dom_skill_select"})`

```
ERR: Unknown tool 'dom-memory.dom_help'. Did you mean tools["dom-memory"].dom_accept?
```
The help layer shipped in `3cf022d`/`2c3b26b` is **not reachable in this session**. `docs/help/dom-errors.md` (71 codes, 35 tool cards) documents tools the running server does not expose.

### E.4 Does `dom.skill-queue` appear in the session?

**No — zero occurrences, confirmed three ways.**

| Evidence | Result |
|---|---|
| 138 prompt snapshots under `.dom-memory/observability/prompts/` | `dom.skill-queue` / `skill-queue` = **0 hits** |
| 189 hook events in `observability/hooks-*.jsonl` | `skill-queue` = **0; `[τ-gate]` = 0**. `injected` (137×) is always `dom.notes` only (`{"notes":1,"chars":2785}`) |
| Running dist build | **0** references to `skill-queue` / `skillQueueFor` / `dom_skill_select` |
| Audited repo | **48** references across `opencode/index.js` (11), `claude/opencode-plugin/index.js` (11), `opencode/scripts/queue_producer.py` (1), `claude/scripts/queue_producer.py` (1) |

Root cause is not the gate: the injected plugin in `Default Project/opencode.json:22` is the **0.2.6 dist**, which predates the queue producer. The 0.3.1 producer, τ-gate and `render_block` never load in this workspace. This independently reproduces finding **F1** recorded in claim `5693405b` ("dom.skill-queue NOT rendered").

### E.5 Hook health (observability)

| Status | Count |
|---|---|
| injected | 137 |
| ok | 42 |
| INVALID_ARGUMENTS | 4 |
| INVALID_FIELD | 4 |
| silent | 2 |
| **total** | **189** |

8 tool-hook refusals (`dom.mcp`) are the BUG-1 / step-bounds negative tests recorded on 2026-11-27; they are expected behaviour, not regressions. `silent` = plan-check/context hooks that chose not to inject.

### E.6 Verdict E

| Check | Result |
|---|---|
| What the orchestrator sees | 29-tool lite server, v0.2.6, project root `omh-baseline-dom-mcp`, 582-claim ledger, CHAIN_OK |
| `dom_help` | **absent** (unknown tool) |
| `dom_skill_select` | **absent** |
| `dom.skill-queue` | **absent from all 138 prompt snapshots** |
| read_gate | not present (schema v4) |
| Plan status | check due; 6 open flat items; 0 conflicts |
| Hook pipeline | alive but only `dom.notes` is injected |

**Severity: CRITICAL.** The session is auditing 0.3.1 code through a 0.2.6 runtime pointed at a different project. Every "what did DOM do?" observation describes the old build; the 0.3.1 help layer, skill router and τ-gate cannot be exercised from here. Any conclusion of the form "0.3.1 works / doesn't work" drawn from this session is unsupported until the plugin is re-pointed at the audited repo's `opencode/` build.

---

## Consolidated verdict

| Area | Severity | One-line finding |
|---|---|---|
| **A** Traceability | **CRITICAL** | 46/47 commits have no claim; 576/582 claims have no live plan item; plan r32 is 6 flat code-review fragments with zero children. |
| **B** Git↔claim | **MAJOR** | 1/30 commits reference a claim; 1/582 claims carry `commit_ref`; 9/16 commit-looking tokens in claim text are not commits; HEAD is dirty. |
| **C** Branches | **MAJOR** | AQ200/AP200/AN300 are isolated (0 shared claims, 0 conflicts) but all three are anchored to plan items archived at r23–r24. |
| **D** Skills | **MAJOR** | 20/20 verified cards, test PASS, but only 9/14 closed capabilities covered; `router`, `git`, `panel`, `ops`, `security` unroutable; 9 card tokens dead. |
| **E** Orchestrator | **CRITICAL** | Session runs v0.2.6 from a different project dir; `dom_help`/`dom_skill_select` absent; `dom.skill-queue` present in 0/138 prompts; 0.3.1 is not deployed here. |

### Highest-value fixes, in order

1. **Deploy the audited build.** Point `Default Project/opencode.json` at `…opencode-dom-memory-plugin/opencode` with `projectDir` = that repo, restart, and re-run E. Until then A–D measure the wrong system.
2. **Stop wiping the plan.** r23→r32 re-ingested the plan into 6 fragments and archived 340 items. Ingest the real plan with stable ids/sub-items; r32's titles must be plan items, not review bullets.
3. **Make `dom_task_graph` see archived-item links** (FI200 shows 0 chains while `dom_task_claims` shows 1). Otherwise history is invisible exactly when it matters.
4. **Enforce claim↔commit at commit time** (the one `Claim:` trailer proves the convention works). Target ≥1 `commit_ref` per `code` claim; forbid commit-looking tokens in prose without a `commit_ref`.
5. **Close the capability vocabulary gap** — add cards for `ops`, `security`, `router`, `git`, `panel`, or delete those tokens from `capabilities.json`; assert the intersection in `check_skill_index.py`.

### Evidence index (read-only)

```
git -C <repo> log --format="%h|%ad|%s" --date=short v0.2.6..v0.3.1     # 47 commits, 1 with Claim:
git -C <repo> rev-list --count HEAD                                    # 72
git -C <repo> status --porcelain ; git diff --stat                     # dirty, 14 files +459/-210
sqlite3 (ro) omh-baseline-dom-mcp/.dom-memory/main/dom.sqlite3
    claims=582  events(replacement)=40  plan_task_versions=776  plan_live=6  plan_archived=340
    task_claims=408  linked_live=6  linked_archived_only=378  unlinked=198
    metadata.commit_ref=1  plan_conflicts=0  plan_checks=376 (58 ingest)
python -B tests/check_skill_index.py                                   # PASS, exit 0
SkillIndex.collect()                                                   # 155 records, 7 card_only, 18 tokens
opencode/mockup/capabilities.json                                      # 14 closed caps
observability/prompts/* (138 files)                                    # dom.skill-queue: 0 hits
observability/hooks-*.jsonl (189 events)                               # injected 137 (notes only), 8 refusals
dist/claude/dom-memory/VERSION                                         # 0.2.6
```
