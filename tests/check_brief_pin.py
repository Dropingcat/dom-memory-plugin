"""Check that a current_task pointing to a done item falls back to the auto pick."""
from __future__ import annotations

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
            svc.start(request="pin test")
            svc.plan_ingest("- [x] Closed item\n- [ ] Open item\n  - [ ] Open child")
            with svc.store.read() as c:
                root = c.execute("SELECT request_claim_id FROM project_root WHERE id=1").fetchone()[0]
            svc.annotate(root, "property", name="current_task", value="П1",
                         reason="pin a done block to test the fallback")
            text = project_brief(svc)
            assert "CURRENT (auto)" in text, text
            assert "current_task=П1 is done" in text, text
            assert "ACTIVE: pinned" not in text, text
            assert "CURRENT: П1 " not in text, text
            assert "Open item" in text, text
            svc.annotate(root, "property", name="current_task", value="П2",
                         reason="pin an open block")
            text = project_brief(svc)
            assert "ACTIVE: pinned by current_task=П2" in text, text
            assert "CURRENT: П2 " in text, text
        finally:
            if previous is None:
                os.environ.pop("DOM_RUNTIME_ROOT", None)
            else:
                os.environ["DOM_RUNTIME_ROOT"] = previous
    print("PASS brief_pin: a done current_task falls back to auto, an open one pins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
