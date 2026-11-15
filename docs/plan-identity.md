# Plan identity: the root item is the plan

**Status:** accepted as the stage-2 design decision; not implemented. Follows the
plan-file guards in `21a0270`. No engine code is changed by this document.

## 1. Problem

A plan ingest that sends a different plan on the same positions can be recorded
as a *revision of the old plan* instead of a *new plan*. On 2026-11-14 this
replaced the live project projection (r11): the root changed from
"Закрыть находки QA004…" to "Публикация репозитория на GitHub", yet three old
`plan_task_id`s were carried onto the new titles and 13 other items were archived
by `mode='full'`. No bytes were lost — the old claims and versions are still in
the ledger — but the current projection showed the new plan as the old plan's
next revision.

The user-facing rule this breaks: **a new plan must create new claims, not update
old ones.**

## 2. The CouchDB revision model: what it gives and what it does not

CouchDB stores every document under an immutable `_id` and a revision tree
(`_rev`). Concurrent writers both persist: the document gets two conflicting leaf
revisions, a deterministic rule picks the winner for views, and the losing leaf
stays reachable (`_conflicts`, `open_revs`). CouchDB does **not** ship a merge
API — the application must fetch all conflicting leaves and merge them. Compaction
removes old revision bodies, so a diff cannot rely on them; the recommended
practice is to store the change description inside each new document.

The lesson is not "use CouchDB". Moving the ledger there would keep the hard part
(resolving competing revisions) and add a second one (compaction destroys the
history the diff would need). The lesson is the identity rule underneath the
tree: **a document id is immutable; changed content is a new revision of the same
id, a different document is a new id.** The revision tree only makes sense
because `_id` never changes.

## 3. Where dom-memory already follows the model

The claim layer already implements the CouchDB properties, and is stronger on the
compaction point:

| CouchDB | dom-memory claim layer |
|---|---|
| immutable `_id` | immutable claim root (`ROOT_IMMUTABLE`); `plan_ref` cannot change |
| `_rev` tree | append-only version chain: `update()` never edits, the new version `supersedes` the old, the old stays `is_current=false` |
| conflicting leaves | `dom_update(mode="fork")`: a parallel variant `-V<n>` with `variant_of`, both current, an open conflict |
| deterministic winner | `is_current` / replacement chain |
| loser preserved | every version stays in history; archive keeps the row |
| no built-in merge | `dom_reconcile` / `reconcile_variants` converge variants that agree; `CONFLICT_READ_REQUIRED` and `PLAN_CONFLICT` are the explicit wrapper |
| compaction loses revisions | no compaction: the hash-chained ledger keeps every revision, so a diff is always recoverable |

So the "updating claims" problem is already transformed into "resolving competing
revisions" — exactly as CouchDB forces — and the wrapper for it exists. The plan
layer is where the identity rule is missing.

## 4. The gap: the plan layer crosses the identity boundary

An item's identity is its `_id` equivalent:

```python
tid = 'pt-' + digest({'parent': parent, 'title': norm(node['title'])})[:16]
```

`flatten` then applies a rename heuristic (planning.py):

```python
if previous and tid not in previous.values():
    old = previous.get((parent, path))
    if old is not None and old not in naive and old not in ids:
        tid = old          # an item at the same place with a new title = a rename
```

`previous` maps `(parent_id, path) -> plan_task_id` of the current projection and
is built only for `mode='full'` (planning.py, `ingest`). The heuristic is correct
**inside one plan**: a renamed item in a revision keeps its identity and becomes a
new version of its header claim. It is wrong **across a plan change**: it reuses
the old id at the same position even when the whole plan is different, because
nothing checks whether the plan root itself changed.

This is the plan-layer equivalent of CouchDB reusing a document `_id` for a
different document. CouchDB never does that, because `_id` is immutable.

### r11 evidence (`plan_task_versions`, live DB)

| Position | r10 (QA004) | r11 | What happened |
|---|---|---|---|
| П1 | "Закрыть находки QA004…" | "Публикация репозитория на GitHub" | rename — same `pt-11e18d…`, v5 |
| П1.1 | "F-33 и F-01: план из plan mode…" | "Как писать и проверять" | rename — same `pt-e3da0c…`, v7 |
| П1.2 | "F-35: нелатинские ключи…" | "README и get-started" | rename — same `pt-6104d8…`, v5 |
| other 13 items | QA004 items | absent | archived by `mode='full'` |

The old claims are intact in history; the current projection is the new plan.

## 5. Decision

> **The identity of a plan is the identity of its root item** — the first
> top-level item (normally `П1`). A change of the root title starts a new plan,
> not a revision. Renaming items is allowed only inside one plan (root unchanged).

Consequences:

* **New plan** (root title changed): every auto-derived item gets a fresh
  `plan_task_id` → new branches → new header claims. The previous plan is
  archived as a unit by `mode='full'`; its claims and versions stay in history.
* **Same plan** (root unchanged): the rename heuristic stays as it is today; a
  renamed item keeps its id and becomes a new version of its header claim.

This is the plan-layer version of CouchDB's `_id` immutability: identity is
declared by the root, not inferred from a position.

## 6. Behavior rules

1. **Root detection.** The previous root is the first top-level item of the
   current projection — the entry in `previous` with `parent_id is None` and the
   smallest position. The new root id is the root's explicit `id` when present,
   otherwise `'pt-' + digest({'parent': None, 'title': norm(root_title)})[:16]`.
2. **New plan.** If both ids exist and differ, the whole tree is treated as a new
   plan: `previous` is dropped for this ingest, so no id is reused anywhere in
   the tree (not just at the root).
3. **Explicit ids win.** A caller that sends `id` on the root declares continuity
   explicitly; the guard does not fire. Explicit ids are already honored by
   `flatten` before the heuristic.
4. **Partial mode is unaffected.** `mode='partial'` already passes
   `previous=None` (a left-out item is not removed), so the heuristic never runs
   there.
5. **No other identity rule changes.** Claim identity, versioning, branching and
   conflict handling stay as they are.

## 7. Implementation sketch (not applied)

Inside `flatten(roots, previous)`, before the walk:

```python
def _first_top(previous):
    """plan_task_id of the first top-level item of the current projection."""
    tops = []
    for (parent, path), tid in previous.items():
        if parent is None:
            suffix = path[len(PLAN_PREFIX):]
            tops.append((int(suffix) if suffix.isdigit() else 0, tid))
    return min(tops)[1] if tops else None


def flatten(roots, previous=None):
    # norm and numbers are defined above this point
    if previous and roots:
        root = roots[0]
        root_id = root.get('id') or 'pt-' + digest({'parent': None, 'title': norm(root['title'])})[:16]
        old_root = _first_top(previous)
        if old_root is not None and old_root != root_id:
            previous = None   # the plan root changed -> a new plan, not a revision
    ...
```

`norm` and `numbers` currently sit below the `previous` block inside `flatten`;
both must be moved above it (or extracted to module level) so the guard uses the
same normalization and numbering as item ids.

Size: a small helper plus a guard (about 12 lines) in
`opencode/mockup/planning.py`, mirrored to `claude/mockup/planning.py`. No schema
change, no new MCP tool, no migration.

## 8. Edge cases and known limits

* **Same root, changed children** → revision, as today.
* **New root, identical child titles** → new plan; all ids are fresh because the
  guard drops `previous` for the whole tree.
* **Explicit root id** → caller-declared continuity; guard does not fire.
* **Old plan had no root** (empty or fully archived) → no guard; heuristic runs
  as today.
* **Several top-level items** → the first top-level item is the anchor; changes
  to the others are revisions of the same plan.
* **A new plan with the same root title** cannot be expressed by this rule. If
  that is ever needed, add an explicit `new_plan` flag — never infer it.
* The rule does not touch `plan_task_versions`, `plan_conflicts` or the ledger
  schema; it only decides which ids `flatten` derives.

## 9. Acceptance criteria

**Test A — new plan creates new claims (the r11 case).**
1. Fresh project. Ingest A (`mode='full'`):
   `1. План A\n1.1 A1\n1.2 A2\n2. A3`.
2. Ingest B (`mode='full'`): `1. План B\n1.1 B1\n1.2 B2\n2. B3`.
3. Assert: every B item's `plan_task_id` is absent from A's ids; every A item is
   archived (`archived=1`); A's header claims are `is_current=false`; B's header
   claims are new claim ids.

**Test B — revision keeps identity.**
1. Fresh project. Ingest A: `1. План A\n1.1 A1\n1.2 A2\n2. A3`.
2. Ingest A again with one item renamed:
   `1. План A\n1.1 A1 renamed\n1.2 A2\n2. A3`.
3. Assert: root and unchanged items keep their ids; `П1.1` keeps its id and gets
   a new header version (`action='updated'`, `previous` set).

**Test C — partial mode unaffected.**
1. `mode='partial'` adding `3. Новый пункт` → a new id; existing items untouched.

**Test D — r11 replay (manual, against the backup).**
Replay r10 → r11 on
`omh-baseline-dom-mcp/.dom-memory/backups/2026-11-14-r11-before-restore/dom.sqlite3`
and confirm no r10 `plan_task_id` appears in r11's current projection and all r10
items are archived.

## 10. Non-goals

* **No CouchDB migration.** The ledger is append-only and hash-chained, has no
  compaction, and already keeps every revision — stronger than CouchDB for diffs.
  The conflict wrapper (`PLAN_CONFLICT`, `open_conflicts`, `force`,
  `candidate`/`accept`, `dom_reconcile`) already exists.
* **No automatic merge of competing plans** beyond that wrapper.
* **No change to claim identity/versioning semantics.**
* **No new MCP tool or schema migration.**

## 11. References

* `opencode/mockup/planning.py` — `flatten` (identity + rename heuristic),
  `ingest` (`previous` for `mode='full'`), `_previous_headers`, `_conflict`,
  `reconcile_variants`.
* `opencode/mockup/admission.py` — `project_plan_snapshot` (archives unmentioned
  items in `mode='full'`, lines 755-764).
* `opencode/mockup/claim_entry.py` — `update` (version chain), `_fork`
  (`variant_of`), `reconcile`.
* `opencode/hooks/plan_hook.py` — `--plan-file`, `QUEUED_STALE`,
  `QUEUED_CONFLICT` (stage-1 guards, commit `21a0270`).
* Related decisions: TD-DOM-58 (`base_revision`), TD-DOM-57 (`session`),
  TD-DOM-60 (fork / read-before-write).
* Live evidence: r11 backup at
  `omh-baseline-dom-mcp/.dom-memory/backups/2026-11-14-r11-before-restore/dom.sqlite3`.
