# Trusted Write Loop 0.3.0 — Tech Cards

**Status:** roadmap; not started. Depends on Observability 0.2.6.
**Goal:** Make every write carry a trusted `WriteContext`, make reads produce DB receipts, and gate agent writes on `dom_find` + `basis`.
**Why separate:** Revision 2 of 0.2.6 was rejected because it mixed observability with a schema/transaction rewrite. This file isolates that work.

## Global Constraints

- No new dependencies. Offline, deterministic.
- `WriteContext` is the only way to supply `origin`, `session`, `session_source`, `principal`. No Service method accepts a raw `session`/`origin` argument.
- `session_source` values: `hook | mcp-plugin | mcp-unverified | director | unknown`. Only `hook` and `mcp-plugin` (after a verified channel) count as trusted for the gate.
- `claim_sessions` and `read_receipts` are unhashed sidecars unless the team accepts a hash-scheme bump. If unhashed, the gate is **advisory against accidental writes, not a defense against a local attacker with DB access**. State this in the UI and docs.
- Migration is in `storage.py` V5, with a v4 fixture DB test. No `.sql` directory.
- No native Panel work here; Panel is 0.3.1.

## Tech Cards

| ID | Card | Files | Test | Fault probe |
|---|---|---|---|---|
| L1 | `WriteContext` dataclass and writer inventory. `write_id` UUID, `claim_id` nullable. Writers: start, claim, update, plan_ingest, capture, inbox drain, fork, candidate submit, admit, system writes. | `admission.py`, `claim_entry.py`, `planning.py`, `distillate.py`, `watcher.py` | `tests/check_write_inventory.py` | one writer missing a row → fail |
| L2 | V5 migration: `claim_sessions(write_id PK, claim_id NULL, session, session_source, origin, principal, db_realpath, duration_ms, result, candidate_session, reviewer_session, at)`; `read_receipts(id PK, write_id, session, session_source, claim_id, source, at)`. | `storage.py` | `tests/check_migration_v5.py` (v4 fixture → v5, DOWN) | missing column/row → fail |
| L3 | Same-transaction writes: `_commit` writes the claim row and `claim_sessions` row together; `find`/`context` write `read_receipts` in the same transaction. No “or immediately after”. | `admission.py`, `claim_entry.py` | `tests/check_transaction_binding.py` | forced commit failure leaves no receipt/claim row |
| L4 | Trusted session channel. Hooks: `DOM_SESSION` set by the plugin from host `sessionID` → `session_source=hook`. MCP: `mcp-unverified` until a plugin-side channel is proven (`ctx.tool.hook` input rewrite or per-call header). No model-supplied session/origin. | `index.js`, `server.py`, `claim_entry.py` | `tests/check_session_authority.py` | model sends `origin=hook`/`session` → refused or recorded `mcp` |
| L5 | DB identity: every writer records `db_realpath`; health compares MCP and hook paths; junction/case/Unicode tests. | `paths.py`, `admission.py` | `tests/check_db_identity.py` | mismatch → `DB_PATH_MISMATCH` |
| L6 | Gate invariant in `Service`: write allowed only if same trusted session has a `read_receipts` row for a claim id in `basis`. Missing/unverified → `GATE_NO_READ`, no write. | `admission.py`, `claim_entry.py` | `tests/check_gate_receipts.py` | no receipt/basis → no write |
| L7 | Plugin UX gate at `ctx.tool.hook("execute.before")`: early block, reason surfaced to the model. Advisory only; `Service` is the invariant. | `index.js` | `tests/check_gate_plugin.mjs` | forged args → no effect; Service still blocks |
| L8 | Candidate/reviewer attribution: `candidate_session` at submit, `reviewer_session` at admit; manual/independent review tests. | `admission.py`, `claim_entry.py` | `tests/check_review_sessions.py` | missing reviewer session → fail |

## Gate Rule

A write is allowed only if:

1. `session_source` is trusted (`hook` or verified `mcp-plugin`);
2. a `read_receipts` row exists for the same session and for a `claim_id` that appears in the claim `basis`;
3. the write and the receipt commit atomically.

`GATE_NO_READ` writes nothing. The JSONL journal is observability only.

## Acceptance

- Writer inventory test covers all ten writers; no raw session/origin parameters remain.
- V4 fixture migrates to V5 and back; all existing claims remain readable.
- Forged session/origin tests fail closed.
- Receipts and writes are same-transaction.
- Gate tests cover allow, no receipt, unverified session, basis without read id, and atomic rollback.
- Docs state the unhashed-sidecar limit if applicable.

## Open Decisions

1. Hash receipts into the chain, or accept advisory-only gate?
2. Can `ctx.tool.hook("execute.before")` rewrite `input`? If not, the MCP session remains `mcp-unverified` and the gate is hook-only until a header channel exists.
3. Do subagent sessions carry a parent link? If not, record `unknown` and show it in health.
4. Native Panel 0.3.1: reuse the monitor JSON API, no second data source.
