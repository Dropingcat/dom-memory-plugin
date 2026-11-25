# Portability and the single-module contract

**Status:** audit + contract, 2026-11-25. Run `python tools/audit_portability.py` (add `--strict`
for a gate). Five of six checks pass; the remaining failure is the real gap: there is no installable
module.

## Audit

| Check | Result | Evidence |
|---|---|---|
| absolute paths in code | PASS | fixed test interpreters and monitor examples |
| shared tree drift | PASS | 24 shared files, 0 differ |
| version consistency | PASS | VERSION = plugin.json = package.json = pyproject = 0.2.6 |
| capability data | PASS | `mockup/capabilities.json` v1, queue carries `capability_version` |
| tests portability | PASS | `DOM_PYTHON`/`PYTHON_BIN` fallback instead of a drive path |
| cli entry point | **FAIL** | `pyproject.toml` has `packages = []` and no `[project.scripts]` |

Docs still contain 20 absolute examples (informational; they are examples, not code).

## Weak spots, ranked

**P0 — there is no module to install.** `pyproject.toml` declares `packages = []`: the engine ships
inside `opencode/` and `claude/` trees, so a new machine gets it by unzipping an archive and editing
`opencode.json` with absolute paths. There is no `dom` command, no versioned entry point, no
`pip install`. This is the opposite of "one module, quick connect".

**P0 — multi-machine ledger is unsafe by default.** The ledger is SQLite with WAL inside
`<project>/.dom-memory`. Two machines opening the same file over a synced folder (OneDrive, SMB) can
lose writes or corrupt the chain. Needed: an explicit single-writer rule, a lock file, and
`dom export/import` for moving the ledger. WAL over a network share must be refused, not attempted.

**P1 — three copies of the engine.** Upstream worktree → `opencode/` + `claude/` → install target.
`tools/sync_from_upstream.py` needs the upstream checkout, which a new machine does not have. Drift
is now checked (py/js), but the direction is wrong: trees should be generated at build time, not
committed and manually synced.

**P1 — host APIs diverge.** `instructions.entry` and `tool.hook` are V2-only. The Claude path has
hooks but no instruction entries. The router core is pure, but delivery adapters differ; without a
capability probe and a degraded mode (file-based queue, no entry), the module silently does nothing
on the other host.

**P1 — cards must be seeded per machine.** Skill cards live in claims, which is portable, but
`provides` and `verified` do not travel with the skill files. A new machine has the same skills and
no capability data, so the router falls back to `skill_search` until cards are imported. Needed:
`dom export --skills` / `dom import --skills`, and `capability_version` skew detection (the queue
already carries the version).

**P2 — version skew.** `VERSION` is 0.2.6 while 0.3.1 work is unreleased; there is no dev marker or
changelog gate. A machine cannot tell whether two trees are the same build.

**P2 — monitor is loopback-only.** That is correct for safety, but there is no documented way to
reach it from another machine; keep it loopback and use an SSH tunnel, do not add a public bind.

**P2 — tests need a runner and one env contract.** The suite is a list of scripts; a fresh machine
needs to know the order and `DOM_PYTHON`. Add `tools/run_tests.py`.

**P3 — docs examples are absolute.** Keep them generic (`<project>`, `${DOM_PYTHON}`).

## Target contract: one module, many hosts

```text
dom doctor                     # read-only: python, deps, paths, config discovery, ledger, skills, versions
dom install --project <dir>    # idempotent: MCP + plugin + skill + profile, paths relative to the package
dom uninstall                  # remove what install wrote, keep the ledger
dom route --step <id> --json   # pure router; can read a registry file, no host needed
dom brief --project <dir>      # the same brief as the hook
dom monitor --project <dir>    # local token, loopback
dom export --ledger|--skills   # portable JSON/zip
dom import ...                 # idempotent merge, version-checked
dom verify                     # chain, schema, capability version, drift
dom lock                       # single-writer lock for the ledger
```

Package shape:

```text
dom/
  __init__.py
  cli.py            # the commands above
  engine/           # mockup/ (claims, plan, storage, server)
  router/           # skill_router + capabilities.json (data, versioned)
  hosts/opencode.py # adapter: entries, tool hooks, prompt.skills
  hosts/claude.py   # adapter: file queue, hooks
  data/             # schemas: ledger, cards, queue, capabilities
```

Rules:

1. **One source of truth.** Host trees are build artifacts generated from `dom/`, with a drift gate
   (`audit_portability.py`) in CI; never hand-edited.
2. **Schemas are versioned data.** `ledger_schema`, `capability_version`, `card_schema`,
   `queue_schema`. A queue from another machine with a different capability version is rejected, not
   guessed.
3. **The router is host-free.** `dom route` takes step JSON + registry JSON and writes queue JSON.
   Host adapters only deliver the queue; they do not score.
4. **Degraded mode is explicit.** No instruction entry → file queue + brief line; no tool hook →
   outcomes from the journal; the router still runs.
5. **One writer per ledger.** `dom lock` + per-machine ledger; moving data is export/import, never a
   shared file on a network drive.
6. **Doctor before install.** `dom install` refuses when `dom doctor` fails; the first run on a new
   machine is diagnostic, not mutating.

## Order of work

1. `tools/run_tests.py` + `dom doctor` (read-only) — the fastest way to make a new machine
   self-diagnosing.
2. `dom install/uninstall` — idempotent config writing, relative paths, profile wiring.
3. Package the engine and generate host trees from it; keep the drift gate.
4. `dom export/import` for ledger and skill cards; version checks.
5. Single-writer lock and the multi-machine policy.
6. `dom route` as the host-free entry point for CI and other harnesses.

## What changed today

- `tools/audit_portability.py` — six mechanical checks, `--strict` for gates.
- `mockup/capabilities.json` v1 — the capability dictionary and accept rules are data; the queue
  carries `capability_version`.
- Tests use `DOM_PYTHON`/`PYTHON_BIN` instead of a drive path; the two node tests pass with the env
  contract.
- Monitor usage examples no longer contain a drive path.
