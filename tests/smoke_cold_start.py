"""Self-check without a host: the first task of a fresh project must
create the project memory and record it automatically.

Runs the two hook programs exactly as a host runs them (plain-text prompt on
stdin for plan_check_hook, a plan payload on stdin for plan_hook --now) against
a throw-away project directory, then inspects the SQLite ledger. The same check
runs against BOTH host trees (claude/ and opencode/), which share the engine.

    python tests/smoke_cold_start.py

Requires a Python interpreter with ``PyYAML`` (only the hook programs are used;
the ``mcp`` package is not needed for this check).
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
TREES = ('claude', 'opencode')


def run(tree, script, args, stdin, cwd, env):
    return subprocess.run([PY, '-B', str(REPO / tree / 'hooks' / script), *args], input=stdin,
                          capture_output=True, text=True, encoding='utf8', env=env, cwd=str(cwd),
                          timeout=120)


def check(tree):
    scratch = Path(tempfile.mkdtemp(prefix='dom-smoke-%s-' % tree))
    project = scratch / 'project'
    project.mkdir()
    env = dict(os.environ)
    env.update({'OPENCODE_PROJECT_DIR': str(project), 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
    env.pop('DOM_RUNTIME_ROOT', None)
    env.pop('DOM_DIRECTORY', None)
    dom = project / '.dom-memory' / 'main'

    # 1) first prompt: memory is created and the request recorded automatically.
    r = run(tree, 'plan_check_hook.py', [], 'первая задача: проверка автосоздания', project, env)
    if r.returncode != 0 or not (dom / 'dom.sqlite3').exists():
        print('FAIL[%s]: memory not created on first task\n  rc=%s out=%r err=%r' % (tree, r.returncode, r.stdout, r.stderr))
        return False
    con = sqlite3.connect(str(dom / 'dom.sqlite3'))
    root = con.execute('SELECT c.content_type FROM project_root p JOIN claims c ON c.id=p.request_claim_id').fetchone()
    if not root or root[0] != 'quote':
        print('FAIL[%s]: root request claim missing' % tree)
        return False

    # 2) a plan is then ingested (not left queued).
    plan = '1. Интеллект-карта\n1.1 Первая подзадача\n2. Вторая задача\n'
    r = run(tree, 'plan_hook.py', ['--now'], plan, project, env)
    state = con.execute('SELECT source_revision FROM plan_projection_state').fetchone()
    if r.returncode != 0 or not state or not state[0].startswith('r'):
        print('FAIL[%s]: plan not ingested\n  rc=%s out=%r err=%r state=%r' % (tree, r.returncode, r.stdout, r.stderr, state))
        return False

    # 3) a second prompt records a duty check (no plan-change request loudness).
    run(tree, 'plan_check_hook.py', [], 'второй промпт', project, env)
    kinds = [k[0] for k in con.execute('SELECT DISTINCT kind FROM plan_checks')]
    print('  %s/: PASS — root claim %s, plan %s, checks=%s' % (tree, root[0], state[0], kinds))
    shutil.rmtree(scratch, ignore_errors=True)
    return True


def main():
    try:
        import yaml  # noqa: F401
    except ImportError:
        print('SKIP: PyYAML is not importable; install it (pip install PyYAML) to run the smoke test.')
        return 3
    results = {tree: check(tree) for tree in TREES}
    if not all(results.values()):
        return 1
    print('PASS: both trees cold-start correctly.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
