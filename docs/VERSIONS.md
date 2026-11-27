# Version lines

Three lines are kept at all times. They are separate on purpose: a live install must never receive unverified code.

## 1. Live

| Field | Value |
|---|---|
| Branch | `main` |
| Tag | `v0.3.1` |
| Version | `0.3.1` |
| Install target | `...omh-baseline-dom-mcp/.claude/worktrees/mcp-llm-server-054018/dist/claude/dom-memory` |
| Contents | skill router pipeline (0.3.1) + trusted write loop (0.3.0) + observability (0.2.6) |
| GitHub | `https://github.com/Dropingcat/dom-memory-plugin/releases/tag/v0.3.1` |

Rules: only a verified release is installed here; no feature branch code is copied into this target.

## 2. Released history

| Tag | Contents |
|---|---|
| `v0.3.0` | trusted write loop: `WriteContext`, `claim_sessions`, `read_receipts`, DB-path health, advisory gate (L1–L8) |
| `v0.2.6` | observability: resolver, prompt snapshot, hook journal, read-only monitor |
| `v0.2.4` | semantic bridge baseline |

## 3. Current line

| Field | Value |
|---|---|
| Version | `0.3.1` |
| Contents | skill router: step registry (π/σ/τ), multi-marker consensus, BM25 char-ngram, hierarchical group filter, deep analysis + render, τ-gate, three-track outcomes, MCP tool `dom_skill_select` |
| Tests | 33 groups, 0 failed (one pre-existing `probe_mcp_plan_ingest` needs the optional `mcp` package) |
| Plan | `C:\Users\Arhys\.opencode\plan\2026-11-27-skill-router-full-plan.md` |

## 4. Planned features

| Field | Value |
|---|---|
| Version | `0.3.2+` (planned) |
| Contents | `dom doctor`, `dom install`, CLI entry point (`[project.scripts]`), packaging, drift gate |
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
