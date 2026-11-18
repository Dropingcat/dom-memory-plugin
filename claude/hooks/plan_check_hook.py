"""Duty mode inside the host session: UserPromptSubmit hook.

On every user prompt (cheap: a few SQLite reads) it
1. starts the project memory when this is the first task (no ``dom.sqlite3``
   yet): the prompt is recorded verbatim as the root request claim, so a host
   plugin needs no separate init step -- connect it and the first task creates
   the memory of this project;
2. takes in plans queued by ``plan_hook`` (``<run>/inbox``);
3. when a plan check is due (``DOM_CHECK_EVERY`` looks without a change,
   default 10) or no plan is known at all, prints the request once per change.
   Claude Code adds a UserPromptSubmit hook's stdout to the model's context,
   so the agent sees the request and answers with ``dom_plan_ingest``.

Every prompt also counts as one look at the plan.  Silent when the memory is
absent and there is nothing to record (``DOM_AUTOSTART=0`` or an empty
prompt).  Never blocks: any failure exits 0.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def payload(raw):
    """-> (prompt, session) from a host payload: Claude JSON or a plain prompt (OpenCode)."""
    text = (raw or '').strip()
    if text.startswith('{'):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            prompt = data.get('prompt')
            session = data.get('session_id')
            return (prompt if isinstance(prompt, str) else '', session if isinstance(session, str) and session.strip() else None)
    return raw or '', None


def autostart(directory, prompt, role, principal):
    """First task of this project: create the memory and record the request verbatim."""
    from mockup.admission import Service
    directory.mkdir(parents=True, exist_ok=True)
    return Service(directory, profile='lite', role=role, principal=principal).start(request=prompt)


def main(argv=None):
    try:
        prompt, session = payload(sys.stdin.read())
        session = session or (os.environ.get('DOM_SESSION') or None)
        sys.path.insert(0, str(ROOT))
        from mockup.paths import plugin_directory
        directory = Path(argv[0]).resolve() if argv else plugin_directory()
        role = os.environ.get('DOM_ROLE') if os.environ.get('DOM_ROLE') in ('owner', 'author', 'reviewer') else 'owner'
        principal = os.environ.get('DOM_PRINCIPAL') or None  # T-02
        if not (directory / 'dom.sqlite3').exists():
            if os.environ.get('DOM_AUTOSTART') == '0' or not prompt.strip():
                return 0
            try:
                started = autostart(directory, prompt, role, principal)
            except Exception as e:  # a broken start must not block the agent, and must not stay silent
                print('plan_check_hook: start %s' % type(e).__name__, file=sys.stderr)
                return 0
            print('[DOM project memory] memory started for this project (branch %s): the request is recorded '
                  'verbatim as the root claim. Record decisions and findings with dom_claim; dom_describe lists the types.'
                  % started.get('main_branch', 'main'))
        from mockup import planning
        from mockup.admission import Service
        svc = Service(directory, profile='lite', role=role, principal=principal)  # T-02
        if prompt.strip():
            try:  # П4.1/П4.4: statement capture; a failure never blocks the prompt
                from mockup import distillate
                res = distillate.capture(svc, prompt, session)
                if res.get('line'):
                    print(res['line'])
            except Exception as e:
                print('plan_check_hook: capture %s' % type(e).__name__, file=sys.stderr)
        check_every = int(os.environ.get('DOM_CHECK_EVERY') or 10)
        status = svc.plan_status(check_every)  # drains the inbox first
        if status.get('status') == 'CHAIN_BROKEN':  # QA004 F-26: say it, every prompt, until fixed
            print('[DOM project memory] CHAIN_BROKEN: the memory database was edited outside the server. '
                  'Writes are refused. Tell the user before relying on the memory.')
            return 0
        if status.get('status') != 'PLAN_STATUS':
            return 0
        if status['has_plan'] and not status['project_closed']:
            planning.record_check(svc, 'agent', None, False, 'check', 'user prompt', session=session)
            status = svc.plan_status(check_every)
        request = status.get('request')
        if request is None:
            return 0
        with svc.store.read() as c:
            last_change = c.execute('SELECT COALESCE(MAX(seq),0) FROM plan_checks WHERE changed=1').fetchone()[0]
            asked = c.execute("SELECT 1 FROM plan_checks WHERE kind='request' AND seq>?", (last_change,)).fetchone()
        if asked:
            return 0
        planning.record_check(svc, 'agent', None, False, 'request', request['kind'], session=session)
        print('[DOM project memory] ' + request['text'])
    except Exception as e:
        print('plan_check_hook: %s' % type(e).__name__, file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
