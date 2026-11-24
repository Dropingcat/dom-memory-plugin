# Prompt block layout and the sensing contract — 0.3.1

**Status:** design, measured on a live snapshot (2026-11-24T19:49:29Z, agent `build`,
session `ses_e29a4263…`, plan r22). This file answers: where a block lives, through which
channel it changes, how blocks sense each other, how often, and how the heuristics learn.

## Measured layout

System prompt: 86 361 chars in six parts. Request payload: system 86 361 + tools 13 055 (13) +
messages 9 577 (50) ≈ 108 993.

| Part | Source key | Chars | Share of system | Owner | Can we change it? |
|---|---|---|---|---|---|
| 0 | core/system | 1 471 | 1.7% | core | no |
| 1 | core/model | 159 | 0.2% | core | no |
| 2a | core/codemode | 8 901 | 10.3% | core | no |
| 2b | core/mcp-guidance | 1 106 | 1.3% | core | no |
| 2c | **core/skill-guidance** | **69 276** | **80.2%** | core registry | registry: `ctx.skill.transform`; text: no |
| 2d | core/date | 29 | 0.0% | core | no |
| 2e | core/environment | 389 | 0.5% | core | no |
| 3 | core/worktree | 238 | 0.3% | core | no |
| 4 | plugin/harness-superpowers-planning | 2 000 | 2.3% | harness plugin | that plugin |
| 5 | **dom** | **2 774** | **3.2%** | DOM plugin | yes (budget 3 400) |

The tool schemas (13 055) live outside the system text. DOM's own catalog is 842 chars inside
`core/codemode` ("29 tools, 2 shown" + `search`), so DOM cannot meaningfully slim the tool list.

**Conclusion:** the prompt is not "tools-heavy", it is **registry-heavy**. 80% of the system prompt is
the skill catalog, rendered by core from every skill at the location. Any plan-aware slimming has to
act on that registry or on that block, not on DOM's 2 774 chars.

## Placement rules

A block is placed by **lifetime and change rate**, not by topic.

| Block | Where | Channel | Cadence | Budget | Writer | Readers |
|---|---|---|---|---|---|---|
| Harness rules | core part 0 | core | static | — | core | model |
| Model identity | core part 1 | core | per model | — | core | model |
| Tool catalog | tools map | `ctx.tool.transform` | registry reload | 13k | core + plugins | model |
| Skill catalog | core part 2c | `ctx.skill.transform` | registry reload | 69k | core + plugins | model |
| Planning policy | part 4 | harness plugin | static | 2k | harness | model |
| Plan brief | part 5 (`dom.notes`) | `session.hook("context")` | every model call (cached per turn) | 3.4k | DOM hook | model |
| Skill queue | session instructions | `instructions.entry.put/remove` key `dom.skill-queue` | on plan-step advance | ≤800 | DOM plugin (router) | model |
| Step outcome | ledger claim property | `tool.hook("execute.after")` | per tool call → property per step | — | DOM plugin | router, panel |
| Weights | `ctx.storage` + claim property | plugin storage, snapshot to ledger | hot: per outcome; ledger: per 10 | — | router | router, panel |
| Panel | DOM API | read-only projection | on demand | — | monitor | user |

Rules that follow from the measurement:

1. **Never duplicate the skill catalog.** The queue names ids and digests; the agent loads the body
   with the `skill` tool. Pasting skill text into the prompt multiplies the 69k.
2. **The brief does not grow.** New per-step state goes to the queue entry, not to `dom.notes`;
   the 3 400 cap stays the contract.
3. **Tool filtering is a core-level lever.** A `context` hook may `delete event.tools[x]` per request,
   but DOM tools sit behind codemode, so this is a 0.4 experiment, not a 0.3.1 win.
4. **Registry edits are location-wide.** `ctx.skill.transform` removes or adds a skill for every
   session at that location; it is not a per-step filter. Use it for real de-registration, not routing.

## Target layout for 0.3.1

```
part 0-4   core + harness                      unchanged
part 5     dom brief (plan, anchors, sources)  unchanged, 3.4k cap
entry      <context key="dom.skill-queue">      NEW, step-bound, <=800 chars
ledger     queue transitions + outcomes         NEW claims/properties, no new table
storage    dom/weights                          hot per-user weights
```

The queue entry (draft):

```text
step: П15.2 (L2 V5 migration)   sufficiency: 0.91
current: sqlite-migration@sha256:ab12, dom-memory@sha256:cd34
prefetch: python-test@sha256:ef56
fallback: skill_search (coverage 0.91 < 0.9? no)
last: success 4 / error 1 / failure 0 / miss 0
```

## Sensing contract

Blocks never parse each other's text. They exchange **ids, digests and counters** through DOM.

```
 router ──writes──► queue claim (step, ids, digests, sufficiency)
    │                     │
    │                     ├──► instructions.entry (rendered by host)
    │                     └──► panel (/api/queue)
    │
    ├──tool.hook(execute.before)──► intent: tool, args.skill, session
    ├──tool.hook(execute.after)───► outcome: status, skill, duration
    └──prompt/context hooks───────► turn start, model call, brief cache

 brief ──reads──► root/header properties, plan, claims, SOURCES
 queue ──reads──► plan step, registry digests, weights
 panel ──reads──► claims + projections only (never instructions, never snapshots)
```

Signals and their meaning:

| Signal | Trigger | Meaning | Effect |
|---|---|---|---|
| `prompt` | new user turn | turn start | seed queue for step 1, attach `prompt.skills` |
| `context` | each model call | the model is about to act | re-inject brief; check queue freshness |
| plan step done | DOM write (`plan_status`/claim) | the router advances | replace entry, write transition property |
| `execute.before` | tool call | intent | gate check (advisory), remember requested skill |
| `execute.after` | tool result | outcome | classify, update weights, count misses |
| digest mismatch | skill content changed | registry drift | refresh index, re-emit queue |
| goal changed | root property / plan revision | new goal | full queue reload, keep weights |

The only coupling between blocks is **the digest**: the queue carries
`sha256(skill.content)`, so a stale entry is detectable without reading the skill body.

## Intensity (how often each block moves)

| Layer | Per user turn | Per model call | Per plan step | Per tool call | Per 10 |
|---|---|---|---|---|---|
| brief | generate 1 | re-inject cached | — | — | — |
| queue entry | seed | check freshness | replace 1 | — | — |
| transitions | — | — | 1 property event | — | — |
| outcomes | — | — | 1 aggregated property | classify 1 | — |
| weights | — | — | — | update 1 | snapshot 1 |
| panel | — | — | — | — | refresh view |

Cost guard: the queue entry is replaced at most once per plan step, never per model call. If the
host's step boundary lags, replace right after the `execute.after` that closes the step — still one
write per step.

## Learning the heuristics

The classifier is deterministic; learning is a weight update plus a gated registry patch.

1. **Outcome classification** (no model): `success` = tool completed and the step check passed;
   `error` = tool error or a failed validation; `failure` = the skill was injected but never called;
   `queue_miss` = a skill was requested that was not in the queue.
2. **Weights** per user and skill: `success +0.05`, `error −0.10`, `failure −0.05`, `miss 0`;
   clip 0..1; decay ×0.98 every 10 steps; prior 0.5; full reset to prior every 100 steps
   (overfitting guard for a single user).
3. **Sufficiency** per step: `covered_subtasks / total_subtasks × successful_calls / (injected × iterations)`;
   the router adds `skill_search` only when coverage < 0.9.
4. **Description patch**: after 3 `error` outcomes on the same skill, propose a new description/body as
   a claim (`propose → validate → accept`, the existing distillate chain). Never auto-accept; the owner
   reviews. The patch records the evidence claim ids.
5. **Promotion**: a skill that causes `queue_miss` twice becomes a session skill; three times, a default
   skill. Demotion is the reverse with `failure`.
6. **Metrics to watch**: miss rate < 20%, sufficiency > 0.9, entry churn ≤ 1 per step, brief ≤ 3 400.

All learning writes land in DOM as claims/properties, so a later audit can replay why a skill was
promoted. Weights in `ctx.storage` are hot state; the ledger snapshot is the truth of record.

## E4 result (2026-11-24) — catalog filtering is not available

Run as a measurement, not an optimisation.

| Step | Context | `core/skill-guidance` | Skills | Probe |
|---|---|---|---|---|
| baseline | desktop session | 69 276 | 166 | no |
| probe added | fresh session | 75 094 | 167 | yes |
| probe added, `brainstorming` removed | fresh session | 74 770 | 166 | yes |
| probe and plugin removed, after restart | fresh CLI session | 68 980 | 165 | no |

Findings:

1. **One skill costs ~5.8k chars.** A 6 013-char description moved the block from 69 276 to 75 094
   (+5 818, ~97% of the description). A long-lived session keeps its old block: the new skill arrives
   as a delta reminder, not by rebuilding the catalog.
2. **`ctx.skill.transform` cannot see the config skills.** `ctx.skill.list()` returns 17 skills
   (bundled `opencode`, `report`, and the external superpowers set) at every location; the 165
   config-discovered skills, including the probe, are outside its view. A `location` argument does
   not change the count.
3. **The transform works only inside that 17.** Removing `brainstorming` (visible) cut the catalog by
   324 chars (75 094 → 74 770), dropped the registry 17 → 16, and the live session was told the skill
   is unavailable. The probe could not be removed because it is not in scope.
4. **Restore is clean.** Deleting the plugin left the registration active until the service restart;
   after the restart a fresh session is back to 68 980 and the registry to 166 (`brainstorming`
   present, probe gone).

Caveats: CLI sessions omit the `browser` skill (−296 vs a desktop session), so only same-context
runs are comparable; provider cache/cost was not measured (needs usage capture, not request text).

**Verdict: E4 fails for the 69k catalog.** The block is core-owned and the plugin API cannot address
its 165 config skills. `ctx.skill.transform` is a registry tool for the plugin-visible set, not a
per-plan filter.

Consequence for 0.3.1: stay safe. Queue entry (ids + digests), outcomes, weights, panel. No
level-wide transform, no attempt to filter the catalog. Shrinking `core/skill-guidance` becomes a
0.4 item that needs a core-level skill filter (or a supported transform for the full registry).

## Experiments before enabling by default

| # | Question | Measure | Pass |
|---|---|---|---|
| E1 | Does `instructions.entry` render at the next step boundary? | entry visible in the next request, removed after | yes/no |
| E2 | Does per-step replacement break provider caching? | two sequential calls with/without replacement | cost delta < 10% |
| E3 | Is the queue entry under 800 chars at 4 skills? | length + readability | ≤ 800 |
| E4 | Does `ctx.skill.transform` removal actually shrink `core/skill-guidance`? | part 2c chars before/after | **fail** for the 69k catalog — see the E4 result; only 17 plugin-visible skills |
| E5 | Does `delete event.tools[x]` reduce the tools map? | tools chars per request | −20% |

E1–E3 belong to П16.3. E4–E5 are 0.4 candidates.

## Non-goals for 0.3.1

- Rewriting the core skill catalog per step (needs core support or E4 evidence).
- Pasting skill bodies into the prompt.
- A model-based router or a model-based outcome classifier.
- A second state store next to the ledger.
