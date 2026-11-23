# Monitor: local token, claims versions, plan tree, prompt block attribution (2026-11-24)

## Local token

- `opencode/monitor/start.py` creates/reuses
  `<project>/.dom-memory/observability/monitor.token` and starts the server.
- Token is printed once by the start script and persisted only in that file.
- `tests/check_monitor_token.py` PASS: created once, reused, non-empty.
- The token is not committed and is not placed in the URL.

## Claims: properties and versions

The live root claim `55b50e87-29c3-461d-a10a-128be15e52ec` now renders as:

- text: board execution claim;
- identity: `version=1`, `current`;
- properties: `plan_revision=r16`, `plan_items=43`,
  `board_sha256=<sha256>`, `decisions=D-001…D-014`;
- anatomy: `basis`, `purpose`, `usage`, `keywords`.

The monitor now returns and renders `properties` with `name`, `value`, `reason`,
`at`, and a `versions` chain. Example: T-05
`7ce92f73-264b-4f42-bfb5-0898373d705f` is `v1` retired, superseded by
`a0ca55b4-e166-495a-82e2-6c9b932359e3` `v2` current. The claims table now has a
`v` column; the detail has `versions`.

`raw_sha256`, `payload` and event JSON moved behind a collapsed **raw JSON**
section; hashes are no longer the default view.

## Plan tree

`/api/plan/tree` returns a tree from `current_plan_tasks` + `plan_branches`:
`source_revision=r16`, 67 active tasks. The UI Plan tab renders
`path`, `title`, `branch`, `status`, `claim_count`.

Finding: the 67 tasks include legacy QA004 items (`П1`–`П4`) and the current
board (`П5`–`П13`). The board was ingested with `mode=partial`, so old items were
not archived. This is a data/planning decision, not a UI bug: either filter the
monitor to the current source revision or archive the legacy plan items.

## Snapshot attribution

Latest snapshot (`dom.prompt-snapshot/2`, 5 system blocks, 50 messages, 13
top-level tools, ~183k chars, `truncated=false`):

| Block | Origin | Chars |
|---|---|---:|
| 0 | `core/system` | 1,471 |
| 1 | `core/model` | 159 |
| 2 | `core/codemode,core/mcp-guidance,core/skill-guidance,core/date,core/environment` | 79,709 |
| 3 | `core/worktree` | 238 |
| 4 | `plugin/harness-superpowers-planning` | 2,000 |

- **DOM contribution in this snapshot:** `dom.notes=[]`. The DOM plugin writes
  the journal and the snapshot, but injects no plan/claim text when there is no
  pending plan check.
- **DOM tools are nested in `execute`:** the top-level `tools` object has 13
  entries (`edit`, `read`, `shell`, …), none named `dom_*`; the Code Mode block
  contains `tools["dom-memory"]...`. The snapshot now extracts `dom_tools` from
  that block and the UI shows them separately.
- **Other plugins:** `plugin/harness-superpowers-planning` is the only non-core
  system block; the 2,000-char block is from the harness planning plugin, not DOM.
- **Redaction bug:** old snapshots show `options.maxTokens="[REDACTED]"` because
  the key regex matched `token` inside `maxTokens`. The regex is now exact-key;
  new snapshots keep `maxTokens` and redact only `token`/`authorization`/etc.

## Checks

- `python -B tests/check_monitor_api.py` — PASS (versions, properties, plan tree).
- `python -B tests/check_monitor_ui.py` — PASS.
- `python -B tests/check_monitor_token.py` — PASS.
- `node --test tests/check_observability.mjs tests/check_prompt_snapshot.mjs` — 6/6 PASS.

## Open

- The live OpenCode plugin process still runs the pre-origin `observability.js`;
  the monitor enriches old snapshots server-side. A later service restart makes
  new snapshots carry `system_blocks` natively.
- Plan legacy cleanup/filter is a decision, not implemented.
