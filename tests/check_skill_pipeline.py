"""E2E skill pipeline test: from step -> queue block.

Covers all invariant and metric requirements:

* F1 -- property invariants: >= 3 independent signals for a queue entry, block <= 800
  chars, no floats in the block, mentions and provides stay separate, determinism.
* F2 -- negative control: a zero-signal step has an empty queue and visible gaps; a
  lexical mention can be selected but never covers a capability, so the gap stays in
  the block.
* F3 -- economics: token budget of the injected block and warm latency.
* F4 -- retrospective: precision@1 and recall@k on labelled steps.
* F5 -- regression: the legacy ``skill_router.select`` contract is untouched and the
  pipeline imports no ledger/database module.

Self-contained: fixtures are hand-built SkillRecords, no network, no user home, no DB.

Run: python -B tests/check_skill_pipeline.py
"""
from __future__ import annotations

import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))

from mockup import skill_router as legacy  # noqa: E402
from mockup import skill_select as ss  # noqa: E402
from mockup.skill_index import SkillRecord, parse_skill_file  # noqa: E402
from mockup.skill_render import TOTAL_BUDGET  # noqa: E402

FLOAT_RE = re.compile(r"\d+\.\d+")
MAX_TOKENS = 200                 # ~4 chars/token; the hard budget is 800 chars
LATENCY_BUDGET_MS = 250.0        # warm, fixture-sized corpus (architecture target: 120 ms)
LATENCY_RUNS = 15


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def record(skill_id, description, categories=(), provides=(), mentions=(), tags=()):
    return SkillRecord(
        skill_id=skill_id,
        name=skill_id,
        description=description,
        categories=tuple(categories),
        tags=tuple(tags),
        mentions=tuple(mentions),
        verified_provides=tuple(provides),
        examples=("python -B tests/check_%s.py" % skill_id.replace("-", "_"),),
        source_path="skills/%s/SKILL.md" % skill_id,
    )


def step(step_id, title, context="", cap=(), category_hint="", markers=(), claims=()):
    return {
        "step_id": step_id,
        "title": title,
        "context": context,
        "cap": list(cap),
        "category_hint": category_hint,
        "markers": list(markers),
        "claims": list(claims),
    }


SKILLS = [
    record("sqlite-migration",
           "Write schema migrations for the sqlite ledger and claim tables.",
           ("databases",), ("sqlite", "migration"), ("sqlite", "migration", "ledger"),
           ("migration", "sqlite")),
    record("dom-memory", "Project memory ledger for claims.",
           ("databases",), ("ledger",), ("ledger", "claim"), ("ledger", "memory")),
    record("test-driven-development",
           "Write the failing test first then implement.",
           ("coding",), ("testing",), ("test", "coding"), ("testing", "tdd")),
    record("pde-solver",
           "Solve partial differential equations for fluid simulation.",
           ("physics",), ("physics",), ("pde", "physics", "fluid"), ("physics", "pde")),
    record("writer-core", "Write reports and articles with review.",
           ("writing",), ("writer", "docs"), ("writing", "report"), ("writing", "report")),
]
CARDS = [{"id": skill.skill_id, "state": "verified"} for skill in SKILLS]

# (label, step, expected top skill).  Kept separate so F4 can score them.
LABELLED = [
    ("sqlite",
     step("s-sqlite", "Add the sqlite migration for the ledger schema",
          "Write a v4->v5 migration and run the tests",
          ("sqlite", "migration"), "databases", ("action:modify", "domain:databases")),
     "sqlite-migration"),
    ("ledger",
     step("s-ledger", "Find the claim in the ledger",
          "Search the ledger for the decision claim",
          ("ledger",), "databases", ("action:inspect", "domain:ledger")),
     "dom-memory"),
    ("tdd",
     step("s-tdd", "Write the failing test first",
          "Implement the parser only after the test fails",
          ("testing",), "coding", ("action:modify",), ("test driven implementation",)),
     "test-driven-development"),
    ("pde",
     step("s-pde", "Solve the PDE for the fluid simulation",
          "Numerically solve the wave equation",
          ("physics",), "physics", ("action:modify",)),
     "pde-solver"),
    ("report",
     step("s-report", "Write the report for the customer",
          "Draft the report and review the prose",
          ("writer",), "writing", ("action:modify",)),
     "writer-core"),
]
CLEAR_STEP = LABELLED[0][1]
ZERO_STEP = step("s-zero", "")


def run(step_dict, skills=SKILLS, cards=CARDS, **kwargs):
    return ss.select_step(step_dict, skills=skills, cards=cards, **kwargs)


# ---------------------------------------------------------------------------
# F1 -- property invariants
# ---------------------------------------------------------------------------

def check_invariants() -> dict:
    result = run(CLEAR_STEP)

    assert result["status"] == ss.STATUS, result["status"]
    assert result["queue"], result
    assert "sqlite-migration" in result["queue"], result["queue"]
    assert "databases" in result["groups"], result["groups"]

    # 1. Every queued skill carries at least three independent signals.
    for skill_id in result["queue"]:
        signals = result["signals"][skill_id]
        assert len(signals) >= 3, (skill_id, signals)
        assert len(signals) == len(set(signals)), (skill_id, signals)
    assert type(result["confidence_bp"]) is int, result["confidence_bp"]
    assert 0 < result["confidence_bp"] <= 10000, result["confidence_bp"]
    assert type(result["plan_version"]) is int, result["plan_version"]

    # 4. Hard block budget.  5. No floats anywhere in the injected text (I-B).
    assert len(result["block"]) <= TOTAL_BUDGET == 800, len(result["block"])
    assert not FLOAT_RE.search(result["block"]), result["block"]

    # 3. Mentions are quoted as evidence; a capability is never "provided" by text.
    block_lower = result["block"].lower()
    assert "mentions:" in result["block"], result["block"]
    assert "verified for cap:" in result["block"], result["block"]
    assert "provides" not in block_lower, result["block"]

    # 6. Same input -> identical ids and identical block bytes.
    again = run(CLEAR_STEP)
    assert again == result, "pipeline must be deterministic"

    # Explicitly rebuilt corpus is also byte-identical.
    explicit = run(CLEAR_STEP, corpus=ss._text.build_corpus(SKILLS))
    assert explicit == result, "an explicit corpus must not change the answer"

    # 7. The index digest is stable and order-independent.
    assert ss.index_digest_for(SKILLS) == ss.index_digest_for(list(reversed(SKILLS)))
    assert result["index_digest"] == ss.index_digest_for(SKILLS), result["index_digest"]

    # Prefetch: up to three following steps, ids only, still inside the budget.
    prefetch = run(
        CLEAR_STEP,
        queue=[LABELLED[1][1], LABELLED[2][1], LABELLED[3][1], LABELLED[4][1]],
        plan_version=7,
    )
    assert prefetch["prefetch"] == ["dom-memory", "test-driven-development", "pde-solver"], \
        prefetch["prefetch"]
    assert prefetch["plan_version"] == 7, prefetch["plan_version"]
    assert "next:" in prefetch["block"] and "then:" in prefetch["block"], prefetch["block"]
    assert len(prefetch["block"]) <= TOTAL_BUDGET, len(prefetch["block"])
    assert not FLOAT_RE.search(prefetch["block"]), prefetch["block"]

    return {"block_chars": len(result["block"]), "queue": result["queue"]}


# ---------------------------------------------------------------------------
# F2 -- negative control consistency
# ---------------------------------------------------------------------------

def check_negative_control() -> dict:
    # 2. Zero signals -> empty queue, but the unselected skills stay visible as gaps
    #    and the block still carries an explicit gap line (never "gaps: none").
    zero = run(ZERO_STEP)
    assert zero["queue"] == [] and zero["advisory"] == [], zero
    assert zero["gaps"], zero
    assert set(zero["gaps"]) == {skill.skill_id for skill in SKILLS}, zero["gaps"]
    assert "gap:" in zero["block"], zero["block"]
    assert "gaps: none" not in zero["block"], zero["block"]
    assert len(zero["block"]) <= TOTAL_BUDGET, len(zero["block"])

    # A description-only "provider": mentions exist, provides do not.  A lexical hit
    # may win a queue slot, but it must never cover the capability.
    chatter = record("sqlite-chatter", "A talk about sqlite topics with no curated capability.",
                     ("databases",), (), ("sqlite",))
    assert chatter.mentions == ("sqlite",), chatter.mentions
    assert chatter.verified_provides == (), chatter.verified_provides

    mention_step = step("s-mention", "Add the sqlite migration",
                        "sqlite schema", ("sqlite",), "databases", ("action:modify",))
    isolated = run(mention_step, skills=[chatter], cards=[])
    assert isolated["queue"] == ["sqlite-chatter"], isolated["queue"]
    assert len(isolated["signals"]["sqlite-chatter"]) >= 3, isolated["signals"]
    assert isolated["missing_caps"] == ["sqlite"], isolated["missing_caps"]
    assert "gap: sqlite -> skill_search" in isolated["block"], isolated["block"]

    # A real provider removes the same gap.
    covered = run(mention_step)
    assert covered["missing_caps"] == [], covered["missing_caps"]
    assert "gaps: none" in covered["block"], covered["block"]

    # mentions are derived from text, provides are never derived: parse a real file.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sqlite-notes" / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            "---\nname: sqlite-notes\ndescription: Notes about the sqlite migration schema\n---\n"
            "# sqlite-notes\n\n## When to use\n\nRead before a migration.\n",
            encoding="utf-8",
        )
        parsed = parse_skill_file(path)
        # MENTION_STEMS are closed character stems: 'migrat' / 'schema' come from the text.
        assert {"migrat", "schema"} <= set(parsed.mentions), parsed.mentions
        assert parsed.verified_provides == (), parsed.verified_provides
        assert parsed.mentions != parsed.verified_provides

    return {"zero_gaps": len(zero["gaps"]), "mention_gap": "sqlite"}


# ---------------------------------------------------------------------------
# F3 -- economics
# ---------------------------------------------------------------------------

def check_economics() -> dict:
    result = run(CLEAR_STEP)
    tokens = (len(result["block"]) + 3) // 4
    assert tokens <= MAX_TOKENS, (tokens, len(result["block"]))
    assert len(result["block"]) <= TOTAL_BUDGET, len(result["block"])

    samples = []
    for _ in range(LATENCY_RUNS):
        started = time.perf_counter()
        run(CLEAR_STEP)
        samples.append((time.perf_counter() - started) * 1000.0)
    p50 = statistics.median(samples)
    assert p50 <= LATENCY_BUDGET_MS, (p50, max(samples))
    return {"tokens": tokens, "p50_ms": p50, "max_ms": max(samples)}


# ---------------------------------------------------------------------------
# F4 -- retrospective precision@1 / recall@3 on the labelled steps
# ---------------------------------------------------------------------------

def check_retrospective() -> dict:
    top1 = 0
    recall3 = 0
    queue_slots = 0
    for _label, labelled_step, expected in LABELLED:
        result = run(labelled_step)
        top = result["queue"][:3]
        queue_slots += len(top)
        if top[:1] == [expected]:
            top1 += 1
        if expected in top:
            recall3 += 1
    total = len(LABELLED)
    precision1 = top1 / total
    recall_at_3 = recall3 / total
    # Fixtures are deliberately unambiguous; these are floors, not targets.
    assert precision1 >= 0.8, (precision1, top1, total)
    assert recall_at_3 >= 0.8, (recall_at_3, recall3, total)
    assert queue_slots >= total, queue_slots
    return {"precision@1": precision1, "recall@3": recall_at_3}


# ---------------------------------------------------------------------------
# F5 -- regression
# ---------------------------------------------------------------------------

def check_regression() -> dict:
    # The legacy router still answers with its documented contract.
    normal = legacy.normalize_step({"path": "П1", "title": "Run the tests",
                                    "claims": ["проверка тестов при проверке"]})
    legacy_skills = [
        {"id": "tdd", "tags": ["testing"], "provides": ["testing"]},
        {"id": "writer", "tags": ["writing"], "provides": ["writer"]},
    ]
    selected = legacy.select(normal, legacy_skills, k=1, explore=False)
    assert selected["step"] == "П1", selected
    assert selected["current"] and selected["current"][0]["id"] == "tdd", selected
    assert selected["capability_version"] == legacy.CAPABILITY_VERSION, selected
    assert selected["fallback"] is None, selected

    # The pipeline stays read-only: importing and running it pulls no database module.
    loaded = {name.split(".")[0] for name in sys.modules}
    assert "sqlite3" not in loaded, "the skill pipeline must not touch the ledger"

    return {"legacy_top": selected["current"][0]["id"]}


def main() -> int:
    invariants = check_invariants()
    negative = check_negative_control()
    economics = check_economics()
    retrospective = check_retrospective()
    regression = check_regression()

    print(
        "PASS skill_pipeline: queue=%s (>=3 signals), block %d<=800 chars, no floats, "
        "deterministic, zero-signal gaps=%d, mention gap visible, %d tokens, "
        "p50=%.2fms, precision@1=%.2f recall@3=%.2f, legacy top=%s"
        % (invariants["queue"], invariants["block_chars"], negative["zero_gaps"],
           economics["tokens"], economics["p50_ms"], retrospective["precision@1"],
           retrospective["recall@3"], regression["legacy_top"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
