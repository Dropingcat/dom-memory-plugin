"""Contract 1.3: the plan brief renders the header's declared CAP envelope.

The CURRENT line carries ``CAP: <cap,cap>`` when the header claim has an
``accept`` property.  The accept items are mapped by
``mockup.skill_router.capabilities_from_accept``; an absent or malformed
property renders no CAP line, so the producer gets ``cap: []``.

Run: python -B tests/check_brief_cap.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        project = Path(tmp)
        run = project / ".dom-memory" / "main"
        run.mkdir(parents=True, exist_ok=True)
        previous = os.environ.get("DOM_RUNTIME_ROOT")
        os.environ["DOM_RUNTIME_ROOT"] = str(project / ".dom-memory")
        try:
            from mockup.admission import Service
            from mockup.brief import project_brief

            svc = Service(run, profile="lite", review_mode="auto")
            svc.start(request="cap brief test")
            svc.plan_ingest("- [ ] L2 V5 migration")
            with svc.store.read() as c:
                header = c.execute(
                    "SELECT tc.claim_id FROM task_claims tc JOIN current_plan_tasks v "
                    "ON v.plan_task_id=tc.plan_task_id WHERE tc.role='defines' AND v.path='П1'"
                ).fetchone()[0]

            # 1. accept stored as JSON text (dom_annotate takes a string value).
            svc.annotate(
                header, "property",
                name="accept", value=json.dumps([{"type": "schema"}], ensure_ascii=False),
                reason="declare the acceptance kind",
            )
            text = project_brief(svc)
            current = next(row for row in text.splitlines() if row.startswith("CURRENT"))
            assert " CAP: migration,sqlite" in current, current

            # 2. A property that is not a valid accept list is not guessed away.
            svc.annotate(
                header, "property",
                name="accept", value="not json",
                reason="malformed accept must render no CAP",
            )
            text = project_brief(svc)
            current = next(row for row in text.splitlines() if row.startswith("CURRENT"))
            assert " CAP:" not in current, current
        finally:
            if previous is None:
                os.environ.pop("DOM_RUNTIME_ROOT", None)
            else:
                os.environ["DOM_RUNTIME_ROOT"] = previous
    print("PASS brief_cap: accept -> CAP: line, malformed accept -> no CAP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
