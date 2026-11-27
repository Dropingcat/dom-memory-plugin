"""Self-check: the skill queue producer (`opencode/scripts/queue_producer.py`).

The producer is the native bridge between the context hook (`index.js`) and the
Python pipeline (`mockup.skill_select.select_step`).  This check pins the envelope
the hook consumes, the fail-open contract, the cache parity and the fingerprint
identity the tau-gate depends on:

1. OK envelope: one JSON line, block <= 800 chars, lines join to block_text,
   `state` carries the normalised sigma, and `fingerprint` equals the Python
   BlockFingerprint of that exact state (the JS gate recomputes the same digest).
2. Missing step_id falls back to `current`; missing caps render as gaps.
3. Fail-open: invalid JSON, a non-object step, and no arguments all exit 0 with
   `{"status":"ERROR"}` and never a traceback on stdout.
4. Cache parity: a cached run and `--no-cache` return byte-identical results, and
   the cache entry is written under `DOM_SKILL_QUEUE_CACHE`.

Run: python -B tests/check_skill_queue_producer.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "opencode" / "scripts" / "queue_producer.py"
sys.path.insert(0, str(ROOT / "opencode"))

from mockup.skill_select import micro_step  # noqa: E402
from mockup.step_registry import BlockFingerprint  # noqa: E402

HEX64 = re.compile(r"^[a-f0-9]{64}$")


def run(args, env=None, stdin=None):
    """Run the producer exactly as the hook does: python -B script ..."""
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env or os.environ.copy(),
        timeout=120,
    )


def envelope(step, extra=(), env=None):
    result = run(["--step", json.dumps(step, ensure_ascii=False), *extra], env=env)
    assert result.returncode == 0, "producer must exit 0, got %r\n%s" % (result.returncode, result.stderr)
    assert result.stdout.count("\n") == 1, "producer prints one JSON line, got %r" % result.stdout[:200]
    data = json.loads(result.stdout)
    assert data.get("status") == "OK", data
    return data, result


def check_envelope(data):
    text = data["block_text"]
    assert isinstance(text, str) and text.strip(), data
    assert len(text) <= 800, "block budget: %d chars" % len(text)
    assert "\n".join(data["block"]) == text, "block lines and block_text disagree"
    assert HEX64.match(data["fingerprint"]), data["fingerprint"]
    assert HEX64.match(data["index_digest"]), data["index_digest"]
    assert data["block_text"].count("gap:") <= 1
    state = data["state"]
    assert set(state) == {"stepId", "capSet", "categoryHint", "markers", "planVersion", "indexDigest"}
    assert state["indexDigest"] == data["index_digest"]
    assert state["stepId"] == data["step_id"]
    assert state["planVersion"] == data["plan_version"]
    # The tau-gate identity: the Python fingerprint is the digest of the reported state.
    sigma = micro_step(
        {
            "step_id": state["stepId"],
            "title": data["block"][0],
            "cap": state["capSet"],
            "category_hint": state["categoryHint"],
            "markers": state["markers"],
            "plan_version": state["planVersion"],
        }
    )
    expected = BlockFingerprint.from_step(sigma, state["indexDigest"], plan_version=state["planVersion"]).digest()
    assert expected == data["fingerprint"], (expected, data["fingerprint"])
    # No capability outside the declared set may be reported as missing.
    declared = {str(cap).strip().lower() for cap in state["capSet"]}
    for cap in data["missing_caps"]:
        assert str(cap).strip().lower() in declared, "invented missing cap %r" % (cap,)
    assert data["gaps"] == data["missing_caps"], "gaps is the rendered (missing) capability list"
    assert isinstance(data["queue"], list) and all(isinstance(item, str) for item in data["queue"])
    assert isinstance(data["confidence_bp"], int) and data["confidence_bp"] >= 0
    if not data["queue"]:
        assert "gap:" in text or "gaps: none" in text, "an empty queue must render its gap line"
    else:
        assert data["queue"][0] in text, "the top queue id must be visible in the block"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="dom-skill-producer-") as scratch:
        env = dict(os.environ)
        env["DOM_SKILL_QUEUE_CACHE"] = str(Path(scratch) / "cache")
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        directory = str(Path(scratch) / "run")
        Path(directory).mkdir()
        step = {"step_id": "П2", "title": "L2 V5 migration of the sqlite ledger",
                "cap": ["sqlite", "migration"]}

        # 1. OK envelope + identity.
        data, _ = envelope(step, ["--directory", directory], env)
        check_envelope(data)
        assert data["step_id"] == "П2"
        assert data["queue"], "the curated migration card must be selected: %r" % (data,)

        # 2. step_id fallback and a visible gap for an uncovered capability.
        fallback, _ = envelope({"title": "L2 V5 migration", "cap": ["sqlite"]}, ["--no-cache"], env)
        check_envelope(fallback)
        assert fallback["step_id"] == "current"

        # 3. Fail-open on every malformed invocation (exit 0, JSON only).
        for args in (["--step", "not json"], ["--step", "[1,2]"], []):
            result = run(args, env=env)
            assert result.returncode == 0, (args, result.returncode)
            payload = json.loads(result.stdout)
            assert payload.get("status") == "ERROR" and payload.get("error"), (args, payload)

        # 4. Cache parity: the first run wrote an entry; a cached run and --no-cache agree.
        cached, _ = envelope(step, ["--directory", directory], env)
        fresh, _ = envelope(step, ["--directory", directory, "--no-cache"], env)
        assert cached == fresh, "the cache must not change the answer"
        cache_files = list((Path(scratch) / "cache").glob("queue-*.json"))
        assert cache_files, "no cache entry was written"
        assert json.loads(cache_files[0].read_text(encoding="utf-8"))["schema"] == 1

    print("PASS: queue producer envelope, fail-open, cache parity and tau identity")
    return 0


if __name__ == "__main__":
    sys.exit(main())
