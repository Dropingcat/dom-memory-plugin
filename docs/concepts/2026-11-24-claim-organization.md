# Claim organization — the owner's requirements

**Status:** consolidated from the owner's statements and the existing engine behavior.
**Purpose:** one place that states how claims must be organized, and what is already true in code vs. what is still missing.

## 1. No overwrite

A claim is **immutable**. There is no edit and no delete in the normal path.

- `dom_update` creates a **new claim version**; the old row stays in the ledger.
- The new version is linked by a replacement event (`supersedes`), and the old version remains readable.
- `dom_update` with `expected_current` refuses a blind write (`CONFLICT_READ_REQUIRED`); a fork or a read-then-update is required.
- The engine's own rule: a proposal never displaces an accepted version before acceptance.

Status: **implemented**.

## 2. Versioning

One logical claim has a version chain, not a row that mutates.

- `dom_claim_versions(claim_id)` returns `v1, v2, …`, `supersedes`, `revision`, `is_current`.
- `dom_context` and the monitor resolve the current version; a retired version is still readable.
- `dom_update(mode="fork")` creates a **variant** version when another agent's current version does not fit.
- Distillates have their own version chain: `propose → validate/accept`; a failed or withdrawn version does not delete an earlier accepted one. The effective version is computed at read time.

Status: **implemented**; the monitor now shows the `v` column and the full chain in claim detail.

## 3. Properties and graph edges

Properties are not free-form notes; they are the edges that make a graph possible.

- **Anatomy** (on the claim): `basis`, `refines`, `purpose`, `usage`, `keywords`.
- **Event properties** (`dom_annotate(kind="property")`): e.g. `plan_revision`, `plan_items`, `expects`, `variant_of`, `merged_into`, `context`.
- **Plan links**: `task_claims` connects a plan item to a claim with a role (`justifies`, `implements`, `validates`, `documents`, `depends_on`).
- **Read surfaces**: `dom_context` returns `basis`, `refines_chain`, `refinements`, `plan`, `resolution`; `dom_task_graph` returns `supersedes` / `basis` / `refines` / `variant_of` / `task_link` edges.

Status: **implemented at the data layer**. The monitor now labels properties and anatomy, but there is **no dedicated graph view** yet. Machine values such as `board_sha256` are still hashes; they are labeled, not removed.

## 4. Native branching and merging

Branches are allocated by the engine, not by the agent.

- Every plan item gets a branch (`AA`, `AA100`, …); a plan item is the branch's identity.
- A claim that shares a body but differs in properties becomes a **variant** in a `-V<n>` branch (`variant_of`), not a duplicate.
- `dom_reconcile` converges variants: when the variant's current version equals the base in every property, it is archived as `merged_into` the base; a variant branch with nothing left gets a converge event.
- Plan ingest runs reconciliation automatically.

Status: **implemented**. The monitor shows branches and claim counts in the plan tree; it does not yet draw the variant/merge graph.

## 5. Analytics and verification entities

The knowledge pipeline is a set of entities, not one claim table:

- **Source** — quote/URL/file; immutable, with a digest.
- **Distillate** — a versioned claim extracted from a source, with `basis_spans`; states `proposed`, `validated`, `accepted`, `rejected`, `withdrawn`, `disputed`, `superseded`.
- **Review** — `dom_review` / `dom_accept` / `dom_admit`; candidate digest and warrant digest are bound to the decision.
- **Audit** — `dom_distillate(action="audit")`, read-only; findings such as `SOURCE_DIGEST_MISMATCH`, `NO_EFFECTIVE`, `DISPUTED`, `DISTILLATE_LOOP`.
- **Analytics** — `dom_task_claims`, `dom_task_graph`, `dom_task_brief`, `dom_trace`, `dom_diff`.

Status: **implemented at the tool layer**. The monitor currently exposes claims, plan and health; it has **no Analytics/Verification tab** for sources, distillates, reviews, audits and disputes. That is the next monitor gap.

## 6. What the monitor should show next

| View | Data source | Status |
|---|---|---|
| Claim versions / properties | `/api/claim/<id>` | done |
| Plan tree with branch and claim count | `/api/plan/tree` | done |
| Graph edges (`basis`, `refines`, `variant_of`, `task_link`, `supersedes`) | `dom_task_graph` or a read-only query | missing |
| Sources / distillates / reviews / audits | `dom_distillate show/audit`, `dom_context` | missing |
| Conflicts / disputes / withdrawn / superseded | properties and events | missing |

## 7. Consistency rule

The monitor is a read-only projection. It must never invent a relationship that is not a stored claim, event, property or plan link. If an edge is not in the ledger, the graph must not draw it.
