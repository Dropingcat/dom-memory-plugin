"""Stage 3 self-check (П4.6): the hard improvement chain of a statement distillate.

A captured statement is a source quote plus the deterministic extractive v1
(§12.4).  The agent may improve it only through ``dom_distillate``:
``propose`` -> ``validate`` (V1-V8) -> ``accept``; the effective version is
always recomputed from the ledger (§12.5).  This check runs the same scenario
against BOTH host trees (claude/ and opencode/):

1. a faithful compression becomes a new version, and the effective version
   does not move until it is accepted;
2. the identical retry replays the version (ALREADY_RECORDED, no duplicate);
3. an anchor that is not in the source is SPAN_NOT_FOUND; a quote that occurs
   twice is SPAN_AMBIGUOUS;
4. a proposal equal to the effective text is NO_IMPROVEMENT;
5. the same text with different anchors is PROPOSAL_CONFLICT;
6. a lost ``basis_spans`` property is repaired by the retry, without a
   duplicate version.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PY = sys.executable
REPO = Path(__file__).resolve().parents[1]

SCENARIO = r'''
import json, os, sqlite3, sys
sys.path.insert(0, sys.argv[1])
project = sys.argv[2]
os.environ['OPENCODE_PROJECT_DIR'] = project
os.chdir(project)
from mockup.paths import plugin_directory
from mockup.admission import Service
from mockup import distillate


def bad(message):
    print('FAIL: ' + message)
    raise SystemExit(1)


ROOT = 'Собери память проекта и захват утверждений пользователя.'
STMT = 'Мы фиксируем детерминированный слой. Мы делаем ставку на проверяемость результата.'
COMPRESSED = 'Мы фиксируем детерминированный слой и делаем ставку на проверяемость результата.'
ANCHORS = [['Мы фиксируем детерминированный слой.', 'Мы делаем ставку на проверяемость результата.']]

svc = Service(plugin_directory(), profile='lite', role='owner')
svc.start(request=ROOT)
cap = distillate.capture(svc, STMT, 's1')
if cap.get('status') != 'STATEMENT_RECORDED':
    bad('capture: %r' % cap)
sid, vid = cap['source_id'], cap['distillate_id']

show = svc.distillate('show', source=sid)
if show.get('status') != 'DISTILLATE_SHOWN':
    bad('show: %r' % show)
v1 = show['versions'][0]
if v1['claim_id'] != vid or v1['state'] != 'extracted':
    bad('v1 must be the extracted root: %r' % v1)
if 'Мы фиксируем' not in v1['text'] or 'проверяемость' not in v1['text']:
    bad('the statement must capture both sentences: %r' % v1['text'])

# 1. a faithful compression is a new version; the effective one does not move
r1 = svc.distillate('propose', source=sid, text=COMPRESSED, anchors=ANCHORS)
if r1.get('status') != 'PROPOSED':
    bad('propose: %r' % r1)
v2 = r1['versions'][-1]['claim_id']
if len(r1['versions']) != 2 or r1['versions'][-1]['state'] != 'proposed':
    bad('propose versions: %r' % r1['versions'])
if (r1.get('effective') or {}).get('claim_id') != vid:
    bad('the proposal must not displace the v1: %r' % r1.get('effective'))

# 2. the identical retry replays the version
r2 = svc.distillate('propose', source=sid, text=COMPRESSED, anchors=ANCHORS)
if r2.get('status') != 'ALREADY_RECORDED' or len(r2['versions']) != 2:
    bad('retry: %r' % r2)

# 3. anchors must be verbatim and unique
r3 = svc.distillate('propose', source=sid, text='Проверяемость результата обеспечена.',
                    anchors=[['Этого нет в источнике.']])
if r3.get('status') != 'SPAN_NOT_FOUND' or not r3.get('hint'):
    bad('missing span: %r' % r3)
r4 = svc.distillate('propose', source=sid, text='Проверяемость результата обеспечена.',
                    anchors=[['Мы ']])
if r4.get('status') != 'SPAN_AMBIGUOUS':
    bad('ambiguous span: %r' % r4)

# 4. the effective text cannot be proposed as an improvement
r5 = svc.distillate('propose', source=sid, text=v1['text'], anchors=ANCHORS)
if r5.get('status') != 'NO_IMPROVEMENT':
    bad('no improvement: %r' % r5)

# 5. same text, other anchors is a conflict, not a new version
r6 = svc.distillate('propose', source=sid, text=COMPRESSED,
                    anchors=[['Мы фиксируем детерминированный слой.']])
if r6.get('status') != 'PROPOSAL_CONFLICT':
    bad('proposal conflict: %r' % r6)

# 6. drift drill: the v2 anchors property is lost; the retry repairs it
db = sqlite3.connect(os.path.join(project, '.dom-memory', 'main', 'dom.sqlite3'))
lost = db.execute("SELECT seq FROM events WHERE kind='property' AND claim_ref=? ORDER BY seq DESC LIMIT 1",
                  (v2,)).fetchone()
if lost is None:
    bad('the v2 anchors property is missing before the drill')
if db.execute('SELECT COUNT(*) FROM events WHERE seq>?', (lost[0],)).fetchone()[0]:
    bad('the drill must remove the last event only')
db.execute('DROP TRIGGER IF EXISTS immutable_events_DELETE')
db.execute('DELETE FROM events WHERE seq=?', (lost[0],))
db.execute("CREATE TRIGGER IF NOT EXISTS immutable_events_DELETE BEFORE DELETE ON events "
           "BEGIN SELECT RAISE(ABORT,'IMMUTABLE'); END")
db.commit()
db.close()
r7 = svc.distillate('propose', source=sid, text=COMPRESSED, anchors=ANCHORS)
if r7.get('status') != 'ALREADY_RECORDED' or not r7.get('repaired'):
    bad('repair: %r' % r7)
if len(r7['versions']) != 2 or r7['versions'][-1]['origin'] != 'agent':
    bad('repair must not duplicate the version: %r' % r7['versions'])
print('PASS')
'''


def check(tree):
    scratch = Path(tempfile.mkdtemp(prefix='dom-pipe-%s-' % tree))
    try:
        project = scratch / 'project'
        project.mkdir()
        env = dict(os.environ)
        env.update({'OPENCODE_PROJECT_DIR': str(project), 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
        env.pop('DOM_RUNTIME_ROOT', None)
        env.pop('DOM_DIRECTORY', None)
        env.pop('DOM_SESSION', None)
        run = subprocess.run([PY, '-B', '-c', SCENARIO, str(REPO / tree), str(project)],
                             capture_output=True, text=True, encoding='utf8', errors='replace',
                             env=env, cwd=str(project), timeout=180)
        if run.returncode != 0 or 'PASS' not in run.stdout:
            print('FAIL[%s]: %s' % (tree, (run.stdout + run.stderr)[-600:]))
            return False
        print('  %s/: PASS — propose is a new version, the v1 stays effective, retries replay, '
              'anchors and conflicts are refused, a lost anchors property is repaired' % tree)
        return True
    finally:
        for path in sorted(scratch.rglob('*'), reverse=True):
            try:
                path.unlink() if path.is_file() else path.rmdir()
            except OSError:
                pass
        try:
            scratch.rmdir()
        except OSError:
            pass


def main():
    ok = all([check(tree) for tree in ('claude', 'opencode')])
    print('PASS: the distillate chain is identical in both trees' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
