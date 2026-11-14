"""Populate / refresh the standalone OpenCode distribution repo from the upstream source.

The upstream checkout (the maintained DOM engine: hooks, mockup package, MCP server,
skill, opencode hook plugin) is the single source of truth.  Run this from the repo root::

    python tools/sync_from_upstream.py --upstream <path to worktree>

It copies exactly the files a host plugin needs (the same set the upstream build uses),
so the repo stays a clean, reviewable mirror that is easy to keep current.
"""
import argparse
import shutil
import sys
from pathlib import Path

SERVER_MODULES = ('__init__.py', 'admission.py', 'claim_entry.py', 'delivery.py', 'dom_contract.py',
                  'llm.py', 'paths.py', 'planning.py', 'ports.py', 'projections.py', 'public_dto.py',
                  'server.py', 'storage.py', 'watcher.py')

REPO = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(prog='sync_from_upstream')
    parser.add_argument('--upstream', required=True, help='upstream checkout (worktree) root')
    args = parser.parse_args(argv)
    up = Path(args.upstream).resolve()
    if not (up / 'mockup' / 'planning.py').is_file():
        print('upstream does not look like a dom-memory checkout: %s' % up, file=sys.stderr)
        return 2

    def copy(src, dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        shutil.copyfile(src, dst)
        print('  %s' % dst.relative_to(REPO))

    print('mockup/')
    (REPO / 'mockup' / 'fixtures').mkdir(parents=True, exist_ok=True)
    for name in SERVER_MODULES:
        copy(up / 'mockup' / name, REPO / 'mockup' / name)
    copy(up / 'mockup' / 'fixtures' / '__init__.py', REPO / 'mockup' / 'fixtures' / '__init__.py')

    print('hooks/')
    for name in ('plan_hook.py', 'plan_check_hook.py'):
        copy(up / 'hooks' / name, REPO / 'hooks' / name)

    print('index.js, scripts/, skills/')
    copy(up / 'plugin' / 'opencode' / 'index.js', REPO / 'index.js')
    copy(up / 'plugin' / 'claude-code' / 'scripts' / 'dom_server.py', REPO / 'scripts' / 'dom_server.py')
    copy(up / 'plugin' / 'claude-code' / 'skills' / 'dom-memory' / 'SKILL.md',
         REPO / 'skills' / 'dom-memory' / 'SKILL.md')

    version = Path(REPO / 'VERSION')
    print('VERSION written:', version.read_text(encoding='utf8').strip() if version.exists() else '(missing)')
    print('done. commit the changes (python tools/build_zip.py emits the distribution zip).')
    return 0


if __name__ == '__main__':
    sys.exit(main())