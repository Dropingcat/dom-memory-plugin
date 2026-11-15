---
name: dom-memory
description: Long-term project memory in the DOM ledger (dom_* MCP tools). Use when a project plan is made or changed, when a decision, quote, formula, code fragment, data or finding should be remembered, when asked "where did this come from / why did we decide this", or when resuming work on a project that has a .dom-memory folder.
---

# DOM project memory

The `dom-memory` MCP server keeps the project's memory in `<project>/.dom-memory`: an append-only ledger of **claims** (primary statements in their original wording) grouped by the **plan**. Nothing is deleted; a change is a new version.

## Start of a session

1. `dom_describe({})` — state: `NOT_STARTED` → `dom_start({"request": "<the user's request, verbatim>"})`.
2. `dom_plan_status({})` — takes in plans queued by the hook and says whether a plan is known. If `request` is not null, do what it says (send the plan with `dom_plan_ingest`).
3. `dom_plan_tree({"root": "П1"})` and `dom_find({"current_only": true})` to re-orient.

## Planning workflow

The approved plan reaches the memory through the hook. The hook never lets a stale or foreign
plan silently overwrite what the project already has:

- **Plan file consumption.** On OpenCode, approval writes `<plan>.md` under `~/.opencode/plan`; the
  context hook hands it to `plan_hook.py --now --plan-file <path>`. A sidecar `<plan>.dom.json`
  records who consumed it (session, principal, revision, digest). A plan file already consumed by
  another session is never written here — it is a foreign plan, queued not ingested.
- **No silent overwrite.** Every hook ingest is `base_revision`-based. If the plan changed since the
  file was read, the hook answers `QUEUED_CONFLICT` (never `force`). If the file is older than the
  last plan change, it is `QUEUED_STALE`. Both go to the inbox and are reported to the model, which
  must read `dom_plan_tree` and send the merged plan with `dom_plan_ingest(base_revision=<current>)`.
- **One write per read file.** The hook writes the consumption stamp on success; a plan file's
  content is ingested for this session at most once.
- **Context comes with the plan.** A plan file is a distillation of the conversation, so the context
  hook also sends the discussion that produced it (user/assistant text only — tool calls and tool
  results are excluded). The hook distils it into a context per item — the requirement, constraint or
  finding behind the item — and the item's header claim carries it (the `context` property and the
  candidate `rationale`). A context is not part of the plan revision: the same plan with a new
  discussion answers `PLAN_UNCHANGED`, and the new context is versioned as a property.
- If the project is not started, the plan itself is the request (`dom_start` on first ingest).

When your session restarts or another agent touches the same plan, start from `dom_plan_status` and
`dom_plan_tree`; re-read the current revision before any `dom_plan_ingest` (see "The plan").

## The plan

- A plan approved in plan mode is taken in by the plugin hook, which then tells you the revision (or that it could not find the plan) and how many items got their discussion context. Right after approval call `dom_plan_status({})`: if the plan is not there (`has_plan` false or the old revision), send it yourself: `dom_plan_ingest({"plan": "1. ...\n1.1 ...", "contexts": {"П1": "<why this item exists, from the conversation>"}})`. The hook's context is extractive (it matches the discussion to the words of each item), so add or correct `contexts` where the reason is stated indirectly; sending the same plan with new `contexts` updates the context without moving the revision.
- To add items to an existing plan without touching the others use `"mode": "partial"` and write their real numbers (`3.`, `3.1`) so they land at П3, П3.1.
- Every item gets its own branch: П1=`AA`, П2=`AB`, П1.1=`AA100`, П1.1.1=`AA110`. Codes never move. A changed item becomes a new version of its header claim.
- Several agents may share one plan. Send `"base_revision": "<source_revision you read>"` with every change: if someone changed the plan since, you get `PLAN_CONFLICT` (with `changed_by` and `item_paths`) and nothing is written — read the plan with `dom_plan_tree`, merge, and send again on the current revision. `force: true` overwrites on purpose. `dom_plan_status` shows who sent the current revision (`ingested_by`) and open `conflicts`.
- When the plan changes, send the whole plan again. Never drop items silently: an item missing from the plan is archived.

## Recording claims

- One statement = one `dom_claim({"plan_task": "П1.1", "type": ..., "text": "<verbatim>"})`. The branch comes from the plan item.
- Types and their required fields: `quote` (source_ref), `decision` (rationale), `formula` (notation, units), `code` (repository_ref, relative_path), `data` (artifact_ref, units), `concept` (definition), `text` (evidence when `classification` is `factual`). Object fields (`units`, `notation`, `columns`) map a symbol to a non-empty value; Cyrillic keys are fine. A missing required field is refused (`MISSING_FIELD`) — ask the user or record it as `text`; never invent a value.
- Say where it comes from and what it is for: `basis` (claim ids / source refs), `refines`, `purpose`, `usage`, `keywords`.
- Comments, properties, links and retiring a claim: `dom_annotate({"claim_id", "kind": "comment|property|link|archive|conflict|resolve", ...})`. Nothing is deleted; archive needs a reason.
- If a write answers `RECORDED_CANDIDATE`, the project runs under a review policy: the record waits for the reviewer (`dom_candidate`, `dom_accept`). Do not retry it.
- Look first: `dom_find({"query": "...", "plan_task": "П1"})`. To change a claim use `dom_update` (new version), not a second claim.
- When other agents work on the same plan, pass `"expected_current": "<claim_id you read>"` to `dom_update`. If someone changed it since, you get `CONFLICT_READ_REQUIRED` with their version and nothing is written. Read it; then update it, or — if it is another agent's version that does not fit — `"mode": "fork"`: your text becomes a parallel variant (both current, an open split in `dom_trace`); `dom_reconcile` merges them when they converge.
- A claim id you remember may have moved on (updated by another agent, or your variant merged into the base by `dom_reconcile`). `dom_context` / `dom_claim_versions` return `resolution` {status: current|replaced|merged|archived, current_claim_id, steps[who, when, why], explanation}; `dom_update` on an old id answers `NOT_CURRENT` with the current `claim_id` and the same `resolution`. Continue from `current_claim_id`.
- "Who did what on this item": `dom_task_graph({"task": "П1"})` — chains of versions with authors and their links (`basis`, `refines`, `supersedes`, `variant_of`); `"agent": "<principal>"` filters one agent. `dom_task_claims` also shows `author`.

## Splitting a long text into claims

1. `dom_extract({"task": "П1.1", "text": "<material>", "provider": "client"})` → rules + schema.
2. Split the material yourself, copying the wording, then `dom_extract_submit({"task", "text", "candidates": [...]})`.
3. Candidates are **not** in the ledger yet: `dom_candidate` to read, `dom_accept({"candidate_id", "decision": "approve"|"reject", "note"})`. When the user wants to review, show them the candidates and let them decide.

## Answering "where did this come from?"

`dom_context({"claim_id": ...})` — the claim, the chain it refines up to the plan item, its basis and the original request. `dom_claim_versions` — history of one claim. `dom_task_claims({"task": "П1", "include_children": true})` — everything under a plan item.

## Rules

- The text of a claim is stored byte for byte; do not paraphrase the user or a source.
- `CHAIN_BROKEN` (also `dom_describe` → `state: CHAIN_BROKEN`) means the database was edited outside the server: writes are refused; stop and tell the user.
- `NOT_CURRENT` on `dom_update`: another session changed the claim first. Read the current version (`dom_claim_versions`) and update that one if your change is still needed.
- The customer request recorded by `dom_start` is never replaced (`ROOT_IMMUTABLE`); add a clarifying claim with `basis` = its id.
- `dom_describe` → `review_policy` shows this project's review policy; a project started under review stays under review.
- `dom_export({"branch": "AA"})` writes a YAML snapshot; `dom_sync` delivers the outbox (the OMH target is simulated).
