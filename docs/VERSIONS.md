# Version lines

Three lines are kept at all times. They are separate on purpose: a live install must never receive unverified code.

## 1. Live

| Field | Value |
|---|---|
| Branch | `main` |
| Tag | `v0.2.6` |
| Version | `0.2.6` |
| Install target | `...omh-baseline-dom-mcp/.claude/worktrees/mcp-llm-server-054018/dist/claude/dom-memory` |
| Contents | observability: resolver, prompt snapshot, hook journal, read-only monitor |
| GitHub | `https://github.com/Dropingcat/dom-memory-plugin/releases/tag/v0.2.6` |

Rules: only a verified release is installed here; no feature branch code is copied into this target.

## 2. Pre-release

| Field | Value |
|---|---|
| Branch | `feature/trusted-write-loop-0.3.0` |
| Version | `0.3.0` (unreleased) |
| Contents | `WriteContext`, `claim_sessions`, `read_receipts`, DB gate |
| Plan | `docs/superpowers/plans/2026-11-23-trusted-write-loop-0.3.0.md` |

Rules: branch starts from live `main`; it is not installed into the live target until it passes checks and is tagged. The previous pre-release branch `feature/observability-0.2.6` is kept as history.

## 3. Planned features

| Field | Value |
|---|---|
| Version | `0.3.1+` (planned) |
| Contents | native OpenCode Panel, retrieval work, TRIZ/tech-card module, penalty/token-budget loop |
| Status | docs only; no code until promoted into a pre-release branch |

## Promotion

`planned → pre-release → live`:

1. A planned item gets a plan/tech cards and a pre-release branch.
2. The branch passes the full check suite and a code review/audit.
3. The live target is backed up, updated, service restarted, and probed.
4. A tag and GitHub release are created only after the live probe.
5. `main` and the live target move together; the old tag stays for rollback.

## Current rollback

- Live code backup: `...dist/claude/dom-memory-0.2.4-backup-2026-11-23`.
- Previous tag: `v0.2.4`.
- Data schema unchanged between 0.2.4 and 0.2.6; rollback is code-only.
