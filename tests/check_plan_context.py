"""Stage 2a+2b self-check: the plan discussion becomes the context of its claims.

The plan file is a distillation of the conversation; the header claim of a plan
item must carry the reason the item exists, not only its title.  This check runs
the same scenario against BOTH host trees (claude/ and opencode/), which share
the engine:

1. a cold-start project, a plan file and a hook payload with ``dom_discussion``;
2. the plan is ingested with a context per item (the ``context`` property of the
   header claim and the rationale of the header candidate);
3. the same plan with a new discussion is PLAN_UNCHANGED: the context is
   versioned as a property, the plan revision does not move (QA004 F-39);
4. without a discussion the plan is still ingested, with no contexts.

    python tests/check_plan_context.py

Requires a Python interpreter with ``PyYAML`` (the hook programs do not need the
``mcp`` package).
"""
import json
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

PLAN = """1. Публикация репозитория на GitHub
1.1 Как писать и проверять
1.2 README и get-started
2. Лицензия MIT
"""
DISCUSSION = """User: Нужно опубликовать проект на GitHub. Репозиторий должен быть публичным, с понятным README и лицензией MIT.

Assistant: Предлагаю план: сначала подготовить README и get-started, затем лицензию, затем опубликовать. Репозиторий уже почти готов, не хватает документации.

User: Ещё важно описать, как писать и проверять код, чтобы контрибьюторы следовали стилю.

Assistant: Добавлю раздел «Как писать и проверять» с правилами и проверками. Ещё проверю, что лицензия MIT совместима с зависимостями.
"""
FOLLOW_UP = 'User: README должен объяснять установку, первый запуск и как писать тесты.'


def run(tree, script, args, stdin, cwd, env):
    return subprocess.run([PY, '-B', str(REPO / tree / 'hooks' / script), *args], input=stdin,
                          capture_output=True, text=True, encoding='utf8', env=env, cwd=str(cwd),
                          timeout=120)


def fresh(tree):
    scratch = Path(tempfile.mkdtemp(prefix='dom-context-%s-' % tree))
    project = scratch / 'project'
    project.mkdir()
    env = dict(os.environ)
    env.update({'OPENCODE_PROJECT_DIR': str(project), 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
    env.pop('DOM_RUNTIME_ROOT', None)
    env.pop('DOM_DIRECTORY', None)
    run(tree, 'plan_check_hook.py', [], 'первая задача: проверка контекста плана', project, env)
    return project, env, scratch


def ingest(tree, project, env, plan_file, discussion=None, session='ses-context'):
    payload = {'hook_event_name': 'PostToolUse', 'tool_name': 'ExitPlanMode',
               'tool_input': {'plan': plan_file.read_text(encoding='utf8')}, 'tool_response': {},
               'cwd': str(project), 'session_id': session}
    if discussion is not None:
        payload['dom_discussion'] = discussion
    return run(tree, 'plan_hook.py', ['--now', '--plan-file', str(plan_file)],
               json.dumps(payload, ensure_ascii=False), project, env)


def revision(project):
    con = sqlite3.connect(str(project / '.dom-memory' / 'main' / 'dom.sqlite3'))
    row = con.execute('SELECT source_revision FROM plan_projection_state').fetchone()
    con.close()
    return row[0] if row else None


def context_events(project):
    """path -> [context property values in event order] (the last one is current)."""
    con = sqlite3.connect(str(project / '.dom-memory' / 'main' / 'dom.sqlite3'))
    con.row_factory = sqlite3.Row
    query = """SELECT v.path, e.payload FROM task_claims tc
               JOIN current_plan_tasks v ON v.plan_task_id = tc.plan_task_id
               JOIN events e ON e.claim_ref = tc.claim_id AND e.kind = 'property'
               WHERE tc.role = 'defines' ORDER BY v.path, e.seq"""
    out = {}
    for row in con.execute(query):
        prop = (json.loads(row['payload']).get('value') or {}).get('value') or {}
        if prop.get('name') == 'context':
            out.setdefault(row['path'], []).append(prop.get('value'))
    con.close()
    return out


def header_rationale(project):
    """path -> rationale of the current header candidate."""
    con = sqlite3.connect(str(project / '.dom-memory' / 'main' / 'dom.sqlite3'))
    con.row_factory = sqlite3.Row
    query = """SELECT v.path, ca.payload FROM task_claims tc
               JOIN current_plan_tasks v ON v.plan_task_id = tc.plan_task_id
               JOIN claims cl ON cl.id = tc.claim_id
               JOIN candidates ca ON ca.id = cl.candidate
               WHERE tc.role = 'defines' ORDER BY v.path"""
    out = {}
    for row in con.execute(query):
        items = json.loads(row['payload']).get('items') or []
        out[row['path']] = (items[0].get('metadata') or {}).get('rationale') if items else None
    con.close()
    return out


def check(tree):
    # 1-2: the discussion becomes the context of every item it explains.
    project, env, scratch = fresh(tree)
    plan_file = project / 'plan.md'
    plan_file.write_text(PLAN, encoding='utf8')
    result = ingest(tree, project, env, plan_file, DISCUSSION)
    if result.returncode != 0 or 'PLAN_INGESTED' not in result.stderr:
        print('FAIL[%s]: plan not ingested\n  rc=%s err=%r' % (tree, result.returncode, result.stderr))
        return False
    contexts = context_events(project)
    if len(contexts) < 3:
        print('FAIL[%s]: expected contexts for most items, got %r' % (tree, sorted(contexts)))
        return False
    if not all(values and values[-1].strip() for values in contexts.values()):
        print('FAIL[%s]: an empty context was stored: %r' % (tree, contexts))
        return False
    rationale = header_rationale(project)
    if not any(rationale.get(path) == values[-1] for path, values in contexts.items()):
        print('FAIL[%s]: the header rationale does not carry the context\n  %r\n  %r'
              % (tree, contexts, rationale))
        return False
    first_revision = revision(project)

    # 3: a new discussion updates the context, not the plan revision.
    result = ingest(tree, project, env, plan_file, FOLLOW_UP)
    if 'PLAN_UNCHANGED' not in result.stderr:
        print('FAIL[%s]: the same plan must be PLAN_UNCHANGED, got %r' % (tree, result.stderr))
        return False
    if revision(project) != first_revision:
        print('FAIL[%s]: a context update moved the plan revision %r -> %r'
              % (tree, first_revision, revision(project)))
        return False
    updated = context_events(project)
    readme = updated.get('П1.1') or []
    if len(readme) < 2 or readme[-1] == readme[0] or 'тесты' not in readme[-1]:
        print('FAIL[%s]: the README context was not versioned by the new discussion: %r' % (tree, readme))
        return False
    shutil.rmtree(scratch, ignore_errors=True)

    # 4: no discussion -> the plan is still ingested, with no contexts.
    project, env, scratch = fresh(tree)
    plan_file = project / 'plan.md'
    plan_file.write_text(PLAN, encoding='utf8')
    result = ingest(tree, project, env, plan_file)
    if result.returncode != 0 or 'PLAN_INGESTED' not in result.stderr:
        print('FAIL[%s]: plan without a discussion not ingested: %r' % (tree, result.stderr))
        return False
    if context_events(project):
        print('FAIL[%s]: contexts appeared without a discussion: %r' % (tree, context_events(project)))
        return False
    shutil.rmtree(scratch, ignore_errors=True)
    print('  %s/: PASS — %d contexts, revision %s, a new discussion versioned the context only'
          % (tree, len(contexts), first_revision))
    return True


def main():
    try:
        import yaml  # noqa: F401
    except ImportError:
        print('SKIP: PyYAML is not importable; install it (pip install PyYAML) to run this check.')
        return 3
    results = {tree: check(tree) for tree in TREES}
    if not all(results.values()):
        return 1
    print('PASS: both trees distil the plan discussion into per-item contexts.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
