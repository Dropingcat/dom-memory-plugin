# Task 5 — MCP plan-ingest latency probe (2026-11-23)

## What was measured

`tests/probe_mcp_plan_ingest.py` starts the existing DOM server over MCP stdio, calls `dom_start`, then `dom_plan_ingest` 10 times with a new 3-item plan per run. No production code was changed.

## Results

| Metric | Value |
|---|---:|
| runs | 10 |
| min | 201.185 ms |
| p50 | 236.591 ms |
| p95 | 342.362 ms |
| max | 342.362 ms |
| statuses | 10 × `PLAN_INGESTED` |

All calls returned `PLAN_INGESTED`.

## Hook-equivalence gap

The MCP tool does not accept the fields the hook path needs:

- `origin` (hook/system/mcp)
- `session` (trusted launch session)
- `base_revision` (concurrent-ingest guard)
- `contexts` (discussion per item)
- stamp (plan-file consumption)

So this is a transport/latency probe, not a replacement for `plan_hook.py --now`.

## Decision

MCP-only plan ingest is **not** adopted in 0.2.6. It moves to 0.3.0 only if a user-agreed threshold is set and the five fields are added to the MCP contract. The measured p50/p95 are the baseline for that decision.

## Check

```powershell
& $py -B tests/probe_mcp_plan_ingest.py
# runs=10, p50=236.591, p95=342.362, statuses all PLAN_INGESTED
```
