"""Launcher of the DOM project-memory MCP server inside a host plugin.

The plugin lives in the host's plugin cache; the data lives in the project:
``<project>/.dom-memory/main`` (see ``mockup.paths.plugin_directory``).
Review policy ``DOM_REVIEW`` = auto | manual | independent; identity
``DOM_PRINCIPAL`` / ``DOM_ROLE`` (owner | author | reviewer).  stdout carries MCP frames only.
"""
import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))


def main():
    try:
        import mcp  # noqa: F401
    except ImportError:
        print('dom-memory: the "mcp" package is missing for %s. Install it: "%s" -m pip install mcp anyio PyYAML'
              % (sys.executable, sys.executable), file=sys.stderr)
        return 3
    from mockup.paths import plugin_directory
    from mockup import server
    directory = plugin_directory()
    review = os.environ.get('DOM_REVIEW') if os.environ.get('DOM_REVIEW') in ('auto', 'manual', 'independent') else 'auto'
    argv = ['--profile', 'prod', '--directory', str(directory), '--review', review]
    if os.environ.get('DOM_PRINCIPAL'):
        argv += ['--principal', os.environ['DOM_PRINCIPAL']]
    if os.environ.get('DOM_ROLE') in ('owner', 'author', 'reviewer'):
        argv += ['--role', os.environ['DOM_ROLE']]
    return server.main(argv)


if __name__ == '__main__':
    sys.exit(main())
