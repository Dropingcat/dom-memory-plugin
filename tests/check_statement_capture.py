"""Stage 3 self-check (П4.1/П4.4): the hook captures user statements.

A prompt that states something (spec §12.3) is recorded by
``plan_check_hook.py`` as a source quote (the prompt verbatim) plus the
deterministic extractive distillate v1 with resolved anchors (§12.4), in one
two-item claims package; the properties (``capture_pack`` on the source,
``basis_spans`` on the v1) go into one following append package (§12.11).
This check runs the same scenario against BOTH host trees (claude/ and
opencode/) and verifies the ledger directly in SQLite:

1. the root prompt is not captured again (it is the root request claim);
2. a decision prompt is captured: quote bytes == prompt, locator/shape, the
   v1 with basis=[source], origin_version=stmt-extract-v1, anchors that
   resolve by offset; both properties present;
3. a question prompt records nothing;
4. a lone unsupported marker records nothing;
5. an explicit request ("Запомни:") captures alone;
6. the same statement with different whitespace is one source (normalized
   idempotency key), the repeat prints nothing;
7. an empty prompt is a silent no-op;
8. with DOM_AUTOSTART=0 and no database the hook exits 0 and starts nothing;
9. `dom_distillate(action="show", source=...)` reads the captured chain back
   through the real server wiring (dispatch + public projection): v1 is
   ``extracted`` and effective, two reads are identical, an unknown source is
   a structured NO_STATEMENT refusal with a hint, and `show` without a target
   is INVALID_FIELD.

    python tests/check_statement_capture.py

Requires the mockup engine importable (the same interpreter runs the server).
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
TREES = ('claude', 'opencode')

PROMPT_ROOT = 'Сделай план миграции базы данных и зафиксируй baseline.'
PROMPT_STMT = 'Делаем детерминированный экстрактивный дистиллят. Это базовый процесс.'

LINE_RE = re.compile(r"\[DOM project memory\] statement (\S+); distillate (\S+) \(extracted\)\. "
                     r"The source is ground truth; improve it with dom_distillate\(action=\"propose\", source=\"\1\", ...\)\.")


def run_hook(tree, project, env, prompt, session='s1'):
    payload = json.dumps({'prompt': prompt, 'session_id': session}, ensure_ascii=False)
    return subprocess.run([PY, '-B', str(REPO / tree / 'hooks' / 'plan_check_hook.py')], input=payload,
                          capture_output=True, text=True, encoding='utf8', errors='replace',
                          env=env, cwd=str(project), timeout=120)


# The same call path the MCP server uses, minus the mcp transport import (the
# test interpreter has no mcp package): the tool must be declared in both
# server routing tables, the Service delegate is called directly, and the
# answer goes through the public projection (closed schema + refusal hints).
TOOL_SNIPPET = r'''
import ast, json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from mockup.paths import plugin_directory
from mockup.admission import Service
from mockup import claim_entry, public_dto
src = (pathlib.Path(sys.argv[1]) / 'mockup' / 'server.py').read_text('utf8')
tables = {}
for node in ast.parse(src).body:
    if (isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Dict)
            and node.targets[0].id in ('LITE_SIGNATURE', 'HANDLERS')):
        tables[node.targets[0].id] = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
for table in ('LITE_SIGNATURE', 'HANDLERS'):
    if 'dom_distillate' not in tables.get(table, set()):
        raise SystemExit('wiring missing: ' + table)
assert 'dom_distillate' in public_dto.LITE_RESPONSES
assert 'dom_distillate' in claim_entry.TOOL_DOCS
svc = Service(plugin_directory(), profile='lite', role='owner')
args = json.loads(sys.argv[2])
action = args.pop('action')
result = svc.distillate(action, **args)
print(json.dumps(public_dto.project_tool('dom_distillate', result, scope=svc.context.branches, profile='lite'),
                 ensure_ascii=False, sort_keys=True))
'''


def call_tool(tree, project, env, **args):
    return subprocess.run([PY, '-B', '-c', TOOL_SNIPPET, str(REPO / tree), json.dumps(args, ensure_ascii=False)],
                          capture_output=True, text=True, encoding='utf8', errors='replace',
                          env=env, cwd=str(project), timeout=120)


def db(project):
    path = project / '.dom-memory' / 'main' / 'dom.sqlite3'
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    return con


def claim_row(con, claim_id):
    return con.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()


def quote_count(con):
    return con.execute("SELECT COUNT(*) FROM claims WHERE content_type='quote'").fetchone()[0]


def properties(con):
    """claim_id -> {property name: parsed value} from the event ledger."""
    out = {}
    for row in con.execute("SELECT claim_ref, payload FROM events WHERE kind='property'"):
        try:
            event = json.loads(row['payload']).get('value') or {}
            value = (event.get('value') or {}).get('value')
        except ValueError:
            continue
        if isinstance(value, str) and value[:1] in ('{', '['):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        out.setdefault(row['claim_ref'], {})[(event.get('value') or {}).get('name')] = value
    return out


def stmt_id(stdout):
    m = LINE_RE.search(stdout)
    return m.group(1) if m else None


def norm(text):
    return re.sub(r'\s+', ' ', text).strip().casefold()


def check(tree, distillate):
    scratch = Path(tempfile.mkdtemp(prefix='dom-stmt-%s-' % tree))
    try:
        project = scratch / 'project'
        project.mkdir()
        env = dict(os.environ)
        env.update({'OPENCODE_PROJECT_DIR': str(project), 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
        env.pop('DOM_RUNTIME_ROOT', None)
        env.pop('DOM_DIRECTORY', None)
        env.pop('DOM_SESSION', None)

        # 1. the first prompt autostarts and is NOT captured again (root request)
        r1 = run_hook(tree, project, env, PROMPT_ROOT)
        if r1.returncode != 0 or 'memory started' not in r1.stdout or 'statement ' in r1.stdout:
            print('FAIL[%s]: the root prompt must autostart without a capture: rc=%s out=%r err=%r'
                  % (tree, r1.returncode, r1.stdout[-300:], r1.stderr[-300:]))
            return False
        con = db(project)
        if quote_count(con) != 1:
            print('FAIL[%s]: the root prompt must stay the single quote (got %d)' % (tree, quote_count(con)))
            return False

        # 2. a decision prompt is captured: source + v1 + properties
        r2 = run_hook(tree, project, env, PROMPT_STMT)
        if r2.returncode != 0 or LINE_RE.search(r2.stdout) is None:
            print('FAIL[%s]: the statement line is missing: rc=%s out=%r err=%r'
                  % (tree, r2.returncode, r2.stdout[-400:], r2.stderr[-400:]))
            return False
        source_id, distillate_id = LINE_RE.search(r2.stdout).groups()
        src = claim_row(con, source_id)
        if src is None or src['content_type'] != 'quote':
            print('FAIL[%s]: no source quote claim %s' % (tree, source_id))
            return False
        if src['raw'].decode('utf8') != PROMPT_STMT:
            print('FAIL[%s]: the source must be the prompt verbatim' % tree)
            return False
        src_meta = json.loads(src['payload'])['metadata']
        if src_meta['source_ref'] != 'session:s1' or src_meta['locator'] != 'prompt:' + hashlib.sha256(norm(PROMPT_STMT).encode('utf8')).hexdigest()[:12]:
            print('FAIL[%s]: the source locator shape is wrong: %r' % (tree, src_meta))
            return False
        props = properties(con)
        if props.get(source_id, {}).get('capture_pack') != {'pack': 'v1', 'digest': distillate.pack_digest()}:
            print('FAIL[%s]: capture_pack missing or wrong: %r' % (tree, props.get(source_id)))
            return False
        v1 = claim_row(con, distillate_id)
        if v1 is None or v1['content_type'] != 'text' or v1['origin_version'] != 'stmt-extract-v1':
            print('FAIL[%s]: the v1 distillate is not a stmt-extract-v1 text claim' % tree)
            return False
        v1_payload = json.loads(v1['payload'])
        if v1_payload['metadata']['classification'] != 'interpretation' or v1_payload['metadata']['attribution'] != 'deterministic':
            print('FAIL[%s]: the v1 metadata must say interpretation/deterministic' % tree)
            return False
        if v1_payload['anatomy'].get('basis') != [source_id] or v1_payload['anatomy'].get('purpose') != 'distilled comment':
            print('FAIL[%s]: the v1 anatomy must name the source: %r' % (tree, v1_payload['anatomy']))
            return False
        spans = props.get(distillate_id, {}).get('basis_spans') or {}
        if spans.get('v') != 1 or spans.get('origin') != 'extract' or not spans.get('sentences'):
            print('FAIL[%s]: basis_spans missing or wrong: %r' % (tree, spans))
            return False
        if spans.get('candidate_digest') != hashlib.sha256(v1['raw']).hexdigest():
            print('FAIL[%s]: candidate_digest must match the v1 text' % tree)
            return False
        src_text = src['raw'].decode('utf8')   # anchors are character offsets in the decoded source
        for sent in spans['sentences']:
            for a in sent['anchors']:
                if src_text[a['start']:a['end']] != a['quote']:
                    print('FAIL[%s]: an anchor does not resolve by offset: %r' % (tree, a))
                    return False

        # 3-4. a question and a lone unsupported marker record nothing
        before = quote_count(con)
        for prompt in ('Как дела?', 'Важно: привет'):
            r = run_hook(tree, project, env, prompt)
            if r.returncode != 0 or 'statement ' in r.stdout or quote_count(con) != before:
                print('FAIL[%s]: %r must not capture: out=%r' % (tree, prompt, r.stdout[-200:]))
                return False

        # 5. an explicit request captures alone
        r5 = run_hook(tree, project, env, 'Запомни: правило номер 5')
        if r5.returncode != 0 or stmt_id(r5.stdout) is None or quote_count(con) != before + 1:
            print('FAIL[%s]: an explicit request must capture: out=%r err=%r' % (tree, r5.stdout[-300:], r5.stderr[-300:]))
            return False

        # 6. the same statement with different whitespace is ONE source
        r6a = run_hook(tree, project, env, 'Решили фиксировать baseline.')
        if stmt_id(r6a.stdout) is None:
            print('FAIL[%s]: the decision prompt must capture: out=%r err=%r' % (tree, r6a.stdout[-300:], r6a.stderr[-300:]))
            return False
        r6b = run_hook(tree, project, env, 'решили фиксировать baseline. ')
        if r6b.returncode != 0 or 'statement ' in r6b.stdout or quote_count(con) != before + 2:
            print('FAIL[%s]: the whitespace variant must dedupe: out=%r err=%r' % (tree, r6b.stdout[-300:], r6b.stderr[-300:]))
            return False

        # 7. an empty prompt is a silent no-op
        r7 = run_hook(tree, project, env, '')
        if r7.returncode != 0 or 'statement ' in r7.stdout:
            print('FAIL[%s]: an empty prompt must stay silent: out=%r' % (tree, r7.stdout[-200:]))
            return False

        # 8. DOM_AUTOSTART=0 without a database starts nothing, exits 0
        env2 = dict(env)
        env2['DOM_AUTOSTART'] = '0'
        empty = scratch / 'empty2'
        empty.mkdir()
        r8 = run_hook(tree, empty, env2, 'Запомни: правило номер 5')
        if r8.returncode != 0 or (empty / '.dom-memory').exists():
            print('FAIL[%s]: DOM_AUTOSTART=0 must start nothing and exit 0: rc=%s' % (tree, r8.returncode))
            return False

        # 9. dom_distillate(show) reads the captured chain back through the
        #    server wiring: v1 is extracted and effective
        r9 = call_tool(tree, project, env, action='show', source=source_id)
        if r9.returncode != 0 or not r9.stdout.strip():
            print('FAIL[%s]: show crashed: rc=%s err=%r' % (tree, r9.returncode, r9.stderr[-400:]))
            return False
        shown = json.loads(r9.stdout)
        if shown.get('status') != 'DISTILLATE_SHOWN':
            print('FAIL[%s]: show must answer DISTILLATE_SHOWN: %r' % (tree, shown))
            return False
        if not shown.get('versions') or shown['versions'][0].get('state') != 'extracted':
            print('FAIL[%s]: v1 must be extracted: %r' % (tree, shown.get('versions')))
            return False
        eff = shown.get('effective') or {}
        if eff.get('claim_id') != distillate_id or eff.get('origin') != 'extract':
            print('FAIL[%s]: effective must be the extracted v1: %r' % (tree, eff))
            return False

        # 10. two consecutive reads are identical (nothing mutable in show)
        r10 = call_tool(tree, project, env, action='show', source=source_id)
        a, b = json.loads(r9.stdout), json.loads(r10.stdout)
        a.pop('read_snapshot', None)
        b.pop('read_snapshot', None)
        if a != b:
            print('FAIL[%s]: show is not deterministic across reads' % tree)
            return False

        # 11. an unknown source is a structured refusal with a hint, never a crash
        r11 = call_tool(tree, project, env, action='show', source='no-such-id')
        out11 = json.loads(r11.stdout) if r11.stdout.strip() else {}
        if out11.get('status') != 'NO_STATEMENT' or not out11.get('hint'):
            print('FAIL[%s]: unknown source must refuse NO_STATEMENT with a hint: %r' % (tree, out11))
            return False

        # 12. show needs a target
        r12 = call_tool(tree, project, env, action='show')
        out12 = json.loads(r12.stdout) if r12.stdout.strip() else {}
        if out12.get('status') != 'INVALID_FIELD':
            print('FAIL[%s]: show without source/distillate must be INVALID_FIELD: %r' % (tree, out12))
            return False
        con.close()
        print('  %s/: PASS — root skipped, statement captured with anchors, questions and lone markers quiet, '
              'dedupe holds, show reads the chain' % tree)
        return True
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main():
    try:
        sys.path.insert(0, str(REPO / 'opencode'))
        import mockup.admission  # noqa: F401
        from mockup import distillate
    except ImportError:
        print('SKIP: the mockup engine is not importable; run this check with the venv that runs the server.')
        return 3
    results = {tree: check(tree, distillate) for tree in TREES}
    ok = all(results.values())
    print('PASS: statement capture identical in both trees' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
