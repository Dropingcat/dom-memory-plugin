"""E2E: the skill-queue is produced and delivered into the request under assembly.

Two levels, both against the real code path (no stub of the pipeline):

1. Producer: ``opencode/scripts/queue_producer.py`` is called exactly as the hook
   calls it.  For ``cap=["sqlite","migration"]`` it must return ``status=OK``,
   select ``sqlite-migration``, keep ``block_text`` within the 800-char budget,
   carry a fingerprint, and render its gap line.
2. Hook: the real plugin (``opencode/index.js``) is registered on a throwaway
   project.  After ``prompt`` (brief -> sigma) and ``context`` (producer ->
   request), the block must appear in ``event.system`` exactly once, and stderr
   must report ``skill-queue injected via system`` -- i.e. delivered through the
   request, never through the deferred queue fallback.

Run: python -B tests/check_skills_in_prompt.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCER = ROOT / "opencode" / "scripts" / "queue_producer.py"
BLOCK_BUDGET = 800
STEP = {
    "step_id": "П2",
    "title": "L2 V5 migration",
    "cap": ["sqlite", "migration"],
    "plan_version": 7,
}
BRIEF = "\n".join(
    [
        "[DOM MEMORY INDEX] plan r7 · 1 items / 1 open · 3 claims · CHAIN_OK",
        'CURRENT: П2 "L2 V5 migration" [pending] anchor abcdef12 CAP: sqlite,migration',
    ]
)


def run_producer(step: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", str(PRODUCER), "--step", json.dumps(step, ensure_ascii=False), "--no-cache"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        cwd=str(ROOT),
    )


def check_producer() -> dict:
    result = run_producer(STEP)
    assert result.returncode == 0, "producer must exit 0, got %r\n%s" % (result.returncode, result.stderr)
    assert result.stdout.count("\n") == 1, "producer prints one JSON line, got %r" % result.stdout[:200]
    data = json.loads(result.stdout)
    assert data.get("status") == "OK", "producer failed: %s" % result.stdout[:300]

    # 1. Queue contains sqlite-migration (cap matched).
    queue = data.get("queue", [])
    assert isinstance(queue, list) and all(isinstance(item, str) for item in queue), queue
    assert "sqlite-migration" in queue, "cap=[sqlite,migration] but queue=%r" % (queue,)
    assert "sqlite-migration" in data.get("block_text", ""), "top queue id is not visible in the block"

    # 2. Block size <= 800 chars.
    block = data["block_text"]
    assert isinstance(block, str) and block.strip(), data
    assert "\n".join(data["block"]) == block, "block lines and block_text disagree"
    assert len(block) <= BLOCK_BUDGET, "block too large: %d chars" % len(block)

    # 3. Fingerprint present, gap line rendered.
    assert data.get("fingerprint"), "no fingerprint: %r" % (data,)
    lower = block.lower()
    assert "gap:" in lower or "gaps:" in lower, "no gap line in block: %s" % block

    return data


def write_harness(tmp: Path, plugin: Path, project: Path, plans: Path, cache: Path) -> Path:
    """Write the Node E2E harness that drives the real plugin hooks."""
    harness = tmp / "hook_e2e.mjs"
    repo = json.dumps(str(ROOT))
    plugin_s = json.dumps(str(plugin))
    project_s = json.dumps(str(project))
    plans_s = json.dumps(str(plans))
    cache_s = json.dumps(str(cache))
    harness.write_text(
        f"""\
import {{ join, delimiter }} from "node:path"
import {{ pathToFileURL }} from "node:url"

const REPO = {repo}
const PLUGIN = {plugin_s}
const PROJECT = {project_s}
const PLANS = {plans_s}

// The copied producer lives in the temp plugin root, so its sibling `mockup/`
// package is found through PYTHONPATH: the real opencode directory.
process.env.PYTHONPATH = [join(REPO, "opencode"), process.env.PYTHONPATH].filter(Boolean).join(delimiter)
process.env.DOM_SKILL_QUEUE_CACHE = {cache_s}

const {{ register }} = await import(pathToFileURL(join(REPO, "opencode", "index.js")).href)

const handlers = {{}}
const puts = []
const ctx = {{
  options: {{ pluginRoot: PLUGIN, projectDir: PROJECT, planDir: PLANS, timeoutMs: 60000, skillTimeoutMs: 60000 }},
  location: {{ directory: PROJECT }},
  session: {{
    hook: async (name, fn) => {{
      handlers[name] = fn
      return {{ dispose() {{}} }}
    }},
    instructions: {{ entry: {{ put: (key, value) => puts.push([key, value]) }} }},
  }},
}}

const cleanup = await register(ctx)
const sessionID = `skills-in-prompt-${{process.pid}}-${{Date.now()}}`
try {{
  await handlers.prompt({{ sessionID, prompt: {{ text: "go" }} }})
  const event = {{ sessionID, system: [] }}
  await handlers.context(event)
  const texts = event.system.map((block) => (typeof block?.text === "string" ? block.text : ""))
  const skill = texts.filter((text) => text.includes("use: sqlite-migration"))
  process.stdout.write(
    JSON.stringify({{ system_blocks: texts.length, skill_blocks: skill.length, block_text: skill[0] || "", puts }}),
  )
}} finally {{
  cleanup()
}}
""",
        encoding="utf-8",
    )
    return harness


def check_delivery(expected_block: str) -> None:
    node = shutil.which("node")
    assert node, "node is required for the hook E2E"
    with tempfile.TemporaryDirectory(prefix="dom-skills-prompt-", ignore_cleanup_errors=True) as tmp_dir:
        tmp = Path(tmp_dir)
        plugin = tmp / "plugin"
        project = tmp / "project"
        plans = tmp / "plans"
        cache = tmp / "cache"
        for directory in (plugin / "hooks", plugin / "scripts", project, plans, cache):
            directory.mkdir(parents=True, exist_ok=True)

        # Hook stubs only: no plan to ingest, and a brief that carries the sigma.
        # The brief literal stays pure ASCII (\uXXXX escapes) and is written to
        # stdout as explicit UTF-8 bytes, so no console locale can corrupt it.
        (plugin / "hooks" / "plan_hook.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        (plugin / "hooks" / "plan_check_hook.py").write_text(
            "import sys\nsys.stdout.buffer.write(%s.encode('utf-8'))\n" % json.dumps(BRIEF),
            encoding="utf-8",
        )
        # The real producer, copied verbatim; mockup/ resolves via PYTHONPATH.
        shutil.copyfile(PRODUCER, plugin / "scripts" / "queue_producer.py")

        harness = write_harness(tmp, plugin, project, plans, cache)
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [node, str(harness)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
            cwd=str(ROOT),
            env=env,
        )
        assert result.returncode == 0, "hook harness failed:\n%s" % result.stderr
        payload = json.loads(result.stdout.strip().splitlines()[-1])

        stderr = result.stderr
        # 1. Delivered: the hook says so on stderr.
        assert "skill-queue injected" in stderr, "no injected marker on stderr:\n%s" % stderr
        # 3. Via the request under assembly, not the deferred queue fallback.
        assert "skill-queue injected via system" in stderr, "delivery was not via system:\n%s" % stderr
        assert "skill-queue queued" not in stderr, "fell back to the deferred queue:\n%s" % stderr
        assert "not delivered" not in stderr, "block was not delivered:\n%s" % stderr

        # 2. The delivered block is the producer's block, within budget, once.
        assert payload["skill_blocks"] == 1, "block delivered %d times: %r" % (
            payload["skill_blocks"],
            payload["system_blocks"],
        )
        delivered = payload["block_text"].strip()
        assert delivered == expected_block.strip(), "delivered block != producer block:\n%r\n---\n%r" % (
            delivered,
            expected_block,
        )
        assert len(delivered) <= BLOCK_BUDGET, "delivered block too large: %d chars" % len(delivered)
        assert payload["puts"] and payload["puts"][0][0] == "dom.skill-queue", payload["puts"]


def main() -> int:
    data = check_producer()
    check_delivery(data["block_text"])
    print(
        "PASS check_skills_in_prompt: queue=%r, block=%d chars, injected via system"
        % (data["queue"], len(data["block_text"]))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
