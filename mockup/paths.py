"""Where the mutable data lives.

Default (repository checkout): ``<repo>/runtime``.  A packaged plugin is copied
into the host's plugin cache, so its data must live elsewhere: the launch
environment sets ``DOM_RUNTIME_ROOT`` (a launch-time fact like ``--directory``,
never a tool argument).  ``plugin_directory`` gives the per-project location a
host plugin uses: ``<project>/.dom-memory/main``.
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def runtime_root():
    override = os.environ.get('DOM_RUNTIME_ROOT')
    if override and Path(override).is_absolute():
        return Path(override).resolve()
    return (REPO_ROOT / 'runtime').resolve()


def plugin_directory(environ=None):
    """Run directory of a host plugin; also pins DOM_RUNTIME_ROOT for this process.

    Order: DOM_DIRECTORY (explicit) > DOM_RUNTIME_ROOT/main > <project>/.dom-memory/main,
    where <project> is CLAUDE_PROJECT_DIR (Claude Code), OPENCODE_PROJECT_DIR,
    HERMES_PROJECT_DIR or the current directory.
    """
    env = os.environ if environ is None else environ
    project = next((env[k] for k in ('CLAUDE_PROJECT_DIR', 'OPENCODE_PROJECT_DIR', 'HERMES_PROJECT_DIR') if env.get(k)), os.getcwd())
    root = Path(env.get('DOM_RUNTIME_ROOT') or Path(project) / '.dom-memory').resolve()
    directory = Path(env.get('DOM_DIRECTORY') or root / 'main').resolve()
    if not directory.is_relative_to(root):
        root = directory.parent
    os.environ['DOM_RUNTIME_ROOT'] = str(root)
    return directory
