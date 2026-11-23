# Task 2 — DOM-position prompt snapshot (2026-11-23)

## Scope

- `opencode/observability.js`: `snapshot(event, {source, notes})` writes `dom.prompt-snapshot/2`.
- `opencode/index.js`: the `context` hook snapshots after DOM injection and records the snapshot id/hash in the journal.
- `tests/check_prompt_snapshot.mjs`: direct snapshot, truncation, and registered-handler integration.

## Snapshot blocks

- `system`: array of blocks, redacted, capped.
- `messages`: roles and content parts; text redacted; media references only.
- `tools`: name, description, input schema; executable functions are never serialized.
- `options`: redacted; large options replaced with a preview.
- `dom.notes`: the exact DOM-injected notes with `source` and `claim_ids`.
- `counts`, `sha256_full`, `truncated`, `hook_order: "unknown"`.

`sha256_full` is computed over the full semantic snapshot before truncation. The file is local debug data, owner-only where the OS supports it, and redacted by default (`DOM_SNAPSHOT_FULL=1` opts into full text).

## Checks

| Check | Command | Result |
|---|---|---|
| direct snapshot, redaction, hash | `node --test tests/check_prompt_snapshot.mjs` | pass |
| truncation 300k | same | `truncated=true`, full hash differs from stored hash |
| registered context handler | same | snapshot file under `prompts/ses_snap/` |
| Task 1 regression | `node --test tests/check_observability.mjs` | 3/3 pass |

Observed:

```text
ok 1 - snapshot stores blocks, redacts secrets, and hashes the full semantic input
ok 2 - snapshot marks truncation and keeps the full-text hash
ok 3 - context hook writes a snapshot through the registered handler
# pass 3
# fail 0
```

## Honest boundary

This is the assembled semantic input at the DOM `context` hook, not the provider-lowered raw HTTP body. A plugin registered after DOM, or provider-specific lowering, is not in this snapshot; `hook_order` stays `unknown` until the installed runtime order is proven.

## Mirror

SHA256 equal:

- `opencode/index.js` = `claude/opencode-plugin/index.js`
- `opencode/observability.js` = `claude/opencode-plugin/observability.js`

## Open

- The live OpenCode plugin is not restarted; snapshots appear after the next host reload.
- Native Panel (0.3.1) will render the same snapshot JSON; the web monitor is Task 3–4.
