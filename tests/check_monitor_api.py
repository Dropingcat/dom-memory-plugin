"""Check the read-only monitor API: health, claims, hooks, snapshots, security."""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))

from mockup.admission import Service  # noqa: E402
from monitor.server import serve  # noqa: E402


def get_json(url: str, token: str, path: str):
    request = Request(url + path, headers={"Authorization": "Bearer " + token})
    with urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8")), dict(response.headers)


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        project = Path(tmp)
        run_dir = project / ".dom-memory" / "main"
        run_dir.mkdir(parents=True, exist_ok=True)
        previous = os.environ.get("DOM_RUNTIME_ROOT")
        os.environ["DOM_RUNTIME_ROOT"] = str(project / ".dom-memory")
        try:
            svc = Service(run_dir, profile="lite", review_mode="auto")
            svc.start(request="monitor api test")
            claim = svc.claim("main", "text", "monitor claim")
            claim_id = claim["claim_id"]
            updated = svc.update(claim_id, "monitor claim v2", "monitor api version test")
            claim_id = updated["new_claim_id"]
            svc.annotate(claim_id, "property", name="note", value="hello")
        finally:
            if previous is None:
                os.environ.pop("DOM_RUNTIME_ROOT", None)
            else:
                os.environ["DOM_RUNTIME_ROOT"] = previous

        obs = project / ".dom-memory" / "observability"
        obs.mkdir(parents=True, exist_ok=True)
        (obs / "hooks-2026-11-23-1234.jsonl").write_text(
            json.dumps({"ts": "2026-11-23T12:00:00Z", "hook": "prompt", "status": "ok", "session_id": "s1"}) + "\n",
            encoding="utf-8",
        )
        snap_dir = obs / "prompts" / "s1"
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "snap.json").write_text(json.dumps({"schema": "dom.prompt-snapshot/2", "session_id": "s1"}), encoding="utf-8")

        monitor = serve(project, port=0, token="t")
        try:
            status, health, headers = get_json(monitor.url, "t", "/api/health")
            assert status == 200 and health["chain_status"] == "CHAIN_OK", health
            assert Path(health["db_path"]).is_absolute()
            assert health["counts"]["claims"] >= 1, health

            status, claims, _ = get_json(monitor.url, "t", "/api/claims?query=monitor")
            assert status == 200 and claims["total"] >= 1, claims
            assert claims["items"][0]["preview"].startswith("monitor"), claims

            status, detail, _ = get_json(monitor.url, "t", "/api/claim/" + claim_id)
            assert status == 200 and detail["claim_id"] == claim_id, detail
            assert detail["version"] >= 2, detail
            assert len(detail["versions"]) >= 2, detail
            assert any(p["name"] == "note" for p in detail["properties"]), detail

            status, plan_tree, _ = get_json(monitor.url, "t", "/api/plan/tree")
            assert status == 200 and plan_tree["status"] == "OK" and "tasks" in plan_tree, plan_tree

            status, hooks, _ = get_json(monitor.url, "t", "/api/hooks")
            assert status == 200 and hooks["items"][0]["hook"] == "prompt", hooks

            status, snaps, _ = get_json(monitor.url, "t", "/api/snapshots?session=s1")
            assert status == 200 and snaps["items"], snaps
            status, snapshot, _ = get_json(monitor.url, "t", "/api/snapshot/s1/snap.json")
            assert status == 200 and snapshot["schema"] == "dom.prompt-snapshot/2", snapshot

            # no token
            try:
                urlopen(monitor.url + "/api/health", timeout=5)
                raise AssertionError("expected 401")
            except HTTPError as exc:
                assert exc.code == 401, exc

            # wrong token
            try:
                urlopen(Request(monitor.url + "/api/health", headers={"Authorization": "Bearer wrong"}), timeout=5)
                raise AssertionError("expected 401")
            except HTTPError as exc:
                assert exc.code == 401, exc

            # Host check
            host, port = monitor.url.replace("http://", "").split(":")
            conn = http.client.HTTPConnection(host, int(port), timeout=5)
            conn.request("GET", "/api/health", headers={"Host": "evil.example", "Authorization": "Bearer t"})
            response = conn.getresponse()
            assert response.status == 403, response.status
            conn.close()

            # no CORS
            conn = http.client.HTTPConnection(host, int(port), timeout=5)
            conn.request("OPTIONS", "/api/health", headers={"Host": host, "Origin": "https://evil.example"})
            response = conn.getresponse()
            assert response.status == 405, response.status
            assert response.getheader("Access-Control-Allow-Origin") is None
            conn.close()

            # traversal
            try:
                get_json(monitor.url, "t", "/api/snapshot/..%2F..%2Fsecret.json")
                raise AssertionError("expected 404/400")
            except HTTPError as exc:
                assert exc.code in (400, 404), exc

            assert headers.get("Content-Security-Policy")
        finally:
            monitor.close()

        # truncated DB
        broken = Path(tmp) / "broken"
        (broken / ".dom-memory" / "main").mkdir(parents=True, exist_ok=True)
        (broken / ".dom-memory" / "main" / "dom.sqlite3").write_bytes(b"not a sqlite database")
        monitor = serve(broken, port=0, token="t")
        try:
            status, health, _ = get_json(monitor.url, "t", "/api/health")
            assert status == 200 and health["chain_status"] == "STORAGE_CORRUPT", health
        finally:
            monitor.close()

    print("PASS monitor_api: health, claims, detail, hooks, snapshots, auth, host, cors, traversal, corrupt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
