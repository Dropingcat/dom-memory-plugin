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
from mockup.dom_contract import raw_hash


def bad(message):
    print('FAIL: ' + message)
    raise SystemExit(1)


def prop_count(claim_id, name):
    db = sqlite3.connect(os.path.join(project, '.dom-memory', 'main', 'dom.sqlite3'))
    try:
        rows = db.execute("SELECT payload FROM events WHERE kind='property' AND claim_ref=? ORDER BY seq",
                          (claim_id,)).fetchall()
    finally:
        db.close()
    count = 0
    for (payload,) in rows:
        try:
            value = json.loads(payload)['value']['value']
        except (ValueError, TypeError, KeyError):
            continue
        if value.get('name') == name:
            count += 1
    return count


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

# 7. validate + accept the v2; retries return the existing records
r8 = svc.distillate('validate', source=sid)
if r8.get('status') != 'VALIDATED' or r8.get('failed'):
    bad('validate: %r' % r8)
r9 = svc.distillate('validate', source=sid)
if r9.get('status') != 'ALREADY_RECORDED':
    bad('validate retry: %r' % r9)
r10 = svc.distillate('accept', source=sid, by='agent')
if r10.get('status') != 'ACCEPTED' or (r10.get('effective') or {}).get('version') != 2:
    bad('accept: %r' % r10)
r11 = svc.distillate('accept', source=sid, by='agent', note='a second note is audit text only')
if r11.get('status') != 'ALREADY_RECORDED':
    bad('accept retry: %r' % r11)
if prop_count(v2, 'distillate_review') != 1 or prop_count(v2, 'distillate_accept') != 1:
    bad('retries must not duplicate the review/accept records')

# 8. a new proposal keeps the accepted v2 effective; acceptance gates are hard
COMPRESSED2 = 'Детерминированный слой фиксируется, проверяемость результата важна.'
r12 = svc.distillate('propose', source=sid, text=COMPRESSED2, anchors=ANCHORS)
if r12.get('status') != 'PROPOSED':
    bad('propose v3: %r' % r12)
v3 = r12['versions'][-1]['claim_id']
if (r12.get('effective') or {}).get('claim_id') != v2:
    bad('the accepted v2 must stay effective: %r' % r12.get('effective'))
r13 = svc.distillate('accept', source=sid)
if r13.get('status') != 'REVIEW_REQUIRED':
    bad('accept without a review: %r' % r13)
r14 = svc.distillate('validate', source=sid)
if r14.get('status') != 'VALIDATED':
    bad('validate v3: %r' % r14)
quote = ANCHORS[0][0]
start = STMT.find(quote)
spans2 = {'v': 1, 'origin': 'agent', 'candidate_digest': raw_hash(COMPRESSED2.encode('utf8')),
          'sentences': [{'i': 0, 'anchors': [{'quote': quote, 'start': start, 'end': start + len(quote)}]}]}
svc.annotate(v3, 'property', name='basis_spans',
             value=json.dumps(spans2, ensure_ascii=False, sort_keys=True), reason='test anchors drift')
r15 = svc.distillate('accept', source=sid)
if r15.get('status') != 'REVIEW_STALE':
    bad('accept after the anchors changed: %r' % r15)

# 9. withdraw the current version: the accepted v2 is effective again
r16 = svc.distillate('withdraw', source=sid, note='superseded by a corrected version')
if r16.get('status') != 'WITHDRAWN' or (r16.get('effective') or {}).get('claim_id') != v2:
    bad('withdraw: %r' % r16)

# 10. V5 facts gate; three failures stop the chain
r17 = svc.distillate('propose', source=sid, text='Мы фиксируем детерминированный слой 7 раз.',
                     anchors=[['Мы фиксируем детерминированный слой.']])
if r17.get('status') != 'PROPOSED':
    bad('propose v4: %r' % r17)
r18 = svc.distillate('validate', source=sid)
if r18.get('status') != 'REVIEW_FAILED' or 'V5_FACTS' not in r18.get('failed', []) \
        or 'V5_FACTS' not in (r18.get('detail') or {}):
    bad('the invented number must fail V5: %r' % r18)
if (r18.get('effective') or {}).get('claim_id') != v2:
    bad('a rejected version must not displace v2: %r' % r18.get('effective'))
for number in ('8', '9'):
    p = svc.distillate('propose', source=sid, text='Мы фиксируем детерминированный слой %s раз.' % number,
                       anchors=[['Мы фиксируем детерминированный слой.']])
    if p.get('status') != 'PROPOSED':
        bad('propose a failing version: %r' % p)
    v = svc.distillate('validate', source=sid)
    if v.get('status') != 'REVIEW_FAILED':
        bad('the failing version must be REVIEW_FAILED: %r' % v)
r19 = svc.distillate('propose', source=sid, text='Мы фиксируем детерминированный слой 10 раз.',
                     anchors=[['Мы фиксируем детерминированный слой.']])
if r19.get('status') != 'DISTILLATE_LOOP':
    bad('three failures must stop the chain: %r' % r19)

# 11. a source dispute makes every version unusable; resolve restores the fallback
r20 = svc.distillate('dispute', source=sid, note='the user says this was said in another sense')
if r20.get('status') != 'DISPUTED' or r20.get('source_state') != 'disputed' or r20.get('effective') is not None:
    bad('dispute the source: %r' % r20)
r21 = svc.distillate('resolve', source=sid, note='the user confirmed the wording; the accepted v2 is the correction')
if r21.get('status') != 'RESOLVED' or r21.get('source_state') != 'active' \
        or (r21.get('effective') or {}).get('claim_id') != v2:
    bad('resolve: %r' % r21)
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
