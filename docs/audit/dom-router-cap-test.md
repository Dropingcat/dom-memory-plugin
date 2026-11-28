# DOM router: cap → queue → dynamic permissions → prompt reduction

**Tester run:** 2026-11-28 ~12:40 UTC (15:40 local)
**CLI:** `C:\Users\Arhys\AppData\Local\Programs\ai.opencode.desktop\resources\opencode-cli.exe`
**Config:** `E:\opencode_harness_portable\projects\opencode-dom-memory-plugin\opencode\opencode.dev.json`
**Model:** `polza/deepseek/deepseek-v4.1-flash-1017`, agent `dom-router`
**Live session:** `ses_dddd46ee5ffeRDb3vIAhl7kgrA`
**Repo state:** HEAD `45f1b31` + uncommitted tester-visible WIP (`tests/check_brief_cap.py`, `tests/check_skill_producer.mjs`, `docs/…`). No repo files were edited by this test.

---

## 1. Test 1 — explicit cap

### Command

```powershell
$env:OPENCODE_CONFIG='E:\…\opencode\opencode.dev.json'
opencode-cli.exe run --standalone --print-logs --auto --format json \
  --model polza/deepseek/deepseek-v4.1-flash-1017 --agent dom-router \
  "Вызови dom_skill_select с шагом {title:'L2 V5 migration: claim_sessions and read_receipts', cap:['sqlite','migration']}. Покажи весь ответ: queue, block, gaps, missing_caps, signals."
```

Exit code `0`; stdout 317 946 B; stderr 129 220 B.

### `dom_skill_select` result (agent did call the MCP tool; payload verified from tool output)

| field | value |
|---|---|
| status | `SKILLS_SELECTED` |
| step_id / plan_version | `current` / `0` |
| index_digest | `f76fde8c…bee68f` |
| fingerprint | `0b6bd5c7…38bf568` |
| **queue** | `["dom-memory", "sqlite-migration", "openalex-database"]` |
| confidence_bp | `10000` |
| **missing_caps** | `[]` |
| groups | `["databases"]` |
| advisory | biorxiv-database, chembl-database, cosmic-database, drugbank-database, fda-database |

**block** (rendered):

```
step: L2 V5 migration: claim_sessions and read_receipts [migration,sqlite | databases]
use: dom-memory — matched: bm25,cap,card,group,marker; verified for cap: sqlite
how: Use skill(dom-memory) with step context: L2 V5 migration: claim_sessions and read_receipts
gaps: none
```

**signals:** dom-memory 5 (`bm25,cap,card,group,marker`), sqlite-migration 5, openalex-database 3 (`bm25,group,marker`). Threshold ≥3 admits all three.

**gaps:** `dom_skill_select.gaps` is **not** "uncovered capabilities" — it is the leftover index (146 skills, first 20 shown, `"... and 127 more"`). The block line `gaps: none` refers to `missing_caps`, so block and `gaps` field contradict each other in wording. The producer has the opposite convention (`gaps := missing_caps`, `gap_skills_count := len(leftover)`), see §4.3.

### Separate: the plugin's own producer (the pipeline that actually runs on the prompt)

Deterministic run of `opencode/scripts/queue_producer.py`:

| step sent to producer | status | queue | gaps | block |
|---|---|---|---|---|
| `{step_id:"П1", title:"0. Аксиома", cap:[]}` | OK | `[]` | 0 | `use: skill_search — no consensus queue…` (256 chars) |
| `{step_id:"L2", title:"L2 V5 migration…", cap:["sqlite","migration"]}` | OK | `["dom-memory","sqlite-migration","openalex-database"]` | 0 | `use: dom-memory … verified for cap: sqlite` |

The producer and the MCP selector agree. **But the live session's producer never received the cap**, because the cap is not read from the user prompt — only from the plan-brief `CURRENT` line. The repo's DOM plan (`planDir=C:\Users\Arhys\.opencode\plan`, brief `plan r7 · 44 items`, education-methodology project) has no `accept` envelope, so the live step was:

```
step: Данные не дублируются: каждая заметка существует в `notes` ровно один раз. [no-cap | no-category]
use: skill_search — no consensus queue for this step
```

That 256-char block is exactly what the plugin injected (`skill_search`, `fp 645ae246ee9c`).

**Side effect:** the agent also wrote a `dom_claim` into branch `BO` (П1) as part of its protocol. Append-only; no repo source touched.

---

## 2. Stderr analysis — was dynamic permissions triggered?

**No.**

| expected log | found |
|---|---|
| `[dom-memory] skill-queue injected via system: sqlite-migration` | ❌ one injection, but `skill_search` |
| `[dom-memory] permissions: session allow-list` | ❌ **0 occurrences** |
| `[dom-memory] permissions: ctx.session.update unavailable` | ❌ 0 |
| errors from the DOM plugin | none (`dom-memory` LoadError count = 0; other global plugins do fail to load — unrelated) |

Exact line:

```
[dom-memory] skill-queue injected via system: skill_search (256 chars, idle 0, fp 645ae246ee9c)
```

Why: `skillQueueFor()` calls `applySkillPermissions(sessionID, queueList)`; `permissionRulesFor()` returns `null` for an **empty** queue (`ids.length === 0`), so the update is skipped entirely — not attempted and failed. The later `top = queueList[0] || "skill_search"` fallback still renders a block, which is why an injection is logged for a skill that is in no queue.

**So Test 1 did not exercise the dynamic-permission branch at all.** The branch is covered by unit tests (below) and by a host-capability probe (below), but not by this live run.

### Host capability probe (independent of DOM, temp config, no repo edits)

`C:\Temp\opencode\hostprobe\` — a 20-line plugin that on the first `context` hook calls
`ctx.session.update({sessionID, permissions:[{action:"skill",resource:"*",effect:"deny"}]})`.

| probe | result |
|---|---|
| session keys | `hook,list,create,get,remove,switchAgent,switchModel,prompt,generate,command,compact,synthetic,interrupt,update,move,wait,context,instructions` |
| `typeof ctx.session.update` | `function` ✅ (requires `sessionID`; without it throws `Missing key at ["sessionID"]`) |
| `instructions.entry` methods | `list, put, remove` (no `set` — matches the plugin's `put` path) |
| deny-all then `skill("astropy")` | `FAILED — Permission denied: skill` |
| same prompt, no deny (control) | `LOADED` |
| catalog size before/after deny | **328 → 328 `<skill>` occurrences (unchanged)** |

Conclusion: the host honors session skill rules for **loading**, and `update` is available. It does **not** rebuild the system-prompt skill catalog mid-session; catalog filtering happens once at session start from the agent profile. Dynamic "allow" therefore unlocks an id named in the queue block, it does not shrink or grow the prompt.

### Deterministic tests

| test | result |
|---|---|
| `node --test tests/check_dynamic_permissions.mjs` | **3/3 PASS** — queue→ruleset, dedup, replace, strict, off, missing API, retry after reject |
| `node --test tests/check_skill_producer.mjs` | **3 pass / 1 fail** — F1 delivery assertions pass (block in `event.system`, `entry.put` attempted); the test fails only on its log assertion because it captures `console.log` while the plugin logs via `console.warn` (line 146). Stale test, not a delivery bug. |
| `python -B tests/check_brief_cap.py` | **FAIL** — `StopIteration`: after `plan_ingest` with `accept`, `project_brief()` emits **no `CURRENT` line at all**. This is the uncommitted F2 work in the working tree. |

---

## 3. Test 2 — prompt snapshot analysis

Newest session dir `.dom-memory/observability/prompts/ses_dddd46ee5ffeRDb3vIAhl7kgrA/` — 6 snapshots.

| snapshot | blocks | system text chars | skills XML | available_skills | queue block |
|---|---|---|---|---|---|
| `…12-40-20` (first request) | 7 | **15 871** | 1 118 | 3 | **256** |
| `…12-40-23` | 6 | 15 615 | 1 118 | 3 | 0 |
| `…12-40-26` | 6 | 15 615 | 1 118 | 3 | 0 |
| `…12-40-28` | 6 | 15 615 | 1 118 | 3 | 0 |
| `…12-40-34` | 6 | 15 615 | 1 118 | 3 | 0 |
| `…12-40-39` (newest) | 6 | **15 615** | 1 118 | 3 | 0 |

Newest block sizes: `[1471, 119, 10695, 238, 2000, 1092]`; `counts.chars = 38 308` (full prompt incl. 13 messages).

- **Total system text 15 615 chars ≈ 19.4 % of the old ~80 332** (≈ −80.6 %). ✅
- **Skills section 1 118 chars (< 2 K).** ✅ (`<available_skills>`: dom-memory, pubchem-database, test-driven-development)
- **available_skills = 3 ≤ 3 queued** numerically ✅ — but **identity mismatch**: the queue was `dom-memory, sqlite-migration, openalex-database`; the catalog holds `dom-memory, pubchem-database, test-driven-development`. `sqlite-migration` has no installed `SKILL.md` anywhere (`~/.config/opencode/skills`, `~/.claude/skills`, `.opencode/skills`), and `openalex-database` is installed but outside the static allow-list.
- The queue block is present **only in the first request**; τ-gate reports idle afterwards, so later steps get no queue line (by design, but worth knowing for multi-step sessions).
- `dom_tools: []` in the snapshot — the DOM MCP tool *descriptions* are not in the system prompt (Code Mode catalog), so they cost nothing there.

---

## 4. Verdict

**Full cycle does NOT work end-to-end in the live host today. Three of four links are proven; the cap→producer→permissions link is not.**

| link | status | evidence |
|---|---|---|
| cap → queue | ✅ PASS (unit + live MCP) | `dom_skill_select` and `queue_producer.py` return the same 3-skill queue, `missing_caps=[]` |
| queue → dynamic permissions | ⚠️ **NOT TRIGGERED LIVE** | 0 `[dom-memory] permissions` lines; empty live queue skips the branch. Ruleset logic 3/3 unit-pass; host honors rules (deny probe) |
| permissions → skill loadable | ✅ PASS (probe) | session deny → `Permission denied: skill`; control → `LOADED` |
| prompt reduction | ✅ PASS | system 15.6 K chars, skills 1.1 K / 3 ids, queue block 256 chars |

### What's missing (ordered by impact)

1. **The user-supplied `cap` never reaches the hook.** The producer reads only the plan brief. A prompt that asks for `cap:['sqlite','migration']` changes nothing unless the plan's `CURRENT` line carries `CAP: …`. Consequence in this run: `skill_search` injected, permissions skipped. Either the step must be passed to the producer/`dom_skill_select` from the request, or the demo must set the plan's `accept` envelope.
2. **F2 (plan `accept` → `CAP:`) is broken in the working tree.** `tests/check_brief_cap.py` fails with no `CURRENT` line after ingesting a plan with `accept`. This is precisely the missing upstream link from item 1, and it is uncommitted WIP.
3. **`sqlite-migration` is a phantom.** It is a `skills.card.json` entry (`state:"verified"`, evidence `check_migration_v5.py`) but no skill directory exists. The queue, the `use:` recommendation and the static allow rule all point at an id that cannot be loaded. Cards need an existence check against the installed index.
4. **`gaps` means two different things.** `dom_skill_select.gaps` = leftover index (146 ids); producer `gaps` = `missing_caps` (0). The block says `gaps: none` while the field lists 146 entries — the agent flagged this itself and it will confuse every consumer.
5. **Catalog is static per session.** `ctx.session.update` gates loading but does not re-filter `<available_skills>`. So "dynamic permissions → smaller prompt" is not true; only the static agent profile reduces the catalog. Docs §7.5/§7.6 should say "unlocks ids", not "changes the catalog".
6. **Stale test/assert channel.** `tests/check_skill_producer.mjs:146` captures `console.log`, plugin logs `console.warn` → false failure. (Delivery itself passes.)
7. **Docs §7.3 is stale.** It claims the `|` allow-rule yields 0 skills; the current dev config uses one rule per id and the live run correctly showed 3 allowed skills.

### Not missing / working well

- Selection quality is deterministic and explainable (5 independent signals, `confidence_bp=10000`, `missing_caps` empty).
- Prompt reduction is real and large: 15.6 K system chars, 1.1 K skill catalog, one 256-char queue block.
- `ctx.session.update` exists in this build; `instructions.entry` has `put`; graceful-degradation paths are unit-tested.

### Artifacts

- `C:\Temp\opencode\dom-cap-test1.stdout.txt` / `.stderr.txt`
- `C:\Temp\opencode\skillselect.json` (exact `dom_skill_select` payload)
- `C:\Temp\opencode\probeA/B.*` (deny vs control), `probe2..4.*`
- `C:\Temp\opencode\snapshot-analysis.txt`, `measure.txt`
- `C:\Temp\opencode\{run_producer.py,analyze_snapshot.py,inspect_skills.py,measure.py}`
- `C:\Temp\opencode\hostprobe\` (temp probe plugin)

TESTER_DONE. No repo edits.
