"""Check the single memory resolver: precedence, realpath, db_path."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))

from mockup.paths import resolve_paths  # noqa: E402


def test_opencode_wins_over_claude(tmp_path):
    env = {"OPENCODE_PROJECT_DIR": str(tmp_path / "oc"), "CLAUDE_PROJECT_DIR": str(tmp_path / "cc")}
    paths = resolve_paths(None, env, tmp_path)
    assert paths.project_dir == os.path.realpath(tmp_path / "oc")


def test_dom_directory_wins_over_project(tmp_path):
    env = {
        "DOM_DIRECTORY": str(tmp_path / "run"),
        "OPENCODE_PROJECT_DIR": str(tmp_path / "project"),
        "CLAUDE_PROJECT_DIR": str(tmp_path / "claude"),
    }
    paths = resolve_paths(None, env, tmp_path)
    assert paths.run_dir == os.path.realpath(tmp_path / "run")
    assert paths.db_path == os.path.realpath(tmp_path / "run" / "dom.sqlite3")


def test_explicit_directory_wins(tmp_path):
    env = {"DOM_DIRECTORY": str(tmp_path / "envrun")}
    paths = resolve_paths(str(tmp_path / "explicit"), env, tmp_path)
    assert paths.run_dir == os.path.realpath(tmp_path / "explicit")


def test_unicode_path_and_realpath(tmp_path):
    project = tmp_path / "Проект"
    paths = resolve_paths(None, {"OPENCODE_PROJECT_DIR": str(project)}, tmp_path)
    assert paths.project_dir == os.path.realpath(project)
    assert paths.db_path.endswith("dom.sqlite3")
    assert paths.db_path == os.path.realpath(paths.db_path)


def test_relative_project_is_resolved(tmp_path):
    env = {"OPENCODE_PROJECT_DIR": "relative-project"}
    paths = resolve_paths(None, env, tmp_path)
    assert paths.project_dir == os.path.realpath(tmp_path / "relative-project")


def test_dom_describe_reports_db_path(tmp_path):
    from mockup.admission import Service
    previous = os.environ.get("DOM_RUNTIME_ROOT")
    os.environ["DOM_RUNTIME_ROOT"] = str(tmp_path)
    try:
        svc = Service(tmp_path, profile="lite", review_mode="auto")
        svc.start(request="test")
        out = svc.describe()
        from mockup.public_dto import project_tool
        public = project_tool("dom_describe", out, profile="lite")
    finally:
        if previous is None:
            os.environ.pop("DOM_RUNTIME_ROOT", None)
        else:
            os.environ["DOM_RUNTIME_ROOT"] = previous
    assert out["db_path"] == os.path.realpath(str(svc.store.path))
    assert out["directory"] == str(svc.store.path.parent)
    assert out["profile"] == "lite"
    assert public["db_path"] == out["db_path"]
    assert public["directory"] == out["directory"]


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        test_opencode_wins_over_claude(base / "a")
        test_dom_directory_wins_over_project(base / "b")
        test_explicit_directory_wins(base / "c")
        test_unicode_path_and_realpath(base / "d")
        test_relative_project_is_resolved(base / "e")
        test_dom_describe_reports_db_path(base / "f")
    print("PASS memory_paths: precedence, explicit, unicode, relative, realpath, describe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
