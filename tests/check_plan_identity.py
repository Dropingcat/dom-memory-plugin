"""Plan identity self-check (docs/plan-identity.md): the root item is the plan.

A new plan must create new claims, not update the claims of the old plan.  The
same plan may rename items.  This check runs three scenarios against BOTH host
trees (claude/ and opencode/), each in a throw-away project:

A. new plan: every id of plan B is fresh, every item of plan A is archived, no
   header claim of A is reused or replaced;
B. revision: with the root unchanged, a renamed item keeps its id and becomes a
   new version of its header claim;
C. partial mode: adding an item does not touch the existing ones.

    python tests/check_plan_identity.py

Requires a Python interpreter with ``PyYAML`` and the engine importable (the
same interpreter runs the MCP server).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
TREES = ('claude', 'opencode')

PLAN_A = """1. План A
1.1 A1
1.2 A2
2. A3
"""
PLAN_B = """1. План B
1.1 B1
1.2 B2
2. B3
"""
PLAN_A_RENAMED = """1. План A
1.1 A1 renamed
1.2 A2
2. A3
"""
PLAN_PARTIAL = """3. Новый пункт
"""

SCENARIO = r'''
import json
import sqlite3
import sys

sys.path.insert(0, sys.argv[1])
mode = sys.argv[2]
from mockup.admission import Service
from mockup.paths import plugin_directory

svc = Service(plugin_directory(), profile='lite')
svc.start(request='plan identity check')
directory = plugin_directory()

A = %r
B = %r
A_RENAMED = %r
PARTIAL = %r


def rows(result):
    return {row['path']: {'id': row['plan_task_id'], 'header': row['header_claim_id'],
                          'action': row['action'], 'previous': row.get('previous')} for row in result['tasks']}


def db():
    con = sqlite3.connect(str(directory / 'dom.sqlite3'))
    con.row_factory = sqlite3.Row
    return con


out = {}
if mode == 'new_plan':
    a = rows(svc.plan_ingest(A, 'agent'))
    b = rows(svc.plan_ingest(B, 'agent'))
    con = db()
    archived = sorted(r['path'] for r in con.execute(
        "SELECT v.path FROM current_plan_tasks v JOIN plan_tasks t ON t.plan_task_id=v.plan_task_id "
        "WHERE v.archived=1 AND t.source='agent'"))
    replaced = {r['claim_ref'] for r in con.execute("SELECT claim_ref FROM events WHERE kind='replacement'")}
    con.close()
    out = {'a': a, 'b': b, 'archived': archived,
           'a_headers_replaced': sorted(set(v['header'] for v in a.values()) & replaced)}
elif mode == 'rename':
    a = rows(svc.plan_ingest(A, 'agent'))
    b = rows(svc.plan_ingest(A_RENAMED, 'agent'))
    con = db()
    replaced = {r['claim_ref'] for r in con.execute("SELECT claim_ref FROM events WHERE kind='replacement'")}
    archived = sorted(r['path'] for r in con.execute(
        "SELECT v.path FROM current_plan_tasks v JOIN plan_tasks t ON t.plan_task_id=v.plan_task_id "
        "WHERE v.archived=1 AND t.source='agent'"))
    con.close()
    out = {'a': a, 'b': b, 'archived': archived,
           'a_headers_replaced': sorted(set(v['header'] for v in a.values()) & replaced)}
elif mode == 'partial':
    a = rows(svc.plan_ingest(A, 'agent'))
    b = rows(svc.plan_ingest(PARTIAL, 'agent', mode='partial'))
    con = db()
    archived = sorted(r['path'] for r in con.execute(
        "SELECT v.path FROM current_plan_tasks v JOIN plan_tasks t ON t.plan_task_id=v.plan_task_id "
        "WHERE v.archived=1 AND t.source='agent'"))
    con.close()
    out = {'a': a, 'b': b, 'archived': archived}
else:
    raise SystemExit('unknown mode')
print(json.dumps(out, ensure_ascii=False))
''' % (PLAN_A, PLAN_B, PLAN_A_RENAMED, PLAN_PARTIAL)


def fresh(tree, mode):
    scratch = Path(tempfile.mkdtemp(prefix='dom-identity-%s-%s-' % (tree, mode)))
    project = scratch / 'project'
    project.mkdir()
    env = dict(os.environ)
    env.update({'OPENCODE_PROJECT_DIR': str(project), 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
    env.pop('DOM_RUNTIME_ROOT', None)
    env.pop('DOM_DIRECTORY', None)
    result = subprocess.run([PY, '-B', '-c', SCENARIO, str(REPO / tree), mode], capture_output=True,
                            text=True, encoding='utf8', env=env, cwd=str(project), timeout=180)
    if result.returncode != 0:
        raise RuntimeError('%s/%s failed: %r' % (tree, mode, result.stderr))
    return json.loads(result.stdout.strip().splitlines()[-1]), scratch


def check_new_plan(tree):
    data, scratch = fresh(tree, 'new_plan')
    a, b = data['a'], data['b']
    if set(v['id'] for v in a.values()) & set(v['id'] for v in b.values()):
        print('FAIL[%s/new_plan]: plan B reused a plan_task_id of plan A' % tree)
        return False
    if sorted(data['archived']) != sorted(a):
        print('FAIL[%s/new_plan]: plan A items not all archived: %r (A: %r)' % (tree, data['archived'], sorted(a)))
        return False
    if set(v['header'] for v in a.values()) & set(v['header'] for v in b.values()):
        print('FAIL[%s/new_plan]: plan B reused a header claim of plan A' % tree)
        return False
    if data['a_headers_replaced']:
        print('FAIL[%s/new_plan]: a header claim of plan A was replaced: %r' % (tree, data['a_headers_replaced']))
        return False
    shutil.rmtree(scratch, ignore_errors=True)
    print('  %s/: new plan PASS — %d fresh ids, %d old items archived, no old header touched'
          % (tree, len(b), len(data['archived'])))
    return True


def check_rename(tree):
    data, scratch = fresh(tree, 'rename')
    a, b = data['a'], data['b']
    if set(a) != set(b):
        print('FAIL[%s/rename]: the tree changed shape: %r -> %r' % (tree, sorted(a), sorted(b)))
        return False
    if any(a[path]['id'] != b[path]['id'] for path in a):
        print('FAIL[%s/rename]: a renamed item lost its id: %r' % (tree, {p: (a[p]['id'], b[p]['id']) for p in a}))
        return False
    if b['П1.1']['action'] != 'updated' or not b['П1.1'].get('previous'):
        print('FAIL[%s/rename]: the renamed item is not a new header version: %r' % (tree, b['П1.1']))
        return False
    if b['П1.1']['header'] == a['П1.1']['header']:
        print('FAIL[%s/rename]: the renamed item kept the old header claim' % tree)
        return False
    if a['П1.1']['header'] not in data['a_headers_replaced']:
        print('FAIL[%s/rename]: the old header of the renamed item was not replaced' % tree)
        return False
    if data['archived']:
        print('FAIL[%s/rename]: a revision archived items: %r' % (tree, data['archived']))
        return False
    shutil.rmtree(scratch, ignore_errors=True)
    print('  %s/: revision PASS — ids kept, П1.1 got a new header version' % tree)
    return True


def check_partial(tree):
    data, scratch = fresh(tree, 'partial')
    a, b = data['a'], data['b']
    if list(b) != ['П3']:
        print('FAIL[%s/partial]: expected only П3, got %r' % (tree, sorted(b)))
        return False
    if b['П3']['id'] in set(v['id'] for v in a.values()):
        print('FAIL[%s/partial]: П3 reused an existing id' % tree)
        return False
    if data['archived']:
        print('FAIL[%s/partial]: partial mode archived items: %r' % (tree, data['archived']))
        return False
    shutil.rmtree(scratch, ignore_errors=True)
    print('  %s/: partial PASS — П3 is new, existing items untouched' % tree)
    return True


def main():
    try:
        import yaml  # noqa: F401
    except ImportError:
        print('SKIP: PyYAML is not importable; install it (pip install PyYAML) to run this check.')
        return 3
    try:
        sys.path.insert(0, str(REPO / 'opencode'))
        import mockup.admission  # noqa: F401
    except ImportError:
        print('SKIP: the mockup engine is not importable; run this check with the venv that runs the server.')
        return 3
    results = {}
    for tree in TREES:
        results[(tree, 'new_plan')] = check_new_plan(tree)
        results[(tree, 'rename')] = check_rename(tree)
        results[(tree, 'partial')] = check_partial(tree)
    if not all(results.values()):
        return 1
    print('PASS: both trees keep plan identity: a new root is a new plan, a rename is a revision.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
