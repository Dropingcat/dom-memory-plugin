"""Check the step -> skill router on artificial steps."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))

from mockup import skill_router  # noqa: E402

SKILLS = [
    {"id": "sqlite-migration", "tags": ["migration", "ledger"], "description": "V5 migration, fixtures"},
    {"id": "python-test", "tags": ["testing"], "description": "pytest and node --test"},
    {"id": "code-review", "tags": ["review"], "description": "review a diff"},
    {"id": "writer", "tags": ["writing", "docs"], "description": "reports and prose"},
    {"id": "docs-md", "tags": ["docs"], "description": "concept docs"},
    {"id": "prompt-brief", "tags": ["prompt", "planning"], "description": "dom.notes and injection"},
    {"id": "git-release", "tags": ["git"], "description": "commits and tags"},
    {"id": "security-gate", "tags": ["security", "ledger"], "description": "WriteContext and the gate"},
    {"id": "dom-memory", "tags": ["ledger", "planning"], "description": "claims and versions"},
    {"id": "panel", "tags": ["planning"], "description": "monitor and panel"},
]


def step(path, title, context="", claims=None, task_type="task", **extra):
    payload = {"path": path, "title": title, "context": context, "claims": claims or [], "task_type": task_type}
    payload.update(extra)
    return skill_router.normalize_step(payload)


def ids(result):
    return [row["id"] for row in result["current"]]


def main() -> int:
    # k cap and determinism
    migration = step("П15.2", "L2 V5 migration: claim_sessions and read_receipts",
                     "V5 adds claim_sessions and read_receipts; v4 schema migrates",
                     ["code column migration"], )
    first = skill_router.select(migration, SKILLS)
    second = skill_router.select(migration, SKILLS)
    assert first == second, "selection must be deterministic"
    assert len(first["current"]) <= 3, first
    assert ids(first)[0] == "sqlite-migration", first
    assert any("migration" in reason for reason in first["current"][0]["why"]), first

    # security step picks the gate skill
    trust = step("П15.4", "L4 trusted session channel", "session_source and the gate", ["code session trust"])
    assert ids(skill_router.select(trust, SKILLS))[0] == "security-gate", ids(skill_router.select(trust, SKILLS))

    # testing step
    testing = step("П15.9", "Run the test suite", "pytest and node --test for the writer", ["text assert"])
    assert ids(skill_router.select(testing, SKILLS))[0] == "python-test", ids(skill_router.select(testing, SKILLS))

    # docs step
    docs = step("П16.4", "Write the concept docs", "readme and spec text", ["text draft"])
    top = ids(skill_router.select(docs, SKILLS))[:2]
    assert "docs-md" in top and "writer" in top, top

    # unknown step falls back to skill_search and low confidence
    unknown = step("П16.9", "Совершенно непонятная работа", "", [])
    result = skill_router.select(unknown, SKILLS)
    assert result["fallback"] == "skill_search", result
    assert result["confidence"] < skill_router.FALLBACK_BELOW, result

    # plan hints: forced and banned
    hinted = step("П16.5", "Do the thing", "", [], hints_skills=["panel"], hints_no_skills=["dom-memory"])
    result = skill_router.select(hinted, SKILLS)
    assert ids(result)[0] == "panel", result
    assert "dom-memory" not in ids(result), result
    assert any(any("forced" in reason for reason in row["why"]) for row in result["current"]), result

    # learned weights lift a weaker skill above a stronger rule match
    weak = step("П16.6", "Review the diff", "review the migration", [])
    plain = skill_router.select(weak, SKILLS)
    boosted = skill_router.select(weak, SKILLS, weights={"docs-md": 0.95})
    assert "docs-md" in ids(boosted), boosted
    assert "docs-md" not in ids(plain)[:1], plain

    # exploration replaces the last pick for one path in ten
    exploring = None
    for index in range(200):
        candidate = step("П17.%d" % index, "Review the diff", "review the migration", [])
        if skill_router._explore(candidate):
            exploring = candidate
            break
    assert exploring is not None
    result = skill_router.select(exploring, SKILLS)
    assert "exploration" in result["current"][-1]["why"], result

    # prefetch returns the next step
    queue = skill_router.build_queue(migration, testing, SKILLS)
    assert queue["next"] == "П15.9", queue
    assert "python-test" in queue["prefetch"], queue
    assert queue["current"][0]["id"] == "sqlite-migration", queue

    print("PASS skill_router: rules, priors, claims, hints, weights, fallback, exploration, prefetch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
