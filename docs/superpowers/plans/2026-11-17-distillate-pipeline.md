# Distillate pipeline (stage 3, П4.1 + П4.6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture user statements (source quote + deterministic extractive v1 distillate with anchors) in the prompt hook, and expose the hard improvement chain `propose → validate(V1–V8) → accept` through a new lite tool `dom_distillate`.

**Architecture:** One new module `mockup/distillate.py` owns capture, extraction, the V1–V8 validators and the seven `dom_distillate` actions; states and the effective version are always recomputed from the ledger (properties `basis_spans` / `distillate_review` / `distillate_accept` are per claim version). The hook calls `capture(svc, ...)` only; the server dispatch wires the tool like every other lite tool (TOOL_DOCS → LITE_SIGNATURE → HANDLERS → public_dto).

**Tech Stack:** Python 3 stdlib only (no PyYAML, no network). SQLite via the existing `Store`. Tests are plain `tests/check_*.py` scripts run against BOTH host trees via subprocess, like `tests/check_inbox_status.py`.

**Spec:** `docs/claim-brief.md` §12.1–§12.14 (the plan argues from the spec; executors read both). Plan items П4.1 (AD100) and П4.6 (AD600) of revision r15.

## Global Constraints

- Deterministic: no model, no network, no randomness in the hook or the validator. Same DB + same pack → same JSON, except `read_snapshot` and stored `at` timestamps.
- The source quote is stored byte-for-byte from the prompt; the capture idempotency key is `stmt:<session>:<sha256(normalized prompt)>` where `normalize = re.sub(r'\s+', ' ', text).strip().casefold()`.
- Both host trees stay byte-identical: every edit under `opencode/mockup|hooks|skills` is copied byte-identically to `claude/` in the same task and verified by sha256 before the task's tests run.
- Append-only: the pipeline never edits a source or an old version; agent versions are new versions via the existing `claim_entry.update` (`attribution`/`origin_version` are preserved by the engine and are NOT the discriminator — `basis_spans.origin` is).
- `VERSION` stays 0.2.4; `tools/build_zip.py` rebuilds both dist archives in place at the end (no release decision in this slice).
- All new refusal codes must get a `_HELP` entry in `public_dto.py` (strict + lite wording), because refusals flow through `_refusal`.
- The hook never blocks the prompt and never calls `dom_distillate`; every failure prints to stderr and exits 0.

## Review Focus

1. **Root-request duplicate:** the first prompt is recorded verbatim as the root request; capture must not record the same text again as a statement. Pinned in Task 3 ("root request prompt → SKIPPED").
2. **CRLF / trailing whitespace prompt:** the stored source is the prompt as received; a repeat with different whitespace is the same statement (normalized key) and must not create a second source. Pinned in Task 3 ("duplicate with different whitespace").
3. **Decimal / abbreviation splitting:** "3.14" and "т." must not split a sentence. Pinned in Task 2 ("decimals and abbreviations stay one sentence").
4. **Dropped negation / invented number in a proposal:** V5 must fail and the effective version must stay the previous one. Pinned in Task 6 ("V5 catches a dropped negation").
5. **Unknown source id:** `dom_distillate(source="no-such-id")` returns a structured refusal (NO_STATEMENT), never a traceback. Pinned in Task 4 ("unknown source is a refusal").
6. **Three failed reviews:** after the third failure `propose`/`validate` refuse with DISTILLATE_LOOP and there is no reset. Pinned in Task 6 ("loop stops the chain").
7. **Property write failure:** a `propose` version without `basis_spans` fails V3, and a retry with the same text repairs the property instead of duplicating the version. Pinned in Task 5 ("repair of a lost anchors property").

---

### Task 1: Sync the two spec amendments the plan discovered

**Files:**
- Modify: `docs/claim-brief.md` (§12.4, §12.6, §12.11)

**Interfaces:**
- Consumes: approved spec (user, 2026-11-17).
- Produces: the wording Tasks 3 and 5 implement (two-transaction capture; stored-span V3).

- [x] **Step 1: Amend §12.4 capture atomicity** — replace "The hook writes the source, the v1 and its `basis_spans` in one transaction: if the transaction fails, nothing is written and the prompt is not blocked." with: "The hook writes the source and the v1 as one two-item claims package; both properties (`capture_pack` on the source, `basis_spans` on the v1) go into one following append transaction. If a transaction fails, the prompt is not blocked; a repeat of the same statement repairs a missing property (§12.9)."

- [x] **Step 2: Amend §12.11** the same way ("in one transaction" → the two-transaction wording).

- [x] **Step 3: Amend §12.6 V3** — replace "each anchor is a non-empty verbatim substring of the source occurring exactly once; offsets resolve" with: "each anchor is a non-empty verbatim substring of the source; a quote that occurs more than once is refused with `SPAN_AMBIGUOUS` at `propose` time; stored offsets must satisfy `source[start:end] == quote` (a repeated sentence gets distinct offsets, so stored spans never need the once-rule)".

- [x] **Step 4: Verify**

Run: `grep -n "two-item claims package" docs/claim-brief.md && grep -n "SPAN_AMBIGUOUS\` at \`propose" docs/claim-brief.md`
Expected: both found.

- [x] **Step 5: Commit** — `git add docs/claim-brief.md docs/superpowers/plans/2026-11-17-distillate-pipeline.md && git commit -m "docs: capture uses two transactions; V3 validates stored spans by offset"`

### Task 2: Extraction primitives (pure functions)

**Files:**
- Create: `opencode/mockup/distillate.py`
- Copy to: `claude/mockup/distillate.py` (byte-identical)
- Test: `tests/check_distillate_extract.py`

**Interfaces:**
- Consumes: `mockup.plan_context._tokens`, `_same` (existing, import only).
- Produces (used by Tasks 3–6):
  - `PACK_VERSION = 'v1'`; `STRONG_MARKERS`, `DECISION_MARKERS`, `NOISE_MARKERS` (tuples of lowercase stems per spec §12.3);
  - `normalize(text: str) -> str` — `re.sub(r'\s+', ' ', text).strip().casefold()`;
  - `split_spans(text: str) -> list[tuple[str, int, int]]` — hardened splitter, offsets into the raw text;
  - `capture_decision(source_text: str, project_nouns: list[str]) -> dict` with keys `capture: bool`, `explicit: bool`, `selected: list[tuple[str, int, int]]` (1–3 sentences, best first by score then position), `score: int`;
  - `pack_digest() -> str` — `sha256` of the canonical marker pack;
  - `MARKER_RE (compiled)`, internal `_marker_hits(sentence) -> set[str]`.

- [x] **Step 1: Write the failing test** `tests/check_distillate_extract.py` — for each tree in `('claude', 'opencode')` run `python -B -c` with `sys.path.insert(0, <tree>)` and assert:
  - `split_spans("Сделай 3.14 и т.д. Потом решаем.")[0]` is one sentence `"Сделай 3.14 и т.д."` (decimal + abbreviation do not split);
  - `split_spans` of text with a fenced block keeps the block inside one unit;
  - `capture_decision("Важно запомнить: делаем детерминированный дистиллят.", [])["capture"]` is True, `["explicit"]` True ("запомни");
  - `capture_decision("Важно: привет", [])["capture"]` is False (lone "важно", no noun, no second marker);
  - `capture_decision("Решили фиксировать baseline r14.", ["baseline", "r14"])["capture"]` is True ("решили" + project noun);
  - `capture_decision("Решили фиксировать baseline.", [])["capture"]` is True (decision marker + second-marker rule via stem "фиксиру..."); and `capture_decision("сделай дашборд", ["дашборд"])["capture"]` is False (noise-only command);
  - `capture_decision` on a 4-sentence input selects at most 3 sentences and none longer than 300 chars;
  - `pack_digest()` is stable across two calls and across both trees.
  Script prints `{"ok": true}` and exits non-zero on the first failed assert.

- [x] **Step 2: Run it** — `python tests/check_distillate_extract.py`
  Expected: FAIL (`ModuleNotFoundError: mockup.distillate`).

- [x] **Step 3: Implement** `opencode/mockup/distillate.py` — module docstring names the spec (§12.3, §12.14); splitter: normalize CRLF for matching but keep raw offsets; do not split after a single-letter abbreviation (`r'(^|\s)\S\.$'` lookbehind guard), after a digit decimal point, inside a fenced code block or a URL; list markers and Markdown headings are separators; scoring per §12.3 (marker = 2 needs support: a project noun or a second distinct marker; explicit strong request captures alone; noise-only = −3).

- [x] **Step 4: Copy byte-identical to `claude/` and verify** — `python -c "import hashlib,pathlib; a=pathlib.Path('opencode/mockup/distillate.py').read_bytes(); b=pathlib.Path('claude/mockup/distillate.py').read_bytes(); assert a==b"`; then run `python tests/check_distillate_extract.py` → PASS.

- [x] **Step 5: Commit** — `git add opencode/mockup/distillate.py claude/mockup/distillate.py tests/check_distillate_extract.py && git commit -m "feat: deterministic capture scoring and hardened sentence splitter (П4.1)"`

### Task 3: `capture` + hook wiring

**Files:**
- Modify: `opencode/mockup/distillate.py` (add capture), `opencode/hooks/plan_check_hook.py`
- Copy to: `claude/mockup/distillate.py`, `claude/hooks/plan_check_hook.py`
- Test: `tests/check_statement_capture.py` (new, both trees)

**Interfaces:**
- Consumes: Task 2 primitives; `claim_entry.build_item`, `_text_bytes`, `_commit`, `_package`; `planning._append`, `_prop` (property event shape: `{'local_id','event_type':'property','claim_ref',...,'value':{'name','value'},'ref':'dom_distillate'}`); `plan_context._tokens`.
- Produces (used by Tasks 4–6):
  - `capture(svc, prompt: str, session: str|None) -> dict` with keys `status` (`STATEMENT_RECORDED | ALREADY_RECORDED | SKIPPED | NO_STATEMENT`), `source_id`, `distillate_id`, `repaired: bool`, `line: str|None`;
  - `is_statement_source(item) -> bool` — current `quote` whose metadata `source_ref` starts with `session:` and whose `locator` starts with `prompt:`;
  - constant `DISTILLATE_WITHDRAW = 'distillate_withdraw'`.

Implementation rules (pinned):
- skip when the root request claim's normalized text equals the prompt's (`SKIPPED`);
- scoring nouns: tokens of all `current_plan_tasks.title` + tokens of the root request claim text;
- source claim: type `quote`, `source_ref='session:<id or "unknown">'`, `locator='prompt:<sha256(normalized)[:12]>'`, `origin_version` default, `reason='user statement capture'`;
- distillate v1: type `text`, `classification='interpretation'`, `attribution='deterministic'`, `origin_version='stmt-extract-v1'`, `purpose='distilled comment'`, `basis=[<source_id>]`, `keywords` = top content tokens of the selected sentences (cap 6); text = selected sentences joined by `' '`;
- one two-item claims package with `idempotency_key = stmt:<session>:<sha256(normalized)>` via `claim_entry._commit` (follow `claim()`'s build pattern, `claim_entry.py:515`); dedupe of that key returns the original receipt (`ALREADY_RECORDED`);
- then ONE append package (`planning._append`) with both property events: `capture_pack` (`{"pack": "v1", "digest": pack_digest()}`) on the source, `basis_spans` (`{"v":1,"origin":"extract","candidate_digest":...,"sentences":[{"i":k,"anchors":[{"quote","start","end"}]}]}`, `i` = index within the candidate) on the v1; `tag='stmt-anchors:<sha[:12]>'` so a repair replay is content-addressed;
- stdout line exactly per spec §12.11 (only when a new source is admitted; when no sentence qualifies the line names the source and "no distillate");
- hook: in `main()` after `svc` is built and before `svc.plan_status(...)`, call `distillate.capture(svc, prompt, session)` in try/except; print `res['line']` when not None; failures to stderr, exit 0.

- [x] **Step 1: Write the failing test** `tests/check_statement_capture.py` — per tree, temp project dir + env (`OPENCODE_PROJECT_DIR`, `PYTHONUTF8=1`), run `plan_check_hook.py` with a JSON stdin `{"prompt": ..., "session_id": "s1"}`:
  1. first run autostarts (existing behavior, root line on stdout) and does NOT capture the root prompt again (no `statement ` line);
  2. second prompt `"Делаем детерминированный экстрактивный дистиллят. Это базовый процесс."` → stdout contains one `statement ` line; a verification script asserts: a `quote` claim on main whose raw text is byte-identical to the prompt, `source_ref`/`locator` shape, `capture_pack` property; a `text` claim with `basis=[source_id]`, `origin_version='stmt-extract-v1'`, `basis_spans` property whose spans satisfy `source[start:end] == quote`;
  3. a question prompt `"Как дела?"` → no new quote claims (count before == after);
  4. lone `"Важно: привет"` → no new quote claims;
  5. `"Запомни: правило номер 5"` → captured (explicit alone);
  6. `"Решили фиксировать baseline."` then `"решили фиксировать baseline. "` (different whitespace) → exactly one source claim for both (normalized dedupe);
  7. hook with an empty prompt → exit 0, no output change;
  8. stderr-only failure drill: env `DOM_AUTOSTART=0`, missing dir → exit 0.

- [x] **Step 2: Run it** — `python tests/check_statement_capture.py` → FAIL (`capture` missing / no statement line).

- [x] **Step 3: Implement** capture + hook wiring as pinned above.

- [x] **Step 4: Copy byte-identical to `claude/` (both files), verify sha256, run** `python tests/check_statement_capture.py` → PASS; also `python tests/check_inbox_status.py` → PASS (no hook regression).

- [x] **Step 5: Commit** — `git add ... && git commit -m "feat: hook captures statements as source quote + extractive v1 with anchors (П4.1, П4.4)"`

### Task 4: `show` + server wiring

**Files:**
- Modify: `opencode/mockup/distillate.py` (states, effective, show), `opencode/mockup/admission.py` (`@guarded` delegate `Service.distillate`), `opencode/mockup/claim_entry.py` (`TOOL_DOCS['dom_distillate']` — summary/arguments/returns/example per `dom_annotate`'s entry shape), `opencode/mockup/server.py` (`LITE_SIGNATURE['dom_distillate'] = ({'action': str}, {'source': str, 'distillate': str, 'text': str, 'anchors': list, 'by': str, 'note': str})`; `HANDLERS['dom_distillate'] = _handle_distillate` delegating to `service.distillate(...)`), `opencode/mockup/public_dto.py` (`RECORDS['DistillateState']`, `LITE_RESPONSES['dom_distillate']`, `_HELP` entries)
- Copy to: `claude/mockup/…` (same five files, byte-identical)
- Test: appended section in `tests/check_statement_capture.py`

**Interfaces:**
- Consumes: `planning._properties`-style property reads (per claim version: `events WHERE claim_ref=? AND kind='property'`), `claim_entry._children/_archived` (version chain, archive set).
- Produces (Tasks 5–6 build on):
  - `_versions(c, claim_id) -> list[dict]` (oldest→newest: `claim_id, version, text, raw_sha256`);
  - `_open_conflicts(c, claim_id) -> list[dict]` (`conflict` without a later same-ref `resolve`, same claim_ref);
  - `_state(c, version_row, is_v1) -> str` per spec precedence `superseded > withdrawn > disputed > accepted > validated > rejected > extracted > proposed`;
  - `effective(c, source_id, versions) -> dict|None` per spec §12.5 pseudocode;
  - `distillate(svc, action, source=None, distillate=None, text=None, anchors=None, by=None, note=None) -> dict` — uniform record keys: `status, simulated, action, source_id, source_state, distillate_id, logical_claim_id, effective, versions, open_conflicts, failed, review, next_steps`;
  - statuses `DISTILLATE_SHOWN | PROPOSED | ALREADY_RECORDED | VALIDATED | REVIEW_FAILED | ACCEPTED | WITHDRAWN | DISPUTED | RESOLVED`; refusal codes `NO_STATEMENT` (source/distillate not found or not a statement), plus the shared codes of later tasks.

- [x] **Step 1: Write the failing test** (append to `tests/check_statement_capture.py`): after the capture scenario — `show` by source id returns `status='DISTILLATE_SHOWN'`, `versions[0]['state']=='extracted'`, `effective` points at the v1 with `origin='extract'`; two consecutive `show` runs produce identical JSON except `read_snapshot`; `show`/any action with `source='no-such-id'` → status `NO_STATEMENT` with a hint; `show` requires `source` or `distillate` (else `INVALID_FIELD`).

- [x] **Step 2: Run** → FAIL (`dom_distillate` unknown tool / record missing).

- [x] **Step 3: Implement** the five wiring edits + states/effective/show. `TOOL_DOCS` entry: summary per spec §12.5 tool surface; arguments `action` (required, enum in description), `source`, `distillate`, `text`, `anchors`, `by`, `note`; example `{"action": "show", "source": "<source_id>"}`.

- [x] **Step 4: Copy byte-identical (5 files) to `claude/`, verify sha256, run** both check scripts → PASS.

- [x] **Step 5: Commit** — `git commit -m "feat: dom_distillate show with ledger-computed states (П4.6)"`

### Task 5: `propose` + checkpoints V1–V8

**Files:**
- Modify: `opencode/mockup/distillate.py`
- Copy to: `claude/mockup/distillate.py`
- Test: `tests/check_distillate_pipeline.py` (new, both trees)

**Interfaces:**
- Consumes: Task 3/4 API; `claim_entry.update` (new version), `claim_entry.annotate` (property), `plan_context._tokens/_same`.
- Produces:
  - `resolve_anchors(source_text, anchors: list[list[str]]) -> list[dict]` — per sentence group, each quote must occur exactly once in the source (`SPAN_NOT_FOUND` / `SPAN_AMBIGUOUS` are `Denied`); returns `{"i", "anchors":[{"quote","start","end"}]}`;
  - `run_checks(c, source_row, candidate, spans, effective_text, fail_count) -> {"checks","failed","detail"}` — V1–V8 exactly per spec §12.6 (V1 digest `sha256(raw)`; V2 `basis_spans.origin='agent'`; V3 offset equality; V4 `novel<=2`/`coverage>=0.6`, `novel=0` for ≤3 content tokens; V5 numbers, non-initial capitalized tokens, negation/quantifier parity `не/нет/never/not/no`, `всегда/никогда/только/always/never/only`; V6 1–3 sentences ≤300 chars, ≤60% of a ≥200-char source, normalized difference; V7 no open conflict; V8 `fail_count < 3`);
  - `propose` flow: current-version text/anchors pre-checks (`ALREADY_RECORDED` on same normalized text + same anchors; property repair when `basis_spans` is missing; `PROPOSAL_CONFLICT` on same text + different anchors; `NO_IMPROVEMENT` vs the effective version), then `claim_entry.update` (new version, reason `'distillate proposal'`, anatomy `basis`/`purpose` preserved) and `basis_spans` property on the NEW version; a failed property write leaves the version unanchored (repair path above).

- [x] **Step 1: Write the failing test** `tests/check_distillate_pipeline.py` — per tree: capture a statement with ≥2 sentences (reuse Task 3's capture via import, not the hook); then:
  - `propose` a faithful compression with valid anchors → `status='PROPOSED'`, `versions` has 2 entries, `effective` still the v1 (rollback guarantee);
  - identical `propose` retry → `ALREADY_RECORDED`, still 2 versions;
  - `propose` with a quote not in the source → `SPAN_NOT_FOUND`; with a twice-occurring quote → `SPAN_AMBIGUOUS`;
  - `propose` byte-identical to the effective v1 text → `NO_IMPROVEMENT`;
  - `propose` same text with different anchors → `PROPOSAL_CONFLICT`;
  - drift drill: delete the v2 `basis_spans` property row via a scratch DB copy, `propose` the same text again → the property is repaired and `validate` can run (no duplicate version).

- [x] **Step 2: Run** → FAIL.

- [x] **Step 3: Implement** `resolve_anchors`, `run_checks` (pure, cursor-in), `propose`.

- [x] **Step 4: Copy byte-identical, verify sha256, run** all three check scripts → PASS.

- [x] **Step 5: Commit** — `git commit -m "feat: dom_distillate propose with deterministic V1-V8 gates (П4.6)"`

### Task 6: `validate`, `accept`, `withdraw`, `dispute`, `resolve`, loop stop

**Files:**
- Modify: `opencode/mockup/distillate.py`
- Copy to: `claude/mockup/distillate.py`
- Test: appended sections in `tests/check_distillate_pipeline.py`

**Interfaces:**
- Consumes: Task 5 `run_checks`; `claim_entry.annotate` for `conflict`/`resolve`/property events (reason passes through the event).
- Produces:
  - `validate` → writes `distillate_review` on the CURRENT version (`verdict='pass'` → status `VALIDATED`; `verdict='fail'` → status `REVIEW_FAILED`, `failed`/`detail` in the answer); review property shape per spec §12.7 (`at` recorded, excluded from `review_digest = sha256(canonical(review without 'at'))`; `anchors_digest = sha256(canonical(basis_spans without candidate_digest))`);
  - `accept(by, note)` → requires a passing review on this exact version (`REVIEW_REQUIRED`), matching digests (`REVIEW_STALE`), no open conflict (V7 recheck), `< 3` failures; writes `distillate_accept`; status `ACCEPTED`, effective flips to the accepted version;
  - `withdraw(note)` → `distillate_withdraw` property on the current version, status `WITHDRAWN`, effective falls back;
  - `dispute(reason)` → conflict on the source when only `source` is given, else on the current version (`conflict_ref='stmt:<source_id>'`); `show` reports `STATEMENT_DISPUTED` via `source_state='disputed'` / version state `disputed`;
  - `resolve(reason)` → `resolve` event with the same ref on the same target;
  - loop stop: `DISTILLATE_LOOP` refuses `propose`/`validate` at the third failure (no reset action);
  - idempotency: validate/accept retries return the existing records (`ALREADY_RECORDED`).

- [x] **Step 1: Write the failing tests** (append): validate pass → `VALIDATED`; `accept(by='agent')` → `ACCEPTED`, `effective.version` = 2; propose v3 → effective stays v2; V5 drill (candidate drops a `не` / invents a number) → `REVIEW_FAILED` with `detail['V5_FACTS']`, effective unchanged; three failures total → next `propose` → `DISTILLATE_LOOP`; `withdraw` on v3 → effective v2; `dispute` on the source → `source_state='disputed'`, effective None; `resolve` after a correction accepted → dispute closed; `accept` without a review → `REVIEW_REQUIRED`; `accept` after the version's anchors changed → `REVIEW_STALE`; two `accept` retries → one accept record.

- [x] **Step 2: Run** → FAIL.

- [x] **Step 3: Implement** the five actions + loop counter.

- [x] **Step 4: Copy byte-identical, verify sha256, run** all check scripts → PASS.

- [x] **Step 5: Commit** — `git commit -m "feat: validate/accept/withdraw/dispute/resolve with built-in rollback and loop stop (П4.6)"`

### Task 7: Documentation, dist, full verification

**Files:**
- Modify: `NOT_DONE.md`, `opencode/skills/dom-memory/SKILL.md` (+ `claude/skills/dom-memory/SKILL.md` byte-identical), `docs/claim-brief.md` §16 mapping check (П4.6/П4.7 rows already present)
- Rebuild: `dist/` via `python tools/build_zip.py`

- [ ] **Step 1: NOT_DONE.md** — add under stage-3 limits: capture is stem-based (false negatives until "запомни"); `by=user` is declared not authenticated; the chain stops after three failed reviews per statement; v2 refinement skill (П4.7) is planned, not implemented.

- [ ] **Step 2: SKILL.md** — short section: capture happens in the hook; improve a distillate only through `dom_distillate(action="propose", source=..., text=..., anchors=[[...]])` → `validate` → `accept`; states/effective come from `show`; source is ground truth; a user correction is a new source.

- [ ] **Step 3: Copy SKILL.md byte-identical to `claude/`, verify sha256.**

- [ ] **Step 4: Full verification** — `python tests/check_distillate_extract.py && python tests/check_statement_capture.py && python tests/check_distillate_pipeline.py && python tests/check_inbox_status.py && python tests/check_plan_identity.py && python tools/build_zip.py`
  Expected: all PASS; build_zip prints both archive paths.

- [ ] **Step 5: Commit** — `git commit -m "docs: stage-3 limits and dom_distillate workflow; rebuild dist"`

## Self-review notes

- Spec coverage: §12.2–§12.9 → Tasks 2–6; §12.10 verification → show + `dom_context` (existing); §12.11 → Task 3; §12.3 capture filter → Tasks 2–3; §12.14 noise measures (marker pack versioning, support rule, splitter hardening) → Tasks 2–3. The brief-side `statements` rollup is П4.2, deliberately out of this slice.
- Types: `distillate()` record keys and status names are defined once (Task 4) and reused verbatim in Tasks 5–6 tests.
- Review Focus inputs each have a pinned test (Tasks 2–6 as listed above).
