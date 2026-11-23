# Task 0 — memory resolver and preflight (2026-11-23)

## Scope

- `opencode/mockup/paths.py`: one resolver, `DOM_DIRECTORY` wins, `OPENCODE_PROJECT_DIR` wins over `CLAUDE_PROJECT_DIR`, `db_path` realpath.
- `opencode/scripts/dom_server.py`: uses `resolve_paths()`.
- `opencode/index.js`: exports `DOM_DIRECTORY`/`DOM_PROJECT_DIR` to hook child processes.
- `opencode/mockup/claim_entry.py` + `public_dto.py`: `dom_describe` returns `directory` and `db_path`; public projection accepts them.
- `config/memory_registry.json` and `scripts/router/memory_preflight.py`: router preflight pattern.

## Checks

| Check | Command | Result |
|---|---|---|
| resolver | `python -B tests/check_memory_paths.py` | PASS: precedence, explicit, unicode, relative, realpath, describe |
| preflight | `python -B tests/check_memory_preflight.py` | PASS: selected, mismatch, counts, unicode, realpath |
| inbox regression | `python -B tests/check_inbox_status.py` | PASS both trees |
| statement capture | `python -B tests/check_statement_capture.py` | PASS both trees |
| distillate pipeline | `python -B tests/check_distillate_pipeline.py` | PASS both trees |
| distillate audit | `python -B tests/check_distillate_audit.py` | PASS both trees |

## Real preflight snapshot

`python scripts/router/memory_preflight.py --output config/memory_runtime_snapshot.json`

| Project | Claims | Plan | Chain |
|---|---:|---|---|
| plugin | 103 | r5 | CHAIN_OK |
| live | 201 | r16 | CHAIN_OK |

`selected=live`, `warnings=[]`, `policy_hash=85845d836c6247febbfe423ad22af4c9c767a1dd7b33335860f46fb91aaaceaf`.

## MCP probe

`tool: dom_describe` through repo stdio server with `DOM_DIRECTORY=.../tmp/opencode/dom-probe`:

```json
{
  "status": "DESCRIBED",
  "profile": "lite",
  "directory": "E:\\opencode_harness_portable\\tmp\\opencode\\dom-probe",
  "db_path": "E:\\opencode_harness_portable\\tmp\\opencode\\dom-probe\\dom.sqlite3",
  "review_mode": "auto"
}
```

The first probe returned `PUBLIC_PROJECTION_INVALID`; `Described` DTO was missing `directory`/`db_path`. Fixed and re-probed.

## Mirror

SHA256 equal in both trees:

- `opencode/index.js` = `claude/opencode-plugin/index.js`
- `opencode/mockup/paths.py` = `claude/mockup/paths.py`
- `opencode/mockup/claim_entry.py` = `claude/mockup/claim_entry.py`
- `opencode/mockup/public_dto.py` = `claude/mockup/public_dto.py`
- `opencode/scripts/dom_server.py` = `claude/scripts/dom_server.py`

## Open

- The live MCP process still runs 0.2.4; it will not show `directory`/`db_path` until rebuild/install/restart (release card, not this task).
- `memory_runtime_snapshot.json` is generated and machine-specific; do not commit it as a portable artifact.
