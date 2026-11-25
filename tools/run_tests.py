"""Run the whole verification suite with one portable contract.

    python tools/run_tests.py [--skip-node] [--filter substring]

Python: every tests/check_*.py, probe_*.py and smoke_*.py, using the interpreter that runs this
script. Node tests run with `node --test` and inherit DOM_PYTHON from sys.executable, so a new
machine only needs a working python and node on PATH.

Exit code 1 on the first failure (the suite is ordered; later failures are usually consequences).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / 'tests'


def python_tests():
    rows = []
    for pattern in ('check_*.py', 'probe_*.py', 'smoke_*.py'):
        rows.extend(sorted(TESTS.glob(pattern)))
    return rows


def node_tests():
    return sorted(TESTS.glob('*.mjs'))


def run(command, env):
    print('$', ' '.join(str(part) for part in command))
    result = subprocess.run(command, cwd=str(REPO), env=env)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-node', action='store_true')
    parser.add_argument('--filter', default='')
    args = parser.parse_args()

    env = dict(os.environ)
    env.setdefault('DOM_PYTHON', sys.executable)
    env.setdefault('PYTHONIOENCODING', 'utf-8')
    env.setdefault('PYTHONUTF8', '1')

    failures = 0
    total = 0
    for path in python_tests():
        if args.filter and args.filter not in path.name:
            continue
        total += 1
        if run([sys.executable, '-B', str(path)], env):
            failures += 1
            print('FAIL', path.name)
            break

    if not args.skip_node:
        files = [path for path in node_tests() if not args.filter or args.filter in path.name]
        if files:
            total += 1
            if run(['node', '--test', *[str(path) for path in files]], env):
                failures += 1
                print('FAIL node tests')

    print('\n%s: %d groups, %d failed' % ('FAIL' if failures else 'PASS', total, failures))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
