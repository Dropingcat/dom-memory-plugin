"""Run every check_*.py and report a summary instead of stopping on the first failure.

    python tools/run_all_checks.py [--skip-node] [--filter substring] [--quiet]

``tools/run_tests.py`` is the ordered gate (it stops at the first failure).  This
script is the breadth view used before a release: it executes all Python checks
(and the Node ``*.mjs`` checks unless ``--skip-node``) and prints PASS/FAIL per file
plus totals, so one unrelated break does not hide the rest.

Exit code 1 if any check failed.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / 'tests'


def python_checks():
    return sorted(TESTS.glob('check_*.py'))


def node_checks():
    return sorted(TESTS.glob('*.mjs'))


def run(command, env, quiet=False):
    started = time.perf_counter()
    result = subprocess.run(command, cwd=str(REPO), env=env,
                            stdout=subprocess.DEVNULL if quiet else None)
    return result.returncode, (time.perf_counter() - started) * 1000.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-node', action='store_true')
    parser.add_argument('--filter', default='')
    parser.add_argument('--quiet', action='store_true', help='show the summary only')
    args = parser.parse_args()

    env = dict(os.environ)
    env.setdefault('DOM_PYTHON', sys.executable)
    env.setdefault('PYTHONIOENCODING', 'utf-8')
    env.setdefault('PYTHONUTF8', '1')

    rows = []
    for path in python_checks():
        if args.filter and args.filter not in path.name:
            continue
        code, elapsed = run([sys.executable, '-B', str(path)], env, quiet=args.quiet)
        rows.append((path.name, code, elapsed))

    if not args.skip_node:
        files = [path for path in node_checks() if not args.filter or args.filter in path.name]
        if files:
            code, elapsed = run(['node', '--test', *[str(path) for path in files]], env,
                                quiet=args.quiet)
            rows.append(('node --test ' + ', '.join(path.name for path in files), code, elapsed))

    if args.quiet:
        for name, code, elapsed in rows:
            print('%-6s %7.1f ms  %s' % ('PASS' if code == 0 else 'FAIL', elapsed, name))

    failed = [row for row in rows if row[1] != 0]
    total = len(rows)
    print('\n%s: %d checks, %d passed, %d failed (%.1f s)'
          % ('FAIL' if failed else 'PASS', total, total - len(failed), len(failed),
             sum(row[2] for row in rows) / 1000.0))
    for name, _code, _elapsed in failed:
        print('FAILED', name)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
