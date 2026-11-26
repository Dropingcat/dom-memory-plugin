"""Threshold calibration for the skill router (offline only).

A hand-picked threshold (``0.3``) is a guess about a distribution nobody measured.
This tool measures it: real step queries are paired with *shuffled* skills, the BM25
scores of those pairs form the background distribution, and the router's threshold is
its 95th percentile.  A real (query, skill) pair must then beat 95% of random pairs to
be admitted -- a claim the runtime can state and the test suite can reproduce.

The null is built per query:

* score every skill for the query;
* drop the best-scoring skill (the likely *intended* one, which the calibrator is not
  told about) so the null never contains the answer;
* sample uniformly from the rest, ``n_shuffles`` rounds over all queries.

The shuffle is seeded (``DEFAULT_SEED``), so the threshold is identical across runs and
machines.  Nothing is written: the result is printed to stdout and the caller stores it
with the index digest.  This module never runs in the hot path.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import sys
from pathlib import Path

# Import the engine exactly the way the ``__main__`` block documents.
_ENGINE_DIR = Path(__file__).resolve().parents[1] / 'opencode'
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

from mockup.skill_text import char_ngrams, score  # noqa: E402

DEFAULT_SEED = 20261127


# ---------------------------------------------------------------------------
# index identity
# ---------------------------------------------------------------------------

def _read(item, key: str):
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def index_digest(skills) -> str:
    """Canonical sha256 over (skill_id, content_digest), order-independent.

    Same algorithm as ``step_registry.skill_index_digest`` for ``{'id', 'digest'}``
    rows, so the stored threshold and the runtime fingerprint agree on identity.
    """
    rows = []
    for skill in skills or ():
        skill_id = ''
        for key in ('skill_id', 'id', 'name'):
            value = _read(skill, key)
            if isinstance(value, str) and value.strip():
                skill_id = value.strip()
                break
        digest = ''
        for key in ('content_digest', 'digest', 'sha256'):
            value = _read(skill, key)
            if value:
                digest = str(value)
                break
        rows.append([skill_id, digest])
    rows.sort()
    encoded = json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _capability_version() -> int:
    """Version of the curated capability data, read the same way skill_index reads it."""
    try:
        data = json.loads((_ENGINE_DIR / 'mockup' / 'capabilities.json').read_text(encoding='utf-8'))
        return int(data.get('version') or 1)
    except (OSError, ValueError, TypeError):
        return 1


# ---------------------------------------------------------------------------
# negative control
# ---------------------------------------------------------------------------

def _percentile(ordered: list[float], percent: float) -> float:
    """Linear-interpolated percentile of an already sorted list (0.0 when empty)."""
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * float(percent) / 100.0
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def shuffled_pair_scores(
    skills: list,
    step_queries: list[str],
    bm25_corpus: dict,
    n_shuffles: int = 100,
    seed: int = DEFAULT_SEED,
) -> list[float]:
    """Background BM25 scores from shuffled (query, skill) pairs.

    For each query, the best-matching skill is removed from the sampling pool (it is
    the likely intended partner), then ``n_shuffles`` random partners are drawn.
    Returns ``n_queries * n_shuffles`` scores, in draw order.
    """
    skill_ids = list(bm25_corpus.get('skill_ids') or ()) if bm25_corpus else []
    queries = [str(query) for query in (step_queries or ()) if str(query).strip()]
    try:
        rounds = int(n_shuffles)
    except (TypeError, ValueError):
        rounds = 0
    if not skill_ids or not queries or rounds <= 0:
        return []

    rng = random.Random(int(seed))
    term_lists = [list(dict.fromkeys(char_ngrams(query))) for query in queries]

    # Per-query pool: every skill except the best match.  With a single skill there is
    # nothing to exclude; with a query that matches nothing the "best" is an arbitrary
    # tie, and excluding it just removes one arbitrary negative.
    pools = []
    for terms in term_lists:
        if len(skill_ids) == 1:
            pools.append(list(skill_ids))
            continue
        best = min(
            skill_ids,
            key=lambda skill_id: (-score(bm25_corpus, terms, skill_id)['total'], skill_id),
        )
        pools.append([skill_id for skill_id in skill_ids if skill_id != best])

    background: list[float] = []
    for _ in range(rounds):
        for terms, pool in zip(term_lists, pools):
            if not terms:
                continue
            partner = pool[rng.randrange(len(pool))]
            background.append(score(bm25_corpus, terms, partner)['total'])
    return background


def calibrate(
    skills: list,
    step_queries: list[str],
    bm25_corpus: dict,
    percentile: float = 95.0,
    n_shuffles: int = 100,
    seed: int = DEFAULT_SEED,
) -> dict:
    """Calibrate the router threshold from the shuffled-pair background.

    Returns ``threshold`` (the requested percentile, 95 by default), the background
    p50/p95/p99, and the identity a cache key needs: ``index_digest`` and
    ``capability_version``.  Scores are rounded to 6 decimals; the same inputs always
    yield the same dict byte-for-byte.
    """
    background = sorted(shuffled_pair_scores(
        skills, step_queries, bm25_corpus, n_shuffles=n_shuffles, seed=seed))
    level = float(percentile)
    if not 0.0 < level <= 100.0:
        raise ValueError('percentile must be in (0, 100], got %r' % (percentile,))
    queries = [str(query) for query in (step_queries or ()) if str(query).strip()]
    return {
        'threshold': round(_percentile(background, level), 6),
        'p50': round(_percentile(background, 50.0), 6),
        'p95': round(_percentile(background, 95.0), 6),
        'p99': round(_percentile(background, 99.0), 6),
        'index_digest': index_digest(skills),
        'capability_version': _capability_version(),
        'n_skills': int(bm25_corpus.get('N') or 0) if bm25_corpus else 0,
        'n_queries': len(queries),
    }


if __name__ == '__main__':
    from mockup.skill_index import SkillIndex
    from mockup.skill_text import build_corpus

    index = SkillIndex()
    skills = index.collect()
    corpus = build_corpus(skills)

    # Real step queries from the plan (RU/EN mixed, as the router sees them).
    step_queries = [
        "L2 V5 migration: claim_sessions and read_receipts",
        "L4 trusted session channel",
        "Написать миграцию v4→v5",
        "Прочитать текущую схему таблицы claims",
        "Прогнать smoke_cold_start",
        "Добавить колонку in_force",
        "Обновить тест check_plan_identity",
        "Описать изменения в GET_STARTED",
        "Согласовать формат audit-таблиц",
        "Выбрать инструмент для тестирования",
    ]

    result = calibrate(skills, step_queries, corpus)
    print(json.dumps(result, indent=2, ensure_ascii=False))
