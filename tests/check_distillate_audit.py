"""Stage 3 critic audit (П4.6): behavioural edges of the distillate chain.

This is not a re-run of the happy path.  It pins the places where a plausible
implementation is silently wrong, and where two names or two states could be
confused by a caller:

1. word-numbers: the source says ``10``, the proposal says ``десять`` -> V5
   fails on surface forms (the values are equal, the texts are not); a repeated
   number (``10 10`` against an anchor with one ``10``) also fails;
2. an ordinary ``и`` is not a number: a faithful compression with ``и`` has a
   passing V5 (the old tokenizer reported ``{'numbers': ['и']}``);
3. a quantifier swap ``всегда`` -> ``никогда`` fails V5 with both counts;
4. a dropped negation ``не фиксируем`` -> ``фиксируем`` fails V5;
5. the capture status ``NO_STATEMENT`` and the tool refusal
   ``STATEMENT_NOT_FOUND`` are different surfaces and do not collide;
6. the refusal table is closed: ``NO_STATEMENT`` and ``VARIANT_CONFLICT`` are
   gone, ``STATEMENT_NOT_FOUND`` is present, and every literal ``Denied`` code
   in ``distillate.py`` has a help entry;
7. an archived source stays readable (``source_state='superseded'``,
   ``effective=None``) but refuses new versions.

Runs the same scenario against BOTH host trees (claude/ and opencode/).
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PY = sys.executable
REPO = Path(__file__).resolve().parents[1]

SCENARIO = r'''
import ast, json, os, sys
sys.path.insert(0, sys.argv[1])
project = sys.argv[2]
os.environ['OPENCODE_PROJECT_DIR'] = project
os.chdir(project)
from mockup.paths import plugin_directory
from mockup.admission import Service
from mockup import distillate, public_dto


def bad(message):
    print('FAIL: ' + message)
    raise SystemExit(1)


def review(sid, text, anchors):
    r = svc.distillate('propose', source=sid, text=text, anchors=anchors)
    if r.get('status') != 'PROPOSED':
        bad('propose must create a version: %r' % r)
    v = svc.distillate('validate', source=sid)
    if v.get('status') not in ('VALIDATED', 'REVIEW_FAILED'):
        bad('validate: %r' % v)
    return v


ROOT = 'Собери память проекта и проверь утверждения.'
svc = Service(plugin_directory(), profile='lite', role='owner')
svc.start(request=ROOT)

# 1. surface forms of a number are compared, not numeric values
cap = distillate.capture(svc, 'Мы фиксируем 10 итераций проверки.', 's1')
if cap.get('status') != 'STATEMENT_RECORDED':
    bad('capture 10: %r' % cap)
v = review(cap['source_id'], 'Мы фиксируем десять итераций проверки.',
           [['10 итераций проверки.']])
if v.get('status') != 'REVIEW_FAILED' or 'V5_FACTS' not in v.get('failed', []):
    bad('10 vs десять must fail V5: %r' % v)
if v['detail']['V5_FACTS'].get('numbers') != ['десять']:
    bad('the V5 detail must name the word surface: %r' % v['detail']['V5_FACTS'])

# 1b. a repeated number is a fact change, not a free extra token
long_source = 'Мы фиксируем ' + ('очень длинное описание ' * 10) + '10 итераций.'
cap = distillate.capture(svc, long_source, 's1b')
if cap.get('status') != 'STATEMENT_RECORDED':
    bad('capture repeated number: %r' % cap)
v = review(cap['source_id'], 'Мы фиксируем 10 10 итераций.', [['10 итераций.']])
if 'V5_FACTS' not in v.get('failed', []) or v['detail']['V5_FACTS'].get('numbers') != ['10']:
    bad('a repeated number must fail V5: %r' % v)

# 2. an ordinary word is not a number; a faithful compression passes V5
cap = distillate.capture(svc, 'Мы фиксируем слой. Мы проверяем результат.', 's2')
if cap.get('status') != 'STATEMENT_RECORDED':
    bad('capture и: %r' % cap)
v = review(cap['source_id'], 'Мы фиксируем слой и проверяем результат.',
           [['Мы фиксируем слой.', 'Мы проверяем результат.']])
if v.get('status') != 'VALIDATED' or (v.get('review') or {}).get('checks', {}).get('V5') != 'pass':
    bad('the ordinary "и" must not be a V5 fact: %r' % v)
if distillate._numeral_tokens('слой и результат') != []:
    bad('ordinary words leaked into numerals: %r' % distillate._numeral_tokens('слой и результат'))
if distillate._numeral_tokens('10') != ['10'] or distillate._numeral_tokens('десять') != ['десять']:
    bad('digit and word surfaces must stay distinct')

# 3. a quantifier swap is a fact change
cap = distillate.capture(svc, 'Мы всегда проверяем якоря перед принятием.', 's3')
if cap.get('status') != 'STATEMENT_RECORDED':
    bad('capture всегда: %r' % cap)
v = review(cap['source_id'], 'Мы никогда проверяем якоря перед принятием.',
           [['всегда проверяем якоря перед принятием.']])
if 'V5_FACTS' not in v.get('failed', []) or not v['detail']['V5_FACTS'].get('quantifier'):
    bad('всегда -> никогда must fail V5 quantifier: %r' % v)
if set(v['detail']['V5_FACTS']['quantifier']) != {'всегда', 'никогда'}:
    bad('both quantifier surfaces must be named: %r' % v['detail']['V5_FACTS'])

# 4. a dropped negation is a fact change
cap = distillate.capture(svc, 'Мы не фиксируем версию без проверки.', 's4')
if cap.get('status') != 'STATEMENT_RECORDED':
    bad('capture negation: %r' % cap)
v = review(cap['source_id'], 'Мы фиксируем версию без проверки.',
           [['не фиксируем версию без проверки.']])
if 'V5_FACTS' not in v.get('failed', []) or v['detail']['V5_FACTS'].get('negation') != ['не']:
    bad('a dropped negation must fail V5 negation: %r' % v)

# 5. capture status and tool refusal are different surfaces
quiet = distillate.capture(svc, 'А что если сделать иначе?', 's5')
if quiet.get('status') != 'NO_STATEMENT':
    bad('a question must be the NO_STATEMENT capture status: %r' % quiet)
missing = svc.distillate('show', source='no-such-id')
if missing.get('status') != 'STATEMENT_NOT_FOUND' or missing.get('field') != 'source':
    bad('an unknown id must be the STATEMENT_NOT_FOUND refusal: %r' % missing)
public = public_dto.project_tool('dom_distillate', missing)
if public.get('status') != 'STATEMENT_NOT_FOUND' or not public.get('hint') \
        or public.get('next_action') != 'dom_distillate':
    bad('the public refusal must carry hint/next_action: %r' % public)

# 6. the refusal table is closed; every literal Denied code has help
if 'STATEMENT_NOT_FOUND' not in public_dto._HELP:
    bad('STATEMENT_NOT_FOUND has no help entry')
for dead in ('NO_STATEMENT', 'VARIANT_CONFLICT'):
    if dead in public_dto._HELP:
        bad('%s is still on the public refusal surface' % dead)
tree = os.path.dirname(os.path.abspath(distillate.__file__))
source = open(os.path.join(tree, 'distillate.py'), encoding='utf8').read()
raised = set()
for node in ast.walk(ast.parse(source)):
    if not isinstance(node, ast.Call) or not node.args:
        continue
    fn = node.func
    direct = isinstance(fn, ast.Name) and fn.id == 'Denied'
    factory = isinstance(fn, ast.Call) and isinstance(fn.func, ast.Name) and fn.func.id == '_denied'
    if (direct or factory) and isinstance(node.args[0], ast.Constant):
        raised.add(node.args[0].value)
missing_help = sorted(code for code in raised if code not in public_dto._HELP)
if missing_help:
    bad('refusal codes without a help entry: %r' % missing_help)

# 7. an archived source is readable but takes no new versions
cap = distillate.capture(svc, 'Мы фиксируем правило архивации источника.', 's6')
if cap.get('status') != 'STATEMENT_RECORDED':
    bad('capture archive: %r' % cap)
sid = cap['source_id']
svc.annotate(sid, 'archive', reason='retired by the audit')
shown = svc.distillate('show', source=sid)
if shown.get('source_state') != 'superseded' or shown.get('effective') is not None:
    bad('an archived source must be superseded with no effective version: %r' % shown)
blocked = svc.distillate('propose', source=sid, text='Мы фиксируем другое правило.',
                         anchors=[['Мы фиксируем правило архивации источника.']])
if blocked.get('status') != 'STATEMENT_NOT_FOUND' or not blocked.get('hint'):
    bad('an archived source must refuse new versions: %r' % blocked)

print('PASS')
'''


def check(tree):
    scratch = Path(tempfile.mkdtemp(prefix='dom-audit-%s-' % tree))
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
            print('FAIL[%s]: %s' % (tree, (run.stdout + run.stderr)[-700:]))
            return False
        print('  %s/: PASS — word-number surfaces (including repeats), ordinary words, '
              'quantifier and negation swaps, capture/refusal split, closed help table, '
              'archived-source read-only' % tree)
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
    print('PASS: the distillate audit is identical in both trees' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
