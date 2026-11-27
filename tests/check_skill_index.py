"""Check the offline skill index and the BM25 category proposer on synthetic catalogs."""
from __future__ import annotations

import re
import sys
import tempfile
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))

from mockup import skill_index as si  # noqa: E402


def skill_text(name, description, extra="", when=("First paragraph of usage.\nSecond line.\n\n- bullet\n"),
               example=True):
    """A SKILL.md body with the sections the collector looks for."""
    lines = ["---", "name: %s" % name, "description: %s" % description]
    if extra:
        lines.append(extra.rstrip())
    lines.append("---")
    lines.extend(["", "# %s" % name, ""])
    if when is not None:
        lines.extend(["## When to use", "", when])
    if example:
        lines.extend(["## Example", "", "```bash", "python -B tools/run.py", "```", ""])
    return "\n".join(lines) + "\n"


def write_skill(root, dirname, text):
    path = Path(root) / dirname / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def by_id(skills, skill_id):
    for record in skills:
        if record.skill_id == skill_id:
            return record
    raise AssertionError("missing record %r in %s" % (skill_id, [row.skill_id for row in skills]))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        main_root = tmp / "skills"
        cache_dir = tmp / "cache"

        write_skill(main_root, "molecule-design", skill_text(
            "molecule-design",
            "Molecular docking and ADMET prediction for drug candidates with protein structure search.",
            "category: [chemistry, research]\ntags: [docking, admet]"))
        write_skill(main_root, "ledger-migration", skill_text(
            "ledger-migration",
            "Write a schema migration for the sqlite ledger and claim tables, then run the tests.",
            "category: databases\ntags: [migration, sqlite]"))
        write_skill(main_root, "writer-prose", skill_text(
            "writer-prose",
            "Write articles and reports; review prose and citations.",
            "category: writing\ntags: [writing, review]"))
        write_skill(main_root, "literature-search", skill_text(
            "literature-search",
            "Literature search and citation management for academic papers.",
            "category: research"))
        write_skill(main_root, "uncategorized-skill", skill_text(
            "uncategorized-skill",
            "ADMET and molecular docking for drug candidates with protein structure data."))
        write_skill(main_root, "bare-skill", "# Bare\n\n## Example\n\n```bash\nls -la\n```\n")
        write_skill(main_root, "malformed-skill", skill_text(
            "malformed-skill",
            'Fire on "run tests", mode: fast',
            "category: coding"))

        index = si.SkillIndex(cache_dir=cache_dir)
        skills = index.collect([main_root], cards_path=False)
        assert skills == index.collect([main_root], cards_path=False), "collect must be deterministic"
        assert [row.skill_id for row in skills] == sorted(row.skill_id for row in skills), skills
        assert len(skills) == 7, [row.skill_id for row in skills]

        # 1. SkillRecord is immutable.
        molecule = by_id(skills, "molecule-design")
        try:
            molecule.name = "changed"
        except FrozenInstanceError:
            pass
        else:
            raise AssertionError("SkillRecord must be frozen")

        # 2. categories is a tuple and several categories survive parsing.
        assert molecule.categories == ("chemistry", "research"), molecule.categories
        assert si.SkillRecord(skill_id="x", name="x", description="", categories=["a", "b"]).categories == ("a", "b")
        assert si.SkillRecord(skill_id="x", name="x", description="", tags="solo").tags == ("solo",)
        assert si._as_tuple("chemistry, research", lower=True) == ("chemistry", "research")
        assert by_id(skills, "writer-prose").categories == ("writing",)

        # 3. mentions come from the description text; provides is not a parsed field.
        field_names = {item.name for item in fields(si.SkillRecord)}
        assert "provides" not in field_names, field_names
        assert not hasattr(molecule, "provides"), "mentions must never be exposed as provides"
        assert molecule.verified_provides == (), "parser never invents curated provides"
        assert molecule.mentions, molecule.mentions
        for mention in molecule.mentions:
            assert mention in molecule.description.lower(), (mention, molecule.description)
        assert "docking" in molecule.mentions, molecule.mentions
        assert "docking" not in by_id(skills, "writer-prose").mentions
        assert si._extract_mentions("") == (), "empty description -> no mentions"
        assert si._extract_mentions("qwerty zzz") == ()

        # 4. Frontmatter, description, tags, sections and examples are parsed.
        migration = by_id(skills, "ledger-migration")
        assert migration.name == migration.skill_id == "ledger-migration"
        assert "schema migration" in migration.description, migration.description
        assert migration.categories == ("databases",), migration.categories
        assert migration.tags == ("migration", "sqlite"), migration.tags
        assert migration.has_when_to_use and migration.has_example, migration
        assert migration.usage_hint == "First paragraph of usage. Second line.", migration.usage_hint
        assert migration.examples == ("python -B tools/run.py",), migration.examples
        assert len(migration.content_digest) == 64, migration.content_digest

        # 5. A file without frontmatter degrades to the directory name, not a crash.
        bare = by_id(skills, "bare-skill")
        assert bare.name == "bare-skill", bare
        assert bare.description == "" and bare.categories == () and bare.tags == (), bare
        assert bare.has_when_to_use is False and bare.has_example is True, bare
        assert bare.examples == ("ls -la",), bare.examples

        # Malformed YAML (an unquoted ": ") falls back to a line reader.
        malformed = by_id(skills, "malformed-skill")
        assert malformed.name == "malformed-skill", malformed
        assert malformed.description.startswith("Fire on"), malformed.description
        assert malformed.categories == ("coding",), malformed.categories

        # Precedence: a later root overrides the same skill_id.
        root_a = tmp / "root-a"
        root_b = tmp / "root-b"
        write_skill(root_a, "molecule-design", skill_text("molecule-design", "root A version", when=None, example=False))
        write_skill(root_b, "molecule-design", skill_text("molecule-design", "root B version", when=None, example=False))
        assert index.collect([root_a, root_b], cards_path=False)[0].description == "root B version"
        assert index.collect([root_b, root_a], cards_path=False)[0].description == "root A version"
        assert index.collect([tmp / "missing-root"], cards_path=False) == [], "missing roots are skipped, not fatal"

        # 6. The proposer assigns a category to an uncategorized skill and skips categorized ones.
        proposer = si.CategoryProposer([row for row in skills if row.categories])
        assert proposer.categories == ("chemistry", "coding", "databases", "research", "writing"), proposer.categories
        uncategorized = by_id(skills, "uncategorized-skill")
        proposals = proposer.propose(uncategorized)
        assert proposals, "an uncategorized skill needs a proposal"
        assert len(proposals) <= 2, proposals
        assert proposals[0][0] == "chemistry", proposals
        assert proposals[0][1] > 0.0 and isinstance(proposals[0][1], float), proposals
        assert "known skill(s)" in proposals[0][2], proposals[0][2]
        assert si.CategoryProposer([row for row in skills if row.categories]).propose(uncategorized) == proposals
        assert [category for category, _, _ in proposer.propose(uncategorized, top_k=1)] == ["chemistry"]
        assert proposer.propose(molecule) == [], "a categorized skill must be skipped"
        assert si.CategoryProposer([]).propose(uncategorized) == [], "no known skills -> no proposal"

        # 7. BM25 is deterministic, standard-library only, and survives Russian inflection.
        corpus = ["sqlite schema migrations", "article about prose and style"]
        first = proposer.bm25("schema migration", corpus)
        second = proposer.bm25("schema migration", corpus)
        assert first == second, (first, second)
        assert first > 0.0, first
        relevant = proposer.bm25("schema migration", ["sqlite schema migrations"])
        irrelevant = proposer.bm25("schema migration", ["article about prose"])
        assert relevant > irrelevant, (relevant, irrelevant)
        assert proposer.bm25("миграция", ["миграции схемы базы данных"]) > 0.0
        assert proposer.bm25("миграция", ["миграции схемы"]) > proposer.bm25("миграция", ["совсем другой текст"])
        assert proposer.bm25("anything", []) == 0.0

        # 8. Cache round-trip: save -> load -> identical records; a miss is None.
        cache = index.build_cache(skills)
        assert cache["schema"] == si.CACHE_SCHEMA
        assert cache["capability_version"] == si.CAPABILITY_VERSION
        assert re.fullmatch(r"[0-9a-f]{64}", cache["fingerprint"]), cache["fingerprint"]
        assert cache["fingerprint"] == si.fingerprint_for(skills)
        assert index.build_cache(skills)["fingerprint"] == cache["fingerprint"], "fingerprint is stable"

        path = index.save_cache(cache)
        assert path == cache_dir / ("index-%s.json" % cache["fingerprint"]), path
        assert path.exists(), path
        loaded = index.load_cache(cache["fingerprint"])
        assert loaded == cache, "cache payload must round-trip"
        restored = si.records_from_cache(loaded)
        assert restored == skills, "records must round-trip through JSON"
        assert si.records_from_cache(None) == []
        assert index.load_cache("0" * 64) is None, "unknown fingerprint is a miss"
        broken = cache_dir / ("index-%s.json" % ("f" * 64))
        broken.write_text("{broken", encoding="utf-8")
        assert index.load_cache("f" * 64) is None, "a corrupt cache is a miss"

    print("PASS skill_index: records, parsing, mentions, precedence, BM25 proposer, cache")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
