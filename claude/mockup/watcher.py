"""Duty mode: keep the plan memory in step with the plan, between MCP sessions.

    python -m mockup.watcher run    --directory runtime/<project> --plan-file PLAN.md [--interval 30] [--check-every 10]
    python -m mockup.watcher once   --directory runtime/<project> --plan-file PLAN.md
    python -m mockup.watcher ingest --directory runtime/<project> --file PLAN.md [--source OMH]
    python -m mockup.watcher status --directory runtime/<project>
    python -m mockup.watcher extract --directory runtime/<project> --task П1.2 --file notes.md

One tick:
1. every ``--plan-file`` is read; the same plan is a recorded look (idempotent
   ingest, no writes to the ledger), a changed one is ingested;
2. ``inbox/*.json`` jobs are processed (written by hooks, OMH or Hermes):
   {"op": "ingest", "plan": ..., "source"?, "contexts"?} | {"op": "extract", "task", "text", "provider"?};
   each job moves to ``inbox/done/`` (or ``inbox/failed/``) with its result;
3. while the project is open, every ``--check-every`` looks without a change a
   plan check is due: a request file is written to ``requests/`` (once per
   change) for the outer orchestrator to hand to the agent; with no plan at
   all, a plan request is written.

Failures are retried (``--retries``); then they are logged to ``watcher.log``
and the state ``ERROR`` is written to ``watcher-status.json`` (signal, never a
silent skip).  The server profile is lite (local single owner).
"""
import argparse
import json
import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .admission import Service
from .runlog import log_record

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / 'runtime').resolve()


def _now():
    return datetime.now(timezone.utc).isoformat()


class Watcher:
    def __init__(self, directory, plan_files=(), source='agent', check_every=10, retries=3, review_mode='auto'):
        self.directory = Path(directory).resolve()
        self.svc = Service(self.directory, profile='lite', review_mode=review_mode)
        self.plan_files = [Path(p) for p in plan_files]
        self.source = source
        self.check_every = check_every
        self.retries = retries
        self.inbox = self.directory / 'inbox'
        self.requests = self.directory / 'requests'

    # ------------------------------------------------------------ io --
    def log(self, record):
        log_record(self.directory, record)

    def state(self, state, **extra):
        body = dict(extra, state=state, at=_now(), pid=os.getpid())
        tmp = self.directory / ('watcher-status.json.%s.tmp' % uuid.uuid4().hex)
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding='utf8')
        os.replace(tmp, self.directory / 'watcher-status.json')

    def _retry(self, what, fn):
        last = None
        for attempt in range(1, self.retries + 1):
            try:
                result = fn()
                status = result.get('status') if isinstance(result, dict) else None
                if status in ('STORAGE_BUSY', 'REVISION_CONFLICT', 'STORAGE_UNAVAILABLE'):
                    last = status
                    time.sleep(0.2 * attempt)
                    continue
                return result
            except Exception as e:  # logged with the type; the tick goes on
                last = '%s: %s' % (type(e).__name__, e)
                time.sleep(0.2 * attempt)
        self.log({'event': 'failed', 'what': what, 'error': last})
        raise RuntimeError('%s failed after %d attempts: %s' % (what, self.retries, last))

    # ---------------------------------------------------------- steps --
    def ingest_text(self, text, source=None, contexts=None):
        return self._retry('ingest', lambda: self.svc.plan_ingest(text, source or self.source, 'auto', contexts))

    def poll_plans(self):
        out = []
        for path in self.plan_files:
            try:
                text = path.read_text(encoding='utf8')
            except OSError as e:
                self.log({'event': 'plan_file_unreadable', 'file': str(path), 'error': type(e).__name__})
                out.append({'file': str(path), 'status': 'UNREADABLE'})
                continue
            result = self.ingest_text(text)
            out.append({'file': str(path), 'status': result.get('status'), 'revision': result.get('source_revision')})
            if result.get('status') == 'PLAN_INGESTED':
                self.log({'event': 'plan_ingested', 'file': str(path), 'revision': result.get('source_revision'),
                          'created': result.get('created'), 'updated': result.get('updated')})
        return out

    def process_inbox(self):
        return drain_inbox(self.svc, self.source, self.log)

    def maybe_request(self):
        status = self.svc.plan_status(self.check_every)
        if status.get('status') != 'PLAN_STATUS':
            return status
        request = status.get('request')
        if request is None:
            return status
        with self.svc.store.read() as c:
            last_change = c.execute('SELECT COALESCE(MAX(seq),0) FROM plan_checks WHERE changed=1').fetchone()[0]
            asked = c.execute("SELECT 1 FROM plan_checks WHERE kind='request' AND seq>?", (last_change,)).fetchone()
        if asked:
            return status  # one request per change; the orchestrator still sees it in dom_plan_status
        self.requests.mkdir(exist_ok=True)
        name = '%s-%s.json' % (request['kind'], datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'))
        (self.requests / name).write_text(json.dumps(dict(request, created_at=_now(), tool='dom_plan_ingest'),
                                                     ensure_ascii=False, indent=1), encoding='utf8')
        from . import planning
        planning.record_check(self.svc, self.source, None, False, 'request', request['kind'])
        self.log({'event': 'request_written', 'kind': request['kind'], 'file': name})
        return status

    def tick(self):
        try:
            plans = self.poll_plans()
            jobs = self.process_inbox()
            status = self.maybe_request()
            self.state('OK', plans=plans, jobs=jobs, plan_check_due=status.get('plan_check_due'),
                       project_closed=status.get('project_closed'))
            return {'plans': plans, 'jobs': jobs, 'status': status}
        except Exception as e:
            self.state('ERROR', error=type(e).__name__, detail=str(e)[:500])
            raise

    def run(self, interval, max_ticks=None):
        n = 0
        while max_ticks is None or n < max_ticks:
            n += 1
            try:
                result = self.tick()
                if result['status'].get('project_closed'):
                    self.state('CLOSED', note='every plan item is done; duty mode stops')
                    return 0
            except Exception:
                pass  # recorded in watcher-status.json and watcher.log; next tick retries
            time.sleep(interval)
        return 0


def inbox_state(svc):
    """-> {'queued': n, 'failed': n, 'done': n}: jobs still waiting and their outcomes.

    ``queued`` counts the job files still directly in ``inbox/`` (a job that
    cannot be taken in yet stays there); ``done``/``failed`` count the result
    files the drain wrote next to the moved job.
    """
    inbox = Path(svc.directory) / 'inbox'

    def count(folder, pattern):
        path = inbox / folder if folder else inbox
        return len(list(path.glob(pattern))) if path.is_dir() else 0

    return {'queued': count(None, '*.json'), 'failed': count('failed', '*.result.json'),
            'done': count('done', '*.result.json')}


def drain_inbox(svc, default_source='agent', log=None):
    """Process ``<run>/inbox/*.json`` jobs once; each moves to done/ or failed/ with its result."""
    inbox = Path(svc.directory) / 'inbox'
    done = []
    if not inbox.is_dir():
        return done
    for job in sorted(inbox.glob('*.json')):
        error = None
        try:
            spec = json.loads(job.read_text(encoding='utf8'))
            op = spec.get('op')
            if op == 'ingest':
                # T-02 / TD-DOM-57: the plan is ingested as the identity that queued it,
                # not as whichever process drains the inbox first.
                owner = svc
                if spec.get('principal') and spec['principal'] != svc.context.principal:
                    from .admission import Service
                    owner = Service(svc.directory, role=svc.role, profile='lite', review_mode=svc.launch_review_mode,
                                    principal=spec['principal'])
                result = owner.plan_ingest(spec['plan'], spec.get('source') or default_source, 'auto', spec.get('contexts'),
                                           session=spec.get('session'))
            elif op == 'extract':
                result = svc.extract(spec['task'], spec['text'], spec.get('provider'))
            else:
                result = {'status': 'UNKNOWN_OP'}
            if result.get('status') in ('NOT_INITIALIZED', 'STORAGE_BUSY'):
                continue  # stays queued: the project is not started yet / the base is busy
            folder = 'done' if result.get('status') in ('PLAN_INGESTED', 'PLAN_UNCHANGED', 'EXTRACTED') else 'failed'
        except Exception as e:
            result, folder = {'status': 'JOB_FAILED', 'error': type(e).__name__}, 'failed'
            error = traceback.format_exception_only(type(e), e)[-1].strip()
        target = inbox / folder
        target.mkdir(exist_ok=True)
        (target / (job.stem + '.result.json')).write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding='utf8')
        os.replace(job, target / job.name)
        if log:  # 2d: every job leaves a trace in watcher.log, not only the failures
            record = {'event': 'job_done' if folder == 'done' else 'job_failed', 'job': job.name,
                      'status': result.get('status'), 'folder': folder}
            if error:
                record['error'] = error
            log(record)
        done.append({'job': job.name, 'status': result.get('status')})
    return done


def _directory(value):
    d = Path(value).resolve()
    from .paths import runtime_root
    if not d.is_relative_to(runtime_root()):
        raise SystemExit('REFUSED: --directory must resolve inside ' + str(runtime_root()))
    d.mkdir(parents=True, exist_ok=True)
    return d


def main(argv=None):
    parser = argparse.ArgumentParser(prog='mockup.watcher', description='DOM plan-memory duty mode.')
    sub = parser.add_subparsers(dest='cmd', required=True)
    for name in ('run', 'once', 'ingest', 'status', 'extract'):
        p = sub.add_parser(name)
        p.add_argument('--directory', required=True)
        p.add_argument('--source', default='agent', choices=('agent', 'OMH'))
        if name in ('run', 'once'):
            p.add_argument('--plan-file', action='append', default=[])
            p.add_argument('--interval', type=float, default=30.0)
            p.add_argument('--check-every', type=int, default=10)
            p.add_argument('--retries', type=int, default=3)
            p.add_argument('--max-ticks', type=int, default=None)
        if name in ('ingest', 'extract'):
            p.add_argument('--file', required=True)
        if name == 'extract':
            p.add_argument('--task', required=True)
            p.add_argument('--provider', default=None)
        if name == 'status':
            p.add_argument('--check-every', type=int, default=10)
    args = parser.parse_args(argv)
    directory = _directory(args.directory)
    if args.cmd in ('run', 'once'):
        w = Watcher(directory, args.plan_file, args.source, args.check_every, args.retries)
        if args.cmd == 'once':
            print(json.dumps(w.tick(), ensure_ascii=False, indent=1, default=str))
            return 0
        return w.run(args.interval, args.max_ticks)
    svc = Service(directory, profile='lite')
    if args.cmd == 'ingest':
        result = svc.plan_ingest(Path(args.file).read_text(encoding='utf8'), args.source)
    elif args.cmd == 'extract':
        result = svc.extract(args.task, Path(args.file).read_text(encoding='utf8'), args.provider)
    else:
        result = svc.plan_status(args.check_every)
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0 if 'status' in result and not str(result['status']).endswith(('DENIED', 'NOT_FOUND', 'NOT_INITIALIZED')) else 1


if __name__ == '__main__':
    sys.exit(main())
