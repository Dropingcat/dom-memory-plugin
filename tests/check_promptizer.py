"""Check the notes promptizer: specs, sentence distillation, sources, no invention."""
from __future__ import annotations

import sys
import tempfile
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))


def main() -> int:
    from mockup import promptizer

    for name, spec in promptizer.SPECS.items():
        assert spec["prompt"], name
        assert spec["limit"] > 0, name
        assert promptizer.mini_prompt(name).startswith(spec["prompt"]), name
        assert "Max %d chars" % spec["limit"] in promptizer.mini_prompt(name), name

    # short values pass unchanged
    assert promptizer.distill("  short   value  ", 240) == "short value"

    # long values cut on a sentence boundary, preferring the marked sentence
    long_text = (
        "This block collects notes. "
        "The now sentence says the agent is drafting the promptizer spec and the test. "
        "Everything else is background."
    )
    got = promptizer.distill(long_text, 90, markers=("now",))
    assert got.startswith("The now sentence"), got
    assert len(got) <= 90, (len(got), got)

    # without a marker the first full sentence wins
    got = promptizer.distill("First sentence here. Second sentence follows.", 30)
    assert got == "First sentence here.", got

    # hard clip never exceeds the limit and marks the cut
    got = promptizer.distill("x" * 200, 20)
    assert len(got) <= 20 and got.endswith("…"), (len(got), got)

    # header context clause is extracted after its marker
    context = "Baseline text. Сейчас: идёт сборка promptizer v0 и его теста. Дальше монитор."
    assert promptizer.clause(context, promptizer.CLAUSE_MARKERS["now"]) == "идёт сборка promptizer v0 и его теста."

    # property wins over context; missing everything is 'not recorded'
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        project = Path(tmp)
        run = project / ".dom-memory" / "main"
        run.mkdir(parents=True, exist_ok=True)
        previous = os.environ.get("DOM_RUNTIME_ROOT")
        os.environ["DOM_RUNTIME_ROOT"] = str(project / ".dom-memory")
        try:
            from mockup.admission import Service
            from mockup import planning
            svc = Service(run, profile="lite", review_mode="auto")
            svc.start(request="promptizer test")
            svc.plan_ingest("1. Item one\n1.1 Child")
            with svc.store.read() as c:
                task = planning.resolve_task(c, "1.1")
                assert task is not None, "plan item 1.1 missing"
                task_id = task["plan_task_id"]
                value = promptizer.render(c, "now", {"now": "curated now"}, task_id, context)
                assert value == "curated now", value
                value = promptizer.render(c, "now", {}, task_id, context)
                assert value == "идёт сборка promptizer v0 и его теста.", value
                value = promptizer.render(c, "why", {}, task_id, "")
                assert value == "not recorded", value
        finally:
            if previous is None:
                os.environ.pop("DOM_RUNTIME_ROOT", None)
            else:
                os.environ["DOM_RUNTIME_ROOT"] = previous

    print("PASS promptizer: specs, sentence distillation, context clause, sources, no invention")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
