# Skill routing and Panel — 0.3.1 concept

**Status:** concept captured 2026-11-24 from the user's decomposition. Not implemented.
0.3.0 (П15, trusted write loop) keeps priority; this file is the contract for 0.3.1.
**Target:** 0.3.1 (Panel + deterministic skill routing).
**Boundary (D-013):** DOM records the plan, the queue state and the outcomes. The router is an
external deterministic script. DOM does not choose skills, agents or models, and no model runs
inside the classifier.

## Source decomposition — 7 blocks

| # | Source phrasing | Meaning | Technical task |
|---|---|---|---|
| Б1 | "Инжекции реализованы грубо, нет блоков, нет связки скрипт-скрипт, низкая валидность" | The monolithic `skill → prompt → skill` ReAct loop puts tool choice on the model | Replace it with a typed `script → script` contract |
| Б2 | "Дашборд-блок скилов: по умолчанию + сессионные + динамические" | Skills have different roles and lifetimes | Three layers with separate load policies |
| Б3 | "DOM-план 1-2-3-4, склейка скилов, анализ шага N+1 на шаге N" | Predictive planning | Live queue with prefetch and timely injection |
| Б4 | "На шаге 2 инжектируем только ГД, очередь на 3 и 4 уже готова" | Queue advance | State machine with an atomic `step_id` move |
| Б5 | "Успех/провал/ошибка/неприменение → обучение" | Four outcomes with different learning effects | Feedback loop with differentiated reflection |
| Б6 | "Стабильная инжекция + минимальное количество (= достаточность)" | KPI | Sufficiency metric, not a bare minimum |
| Б7 | "Цель формируется промтингом, смена цели = её достижение" | Plan is not static | Goal-driven replanning in DOM |

## Б1 — why the current shape fails

- No dedicated prompt blocks, so the model decides everything in one turn.
- Low validity: the model hallucinates skills it does not have.
- SkillJect-style injection risk.

Fix: a deterministic router between modules with a typed contract (JSON Schema / Pydantic shape).
One user, so the contract can be strict rather than universal.

```
script_A (router) --JSON contract--> script_B (harness)
      |                                  |
      +---------- skill_registry <-------+
```

## Б2 — three skill layers

| Layer | Contents | Load policy | Updates |
|---|---|---|---|
| Default | System skills (registry search, diagnostics) | Always in context | Never |
| Session | User preferences, persistent skills grown from one user's patterns | Lazy, on first use | On session change |
| Dynamic | Skills for the current step, prefetch for the next | Injected per step | Every step |

Correction to the original phrasing: registry search is **not** a default skill. "The agent does
not search — the router searches." Search is a fallback skill injected only when the selector fails.

```json
"skills": {
  "default_skills":      { "policy": "always",         "fallback_only": ["skill_search"] },
  "session_skills":      { "policy": "lazy_first_use", "entries": ["user_patterns"] },
  "dynamic_skill_queue": { "policy": "step_bound",     "current": [], "next": [] }
}
```

## Б3 — live queue from the DOM plan

The DOM plan tree is the router's source of truth. Skills are glued to steps, not chosen by the
model. "Minimal sufficiency" = every subtask of the step is covered without redundancy.

```python
def prepare_dynamic_skill_queue(plan, current_id, history):
    current_step = plan[current_id]
    next_id = plan.next(current_id)

    current_skills = classify(current_step.type)
    next_skills = classify(plan[next_id].type) if next_id else []

    corrections = extract_lessons(history, window=5)
    current_skills |= corrections.additions
    current_skills -= corrections.removals

    if compute_coverage(current_step.subtasks, current_skills) < 0.9:
        current_skills.append("skill_search")  # fallback

    return {"current": current_skills, "prefetch": next_skills}
```

## Б4 — queue advance as a state machine

```
S_n    : current = P_n,   prefetch = P_{n+1}
  --[step P_n completed]-->  S_{n+1} : current = P_{n+1}, prefetch = P_{n+2}
  --[goal changed]------->   S_1'    : current = P_1',   prefetch = P_2'   (full reload)
```

Every transition is appended to the DOM ledger with the previous hash, so an audit can replay the
queue. The queue state is a claim/property, not a second database.

## Б5 — four outcomes, four effects

| Outcome | Source phrasing | What it teaches | Correction |
|---|---|---|---|
| success | "применение без ошибки" | Confirms the heuristic | Weight up |
| error | "применение с ошибкой" | The skill description is wrong | Patch `skill.description` |
| failure | "неприменение скила" | The selector chose wrong | Add a prerequisite to the classifier |
| queue_miss | "агенту пришлось искать" | The router missed | Promote the skill to default or session |

Personalization is a Bayesian weight update per user, not an ML model:

```python
Δ = {"success": +0.05, "error": -0.10, "failure": -0.05, "queue_miss": 0.0}
user_skill_weights[tool] = clip(user_skill_weights[tool] + Δ[outcome], 0.0, 1.0)
```

Weights and episodes are stored as DOM claims (properties + versions), so they are auditable.

## Б6 — sufficiency KPI

```
SufficiencyScore = covered_subtasks / total_subtasks * SkillEfficiency
SkillEfficiency  = successful_tool_calls / (injected_skills * react_iterations)
```

Target: `SufficiencyScore > 0.9`, with `injected_skills` close to the minimum cover set. The minimum
is a direction, not a hard target: cutting a needed skill is worse than injecting one extra.

## Б7 — goal-driven replanning

```
active --[subgoal completed]--> replan(queue, remaining_goals)
active --[goal changed]-------> rebuild_queue(new_goal)
active --[all done]-----------> terminate_session()
```

## Four layers

```
LAYER 1  Goal management        DOM plan_tree, replanning, hash-chained ledger
LAYER 2  Skill router           classify -> corrections -> coverage -> queue (deterministic)
LAYER 3  Agent harness          3-level skill prompt, structured output
LAYER 4  Reflection & learning  outcome class -> weights -> registry patch -> episodic claim
```

## Validation plan

**Empirical:** cold start (`SufficiencyScore > 0.7` from classifier priors); goal change reloads the
queue without losing weight history; `skill_search` fires and teaches the router.

**Theoretical:** Guarded Agent (injection defence) → Layers 1–2; Progressive Loading → Layer 3;
Reflexion/OPAR → Layer 4; Context Engineering → prefetch; Harness Learning → weight update.

**Architectural risks:**

| Risk | Mitigation |
|---|---|
| Weight drift when the user changes style | Sliding window + exponential decay |
| Yes-Man reflection | Deterministic outcome classifier, no LLM |
| Race on parallel steps | SQLite WAL + advisory lock in DOM |
| Overfitting to one user | Reset to prior every 100 steps |
| Fallback flooding | Alert when `queue_miss > 20%`, review the registry |

## Channels (verified 2026-11-24)

Sources: `opencode.ai/docs/build/plugins/server` (Hooks → Sessions, Tools, Skills) and the live
plugin `opencode/index.js`, which is a V2 plugin (`export default { id, setup(ctx) }`).

| Channel | When it runs | What it can change | Use for |
|---|---|---|---|
| `session.hook("prompt")` | once per user prompt, before skill resolution | `prompt.text`, `files`, `agents`, `skills` | attach first-step skills natively |
| `session.instructions.entry.put/remove` | keyed entry; changes are announced at the **next step boundary** | a `<context key="...">` block in session instructions | the live skill queue |
| `session.hook("context")` | every model call, including tool-driven continuations | `event.system` parts, `event.tools`, `event.options` | the plan brief (`dom.notes`) |
| `tool.hook("execute.before"/"after")` | per tool call | inspect/replace input; observe result | advisory gate, `queue_miss` accounting |
| `ctx.skill.list()/transform` | registry read/edit at the location | skill definitions | the router's registry index |

### Decision 1 — the queue does not ride `dom.notes`

`dom.notes` stays the plan brief: bounded (3,400 chars), stable, shared with the Claude Code path.
The queue is a keyed instruction entry `dom.skill-queue` with step id, current ids + digests,
prefetch ids and the sufficiency flag. The host announces the new entry at the next step boundary,
so the switch costs no extra turn and no system-block rewrite.

Caveat: the host's "step boundary" is a model step, not a DOM plan step. The router advances the DOM
step explicitly; the entry becomes visible at the next model call (at most one step of lag). If that
lag matters, the plugin replaces the entry right after the `execute.after` that closes the step.

### Decision 2 — registry is `ctx.skill.list()`

The runtime registry is authoritative: `{ id, name, description, path, content }`. The router builds
its index at session start and pins `sha256(content)` per skill into the queue entry. No custom
registry file; the index is derived, never hand-edited.

### Decision 3 — weights live in plugin storage, snapshots in the ledger

`ctx.storage` holds hot per-user weights (`dom/weights`). A claim property `skill_weights` is written
every 10 steps or when a weight moves by more than 0.1 — auditable without a claim per outcome.

### Decision 4 — one transition per plan step, not per model call

Each step advance writes one property event on the queue claim:
`{step, current, prefetch, sufficiency, digest, outcome}`; the instruction entry is replaced in the
same plugin call. The ledger's hash chain covers the audit; no new table.

### Decision 5 — `queue_miss` is observed, not self-reported

`tool.hook("execute.after")` watches the skill tool: a requested id absent from the current queue is
a miss. Counter in `ctx.storage`, claim property every 10 misses, alert above 20%.

### Decision 6 — Panel reads DOM, never the host

Panel queries the monitor JSON API plus `/api/queue` and `/api/outcomes`, both projections of DOM
claims. It must not scrape instruction entries or snapshots for state.

### Decision 7 — attache skills at admission for the first step

`prompt.skills` in the prompt hook is resolved and validated by the core, so the first step of a turn
uses native skill loading; later steps use the queue entry. Full skill text is never pasted into the
queue — ids and one-line "when to use" only.

## Still open

1. Skill id stability: does a rename keep `Skill.ID`, and is there a version per skill content?
2. Entry size cap: the host does not document one; keep the entry under ~800 chars and measure.
3. Subagents: does a child session inherit `dom.skill-queue`, or does the router issue a child queue?
4. Prompt cache: replacing the entry every step may invalidate provider caching; measure on two
   sequential calls before enabling per-step replacement by default.
5. Review ownership: who approves a `skill.description` patch after an `error` outcome — the owner,
   or the router with a validation gate?

## Spike before implementation (first 0.3.1 task)

1. Put a `dom.skill-queue` entry into a test session; confirm it appears in the next model request
   and is visible as a `<context key="dom.skill-queue">` block.
2. Remove it; confirm it disappears at the next step boundary.
3. Measure the entry's chars and the cache behaviour across two sequential calls.
4. Only then wire the router: read `ctx.skill.list()`, classify, write the entry, record the
   transition claim.


## Injection contract (draft)

```json
{
  "step_id": "П15.1",
  "goal": "0.3.0 trusted write loop",
  "current": ["dom-memory", "python-test"],
  "prefetch": ["sqlite-migration"],
  "fallback": ["skill_search"],
  "sufficiency": 0.93,
  "outcomes": {"success": 4, "error": 1, "failure": 0, "queue_miss": 0}
}
```

The brief stays a pointer: `dom.notes` carries step ids and skill names; full skill text is loaded by
the harness only for the current step.
