# Claim-level brief: line, accumulated, gaps, search, vector, next

**Status:** proposed design. The architecture was accepted by the user on
2026-11-16 as the baseline for stage 3; plan item П4 (branches AD,
AD100–AD500). No product code is written yet.

## 1. Problem

The dynamic plan mechanism knows *that* a plan exists and *when* it is stale.
It does not answer, for one plan item:

- what is already accumulated in claims for this item, and how it relates to
  work already done;
- how the item relates to the original task through the tree to the root,
  step by step;
- where the current position deviates from the line (for example
  software → interface → welcome page);
- what to search to shift the activity vector, and which claim would close the
  gap.

`dom_task_claims` is an inventory. `dom_task_graph` is a history. Neither is a
decision view. `dom_task_brief` is the third read of the same material.

## 2. Baseline decisions

- The brief is a read view, not a new source of truth. `task_claims` =
  inventory, `task_graph` = history, `brief` = decision. One internal
  `_item_state(c, ids)` gather feeds all three; the public shapes of the
  existing tools stay stable.
- Nothing is stored: `line`, `gaps`, `search`, `vector` and `next` are
  computed at read time. The same database state produces the same JSON
  (except `read_snapshot`). A stored deviation would go stale and pollute the
  event chain.
- `gaps` is a closed vocabulary of codes with `severity`, `why`, `closes_by`
  and `scope`; no free text. `closes_by` is the bridge to `search`.
- `gaps` stays structural, but the `expects` comparison is laid in from the
  start (§8).
- A new read-only tool `dom_task_brief(task, include_children=False,
  limit=20)`. Do not extend `task_claims` or `task_graph`.
- The hook injects one pointer line, not the whole brief; depth is pulled by
  the tool. Deduplication uses the existing `plan_checks` log.
- User statements are captured as a source quote plus a distilled comment; the
  source is always kept.
- The deterministic extractive distillate is v1. Agent improvement is a hard
  chain: `propose → validate (V1–V8) → accept`. States and the effective
  version are computed at read time; a proposal never displaces an accepted
  version before acceptance (§12.5–§12.9).
- The validator is deterministic and lives in the tool, not in the hook. The
  source is ground truth; no unvalidated version is effective.

## 3. The three views

| View | Question | Tool |
|---|---|---|
| Inventory | what is linked to the item, by role? | `dom_task_claims` |
| History | who changed what, when, in which version? | `dom_task_graph` |
| Decision | where are we, what is missing, what next? | `dom_task_brief` |

## 4. Contract

`dom_task_brief(task, include_children=False, limit=20)` → `TASK_BRIEF`.

- `task`: plan item id, path (`П1.2`) or branch code; resolved by
  `planning.resolve_task`.
- `include_children`: rollup counts for the subtree; no full child lists.
- `limit`: maximum claims per role in `accumulated` (default 20).
- Read-only, lite-only, `scope`, `ready`, `integrity_gate`,
  `public_dto.read_snapshot`.
- Refusals: `TASK_NOT_FOUND`, `TASK_ARCHIVED`, `PROFILE_STRICT`.

Response:

```
{status, task{...}, root{request_preview, goal_preview, goal_confirmed},
 line[...], accumulated{...}, gaps[...], search[...], vector{...}, next[...],
 read_snapshot}
```

## 5. line

Source: the `current_plan_tasks` row from `resolve_task`, `plan_branches`
(branch, level), a parent walk via `parent_id`. Per level: `_header_claim`,
`_properties(header)` → `context`, `_claims_by_plan_ref` + `task_claims` →
counts. Root anchor: `project_root` (`request_claim_id`, `goal_claim_id`,
`goal_confirmed`).

Per-level shape:

```
{path, plan_task_id, branch, level, title, status,
 context: {text, present, source: 'discussion|agent'},
 body, claims: {total, current, by_role, by_type, stale},
 flags: [codes]}
```

- `context` is the `context` property of the header claim, not a plan field.
  It does not enter the revision and does not make the plan stale (2a+2b).
  `body` is the plan item's description; do not conflate the two.
- Three levels (software → interface → welcome page) are three array elements.
  Flags are computed per level, not only at the leaf.
- Cost: one tree query, one `_claims_by_plan_ref` for all line ids, one
  `task_claims` for all ids, N `_properties` (N = depth, cap 12), one read
  transaction.
- Edges: a flat plan gives one level (not an error, but parent/child flags
  cannot fire); an archived item → `TASK_ARCHIVED`; several header claims after
  a rename → the latest and `_current`, with `header_versions` in
  `accumulated`.

## 6. accumulated

`task_claims` already returns entries with `link_stale`, `is_current`,
`version`, `author` and `preview`. `task_graph` returns version chains and
edges. The brief adds:

- `by_role` summary: count, stale, last change;
- `last_change`: the freshest admitted claim, its role, type, author, time and
  preview;
- `open_conflicts`: `conflict_ref` values without a `resolve` in the item's
  branches — the same add/discard logic as `projections.snapshot`, but in one
  query over the item's branches;
- `variants`: from `variant_of` / `resolve_claim`;
- `header`: the current header claim, its version and `context`;
- `orphaned_links`: links to archived items;
- `statements`: the item's claims whose `basis` cites a statement source or
  distillate (§12.12), rolled up to `{source_id, distillate_id, cited_version,
  state, effective_version, disputed}`; cap 10, ordered by source id. A
  statement enters the brief through an item claim that cites it, not through
  a global scan.

`defines` is excluded from `by_role`: the header is the plan mirror, not work.
`stale` is counted by `logical_claim_id`, otherwise several link rows for the
versions of one claim would double-count. `defines` is also excluded from the
stale count: after a body change the old header link row keeps the old
semantic digest, and counting it would mark every updated item as
`STALE_LINKS` forever. Only non-header links can be stale.

Key subtlety: `link_stale` means "the item changed after the link", not "the
claim is stale". `semantic_digest` covers title, body, parent_id and purpose,
but **not status**. A status change does not mark links stale — that is
correct, but the flag must be named explicitly, or the agent will misread it.

Children are excluded by default: direction 1 is "what is accumulated for the
item". Children are an optional `include_children` rollup, not a full list. The
full list remains `dom_task_claims(include_children=true)`.

## 7. gaps

A closed table of codes. Each code has `{code, severity, why, closes_by,
scope}`. It is computed at every level of the line.

| Code | When | Severity | Closed by |
|---|---|---|---|
| `OPEN_CONFLICT` | a `conflict_ref` without `resolve` | high | `dom_reconcile` / resolve |
| `STALE_LINKS` | `link_stale > 0` | high | re-read the item, update/relink |
| `PARENT_DONE_CHILD_OPEN` | parent done, item open | high | reopen the parent or close the item |
| `NO_IMPLEMENTS` | 0 current role=implements | high if in_progress/done, medium if pending | decision/code with role=implements |
| `NO_VALIDATES` | 0 current role=validates | high if done, medium otherwise | data/quote/text(factual) with role=validates |
| `NO_CONTEXT` | no context and empty body | medium | user answer → `dom_plan_ingest(contexts=...)` |
| `EMPTY_ITEM` | 0 claims, no context, no body | medium | context or split the item |
| `ORPHAN_LINK` | a link to an archived item | medium | relink to the current item |
| `PARENT_OPEN_CHILD_DONE` | parent open, item done | medium | check whether the parent's product is assembled |
| `CLAIMS_BUT_PENDING` | status pending, claims exist | low | update the plan status or mark exploratory |
| `GOAL_UNCONFIRMED` | `goal_confirmed=false`, root only | low | customer confirmation |
| `SUBTREE_OPEN` | open children | info | do not close the parent before the children |
| `EXPECTS_MISSING` | no `expects` property | medium | declare `expects` (§8) |
| `EXPECTS_UNMET` | a declared role/type has no current claim | high if done, medium otherwise | add the missing role/type |
| `NO_DISTILLATE` | a captured statement has no distillate | medium | `dom_distillate propose` |
| `DISTILLATE_EXTRACTIVE` | the effective distillate is the mechanical extract | low | optional: `dom_distillate propose`; the source stays |
| `DISTILLATE_PENDING` | the current version is proposed/validated, not accepted | medium | `dom_distillate validate` / `accept` / `withdraw` |
| `DISTILLATE_REJECTED` | the latest review failed | medium | propose a corrected version |
| `DISTILLATE_DISPUTED` | an open conflict on a distillate version | high | `dom_distillate resolve` after a corrected version |
| `DISTILLATE_LOOP` | three failed reviews for one logical claim | high | stop and ask; a new user instruction is a new source |
| `DISTILLATE_VARIANT_CONFLICT` | two accepted variants of one source | high | withdraw one, then resolve |
| `STATEMENT_DISPUTED` | an open conflict on the source itself | high | user confirmation or a new source |

`severity` is a constant table, not a model. Tuning it is a product decision;
tests pin it. `closes_by` names the role and type that would close the gap;
without it, search objects would be generic words.

Why a closed vocabulary: reproducibility, testability and hook deduplication by
`digest(codes)`. Free text would break all three.

## 8. expects

`expects` is a property of the header claim, like `context`. Its value is a
JSON string:

```
{"artifact": "what must exist when the item is done",
 "evidence": ["data", "quote"],
 "roles": ["implements", "validates"],
 "notes": "optional"}
```

- `EXPECTS_MISSING` (medium): no `expects` property.
- `EXPECTS_UNMET` (high if done, medium otherwise): a role or type named in
  `expects` has no current claim; `missing` lists them.
- `artifact` is not machine-verifiable. The brief shows it and asks the agent
  to confirm; the engine checks roles and types, not semantics. This is the
  honest limit.
- Set it with `dom_annotate(kind='property', name='expects', value=<json>)`.
  `dom_plan_ingest` JSON items may accept `expects` later; that parser change
  is not part of the first slice.
- The schema is documented here and referenced from the plan item П4.3.

## 9. search

A pure generator: `(line, accumulated, gaps) → [objects]`. The query string is
built from the title (`_clean_title`), 1–2 informative context sentences (reuse
the `plan_context` splitter), `anatomy.keywords` of current claims, ancestor
titles for disambiguation, and `requirement_refs`.

Templates are tied to the gap code and the expected type:

- `NO_IMPLEMENTS` → `"{title} implementation approach"` (repo),
  `"{title} architecture decision"` (web);
- `NO_VALIDATES` → `"{title} acceptance criteria"`, `"{title} test plan"`
  (web/repo);
- `NO_CONTEXT` → `"{title} purpose"`, scope `user` — a question to the
  customer, not a web query;
- `STALE_LINKS` → scope `memory`, the current title, action "re-read";
- `OPEN_CONFLICT` → scope `memory`, the `conflict_ref`, action
  `dom_reconcile`;
- `STATEMENT_DISPUTED` / `DISTILLATE_DISPUTED` → scope `memory` (read the
  source and the version chain) or `user` (ask), action `dom_distillate
  resolve`;
- `DISTILLATE_PENDING` / `DISTILLATE_REJECTED` → scope `memory`, the
  statement id, action `dom_distillate`.

Object shape:

```
{id, query, scope: 'memory|repo|web|user',
 why: 'NO_IMPLEMENTS on П1.2.1',
 closes: [{role: 'implements', type: 'decision'}],
 expect: 'decision with rationale',
 priority}
```

Cap 7, deduplication by normalized query, order: gap severity → depth (deeper
first) → template id. No embeddings, no randomness: one database state gives
one list.

The four scopes are the honest answer to "where to look": the engine does not
search the web itself, but it says where the answer should come from — the
project memory, the code, the web, or the user.

The loop closes: object → agent searches → records a claim of the required
role and type with `source_ref`/`evidence` → the next brief sees that `closes`
is satisfied → the gap disappears. If the agent records the wrong role, the gap
stays. That is the enforcement.

## 10. vector

A pure function over `gaps` and `line`. The priority is a total order:

1. `OPEN_CONFLICT` — the memory is contradictory;
2. `STATEMENT_DISPUTED` / `DISTILLATE_DISPUTED` / `DISTILLATE_LOOP` /
   `DISTILLATE_VARIANT_CONFLICT` — the item relies on a contested or blocked
   statement;
3. `STALE_LINKS` — the brief may rest on an old wording;
4. `PARENT_DONE_CHILD_OPEN` — the plan is contradictory;
5. `NO_IMPLEMENTS` when done/in_progress;
6. `NO_VALIDATES` when done;
7. `NO_CONTEXT`;
8. `DISTILLATE_PENDING` / `DISTILLATE_REJECTED` / `NO_DISTILLATE` /
   `CLAIMS_BUT_PENDING` / `PARENT_OPEN_CHILD_DONE`;
9. `DISTILLATE_EXTRACTIVE` / `GOAL_UNCONFIRMED`;
10. all clear → move up to the parent or to the next open item.

The order is not arbitrary: make the memory trustworthy, then the plan
consistent, then close the work, then the "why", then housekeeping. The vector
must not say "add more documents" while links are stale.

Shape:

```
{focus: 'П1.2.1', code: 'NO_IMPLEMENTS',
 action: 'На П1.2.1 есть 4 documents, но нет implements. Запиши решение или код, который реализует пункт.',
 because: {documents: 4, implements: 0, stale: 2},
 then: 'NO_VALIDATES',
 stop: 'status=done AND validates>=1 AND stale=0 AND conflicts=0 AND no disputed/blocked statement',
 up: 'П1.2 откроется, когда лист закрыт'}
```

`stop` is the success contract. The engine cannot verify that the product
exists, but it can require a done declaration plus an evidence claim. That is
the closest deterministic proxy for "get the product".

`vector` is one line, not a list. The list is `gaps`; `vector` is the top of
the order.

## 11. next

Derived from `vector` and `search`, cap 3–5: `dom_find` for scope=memory,
`dom_claim` with the exact type/role and required fields (placeholders, not
invented text), `dom_update` with `expected_current` for stale,
`dom_reconcile` for conflicts, `dom_plan_ingest(contexts=...)` for missing
context, `dom_task_brief` for the parent after closing. The existing
`next_steps` pattern is reused.

## 12. Statement capture

### 12.1 Problem

User statements are scattered in prompts and may be disputable. The memory
needs the original text and a distilled comment; the source must be kept for
comparison and verification. This is the same principle as the plan: the plan
file is the source, the per-item context is the distillate. Statements need a
more branched version because a statement can be challenged.

### 12.2 Two claims

- **Source:** `quote`, text verbatim, `source_ref = session:<id>`,
  `locator = prompt:<sha256(normalized prompt)[:12]>`,
  `source_snapshot_digest` derived by the server. The locator is a stable
  prompt identity, not a counter the hook would have to remember.
- **Distillate:** `text`, `classification = interpretation`,
  `basis = [source_id]`, `purpose = distilled comment`. The text is exactly
  the selected source sentences joined by a space; the display marker
  ("User states:") is added by the reader and is not part of the claim.
- `attribution` and `origin_version` are fixed at the first admission and
  describe the source lineage (`stmt-extract-v1`, deterministic). A
  `dom_update` version cannot rewrite them, so they are **not** the pipeline
  discriminator. The per-version origin is the `basis_spans.origin` property
  (`extract|agent`); it is written for every version and is authoritative.
- The source is immutable. The distillate is a version chain: a new version
  is a new claim version, never an edit. It can be disputed, withdrawn or
  superseded; forks are not used for statements in v1 (§12.8).
  `dom_context(distillate)` shows the source in `basis`, the version
  properties and the plan chain in `refines`.

### 12.3 Capture filter

- Markers are data, not code: a versioned marker pack (`marker_pack = "v1"`)
  lists strong markers ("запомни", "запиши", "важно", "это факт", "я
  утверждаю", "наше правило", "не так", "ошибка", "должно быть", "всегда",
  "никогда", "потому что", "значит"), decision/requirement markers ("делаем",
  "решили", "фиксируем", "согласен", "подтверждаю", "требование", "правило",
  "нельзя", "обязательно", "лучше сделать", "базлайн") and noise markers
  (pure commands: "сделай", "покажи", "запусти"). Appending words is a
  release change, and the pack digest is recorded on the source claim as the
  `capture_pack` property, so "why was this captured" stays answerable after
  the pack grows.
- Marker matching is stem-based with the `plan_context._same` prefix matcher:
  "решили / решаем / решение" match one stem. Exact word forms are not
  required, and new inflections of a known stem are caught without a pack
  change.
- Scoring: strong or decision marker = 2; a declarative sentence with a
  project noun (plan titles, claim keywords) = 1; noise-only = −3. Capture
  at ≥ 2, and a marker must be supported: a lone "важно" without a project
  noun and without a second marker does not capture — generic emphasis is
  the main noise source. An explicit request ("запомни", "запиши") captures
  alone, so a false negative of the dictionary is always cheap to repair.
- A prompt that contains a decision marker is captured even if it also
  contains a command: the decision is the claim, the command is the action.
- Capture the whole prompt as the source; the distillate selects 1–3
  declarative sentences. This keeps the source verifiable in context.
- Splitter hardening (shared with §12.6): no split after a single-letter
  abbreviation ("т.", "Л."), after a digit decimal point ("3.14"), inside a
  fenced code block or a URL; list markers and Markdown headings are unit
  separators.
- Deduplication: `idempotency_key = stmt:<session>:<sha256(normalized
  prompt)>`; the server returns `ALREADY_RECORDED`.
- Cap: one source per prompt. No per-session cap is enforced in 0.2.4: the
  plan's `M` was never defined, so the limit is documented here instead of
  guessed in code. Deduplication by the normalized key remains the only
  suppression. Failures never block the prompt.

### 12.4 Deterministic extractive distillate (v1)

- Extractive, not abstractive: the top 1–3 declarative sentences by score,
  joined by a space; keywords come from the selected sentences. The display
  marker ("Пользователь утверждает:" / "User states:") is added by the reader,
  not stored in the claim text.
- Anchors: one entry per selected source sentence, with its index and resolved
  verbatim span, stored as the `basis_spans` property on the distillate
  version:
  `{"v":1,"origin":"extract","candidate_digest":"<sha256>","sentences":[{"i":2,"anchors":[{"quote":"...","start":41,"end":73}]}]}`.
  The same shape is used for agent versions, so the anchor check is one code
  path.
- `classification = interpretation`, `attribution = deterministic`,
  `origin_version = stmt-extract-v1`; no inferred assumptions.
- The extractive version is the baseline and the fallback. It is usable
  without review because every sentence is copied from the source. The hook
  writes the source and the v1 as one two-item claims package; both properties
  (`capture_pack` on the source, `basis_spans` on the v1) go into one following
  append transaction. If a transaction fails, the prompt is not blocked; a
  repeat of the same statement repairs a missing property (§12.9).
- While it is effective, `DISTILLATE_EXTRACTIVE` (low) is reported. Improvement
  is optional.
- If no sentence qualifies, the capture itself returns `NO_STATEMENT` and no
  source is written. A captured source always gets a v1 in 0.2.4, so the
  source-only / `NO_DISTILLATE` (medium) path is not implemented; the §7 health
  code stays reserved for a future source-only capture.

### 12.5 The improvement pipeline (hard chain)

The agent may improve the extractive distillate, but only through this chain.
The chain is append-only, and every gate is computed from the ledger, not
remembered by the agent. No step edits the source or an earlier version.

```
source (quote, immutable)
  -> extract (deterministic v1 + anchors)        hook
  -> propose (agent text + anchors)               agent
  -> validate (V1-V8, deterministic)              dom_distillate
  -> accept (review + candidate digests)          agent or user
  -> effective (computed at read time)            any reader

propose/validated/accepted -> withdraw            agent or user
any version -> dispute -> resolve                 agent or user
user correction -> new source, old chain archived user
```

Actors:

- **hook**: capture and extract only. No model, no network, never blocks the
  prompt.
- **agent**: `propose`, `validate`, `accept` (`by=agent`), `withdraw`,
  `dispute`, `resolve`.
- **user**: `accept` (`by=user`), `withdraw`, `dispute`, `resolve`, and
  corrections (a correction is a new source, not an edit).
- `by` is recorded, not verified: lite is a single-owner profile. A forged
  `by=user` is an audit finding, not a security boundary.

States of one distillate version, computed at read time:

| State | Condition | Effective? |
|---|---|---|
| `extracted` | v1 with `basis_spans.origin=extract` | yes, fallback |
| `proposed` | agent version, no review | no |
| `validated` | review pass, no accept | no |
| `accepted` | review pass + accept matching | yes |
| `rejected` | latest review fail | no |
| `withdrawn` | withdraw property | no |
| `disputed` | open conflict on this version | no |
| `superseded` | archived, or the source was replaced | no |

The state of a version is computed independently of whether it is current. A
non-current accepted version can still be the effective fallback. The
precedence is: `superseded` (archived/source replaced), `withdrawn`,
`disputed`, `accepted`, `validated`, `rejected`, `extracted`, `proposed`.

Effective version:

```
effective(source):
  if an open conflict exists on the source: none (source only)
  for v in versions newest first:
    if state(v) == accepted and no open conflict on v: return v
  if v1 exists and no open conflict on v1: return v1 (extracted)
  return none (source only)
```

The current version may be `proposed`, `rejected` or `withdrawn` while an older
accepted version stays effective. That is the built-in rollback: a proposal
never displaces the accepted text until it passes validation and acceptance.

Transitions and refusals:

```
propose always appends a new version; it never changes an old one.
new version --validate(pass)--> validated
new version --validate(fail)--> rejected
validated --accept--> accepted
accepted --propose--> a new proposed version; the old accepted stays effective
any non-v1 --withdraw--> withdrawn
any --dispute--> disputed
disputed --resolve--> the state computed without the conflict
source correction --> old chain superseded
```

- `STATEMENT_NOT_FOUND`: the id is not a captured statement source or a
  distillate version of one, or the source is archived (readable with `show`,
  but closed to new versions and reviews). The capture-side status
  `NO_STATEMENT` is not a refusal and is never returned by `dom_distillate`.
- `PROPOSAL_INVALID`: schema/provenance/anchor shape fails; nothing is
  written.
- `PROPOSAL_CONFLICT`: the current version has the same normalized text but
  different anchors; withdraw it or supply the original anchors. This
  prevents two indistinguishable versions of the same text.
- `SPAN_NOT_FOUND`, `SPAN_AMBIGUOUS`: an anchor is not a unique verbatim
  substring of the source.
- `NO_IMPROVEMENT`: the candidate's normalized text equals the effective
  version's normalized text (§12.6).
- `REVIEW_REQUIRED`, `REVIEW_FAILED`, `REVIEW_STALE`: acceptance without a
  matching passing review for this exact version.
- `DISTILLATE_DISPUTED`: an open conflict on the source or the candidate.
- `DISTILLATE_LOOP`: three failed reviews for this logical claim.
- `NOT_CURRENT`: `validate`/`accept`/`withdraw` on an old version; read
  `show` and act on the current version.

Tool surface:

```
dom_distillate(action, source?, distillate?, text?, anchors?, by?, note?)
  action: show | propose | validate | accept | withdraw | dispute | resolve
  anchors: [["verbatim quote", ...], ...]   one inner list per candidate
                                            sentence, in order
```

- `show` is read-only: the source, version chain, states, effective version,
  open conflicts, failed checks and next actions. It requires `source` or
  `distillate`.
- The write actions are lite-only, `scope`, `ready`, `integrity_gate`.
- `source` and `distillate` accept claim ids; `distillate` defaults to the
  logical distillate of the source.
- `propose` resolves each quote to a `{quote,start,end}` span, writes a new
  version and its `basis_spans` property. If the property write fails, the
  version exists without anchors and validation fails V3; a retry with the
  same text repairs the property (same text, different anchors is
  `PROPOSAL_CONFLICT`).
- `validate` acts on the current version only; `accept` and `withdraw` too.
- The hook never calls this tool.

### 12.6 Checkpoints V1–V8

All checks are deterministic Python, shared by the tool and the tests. No
model, no network, no randomness. Each check returns `pass` or a failure code
with a short deterministic detail.

| Check | Rule | Failure |
|---|---|---|
| V1 SOURCE | the source exists, is a current `quote`, is not archived; `sha256(source text UTF-8)` matches `source_snapshot_digest`; the candidate `basis` is exactly `[source_id]` | `V1_SOURCE` |
| V2 PROVENANCE | candidate is `text`, `classification=interpretation`, `purpose` non-empty, `basis_spans.origin=agent`; it is the current version of the logical distillate | `V2_PROVENANCE` |
| V3 ANCHORS | one anchor group per candidate sentence; 1–3 sentences; each anchor is a non-empty verbatim substring of the source; a quote that occurs more than once is refused with `SPAN_AMBIGUOUS` at `propose` time; stored offsets must satisfy `source[start:end] == quote` (a repeated sentence gets distinct offsets, so stored spans never need the once-rule); `candidate_digest` matches the candidate text | `V3_ANCHORS` |
| V4 GROUNDING | per sentence, content tokens (the `plan_context` tokenizer and `_same` inflection match) against the anchored quotes: `novel <= 2` and `coverage >= 0.6`; if the sentence has ≤ 3 content tokens, `novel = 0` | `V4_GROUNDING` |
| V5 FACTS | per sentence: every number surface appears in the anchor (digit tokens as written; word numerals only from the explicit `v1` surface pack, so `10` vs `десять` fails and ordinary words like `и` are ignored); every non-initial capitalized token appears in the source case-insensitively; negation markers and quantifiers (`always/never/only/всегда/никогда/только`) have equal counts in the sentence and the anchor | `V5_FACTS` |
| V6 COMPRESSION | 1–3 sentences, each ≤ 300 chars; total ≤ 60% of the source when the source is ≥ 200 chars, otherwise ≤ the source; normalized text differs from the effective version's text | `V6_COMPRESSION` |
| V7 DISPUTE | no open conflict on the source or the candidate | `V7_DISPUTE` |
| V8 LOOP | fewer than three failed reviews for this logical claim | `V8_LOOP` |

Notes:

- Sentence splitting is shared by the extractor, the anchor check and the
  candidate: normalize CRLF, apply the splitter hardening rules of §12.3,
  split on `(?<=[.!?…])\s+`, drop empty fragments, preserve order. The
  sentence index is stable for a given source text and pack.
- "Normalized text" in V6 and in the idempotency keys means:
  `re.sub(r'\s+', ' ', text).strip().casefold()`. A whitespace- or
  case-only change is the same text.
- V4 is a grounding proxy, not semantic equivalence. The agent may compress,
  reorder and lightly rephrase inside the source vocabulary; it cannot
  introduce facts. Two novel content words per sentence is the allowance for
  connectives and normalisation.
- V5 is the safety net for disputable statements: dropping "не", changing
  "всегда" to "иногда" or inventing a number is a hard failure.
- V6 stops a "distillate" that is longer than the source and a no-op rewrite.
- V7 is checked again at acceptance: a dispute opened after validation blocks
  the accept.
- V8 is a gate, not a quality check. After three failures the chain stops; a
  new user instruction is a new source, and a new source starts a new chain.

### 12.7 Review, acceptance and computed state

Three properties on the candidate version carry the chain. Properties are
per-version: a new version starts with none, so an old acceptance cannot leak
into it.

`basis_spans` (written by `propose`):

```
{"v":1,"origin":"agent","candidate_digest":"<sha256(text)>",
 "sentences":[{"i":0,"anchors":[{"quote":"...","start":12,"end":40}]},
              {"i":1,"anchors":[...]}]}
```

`distillate_review` (written by `validate`):

```
{"v":1,"validator":"dom-distillate/1","at":"<iso8601>","source_id":"...",
 "source_digest":"...","candidate_digest":"...","anchors_digest":"...",
 "checks":{"V1":"pass",...,"V8":"pass"},"failed":["V5_FACTS"],
 "detail":{"V5_FACTS":{"sentence":1,"novel":["всегда"]}},
 "verdict":"pass|fail"}
```

- `failed` lists failure codes; it is empty exactly when `verdict = "pass"`.
- `detail` maps each failed code to a small deterministic detail object
  (sentence index, offending tokens). It is for the agent to fix the text,
  not for a human to read.

`distillate_accept` (written by `accept`):

```
{"v":1,"candidate_digest":"...","review_digest":"...",
 "accepted_by":"agent|user","note":"..."}
```

- `candidate_digest = sha256(candidate text)`.
- `anchors_digest = sha256(canonical JSON of basis_spans without its own
  candidate_digest field)`.
- `review_digest = sha256(canonical JSON of the review without `at`)`; `at` is
  stored for audit but excluded from the decision.
- `accept` requires: a review on this version with `verdict=pass`,
  `candidate_digest` equal to the text hash, `anchors_digest` equal to the
  current `basis_spans`, no open conflict, and fewer than three failures.
- A new version invalidates both records automatically: its text hash differs,
  and it has no properties. That is why acceptance is version-bound.
- `show` recomputes the state from the ledger. Nothing is stored as a mutable
  status.

### 12.8 Dispute, withdraw, rollback

- **Dispute**: `dom_distillate(action='dispute')` writes a `conflict` event
  with `conflict_ref = stmt:<source_id>` and a required reason. The target is
  the source when only `source` is given, and the current distillate version
  otherwise. A conflict on the source makes every distillate unusable
  (`STATEMENT_DISPUTED`, high); a conflict on a version skips that version
  (`DISTILLATE_DISPUTED`, high) and the effective version falls back.
- **Counter-claim**: a new version of the same logical distillate with the
  corrected text and anchors. Do not fork a statement in v1; a fork that
  produces two accepted variants is reported as `DISTILLATE_VARIANT_CONFLICT`
  (high). This is a §7 health code, not a `dom_distillate` refusal: v1 has no
  converge action for it.
- **Resolve**: `dom_distillate(action='resolve')` writes a `resolve` event
  with the same `conflict_ref` on the same target, after the correction is
  accepted (or after the user confirms the source). Resolution does not edit
  either text.
- **Withdraw**: `dom_distillate(action='withdraw')` writes a
  `distillate_withdraw` property on the current version with a reason. The
  version is not effective; the previous accepted version (or v1) is. A
  withdrawn version stays in history and can be superseded by a new version.
- **Rollback**: a proposal never displaces the effective version until it is
  accepted. Withdrawing the proposal is therefore a fallback, not a rewrite.
- **User correction**: a correction is a new source claim (a new quote). The
  old logical distillate is archived with reason `superseded_by:<new_source>`.
  The old source and all versions stay in the ledger. This is the only way to
  change what the user said.
- **Loop stop**: after three failed reviews for one logical claim,
  `DISTILLATE_LOOP` (high) is reported and `propose`/`validate` refuse. The
  agent stops and asks; the user's next instruction is captured as a new
  source, which starts a new chain. There is no reset action.

### 12.9 Idempotency and stop conditions

- `capture`: `stmt:<session>:<sha256(normalized prompt)>`; the stored source
  stays the prompt as received, the key ignores whitespace/case (§12.6).
- `propose`: keyed on `source_id + candidate_digest + anchors_digest`.
  A retry with the same text and anchors returns the existing version
  (`ALREADY_RECORDED`); if that version lost its `basis_spans` property, the
  retry appends the property and reports the repair. The same source with a
  different text is a new version; the same text with different anchors is
  `PROPOSAL_CONFLICT`, not a new version.
- `validate`: `stmt-validate:<candidate_digest>:<anchors_digest>:
  <source_digest>:<validator_version>`. A retry returns the existing review;
  an anchor repair changes `anchors_digest`, so the next `validate` re-runs
  instead of returning the stale review.
- `accept`: `stmt-accept:<candidate_digest>:<review_digest>:<accepted_by>`.
  `note` is audit text and is not part of the key: a retry with another note
  returns the existing acceptance.
- `withdraw`, `dispute`, `resolve`: the annotation event is content-addressed;
  an identical retry is `ALREADY_RECORDED`.
- Stop conditions: `accepted` (use it); `DISTILLATE_LOOP` (ask the user);
  `STATEMENT_DISPUTED` (source only); source archived (stop, use the new
  source); three failed reviews (stop).
- Every action returns the state, the effective version and `next_steps`, so
  the agent does not guess the next gate.

### 12.10 Verification

- `dom_context(distillate)` returns the source in `basis`, the properties
  (`basis_spans`, `distillate_review`, `distillate_accept`) and the plan chain.
- `dom_distillate(action='show')` returns the version chain, the state of each
  version, the effective version and the open conflicts.
- `source_snapshot_digest` is recomputed from the source text; a mismatch is a
  hard failure (V1) and an integrity finding.
- The source text is byte-identical to the prompt. `dom_find` searches both
  the source and the distillate.
- A reader can always answer: which text is effective, which review accepted
  it, and which source sentence each part came from.

### 12.11 Hook flow

- On `UserPromptSubmit`: parse the prompt and session; autostart if needed;
  `capture_statement`; then the plan status / brief pointer.
- `capture_statement`: filter → write the source and the extractive distillate
  as one two-item claims package; both properties (`capture_pack` on the
  source, `basis_spans` on the v1) go into one following append transaction
  (if any sentence qualifies) → one line on stdout when a new source is
  admitted. A repeat of the same statement repairs a missing property (§12.9).
- The line: `[DOM project memory] statement <source_id>; distillate
  <distillate_id> (extracted). The source is ground truth; improve it with
  dom_distillate(action="propose", source="<source_id>", ...).`
- Store on `main`, no `plan_task`. The agent associates a statement with an
  item when it uses it (§12.12).
- The hook never calls `dom_distillate`, never calls a model, and never
  blocks; errors go to stderr.

### 12.12 Association with plan items

- A captured statement is global: the source and the distillate live on
  `main`.
- Detection (used by the brief): a statement source is a current `quote`
  whose `source_ref` starts with `session:` and whose `locator` starts with
  `prompt:`; a distillate is a current `text` whose `basis` is exactly one
  such source. Any other quote is an ordinary quote and is not rolled up.
- To use a statement for an item, the agent records an item-level claim
  (`decision` or `text`) with `plan_task` and `basis = [source_id,
  effective_distillate_id]`. The brief sees that claim in `accumulated`, and
  `dom_context` walks it back to the source.
- The item-level claim is not a copy of the statement for decision purposes;
  it is the item's conclusion, with the statement as evidence. If the
  distillate changes, the item-level claim is updated or a new version is
  written; until then the brief shows `cited_version` next to
  `effective_version`, so a drift is visible without a gap code.
- v1 does not auto-link a statement to the last touched item: a wrong link is
  worse than no link. The hook's pointer names the last touched item; the
  association remains an explicit act.

### 12.13 Limits

- V4/V5 are deterministic proxies for fidelity; they cannot prove that a
  paraphrase means the same thing. The agent is constrained to the source
  vocabulary; a true semantic rewrite is out of scope.
- `by=user` is declared, not authenticated in lite.
- The source is immutable; a correction is a new source, so the ledger keeps
  both versions of what the user said.
- The extractive v1 is a baseline, not a summary; its quality depends on the
  sentence splitter and the filter.
- The chain does not run in the hook; a statement can sit in `extracted`
  forever without harming the project.

### 12.14 Brittleness of the deterministic layer and noise control

The deterministic layer is deliberately simple, and simplicity has known
failure modes. They are listed here with their worst case and why they are
acceptable — a brittleness that costs at most a missed statement or a noisy
capture, never a wrong fact, is acceptable for a ground-truth-first design.

| Brittle point | Failure mode | Worst case | Mitigation |
|---|---|---|---|
| Marker dictionary | synonym, idiom, English phrase, negated marker not in the pack | missed statement (false negative) | stem matching covers inflections; an explicit "запомни" captures alone; the agent can always record a quote manually |
| Marker dictionary | generic emphasis ("важно") in a greeting or side remark | noisy capture (false positive) | a marker must be supported by a project noun or a second marker |
| Sentence splitter | abbreviations, decimals, code, URLs split wrong | wrong sentence boundaries; distillate picks fragments | hardening rules (§12.3); each rule is a pinned test |
| Extractor score | among several declaratives it picks the wrong one | weak v1 wording | v1 is only a fallback (`DISTILLATE_EXTRACTIVE`, low); improvement is the pipeline's purpose |
| V3 anchors | repeated phrase in the source → `SPAN_AMBIGUOUS`; whitespace-normalized quote → `SPAN_NOT_FOUND` | proposal refused | the agent extends the quote to uniqueness; offsets, not indices, are the disambiguator |
| V4 grounding | legitimate synonym substitution ("быстро"→"оперативно") | `V4_GROUNDING` fail | intended: the agent stays in the source vocabulary; a true paraphrase is out of scope (§12.13) |
| V5 facts | word-numbers ("десять" vs "10"), dates, double negation | `V5_FACTS` fail | intended: numbers and negation parity are the safety net for a disputable statement |
| V6 compression | reordering-only improvements below 60% of the source | `NO_IMPROVEMENT` | reordering that does not change the normalized text is a no-op by definition |

Two noise valves bound the cost of every case above: the source is stored
verbatim regardless of the distillate quality, and every gap severity is
constant (§7), so a bad extraction degrades the brief's comfort, never its
correctness.

Ways to improve extraction further, with their trade-offs:

| Approach | Robustness | Cost | Verdict |
|---|---|---|---|
| Extendable marker pack | low–medium, still lexical | near zero | v1 base (this slice); pack is versioned data, growth is a release change |
| Agent-side dynamic analysis: a skill that recognizes user phrases and extracts instructions through iterative checklist runs and independent distillations, aggregated tribunal-style into a fixed-format output | high | agent tokens per improvement | v2 (a separate plan item); it produces `propose` inputs, and V1–V8 remain the hard gate |
| Brainstorming-style multi-draft understanding of the phrase | high, diminishing returns above ~3 drafts | higher token cost | a mode of the v2 skill (`drafts` parameter), not a separate mechanism |

The agent-side skill (v2) sketch: generate candidate distillations
independently, critique each against a fixed checklist (grounding, facts,
tone, completeness), aggregate the critiques tribunal-style into one
proposal in the exact `propose` schema (`text` + `anchors`), then submit it
to `dom_distillate(action='propose')`. The LLM lives in the agent layer;
the ledger sees only a proposal, and the deterministic validator decides.
No new storage, no new trust: a bad proposal fails V4/V5 and is recorded as
`rejected`, which is the audit trail.

## 13. Hook integration

- `plan_status` (or a separate `brief_pointer`) returns one line when: there is
  a plan, it is not closed, the target item has a gap of severity ≥ medium, and
  `digest(codes)` was not injected before.
- Target: the last admitted non-header claim with `anatomy.plan_ref`; fallback
  the first `in_progress` by path; fallback the first open leaf.
- Deduplication: `plan_checks` `kind='brief'`, `note = plan_task_id + ':' +
  digest(codes)`.
- Text: `[DOM project memory] П1.2.1: 4 documents, 0 implements, 2 stale.
  dom_task_brief(task="П1.2.1") before the next step.`
- Depth is pull; the hook never prints the full brief.

## 14. Determinism and tests

- `check_task_brief.py`: a three-level plan, only documents →
  `NO_IMPLEMENTS` and `NO_VALIDATES`; an implements claim closes the gap and
  the vector moves to validates; done without validates → high; a rename →
  `STALE_LINKS`; two runs on one database give the same JSON except
  `read_snapshot`.
- `check_statement_capture.py`: a declarative assertion → source + distillate;
  the source is byte-identical; `basis` points to the source; the v1 carries
  `basis_spans` with resolved offsets; a question, greeting or command → no
  claim; a lone "важно" without a project noun → no claim; "запомни" →
  captured alone; inflected markers ("решили"/"решаем") → one stem; "3.14"
  and "т." do not split a sentence; the `capture_pack` digest is on the
  source; a duplicate → `ALREADY_RECORDED`; a dispute → a conflict event and
  `STATEMENT_DISPUTED`; an update → a new distillate version and an unchanged
  source; `dom_context` returns the source and the
  properties.
- `check_distillate_pipeline.py`: v1 extracted → effective v1; propose →
  proposed, effective still v1; validate pass → validated; accept → accepted,
  effective v2; propose v3 → effective v2 (rollback); validate fail on a
  dropped negation or an invented number → rejected; three failures →
  `DISTILLATE_LOOP`; withdraw → effective v2; dispute → source only; resolve
  + accept → accepted; a new version invalidates the old accept; retries are
  idempotent; two `show` runs are identical except `read_snapshot`.
- `check_distillate_audit.py`: word-number surfaces (`10` vs `десять` fails
  V5), an ordinary `и` is not a number, `всегда`→`никогда` and a dropped `не`
  fail V5 with counts, the capture status `NO_STATEMENT` and the tool refusal
  `STATEMENT_NOT_FOUND` stay separate, the help table is closed over every
  literal `Denied` code, and an archived source is readable but takes no new
  versions.
- `check_brief_hook.py`: one line; silence when the state is unchanged; a new
  line after the gaps change.
- Both host trees stay byte-identical; the dist archives are rebuilt.

## 15. Limits and non-goals

- The engine cannot judge semantic deviation; it checks structure and declared
  `expects`.
- A flat plan limits the line.
- The hook's target item is a heuristic (last touched).
- Search queries are keyword templates; quality depends on title and context.
- V4/V5 are grounding proxies, not semantic equivalence; the agent is
  constrained to the source vocabulary.
- The capture filter is stem-based, not semantic: a synonym outside the pack
  is a false negative, and the cost is a missed statement until the user
  says "запомни" or the agent records the quote manually.
- `by=user` is declared, not authenticated in lite.
- A statement is global until an item-level claim cites it; v1 does not
  auto-link.
- The improvement chain stops after three failed reviews per source.
- No embeddings and no LLM inside the hook or the validator. NER is not part
  of the runtime: capture stays a deterministic marker pack. A NER model may
  only be an offline audit oracle over a frozen corpus, never a capture path.
- A captured source always has a v1; source-only capture is not implemented.
- The per-session capture cap is not enforced; deduplication is by text.

## 16. Plan mapping

| Plan item | Branch | Content |
|---|---|---|
| П4 | AD | baseline and acceptance |
| П4.1 | AD100 | statement capture: source + extractive distillate |
| П4.2 | AD200 | brief: line, accumulated, gaps, search, vector, next |
| П4.3 | AD300 | `expects` comparison |
| П4.4 | AD400 | hook: capture and brief pointer |
| П4.5 | AD500 | tests, documentation, dist |
| П4.6 | AD600 | improvement pipeline: propose/validate/accept, states, rollback |

## 17. Open questions

- `expects` authoring: a property via `dom_annotate` first, or a
  `dom_plan_ingest` JSON field first?
- Should the brief include project-level statements at the root by default, or
  only when the agent links them?
- Should `accepted_by=user` be mandatory for statements that change a
  requirement, or only for disputed ones? (v1: only disputed.)
- The v2 agent-side refinement skill (§12.14): a separate plan item now, or
  after the v1 slice ships? Its output format is already the `propose` schema,
  so it needs no storage change.
- Should the marker pack be user-editable at runtime (a root-claim property)
  or release-shipped only? (v1: release-shipped, digest recorded per source.)
