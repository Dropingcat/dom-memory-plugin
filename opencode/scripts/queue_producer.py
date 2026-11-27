#!/usr/bin/env python3
"""Skill queue producer: one micro-step (sigma) -> one injected skill block.

Called from ``opencode/index.js`` (the context hook) through ``spawnSync``:

    python -B scripts/queue_producer.py \
        --step '{"step_id":"П15","title":"...","cap":["sqlite"],"context":"..."}' \
        [--directory <dom run dir>] [--queue '[{...},{...}]'] [--no-cache]

The script is a thin adapter, not a second pipeline: every decision (index walk,
BM25, multi-marker hint, group filter, consensus, deep analysis, positional
render, fingerprint) is owned by ``mockup.skill_select.select_step``.  The JSON
envelope below only adds the identity fields the tau-gate in ``gates/tau_gate.js``
needs, so the JS hook and this process hash exactly the same sigma:

    {"status":"OK",
     "step_id": "...", "plan_version": 0,
     "index_digest": "...", "fingerprint": "...",
     "state": {"stepId": ..., "capSet": [...], "categoryHint": ...,
               "markers": [...], "planVersion": ..., "indexDigest": ...},
     "block": ["line", ...], "block_text": "...",
     "queue": [...], "advisory": [...], "prefetch": [...],
     "gaps": [...], "gap_skills_count": N, "missing_caps": [...],
     "groups": [...], "confidence_bp": N, "signals": {...}}

``gaps`` is the list the block actually renders (a capability the queue does not
cover); ``gap_skills_count`` is the count of indexed skills that were not selected
(the full ``select_step`` gap set), kept as a number to keep the payload small.

Fail-open contract: any failure prints a single ``{"status":"ERROR",...}`` object
on stdout and exits 0.  A broken skill index must never block a model request.

Performance: the context hook runs on every model call, and a cold index walk
costs ~0.5 s.  The producer therefore caches its full JSON result under a key
made of the producer schema, the step payload, and a *code+data stamp* (mtime and
size of every SKILL.md, every ``mockup/*.py`` and every ``mockup/*.json``).  Any
change to the pipeline or to a skill invalidates the entry, so the cache can
never disagree with a fresh run; ``--no-cache`` bypasses it entirely.

``--directory`` scopes the cache file to one DOM run directory (a deleted project
leaves its own cache behind, nothing else).  The selection itself never reads the
ledger: the queue is a pure function of the step, the index and the cards.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

# scripts/ -> opencode/, the package root that holds mockup/.
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

SCHEMA = 1
CACHE_ENV = "DOM_SKILL_QUEUE_CACHE"


# ---------------------------------------------------------------------------
# output / cache plumbing (stdlib only; used before any mockup import)
# ---------------------------------------------------------------------------

def emit(payload: dict) -> None:
    """One UTF-8 JSON line on stdout, whatever the console locale is."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover - exotic stdout
        pass
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _stat_rows(paths) -> list[str]:
    rows = []
    for path in paths:
        try:
            stat = Path(path).stat()
            rows.append("%s|%d|%d" % (path, stat.st_mtime_ns, stat.st_size))
        except OSError:
            rows.append("%s|-|-" % (path,))
    return rows


def index_stamp() -> str:
    """Code + data stamp: changes whenever the index inputs or the pipeline change."""
    from mockup.skill_index import SKILL_ROOTS

    paths: list = []
    for root in SKILL_ROOTS:
        root = Path(root)
        if not root.is_dir():
            continue
        try:
            paths.extend(sorted(root.rglob("SKILL.md"), key=str))
        except OSError:
            continue  # an unreadable root is skipped, exactly like SkillIndex.collect
    mockup_dir = PLUGIN_ROOT / "mockup"
    paths.extend(sorted(mockup_dir.glob("*.py"), key=str))
    paths.extend(sorted(mockup_dir.glob("*.json"), key=str))
    return hashlib.sha256("\n".join(_stat_rows(paths)).encode("utf-8")).hexdigest()


def cache_key(step: dict, queue_steps: list, stamp: str) -> str:
    payload = {"schema": SCHEMA, "stamp": stamp, "step": step, "queue": queue_steps}
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def cache_dir(directory: Path | None) -> Path:
    base = os.environ.get(CACHE_ENV)
    if base:
        return Path(base)
    scope = hashlib.sha256(str(directory or "").encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / "dom-skill-queue" / scope


def cache_read(directory: Path | None, key: str) -> dict | None:
    path = cache_dir(directory) / ("queue-%s.json" % key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if (not isinstance(payload, dict) or payload.get("schema") != SCHEMA
            or payload.get("key") != key):
        return None  # a foreign or corrupt entry is a miss, never an error
    result = payload.get("result")
    return result if isinstance(result, dict) else None


def cache_write(directory: Path | None, key: str, result: dict) -> None:
    try:
        target = cache_dir(directory)
        target.mkdir(parents=True, exist_ok=True)
        path = target / ("queue-%s.json" % key)
        temp = path.with_suffix(".%d.tmp" % os.getpid())
        temp.write_text(json.dumps({"schema": SCHEMA, "key": key, "result": result},
                                   ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)  # atomic: a reader never sees a half-written entry
    except OSError:
        pass  # the cache is an optimisation; a read-only temp dir is not an error


# ---------------------------------------------------------------------------
# the pipeline adapter
# ---------------------------------------------------------------------------

def build(step: dict, queue_steps: list) -> dict:
    """Run select_step for one sigma and return the JSON-safe envelope."""
    from mockup import skill_select
    from mockup.step_registry import BlockFingerprint

    step_obj = skill_select.micro_step(step, step.get("plan_version") or 0)
    result = skill_select.select_step(step_obj, queue=list(queue_steps or ()),
                                      plan_version=step_obj.plan_version)

    # The identity the JS gate recomputes.  It is read off the *normalised* sigma, so
    # the derived category hint and action markers are the ones the block was built
    # from, not the caller's raw input.
    state = {
        "stepId": step_obj.step_id,
        "capSet": list(step_obj.cap),
        "categoryHint": step_obj.category_hint,
        "markers": list(step_obj.markers),
        "planVersion": int(step_obj.plan_version),
        "indexDigest": result["index_digest"],
    }
    fingerprint = BlockFingerprint.from_step(
        step_obj, result["index_digest"], plan_version=step_obj.plan_version).digest()
    if fingerprint != result["fingerprint"]:  # pragma: no cover - both derive from the same sigma
        raise RuntimeError("fingerprint mismatch inside select_step")

    block_text = str(result["block"])
    return {
        "status": "OK",
        "schema": SCHEMA,
        "step_id": step_obj.step_id,
        "plan_version": int(step_obj.plan_version),
        "index_digest": result["index_digest"],
        "fingerprint": result["fingerprint"],
        "state": state,
        "block": block_text.split("\n"),
        "block_text": block_text,
        "queue": list(result["queue"]),
        "advisory": list(result["advisory"]),
        "prefetch": list(result["prefetch"]),
        "gaps": list(result["missing_caps"]),
        "gap_skills_count": len(result["gaps"]),
        "missing_caps": list(result["missing_caps"]),
        "groups": list(result["groups"]),
        "confidence_bp": int(result["confidence_bp"]),
        "signals": result["signals"],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="DOM skill queue producer (one sigma -> one block)")
    parser.add_argument("--step", required=True, help="micro-step as a JSON object")
    parser.add_argument("--directory", default=None, help="DOM run directory (scopes the cache)")
    parser.add_argument("--queue", default=None, help="following steps as a JSON array (prefetch)")
    parser.add_argument("--no-cache", action="store_true", help="bypass the result cache")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        # Fail-open even on a bad invocation: the hook must never see a non-zero exit.
        emit({"status": "ERROR", "error": "invalid arguments"})
        return 0

    try:
        try:
            step = json.loads(args.step)
        except ValueError:
            emit({"status": "ERROR", "error": "invalid step JSON"})
            return 0
        if not isinstance(step, dict):
            emit({"status": "ERROR", "error": "step must be a JSON object"})
            return 0

        if args.queue:
            try:
                queue_steps = json.loads(args.queue)
            except ValueError:
                queue_steps = []
        else:
            queue_steps = step.get("queue") or []
        if not isinstance(queue_steps, list):
            queue_steps = []

        directory = Path(args.directory).resolve() if args.directory else None
        key = cache_key(step, queue_steps, index_stamp())
        if not args.no_cache:
            cached = cache_read(directory, key)
            if cached is not None:
                emit(cached)
                return 0

        result = build(step, queue_steps)
        if not args.no_cache:
            cache_write(directory, key, result)
        emit(result)
    except Exception as exc:  # the whole point: never raise into the host hook
        emit({"status": "ERROR", "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
    return 0


if __name__ == "__main__":
    sys.exit(main())
