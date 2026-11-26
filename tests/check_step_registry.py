"""Check the micro-step registry (sigma) and the tau fingerprint gate.

Run: python -B tests/check_step_registry.py
"""
from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))

from mockup import step_registry  # noqa: E402
from mockup.step_registry import (  # noqa: E402
    BlockFingerprint,
    MicroStep,
    RegistryExtractor,
    StepWindow,
    skill_index_digest,
)

TASKS = [
    {"plan_task_id": "pt-1", "path": "П1", "title": "Прочитать схему таблицы claims",
     "status": "done", "version": 2, "accept": [{"id": "a1", "type": "claim"}],
     "context": "sqlite ledger"},
    {"plan_task_id": "pt-2", "path": "П2", "title": "Написать миграцию v4→v5",
     "status": "in_progress", "version": 2, "accept": [{"id": "a2", "type": "schema"}],
     "context": "x" * 900},
    {"plan_task_id": "pt-3", "path": "П3", "title": "Обновить тест check_plan_identity",
     "status": "pending", "version": 2},
    {"plan_task_id": "pt-4", "path": "П4", "title": "Прогнать smoke_cold_start",
     "status": "done", "version": 2},
    {"plan_task_id": "pt-5", "path": "П5", "title": "Описать изменения в GET_STARTED",
     "status": "pending", "version": 2},
    {"plan_task_id": "pt-6", "path": "П6", "title": "Согласовать формат audit-таблиц",
     "status": "pending", "version": 2},
    {"plan_task_id": "pt-7", "path": "П7", "title": "Обновить панель",
     "status": "pending", "version": 2},
]

TREE = [{
    "plan_task_id": "pt-pi", "path": "П1", "title": "Схема таблицы claims",
    "status": "in_progress", "version": 4, "accept": [{"id": "a", "type": "schema"}],
    "context": "plan item context",
    "children": [
        {"plan_task_id": "pt-a", "path": "П1.1", "title": "Добавить колонку in_force",
         "status": "pending", "version": 4, "context": "sqlite ledger schema change"},
        {"plan_task_id": "pt-b", "path": "П1.2", "title": "Описать изменение",
         "status": "pending", "version": 4},
    ],
}, {
    "plan_task_id": "pt-q", "path": "П2", "title": "Написать отчёт",
    "status": "pending", "version": 1,
}]


class FakeRouter:
    """Minimal router stand-in: proves the injected module is used."""
    RULES = {"testing": ("test",)}
    CAPABILITIES = ("testing",)

    def __init__(self):
        self.calls = 0

    def capabilities_from_accept(self, accept):
        self.calls += 1
        return ["testing"], []


class Row:
    """Read-only mapping shaped like sqlite3.Row."""

    def __init__(self, data):
        self._data = dict(data)

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key):
        return self._data[key]


def check_frozen():
    step = MicroStep(step_id="s", title="t", parent_plan_task_id="", plan_version=1,
                     cap=("b", "a", "a"), markers=("z", "a"), context="ok")
    assert step.cap == ("a", "b"), step.cap            # canonical set
    assert step.markers == ("a", "z"), step.markers
    try:
        step.title = "changed"
        raise AssertionError("MicroStep must be frozen")
    except FrozenInstanceError:
        pass
    try:
        MicroStep(step_id="s", title="t", parent_plan_task_id="", plan_version=1,
                  context="y" * 601)
        raise AssertionError("MicroStep.context must be capped at 600")
    except ValueError:
        pass

    window = StepWindow(current=step, next_steps=(step,))
    try:
        window.current = step
        raise AssertionError("StepWindow must be frozen")
    except FrozenInstanceError:
        pass
    try:
        StepWindow(current=step, next_steps=(step, step, step, step))
        raise AssertionError("StepWindow must hold at most 3 next steps")
    except ValueError:
        pass


def check_window(extractor):
    window = extractor.from_plan_tasks(TASKS, "2")  # 'П2' by normalized path
    assert window.current.step_id == "pt-2", window.current
    assert window.current.title == "Написать миграцию v4→v5", window.current
    assert window.current.plan_version == 2, window.current
    assert window.current.parent_plan_task_id == "", window.current
    assert window.current.cap == ("migration", "sqlite"), window.current.cap
    assert window.current.category_hint == "migration", window.current.category_hint
    assert len(window.current.context) == step_registry.CONTEXT_LIMIT, len(window.current.context)
    assert window.current.context == ("x" * 900)[:step_registry.CONTEXT_LIMIT]
    assert "migration" in window.current.markers, window.current.markers
    assert "action:modify" in window.current.markers, window.current.markers
    assert "domain:migration" in window.current.markers, window.current.markers

    assert len(window.next_steps) == 3, window.next_steps       # 0-3 prefetch
    assert [s.step_id for s in window.next_steps] == ["pt-3", "pt-5", "pt-6"], window.next_steps
    assert len(window.steps) == 4, window.steps
    assert all(s.step_id != "pt-4" for s in window.steps), "done steps are not prefetched"

    by_id = extractor.from_plan_tasks(TASKS, "pt-2")
    by_title = extractor.from_plan_tasks(TASKS, "Написать миграцию v4→v5")
    assert by_id.current.step_id == by_title.current.step_id == "pt-2"

    tail = extractor.from_plan_tasks(TASKS, "pt-7")
    assert tail.next_steps == (), tail.next_steps               # last item: nothing to prefetch

    # Live projection rows are read-only mappings (sqlite3.Row), not dicts.
    live = [Row({"plan_task_id": "pt-live", "path": "П1", "title": "Read the schema",
                 "status": "pending", "version": 5, "parent_id": None})]
    live_window = extractor.from_plan_tasks(live, "pt-live")
    assert live_window.current.step_id == "pt-live", live_window.current
    assert live_window.current.plan_version == 5, live_window.current

    # Nested plan: children become the sigma queue, caps inherit from the plan item (pi).
    nested = extractor.from_plan_tasks(TREE, "П1.1")
    assert nested.current.step_id == "pt-a", nested.current
    assert nested.current.parent_plan_task_id == "pt-pi", nested.current
    assert nested.current.cap == ("migration", "sqlite"), nested.current.cap
    assert [s.step_id for s in nested.next_steps] == ["pt-b", "pt-q"], nested.next_steps

    for empty, ref, marker in (([], "x", "PLAN_EMPTY"), (TASKS, "nope", "PLAN_TASK_NOT_FOUND")):
        try:
            extractor.from_plan_tasks(empty, ref)
            raise AssertionError("expected %s" % marker)
        except ValueError as exc:
            assert marker in str(exc), exc


def check_hints(extractor):
    # >= 2 independent evidence families -> a category
    assert extractor.multi_marker_hint("Написать миграцию v4→v5", "", []) == "migration"
    assert extractor.multi_marker_hint("Разобраться", "", ["claim_sessions and hash-chain"]) == "ledger"
    assert extractor.multi_marker_hint("Обновить схему", "", [],
                                       accept=[{"type": "schema"}]) == "migration"

    # <= 1 family -> '' (a single verb or a single identifier is not enough)
    assert extractor.multi_marker_hint("Run the tests", "", []) == ""
    assert extractor.multi_marker_hint("Написать миграцию", "", []) == ""
    assert extractor.multi_marker_hint("Сделать зарядку", "", [],
                                       accept=[{"type": "schema"}]) == ""
    assert extractor.multi_marker_hint("тест миграции", "", []) == ""
    assert extractor.multi_marker_hint("", "", []) == ""
    # One family is never enough, including explicit capabilities alone.
    assert extractor.multi_marker_hint("Сделать зарядку", "", [],
                                       capabilities=["testing"]) == ""
    assert extractor.multi_marker_hint("Сделать зарядку", "", [], accept=[{"type": "test"}],
                                       capabilities=["testing"]) == "testing"
    # Whole-word stems: 'auth' does not fire on 'author', 'run' does not fire on 'runtime'.
    assert extractor.multi_marker_hint("Check the author field", "", []) == ""
    assert extractor.multi_marker_hint("Update the runtime", "", []) == ""

    # Addendum §2.3 sigma examples: accept (authority) plus text evidence resolve the category.
    assert extractor.multi_marker_hint("Прочитать текущую схему таблицы claims", "", [],
                                       accept=[{"type": "claim"}]) == "ledger"
    assert extractor.multi_marker_hint("Добавить колонку in_force", "", [],
                                       accept=[{"type": "schema"}]) == "sqlite"
    assert extractor.multi_marker_hint("Написать миграцию v4→v5", "", [],
                                       accept=[{"type": "schema"}]) == "migration"
    assert extractor.multi_marker_hint("Обновить тест check_plan_identity", "", [],
                                       accept=[{"type": "test"}]) == "testing"
    assert extractor.multi_marker_hint("Прогнать smoke_cold_start", "", [],
                                       accept=[{"type": "test"}]) == "testing"

    # The injected router drives capabilities_from_accept.
    fake = FakeRouter()
    injected = RegistryExtractor(fake)
    window = injected.from_plan_tasks(
        [{"plan_task_id": "x", "path": "П1", "title": "test it", "status": "pending",
          "version": 1, "accept": [{"id": "a", "type": "whatever"}]}], "x")
    assert fake.calls >= 1, fake.calls
    assert window.current.cap == ("testing",), window.current.cap
    assert window.current.category_hint == "testing", window.current.category_hint

    try:
        RegistryExtractor(object())
        raise AssertionError("a router without capabilities_from_accept must be refused")
    except TypeError:
        pass


def check_fingerprint():
    kwargs = dict(step_id="pt-2", cap_set=frozenset({"sqlite", "migration"}),
                  category_hint="migration", markers=frozenset({"migration", "domain:migration"}),
                  plan_version=2, index_digest="idx-a")
    base = BlockFingerprint(**kwargs)
    same = BlockFingerprint(step_id="pt-2", cap_set=frozenset({"migration", "sqlite"}),
                            category_hint="migration",
                            markers=frozenset({"domain:migration", "migration"}),
                            plan_version=2, index_digest="idx-a")

    # Changed (and first) -> re-emit, idle counter reset.
    assert base.should_reemit(None) is True
    assert base.advance(None).idle_reissue_count == 0

    # Unchanged -> idle re-issue: no re-emit, counter increments.
    assert same.should_reemit(base) is False
    assert same.advance(base).idle_reissue_count == 1
    assert same.advance(same.advance(base)).idle_reissue_count == 2

    # No hard cap on accumulated idle re-issues (H1), and a real change resets the count.
    carry = base
    for _ in range(20):
        assert base.should_reemit(carry) is False
        carry = base.advance(carry)
    assert carry.idle_reissue_count == 20, carry.idle_reissue_count
    for change in (dict(plan_version=3), dict(index_digest="idx-b"),
                   dict(cap_set=frozenset({"sqlite", "migration", "testing"})),
                   dict(category_hint=""), dict(markers=frozenset({"migration"})),
                   dict(step_id="pt-3")):
        changed = BlockFingerprint(**{**kwargs, **change})
        assert changed.should_reemit(carry) is True, change
        assert changed.advance(carry).idle_reissue_count == 0, change

    # The idle counter is not part of the fingerprint identity.
    counted = replace(base, idle_reissue_count=7)
    assert counted.should_reemit(base) is False
    assert counted.digest() == base.digest()

    # Frozen and validated.
    try:
        base.step_id = "other"
        raise AssertionError("BlockFingerprint must be frozen")
    except FrozenInstanceError:
        pass
    try:
        BlockFingerprint(**{**kwargs, "idle_reissue_count": -1})
        raise AssertionError("idle_reissue_count cannot be negative")
    except ValueError:
        pass

    # Determinism: same inputs -> same hash, order-independent.
    left = BlockFingerprint(step_id="s", cap_set=frozenset({"b", "a"}), category_hint="c",
                            markers=frozenset({"y", "x"}), plan_version=1, index_digest="i")
    right = BlockFingerprint(step_id="s", cap_set=frozenset({"a", "b"}), category_hint="c",
                             markers=frozenset({"x", "y"}), plan_version=1, index_digest="i")
    assert left.digest() == right.digest()
    assert len(left.digest()) == 64
    assert replace(right, step_id="t").digest() != right.digest()
    assert replace(right, index_digest="j").digest() != right.digest()
    assert skill_index_digest([{"id": "a", "digest": "1"}, {"id": "b", "digest": "2"}]) == \
        skill_index_digest([{"id": "b", "digest": "2"}, {"id": "a", "digest": "1"}])
    assert skill_index_digest([{"id": "a", "digest": "1"}]) != \
        skill_index_digest([{"id": "a", "digest": "2"}])

    # from_step carries the sigma identity and the inherited cap set.
    window = RegistryExtractor().from_plan_tasks(TASKS, "pt-2")
    built = BlockFingerprint.from_step(window.current, "idx-a")
    assert built.step_id == window.current.step_id
    assert built.cap_set == frozenset(window.current.cap)
    assert built.category_hint == "migration"
    assert built.plan_version == 2
    assert built.idle_reissue_count == 0
    override = BlockFingerprint.from_step(window.current, "idx-a", plan_version=3, cap=("testing",))
    assert override.plan_version == 3 and override.cap_set == frozenset({"testing"})


def main() -> int:
    extractor = RegistryExtractor()
    check_frozen()
    check_window(extractor)
    check_hints(extractor)
    check_fingerprint()
    print("PASS step_registry: frozen sigma window, multi-marker hint, plan-task extraction, tau gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
