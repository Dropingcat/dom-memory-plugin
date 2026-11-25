# Step → skill selection: contract, rules and integration

**Status:** prototype, 2026-11-24. Code: `opencode/mockup/skill_router.py`, test:
`tests/check_skill_router.py` (artificial steps). Not wired to the plugin yet.
**Goal:** a deterministic router that turns a plan step into a small, explainable skill queue and
learns from outcomes — without a model, without a separate agent.

## What the router needs from planning

The plan block already carries most signals. One adapter reads them; no schema change is required.

| Router field | Source | Notes |
|---|---|---|
| `path`, `title`, `task_type`, `status` | `current_plan_tasks` | `task_type` is `phase` or `task` |
| `context` | header claim property `context` | the card text, written by `dom_plan_ingest` |
| `boundaries` | header claim property `boundaries` | already curated for П14/П15 |
| `claims` | `task_claims` joined to claim text | content types: code, data, decision, text |
| `hints_skills` | header property `skills` (new, optional) | force ids, e.g. `["python-test"]` |
| `hints_no_skills` | header property `no_skills` (optional) | ban ids for this step |

Why properties and not a new table: a step hint is a decision about the step, so it belongs on the
header claim, versioned and auditable. `dom_annotate(kind="property", name="skills", value=...)`
already writes them.

## Step prompting

A step is prompted in three parts, each with one job:

```text
STEP: П15.2 "L2 V5 migration: claim_sessions and read_receipts" [AO200 in_progress]
CONTEXT: V5 adds claim_sessions and read_receipts; v4 fixture migrates up and down.
BOUNDARIES: no new dependencies; claims table unchanged.
ACCEPT: migration test passes on a v4 fixture; rollback restores v4.
QUEUE: sqlite-migration@<digest>, dom-memory@<digest>; prefetch: python-test@<digest>
```

- `CONTEXT` and `BOUNDARIES` come from the header properties (already in `dom.notes`).
- `ACCEPT` is the step's `body` or a new `accept` property; it is what the outcome classifier checks
  when the step closes.
- `QUEUE` is the router's output; it is the only new line.

The step prompt is data, not instructions: it names the step, its limits and the queue. The model
decides how to execute; the router only decides what is one screen away.

## Skill record

```python
skill = {
  "id": "sqlite-migration",
  "description": "V5 migration, fixtures",
  "tags": ["migration", "ledger"],     # frontmatter metadata.opencode/tags, else derived
}
```

Tags are the join key between a step and a skill. Source order: frontmatter
`metadata.opencode/tags` → a small synonym map over the id and description. Tags are part of the
skill digest, so a tag change invalidates the queue.

## Rules

Keyword groups vote for tags (case-folded, substring match):

| Group | Words (excerpt) | Tags it satisfies |
|---|---|---|
| migration | migrat, миграц, schema, схем, v5, columns, таблиц | `migration` |
| testing | test, тест, check, провер, assert, pytest, node --test | `testing` |
| review | review, ревью, аудит, audit, diff, находк | `review` |
| docs | docs, докум, readme, spec, концепц | `docs` |
| prompt | prompt, промт, инъекц, inject, brief, notes | `prompt` |
| planning | plan, план, step, шаг, queue, очеред | `planning` |
| writing | report, отчёт, статья, article, текст, prose | `writing` |
| git | git, commit, коммит, branch, ветк, push | `git` |
| ledger | claim, клайм, ledger, appendix, hash, chain, sqlite | `ledger` |
| security | security, безопасн, gate, гейт, trust, session_source | `security` |

Rules are deliberately narrow. The first test caught `session` matching every `claim_sessions`
step; the security group now requires `session_source`, `trust` or `gate`.

## Score

```text
score = 0.30 * prior(task_type)
      + 0.40 * matched_groups / step_groups
      + 0.10 * claim_matched_groups / step_groups
      + 0.40 * weight(user, skill)
      + 1.50 if forced by plan hint
      - 0.60 if banned by plan hint

prior: phase 0.25, task 0.35
weight: 0..1, default 0.5
k: 3, deterministic tie-break by id
exploration: one step in ten (sha256(path)[0] % 10 == 0) takes rank 4 instead of rank 3
fallback: "skill_search" when the top score < 0.45
```

Every selected skill carries `why`: `prior(task)=0.35`, `rules=migration,ledger`,
`claims=migration`, `weight=0.50`, `forced by plan hint`, `exploration`.

## Queue output

```json
{
  "step": "П15.2",
  "current": [
    {"id": "sqlite-migration", "score": 0.605, "why": ["prior(task)=0.35", "rules=migration,ledger", "weight=0.50"], "tags": ["migration", "ledger"]},
    {"id": "security-gate", "score": 0.5717, "why": ["prior(task)=0.35", "rules=ledger,security", "weight=0.50"], "tags": ["security", "ledger"]}
  ],
  "confidence": 0.605,
  "fallback": null,
  "prefetch": ["python-test"],
  "next": "П15.9",
  "groups": {"migration": "migrat", "ledger": "claim"}
}
```

The entry injected into the session carries only `step`, `current` ids + digests, `prefetch` and
`confidence`; `why` stays in the panel and the ledger.

## Integration points (code)

| Where | Function | What it does |
|---|---|---|
| context hook | `skill_router.build_queue(step, next_step, skills, weights)` | compute on step change, cache per step |
| instructions entry | `dom.skill-queue` | replace on step advance, remove on step close |
| prompt hook | `prompt.skills` | attach the first-step ids (core validates them) |
| tool hook `execute.after` | `outcomes.record(skill_id, status)` | hit/failure/error/miss → weights |
| DOM | `dom_annotate` property `skill_queue` | audit: step, ids, scores, digests |
| DOM | claim edges `step_type → skill` | learned map with evidence counts |
| panel | `GET /api/queue` | reads `skill_queue` properties, never instructions |

New code needed:

- `skill_router.py` (done, pure) — scoring and selection;
- `skill_adapters.py` — plan/claims/registry → router inputs (DB reads, tag derivation);
- `skill_queue.py` — cache per step, digest computation, entry rendering;
- `outcomes.py` — classify `execute.after`, update weights, emit claims;
- `index.js` — call the queue on context, replace the entry on step advance, attach `prompt.skills`;
- tests: `check_skill_router.py` (done), `check_skill_adapters.py`, `check_queue_entry.py`,
  `check_outcomes.py`.

## Artificial steps used by the test

| Step | Text | Top pick |
|---|---|---|
| П15.2 | V5 migration, claim_sessions, read_receipts | `sqlite-migration` |
| П15.4 | trusted session channel, session_source, gate | `security-gate` |
| П15.9 | run the test suite, pytest, node --test | `python-test` |
| П16.4 | concept docs, readme, spec | `docs-md`, `writer` |
| П16.9 | unrelated work, no keywords | fallback `skill_search` |
| П16.5 | forced `panel`, banned `dom-memory` | `panel` only |

## Live plan result (2026-11-25) — capability tags are mandatory

The selector was run on the real plan (П15.1–П15.3) against the real 166-skill registry, with tags
derived from skill descriptions:

| Step | Top picks (score) | Verdict |
|---|---|---|
| П15.1 WriteContext, session, gate | ariz-contradiction-resolution, dom-memory, scientific-problem-selection (0.572) | wrong: no skill provides sessions/gate |
| П15.2 V5 migration | ariz-contradiction-resolution, compare, dom-memory (0.505) | wrong: no migration skill exists |
| П15.3 same-transaction receipts | verification-before-completion (0.705) | accidental: "commit" matched `git` |

Diagnosis: the selector is not the failure. Description text is marketing prose; 94 of 166 skills
picked up some tag, but none of them provides `sqlite`, `migration` or `security`. Rule matching on
free text cannot recover a capability that is not in the catalog.

Fix — the capability contract:

```text
step header property   needs:    ["sqlite", "migration", "ledger"]
skill frontmatter      metadata.opencode/provides: ["sqlite", "migration"]
```

Score with needs: `0.90 * coverage + 0.15 * prior + 0.30 * weight`. Skills with zero coverage are
not emitted at all; if nothing covers a need, the queue is empty and `fallback=skill_search`.
Measured after the change: П15.1 and П15.2 return `current=[]`, `fallback=skill_search` instead of
alphabetical decoys.

Implication: 0.3.1 needs a small curated capability layer (10–20 skills with `provides`), not 166
tags sprayed over descriptions.

## Acceptance and the advance rule

A step closes only when all four hold:

1. `accept` on the step header: `[{check, evidence}]`, e.g.
   `{"check": "tests/check_migration_v5.py", "evidence": "exit 0"}` or
   `{"check": "claim", "evidence": "<claim id>"}`.
2. Every evidence item exists and passes (command exit 0, artifact digest, linked claim).
3. An `implements` claim and a `validates` claim are linked to the step.
4. The plan status is written `done`.

On success: the next step's queue replaces the instruction entry in the same plugin call, and one
transition claim records `{from, to, queue_digest, outcome, accept}`. On failure: stay on the step,
record `failure` (not `error`) with the missing checks, and requeue with the failed checks first.

No learning from steps that were abandoned or replanned: their outcome is `unknown`, not `failure`.

## Learning structure

The capability graph is the primary structure; weights are the secondary one.

| Layer | Content | Storage | Update |
|---|---|---|---|
| 0 | `needs → provides` capability graph | plan properties + skill frontmatter | curated, versioned |
| 1 | `(step_type, capability, skill)` counts | claims with evidence | per outcome |
| 2 | `(user, skill)` weight | `ctx.storage` + ledger snapshot | per outcome, decay ×0.98/10, reset /100 |
| 3 | skill description patches | claim chain propose→validate→accept | after 3 errors on one skill |

Signals: hit (queued and loaded), failure (queued, unused at step close), error (tool error after a
load), miss (loaded outside the queue), accept (the step's checks passed). The classifier reads tool
status and the acceptance result; it never asks a model.

Shadow mode before injection: compute the queue for 20–30 steps, write it to the ledger (or the
journal), and compare predicted ids with the skills actually loaded. No behavior change while the
hit rate is unknown. Enable the entry once hit ≥ 0.7 and miss ≤ 0.2 over a window of 10 steps.

Guardrails: per-step cap 3, exploration 10% by path hash, stale weights (older plan version)
ignored, fallback when coverage < 1, and no queue at all when the step has no `needs` and no rules.

## Guards and failure modes

- No plan or no current step → no queue, `dom.notes` alone.
- Empty registry → `fallback=skill_search`, confidence 0.
- A step with no keywords → prior + weights only; confidence usually below the fallback threshold.
- Exploration must not fire on every step; the hash rule makes it stable per path.
- Weights older than the step version (`linked_at_plan_version` < step version) are stale: ignore
  them and re-learn.
- Hints are decisions: a forced skill with a bad outcome becomes an `error` against the hint, not
  against the router.

## Open decisions

1. Capability vocabulary: who authors `needs` — the planner at step creation, or the router as a
   proposal claim reviewed by the owner? Start with the planner; the router may suggest.
2. Vocabulary size: keep it small (10–20 capabilities: sqlite, migration, testing, review, docs,
   prompt, planning, security, ledger, git). A large vocabulary becomes a second skill catalog.
3. `k`: 3 is the current cap; 2 may be better for narrow steps, 4 for review steps.
4. Exploration: 10% is a guess; measure miss rate before tuning.
5. Step scope: the router runs per plan step; sub-steps inside one turn are not steps. Confirm with
   the first shadow run.
6. Missing provider: when `needs` has no provider, the queue is empty and `skill_search` fires. Who
   turns that into a new skill or a new capability — the owner, not the router.

