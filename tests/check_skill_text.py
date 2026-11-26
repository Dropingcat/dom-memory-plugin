"""Check the BM25 skill-text layer: n-grams, normalization, multi-field corpus, search.

The corpus is synthetic and in-memory: no home directory, no network, no cache.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))

from mockup import skill_index as si  # noqa: E402
from mockup import skill_text as st  # noqa: E402

# Mixed RU/EN text, because the router sees both and Cyrillic n-grams are the reason
# we do not need a Russian stemmer.
SKILLS = [
    {
        "skill_id": "sqlite-migration",
        "name": "SQLite Migration",
        "tags": ("sqlite", "migration", "database"),
        "description": ("Write schema migrations for the sqlite ledger. "
                        "Написать миграцию схемы базы данных."),
        "body": "DDL, column changes and rollback plans.",
    },
    {
        "skill_id": "pde-solver",
        "name": "PDE Solver",
        "tags": ("pde", "physics", "numerics"),
        "description": "Solve partial differential equations with finite differences.",
        "body": "Boundary conditions and time stepping.",
    },
    {
        "skill_id": "writer-core",
        "name": "Writer Core",
        "tags": ("writing", "docs"),
        "description": "Write articles, reports and documentation in Russian or English.",
        "body": "Prose review and citation checks.",
    },
]


def terms(text):
    """Deduped query terms, the shape score() expects."""
    return list(dict.fromkeys(st.char_ngrams(text)))


def main() -> int:
    # 1. char_ngrams: overlapping 3-5-grams for ASCII and Unicode, multiplicity kept.
    assert st.char_ngrams("migrate") == [
        "mig", "igr", "gra", "rat", "ate",
        "migr", "igra", "grat", "rate",
        "migra", "igrat", "grate",
    ], st.char_ngrams("migrate")
    assert st.char_ngrams("MIgrate!") == st.char_ngrams("migrate"), "case/punctuation ignored"
    assert st.char_ngrams("migrate migrate") == st.char_ngrams("migrate") * 2, "tf is preserved"
    assert st.char_ngrams("ab") == [], "a token shorter than n_min contributes nothing"
    assert st.char_ngrams("migrate", 4, 4) == ["migr", "igra", "grat", "rate"]
    assert st.char_ngrams("migrate", 3, 5) == st.char_ngrams("migrate")
    try:
        st.char_ngrams("x", 5, 3)
    except ValueError:
        pass
    else:
        raise AssertionError("an inverted n range must raise")

    ru = st.char_ngrams("миграция")
    assert ru[:6] == ["миг", "игр", "гра", "рац", "аци", "ция"], ru
    assert ru[6:11] == ["мигр", "игра", "грац", "раци", "ация"], ru
    assert ru[11:] == ["мигра", "играц", "граци", "рация"], ru
    assert len(ru) == 15 and all(3 <= len(gram) <= 5 for gram in ru), ru
    # Inflection survives: "миграция" and "миграцию" share most of their grams.
    assert set(st.char_ngrams("миграцию")) & set(ru), "RU inflection must share grams"

    # 2. normalize: lowercase, punctuation dropped, whitespace collapsed.
    assert st.normalize("Миграция схемы!") == "миграция схемы"
    assert st.normalize("  MULTI\t\nspace   text  ") == "multi space text"
    assert st.normalize("Check_Skill_Index.py!") == "check_skill_index py"
    assert st.normalize("v4→v5") == "v4 v5"
    assert st.normalize(None) == ""
    assert st.normalize("!!!") == ""

    # 3. build_corpus: the documented payload, from dicts and from SkillRecord.
    corpus = st.build_corpus(SKILLS)
    assert corpus["N"] == 3, corpus["N"]
    assert corpus["skill_ids"] == ("sqlite-migration", "pde-solver", "writer-core"), corpus["skill_ids"]
    assert corpus["field_weights"] == st.FIELD_WEIGHTS, corpus["field_weights"]
    assert corpus["k1"] == st.K1 and corpus["b"] == st.B
    assert corpus["avgdl"] > 0.0 and isinstance(corpus["avgdl"], float)
    for field in st.FIELD_NAMES:
        assert corpus["field_avgdl"][field] > 0.0, field
        assert corpus["term_idf"][field], field
        assert all(value > 0.0 for value in corpus["term_idf"][field].values()), field
    for skill in SKILLS:
        skill_id = skill["skill_id"]
        assert corpus["doc_len"][skill_id] == sum(corpus["field_len"][skill_id].values())
        assert set(corpus["field_terms"][skill_id]) == set(st.FIELD_NAMES)
    # Declared weights order: name > tags > description > body.
    assert (st.FIELD_WEIGHTS["name"] > st.FIELD_WEIGHTS["tags"]
            > st.FIELD_WEIGHTS["description"] > st.FIELD_WEIGHTS["body"])

    # A SkillRecord has no body: usage_hint + examples stand in, and the record is
    # indexed by skill_id like any other item.
    record = si.SkillRecord(
        skill_id="usage-skill",
        name="Usage Skill",
        description="Reader helper.",
        tags=("helper",),
        usage_hint="Read the usage hint block before acting.",
        examples=("python -B demo.py",),
    )
    record_corpus = st.build_corpus([record])
    assert record_corpus["N"] == 1
    assert record_corpus["field_len"]["usage-skill"]["body"] > 0, "usage_hint must feed body"
    assert st.search(record_corpus, "usage hint", top_k=1)[0]["skill_id"] == "usage-skill"

    # A later duplicate skill_id overrides an earlier one; missing ids are skipped.
    duplicate = st.build_corpus([
        {"skill_id": "dup", "name": "Dup", "description": "first text"},
        {"skill_id": "dup", "name": "Dup", "description": "second text"},
        {"description": "no identity at all"},
    ])
    assert duplicate["N"] == 1 and duplicate["skill_ids"] == ("dup",)
    assert st.search(duplicate, "second")
    assert st.search(duplicate, "first") == [], "override keeps the later text only"
    assert st.build_corpus([])["N"] == 0, "an empty catalog is legal"

    # 4. search: top-k, deterministic, documented result shape, gap for no evidence.
    hits = st.search(corpus, "sqlite schema migration", top_k=10)
    assert hits, hits
    assert len(hits) <= 10, hits
    assert hits == st.search(corpus, "sqlite schema migration", top_k=10), "search is deterministic"
    assert [hit["skill_id"] for hit in hits][0] == "sqlite-migration", hits
    assert [hit["total_score"] for hit in hits] == sorted(
        (hit["total_score"] for hit in hits), reverse=True), "results are ranked"
    for hit in hits:
        assert set(hit) == {"skill_id", "total_score", "field_scores"}, hit
        assert set(hit["field_scores"]) == set(st.FIELD_NAMES), hit
        assert hit["total_score"] > 0.0
    assert [hit["skill_id"] for hit in st.search(corpus, "sqlite", top_k=1)] == ["sqlite-migration"]
    assert st.search(corpus, "qwertyzzz") == [], "no lexical evidence -> empty (gap)"
    assert st.search(corpus, "") == []
    assert st.search(corpus, "sqlite", top_k=0) == []
    assert st.search({}, "sqlite") == []
    assert st.search(st.build_corpus([]), "sqlite") == []

    # 5. A matching skill outscores an unrelated one, and field weight is respected.
    cases = {
        "sqlite schema migration": "sqlite-migration",
        "миграция схемы": "sqlite-migration",
        "partial differential equations": "pde-solver",
        "articles and reports": "writer-core",
    }
    for query, expected in cases.items():
        ranked = st.search(corpus, query, top_k=len(SKILLS))
        assert ranked[0]["skill_id"] == expected, (query, ranked)
        matching = st.score(corpus, terms(query), expected)["total"]
        assert matching > 0.0, (query, matching)
        for other in SKILLS:
            if other["skill_id"] == expected:
                continue
            assert matching > st.score(corpus, terms(query), other["skill_id"])["total"], query

    # The same term in the name beats the same term in the body (weights 2.0 vs 0.3).
    field_corpus = st.build_corpus([
        {"skill_id": "in-name", "name": "Kubernetes", "tags": (), "description": "", "body": ""},
        {"skill_id": "in-body", "name": "Something", "tags": (), "description": "", "body": "Kubernetes"},
    ])
    kubernetes = terms("kubernetes")
    assert st.score(field_corpus, kubernetes, "in-name")["total"] > \
        st.score(field_corpus, kubernetes, "in-body")["total"]

    # total is the weighted sum of the four field scores (rounding tolerance).
    row = st.score(corpus, terms("миграция схемы"), "sqlite-migration")
    assert set(row) == set(st.FIELD_NAMES) | {"total"}, row
    assert row["description"] > 0.0 and row["total"] > 0.0, row
    weighted = sum(st.FIELD_WEIGHTS[field] * row[field] for field in st.FIELD_NAMES)
    assert abs(row["total"] - weighted) < 1e-5, (row, weighted)
    assert st.score(corpus, terms("миграция"), "missing-skill")["total"] == 0.0
    assert st.score(corpus, [], "sqlite-migration")["total"] == 0.0
    assert st.score({}, terms("sqlite"), "sqlite-migration")["total"] == 0.0

    # 6. Pure stdlib: no vector/ML library was imported anywhere in this process.
    banned = {"numpy", "scipy", "sklearn", "pandas", "rank_bm25", "torch"}
    loaded = {name.split(".")[0].lower() for name in sys.modules}
    assert not (banned & loaded), banned & loaded

    print("PASS skill_text: n-grams, normalize, corpus, multi-field BM25 search")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
