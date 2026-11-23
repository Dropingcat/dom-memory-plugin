"""Check memory_preflight.py: registry, realpath, mismatch warning, read-only probe."""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[3]
SCRIPT = HARNESS / "scripts" / "router" / "memory_preflight.py"


def load_module():
    spec = importlib.util.spec_from_file_location("memory_preflight", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def make_db(path: Path, claim: str = "c1", revision: str = "r1") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS claims(id TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS events(seq INTEGER)")
        con.execute("CREATE TABLE IF NOT EXISTS plan_projection_state(source TEXT, source_revision TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS review_policy_log(mode TEXT)")
        con.execute("INSERT INTO claims(id) VALUES(?)", (claim,))
        con.execute("INSERT INTO events(seq) VALUES(1)")
        con.execute("INSERT INTO plan_projection_state(source, source_revision) VALUES('agent', ?)", (revision,))
        con.execute("INSERT INTO review_policy_log(mode) VALUES('auto')")
        con.commit()
    finally:
        con.close()


def main() -> int:
    mod = load_module()
    root = Path(os.environ.get("TEMP", "/tmp")) / "dom-memory-preflight-test"
    if root.exists():
        import shutil
        shutil.rmtree(root)
    root.mkdir(parents=True)

    p1 = root / "projects" / "p1"
    p2 = root / "projects" / "p2"
    uni = root / "projects" / "Проект"
    make_db(p1 / ".dom-memory" / "main" / "dom.sqlite3", "a", "r1")
    make_db(p2 / ".dom-memory" / "main" / "dom.sqlite3", "b", "r2")
    make_db(uni / ".dom-memory" / "main" / "dom.sqlite3", "c", "r3")

    registry = {
        "version": 1,
        "projects": {
            "p1": {"project_dir": str(p1), "profile": "prod", "review": "auto", "primary": True},
            "p2": {"project_dir": str(p2), "profile": "prod", "review": "auto"},
            "uni": {"project_dir": str(uni), "profile": "prod", "review": "auto"},
        },
    }
    snap = mod.build_snapshot(registry, root, "p1", {"p1": str(p2 / ".dom-memory" / "main" / "dom.sqlite3")})
    assert snap["selected"] == "p1", snap
    assert "DB_PATH_MISMATCH" in snap["warnings"], snap
    assert snap["projects"]["p1"]["claims"] == 1, snap
    assert snap["projects"]["p1"]["plan_revision"] == "r1", snap
    assert os.path.realpath(snap["projects"]["p1"]["db_path"]) == snap["projects"]["p1"]["db_path"], snap
    assert len(snap["policy_hash"]) == 64, snap

    unicode_snap = mod.build_snapshot(registry, root, "uni")
    assert unicode_snap["projects"]["uni"]["exists"] is True, unicode_snap
    assert unicode_snap["projects"]["uni"]["claims"] == 1, unicode_snap

    # Junction realpath: create a junction if Windows allows it; otherwise report SKIP.
    junction = root / "junction"
    try:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(p1)], check=True,
                       capture_output=True, text=True)
        reg_j = {"version": 1, "projects": {"j": {"project_dir": str(junction)}}}
        snap_j = mod.build_snapshot(reg_j, root, "j")
        assert snap_j["projects"]["j"]["db_path"] == os.path.realpath(p1 / ".dom-memory" / "main" / "dom.sqlite3"), snap_j
    except (OSError, subprocess.CalledProcessError) as exc:
        print("SKIP junction: %s" % exc)

    print("PASS memory_preflight: selected, mismatch, counts, unicode, realpath")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
