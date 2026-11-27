"""Check the multi-marker consensus filter and the two-level group selector.

Self-contained: no DB, no network, no fixtures outside the repo.

Run: python -B tests/check_skill_consensus.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))

from mockup import skill_consensus  # noqa: E402
from mockup.skill_consensus import ConsensusFilter, ConsensusVerdict  # noqa: E402
from mockup.skill_group import GroupSelector, GroupWeights, exploration_trigger  # noqa: E402
from mockup.skill_index import SkillRecord  # noqa: E402
from mockup.step_registry import MicroStep  # noqa: E402

CATEGORIES = ("databases", "physics", "chemistry", "coding")


def record(skill_id, categories=(), provides=(), mentions=(), description="", tags=()):
    return SkillRecord(
        skill_id=skill_id,
        name=skill_id,
        description=description,
        categories=tuple(categories),
        tags=tuple(tags),
        mentions=tuple(mentions),
        verified_provides=tuple(provides),
    )


def step(cap=("sqlite",), hint="databases", markers=("action:modify",), title="Add sqlite migration"):
    return MicroStep(
        step_id="s1",
        title=title,
        parent_plan_task_id="П1",
        plan_version=1,
        cap=cap,
        category_hint=hint,
        markers=markers,
        context="",
    )


DB_STEP = step()


def verdict_rows(result):
    return result["queue"] + result["advisory"]


def main() -> int:
    # 1. ConsensusVerdict is frozen -- immutable.
    verdict = ConsensusVerdict(
        skill_id="x",
        queue_mode="gap",
        signals=("group",),
        signal_count=1,
        confidence_bp=2500,
        group_match=True,
        bm25_in_top=False,
        marker_overlap=False,
        cap_provided=False,
        mention_match=False,
        card_verified=False,
    )
    try:
        verdict.skill_id = "y"
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("ConsensusVerdict must be frozen")

    # The mode is derived from the signal count: a hand-built queue with one signal is refused.
    try:
        ConsensusVerdict(
            skill_id="x",
            queue_mode="queue",
            signals=("group",),
            signal_count=1,
            confidence_bp=2500,
            group_match=True,
            bm25_in_top=False,
            marker_overlap=False,
            cap_provided=False,
            mention_match=False,
            card_verified=False,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("one signal must not be able to claim queue mode")

    # 2. evaluate: 3 signals -> queue; 2 -> advisory; 1 -> gap; 0 -> gap.
    consensus = ConsensusFilter()

    three = consensus.evaluate(
        "three", ("sqlite",), "databases", ("action:modify",), {"three"},
        record("three", ("databases",), ("sqlite",), description="Ledger for claims"), None,
    )
    assert three.queue_mode == "queue", three
    assert three.signal_count == 3, three
    assert three.signals == ("bm25", "cap", "group"), three.signals
    assert three.confidence_bp == 7000, three.confidence_bp

    two = consensus.evaluate(
        "two", ("sqlite",), "databases", ("action:modify",), set(),
        record("two", ("databases",), ("sqlite",), description="Ledger for claims"), None,
    )
    assert two.queue_mode == "advisory", two
    assert two.signal_count == 2, two
    assert two.confidence_bp == 5000, two.confidence_bp

    one = consensus.evaluate(
        "one", ("sqlite",), "databases", ("action:modify",), set(),
        record("one", ("databases",), description="Ledger for claims"), None,
    )
    assert one.queue_mode == "gap" and one.signal_count == 1, one
    assert one.signals == ("group",), one.signals

    zero = consensus.evaluate(
        "zero", ("sqlite",), "databases", ("action:modify",), set(),
        record("zero", ("physics",), description="Molecule toolkit"), None,
    )
    assert zero.queue_mode == "gap" and zero.signal_count == 0, zero
    assert zero.signals == () and zero.confidence_bp == 0, zero

    # 3. No single signal can force queue: each signal is checked in isolation.
    isolated = {
        "group": consensus.evaluate(
            "group-only", ("sqlite",), "databases", ("action:modify",), set(),
            record("group-only", ("databases",), description="Ledger for claims"), None),
        "cap": consensus.evaluate(
            "cap-only", ("sqlite",), "databases", ("action:modify",), set(),
            record("cap-only", ("physics",), ("sqlite",), description="Partial differential equation solver"), None),
        "marker": consensus.evaluate(
            "marker-only", ("sqlite",), "databases", ("action:modify",), set(),
            record("marker-only", ("physics",), description="Update the solver"), None),
        "bm25": consensus.evaluate(
            "bm25-only", ("sqlite",), "databases", ("action:modify",), {"bm25-only"},
            record("bm25-only", ("physics",), description="Partial differential equation"), None),
        "card": consensus.evaluate(
            "card-only", ("sqlite",), "databases", ("action:modify",), set(),
            record("card-only", ("chemistry",), description="Molecule toolkit"),
            {"id": "card-only", "state": "verified"}),
    }
    for expected_signal, isolated_verdict in isolated.items():
        assert isolated_verdict.signals == (expected_signal,), (expected_signal, isolated_verdict)
        assert isolated_verdict.signal_count == 1, isolated_verdict
        assert isolated_verdict.queue_mode == "gap", (expected_signal, isolated_verdict.queue_mode)
        assert isolated_verdict.confidence_bp == skill_consensus.SIGNAL_WEIGHTS[expected_signal], isolated_verdict

    # 4. filter_batch: queue/advisory/gap counts, sorting and truncation.
    skills = [
        record("a_three", ("databases",), ("sqlite",), description="Ledger for claims"),
        record("b_two", ("databases",), ("sqlite",), description="Ledger for claims"),
        record("c_two_marker", ("databases",), description="Update the schema"),
        record("d_cap", ("physics",), ("sqlite",), description="Partial differential equation solver"),
        record("e_group", ("databases",), description="Ledger for claims"),
        record("f_card", ("chemistry",), description="Molecule toolkit"),
    ]
    cards = [{"id": "f_card", "state": "verified"}]

    def bm25_for_a(query_text, top_k):
        assert isinstance(query_text, str) and query_text.strip(), query_text
        return [{"skill_id": "a_three", "total_score": 9.5}][:top_k]

    batch = ConsensusFilter(bm25_for_a).filter_batch(skills, DB_STEP, cards)
    assert [v.skill_id for v in batch["queue"]] == ["a_three"], [v.skill_id for v in batch["queue"]]
    assert [v.skill_id for v in batch["advisory"]] == ["b_two", "c_two_marker"], batch["advisory"]
    assert batch["gaps"] == ["d_cap", "e_group", "f_card"], batch["gaps"]
    for left, right in zip(batch["advisory"], batch["advisory"][1:]):
        assert left.confidence_bp >= right.confidence_bp, (left, right)

    truncated = ConsensusFilter(bm25_for_a).filter_batch(
        skills, DB_STEP, cards, top_k_queue=1, top_k_advisory=1)
    assert len(truncated["queue"]) == 1 and len(truncated["advisory"]) == 1, truncated
    assert set(truncated["gaps"]) == {"c_two_marker", "d_cap", "e_group", "f_card"}, truncated["gaps"]

    # BM25 is optional: without it the three-signal case drops to advisory, and a broken
    # index is a silent miss rather than a crash.
    no_bm25 = ConsensusFilter().filter_batch(skills, DB_STEP, cards)
    assert no_bm25["queue"] == [], no_bm25["queue"]
    assert [v.skill_id for v in no_bm25["advisory"]] == ["a_three", "b_two", "c_two_marker"], \
        no_bm25["advisory"]
    assert no_bm25["advisory"][0].bm25_in_top is False
    raising = ConsensusFilter(lambda query, k: (_ for _ in ()).throw(RuntimeError("no index")))
    broke = raising.filter_batch(skills, DB_STEP, cards)
    assert broke["queue"] == [] and broke["advisory"][0].skill_id == "a_three", broke

    # Reproducible: same inputs -> same verdicts.
    first = ConsensusFilter(bm25_for_a).filter_batch(skills, DB_STEP, cards)
    second = ConsensusFilter(bm25_for_a).filter_batch(skills, DB_STEP, cards)
    assert [(v.skill_id, v.queue_mode, v.signal_count, v.signals, v.confidence_bp)
            for v in verdict_rows(first)] == \
           [(v.skill_id, v.queue_mode, v.signal_count, v.signals, v.confidence_bp)
            for v in verdict_rows(second)], (first, second)

    # 5. GroupSelector: select_groups returns the matching category first.
    selector = GroupSelector(CATEGORIES)
    groups = selector.select_groups("databases", ("sqlite",), ("action:modify",))
    assert groups and groups[0][0] == "databases", groups
    assert groups[0][1] >= 0.7, groups
    assert "category=1" in groups[0][2] and "cap=1" in groups[0][2], groups[0][2]
    assert all(group[0] != "chemistry" for group in groups), groups

    # Capability alone still selects its group; markers alone select theirs.
    by_cap = GroupSelector(CATEGORIES).select_groups("", ("testing",), ())
    assert by_cap and by_cap[0][0] == "coding", by_cap
    by_marker = GroupSelector(CATEGORIES).select_groups("", (), ("domain:physics",))
    assert by_marker and by_marker[0][0] == "physics", by_marker

    # No evidence -> no group at all (the prior is not a match).
    assert GroupSelector(CATEGORIES).select_groups("", (), ()) == []
    assert GroupSelector(()).select_groups("databases", ("sqlite",), ()) == []

    # 6. Exploration fires deterministically 1 in 10 steps.
    with tempfile.TemporaryDirectory() as tmp:
        weights_path = Path(tmp) / "skill-weights.json"
        explorer = GroupSelector(CATEGORIES, weights_path)
        explored = 0
        for _ in range(10):
            rows = explorer.select_groups("databases", ("sqlite",), ())
            explored += sum(1 for _category, _score, reason in rows if "exploration" in reason)
        assert explored == 1, explored
        assert exploration_trigger(10) and not exploration_trigger(9), "trigger must be 1/10"
        forced = GroupSelector(CATEGORIES).select_groups(
            "databases", ("sqlite",), (), explore=True)
        assert any("exploration" in reason for _category, _score, reason in forced), forced

        # 7. Weights persist between sessions and move in the right direction.
        writer = GroupSelector(CATEGORIES, weights_path)
        assert writer.weight_for("databases") == 0.5, writer.weight_for("databases")
        writer.record_outcome("databases", "dom-memory", True)
        assert writer.weight_for("databases") > 0.5, writer.weight_for("databases")
        payload = json.loads(weights_path.read_text(encoding="utf-8"))
        assert payload["schema"] == 1, payload
        assert payload["weights"]["databases"]["hit_count"] == 1, payload

        reader = GroupSelector(CATEGORIES, weights_path)
        assert reader.weight_for("databases") == writer.weight_for("databases"), (
            reader.weight_for("databases"), writer.weight_for("databases"))
        reader.record_outcome("databases", "other", False)
        assert reader.weight_for("databases") < writer.weight_for("databases"), "a miss must lower the weight"

        # A corrupt weights file degrades to the prior instead of raising.
        (Path(tmp) / "broken.json").write_text("{not json", encoding="utf-8")
        assert GroupSelector(CATEGORIES, Path(tmp) / "broken.json").weight_for("databases") == 0.5

    # 8. Scores are integers (confidence_bp), never floats.
    for candidate in verdict_rows(first) + verdict_rows(truncated):
        assert type(candidate.confidence_bp) is int, candidate
        assert 0 <= candidate.confidence_bp <= 10000, candidate
        assert type(candidate.signal_count) is int, candidate

    # GroupWeights: the prior and the empty hit-rate are well defined.
    prior = GroupWeights(group="databases")
    assert prior.weight == 0.5 and prior.hit_rate == 0.0, prior
    assert GroupWeights(group="x", hit_count=3, miss_count=1).hit_rate == 0.75

    # 9. End-to-end: step -> categories -> filtered skills -> queue (verified cards included).
    card_data = json.loads((ROOT / "opencode" / "mockup" / "skills.card.json").read_text(encoding="utf-8"))
    cards = card_data["cards"]
    skills = [
        record("dom-memory", ("databases",), ("ledger", "sqlite"), ("sqlite", "ledger"),
               description="Project memory ledger for sqlite claims"),
        record("sqlite-migration", ("databases",), ("sqlite", "migration"), ("migration", "sqlite"),
               description="SQLite schema upgrade"),
        record("pde-solver", ("physics",), ("physics", "runtime"), ("pde",),
               description="Partial differential equation solver"),
        record("rdkit", ("chemistry",), ("chemistry", "molecule"), ("molecule",),
               description="Molecule toolkit"),
    ]
    e2e_step = step(cap=("sqlite", "migration"), hint="databases",
                    markers=("action:modify", "domain:databases"))
    e2e_selector = GroupSelector(CATEGORIES)
    e2e_groups = e2e_selector.select_groups(
        "databases", ("sqlite", "migration"), ("action:modify", "domain:databases"))
    assert e2e_groups[0][0] == "databases", e2e_groups

    e2e = e2e_selector.select_skills(skills, e2e_step, cards, ConsensusFilter(), e2e_groups)
    assert [v.skill_id for v in e2e["queue"]] == ["dom-memory", "sqlite-migration"], e2e["queue"]
    assert all(v.confidence_bp == 7500 for v in e2e["queue"]), e2e["queue"]
    visible = {v.skill_id for v in verdict_rows(e2e)} | set(e2e["gaps"])
    assert "pde-solver" not in visible and "rdkit" not in visible, visible

    # No selected group -> no candidates, but every skill is reported as a gap.
    empty = e2e_selector.select_skills(skills, e2e_step, cards, ConsensusFilter(), [])
    assert empty["queue"] == [] and empty["advisory"] == [], empty
    assert empty["gaps"] == ["dom-memory", "pde-solver", "rdkit", "sqlite-migration"], empty["gaps"]

    print("PASS skill_consensus: 5 independent signals, queue>=3, advisory=2, gap<=1; "
          "confidence in integer bp; %d verified cards; group selection with 4 factors, "
          "deterministic 1/10 exploration, persistent weights, end-to-end queue %s"
          % (len(cards), [v.skill_id for v in e2e["queue"]]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
