"""Emit the distributable zips for BOTH host versions from the repo trees.

    python tools/build_zip.py

Writes:

    dist/claude/dom-memory.plugin                 Claude Code plugin archive
                                                 (`.claude-plugin/plugin.json` first, same layout the
                                                 desktop app takes for a custom plugin)
    dist/opencode/opencode-dom-memory-<VERSION>.zip
                                                 OpenCode plugin distribution (flat tree at the archive
                                                 root, unzip anywhere and point file:///… at it)

The trees under claude/ and opencode/ are the source: regenerate them with
tools/sync_from_upstream.py, then run this.
"""
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VERSION = (REPO / 'VERSION').read_text(encoding='utf8').strip()


def _walk_ordered(tree):
    """Files in the tree, .claude-plugin/plugin.json first when present, else sorted."""
    tree = Path(tree)
    files = [f for f in tree.rglob('*') if f.is_file() and '__pycache__' not in f.parts]
    key = lambda f: (f.relative_to(tree).as_posix() == '.claude-plugin/plugin.json' and '.claude-plugin' in f.parts,
                     f.relative_to(tree).as_posix())
    return sorted(files, key=key)


def _zip(tree, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in _walk_ordered(tree):
            z.write(f, f.relative_to(tree).as_posix())
    return target.stat().st_size


def main():
    reports = {}
    for tree, name in (('claude', 'dom-memory.plugin'),
                       ('opencode', 'opencode-dom-memory-%s.zip' % VERSION)):
        target = REPO / 'dist' / tree / name
        reports[tree] = {'zip': str(target), 'bytes': _zip(REPO / tree, target),
                         'files': len(_walk_ordered(REPO / tree))}
    print(json.dumps({'version': VERSION, 'zips': reports,
                      'at': datetime.now(timezone.utc).isoformat(timespec='seconds')}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
