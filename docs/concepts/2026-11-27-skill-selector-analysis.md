# Skill Selector Pipeline — Approach Evaluation and Selection (DOM Memory 0.3.1)

**Role:** analyst (Code Factory)
**Date:** 2026-11-26
**Scope:** choose the implementation strategy for automated per-step skill selection and injection
**Verdict:** **two-level lexical cascade over the exact capability contract** (addendum variant 0.6), with the dense channel deferred and confidence-gated.
**Confidence:** 0.80
**Read-only statement:** no repository file, test, git state or live ledger was modified. DB access was read-only; the only written artifacts are this report and `C:\Temp\opencode\skill-selector-analysis.json`.

---

## 0. What was evaluated

The pipeline must take a micro-step **σ**, inherit capabilities from the plan item **π**, derive a **phase** from the σ verb, search ~150 skill files, rank by keywords + character n-gram TF-IDF + optional embeddings, threshold against a calibrated negative control, return top-3 with `what/why/how` (saying *mentions*, never *provides*), inject a stable ≤800-char block gated by a fingerprint, and keep a missing provider visible as a gap.

The three time scales are fixed by the addendum and must not be mixed:

| Scale | Object | Who writes it | Frequency | What lives there |
|---|---|---|---|---|
| π | plan item / claim branch | plan agent → ledger | 1–10 per session | accept, closure, authority for capability |
| σ | micro-step registry record | plan decomposition | 20–80 per session | queue selection, `phase`, injection, re-evaluation |
| τ | prompt / agent turn | user / agent | 50–200 per session | fingerprint gate, re-emit vs silence |

Source: `docs/concepts/2026-11-27-skill-selector-addendum.md` §1 (π/σ/τ table). This analysis adopts that model as given.

The non-negotiable constraints are the collapse-audit items H1, S1, C2, C3 (collapse-audit.md:24–30, 68–84, 131–139, 143–151) plus I-A, I-B, I-C (addendum §5.2, §6). No approach is acceptable if it weakens any of them.

---

## 1. Approach matrix

Ranked by the five requested axes. `collapse_score` means collapse resistance (higher is safer); the specific patterns are named.

| # | Approach | Complexity | Precision | Latency | Collapse resistance | Deps |
|---|---|---|---|---|---|---|
| **1** | **Two-level lexical cascade over exact capability contract** | medium, ~1.2–1.5k LOC core + 80 JS; 2–4 weeks | medium-high: exact gate blocks decoys; calibrated BM25 for recall | σ 5–20 ms warm; τ ≤8 ms | **high** — H1/S1/C2/C3 answered structurally; I-A/B/C preserved | stdlib + PyYAML |
| 2 | Exact capability join only (`needs → provides`) | low, ~150–250 LOC | very high where covered, but recall ≈13% | <5 ms | high against decoys, **low** against emptiness (R2/R3, D1) | none |
| 3 | Hybrid lexical + confidence-gated dense (AgentIR shape) | high, ~2.2–2.8k LOC, 5–7 weeks | high if dense helps; marginal at 150 docs | σ 20–60 ms + model load | medium — model drift, egress, non-reproducibility | onnxruntime + 118 MB int8 model or API |
| 4 | Dense-first description RAG | high, full retrieval stack | **low-medium**: confident cosine decoys | 10–60 ms | **low** — C2/C7/C8/C11 | heavy model/API stack |
| 5 | LLM-mediated retrieval / adjudication loop | high, + API + caching + redaction | potentially high, noisy and costly | 300 ms–2 s per σ | **low** — egress, non-determinism, self-grading | LLM API or `provider=client` |

Why the exact join (#2) is not the winner despite being safest: the task requires searching 150+ descriptions and per-step phase resolution. The existing router proves the join works but is silent for 87% of the catalog. It is the *authority inside* #1, not the whole pipeline.

Why dense-first (#4) is rejected: `rag-audit-report.md` §1 C1–C3 shows it solves the wrong problem (the missing artifact is `capability → skill`, not ranking) and `C7`/`C8` show it degrades reproducibility and cannot store vectors in immutable float-free claims. The proposal claim `c36aff1b` conflicts with the standing decisions `12eeb186` (empty queue + `skill_search`) and `4a452714` ("никаких эмбеддингов и случайности") and with plan item П9 ("не строить новый RAG до доказательства пробела").

Why the LLM loop (#5) is rejected as default: the same egress/privacy problem as #4, plus non-determinism and cost on every σ. It remains the existing optional S4b adjudicator over top-5, off by default.

---

## 2. Winner rationale mapped to audit findings

| Finding | Where it bites | Structural answer in the winner |
|---|---|---|
| **H1** hard re-issue counter | addendum §3.2 proposes K=8; 60–80 σ would exhaust it | count **idle** re-issues only (fingerprint unchanged but block re-emitted); legitimate `(cap,phase)` changes are never counted. Cap idle only. |
| **S1** weights from outcomes | outcome → weight → selector → same skill | `effective_weight = min(raw_weight, PRIORS[task_type])`; clip 0..1; decay ×0.98/10, reset /100 (`skill_router.py:16–19`). Promotion also needs manual/independent review. |
| **C2** receipt = skill hit | read receipt used as usage proxy | three physical tracks: `read_receipts` (`storage.py:231`, L2), `execute.after` tool calls (new hook; only `execute.before` exists at `index.js:419–421`), and `plan_checks`/accept. One invariant test forbids substitution. |
| **C3** mentions = provides | `_extract_caps` over full text | `mentions` is a separate offline field; `provides` comes only from the curated card or frontmatter (`skill_router.py:133–138`); mentions never satisfies a need; gap stays visible. |
| **I-C** selector must be read-only | queue could rewrite the plan | selector emits no revision events; AST check rejects INSERT/UPDATE/DELETE on the hot path; corrections go through the existing plan ingress as a revision. |
| **I-A** gradation by position | confidence-driven size churns the block | fixed positional layout: σ full (~400 chars), σ+1 ids (~120), σ+2 one id (~80), σ+3/4 title (~50 each), gaps (~60), total ≈760 ≤ 800 (addendum §5.1). |
| **I-B** no floats in the block | `conf: 0.71 → 0.73` breaks the cache | only `score_bp`/`coverage_bp`/`fit_bp` integers in the block; confidence lives in the panel and shadow card. |
| R7 mirror overwrite | edits in `opencode/mockup/` are resynced | develop upstream; CI proves `claude/mockup ≡ opencode/mockup` (E0). |
| R2 / R3 coverage + bootstrap deadlock | 0/150 skills declare `provides`; no outcomes → no verified cards | manual owner verification of 20 cards first; release notes state 20/150; advisory outcomes are labelled as biased observations. |
| D-new σ vs 14-capability dictionary | cap-only needs are constant across σ | orthogonal `phase` axis: 14 cap × 6 phase = 84 classes, human-curatable; `unknown` is explicit. |
| D3 `cover_norm` ambiguity | one formula mixed two meanings | two separate formulas with separate weights and separate tests. |
| R8 non-reproducible stdout | `metrics.ms` inside the hashed object | `result` hashed, `telemetry` excluded (`skill_cli.py`). |
| D1 forced/banned double count | inside affinity *and* as bonuses | forced/banned are filters only; `bans > forces` explicit (`addendum §7 D1/D2`). |
| D-thr arbitrary 0.3 | background char-ngram cosine on RU is 0.15–0.35 | threshold := 95th percentile of a shuffled-pair negative control, stored with `index_digest` + `capability_version`. |
| D-corr correlated channels | kw, ngram, tfidf ρ≈0.6–0.9 | `lex = max(...)`, not a sum; structural term kept independent. |
| D4 confidence conflates coverage/quality | gap fallback shows 0.71 | `coverage_bp` and `fit_bp` separate; partial injection (covered in queue, gaps separate). |
| D6 RRF computed but unused | two ordering mechanisms | drop RRF; sort `(-cover, -lex, id)`. |
| D8 invariant vs metric | "gap-detection rate = 1.0" called a metric | invariants (zero violations) separated from metrics (thresholds on a sample). |
| S2 / G3 promotion / client proposals | auto-review could close the loop | `provides` proposals never auto; manual or independent review only. |
| G1 step-text gaming | synonym edit flips the queue | closed verb dictionary, first verb only, exact lemma/prefix, `unknown` on ambiguity. |

The result is a pipeline that cannot "guess a capability" by construction: lexical channels may **rank**, but only the exact `needs → provides` join may **admit**, and the gap is always rendered.

---

## 3. Architecture in one screen

```
OFFLINE  (once per file digest; ~300–500 ms for 150 files)
  collect roots in precedence order
    ~/.claude/skills/**/SKILL.md          (14 measured)
    ~/.config/opencode/skills/**/SKILL.md (134 measured)
    <project>/.opencode|.claude|.agents/skills, plugin skills
  parse frontmatter + ## sections
    -> mentions[]  (NEVER provides)
    -> usage_hint  (first paragraph, verbatim)
    -> examples[]  (verbatim commands from ## Example)
    -> sections{}  (When-to-use / Example / Tags)
  provides <- curated card | frontmatter | []
  persist .dom-memory/skill-cache/index-<fingerprint>.json  (gitignored)
        │ index held in the live MCP server memory
σ-BOUNDARY  (on (cap,phase) | plan_version | index_digest change)
  Stage 1  keyword rules ∪ char 3–5-gram BM25  -> 40–60
  Stage 2  max(lex) + structural, calibrated  -> 15–20
  Stage 3b relevance over the in-memory index (no disk)
  Stage 4  top-3 + what / why / how
  prefetch σ+1..σ+4, k=2, ids only
  budget ≤120 ms warm
        │
τ-GATE  (every prompt; JS in the plugin process; ≤8 ms; no spawn)
  fp = hash(active_step_id, cap_set, phase, plan_version, index_digest)
  fp == prev_fp ? silence : re-emit
  idle-reissue counter (legitimate changes uncounted)
```

Sources: `2026-11-27-skill-selector-addendum.md` §3.2 (offline/σ/τ split), `2026-11-26-skill-selector-architecture.md` §3–§4 (stage design, S2 channels), §5 (data roots).

Transport choice follows addendum §4: a tool in the already-running MCP server (5–20 ms) rather than `spawnSync` (150–400 ms; `index.js:237–250`). A JS re-implementation of scoring is rejected because two implementations inevitably diverge between Claude and OpenCode.

Data roots and frontmatter facts: 150 `SKILL.md`, 132/134 with `description`, 49 with `tags`, **0 with `provides`** (`rag-audit-report.md` §2 N3). That single fact is why the curated card layer is mandatory and why coverage is honestly 20/150 at first.

---

## 4. Module breakdown

All paths relative to the repo. Pure modules live in `opencode/mockup/` because that is the shipped engine imported by `tests/check_*` (architecture §8). Optional/benchmark code lives in `tools/` and is never imported by tests.

| Module | File | ~LOC | Deps | Responsibility |
|---|---|---|---|---|
| PhaseClassifier | `opencode/mockup/skill_phase.py` | 80 | json | first-verb → phase; closed dict; `unknown` on ambiguity (G1) |
| SkillCollector/Index | `opencode/mockup/skill_index.py` | 140 | hashlib/json/pathlib/PyYAML | precedence collect, parse, mentions/examples, digest cache |
| Tokenizers/BM25 | `opencode/mockup/skill_text.py` | 140 | stdlib | char 3–5-grams, field-weighted BM25 (`k1=1.5,b=0.75`) |
| CardStore | `opencode/mockup/skill_cards.py` | 80 | json | `provides` from card/frontmatter only; never description |
| StepWindow/QueryCard | `opencode/mockup/skill_query.py` | 180 | promptizer, skill_router, skill_phase | σ window, distill, cap inheritance, coverage matrix |
| RecallChannels | `opencode/mockup/skill_candidates.py` | 160 | skill_text | exact gate + lexical + affinity; `max`, no RRF |
| Selector | `opencode/mockup/skill_select.py` | 220 | skill_router, candidates, cards | filter/rerank/minimal-cover/hysteresis/gaps; read-only |
| ThresholdCalibrator | `tools/skill_calibrate.py` | 90 | stdlib | shuffled negative control → 95th percentile |
| Renderer | `opencode/mockup/skill_render.py` | 120 | none | positional gradation ≤800; ints only; "mentions" |
| DerivedCache | `opencode/mockup/skill_cache.py` | 60 | json/hashlib | keys include `capability_version` + `weights_digest` |
| OutcomeTracker | `opencode/mockup/skill_outcomes.py` | 150 | none | labels, three tracks, `max_weight ≤ priors` |
| CLI | `opencode/mockup/skill_cli.py` | 90 | all | JSON stdout; `result` vs `telemetry` split |
| JS bridge | `opencode/index.js` | +80 | node | τ-gate, entry put/remove, `execute.after`, idle counter |
| Curated data | `skills.card.json`, `phase.json` | data | `CAPABILITY_VERSION` | 20 verified cards; phase v1 |
| Optional | `tools/skill_bench.py`, `skill_dense.py`, `polza_llm.py` | 200/120/60 | lazy | offline measurement only |
| Tests | `tests/check_skill_{index,text,query,select,render,outcomes,phase}.py` | ~700 | python+node | property invariants; no network |

Core estimate ≈1.2–1.5k LOC Python + 80 JS + ~700 tests; optional tools separate. That fits 1–2 developers over 2–4 weeks **after** the E0–E3 bootstrap, which is the honest cost driver: 20 cards × (read + formulate + review) is 2–4 hours, and 150 cards would be 15–30 hours (addendum §8.2).

The stable contract is one pure function:

```python
def select_pipeline(window: StepWindow, index: SkillIndex,
                    weights: dict[str, float], options: SelectOptions) -> SelectionResult: ...
# SelectionResult: step, mode, current[], prefetch[], gaps[], fallback,
#                  coverage_bp, fit_bp, channels{}, why{}, digests{}, metrics{}
```

`SelectionResult` is pure data; only `render_entry` and `render_shadow_card` format it. The prompt contract and the claim contract can therefore be tested independently of the algorithm (architecture §8).

---

## 5. Stage plan E0–E8

| Stage | Name | Depends on | Acceptance |
|---|---|---|---|
| **E0** | Freeze decisions and placement | — | Q1–Q5 answered in writing; development upstream (R7); CI proves `claude/mockup ≡ opencode/mockup`; σ registry schema recorded |
| **E1** | Registry `cap[]` + `phase`, dictionary v1 | E0 | additive migration; existing ledger hash unchanged; `unknown` explicit |
| **E2** | Offline indexer | E0 | 150 files; digest cache; second run 0 reads; no invented `provides` |
| **E3** | Curate top-20 `provides` | E0 | 20 cards `state=verified`; manual/independent review; remainder reported uncurated |
| **E4** | Phase classifier + 20 σ labels | E1 | `unknown ≤20%`; exact verbs only; synonym trap does not flip phase |
| **E5** | Stage 1–2 + negative control | E2, E3, E4 | threshold = 95th percentile of shuffled pairs, stored with digests; no hand-picked number; gap cases empty |
| **E6** | Stage 3b–4 + positional render | E5 | ≤800 chars at 4 skills; `why` says "mentions" + source; no floats; rename does not change block |
| **E7** | MCP tool + JS τ-gate | E6 | σ p95 ≤120 ms; τ p95 ≤8 ms; idle-only counter; failure serves cached block, never a wrong one |
| **E8** | Regression + v0.3.1 gate | E1–E7 | four existing checks pass unchanged; ledger hash unchanged; no network/optional import in tests; byte-identical result twice; notes state 20/150 |

Deferred beyond E8 (do not pull forward): E9 `execute.after` labels + manual audit of 30; E10 weights/promotion/top-50 curation; E11 panel; E12 benchmark n-gram vs BM25 vs dense. These belong to 0.4.0. Pulling them into 0.3.1 would rebuild the self-grading loop before the acceptor exists.

Important ordering point from the addendum §9.4: **bootstrap before cascade**. Building seven stages before `accept`, cards and `phase` exist means debugging something that cannot run (the live ledger has 0 `accept`, 0 `needs` — `rag-audit-report.md` C5).

---

## 6. Risk register

| ID | Risk | P | I | Detection | Mitigation |
|---|---|---|---|---|---|
| R1 | Confident decoy: lexical mention admits a non-provider; gap hidden | high | critical | gap cases in `check_skill_select`; ≥20 labelled σ decoy audit; fallback-rate monitor | exact join is the only authority; mentions separate; threshold from negative control; gap rendered |
| R2 | Coverage illusion: 87% uncurated while queue looks authoritative | high | critical | per-release coverage report; count empty-σ; compare providers to the 14 caps | bootstrap 20 cards; publish 20/150; every uncovered cap becomes a gap claim |
| R3 | Self-grading: weights/promotion outrun priors | medium | high | test `effective_weight ≤ PRIORS`; audit first 30 labels; saturation at 1.0; promotion log | clip to prior; decay/reset; promotion only with independent review |
| R4 | Prompt-cache instability: churn, float leak, idle re-issue | medium | high | cached-token ratio; block re-issue count; byte-diff on equal `(cap,phase)`; two-run hash | fingerprint gate; silence on equal; idle-only count; positional layout; integers |
| R5 | Transport collapse: MCP death or 150–400 ms spawn | medium | high | health-check latency; tool-failure count; p95 alerts; stale `index_digest` | MCP tool primary; cached block on failure; slow spawn fallback + circuit breaker |

Additional watch items (not top-5): G1 phase gaming (mitigated by the closed dictionary), H3 budget overflow (boundary test at E6), S3 untracked `skill_search` fallback (panel metric at E11), H2 k=3 repetition (accepted, low).

Note on R4: the external study `arXiv:2610.34817` found that prompt-caching mis-integrations usually fail **silently**, so the economic contour is not optional — it is the only detector for this class of bug.

---

## 7. External evidence

The configured web-search tool returned **HTTP 402** on every query, so the evidence below comes from direct fetches of the arXiv API, Hugging Face API, AWS documentation and OpenCode docs — the same method the prior audit used. No claim here rests on a general search snippet.

| Source | Finding used | URL |
|---|---|---|
| SkillSeek (SkillsBench, 89 tasks) | Plain **BM25 alone matched or beat** the LLM-mediated retrieval loop on 3 of 4 settings; a small cross-encoder covered the fourth; spend dropped $51.30 → $27.54 | https://arxiv.org/abs/2609.38822 |
| Field-Aware Agent Skill Retrieval | Treating name/description/body as separate fields improves hybrid retrieval; the advantage **grows with bank size** — at 150 skills, structure/curation beats a bigger model | https://arxiv.org/abs/2608.02880 |
| SkillDreamer / Imagine Before Acting | Formalizes **Task–Skill Misalignment**: tasks state objectives, skills state capabilities; infer capabilities first, then retrieve. This is exactly `accept → cap → needs → provides` | https://arxiv.org/abs/2609.01642 |
| SkillRet | 16k-skill benchmark; off-the-shelf retrievers struggle at scale — 150 skills is small, heavy retrieval infra is premature | https://arxiv.org/abs/2605.05726 |
| SRA (Skill Retrieval Augmentation) | Retrieval is not the only bottleneck; the model's **decision to load a skill** matters, so always-top-3 can hurt | https://arxiv.org/abs/2604.24594 |
| AgentIR cascade | Confidence-triggered cascade decides from the **BM25 top-k margin** whether the ~52 ms dense channel is worth running; skips 63–100% of queries at parity | https://arxiv.org/abs/2605.25092 |
| RusBEIR | 17 Russian IR datasets; **BM25 is a strong baseline** once preprocessing handles morphology in morphologically rich languages | https://arxiv.org/abs/2504.12879 |
| CACHELINT / prompt-caching mis-integration | Empirical study of 120 PRs in 10 agent systems: most prompt-caching bugs **fail silently**; request-side and read-side errors | https://arxiv.org/abs/2610.34817 |
| Prompt Cache | Reusing attention states for overlapping prefixes is a real latency lever, which is why block stability is an economic requirement | https://arxiv.org/abs/2311.04934 |
| AWS Bedrock prompt caching | Implicit vs explicit prefix caching; cached prefixes reduce latency and input cost | https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html |
| HF `intfloat/multilingual-e5-small` | Multilingual incl. `ru`; ONNX int8 is **118 MB** (fp32 470 MB, fp16 235 MB) — confirms the "80 MB" claim is false for a multilingual model | https://huggingface.co/intfloat/multilingual-e5-small |
| HF `Xenova/multilingual-e5-small` (ONNX) | File sizes verified via API: `model_int8.onnx` 118,054,593 bytes | https://huggingface.co/Xenova/multilingual-e5-small/tree/main/onnx |
| OpenCode skills docs | `SKILL.md` discovery, frontmatter `name`/`description`, precedence — the filesystem collector's contract | https://opencode.ai/docs/skills/ |
| OpenCode server API | 190 operations / 542 schemas; skill and session surfaces exist, but no documented per-step router | https://opencode.ai/docs/server/ |

Interpretation: the literature consistently supports **lexical-first, structure-aware, capability-gated** retrieval at this scale, and treats dense retrieval as an optional second stage. It does **not** support jumping to a neural index for 150 documents with no labelled evaluation set. That is the same conclusion the internal `rag-audit-report.md` reached independently (§4 verdict).

---

## 8. What this approach honestly does not do

- It covers **20 of 150** skills at first. The other ~87% remain outside authority. This is stated in the release notes, not hidden.
- It cannot learn automatically in 0.3.1. Learning (`execute.after`, weights, promotion) is 0.4.0 and gated on the acceptor.
- With `phase = unknown` and empty `cap[]`, it returns a gap. That is correct, but a session made entirely of such σ receives no queue.
- It does not filter the 69k `core/skill-guidance` catalog. E4 measured that `ctx.skill.transform` cannot reach the config skills (`prompt-block-layout.md:151–187`), so catalog shrinking remains a 0.4+ core-level item.
- It does not use embeddings. If a later benchmark on a labelled RU set shows a significant gain, the dense channel enters as a **shadow reranker behind the confidence trigger**, never as the router.

---

## 9. Open questions

| ID | Question | Blocks | Why |
|---|---|---|---|
| Q1 | Are `skill_router.py`, `capabilities.json`, `promptizer.py`, `check_skill_router.py` shipped, or mirror-only? `VERSIONS.md:31–35` says 0.3.1 is docs-only | E0–E1 effort | If mirror-only, rules and `capabilities_from_accept` are re-authored |
| Q2 | Actual σ registry schema and revision semantics | E1, entire two-level model | Decides additive `cap[]`/`phase` vs a new entity, and whether rename preserves identity |
| Q3 | Upstream vs mirror development | all | R7: mirror edits are overwritten by `sync_from_upstream.py` |
| Q4 | Who may correct the registry at a step | I-C boundary | Selector must stay read-only or the queue self-confirms |
| Q5 | Is `cap × phase` approved as the key decision | E4–E6 | Without it, per-step re-evaluation is a constant |
| Q6 | May advisory candidates appear before any `accept` exists | E6 default mode | Live ledger has 0 accept / 0 needs; decides day-one usefulness |
| Q7 | Is dense/egress allowed at all, and which model is pinned | 0.9/1.2 only | Not needed for the winner; marginal gain small at 150 docs; int8 model is 118 MB |
| Q8 | Does this host expose `session.instructions.entry.put/remove`, and do subagents inherit the queue | E7 transport | `index.js` has no `instructions` reference; only the tail `event.system.push` fallback at `:388–392` |

---

## 10. Citations map (structural claims)

- Router primitives: `opencode/mockup/skill_router.py:16–19` (priors), `:22–33` (rules), `:53–68` (`capabilities_from_accept`), `:71–87` (`step_state`), `:141–172` (`score`, needs early-return), `:180–208` (`select`, zero-coverage drop at `:188–189`).
- Capability dictionary: `opencode/mockup/capabilities.json:3–18` (14 caps), `:19–26` (6 accept rules).
- Schema/tracks: `opencode/mockup/storage.py:35` (`SCHEMA_VERSION=5`), `:95` (IMMUTABLE list), `:122` (read_receipts keys), `:230` (`claim_sessions`), `:231` (`read_receipts`).
- Plugin transport: `opencode/index.js:237–250` (`spawnSync`), `:320–346` (prompt hook), `:351–414` (context hook), `:388–392` (`event.system.push`), `:419–421` (`execute.before` only).
- Budget/placement: `docs/concepts/2026-11-24-prompt-block-layout.md:44` (queue ≤800), `:151–187` (E4 fails), `:189–220` (E6).
- π/σ/τ, phase, split, transport, invariants: `docs/concepts/2026-11-27-skill-selector-addendum.md` §1, §2.2–2.3, §3.2, §4, §5.1–5.2, §6, §7, §9.1, §10.1.
- Collapse patterns: `docs/concepts/2026-11-27-collapse-audit.md:24–30` (H1), `:68–84` (S1), `:131–139` (C2), `:143–151` (C3), `:223–243` (matrix).
- Stage design and modules: `docs/concepts/2026-11-26-skill-selector-architecture.md:36–48` (C1–C12), `:148–170` (S0), `:225–260` (S2), `:262–283` (S3), `:308–330` (S5), `:332–350` (S6), `:362–377` (S7), `:507–551` (module tree).
- Prior audits/claims: `C:\Temp\opencode\rag-audit-report.md` §1 C1–C12, §2 N1–N10, §3.2, §4; claims `c36aff1b` (RAG proposal), `43db3a57` (skill_index spec), `41f7e44e` (embedding harness), `3ad7be8b` (collapse audit), `12eeb186` (empty queue + `skill_search`), `4a452714` (no embeddings), `a3b2a0fe` (router v3), `09cd0634` (0.3.1 наработки), `e422a764` (94/166 got a tag; none provides sqlite/migration/security).
- Version lines: `docs/VERSIONS.md:5–14` (live 0.2.6), `:31–35` (0.3.1 planned, docs only).

---

## 11. Bottom line

Build the **lexical cascade**, not the embedding router. Put the exact capability join at the centre as the only authority, add a deterministic `phase` axis for per-step resolution, calibrate the lexical threshold against a shuffled negative control, render a positional ≤800-char block gated by a fingerprint that counts *idle* re-issues only, and keep **three separate tracks** for read, call and accept. Defer dense retrieval and outcome learning to 0.4.0, behind a measured gate.

This is the only option that satisfies every non-negotiable constraint without weakening one, is implementable in 2–4 weeks by 1–2 developers, and matches what the 2026 literature and the project's own three prior audits independently concluded.
