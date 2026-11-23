"""Check the baseline DOM index printed by plan_check_hook when no check is due."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))
HOOK = ROOT / "opencode" / "hooks" / "plan_check_hook.py"


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        project = Path(tmp)
        run = project / ".dom-memory" / "main"
        run.mkdir(parents=True, exist_ok=True)
        previous = os.environ.get("DOM_RUNTIME_ROOT")
        os.environ["DOM_RUNTIME_ROOT"] = str(project / ".dom-memory")
        try:
            from mockup.admission import Service
            svc = Service(run, profile="lite", review_mode="auto")
            svc.start(request="baseline index test")
            svc.plan_ingest("1. Item one\n1.1 Child\n2. Item two")
        finally:
            if previous is None:
                os.environ.pop("DOM_RUNTIME_ROOT", None)
            else:
                os.environ["DOM_RUNTIME_ROOT"] = previous
        env = dict(os.environ)
        env.update({
            "OPENCODE_PROJECT_DIR": str(project),
            "DOM_RUNTIME_ROOT": str(project / ".dom-memory"),
            "DOM_CHECK_EVERY": "100",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        })
        result = subprocess.run(
            [sys.executable, "-B", str(HOOK)],
            input="hello baseline",
            text=True,
            capture_output=True,
            cwd=str(project),
            env=env,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "[DOM MEMORY INDEX]" in result.stdout, result.stdout
        assert "plan r1" in result.stdout, result.stdout
        assert "claims" in result.stdout, result.stdout
    print("PASS dom_index_injection: baseline index printed when no check is due")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
