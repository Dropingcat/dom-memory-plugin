"""Emit the distributable zip (mirrors the Claude Code plugin packaging).

    python tools/build_zip.py

Writes ``dist/opencode-dom-memory-<VERSION>.zip`` with the plugin files at the
archive root (the same flat layout the Claude Code ``.plugin`` zip uses), so a
maintainer can hand out one artifact.
"""
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VERSION = (REPO / 'VERSION').read_text(encoding='utf8').strip()

# Files shipped in the distribution, in the same order/shape the upstream build
# uses. Generated artifacts (tools/, tests/, pyproject.toml, .git) are development
# files and stay out of the zip; the runnables a host loads are enough.
INCLUDE = (
    'README.md',
    'VERSION',
    'package.json',
    'index.js',
    'opencode.json.example',
    'hooks/plan_hook.py',
    'hooks/plan_check_hook.py',
    'scripts/dom_server.py',
    'skills/dom-memory/SKILL.md',
    *(f'mockup/{m}' for m in ('__init__.py', 'admission.py', 'claim_entry.py', 'delivery.py',
                              'dom_contract.py', 'llm.py', 'paths.py', 'planning.py', 'ports.py',
                              'projections.py', 'public_dto.py', 'server.py', 'storage.py', 'watcher.py',
                              'fixtures/__init__.py')),
)


def main():
    dist = REPO / 'dist'
    dist.mkdir(exist_ok=True)
    target = dist / ('opencode-dom-memory-%s.zip' % VERSION)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
        for rel in INCLUDE:
            z.write(REPO / rel, rel)
    print(json.dumps({'zip': str(target), 'version': VERSION,
                      'files': len(INCLUDE), 'bytes': target.stat().st_size}, indent=1))
    print('# at %s' % datetime.now(timezone.utc).isoformat(timespec='seconds'))
    return 0


if __name__ == '__main__':
    sys.exit(main())