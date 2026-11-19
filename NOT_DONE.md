# Not done / not finalised

This is the honest list of what is **not** implemented or not finished. Read it
before relying on the plugin in serious workflows. Items are roughly grouped by
severity; nothing here is hidden — it is all known and deliberately scoped.

## Workflows that do not work yet

* **Per-session DOM is not a separate database.** A "DOM per session" is
  implemented as *automatic creation of the project DOM* plus attribution of every
  write to the session (`plan_checks.session`, `DOM_SESSION`) — not as a physically
  separate database per session. A separate DB per session was rejected because it
  would break the shared MCP server (its environment/`OPENCODE_PROJECT_DIR` is
  static), i.e. the agent would not see the memory.
* **No plan capture from chat when there is no plan file.** The OpenCode port reads
  the plan from a fresh plan file under `~/.opencode/plan` (written by the plan
  agent). If the plan only exists in the conversation and no file is written, it is
  not ingested automatically — you must use `dom_plan_ingest` manually.
* **Plan-review cycle is not wired as an app flow.** The plan-check duty cycle asks
  the agent to update the plan; a full "review my plan" human/agent cycle on top of
  it is not built.
* **No UI.** Everything is driven through MCP tools, hooks and SQLite. There is no
  viewer, report or dashboard outside the agent's own tool calls.

## Host coverage

* **Only Windows has been run live.** The OpenCode plugin and both hook programs
  were verified on Windows. macOS / Linux are expected to work (pure Python + Node,
  no OS-specific calls in the hook path) but have not been *tested*.
* **OpenCode plugin: verified live.** Cold start, plan ingress and the duty cycle
  were exercised against a real OpenCode session.
* **Claude Code plugin: built and structurally validated, not run live in the app**
  in this workspace. The `.plugin` archive and `hooks.json` follow the upstream
  template, but no Claude Code session was available here to smoke-test it.

## Engine / features

* **`mockup/` is a simulation-grade implementation.** The package name is honest:
  it simulates the DOM/MCP server with deterministic admission and delivery. It is
  not a hardened service — no auth, no multi-process locking beyond what the ledger
  implies, no daemon.
* **`DOM_LLM_PROVIDER=anthropic` is optional and untested end-to-end.** The default
  provider is `deterministic`; the anthropic provider depends on the `anthropic`
  package and was not exercised.
* **Review policy `independent` is present but barely exercised.** `auto` is the
  default path; `manual` and `independent` exist in the contract but lack a real
  reviewer integration.
* **No garbage collection / compaction.** The ledger is append-only and grows;
  claiming "old" notes as archived does not shrink the database.
* **No multi-project admin.** `OPENCODE_PROJECT_DIR` selects one project; switching
  projects means restarting the server with another directory.

## Statement distillate (stage 3)

* **Capture is stem-based, not semantic.** The hook captures a prompt only when
  a versioned marker pack (`capture_pack`) scores a sentence; a paraphrase with
  no marker and no project noun is a false negative until the user says
  "запомни"/"запиши" (an explicit request always captures). The pack is
  deliberately small; growing it is a versioned change, and the recorded digest
  says which pack captured a statement.
* **`by=user` is declared, not authenticated.** `accept(by='user')` is recorded
  in the ledger; the lite profile is single-owner, so the server cannot prove
  who called. A forged `by` is an audit finding, not a security boundary.
* **The chain stops after three failed reviews per logical statement.** V8
  refuses `propose`/`validate` with `DISTILLATE_LOOP`; there is no reset action
  in this slice — ask the user for a new instruction (a correction is a new
  source).
* **The v2 refinement skill is planned, not implemented (П4.7).** The chain
  (`propose` → `validate` V1-V8 → `accept`) exists and is tested; an agent-side
  skill that reliably produces a good compression with anchors is a separate
  slice.
* **No statement rollup in the brief.** The `statements` rollup (П4.2) is
  deliberately out of this slice; statements are read one chain at a time with
  `dom_distillate(action='show')`.
* **The per-session capture cap is not enforced.** The plan said "M per
  session" but never defined `M`; guessing a number would be a policy change,
  not a bug fix. Deduplication by the normalized prompt key is the only
  suppression in 0.2.4.
* **A captured source always gets a v1.** Source-only capture (the
  `NO_DISTILLATE` health path) is not implemented; `NO_DISTILLATE` stays a
  reserved §7 code. A future fix needs `propose` on a rootless source, which is
  a separate feature.
* **No NER in the runtime.** Capture is the deterministic marker pack. A NER
  model may only be an offline audit oracle over a frozen corpus; it is not a
  capture path and adds no network/model dependency to the hook.
* **The V5 numeral pack is Russian.** `10` vs `десять` fails, but an English
  word number (`ten`) is not compared to a digit: it is outside the explicit
  surface pack and passes V5. Adding a language is a pack change with tests,
  not a regex guess.
* **`VARIANT_CONFLICT` is not a refusal.** Two accepted variants of one source
  are reported only as the `DISTILLATE_VARIANT_CONFLICT` §7 health code; v1 has
  no converge action for it. The refusal surface is `STATEMENT_NOT_FOUND`
  (the capture status `NO_STATEMENT` is a different, non-refusal surface).
* **An archived source is read-only.** `show` still reports it with
  `source_state='superseded'` and no effective version; `propose`, `validate`,
  `accept` and `withdraw` refuse with `STATEMENT_NOT_FOUND`. `dispute`/`resolve`
  remain available for cleanup.

## Release / packaging

* **Not published to npm / PyPI.** The trees carry `package.json` and
  `pyproject.toml`, but nothing is published to any registry; distribution is by
  GitHub clone/zip only.
* **No `console_scripts` entry point.** `scripts/dom_server.py` is run by path; no
  `pip install` installs a `dom-server` command.
* **`dist/` archives are not signed / checksummed** in a release pipeline.
* **Versioning is manual.** `VERSION`, `package.json`, `pyproject.toml` and the
  manifests are bumped by hand on release.

## Testing

* **The pytest suite is not green on a system Python.** A large part of the engine
  does not import on the interpreter without the `mcp` package (`import mcp.types`);
  the maintainer's workflow runs tests through a dedicated venv.
* **The automated self-checks are host-free but not exhaustive**
  (`tests/smoke_cold_start.py`, `tests/check_plan_context.py`,
  `tests/check_inbox_status.py`, `tests/check_plan_identity.py`,
  `tests/check_statement_capture.py`, `tests/check_distillate_extract.py`,
  `tests/check_distillate_pipeline.py`, `tests/check_distillate_audit.py`).
  They cover the critical cold-start
  path, the plan context, the plan queue, plan identity, hook statement capture,
  the marker/splitter primitives, the whole distillate chain and its behavioural
  edges (word-numbers, quantifier/negation swaps, name surfaces, archived
  sources) in both host trees. There is no test for the review policies,
  encryption, or the anthropic provider.
* **No CI.** No GitHub Actions / pre-commit pipeline is configured yet.

## Known rough edges

* **Prompt-injection surface:** hook output is fed back into the model request; a
  malicious project file could try to steer the agent. Validate what the memory
  tells the agent.
* **Windows path form in examples:** `file:///C:/…` and `C:/…` are used throughout;
  on POSIX use `/absolute/path` and `file:///…`.
* **Deterministic extraction is lossy:** the deterministic claim provider records
  decisions/plan items structurally; natural-language nuance from long prompts is
  not reproduced verbatim except for the root request claim.
* **Plan context is extractive, not semantic:** the hook distils the plan
  discussion into per-item contexts by matching sentence units to the words of
  each item (word prefixes absorb inflection, frequent words weigh less). An item
  whose title shares no words with the discussion gets no context, and a reason
  stated in paraphrase can be missed; the agent is asked to add the missing
  `contexts` with `dom_plan_ingest`. No model is called, so the result is
  reproducible but not a paraphrase.
* **A new plan that keeps the old root title is not expressible.** Plan identity
  is the identity of the root item: renaming the root starts a new plan (fresh
  ids, old plan archived as a unit), while a genuinely different plan that reuses
  the old root title is recorded as a revision of it. There is no `new_plan`
  flag; the rule and its limits are in
  [`docs/plan-identity.md`](docs/plan-identity.md).

---

If you rely on any item above, that is exactly the kind of feedback the project
needs — open an issue rather than assume it works.
