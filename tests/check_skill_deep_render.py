"""Check deep analysis (D1) and the positional gradation renderer (D2).

Self-contained: no DB, no network, no disk reads at runtime of the modules under test.
Fixtures are hand-built SkillRecord / MicroStep / ConsensusVerdict values.

Run: python -B tests/check_skill_deep_render.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))

from mockup import skill_render as sr  # noqa: E402
from mockup.skill_consensus import ConsensusFilter  # noqa: E402
from mockup.skill_deep import (  # noqa: E402
    MAX_HOW,
    MAX_WHAT,
    MAX_WHY,
    SkillAnalysis,
    analyze,
    analyze_batch,
)
from mockup.skill_index import SkillRecord  # noqa: E402
from mockup.step_registry import MicroStep  # noqa: E402


def record(skill_id, description="", mentions=(), examples=(), source_path="",
           provides=(), name=None, categories=("databases",), usage_hint=""):
    return SkillRecord(
        skill_id=skill_id,
        name=name if name is not None else skill_id,
        description=description,
        categories=tuple(categories),
        mentions=tuple(mentions),
        verified_provides=tuple(provides),
        examples=tuple(examples),
        source_path=source_path,
        usage_hint=usage_hint,
    )


def step(title="Write sqlite migration", cap=("sqlite", "migration"), hint="databases",
         markers=("action:modify", "domain:databases"),
         context="Add a v4->v5 migration for the ledger schema.", step_id="s1"):
    return MicroStep(
        step_id=step_id,
        title=title,
        parent_plan_task_id="P1",
        plan_version=1,
        cap=cap,
        category_hint=hint,
        markers=markers,
        context=context,
    )


CURRENT_STEP = step()
MIGRATION = record(
    "sqlite-migration",
    name="SQLite Migration",
    description="Write schema migrations for the sqlite ledger.",
    mentions=("sqlite", "migration", "ledger"),
    examples=("python -B tools/migrate.py",),
    source_path="opencode/skills/sqlite-migration/SKILL.md",
    provides=("sqlite", "migration"),
)
TDD = record(
    "test-driven-development",
    description="Write the failing test first, then implement.",
    mentions=("test", "coding"),
    examples=("python -B tests/check_skill_deep_render.py",),
    source_path="skills/tdd/SKILL.md",
    categories=("coding",),
)


def verdict_for(skill, step_obj=CURRENT_STEP, bm25=(), cards=()):
    return ConsensusFilter().evaluate(
        skill.skill_id,
        step_obj.cap,
        step_obj.category_hint,
        step_obj.markers,
        set(bm25),
        skill,
        cards[0] if cards else None,
    )


def main() -> int:
    # ---------------------------------------------------------------- D1: deep ==
    # 1. Every field is within its hard character limit, including hostile input.
    consensus = verdict_for(MIGRATION, bm25={MIGRATION.skill_id},
                            cards=[{"id": MIGRATION.skill_id, "state": "verified"}])
    analysis = analyze(MIGRATION, CURRENT_STEP.title, CURRENT_STEP.context,
                       consensus, CURRENT_STEP.cap)
    assert len(analysis.what) <= MAX_WHAT, (len(analysis.what), analysis.what)
    assert len(analysis.why) <= MAX_WHY, (len(analysis.why), analysis.why)
    assert len(analysis.how) <= MAX_HOW, (len(analysis.how), analysis.how)
    assert analysis.what == "SQLite Migration", analysis.what
    assert analysis.skill_id == "sqlite-migration"

    long_record = record(
        "overflow-skill",
        name="N" * 300,
        description="D" * 600,
        mentions=("sqlite",),
        examples=("x" * 600,),
        source_path="a" * 300,
    )
    long_analysis = analyze(long_record, CURRENT_STEP.title, CURRENT_STEP.context,
                            verdict_for(long_record), CURRENT_STEP.cap)
    assert len(long_analysis.what) <= MAX_WHAT, len(long_analysis.what)
    assert len(long_analysis.why) <= MAX_WHY, len(long_analysis.why)
    assert len(long_analysis.how) <= MAX_HOW, len(long_analysis.how)
    for item in (analysis, long_analysis):
        assert isinstance(item, SkillAnalysis)
        assert item.evidence_level == "mentions", item.evidence_level

    # 2. why NEVER says "provides": mentions and curated caps have their own wording.
    assert "provides" not in analysis.why.lower(), analysis.why
    assert "mentions" in analysis.why.lower(), analysis.why

    provided_only = record("claim-ledger", description="Hash-chained claim store",
                           mentions=(), provides=("ledger",))
    provided_verdict = ConsensusFilter().evaluate(
        "claim-ledger", ("ledger",), "databases", (), set(), provided_only, None)
    provided_analysis = analyze(provided_only, CURRENT_STEP.title, CURRENT_STEP.context,
                                provided_verdict, ("ledger",))
    assert "provides" not in provided_analysis.why.lower(), provided_analysis.why
    assert "verified for cap: ledger" in provided_analysis.why, provided_analysis.why

    # The closed evidence level cannot be hand-set to the forbidden value.
    try:
        SkillAnalysis(skill_id="x", what="w", why="y", how="h", evidence_level="provides")
    except ValueError:
        pass
    else:
        raise AssertionError("evidence_level must refuse 'provides'")

    # 3. Examples are reproduced verbatim with their source path.
    assert "python -B tools/migrate.py" in analysis.how, analysis.how
    assert MIGRATION.source_path in analysis.how, analysis.how
    assert analysis.mentions == ("sqlite", "migration", "ledger"), analysis.mentions

    no_example = record("no-example", description="Plan the work", mentions=("plan",),
                        categories=("writing",))
    no_example_analysis = analyze(no_example, CURRENT_STEP.title, CURRENT_STEP.context,
                                  verdict_for(no_example), CURRENT_STEP.cap)
    assert no_example_analysis.how.startswith("Use skill(no-example) with step context:"), \
        no_example_analysis.how
    assert "Add a v4->v5 migration" in no_example_analysis.how, no_example_analysis.how

    # Determinism: same inputs -> equal analyses.
    repeat = analyze(MIGRATION, CURRENT_STEP.title, CURRENT_STEP.context,
                     consensus, CURRENT_STEP.cap)
    assert repeat == analysis, (repeat, analysis)

    # ----------------------------------------------------------------- D2: render ==
    next_analysis = analyze(TDD, "Update the ledger test", "Cover the v5 migration",
                            verdict_for(TDD), ("testing",))
    next_step = step(title="Update the ledger test", cap=("testing",), hint="coding",
                     markers=("action:modify",), step_id="s2")
    then_step = step(title="Run the smoke suite", cap=(), hint="", markers=(),
                     step_id="s3")
    future_step = step(title="Describe the changes", cap=(), hint="", markers=(),
                       step_id="s4")
    future_step2 = step(title="Agree audit format", cap=(), hint="", markers=(),
                        step_id="s5")

    # 4. The whole block is <= 800 chars; total_chars is derived from the lines.
    block = sr.render_block(
        CURRENT_STEP, analysis,
        [(next_step, next_analysis)],
        [(then_step, "test-driven-development")],
        [future_step, future_step2],
        ["security"],
        "fp-0001",
    )
    assert block.total_chars <= sr.TOTAL_BUDGET, block.total_chars
    assert block.total_chars == len(block.text), (block.total_chars, len(block.text))
    assert block.total_chars == len("\n".join(block.lines))
    try:
        sr.QueueBlock(lines=("x" * (sr.TOTAL_BUDGET + 1),), total_chars=0, has_gaps=False)
    except ValueError:
        pass
    else:
        raise AssertionError("QueueBlock must refuse an over-budget block")

    # 5. Gradation depends on position: current > next > then > future.
    current_text = sr.render_current(CURRENT_STEP, analysis)
    next_text = sr.render_next(next_step, next_analysis)
    then_text = sr.render_then(then_step, "test-driven-development")
    future_text = sr.render_future(future_step)
    assert len(current_text) <= sr.BUDGET_CURRENT, len(current_text)
    assert len(next_text) <= sr.BUDGET_NEXT, len(next_text)
    assert len(then_text) <= sr.BUDGET_THEN, len(then_text)
    assert len(future_text) <= sr.BUDGET_FUTURE, len(future_text)
    assert len(current_text) > len(next_text) > len(then_text) > len(future_text), (
        len(current_text), len(next_text), len(then_text), len(future_text))
    assert current_text.startswith("step: Write sqlite migration"), current_text
    assert next_text.startswith("next: Update the ledger test — "), next_text
    assert then_text.startswith("then: Run the smoke suite — "), then_text
    assert future_text == "step: Describe the changes", future_text

    # 6. No floating point numbers in the rendered block (I-B).
    assert not re.search(r"\d+\.\d+", block.text), block.text
    for line in (current_text, next_text, then_text, future_text,
                 sr.render_gaps(["security"])):
        assert not re.search(r"\d+\.\d+", line), line

    # 7. Same fingerprint + same inputs -> identical bytes.  Format also ignores
    #    confidence-level fields such as mentions (I-A): only position matters.
    again = sr.render_block(
        CURRENT_STEP, analysis,
        [(next_step, next_analysis)],
        [(then_step, "test-driven-development")],
        [future_step, future_step2],
        ["security"],
        "fp-0001",
    )
    assert again == block, (again, block)
    assert again.text == block.text

    same_text_other_mentions = SkillAnalysis(
        skill_id=analysis.skill_id,
        what=analysis.what,
        why=analysis.why,
        how=analysis.how,
        mentions=("a-different-mention-set",),
    )
    re_rendered = sr.render_block(
        CURRENT_STEP, same_text_other_mentions,
        [(next_step, next_analysis)],
        [(then_step, "test-driven-development")],
        [future_step, future_step2],
        ["security"],
        "fp-0001",
    )
    assert re_rendered.text == block.text, "format must not depend on mentions/confidence"

    # 8. Gaps are always visible; an empty gap set is explicit, not silent.
    assert block.has_gaps is True
    assert "gap: security -> skill_search" in block.text, block.text
    no_gaps = sr.render_block(CURRENT_STEP, analysis, [], [], [], [], "fp-0002")
    assert "gaps: none" in no_gaps.text, no_gaps.text
    assert no_gaps.has_gaps is False
    assert len(sr.render_gaps([])) <= sr.BUDGET_GAPS
    assert len(sr.render_gaps(["security", "migration"])) <= sr.BUDGET_GAPS

    # 9. End-to-end: consensus -> deep analysis -> rendered block.
    skills = [
        record("dom-memory", description="Project memory ledger for sqlite claims",
               mentions=("sqlite", "ledger"), provides=("ledger", "sqlite"),
               examples=("python -B scripts/dom_server.py",),
               source_path="skills/dom-memory/SKILL.md"),
        record("sqlite-migration", description="Write schema migrations for sqlite",
               mentions=("sqlite", "migration"), provides=("sqlite", "migration"),
               examples=("python -B tools/migrate.py",),
               source_path="skills/sqlite-migration/SKILL.md"),
        record("pde-solver", description="Partial differential equation solver",
               mentions=("pde",), categories=("physics",)),
        record("rdkit", description="Molecule toolkit", mentions=("molecule",),
               categories=("chemistry",)),
    ]
    cards = [
        {"id": "dom-memory", "state": "verified"},
        {"id": "sqlite-migration", "state": "verified"},
    ]

    def bm25_fn(query_text, top_k):
        assert isinstance(query_text, str) and query_text.strip(), query_text
        return [{"skill_id": "dom-memory", "total_score": 9},
                {"skill_id": "sqlite-migration", "total_score": 8}][:top_k]

    selected = ConsensusFilter(bm25_fn).filter_batch(skills, CURRENT_STEP, cards)
    queue = selected["queue"]
    assert [v.skill_id for v in queue] == ["sqlite-migration", "dom-memory"], queue

    analyses = analyze_batch(skills, CURRENT_STEP, queue, CURRENT_STEP.cap)
    assert [item.skill_id for item in analyses] == ["sqlite-migration", "dom-memory"], analyses
    assert "provides" not in " ".join(item.why for item in analyses).lower()
    assert "mentions" in analyses[0].why.lower(), analyses[0].why

    e2e_block = sr.render_block(
        CURRENT_STEP, analyses[0],
        [(step(title="Update the ledger test", cap=("testing",), hint="coding",
               markers=("action:modify",), step_id="s2"), analyses[1])],
        [(step(title="Run the smoke suite", cap=(), hint="", markers=(),
               step_id="s3"), "test-driven-development")],
        [step(title="Describe the changes", cap=(), hint="", markers=(), step_id="s4")],
        ["security"],
        "e2e-fingerprint",
    )
    assert e2e_block.total_chars <= sr.TOTAL_BUDGET, e2e_block.total_chars
    assert e2e_block.fingerprint == "e2e-fingerprint"
    assert "gap: security -> skill_search" in e2e_block.text, e2e_block.text
    assert "use: sqlite-migration" in e2e_block.text, e2e_block.text
    assert "next: Update the ledger test — dom-memory@" in e2e_block.text, e2e_block.text

    print(
        "PASS skill_deep_render: what<=%d why<=%d how<=%d; why says mentions/"
        "verified-for only; examples verbatim with source; block %d<=%d chars, "
        "gradation %d>%d>%d>%d, no floats, gaps always visible, e2e queue %s"
        % (MAX_WHAT, MAX_WHY, MAX_HOW, e2e_block.total_chars, sr.TOTAL_BUDGET,
           len(current_text), len(next_text), len(then_text), len(future_text),
           [item.skill_id for item in analyses])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
