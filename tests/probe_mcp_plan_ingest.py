"""Latency probe: existing dom_plan_ingest over MCP stdio.

This is deliberately not a production change: it measures the current MCP
tool and lists the hook-equivalent fields the tool does not accept.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:
    print("SKIP probe_mcp_plan_ingest: mcp module not available")
    raise SystemExit(0)

SERVER = Path(__file__).resolve().parents[1] / "opencode" / "scripts" / "dom_server.py"
PY = sys.executable


def _pct(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


async def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        project = Path(tmp)
        run = project / ".dom-memory" / "main"
        run.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update({
            "DOM_DIRECTORY": str(run),
            "DOM_RUNTIME_ROOT": str(project / ".dom-memory"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        })
        params = StdioServerParameters(command=PY, args=[str(SERVER)], env=env)
        samples: list[float] = []
        statuses: list[str] = []
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                started = time.perf_counter()
                await session.call_tool("dom_start", {"request": "mcp plan ingest latency probe"})
                samples.append((time.perf_counter() - started) * 1000)
                for index in range(10):
                    plan = "1. Probe item\n1.1 Child %d\n2. Done" % index
                    started = time.perf_counter()
                    result = await session.call_tool("dom_plan_ingest", {
                        "plan": plan,
                        "source": "agent",
                        "mode": "full",
                    })
                    samples.append((time.perf_counter() - started) * 1000)
                    payload = json.loads(result.content[0].text)
                    statuses.append(str(payload.get("status")))
        values = samples[1:]
        evidence = {
            "server": str(SERVER),
            "runs": len(values),
            "samples_ms": [round(value, 3) for value in values],
            "min_ms": round(min(values), 3),
            "p50_ms": round(statistics.median(values), 3),
            "p95_ms": round(_pct(values, 0.95), 3),
            "max_ms": round(max(values), 3),
            "statuses": statuses,
            "missing_fields_for_hook_equivalence": ["origin", "session", "base_revision", "contexts", "stamp"],
        }
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
