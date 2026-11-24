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
        if result.stderr:
            print("stderr:", result.stderr)
        assert "[DOM MEMORY INDEX]" in result.stdout, result.stdout
        assert "plan r1" in result.stdout, result.stdout
        assert "claims" in result.stdout, result.stdout
        assert "ROOT:" in result.stdout or "ROOT REQUEST:" in result.stdout, result.stdout
        assert "MAIN TASK:" in result.stdout, result.stdout
        assert "GRANDLINE:" in result.stdout, result.stdout
        assert "LINE:" in result.stdout, result.stdout
        assert "CURRENT" in result.stdout, result.stdout
        assert "ACTIVE:" in result.stdout, result.stdout
        assert "CURRENT MEANING:" in result.stdout, result.stdout
        assert "WHY:" in result.stdout, result.stdout
        assert "NOW:" in result.stdout, result.stdout
        assert "TOOLS:" in result.stdout, result.stdout
        assert "GITS:" in result.stdout, result.stdout
        assert "DONE:" in result.stdout, result.stdout
        assert "NEXT:" in result.stdout, result.stdout
        assert "RISKS:" in result.stdout, result.stdout
        assert "RISK CHECKS:" in result.stdout, result.stdout
        assert "CONFLICTS:" in result.stdout, result.stdout
        assert "GRAPH:" in result.stdout, result.stdout
        assert "RECENT STATEMENTS:" in result.stdout, result.stdout
        assert "CHECKLIST:" in result.stdout, result.stdout
    print("PASS dom_index_injection: root/line/current/checklist brief printed when no check is due")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
