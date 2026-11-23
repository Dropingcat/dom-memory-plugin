"""Where the mutable data lives.

Default (repository checkout): ``<repo>/runtime``.  A packaged plugin is copied
into the host's plugin cache, so its data must live elsewhere: the launch
environment sets ``DOM_RUNTIME_ROOT`` (a launch-time fact like ``--directory``,
never a tool argument).  ``plugin_directory`` gives the per-project location a
host plugin uses: ``<project>/.dom-memory/main``.

One resolver is used by every entry point (MCP server, hooks, watcher, monitor).
Precedence:

1. explicit ``--directory`` / ``DOM_DIRECTORY`` (run directory);
2. ``DOM_PROJECT_DIR``;
3. ``OPENCODE_PROJECT_DIR``;
4. ``CLAUDE_PROJECT_DIR``;
5. ``HERMES_PROJECT_DIR``;
6. current directory.

``OPENCODE_PROJECT_DIR`` deliberately wins over ``CLAUDE_PROJECT_DIR``: an
inherited Claude variable must not redirect an OpenCode plugin.  ``db_path`` is
always the realpath, so a junction, symlink or case-variant path is seen as the
same database by the preflight and the monitor.
"""
import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_KEYS = ('DOM_PROJECT_DIR', 'OPENCODE_PROJECT_DIR', 'CLAUDE_PROJECT_DIR', 'HERMES_PROJECT_DIR')


@dataclass(frozen=True)
class Paths:
    project_dir: str
    run_dir: str
    db_path: str


def _resolve(value, cwd):
    path = Path(value)
    if not path.is_absolute():
        path = Path(cwd) / path
    return path.resolve()


def _project_dir(env, cwd):
    for key in PROJECT_KEYS:
        value = env.get(key)
        if value:
            return _resolve(value, cwd)
    return Path(cwd).resolve()


def resolve_paths(explicit=None, environ=None, cwd=None):
    """Return the one project/run/db identity every writer must use."""
    env = os.environ if environ is None else environ
    cwd = Path(cwd or os.getcwd()).resolve()
    project = _project_dir(env, cwd)
    root = env.get('DOM_RUNTIME_ROOT') or project / '.dom-memory'
    root = _resolve(root, cwd)
    run = explicit or env.get('DOM_DIRECTORY') or root / 'main'
    run = _resolve(run, cwd)
    if not run.is_relative_to(root):
        root = run.parent
    db_path = Path(os.path.realpath(run / 'dom.sqlite3'))
    return Paths(str(project), str(run), str(db_path))


def runtime_root():
    override = os.environ.get('DOM_RUNTIME_ROOT')
    if override and Path(override).is_absolute():
        return Path(override).resolve()
    return (REPO_ROOT / 'runtime').resolve()


def plugin_directory(environ=None):
    """Run directory of a host plugin; also pins DOM_RUNTIME_ROOT for this process.

    Kept as the compatibility entry point; every caller now goes through
    ``resolve_paths`` so the MCP server and the hooks cannot disagree.
    """
    paths = resolve_paths(None, os.environ if environ is None else environ, os.getcwd())
    os.environ['DOM_RUNTIME_ROOT'] = str(Path(paths.run_dir).parent)
    return Path(paths.run_dir)
