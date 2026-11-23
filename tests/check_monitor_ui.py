"""Check the read-only monitor UI: tabs, CSP, no external assets, textContent only."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))

from monitor.server import serve  # noqa: E402


def fetch(url: str) -> tuple[int, str, dict]:
    with urlopen(url, timeout=10) as response:
        return response.status, response.read().decode("utf-8"), dict(response.headers)


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        project = Path(tmp)
        monitor = serve(project, port=0, token="t")
        try:
            status, html, headers = fetch(monitor.url + "/")
            assert status == 200
            assert "Hooks &amp; injections" in html, html[:200]
            assert "DOM claims" in html
            assert "Health" in html
            assert 'src="http' not in html and 'href="http' not in html
            assert headers.get("Content-Security-Policy")

            status, js, _ = fetch(monitor.url + "/static/app.js")
            assert status == 200
            assert "textContent" in js
            assert "innerHTML" not in js
            assert "http://" not in js and "https://" not in js

            status, css, _ = fetch(monitor.url + "/static/style.css")
            assert status == 200 and "--bg" in css
        finally:
            monitor.close()
    print("PASS monitor_ui: tabs, CSP, no external assets, textContent only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
