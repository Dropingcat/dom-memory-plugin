"""Start the read-only DOM monitor with a persistent local token file.

    python opencode/monitor/start.py --project E:/path/to/project
    python opencode/monitor/start.py --project E:/path/to/project --open

The token is created once at
`<project>/.dom-memory/observability/monitor.token` (owner-only where the OS
supports it) and reused on later starts.  The server stays in this process.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import webbrowser
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from monitor.server import serve  # noqa: E402


def load_or_create_token(path: str | Path) -> str:
    token_path = Path(path)
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if token:
        return token
    token = secrets.token_urlsafe(24)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(token_path, 0o600)
    except OSError:
        pass
    return token


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Start the DOM read-only monitor")
    ap.add_argument("--project", required=True)
    ap.add_argument("--directory", default=None)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token-file", default=None)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args(argv)

    project = Path(args.project).resolve()
    token_file = Path(args.token_file) if args.token_file else project / ".dom-memory" / "observability" / "monitor.token"
    token = load_or_create_token(token_file)
    monitor = serve(project, args.port, token, args.directory)
    print(json.dumps({
        "url": monitor.url,
        "token": token,
        "token_file": str(token_file),
        "db_path": str(Path(args.directory) if args.directory else project / ".dom-memory" / "main"),
    }, ensure_ascii=False))
    if args.open:
        webbrowser.open(monitor.url)
    try:
        monitor.thread.join()
    except KeyboardInterrupt:
        monitor.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
