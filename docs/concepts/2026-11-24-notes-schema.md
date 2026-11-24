# `dom.notes` schema v1 — blocks and variables

**Status:** implemented in `opencode/mockup/brief.py`; populated from DOM claims, plan headers and properties.
**Rule:** the note is a **pointer**, not a claim dump. Full text stays behind `dom_find` / `dom_context` / `dom_task_claims`.

## Blocks

| Block | Meaning | Source |
|---|---|---|
| `MAIN_TASK` | the project's main task and what it means | root request claim properties `main_task`, `main_task_meaning` |
| `ROOT_REQUEST` | the original request, verbatim | `project_root.request_claim_id` |
| `GRANDLINE` | the main project line | `MAIN_TASK` + current plan revision |
| `LINE` | root → branch → sub-branch → current block | `current_plan_tasks` parent chain; each level's header `context` property |
| `CURRENT` | the block being worked on | `current_task` property on the root claim; fallback: `in_progress`, else first open leaf |
| `ACTIVE` | whether the current block is pinned | `current_task` property |
| `CURRENT MEANING` | what the current block is | current header property `meaning` |
| `WHY` | why it matters for the main task | current header property `why` |
| `NOW` | what the agent is doing right now | current header property `now` |
| `DONE` | what is done in the current branch | sibling/child task statuses |
| `PLANNED` | what is next | next open siblings/children |
| `RISKS` | linked risk/analysis/decision claims | `task_claims` + text/decision claims with risk words |
| `CONFLICTS` | open plan conflicts | `planning.open_conflicts` |
| `VECTOR` | execution direction and next step | current header property `vector` |
| `BOUNDARIES` | scope boundaries relative to the grandline/line | current header property `boundaries` |
| `TOOLS` | tools used and their purpose | current/root header property `tools` |
| `GITS` | repositories, branches, commits | current/root header property `gits` |
| `RECENT STATEMENTS` | latest user/agent statements and distillates, thesis-only | latest main claims not linked to a plan item |
| `ANCHORS` | claim ids for root, plan, headers | computed |
| `CHECKLIST` | what the agent must hold | static DOM contract |

## Processes and requirements

### RISKS

1. Collect linked work claims of the current task (`task_claims`, role != `defines`).
2. Mark a claim as a risk when its text contains `risk`, `blocker`, `аналит`, `analysis`, `decision`, `риск` or `блокер`.
3. Count open plan conflicts and stale task links (`linked_at_plan_version` older than the task version).
4. Emit at most three risk previews and a `RISK CHECKS` line with the counts.
5. If no risk is found, write `none linked` **after** the checks; never invent a risk.

Requirement: every risk must name a claim id and a short thesis; full text is read with `dom_context`.

### CONFLICTS

1. Source: `planning.open_conflicts(c)` (unforced `plan_conflicts` not followed by an ingest of the same principal).
2. Show the count and the affected `item_paths`.
3. A conflict is blocking for the current branch; the remediation is `dom_reconcile` or a new plan ingest that supersedes it.
4. Do not hide a conflict behind `0 open` when the query failed; a failure is `not recorded`.

### CHECKLIST

The checklist is assembled, not static:

- baseline: hold baselines, mark current task, no overwrite, anatomy/basis, native branch, verify, update status;
- if the task has no `implements` link: add “link an implementing claim”;
- if it has no `validates` link: add “add validation evidence”;
- if conflicts: add “resolve the open plan conflict”;
- if risks: add “mitigate the linked risk”;
- if `NEXT` is non-empty: add “execute next step 1”.

Each item is an action with an anchor (claim id or plan path); the maximum is nine items.

### NEXT

`NEXT` is ordered, not a bag:

1. open siblings/children of the current task, in plan order;
2. missing graph links (`implements`, `validates`);
3. open conflicts;
4. linked risks.

Limit: four steps. Each step names a path or a concrete graph action. This is the sub-block that catches the “remnants” planning missed: if the plan has open items or the graph has a missing role, they become next steps instead of disappearing.

### GRAPH

`GRAPH` summarizes the task graph:

- number of linked work claims and roles (`implements`, `validates`, `documents`, `depends_on`, ...);
- edges are read with `dom_task_graph`: `supersedes`, `basis`, `refines`, `variant_of`, `task_link`;
- a task with only the `defines` header has no work graph; the checklist and `NEXT` say so.

Use the graph for the question-answer chain:

1. resolve the task from the user question (`П14.2`, branch, title);
2. read `dom_task_claims` and `dom_task_graph` for that task;
3. follow `basis`/`refines` to the source/distillate;
4. inject the anchor and a one-sentence thesis; the agent reads the full text with `dom_context`.

The graph decides **which** claims are relevant; the question decides **what** to ask; the brief only carries pointers.

## Knowledge graph on claims

The existing graph is a claim graph, not a separate graph database:

- a claim has versions and replacement edges (`supersedes`);
- anatomy adds `basis` and `refines` edges;
- plan links add `task_link` edges with a role;
- variants add `variant_of` and `merged_into` edges;
- conflicts add `conflict`/`resolve` events.

Example from the live plan (`П1.1`): each linked code/data/text claim has a `refines` edge to the item header and a `task_link` edge with role `documents`. For `П14.2`, after linking the implementation and validation claims, the graph reports `documents=2, implements=1, validates=1`.



## Value policy

- A missing value is printed as `not recorded`; the generator never invents it.
- Each baseline is the header claim's `context` property, shortened to fit.
- `CURRENT` is `CURRENT (auto)` when `current_task` is missing; the agent is told to pin it.
- `RECENT STATEMENTS` includes the claim type, short id and a preview; the full source/distillate is read with `dom_context`/`dom_distillate`.
- The whole note is capped at 3,400 characters.

## Traceability (SOURCES)

Every value carries its origin, printed as one `SOURCES` line:

```text
SOURCES: plan r17; root 149d33e5; meaning=property 5132659e; why=property 5132659e;
now=property 5132659e; vector=property 5132659e; boundaries=property 5132659e;
tools=property 5132659e; gits=property 5132659e
```

- `plan` is the revision of the plan projection, `root` is the root request claim.
- `property <id>` means the value is the header/root claim's curated property with that anchor.
- `context <id>` means the clause was taken from the header context after its marker.
- `claim <id>` means no property existed and the thesis was distilled from the latest linked work claim.
- `not recorded` means no source exists; the value is not guessed.
- `vector=default` means the block uses the built-in execution hint, not a DOM value.

`SOURCES` is the lookup key for the question-answer chain: read the anchor with `dom_context`,
walk `basis`/`refines`, and only then act. The brief stays a pointer; the ledger keeps the text.


## Injection

- The `prompt` hook generates the note and caches it per session.
- Every `context` call in the same turn re-injects the cached note, including tool continuations.
- A new user prompt replaces the cache and regenerates the note.
- The note is data, not instructions; the checklist is the only instruction-shaped block and is generated by the DOM plugin.

## Distillate / promptizer

`mockup/promptizer.py` implements the per-variable promptizer:

- one spec per variable (`prompt`, `limit`, `markers`, fallback `roles`);
- `mini_prompt(name)` is the mini-prompt an agent follows to curate the value;
- source order: curated property → plan header context clause (`Текущая задача:`, `Сейчас:`, …) → latest linked work claim → `not recorded`;
- `distill()` keeps a short value verbatim and cuts a long one on a sentence boundary, preferring the sentence that carries the variable's marker;
- anchors stay on the claim: the brief carries the thesis and the claim id, the full text is read with `dom_context`/`dom_distillate`.

The runtime never calls a model. V0 prefers the curated property and then the plan context; reading a `dom_distillate` chain as a source is a later step (there are no distillates in the live ledger yet).


## Example

```text
[DOM MEMORY INDEX] plan r17 · 73 items / 60 open · 256 claims · CHAIN_OK
MAIN TASK: Разработка плагина DOM — плагин ведения памяти проекта…
ROOT REQUEST: Гони здесь (149d33e5)
GRANDLINE: Разработка плагина DOM (plan r17)
LINE:
  П14 "Хуки, инъекции и notes-контур" [AN pending] baseline: …
  П14.2 "Notes blocks and variables" [AN200 pending] baseline: …
CURRENT: П14.2 … anchor 5132659e
ACTIVE: pinned by current_task=П14.2
CURRENT MEANING: Notes blocks and variables…
WHY: помогает агенту не терять контекст…
NOW: планирую действие по задаче [хуки и инъекции]…
DONE: 0 …; PLANNED: П14.1, П14.3, П14.4, П14.5
RISKS: none linked
CONFLICTS: 0 open
RECENT STATEMENTS: …
ANCHORS: root 149d33e5; plan r17; headers b3d6ff11, 5132659e
VECTOR: собрать notes-контур…
BOUNDARIES: не менять ядро claims/версии…
TOOLS: dom-memory, monitor, shell, edit, read, git
GITS: opencode-dom-memory-plugin; branch main; commit c3639be
CHECKLIST: …
```
