"""Audit portability and single-module hygiene for the DOM plugin.

    python tools/audit_portability.py [--strict]

Checks are mechanical and cross-platform:

  absolute   hardcoded drive paths in code/tests/tools (docs are reported separately)
  drift      shared files under opencode/ and claude/ that differ
  versions   VERSION, pyproject, package.json and plugin manifest disagree
  cli        no installable entry point (pyproject packages=[] and no scripts)
  caps       capability dictionary is code-only, not a versioned data file
  tests      tests that hardcode an interpreter or an absolute project path

Exit code is 0 unless --strict and a check fails, so the script can run as a report gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CODE_DIRS = ('opencode', 'claude', 'tests', 'tools', 'scripts')
TEXT_SUFFIXES = {'.py', '.js', '.mjs', '.json', '.jsonc', '.ps1', '.sh', '.toml'}
DRIVE = re.compile(r'(?<![\w/])([A-Za-z]):[\\/](?:[^\s"\'<>|]+)')
POSIX_ABS = re.compile(r'(?<![\w.])/(?:home|Users|opt|srv|mnt)/[^\s"\'<>|]+')
DOC_SUFFIXES = {'.md'}


def rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def tracked_files(suffixes=None):
    listed = _git_listed()
    if listed is not None:
        candidates = listed
    else:
        candidates = [path for path in REPO.rglob('*') if path.is_file()]
    for path in candidates:
        parts = set(path.parts)
        if '.git' in parts or '__pycache__' in parts or 'node_modules' in parts:
            continue
        if path.name.endswith(('.zip', '.plugin', '.sqlite3', '.sqlite3-wal', '.sqlite3-shm')):
            continue
        if suffixes and path.suffix.lower() not in suffixes:
            continue
        yield path


def _git_listed():
    try:
        import subprocess
        result = subprocess.run(['git', '-C', str(REPO), 'ls-files'], capture_output=True, text=True, check=True)
        return [REPO / line for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return None


def check_absolute():
    code_hits, doc_hits = [], []
    for path in tracked_files(TEXT_SUFFIXES | DOC_SUFFIXES):
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if not (DRIVE.search(line) or POSIX_ABS.search(line)):
                continue
            row = '%s:%d: %s' % (rel(path), number, line.strip()[:110])
            (doc_hits if path.suffix.lower() in DOC_SUFFIXES else code_hits).append(row)
    return code_hits, doc_hits


def check_drift():
    out, only = [], []
    left = REPO / 'opencode'
    right = REPO / 'claude'
    if not left.is_dir() or not right.is_dir():
        return ['missing opencode/ or claude/ tree'], only
    for path in left.rglob('*'):
        if not path.is_file() or path.suffix.lower() not in {'.py', '.js', '.mjs'}:
            continue
        other = right / path.relative_to(left)
        if not other.exists():
            only.append(rel(path))
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != hashlib.sha256(other.read_bytes()).hexdigest():
            out.append('differs: ' + rel(path))
    return out, only


def _version(value):
    return str(value or '').strip()


def check_versions():
    found = {}
    if (REPO / 'VERSION').exists():
        found['VERSION'] = _version((REPO / 'VERSION').read_text(encoding='utf-8'))
    pyproject = REPO / 'pyproject.toml'
    if pyproject.exists():
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding='utf-8'), re.M)
        if match:
            found['pyproject'] = match.group(1)
    package = REPO / 'opencode' / 'package.json'
    if package.exists():
        found['opencode/package.json'] = _version(json.loads(package.read_text(encoding='utf-8')).get('version'))
    manifest = REPO / 'claude' / '.claude-plugin' / 'plugin.json'
    if manifest.exists():
        found['claude/plugin.json'] = _version(json.loads(manifest.read_text(encoding='utf-8')).get('version'))
    distinct = {value for value in found.values() if value}
    if len(distinct) > 1:
        return ['version mismatch: ' + ', '.join('%s=%s' % item for item in sorted(found.items()))], found
    return [], found


def check_cli():
    pyproject = REPO / 'pyproject.toml'
    if not pyproject.exists():
        return ['no pyproject.toml']
    text = pyproject.read_text(encoding='utf-8')
    packages = re.search(r'^packages\s*=\s*\[([^\]]*)\]', text, re.M)
    scripts = re.search(r'^\[project\.scripts\]', text, re.M)
    problems = []
    if packages and not packages.group(1).strip():
        problems.append('pyproject packages=[] — nothing is installable')
    if not scripts:
        problems.append('no [project.scripts] entry point (e.g. dom = ...)')
    return problems


def check_capabilities():
    candidates = [
        REPO / 'opencode' / 'mockup' / 'capabilities.json',
        REPO / 'opencode' / 'capabilities.json',
        REPO / 'shared' / 'capabilities.json',
    ]
    if any(path.exists() for path in candidates):
        return []
    source = REPO / 'opencode' / 'mockup' / 'skill_router.py'
    if source.exists() and 'CAPABILITIES = (' in source.read_text(encoding='utf-8'):
        return ['capability dictionary lives only in skill_router.py (no versioned data file)']
    return []


def check_tests():
    out = []
    for path in (REPO / 'tests').rglob('*'):
        if not path.is_file() or path.suffix.lower() not in {'.py', '.mjs', '.js'}:
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        for number, line in enumerate(text.splitlines(), 1):
            if DRIVE.search(line) and ('python' in line.lower() or 'PY' in line):
                out.append('%s:%d: hardcoded interpreter/path: %s' % (rel(path), number, line.strip()[:100]))
            elif POSIX_ABS.search(line):
                out.append('%s:%d: absolute project path: %s' % (rel(path), number, line.strip()[:100]))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--strict', action='store_true', help='exit 1 when a check fails')
    args = parser.parse_args()

    code_absolute, doc_absolute = check_absolute()
    drift, opencode_only = check_drift()
    version_problems, versions = check_versions()
    cli_problems = check_cli()
    caps_problems = check_capabilities()
    test_problems = check_tests()

    print('# portability audit')
    print('repo:', REPO)
    print()
    print('versions:', ', '.join('%s=%s' % item for item in sorted(versions.items())) or 'none')

    sections = [
        ('absolute paths in code', code_absolute),
        ('shared tree drift', drift),
        ('version consistency', version_problems),
        ('cli entry point', cli_problems),
        ('capability data', caps_problems),
        ('tests portability', test_problems),
    ]
    failed = 0
    for title, rows in sections:
        print('\n%s: %s' % (title, 'PASS' if not rows else 'FAIL'))
        for row in rows[:20]:
            print('  -', row)
        if len(rows) > 20:
            print('  ... %d more' % (len(rows) - 20))
        if rows:
            failed += 1

    if doc_absolute:
        print('\ndocs mention absolute paths (informational): %d' % len(doc_absolute))
        for row in doc_absolute[:5]:
            print('  -', row)

    if opencode_only:
        print('\nopencode-only files (expected host-specific): %d' % len(opencode_only))
        for name in opencode_only[:8]:
            print('  -', name)

    print('\nchecks failed: %d of %d' % (failed, len(sections)))
    if args.strict and failed:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
