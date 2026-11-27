# Skill selection and injection — architecture proposal (0.3.1)

**Date:** 2026-11-26
**Author:** architecture subagent (read-only)
**Scope:** automated per-step skill selection for the DOM Memory plugin
**Verdict:** build a new, staged *cascade* beside `skill_router.py`, not inside it. Capability
graph is the authority; lexical retrieval is the recall engine; LLM and embeddings are optional
shadow channels. The pipeline is read-only on the hot path and writes only through the hooks.

---

## 1. Executive summary

The current router is a single scoring function whose `needs` branch short-circuits everything
(`skill_router.py:150-157`) and whose non-`needs` branch is gated off because no live step has
`accept` (`skill_router.py:73-76`; audit C5). The user's vision — several passes, code + LLM,
hierarchical and partly parallel — is therefore not an extension of `score()`; it is a second,
larger component that *consumes* the router's pure primitives. The proposal is a seven-stage
cascade `S0 inventory → S1 distill → S2 recall (4 channels) → S3 filter → S4 deep analysis →
S5 minimal-cover selection → S6 inject → S7 outcomes`, run over a four-step window
(current + next 3) and budgeted coarse-to-fine: full depth only for the current step, exact-join
only for steps 2–4. Deterministic Python (stdlib + PyYAML) does S0–S3, S5 and all rendering;
a char 3–5-gram BM25 provides the Russian-tolerant lexical baseline the audit asks for (C6/N10);
`dom_distillate` / `dom_extract(provider="client")` are used only for one-time card curation, never
in the hot path; one optional LLM call (Qwen3.5-9B via a `command` provider) may adjudicate the
top-5 of the current step and is off by default because of egress (C11). The architectural
invariant is that **a missing provider stays a visible gap, never a cosine-similarity decoy**: the
pipeline emits `gap + skill_search`, as the capability contract already does (`skill_router.py:188-200`).

---

## 2. Constraints → design response

| # | Constraint (source) | Design response |
|---|---|---|
| C1 | `score()` early-returns on `needs`; `select()` drops zero coverage (`skill_router.py:150-157,188-189`) | New `skill_select.select_pipeline()`. `skill_router.py` is imported for `capabilities_from_accept`, `normalize_step`, `rule_groups`, `PRIORS`, `CAPABILITY_VERSION` only. No edit to `score()`/`select()`; `check_skill_router.py` must keep passing unchanged. |
| C2 | Catalog has no provider for `sqlite`/`migration`/`security` (`rag-audit-report.md` C2) | Stage S3 has a first-class `gap` verdict and S5 returns `fallback=skill_search` + a `gap` claim candidate. Semantic channels can only *rank*, never *admit*, a candidate into authoritative mode. |
| C3 | Four decisions forbid description-based routing (C3) | `needs → provides` remains layer 0. Lexical/dense channels fill a bounded `≤8` candidate set; they never satisfy a need by themselves. |
| C4 | `ctx.skill.list()` is JS-only and sees ~17/166 (C4, E4) | Filesystem collector over documented roots (134 + 14 measured). Optional `index.js` snapshot as an overlay, never required. |
| C5 | No live step has `accept` (C5) | Dual mode: `needs_source=accept` → authoritative queue; `needs_source=inferred` → **advisory only** (ledger + panel, not injected by default). Guarantee preserved, vision still measurable. |
| C6 | 80 MB model is English-only; steps are RU+EN (C6) | Char 3–5-gram TF-IDF/BM25 in stdlib (audit N10). A neural model is a deferred, pinned, optional channel. |
| C7 | Cosine scores break the `why` contract and reproducibility (C7) | Deterministic channel order, explicit integer/lexical `why` terms, `score_bp` integers, `sort_keys=True`. Any semantic channel records `model_id` + `index_digest`; without it the queue is bit-reproducible. |
| C8 | Floats rejected in claims; claims immutable (C8) | Vectors and scores live in a derived JSON cache under the run dir (`.dom-memory/` is gitignored, `.gitignore:7`). Only digests and basis points reach claims. |
| C9 | `run_tests.py` is local, no network, no optional deps (`run_tests.py:23-70`; `pyproject.toml` deps = mcp/anyio/PyYAML) | Baseline is stdlib + PyYAML. Heavy/optional code lives in `tools/`, imported lazily, never by `tests/check_*`. |
| C10 | 0.3.1 scope is portability + cards + accept + shadow (C10) | This design is the *selection* half; it ships after 0.3.0 closes and reuses the 0.3.1 card/accept/shadow items instead of adding a new program. |
| C11 | Prompt/step text leaving the machine is a privacy risk (C11) | LLM stage off by default, one allowlisted endpoint, redaction of claims/paths, and a `provider=client` alternative that needs no egress. |
| C12 | Missing half is `capability → skill`, not ranking (C12) | `skills.card.json` with curated `provides` is a Stage-0 deliverable; `unverified` cards are excluded from authoritative routing. |

---

## 3. Pipeline architecture

### 3.1 Diagram

```
                 plan DB (ro)            filesystem skills (150)          goal claim
                      │                          │                             │
        ┌─────────────▼──────────────────────────▼─────────────────────────────▼─────┐
        │ S0  INVENTORY & CONTEXT                    deterministic · Python · no writes│
        │  StepWindow[current,+1,+2,+3]      SkillIndex[cards: id, digest, fields]     │
        │  SkillCollector (roots, precedence, frontmatter)   fingerprint cache         │
        └─────────────┬──────────────────────────────────────────────────────────────┘
                      │ QueryCard
        ┌─────────────▼──────────────────────────────────────────────────────────────┐
        │ S1  DISTILL                                deterministic · Python           │
        │  step text → rules/terms/needs(authoritative|inferred)                       │
        │  promptizer.distill + plan_context tokens + capability lexicon               │
        │  coverage matrix: "what we have" × "what steps 1..4 need"                    │
        └──────┬───────────────┬───────────────────┬────────────────────┬────────────┘
               │               │                   │                    │
      ┌────────▼──────┐ ┌──────▼────────┐ ┌────────▼────────┐ ┌─────────▼─────────┐
      │ S2a EXACT     │ │ S2b LEXICAL   │ │ S2c AFFINITY    │ │ S2d DENSE (opt)   │
      │ needs∩provides│ │ BM25 char 3-5 │ │ weights,        │ │ embeddings,       │
      │ authoritative │ │ field-aware   │ │ prefetch, miss, │ │ shadow only,      │
      │ gate          │ │ RU+EN, stdlib │ │ hints, history  │ │ derived cache     │
      └────────┬──────┘ └──────┬────────┘ └────────┬────────┘ └─────────┬─────────┘
               └───────────────┴───────────┬───────┴────────────────────┘
                                           │  union + RRF
        ┌──────────────────────────────────▼─────────────────────────────────────────┐
        │ S3  FILTER                       deterministic · Python                    │
        │  dedupe by id · drop banned · drop stale digest · drop unverified in        │
        │  authoritative mode · zero-coverage → GAP list · cap 8 candidates           │
        └──────────────────────────────────┬─────────────────────────────────────────┘
                                           │ ≤8
        ┌──────────────────────────────────▼─────────────────────────────────────────┐
        │ S4  DEEP ANALYSIS                                                          │
        │  S4a rerank: 0.55·cover + 0.25·lex + 0.10·affinity + 0.05·prior + 0.05·hyst│
        │  S4b adjudicate top-5 (OPTIONAL LLM, off by default, structured JSON)       │
        │  S4c distill card: what / why / how (≤120 chars each, deterministic)        │
        └──────────────────────────────────┬─────────────────────────────────────────┘
                                           │
        ┌──────────────────────────────────▼─────────────────────────────────────────┐
        │ S5  SELECT & PACK                deterministic · Python                    │
        │  greedy minimal cover over needs · k≤3 · hysteresis · confidence ·          │
        │  fallback=skill_search when cover<1 or top<0.45                             │
        └───────┬──────────────────────┬───────────────────────────┬─────────────────┘
                │                      │                           │
      ┌─────────▼──────────┐  ┌────────▼───────────┐      ┌────────▼──────────┐
      │ S6a INJECT         │  │ S6b SHADOW CARD    │      │ S6c PANEL         │
      │ entry ≤800 chars   │  │ claim: ints/strings│      │ /api/queue        │
      │ ids+digest+why     │  │ fit, source, bp    │      │ read-only         │
      └─────────┬──────────┘  └────────┬───────────┘      └───────────────────┘
                │                      │
        ┌───────▼──────────────────────▼─────────────────────────────────────────────┐
        │ S7  OUTCOMES                  tool.hook(execute.after) · JS → Python       │
        │  hit | miss | error | excess | failure | unknown → weights, card state     │
        └───────┬────────────────────────────────────────────────────────────────────┘
                └──────────────► S2c affinity / S4a weight term (next step)
```

### 3.2 The four passes the user asked for, mapped

| User phrase | Stage | Implementation |
|---|---|---|
| "a script with a queue of steps and their calculations" | S0 + S1 | `StepWindow` over `current_plan_tasks`; per-step need calculation; coverage matrix |
| "extraction: what we have and what we might need" | S1 | `have` = cards/providers + loaded-this-session; `might_need` = accept∪inferred needs + prefetch reuse |
| "keyword search + semantic analysis" | S2b / S2d + S4a | char-ngram BM25; dense is optional; field-aware rerank is the semantic-ish pass that ships |
| "first-level filtering → deeper analysis of filtered → final list" | S3 → S4 → S5 | hard filter → rerank/adjudicate/distill → minimal cover + explanation |
| "multi-stage and hierarchical, sequential and parallel" | whole | coarse-to-fine over the window; parallel *across steps*, sequential *within a step* (§3.4) |
| "where embeddings fit" | S2d only | shadow recall channel, derived cache, pinned model; never authoritative |

### 3.3 Why this is not "RAG"

RAG ranks documents by similarity to a query. Here the *authority* is an exact typed relation
(`needs → provides`) and the query is a distilled capability statement, not raw prose. Retrieval
exists only to (a) find lexical neighbours when `needs` is inferred, and (b) order candidates that
already passed the capability gate. This is the SkillDreamer shape (infer required capabilities,
then retrieve), which the audit found to be the correct one (`rag-audit-report.md` §3.2), and it
keeps the gap visible, which embeddings cannot.

### 3.4 Parallelism and budget (explicit)

| Axis | Policy | Reason |
|---|---|---|
| Stages S0→S6 | **sequential** | later stages consume earlier outputs; determinism |
| Channels S2a–S2d | **sequential, fixed order** | 150 docs × ~4k terms is single-digit ms; merging order is part of reproducibility |
| Steps in the window | **parallel** (`ThreadPoolExecutor`, ≤4) | independent; only I/O and CPU on separate cards; no shared mutable state |
| Optional LLM adjudication | **parallel fan-out ≤5, one call batched** | one request with all 5 candidates is cheaper and more consistent than 5 calls |
| Prefetch depth | **budgeted** | depth 0 = full; depth 1 = S1+S2a+S2b+S3; depth 2–3 = S1 + exact join only |
| Hard budget | index build ≤120 ms (cached), per-step select ≤40 ms, hook total ≤300 ms | hook runs inside `spawnSync` (`index.js:237-250`), 60 s timeout but UX-bound |

---

## 4. Stage-by-stage design

### S0 — Inventory and context

**Input:** run DB path, project dir, plan step id (or "auto"), session id.
**Output:** `StepWindow` + `SkillIndex` handle.

- `StepWindow = [step, next, next2, next3]`. The current step is `in_progress` else the same
  `_choose_current` rule the brief already uses (`brief.py:43-52`). Next steps come from
  `current_plan_tasks` ordered by `path`; a child of the current step is preferred over a sibling.
- `SkillIndex` is built from filesystem roots in precedence order (later wins by id):
  1. `~/.claude/skills/**/SKILL.md`
  2. `~/.config/opencode/skills/**/SKILL.md`
  3. `<project>/.claude/skills`, `<project>/.opencode/skills`, `<project>/.agents/skills`
  4. plugin `opencode/skills`, `claude/skills`
  Measured: 134 + 14 + 2 = 150 files (audit N3). Bundled/HTTP skills are missing; an optional
  JS snapshot (N4) is merged as `source=snapshot` without changing ids.
- Per card: `id` (frontmatter `name` else dir name, casefolded for comparison), `path`, `digest =
  sha256(file bytes)`, `description`, `tags`, `provides`, `state`, `fields` (name/desc/body tokens).
- **`provides` is never derived from the description.** Order: `skills.card.json` → frontmatter
  `metadata.opencode/provides` → `[]`. This is the C2/C12 fix and the reason the pipeline cannot
  invent a migration skill.
- Cache key `fingerprint = sha256(sorted(path, mtime_ns, size) + card_digest + CAPABILITY_VERSION)`
  → `<runDir>/skill-cache/index-<fingerprint>.json`. `.dom-memory/` is gitignored
  (`dom-wu-usage-analysis.md:188-190`), so the cache can never be committed.
- **Read-only:** open the DB with `file:...?mode=ro` (same practice as the audit and as the gate
  query in `index.js:58-65`). The router never writes; S6/S7 writes go through the hook process.

**Code/LLM role:** 100 % Python, no model.

### S1 — Distillation into a QueryCard

**Input:** `StepWindow`, header properties (`context`, `boundaries`, `accept`, `needs`, `skills`,
`no_skills`), linked claims, goal claim, weights, recent outcomes.
**Output:** `QueryCard` (bounded ≤2 KB in memory; only the distilled ≤600-char text reaches the prompt).

Two distillations, both deterministic, reusing existing primitives:

1. **Step distillation.** `promptizer.distill()` (`promptizer.py:99-118`) cuts the step context on a
   sentence boundary preferring markers; `promptizer.clause()` (`:121-132`) extracts
   `Сейчас:`/`Зачем:` clauses. Claim text is folded in through `planning._claim_text`, the same
   path `promptizer.linked_claim` uses (`:135-147`).
2. **Need distillation.**
   - `needs.authoritative = capabilities_from_accept(accept)` (`skill_router.py:53-68`) —
     zero guessing, `unmapped` reported.
   - `needs.inferred = lexicon(text) ∪ rule_groups(text)` where `lexicon` maps surfaces
     (`v5`, `claim_sessions`, `миграц`) to the closed 14-capability dictionary
     (`capabilities.json:3-18`); `rule_groups` is the existing narrow rule table
     (`skill_router.py:22-33`). Every inferred need carries its evidence token.
   - `needs_source ∈ {accept, inferred, none}` decides the mode (§5.2).

`QueryCard` shape:

```python
{
 "step":  {"path","title","task_type","status","branch","plan_version"},
 "window":["П1.2","П1.3","П1.4","П1.5"],
 "goal":  {"target","constraints","version"},
 "needs": {"authoritative":[...], "inferred":[{"cap","evidence"}], "source":"accept|inferred|none",
           "unmapped":[]},
 "text":  {"distilled":"<=600 chars", "terms":[...], "rules":{"migration":"migrat"}},
 "bans":["dom-memory"], "forces":["python-test"],
 "history":{"weights":{"id":0.62}, "recent_misses":[], "prev_queue":[]},
 "budget":{"k":3,"entry_chars":800,"deep":5}
}
```

The **coverage matrix** produced here is the "what we have vs what we might need" artifact:

```text
step    needs                      providers (cards)          verdict
П1.2    sqlite,migration,ledger    dom-memory[ledger]        GAP sqlite,migration
П1.3    testing                    test-driven-development    covered
П1.4    docs                       writing-plans, sources     covered(2)
```

It is cached per `(plan_version, step)` and rendered into the shadow card, not the prompt.

**Code/LLM role:** Python only. `dom_distillate` is *not* called per step — it is the reviewed
patch path for cards (§4.0) and would add a write to the hot path.

### S2 — Candidate generation (recall, 4 channels)

**Input:** `QueryCard`, `SkillIndex`, weights.
**Output:** `Candidate{id, digest, channels[], raw{}}`, at most ~30 before S3.

| Channel | Algorithm | Role | Output |
|---|---|---|---|
| **S2a exact** | `cover = |needs ∩ provides| / |needs|`; only cards with `cover>0` | authority; guarantees no decoy | `candidates` + `gap` for uncovered needs |
| **S2b lexical** | char 3–5-gram + word BM25 over 4 fields (name ×2.0, tags ×1.5, description ×1.0, body ×0.3); `k1=1.5,b=0.75`; IDF from the 150-doc corpus | recall; RU inflection (`миграция/миграции`) + code identifiers (`claim_sessions`) | top 15 |
| **S2c affinity** | `weight(user,skill)` from the snapshot (0..1), `+0.15` if in previous queue, `+0.10` if it was a recent miss, `+1.50` forced, `-0.60` banned — same constants as the router (`skill_router.py:39-41`) | personalisation + hint honouring | top 5 |
| **S2d dense** *(optional)* | cosine over cached vectors, model pinned (`bge-m3` or Yandex query/doc — RU-native; audit §3.1) | **shadow only**; never admits a candidate | top 10 |

Fusion: union by id, keep per-channel ranks, compute reciprocal-rank sum
`rr = Σ 1/(60+rank_c)`. RRF is used only for ordering inside S3–S4; the authority of S2a is kept
as a separate boolean (`via_exact`), so a lexical hit can never outrank a coverage violation.

**Code/LLM role:** Python. No model in S2a–S2c. S2d needs an embedding backend and is a separate
optional module (`skill_dense.py`) activated by config, exercised only by `tools/skill_bench.py`.

### S3 — First-level filtering (deterministic)

Rules, in order:

1. drop ids in `bans`;
2. drop cards whose digest ≠ index digest (stale entry, `prompt-block-layout.md:108-112`);
3. in **authoritative** mode drop `state != verified` (amendment: `unverified → shadow → verified →
   deprecated`, `step-skill-selection.md:319-321`);
4. in **authoritative** mode drop `cover == 0` when `needs` is non-empty — exactly the guard the
   router already applies (`skill_router.py:188-189`);
5. keep forced ids even if they fail 3–4 (explicit decision wins, and a bad outcome is charged to
   the hint, not the router — `step-skill-selection.md:352-354`);
6. dedupe by id, sort by `(-cover, -rr, id)`, cap 8.

Uncovered needs are **not** dropped silently: they become `gaps[]` with the evidence and are
rendered as `gap: sqlite (no provider) → skill_search`. This is the single most important
behaviour inherited from the capability contract.

### S4 — Deep analysis

**S4a — deterministic rerank (always).**

```text
base = 0.55*cover_norm + 0.25*lex_norm + 0.10*affinity + 0.05*prior(task_type) + 0.05*hysteresis
bonuses: +1.50 forced, -0.60 banned
penalty: -0.20 unverified (advisory mode only; authoritative already filtered)
tie-break: (-score, id)
```

`cover_norm` = 1.0 in inferred mode (no authoritative denominator) replaced by `lex_norm`;
`prior` reuses `PRIORS` (`skill_router.py:16-19`). `hysteresis` = 1 if the skill was in the previous
queue for this step and the coverage delta ≤ 0.15 (amendment 3, `step-skill-selection.md:337-339`);
this is what prevents the queue from oscillating between two near-equal skills and keeps churn
≤ 1 per step.

**S4b — optional LLM adjudication (off by default).**

- Input: at most **5** candidates, each as `{id, name, description ≤400 chars, provides, distilled
  why}`; plus the distilled step (≤600 chars) and the goal.
- Output (strict JSON, schema-validated): `[{id, fit: "yes"|"no"|"unknown", why ≤120,
  evidence: ["description"|"body"|"capability"], risk ≤120}]`.
- Model: Qwen3.5-9B via Polza through the existing `command` provider (`llm.py:173-191`) with a
  ~60-line stdlib `urllib` bridge in `tools/polza_llm.py` — no new runtime dependency, no SDK.
  `provider=client` (`llm.py:194-201`) is the no-egress alternative: the agent answers a
  `SKILL_ADJUDICATION_REQUEST`; never used in the hot path by default.
- Rules: no floats in output claims (`score_bp` ints); `fit=no` can only demote, never promote above
  a `fit=unknown`; `unknown` is a legal and common answer (SRA caveat: always-top-3 can hurt,
  `rag-audit-report.md` §3.2).
- Caching: `sha256(query_digest + [skill digests] + model_id + prompt_version)` →
  `<runDir>/skill-cache/llm-<sha>.json`. Same step + same catalog = same answer, no repeat cost.

**S4c — card distillation (always, deterministic).**

For each surviving candidate produce `{what, why, how}`:

- `what` = frontmatter `name` + first clause of `description` (`promptizer.distill(desc, 120)`).
- `why` = the dominant scoring term, rendered from the `why[]` list (`cover=2/3(sqlite,migration)`,
  `bm25=claim_sessions`, `weight=0.62`, `forced by plan hint`).
- `how` = `load with skill(id)` + the body heading that best matches the query terms
  (first `##` line containing a query token) + digest prefix. **Never the body.**

This is the "cards with brief explanations" the vision asks for, and the reason the injected entry
stays ≤800 chars (`prompt-block-layout.md:44`).

### S5 — Selection and packing

1. **Minimal cover.** Greedy set cover over the authoritative needs, using S4a score as the
   tie-break: pick the skill covering the most uncovered needs, repeat until `k=3` or no need
   remains. Deterministic (`(-gain, -score, id)`).
2. **Prefetch** for step+1: same selection at depth 1, `k=2`, ids only.
3. **Confidence** = `min(1.0, 0.6*covered_ratio + 0.4*top_score_clipped)`; `fallback='skill_search'`
   when `confidence < FALLBACK_BELOW (0.45)` or `gaps` is non-empty.
4. **Hysteresis** applied after cover, before render (one swap max).
5. **Advisory mode** (no accept): return `current=[]`, `advisory=[...]`, `fallback='skill_search'`,
   and the full coverage matrix. Nothing is injected; the record is still written for shadow
   measurement.

Output object (also the CLI's stdout):

```json
{"schema":"dom/skill-queue/1","mode":"authoritative|advisory","step":"П1.2",
 "plan_version":23,"current":[{"id","digest","score_bp","why","what","how"}],
 "prefetch":["..."],"next":"П1.3","gaps":["sqlite"],"fallback":null,
 "confidence_bp":710,"capability_version":1,"index_digest":"sha256:...",
 "channels":{"exact":2,"lexical":1,"affinity":0,"dense":0},
 "metrics":{"candidates":6,"ms":14},"llm":{"mode":"off"}}
```

### S6 — Render and inject

**Entry (≤800 chars, `prompt-block-layout.md:44`):**

```text
step: П1.2 "Наработки: портабельность"  conf: 0.71
use: dom-memory@a1b2c3d4 (ledger: claims, versions) | test-driven-development@e5f6a7b8 (testing)
next: writing-plans@90ab12cd
gap: sqlite -> skill_search
```

Rules: ids + 8-hex digest + one clause; no bodies; replaced at most once per plan step
(`skill-router-0.3.1.md:183-192`); the host announces it at the next step boundary.

**Transport fallback:** `index.js` currently injects through `event.system.push({type:"text"})`
(`index.js:388-392`) and never touches instructions entries (no `instructions` reference in
`index.js`). So the implementation adds `ctx.session.instructions.entry.put("dom.skill-queue", …)`
when available and falls back to a tail `event.system.push` otherwise. Tail placement is also the
cheap side of provider caching (`prompt-block-layout.md:208-211`).

**S6b shadow card** (claim-friendly, no floats):

```text
properties: step, plan_version, mode, queue_digest, index_digest,
            candidates[{id, fit:"yes|no|unknown", score_bp, source:"accept|lexical|weight|shadow"}],
            outcome:"hit|miss|error|excess|failure|unknown", gaps[]
```

Bounded to ≤5 candidates so the claim stays small (`step-skill-selection.md:272-283`).

### S7 — Outcomes and learning

- `tool.hook("execute.after")` does not exist yet (`index.js:419-421` registers only
  `execute.before`); adding it is the first integration task. It observes the `skill` tool call and
  the step close, and forwards `{tool, args, status, step, queue_digest}` to
  `skill_outcomes.classify()`.
- Labels are the staged set from the amendments (`step-skill-selection.md:309-315`):
  `hit` (queued + loaded), `miss` (loaded outside queue), `error` (tool failed after load),
  `excess` (queued, unused, step accepted), `failure` (queued, unused, step not accepted),
  `unknown` (abandoned/replanned). Prefetch carries `scope=next` and is never counted as failure.
- Weight update reuses the concept's deltas (`skill-router-0.3.1.md:101-104`) with clip 0..1,
  decay ×0.98/10 steps, reset /100. Weights are hot in `ctx.storage` (JS) and snapshotted to the
  `skill_weights` property, which is what Python can read. The payload from `index.js` carries the
  hot copy; the claim property is the fallback and the audit record.
- Card promotion `unverified → shadow → verified → deprecated` only from outcomes + owner approval,
  never from the selector's own verdict (label leakage guard, `step-skill-selection.md:280-283`).

---

## 5. Data sources

### 5.1 Skill corpus (filesystem)

| Root | Measured | Precedence | Notes |
|---|---|---|---|
| `~/.config/opencode/skills/**/SKILL.md` | 134 | 2 | primary config catalog |
| `~/.claude/skills/**/SKILL.md` | 14 | 1 | shared with Claude path |
| `<project>/.opencode|.claude|.agents/skills` | per project | 3 (highest) | project override |
| repo `opencode/skills`, `claude/skills` | 2 | 3 | dom-memory itself |
| bundled / HTTP / Console | not counted | overlay | optional JS snapshot only |

Frontmatter: 132/134 have `description`, 49 have `tags`, **0 have `provides`** (audit N3). Parse
with `yaml.safe_load` on the first `---` block (PyYAML is already a dependency,
`pyproject.toml:10`); tolerate BOM/CRLF and the two files without frontmatter (index them as
`state=unverified`). `metadata.opencode/autoinvoke: false` → not routable (hidden from the model).

### 5.2 Plan and step state

- `current_plan_tasks` view + `plan_task_versions` (status only via `STATUS_MAP`,
  `planning.py:31`); step identity is `plan_task_id` + `plan_version` (WU C4).
- Header claim properties: `context`, `boundaries`, `accept`, `needs`, `skills`, `no_skills`
  (`step-skill-selection.md:12-23`). No schema change.
- Linked claims via `task_claims` (roles `implements|documents|validates`, `promptizer.py:135-147`).
- Goal claim: `target`, `constraints`, `version` (`step-skill-selection.md:225-237`).

### 5.3 Queue and learning state

- Previous queue: `skill_queue` property (digest, ids, plan_version) — source of hysteresis and
  stale detection.
- Weights: `skill_weights` property (Python-readable) + `ctx.storage` hot copy (JS).
- Outcomes: `execute.after` events → `skill_outcome` properties.
- Read receipts (`claim_entry.py:1145-1179`) give "which claims this session actually read" — the
  only honest basis for `miss`/`hit` attribution across sessions (WU A4).

### 5.4 Curated card file

`opencode/mockup/skills.card.json`, versioned with `capability_version`:

```json
{"version":1,"capability_version":1,
 "skills":{"dom-memory":{"provides":["ledger","planning"],"state":"verified"},
           "test-driven-development":{"provides":["testing"],"state":"verified"},
           "writing-plans":{"provides":["planning","docs"],"state":"verified"}}}
```

Seed set = the 20 names in audit Phase 2 (`rag-audit-report.md:421`). Cards without a `provides`
entry fall back to frontmatter, then to `[]` and therefore cannot satisfy an authoritative need —
which is the intended, visible failure.

---

## 6. Integration points with existing code

| Point | Existing fact | New call | Failure mode |
|---|---|---|---|
| `session.hook("prompt")` (`index.js:320-346`) | runs per user turn, calls `planCheck` | `skillStep("auto")` seeds the window and, if the entry API exists, puts `dom.skill-queue`; also attaches `prompt.skills` for step 1 (Decision 7, `skill-router-0.3.1.md:204-208`) | silent `status=silent`, never blocks (`index.js:23-24`) |
| `session.hook("context")` (`index.js:351-414`) | injects notes via `event.system.push` (`:388-392`) | freshness check: if `queue_digest` ≠ index digest → recompute once; otherwise re-push cached text | fall back to cached entry; no extra spawn |
| step close (plan write / `dom_plan_status`) | happens through the hook/Service path | `skillStep(step+1)` replaces the entry in the same call (Decision 4, `skill-router-0.3.1.md:188-192`) | entry replaced at next boundary at worst |
| `tool.hook("execute.after")` | **not registered** (`:419-421` only `execute.before`) | new hook → `skill_outcomes.classify()` | advisory only; Service gate unchanged |
| weights | `ctx.storage` (JS) | payload field `weights`; Python mirrors the `skill_weights` property | missing → 0.5 default |
| `dom_find` / `dom_context` | read-only claim lookup (`claim_entry.py:1114-1182`) | used by S0 to pull step claims when the direct SQL read is unavailable | degrade to title+context only |
| `dom_annotate` | property writer (`claim_entry.py:616-672`) | writes `skill_queue`, `skill_weights`, `skill_index_digest`, `skill_outcome` | never on the hot path; batched per step |
| `dom_distillate` | propose→validate→accept (`claim_entry.py:1620-1632`) | card patch path only (description/`provides` after 3 errors) | not required for 0.3.1 bootstrap |
| `dom_extract(provider="client")` | agent-as-model, server checks (`llm.py:262-306`) | one-time `provides` proposal for 15–20 skills, then owner accept | never per step |
| panel `/api/queue` | reads DOM projections only (`skill-router-0.3.1.md:199-202`) | projects the shadow card | read-only |

**Hard rule:** the Python pipeline is invoked like the existing hook programs — `spawnSync(python,
["-B", script], {input: JSON, env: {DOM_SESSION, DOM_SESSION_SOURCE:"hook"}})` (`index.js:237-250`)
— and returns JSON on stdout. It opens SQLite `mode=ro`, never writes. All writes happen in the JS
hook process (entry) or through the existing Service path (claims). This keeps the hash chain out of
the router's reach by construction (WU §4.1: new tables are chain-safe, but the simpler invariant is
"router does not write at all").

---

## 7. Dependencies and fallback chain

### 7.1 Dependency ladder

| Tier | Dependency | Used by | Required? |
|---|---|---|---|
| 0 | Python 3.11 stdlib (`hashlib`, `json`, `sqlite3`, `re`, `math`, `pathlib`, `concurrent.futures`) | everything | yes |
| 1 | PyYAML (already in `pyproject.toml:10`) | frontmatter | yes |
| 2 | `mockup.promptizer`, `mockup.planning`, `mockup.brief` | S1 | yes (in-tree) |
| 3 | `mockup.skill_router` pure helpers | S1/S3/S5 constants | yes (in-tree, unmodified) |
| 4 | `anthropic` SDK (optional extra) | **not used** | no |
| 5 | Polza HTTP (stdlib `urllib`) via `command` provider | S4b | no, default off |
| 6 | `numpy` / ONNX / embeddings | S2d only | no, `tools/` only |

### 7.2 Fallback chain (each line is a designed state, not an exception)

1. **No plan / no step** → no queue, `dom.notes` alone (guard, `step-skill-selection.md:345-347`).
2. **No `accept`** → `mode=advisory`, `current=[]`, `fallback=skill_search`; coverage matrix still
   written. This is the state of the live ledger today (0 `accept`, 0 `needs` — C5).
3. **No cards / no `provides`** → authoritative needs have no providers → `gaps` + `skill_search`.
   Lexical candidates are advisory only. Never a decoy (C2).
4. **Index read failure** (permission, bad frontmatter) → skip the file, count `degraded`; <1 skill →
   confidence 0 + `skill_search`.
5. **Stale queue** (skill digest changed) → recompute once, re-emit; the entry carries digests so
   the mismatch is detectable without reading bodies (`prompt-block-layout.md:108-112`).
6. **No weights** → prior 0.5.
7. **Entry API absent** → `event.system.push` tail block (≤800 chars).
8. **LLM unavailable/timeout** → S4b skipped, `llm.mode="off"`, deterministic queue unchanged.
9. **Embeddings absent** → S2d skipped; output is byte-identical to the baseline.
10. **Any exception** → return `{"mode":"failed","fallback":"skill_search"}`; the hook logs and the
    session continues (a hook never blocks, `index.js:23-24`).

### 7.3 What ships in 0.3.1 vs later

| 0.3.1 (this design, baseline) | Deferred (0.4+) |
|---|---|
| S0–S3, S4a, S4c, S5, S6 render | S2d dense channel |
| filesystem index + curated cards | S4b enabled by default |
| shadow/advisory + metrics | claim-native card registry replacing JSON |
| `execute.after` outcome loop | LLM card-patch pipeline |
| optional S4b behind a flag | dashboard/agent-profile variant (Decision 10) |

---

## 8. Concrete module structure

All paths relative to the repo. Pure modules live in `opencode/mockup/` (that is the shipped
engine and what `tests/check_*.py` imports, e.g. `check_skill_router.py:7-11`); benchmark/optional
code lives in `tools/`.

```text
opencode/mockup/
  skill_index.py      SkillCollector.collect() -> list[SkillCard]
                      SkillIndex.build(cards) / load_or_build(run_dir, roots, cards)
                      fingerprint(roots, card_digest) -> str
  skill_text.py       normalize(text) · tokens(text) · char_ngrams(text, 3..5)
                      class Bm25Index: add(doc_id, fields) · search(query, k) · idf(term)
                      FIELD_WEIGHTS = {"name":2.0,"tags":1.5,"description":1.0,"body":0.3}
  skill_cards.py      load_curated(path) · merge(index, curated) · state_for(card)
                      provides_for(card) -> list[str]   # never from description
  skill_query.py      class StepWindow: build(service, task_id, depth=4)
                      distill_step(step, props, claims) -> str  (<=600)
                      infer_needs(text, claims) -> list[Need]
                      class QueryCard: from_window(...) · mode() · coverage_matrix()
  skill_candidates.py channel_exact(query, index) -> Candidates + gaps
                      channel_lexical(query, index) -> Candidates
                      channel_affinity(query, index, weights) -> Candidates
                      fuse(channels) -> Candidates            # RRF, keeps via_exact
  skill_select.py     select_pipeline(window, index, weights, options) -> SelectionResult
                      _filter(cands, query) · _rerank(cands, query)
                      _minimal_cover(cands, needs, k) · _confidence(...)
                      # imports: capabilities_from_accept, rule_groups, PRIORS only
  skill_deep.py       adjudicate(cands, query, provider=None) -> list[Verdict]   # optional
                      distill_card(card, query) -> {what, why, how}
  skill_render.py     render_entry(result, budget=800) -> str
                      render_shadow_card(result) -> dict        # ints/strings only
  skill_cache.py      get(namespace, key) · put(namespace, key, value)
                      invalidate(plan_version)
  skill_outcomes.py   classify(event, queue) -> Label
                      apply_weight(weights, label) · metrics(labels) -> dict
  skill_cli.py        main(argv) -> JSON on stdout   # --step auto|path, --mode shadow|inject
  skills.card.json    curated provides/state (versioned with capability_version)

opencode/index.js     + skillStep(), + entry put/remove, + execute.after hook
tools/
  polza_llm.py        stdlib urllib bridge for the `command` provider (optional, not imported at runtime)
  skill_bench.py      TF-IDF vs bge-m3 vs yandex; gap cases; precision@1/@3, MRR, latency
tests/
  check_skill_index.py    fixture tree: precedence, missing frontmatter, digest, cache fingerprint
  check_skill_text.py     RU inflection, code identifiers, BM25 monotonicity, determinism
  check_skill_query.py    accept→needs, inferred needs evidence, coverage matrix, no-accept mode
  check_skill_select.py   gap (no decoy), forced/banned, minimal cover, hysteresis, confidence
  check_skill_render.py   <=800 chars, digests present, no body text, sort_keys stable
  check_skill_outcomes.py label mapping incl. prefetch scope=next, weight clip/decay
```

**Key contract (stable, testable):**

```python
def select_pipeline(window: StepWindow,
                    index: SkillIndex,
                    weights: dict[str, float],
                    options: SelectOptions) -> SelectionResult: ...
# SelectionResult: step, mode, current[], prefetch[], gaps[], fallback,
#                  confidence_bp, channels{}, why{}, digests{}, metrics{}
```

`SelectionResult` is pure data; `render_entry` and `render_shadow_card` are the only formatters, so
the prompt contract and the claim contract can be tested independently of the algorithm.

**Test/CI invariants:** new tests use a temporary fixture tree (3–5 fake `SKILL.md`), never the
user's home directory; no network; no optional import at module top level; the whole suite still runs
through `python tools/run_tests.py` with only python+node (`run_tests.py:23-70`).

---

## 9. Evaluation (how we will know it works)

Shadow window ≥20 real steps, no injection until the gate passes:

| Metric | Target | Source |
|---|---|---|
| queue hit rate | ≥ 0.70 | `execute.after` |
| miss rate | ≤ 0.20 | loaded outside queue |
| gap-detection rate | 1.0 (never a decoy for a missing capability) | S3 `gaps` |
| `fallback_search_rate` | < 0.15 | queue records |
| entry size | ≤ 800 chars | renderer |
| churn | ≤ 1 swap/step | hysteresis log |
| latency p95 | ≤ 300 ms | `metrics.ms` |
| determinism | identical JSON on two runs | hash of stdout |

The audit's warning stands: the selector must not grade itself. `fit` is a hypothesis; the label
comes only from outcomes or a human override (`step-skill-selection.md:280-283`).

---

## 10. Open questions (3)

1. **Advisory visibility.** May `mode=advisory` candidates appear in the prompt at all (clearly
   marked, not authoritative) before any step has `accept`, or must they stay in the ledger/panel
   until the acceptor exists? *Recommendation: ledger + panel only in 0.3.1; a config flag
   (`DOM_SKILL_ADVISORY=1`) to preview, default off.* This is the one decision that most changes
   perceived usefulness on day one.
2. **Who curates the first `provides` map** for the 15–20 seed skills — owner-only, or
   `dom_extract(provider="client")` proposal followed by owner `dom_accept`? *Recommendation: client
   proposal + owner accept; it costs one review round and gives the card a real evidence chain.*
3. **Egress for S4b.** Is a Qwen3.5-9B call to Polza allowed on the distilled step text (≤600 chars,
   no claims, no paths), or is `provider=client` the only acceptable adjudicator in 0.3.1?
   *Recommendation: ship S4b disabled, implement the `command` bridge, and enable only after an
   explicit privacy decision (C11).*

---

## Appendix A — What was read (evidence base)

- `opencode/mockup/skill_router.py` (224 lines; constants 16-42, capability load 47-50,
  `capabilities_from_accept` 53-68, `step_state` 71-87, `normalize_step` 95-108, `rule_groups`
  111-120, `score` 141-172, `select` 180-208, `build_queue` 211-224).
- `opencode/mockup/capabilities.json` (14 caps 3-18; 6 accept rules 19-26).
- `docs/concepts/2026-11-24-step-skill-selection.md` (fields 12-23, prompt 30-40, rules 59-77,
  score 79-97, live-plan failure 152-180, accept rule 182-197, learning 199-220, cards 221-307,
  amendments 309-343, guards 345-354).
- `docs/concepts/2026-11-24-skill-router-0.3.1.md` (7 blocks 10-20, layers 37-54, state machine
  79-88, outcomes 90-104, channels 156-164, decisions 1-10 at 166-270, injection contract 293-305).
- `docs/concepts/2026-11-24-prompt-block-layout.md` (layout 9-30, placement 33-47, target 60-78,
  sensing 80-112, intensity 114-127, E4 151-187, E6 189-220, non-goals 235-239).
- `C:\Temp\opencode\rag-audit-report.md` (C1-C12, N1-N10, §3.2 papers: SkillSeek, Field-Aware,
  SkillDreamer, SkillRet, SRA; verdict §4; phases §5).
- `C:\Temp\opencode\dom-wu-usage-analysis.md` §0-4 (chain-safe tables 152-168, version derived
  170-174, plan closes only from source 176-180, `.dom-memory` ignored 188-190).
- `opencode/mockup/claim_entry.py` (`find` 1114-1182, read receipts 1145-1179, `dom_extract`
  1513-1522, `dom_distillate` 1620-1632).
- `opencode/mockup/promptizer.py` (`distill` 99-118, `clause` 121-132, `resolve` 165-178).
- `opencode/mockup/plan_context.py` (`distill_contexts` 104-169 — the existing deterministic
  "assign discussion units to plan items" precedent).
- `opencode/mockup/llm.py` (providers 201-202, client provider 262-306).
- `opencode/mockup/brief.py` (MAX_CHARS 13, `_choose_current` 43-52).
- `opencode/index.js` (run/spawn 237-250, prompt hook 320-346, context hook 351-414, system push
  388-392, execute.before only 419-421; no `instructions`/`.skill`/`execute.after` references).
- `tools/run_tests.py` 23-70; `pyproject.toml`; `.gitignore:7`.
- Filesystem: 134 `SKILL.md` under `~/.config/opencode/skills`, 14 under `~/.claude/skills`
  (re-verified 2026-11-26); frontmatter sample confirms `name`/`description`/`category`/`metadata`.

**Read-only statement:** no repository file, test, git state, or live database was modified. All
DB access was `mode=ro`; only this report and temporary analysis files under `C:\Temp\opencode`
were created.
