"""Check the persistent local monitor token."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))

from monitor.start import load_or_create_token  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = Path(tmp) / ".dom-memory" / "observability" / "monitor.token"
        first = load_or_create_token(path)
        second = load_or_create_token(path)
        assert first == second, (first, second)
        assert len(first) >= 20, first
        assert path.read_text(encoding="utf-8").strip() == first
    print("PASS monitor_token: created once, reused, non-empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
