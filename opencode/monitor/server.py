"""Read-only HTTP API for the DOM observability monitor.

No writes, no external dependencies.  Binds 127.0.0.1, requires a bearer token
for every `/api/*` request, checks the Host header, and returns JSON errors.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from mockup.admission import verify_chain  # noqa: E402
from mockup import claim_entry  # noqa: E402
from mockup.storage import Store  # noqa: E402

MAX_SNAPSHOT_BYTES = 10 * 1024 * 1024
MAX_HOOK_LINES = 200
MAX_CLAIMS = 200
SESSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SNAPSHOT_RE = re.compile(r"^[A-Za-z0-9._-]+\.json$")
HOSTS = {"127.0.0.1", "localhost"}
STATIC_DIR = Path(__file__).resolve().parent / "static"


@dataclass
class Monitor:
    url: str
    token: str
    server: ThreadingHTTPServer
    thread: threading.Thread

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _read_text(raw) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return str(raw or "")


def _claim_graph(con):
    children = claim_entry._children(con)
    archived = claim_entry._archived(con)
    info = claim_entry._lineage(children)
    parent = {new: old for old, new in children.items()}
    return children, archived, info, parent


def _claim_version(claim_id: str, info, parent, archived) -> dict:
    root, version = info(claim_id)
    return {
        "logical_claim_id": root,
        "version": version,
        "supersedes": parent.get(claim_id),
        "is_current": claim_id not in archived,
    }


def _properties(con, claim_id: str) -> list[dict]:
    out = []
    for event in con.execute(
        "SELECT payload FROM events WHERE claim_ref=? AND kind='property' ORDER BY seq",
        (claim_id,),
    ):
        try:
            payload = json.loads(event["payload"])
            value = (payload.get("value") or {}).get("value") or {}
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            out.append({
                "name": value["name"],
                "value": value.get("value"),
                "reason": payload.get("reason"),
                "at": payload.get("server_time"),
            })
    return out


def _tail_lines(path: Path, max_bytes: int = 2 * 1024 * 1024) -> list[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
    except OSError:
        return []
    text = data.decode("utf-8", "replace")
    lines = text.splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]
    return lines


def _read_hooks(project_dir: Path, limit: int = MAX_HOOK_LINES) -> tuple[list[dict], int]:
    obs = project_dir / ".dom-memory" / "observability"
    events: list[dict] = []
    dropped = 0
    try:
        names = sorted(obs.glob("hooks-*.jsonl"))
    except OSError:
        names = []
    for path in names:
        for line in _tail_lines(path):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("status") == "LINE_TOO_LARGE" or event.get("dropped"):
                dropped += 1
            events.append(event)
    events.sort(key=lambda item: str(item.get("ts") or ""))
    return events[-limit:], dropped


def _list_snapshots(project_dir: Path, session: str | None = None, limit: int = 100) -> list[dict]:
    root = project_dir / ".dom-memory" / "observability" / "prompts"
    out: list[dict] = []
    if not root.is_dir():
        return out
    sessions = [root / session] if session else sorted(p for p in root.iterdir() if p.is_dir())
    for directory in sessions:
        if not directory.is_dir() or not SESSION_RE.match(directory.name):
            continue
        for path in sorted(directory.glob("*.json")):
            if not SNAPSHOT_RE.match(path.name):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            out.append(
                {
                    "id": f"{directory.name}/{path.name}",
                    "session": directory.name,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
    out.sort(key=lambda item: item["mtime"], reverse=True)
    return out[:limit]


def _snapshot_path(project_dir: Path, snapshot_id: str) -> Path | None:
    parts = snapshot_id.split("/", 1)
    if len(parts) != 2 or not SESSION_RE.match(parts[0]) or not SNAPSHOT_RE.match(parts[1]):
        return None
    root = (project_dir / ".dom-memory" / "observability" / "prompts").resolve()
    path = (root / parts[0] / parts[1]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path


def _classify_block(block: dict) -> str:
    meta = block.get("metadata") or {}
    instructions = (meta.get("experimental") or {}).get("instructions")
    if isinstance(instructions, list) and instructions:
        keys = [item.get("key") for item in instructions if isinstance(item, dict) and item.get("key")]
        if keys:
            return ",".join(keys)
    text = str(block.get("text") or "")
    if text.startswith("You are an AI agent running in OpenCode"):
        return "core/system"
    if text.startswith("# Your Model"):
        return "core/model"
    if text.startswith("When you create a worktree"):
        return "core/worktree"
    if text.startswith("[harness-superpowers-planning"):
        return "plugin/harness-superpowers-planning"
    if text.startswith("[DOM"):
        return "dom"
    return "unknown"


def _enrich_snapshot(snapshot: dict) -> dict:
    """Backward-compatible block attribution for snapshots written before 0.2.6 Task 2."""
    if not isinstance(snapshot, dict):
        return snapshot
    system = snapshot.get("system") or []
    if "system_blocks" not in snapshot:
        snapshot["system_blocks"] = [
            {
                "index": index,
                "origin": _classify_block(block if isinstance(block, dict) else {}),
                "chars": len(str((block or {}).get("text") or "")),
                "head": str((block or {}).get("text") or "")[:120],
            }
            for index, block in enumerate(system)
        ]
    if "origin_summary" not in snapshot:
        summary = {}
        for block in snapshot["system_blocks"]:
            summary[block["origin"]] = summary.get(block["origin"], 0) + 1
        snapshot["origin_summary"] = summary
    if "dom_tools" not in snapshot:
        codemode = next(
            (block for block in system if isinstance(block, dict) and "core/codemode" in _classify_block(block)),
            None,
        )
        text = str((codemode or {}).get("text") or "")
        snapshot["dom_tools"] = sorted(set(re.findall(r"tools\[\"dom-memory\"\]\.([A-Za-z0-9_]+)", text)))
    return snapshot


def _health(project_dir: Path, directory: Path) -> dict:
    store = Store(directory, read_only=True)
    db_path = os.path.realpath(str(store.path))
    base = {"db_path": db_path, "directory": str(directory)}
    if not store.path.is_file():
        return {**base, "chain_status": "MISSING_DB", "warnings": ["MISSING_DB"]}
    try:
        with store.read() as con:
            chain = verify_chain(con)
            counts = {}
            for table in ("claims", "events", "candidates", "reviews", "plan_tasks"):
                try:
                    counts[table] = int(con.execute("SELECT count(*) FROM %s" % table).fetchone()[0])
                except sqlite3.Error:
                    counts[table] = None
            plan = None
            try:
                row = con.execute(
                    "SELECT source_revision FROM plan_projection_state ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                plan = row[0] if row else None
            except sqlite3.Error:
                pass
            review = None
            try:
                row = con.execute("SELECT mode FROM review_policy_log ORDER BY rowid DESC LIMIT 1").fetchone()
                review = row[0] if row else None
            except sqlite3.Error:
                pass
        warnings = []
        if counts.get("claims") is None:
            warnings.append("SCHEMA_INCOMPLETE")
        return {
            **base,
            "chain_status": chain.get("status", "UNKNOWN"),
            "events": chain.get("events"),
            "counts": counts,
            "plan_revision": plan,
            "review_mode": review,
            "warnings": warnings,
        }
    except (sqlite3.DatabaseError, OSError) as exc:
        return {
            **base,
            "chain_status": "STORAGE_CORRUPT",
            "warnings": ["STORAGE_CORRUPT"],
            "error": type(exc).__name__,
        }


def _claims(project_dir: Path, directory: Path, query: str, typ: str, branch: str, limit: int) -> dict:
    store = Store(directory, read_only=True)
    if not store.path.is_file():
        return {"status": "MISSING_DB", "items": [], "total": 0}
    limit = max(1, min(limit, MAX_CLAIMS))
    sql = (
        "SELECT cl.id, cl.content_type, cl.origin_branch, cl.origin_version, cl.raw, cl.raw_sha256, "
        "NOT EXISTS(SELECT 1 FROM events e WHERE e.claim_ref=cl.id AND e.kind IN ('replacement','archive')) AS is_current "
        "FROM claims cl WHERE (?1='' OR lower(CAST(cl.raw AS TEXT)) LIKE '%' || lower(?1) || '%') "
        "AND (?2='' OR cl.content_type=?2) AND (?3='' OR cl.origin_branch=?3) "
        "ORDER BY cl.rowid DESC LIMIT ?4"
    )
    with store.read() as con:
        rows = con.execute(sql, (query, typ, branch, limit)).fetchall()
        total = int(
            con.execute(
                "SELECT count(*) FROM claims cl WHERE (?1='' OR lower(CAST(cl.raw AS TEXT)) LIKE '%' || lower(?1) || '%') "
                "AND (?2='' OR cl.content_type=?2) AND (?3='' OR cl.origin_branch=?3)",
                (query, typ, branch),
            ).fetchone()[0]
        )
        _children, archived, info, parent = _claim_graph(con)
    items = []
    for row in rows:
        text = _read_text(row["raw"])
        version = _claim_version(row["id"], info, parent, archived)
        items.append(
            {
                "claim_id": row["id"],
                "type": row["content_type"],
                "branch": row["origin_branch"],
                "origin_version": row["origin_version"],
                "raw_sha256": row["raw_sha256"],
                "is_current": version["is_current"],
                "logical_claim_id": version["logical_claim_id"],
                "version": version["version"],
                "supersedes": version["supersedes"],
                "preview": text[:240],
                "chars": len(text),
            }
        )
    return {"status": "OK", "items": items, "total": total, "truncated": total > len(items)}


def _claim_detail(directory: Path, claim_id: str) -> dict | None:
    store = Store(directory, read_only=True)
    if not store.path.is_file():
        return None
    with store.read() as con:
        row = con.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        if row is None:
            return None
        payload = {}
        try:
            payload = json.loads(row["payload"])
        except (ValueError, TypeError):
            payload = {}
        children, archived, info, parent = _claim_graph(con)
        version = _claim_version(claim_id, info, parent, archived)
        properties = _properties(con, claim_id)
        versions = []
        root = version["logical_claim_id"]
        cursor = root
        seen = set()
        while cursor and cursor not in seen:
            seen.add(cursor)
            found = con.execute(
                "SELECT cl.raw_sha256, t.revision FROM claims cl JOIN transactions t ON t.id=cl.tx WHERE cl.id=?",
                (cursor,),
            ).fetchone()
            if found is None:
                break
            _, number = info(cursor)
            versions.append({
                "claim_id": cursor,
                "version": number,
                "supersedes": parent.get(cursor),
                "raw_sha256": found["raw_sha256"],
                "revision": found["revision"],
                "is_current": cursor not in archived,
            })
            cursor = children.get(cursor)
        events = [
            {"kind": event["kind"], "claim_ref": event["claim_ref"], "payload": _read_text(event["payload"])[:2000]}
            for event in con.execute(
                "SELECT kind, claim_ref, payload FROM events WHERE claim_ref=? ORDER BY seq DESC LIMIT 20",
                (claim_id,),
            )
        ]
    return {
        "status": "OK",
        "claim_id": claim_id,
        "type": row["content_type"],
        "branch": row["origin_branch"],
        "origin_version": row["origin_version"],
        "text": _read_text(row["raw"]),
        "raw_sha256": row["raw_sha256"],
        "logical_claim_id": version["logical_claim_id"],
        "version": version["version"],
        "supersedes": version["supersedes"],
        "is_current": version["is_current"],
        "properties": properties,
        "anatomy": payload.get("anatomy") or {},
        "metadata": payload.get("metadata") or {},
        "requirement_refs": payload.get("requirement_refs") or [],
        "payload": payload,
        "versions": versions,
        "events": events,
    }


def _plan_tree(directory: Path) -> dict:
    store = Store(directory, read_only=True)
    if not store.path.is_file():
        return {"status": "MISSING_DB", "source_revision": None, "tasks": []}
    with store.read() as con:
        state = con.execute(
            "SELECT source, source_revision FROM plan_projection_state ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        rows = con.execute(
            "SELECT v.plan_task_id, v.parent_id, v.path, v.title, v.status, v.archived, v.source_revision, "
            "b.branch, b.level, b.parent_branch, "
            "(SELECT count(*) FROM task_claims tc WHERE tc.plan_task_id=v.plan_task_id) AS claim_count "
            "FROM current_plan_tasks v LEFT JOIN plan_branches b ON b.plan_task_id=v.plan_task_id "
            "WHERE v.archived=0 ORDER BY v.path"
        ).fetchall()
    tasks = [dict(row) for row in rows]
    by_parent: dict[str | None, list[dict]] = {}
    for task in tasks:
        by_parent.setdefault(task["parent_id"], []).append(task)

    def build(parent=None):
        out = []
        for task in by_parent.get(parent, []):
            task["children"] = build(task["plan_task_id"])
            out.append(task)
        return out

    return {
        "status": "OK",
        "source": state["source"] if state else None,
        "source_revision": state["source_revision"] if state else None,
        "count": len(tasks),
        "tasks": build(None),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "dom-monitor/1.0"
    project_dir: Path
    directory: Path
    token: str

    def log_message(self, _format, *_args):
        return

    def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'",
        )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, value):
        self._send(status, _json_bytes(value))

    def _send_file(self, path: Path, content_type: str):
        try:
            self._send(200, path.read_bytes(), content_type)
        except OSError:
            self._error(404, "NOT_FOUND")

    def _error(self, status: int, code: str, message: str = ""):
        self._json(status, {"error": {"code": code, "message": message}})

    def _allowed_host(self) -> bool:
        host = self.headers.get("Host", "")
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return name in HOSTS

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[7:], self.token)

    def do_OPTIONS(self):
        self._error(405, "METHOD_NOT_ALLOWED")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._allowed_host():
            self._error(403, "HOST_NOT_ALLOWED")
            return
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            if not self._authorized():
                self._error(401, "UNAUTHORIZED")
                return
        try:
            if path == "/api/health":
                self._json(200, _health(self.project_dir, self.directory))
                return
            if path == "/api/claims":
                query = parse_qs(parsed.query)
                body = _claims(
                    self.project_dir,
                    self.directory,
                    (query.get("query") or [""])[0],
                    (query.get("type") or [""])[0],
                    (query.get("branch") or [""])[0],
                    int((query.get("limit") or ["50"])[0]),
                )
                self._json(200 if body.get("status") == "OK" else 503, body)
                return
            if path.startswith("/api/claim/"):
                claim_id = path.rsplit("/", 1)[-1]
                if not re.match(r"^[A-Fa-f0-9-]{36}$", claim_id):
                    self._error(400, "INVALID_ID")
                    return
                body = _claim_detail(self.directory, claim_id)
                if body is None:
                    self._error(404, "NOT_FOUND")
                    return
                self._json(200, body)
                return
            if path == "/api/hooks":
                events, dropped = _read_hooks(self.project_dir)
                self._json(200, {"status": "OK", "items": events, "dropped_events": dropped})
                return
            if path == "/api/snapshots":
                query = parse_qs(parsed.query)
                session = (query.get("session") or [None])[0]
                if session is not None and not SESSION_RE.match(session):
                    self._error(400, "INVALID_ID")
                    return
                self._json(200, {"status": "OK", "items": _list_snapshots(self.project_dir, session)})
                return
            if path.startswith("/api/snapshot/"):
                snapshot_id = path[len("/api/snapshot/"):]
                path_obj = _snapshot_path(self.project_dir, snapshot_id)
                if path_obj is None:
                    self._error(404, "NOT_FOUND")
                    return
                if path_obj.stat().st_size > MAX_SNAPSHOT_BYTES:
                    self._error(413, "TOO_LARGE")
                    return
                try:
                    self._json(200, _enrich_snapshot(json.loads(path_obj.read_text(encoding="utf-8"))))
                except (OSError, ValueError):
                    self._error(422, "INVALID_SNAPSHOT")
                return
            if path == "/api/plan/tree":
                self._json(200, _plan_tree(self.directory))
                return
            if path == "/api/plan":
                self._json(200, {"status": "OK", "plan_revision": _health(self.project_dir, self.directory).get("plan_revision")})
                return
            if path in ("/", "/index.html"):
                self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
                return
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                if not re.match(r"^[A-Za-z0-9._-]+$", name):
                    self._error(404, "NOT_FOUND")
                    return
                target = (STATIC_DIR / name).resolve()
                if not target.is_relative_to(STATIC_DIR.resolve()) or not target.is_file():
                    self._error(404, "NOT_FOUND")
                    return
                if name.endswith(".js"):
                    content_type = "text/javascript; charset=utf-8"
                elif name.endswith(".css"):
                    content_type = "text/css; charset=utf-8"
                elif name.endswith(".svg"):
                    content_type = "image/svg+xml"
                else:
                    content_type = "application/octet-stream"
                self._send_file(target, content_type)
                return
            self._error(404, "NOT_FOUND")
        except (ValueError, sqlite3.DatabaseError, OSError) as exc:
            self._error(503, "STORAGE_CORRUPT", type(exc).__name__)


def serve(project_dir: str | Path, port: int = 0, token: str | None = None, directory: str | Path | None = None) -> Monitor:
    project = Path(project_dir).resolve()
    run = Path(directory).resolve() if directory else project / ".dom-memory" / "main"
    secret = token or secrets.token_urlsafe(32)
    handler = type("BoundHandler", (Handler,), {"project_dir": project, "directory": run, "token": secret})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:%d" % server.server_address[1]
    return Monitor(url=url, token=secret, server=server, thread=thread)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DOM read-only observability monitor")
    ap.add_argument("--project", required=True)
    ap.add_argument("--directory", default=None)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--token", default=None)
    ap.add_argument("--print-token", action="store_true")
    args = ap.parse_args(argv)
    monitor = serve(args.project, args.port, args.token, args.directory)
    print(json.dumps({"url": monitor.url, "token": monitor.token if args.print_token else "***", "db_path": str(Path(args.directory or Path(args.project) / ".dom-memory" / "main"))}))
    try:
        monitor.thread.join()
    except KeyboardInterrupt:
        monitor.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
