"""Stage 2d self-check: the plan queue is visible and logged.

A plan queued by a hook must not disappear silently.  This check runs the same
scenario against BOTH host trees (claude/ and opencode/):

1. a plan queued by ``plan_hook.py`` (no ``--now``) is taken in by
   ``dom_plan_status``: ``inbox_processed=1``, ``inbox_queued=0``, plan r1;
2. the run log (``<run>/watcher.log``) records the queueing and the drain;
3. a job that cannot be processed moves to ``inbox/failed/`` and is counted in
   ``inbox_failed``, with a ``job_failed`` record in the log.

    python tests/check_inbox_status.py

Requires a Python interpreter with ``PyYAML`` (the hook programs) and the engine
importable (the same interpreter runs the MCP server).
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
1.1 README и get-started
2. Лицензия MIT
"""

STATUS = """
import json
import sys
sys.path.insert(0, sys.argv[1])
from mockup.admission import Service
from mockup.paths import plugin_directory
from mockup import public_dto
raw = Service(plugin_directory(), profile='lite').plan_status()
print(json.dumps(public_dto.project_tool('dom_plan_status', raw, profile='lite')))
"""


def run(tree, script, args, stdin, cwd, env):
    return subprocess.run([PY, '-B', str(REPO / tree / 'hooks' / script), *args], input=stdin,
                          capture_output=True, text=True, encoding='utf8', env=env, cwd=str(cwd),
                          timeout=120)


def fresh(tree):
    scratch = Path(tempfile.mkdtemp(prefix='dom-inbox-%s-' % tree))
    project = scratch / 'project'
    project.mkdir()
    env = dict(os.environ)
    env.update({'OPENCODE_PROJECT_DIR': str(project), 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
    env.pop('DOM_RUNTIME_ROOT', None)
    env.pop('DOM_DIRECTORY', None)
    run(tree, 'plan_check_hook.py', [], 'первая задача: проверка очереди', project, env)
    return project, env, scratch


def plan_status(tree, project, env):
    result = subprocess.run([PY, '-B', '-c', STATUS, str(REPO / tree)], capture_output=True, text=True,
                            encoding='utf8', env=env, cwd=str(project), timeout=120)
    if result.returncode != 0:
        raise RuntimeError('plan_status failed: %r' % result.stderr)
    return json.loads(result.stdout.strip().splitlines()[-1])


def log_records(project):
    path = project / '.dom-memory' / 'main' / 'watcher.log'
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf8').splitlines() if line.strip()]


def revision(project):
    con = sqlite3.connect(str(project / '.dom-memory' / 'main' / 'dom.sqlite3'))
    row = con.execute('SELECT source_revision FROM plan_projection_state').fetchone()
    con.close()
    return row[0] if row else None


def check(tree):
    project, env, scratch = fresh(tree)
    plan_file = project / 'plan.md'
    plan_file.write_text(PLAN, encoding='utf8')
    payload = {'hook_event_name': 'PostToolUse', 'tool_name': 'ExitPlanMode',
               'tool_input': {'plan': PLAN}, 'tool_response': {}, 'cwd': str(project),
               'session_id': 'ses-inbox'}
    queued = run(tree, 'plan_hook.py', ['--plan-file', str(plan_file)],
                 json.dumps(payload, ensure_ascii=False), project, env)
    if queued.returncode != 0 or 'QUEUED' not in queued.stderr:
        print('FAIL[%s]: the plan was not queued: rc=%s err=%r' % (tree, queued.returncode, queued.stderr))
        return False
    if not any(r.get('event') == 'queued' for r in log_records(project)):
        print('FAIL[%s]: queueing left no record in watcher.log' % tree)
        return False

    status = plan_status(tree, project, env)
    if status.get('inbox_processed') != 1 or status.get('inbox_queued') != 0 or status.get('inbox_failed') != 0:
        print('FAIL[%s]: queue counters after the drain: %r' % (tree, status))
        return False
    if not status.get('has_plan') or revision(project) != 'r1':
        print('FAIL[%s]: the queued plan was not taken in (has_plan=%r, revision=%r)'
              % (tree, status.get('has_plan'), revision(project)))
        return False
    if not any(r.get('event') == 'job_done' and r.get('status') == 'PLAN_INGESTED' for r in log_records(project)):
        print('FAIL[%s]: the drain left no job_done record: %r' % (tree, log_records(project)[-3:]))
        return False

    # A job that cannot be read is not lost: it moves to failed/ and is counted.
    inbox = project / '.dom-memory' / 'main' / 'inbox'
    (inbox / 'broken.json').write_text('{not json', encoding='utf8')
    status = plan_status(tree, project, env)
    if status.get('inbox_failed') != 1 or status.get('inbox_queued') != 0:
        print('FAIL[%s]: a broken job is not reported (status %r)' % (tree, status))
        return False
    if not any(r.get('event') == 'job_failed' and r.get('status') == 'JOB_FAILED' for r in log_records(project)):
        print('FAIL[%s]: the failed job left no record in watcher.log' % tree)
        return False
    failed = list((inbox / 'failed').glob('broken.result.json'))
    if not failed:
        print('FAIL[%s]: the broken job has no result file' % tree)
        return False
    shutil.rmtree(scratch, ignore_errors=True)
    print('  %s/: PASS — queued -> r1, inbox_processed=1, a broken job counted and logged' % tree)
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
    results = {tree: check(tree) for tree in TREES}
    if not all(results.values()):
        return 1
    print('PASS: both trees report and log the plan queue.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
