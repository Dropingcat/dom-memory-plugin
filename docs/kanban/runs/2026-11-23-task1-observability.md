# Task 1 — hook observability journal (2026-11-23)

## Scope

- `opencode/observability.js`: per-PID UTC JSONL, 64 KiB line cap, retention by age, key redaction, `record` never throws, `dropped` counter.
- `opencode/index.js`: `prompt` and `context` hooks record duration, session, status, injected chars/sha256/preview, system block counts, `db_path`.
- `tests/check_observability.mjs`: drives the registered handlers through a fake `ctx`.

## Checks

| Check | Command | Result |
|---|---|---|
| journal + hook | `node --test tests/check_observability.mjs` | 3 pass / 0 fail |
| record failure | same | `dropped=1`, no throw |
| retention | same | old PID file deleted, current kept |

Observed test output:

```text
ok 1 - prompt hook writes one journal line with session, duration and db_path
ok 2 - record never throws and increments dropped on an impossible path
ok 3 - retention deletes old PID files but keeps the current one
# pass 3
# fail 0
```

## Journal shape

```json
{
  "ts": "2026-11-23T...",
  "pid": 1234,
  "hook": "prompt",
  "phase": "after",
  "session_id": "ses_1",
  "duration_ms": 588,
  "source": "dom.plan_check",
  "status": "ok",
  "injected": {"notes": 1, "chars": 80, "sha256": "...", "preview": "..."},
  "error": null,
  "db_path": ".../dom.sqlite3"
}
```

## Mirror

SHA256 equal:

- `opencode/index.js` = `claude/opencode-plugin/index.js`
- `opencode/observability.js` = `claude/opencode-plugin/observability.js`

## Open

- Task 2 adds the DOM-position snapshot; this journal intentionally stores only a preview and hash of injected text.
- The live OpenCode plugin is not restarted; new journal files appear after the next host reload.
