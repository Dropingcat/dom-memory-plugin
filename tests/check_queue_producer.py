"""Contract 1.3 bridge check: a CAP envelope reaches the queue producer.

`opencode/index.js` parses ``CAP: a,b`` off the plan brief's CURRENT line and
passes ``{"cap": ["a","b"]}`` to ``opencode/scripts/queue_producer.py``.  This
check pins the Python half of that bridge: the producer must treat the declared
capabilities as a signal and select the curated ``sqlite-migration`` card.

Run: python -B tests/check_queue_producer.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "opencode" / "scripts" / "queue_producer.py"


def produce(step, env):
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--step", json.dumps(step, ensure_ascii=False), "--no-cache"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert result.stdout.count("\n") == 1, result.stdout[:200]
    data = json.loads(result.stdout)
    assert data.get("status") == "OK", data
    return data


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="dom-queue-cap-") as scratch:
        env = dict(os.environ)
        env["DOM_SKILL_QUEUE_CACHE"] = str(Path(scratch) / "cache")
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        # 1. The CAP list from the brief is the sigma's declared envelope.
        data = produce({"title": "L2 V5 migration", "cap": ["sqlite", "migration"]}, env)
        assert data["state"]["capSet"] == ["migration", "sqlite"], data["state"]
        queue = data["queue"]
        assert "sqlite-migration" in queue, "queue must include sqlite-migration: %r" % (queue,)
        assert "sqlite-migration" in data["block_text"], data["block_text"]

        # 2. Without a declared cap there is no invented cap signal (fail-open).
        empty = produce({"title": "L2 V5 migration"}, env)
        assert empty["state"]["capSet"] == [], empty["state"]
        assert empty["missing_caps"] == [], empty["missing_caps"]

    print("PASS queue_producer: cap from brief selects the sqlite-migration card")
    return 0


if __name__ == "__main__":
    sys.exit(main())
