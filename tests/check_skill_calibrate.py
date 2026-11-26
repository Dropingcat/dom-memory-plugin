"""Check the offline threshold calibrator: seeded null, percentiles, identity fields.

No DB, no files written, no network. The corpus is a fixed synthetic catalog, so the
calibrated threshold is a reproducible number rather than a property of this machine.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))

from mockup import skill_index as si  # noqa: E402
from mockup import skill_text as st  # noqa: E402
from mockup import step_registry as sr  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "skill_calibrate", ROOT / "tools" / "skill_calibrate.py")
cal = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cal)

SKILLS = [
    {"skill_id": "sqlite-migration", "name": "SQLite Migration",
     "tags": ("sqlite", "migration"),
     "description": "Write schema migrations for the sqlite ledger. "
                    "Написать миграцию схемы базы данных.",
     "body": "DDL and column changes.", "content_digest": "d1"},
    {"skill_id": "claim-ledger", "name": "Claim Ledger",
     "tags": ("ledger", "review"),
     "description": "Append-only claim ledger with hash chain and receipts.",
     "body": "Read and write claims.", "content_digest": "d2"},
    {"skill_id": "tdd", "name": "Test-Driven Development",
     "tags": ("testing", "coding"),
     "description": "Write a failing test first, then implement the code to pass it.",
     "body": "Red green refactor.", "content_digest": "d3"},
    {"skill_id": "code-review", "name": "Code Review",
     "tags": ("review", "quality"),
     "description": "Inspect code for defects and missing tests.",
     "body": "Structured critique.", "content_digest": "d4"},
    {"skill_id": "writer-core", "name": "Writer Core",
     "tags": ("writing", "docs"),
     "description": "Write articles and documentation in Russian or English.",
     "body": "Prose and citation checks.", "content_digest": "d5"},
    {"skill_id": "pde-solver", "name": "PDE Solver",
     "tags": ("pde", "physics"),
     "description": "Solve partial differential equations with finite differences.",
     "body": "Boundary conditions.", "content_digest": "d6"},
]

QUERIES = [
    "миграция схемы sqlite",
    "claim ledger hash chain",
    "write a failing test first",
    "inspect code for defects",
    "статья и документация",
    "partial differential equations",
]


def main() -> int:
    corpus = st.build_corpus(SKILLS)

    # 1. The negative control is seeded, complete and non-negative.
    scores = cal.shuffled_pair_scores(SKILLS, QUERIES, corpus, n_shuffles=20)
    assert len(scores) == len(QUERIES) * 20, len(scores)
    assert all(isinstance(value, float) and value >= 0.0 for value in scores), scores
    assert scores == cal.shuffled_pair_scores(SKILLS, QUERIES, corpus, n_shuffles=20), \
        "the fixed seed must reproduce the sample"
    other_seed = cal.shuffled_pair_scores(SKILLS, QUERIES, corpus, n_shuffles=20, seed=1)
    assert len(other_seed) == len(scores) and other_seed != scores, "seed must actually shuffle"
    assert cal.shuffled_pair_scores(SKILLS, QUERIES, corpus, n_shuffles=0) == []
    assert cal.shuffled_pair_scores(SKILLS, [], corpus, n_shuffles=20) == []
    assert cal.shuffled_pair_scores([], QUERIES, st.build_corpus([]), n_shuffles=20) == []

    # The null never contains the answer: the best match of each query is excluded,
    # so no background sample can reach the best real score.
    best_real = max(st.search(corpus, query, top_k=1)[0]["total_score"]
                    for query in QUERIES if st.search(corpus, query, top_k=1))
    assert max(scores) < best_real, (max(scores), best_real)

    # 2. calibrate: contract keys, percentiles, determinism.
    result = cal.calibrate(SKILLS, QUERIES, corpus)
    assert set(result) == {
        "threshold", "p50", "p95", "p99",
        "index_digest", "capability_version", "n_skills", "n_queries",
    }, sorted(result)
    assert result == cal.calibrate(SKILLS, QUERIES, corpus), "same input -> same dict"
    assert result["n_skills"] == len(SKILLS) and result["n_queries"] == len(QUERIES)
    assert result["p50"] <= result["p95"] <= result["p99"], result
    assert result["threshold"] == result["p95"], "default percentile is 95"
    assert result["p95"] > 0.0, "the background is not a constant zero"
    assert result["threshold"] == cal.calibrate(SKILLS, QUERIES, corpus, percentile=95.0)["threshold"]
    assert cal.calibrate(SKILLS, QUERIES, corpus, percentile=50.0)["threshold"] == result["p50"]
    assert cal.calibrate(SKILLS, QUERIES, corpus, percentile=99.0)["threshold"] == result["p99"]
    assert cal.calibrate(SKILLS, QUERIES, corpus, percentile=100.0)["threshold"] == max(
        cal.shuffled_pair_scores(SKILLS, QUERIES, corpus))

    # 3. Identity: the digest matches the runtime index digest algorithm, and the
    # capability version matches the curated data the rest of the engine reads.
    assert result["index_digest"] == sr.skill_index_digest(
        [{"id": skill["skill_id"], "digest": skill["content_digest"]} for skill in SKILLS])
    assert result["index_digest"] == cal.index_digest(SKILLS)
    assert result["index_digest"] != cal.index_digest([]), "identity is not a constant"
    assert result["capability_version"] == si.CAPABILITY_VERSION

    # 4. Degenerate inputs degrade to zero instead of raising.
    empty = cal.calibrate([], [], st.build_corpus([]))
    assert empty["threshold"] == 0.0 and empty["p50"] == empty["p95"] == empty["p99"] == 0.0
    assert empty["n_skills"] == 0 and empty["n_queries"] == 0
    assert cal.calibrate(SKILLS, QUERIES, corpus, n_shuffles=0)["threshold"] == 0.0

    # 5. An impossible percentile is an error, not a silent clamp.
    for bad in (0.0, -5.0, 100.5):
        try:
            cal.calibrate(SKILLS, QUERIES, corpus, percentile=bad)
        except ValueError:
            pass
        else:
            raise AssertionError("percentile %r must raise ValueError" % (bad,))

    print("PASS skill_calibrate: seeded null, p50/p95/p99, digest + capability identity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
