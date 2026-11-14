"""Populate / refresh the repo trees for BOTH host versions from the upstream source.

The upstream checkout (the maintained DOM engine: mockup package, hook programs,
MCP server launcher, skill, opencode hook plugin) is the single source of truth.
Run this from the repo root::

    python tools/sync_from_upstream.py --upstream <path to worktree>

It fills the two self-contained host trees:

    claude/    Claude Code plugin (mockup/ + hooks/*.py + scripts/ + skills/ + opencode-plugin/index.js)
    opencode/  OpenCode plugin    (same engine files + the hook plugin entry opencode/index.js)

Host manifests (.claude-plugin/plugin.json, .mcp.json, hooks/hooks.json,
package.json, opencode.json.example) stay in the repo and are written portably
(${DOM_PYTHON:-python}), so a fresh clone works on any machine; the engine files
are refreshed in both trees so the versions never diverge.
"""
import argparse
import shutil
import sys
from pathlib import Path

SERVER_MODULES = ('__init__.py', 'admission.py', 'claim_entry.py', 'delivery.py', 'dom_contract.py',
                  'llm.py', 'paths.py', 'planning.py', 'ports.py', 'projections.py', 'public_dto.py',
                  'server.py', 'storage.py', 'watcher.py')

REPO = Path(__file__).resolve().parents[1]


def copy(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.copyfile(src, dst)
    print('  %s' % dst.relative_to(REPO).as_posix())


def sync_engine(up, tree):
    print('mockup/ -> %s/mockup' % tree)
    (REPO / tree / 'mockup' / 'fixtures').mkdir(parents=True, exist_ok=True)
    for name in SERVER_MODULES:
        copy(up / 'mockup' / name, REPO / tree / 'mockup' / name)
    copy(up / 'mockup' / 'fixtures' / '__init__.py', REPO / tree / 'mockup' / 'fixtures' / '__init__.py')

    print('hooks/*.py -> %s/hooks' % tree)
    for name in ('plan_hook.py', 'plan_check_hook.py'):
        copy(up / 'hooks' / name, REPO / tree / 'hooks' / name)

    print('scripts/, skills/ -> %s' % tree)
    copy(up / 'plugin' / 'claude-code' / 'scripts' / 'dom_server.py', REPO / tree / 'scripts' / 'dom_server.py')
    copy(up / 'plugin' / 'claude-code' / 'skills' / 'dom-memory' / 'SKILL.md',
         REPO / tree / 'skills' / 'dom-memory' / 'SKILL.md')


def sync_claude(up):
    print('== claude/ ==')
    sync_engine(up, 'claude')
    # The OpenCode hook plugin is host-independent: shipped inside the claude tree too,
    # so a host pointing at either build can load the same hooks (mirrors upstream build).
    copy(up / 'plugin' / 'opencode' / 'index.js', REPO / 'claude' / 'opencode-plugin' / 'index.js')


def sync_opencode(up):
    print('== opencode/ ==')
    sync_engine(up, 'opencode')
    copy(up / 'plugin' / 'opencode' / 'index.js', REPO / 'opencode' / 'index.js')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='sync_from_upstream')
    parser.add_argument('--upstream', required=True, help='upstream checkout (worktree) root')
    args = parser.parse_args(argv)
    up = Path(args.upstream).resolve()
    if not (up / 'mockup' / 'planning.py').is_file() or not (up / 'plugin' / 'opencode' / 'index.js').is_file():
        print('upstream does not look like a dom-memory checkout: %s' % up, file=sys.stderr)
        return 2
    sync_claude(up)
    sync_opencode(up)
    # The MIT license must ship inside each tree so the built zips carry it.
    for tree in ('claude', 'opencode'):
        copy(REPO / 'LICENSE', REPO / tree / 'LICENSE')
    version = (REPO / 'VERSION').read_text(encoding='utf8').strip()
    print('done. VERSION=%s — commit and rebuild zips (python tools/build_zip.py).' % version)
    return 0


if __name__ == '__main__':
    sys.exit(main())
