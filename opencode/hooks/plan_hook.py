"""Plan hook: hand the agent's plan to the DOM plan memory (no MCP session needed).

Claude Code plugin (``hooks/hooks.json``): PostToolUse on ``ExitPlanMode``.
Standalone / OpenCode / Hermes / OMH: pipe the plan as text or JSON on stdin::

    python hooks/plan_hook.py --directory runtime/<run> --source OMH < plan.md

Without ``--directory`` the run directory is ``<project>/.dom-memory/main``
(``mockup.paths.plugin_directory``).  ``--now`` ingests directly; when the
project is not started yet (or without ``--now``) the plan is queued in
``<run>/inbox/`` and taken in by the duty mode or the next ``dom_plan_status``.

``TodoWrite`` lists are session to-dos, not the project plan: they are ignored
unless ``--todos`` is given.  The hook never blocks the agent: every failure
exits 0 with a note on stderr.
"""
import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


APPROVED_MARK = '## Approved Plan:'
PLAN_FILE_KEYS = ('filePath', 'file_path', 'planFilePath', 'plan_file_path', 'planFile', 'plan_file')
PLAN_FILE_MAX_AGE = 15 * 60  # only a plan file written just now is the plan just approved


def _text_plan(value):
    """A plan text out of a tool_response string: the part after '## Approved Plan:' if present."""
    if not isinstance(value, str) or APPROVED_MARK not in value:
        return None  # any other tool text ("User has approved your plan") is not the plan
    return value.split(APPROVED_MARK, 1)[1].strip() or None


def _read_file(path):
    try:
        p = Path(path).expanduser()
        if p.is_file() and p.stat().st_size <= 1024 * 1024:
            text = p.read_text(encoding='utf8', errors='replace').strip()
            return text or None
    except (OSError, ValueError):
        return None
    return None


def _from_mapping(obj):
    """plan text from a tool_input/tool_response dict: 'plan' or a plan file path."""
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get('plan'), str) and obj['plan'].strip():
        return obj['plan']
    for key in PLAN_FILE_KEYS:
        if isinstance(obj.get(key), str):
            text = _read_file(obj[key])
            if text:
                return text
    for value in obj.values():  # e.g. {"data": {"plan": ...}} or a text block
        if isinstance(value, dict):
            found = _from_mapping(value)
            if found:
                return found
    return None


def _from_response(resp):
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except ValueError:
            return _text_plan(resp)
    if isinstance(resp, dict):
        found = _from_mapping(resp)
        if found:
            return found
        for key in ('text', 'content', 'result', 'message'):
            if isinstance(resp.get(key), (str, list)):
                found = _from_response(resp[key])
                if found:
                    return found
        return None
    if isinstance(resp, list):  # content blocks [{"type": "text", "text": ...}]
        for block in resp:
            found = _from_response(block)
            if found:
                return found
    return None


def _from_transcript(path):
    """The last ExitPlanMode input/result in the session transcript (JSONL)."""
    try:
        p = Path(path).expanduser()
        if not p.is_file():
            return None
        lines = p.read_text(encoding='utf8', errors='replace').splitlines()[-400:]
    except (OSError, ValueError):
        return None
    exit_ids = set()
    found = None
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        content = ((entry.get('message') or {}).get('content')) if isinstance(entry, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use' and block.get('name') == 'ExitPlanMode':
                exit_ids.add(block.get('id'))
                found = _from_mapping(block.get('input') or {}) or found
            elif block.get('type') == 'tool_result' and block.get('tool_use_id') in exit_ids:
                found = _from_response(block.get('content')) or found
        if isinstance(entry.get('toolUseResult'), dict):
            found = _from_mapping(entry['toolUseResult']) or found
    return found


def _latest_plan_file(cwd=None):
    """Newest recent *.md in <cwd>/.claude/plans only.

    The shared ~/.claude/plans is never read: it holds plans of every project and
    session, and a foreign plan would be written into this append-only memory
    (independent review R-01 / TD-DOM-56).  When the plan is not in the payload,
    the transcript or the project's own plan folder, the agent is asked instead."""
    import time
    if not cwd:
        return None
    best = None
    try:
        for f in (Path(cwd) / '.claude' / 'plans').glob('*.md'):
            m = f.stat().st_mtime
            if time.time() - m <= PLAN_FILE_MAX_AGE and (best is None or m > best[0]):
                best = (m, f)
    except OSError:
        return None
    return _read_file(best[1]) if best else None


def plan_from_hook(raw, todos=False):
    """-> (plan, kind) from a Claude Code hook payload, plain text or JSON.

    ExitPlanMode payloads differ between hosts: the CLI puts the plan in
    ``tool_input.plan``; the desktop app passes only ``allowedPrompts`` and keeps
    the plan in a plan file (QA004 F-33).  Looked at in order: tool_input,
    tool_response (plan, plan file path, text after '## Approved Plan:'), the
    session transcript, the newest recent file in ``<cwd>/.claude/plans``.
    ``kind`` is ``ExitPlanMode-missing`` when the plan was not found.
    """
    try:
        data = json.loads(raw)
    except ValueError:
        return (raw, 'text') if raw.strip() else (None, None)
    if isinstance(data, dict) and 'tool_name' in data:
        tool_input = data.get('tool_input') or {}
        if data['tool_name'] == 'ExitPlanMode':
            plan = (_from_mapping(tool_input) or _from_response(data.get('tool_response'))
                    or (_from_transcript(data['transcript_path']) if isinstance(data.get('transcript_path'), str) else None)
                    or _latest_plan_file(data.get('cwd')))
            return (plan, 'ExitPlanMode') if plan else (None, 'ExitPlanMode-missing')
        if todos and data['tool_name'] == 'TodoWrite' and isinstance(tool_input.get('todos'), list):
            return json.dumps({'todos': tool_input['todos']}, ensure_ascii=False), 'TodoWrite'
        return None, None
    return json.dumps(data, ensure_ascii=False), 'json'


def tell_agent(text):
    """PostToolUse: hookSpecificOutput.additionalContext reaches the model."""
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': '[DOM project memory] ' + text}},
                     ensure_ascii=False))


def launch_identity():
    """(principal, role) of this host session: the same DOM_PRINCIPAL / DOM_ROLE the MCP server gets (T-02)."""
    import os
    principal = os.environ.get('DOM_PRINCIPAL') or None
    role = os.environ.get('DOM_ROLE') if os.environ.get('DOM_ROLE') in ('owner', 'author', 'reviewer') else 'owner'
    return principal, role


def session_of(raw):
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    sid = data.get('session_id') if isinstance(data, dict) else None
    return sid if isinstance(sid, str) and sid.strip() else None


def queue(directory, plan, source, kind, principal=None, session=None):
    inbox = directory / 'inbox'
    inbox.mkdir(parents=True, exist_ok=True)
    name = 'plan-%s-%s.json' % (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'), uuid.uuid4().hex[:6])
    tmp = inbox / (name + '.tmp')
    tmp.write_text(json.dumps({'op': 'ingest', 'plan': plan, 'source': source, 'hook': kind, 'principal': principal, 'session': session},
                              ensure_ascii=False), encoding='utf8')
    tmp.replace(inbox / name)
    return name


def main(argv=None):
    parser = argparse.ArgumentParser(prog='plan_hook')
    parser.add_argument('--directory', default=None, help='DOM run directory (default: <project>/.dom-memory/main)')
    parser.add_argument('--source', default='agent', choices=('agent', 'OMH'))
    parser.add_argument('--now', action='store_true', help='ingest directly; falls back to the inbox when the project is not started')
    parser.add_argument('--todos', action='store_true', help='also treat TodoWrite lists as the plan')
    args = parser.parse_args(argv)
    kind = None
    try:
        raw = sys.stdin.read()
        plan, kind = plan_from_hook(raw, args.todos)
        principal, role = launch_identity()
        session = session_of(raw)
        if plan is None:
            if kind == 'ExitPlanMode-missing':  # never silent (F-33): the agent sends the plan itself
                print('plan_hook: approved plan not found in the hook payload', file=sys.stderr)
                tell_agent('The approved plan did not reach the memory automatically. Call dom_plan_ingest with the approved '
                           'plan as numbered items (1., 1.1, ...) and contexts for each item.')
            return 0
        sys.path.insert(0, str(ROOT))
        if args.directory:
            directory = Path(args.directory).resolve()
        else:
            from mockup.paths import plugin_directory
            directory = plugin_directory()
        if args.now:
            from mockup.admission import Service
            svc = Service(directory, profile='lite', role=role, principal=principal)
            if not svc.store.path.exists():
                # First task of this project: no memory yet, the plan itself is the request
                # (a host plugin needs no init step; the prompt hook does the same with the prompt).
                directory.mkdir(parents=True, exist_ok=True)
                svc.start(request=plan)
            result = svc.plan_ingest(plan, args.source, session=session)
            if result.get('status') in ('PLAN_INGESTED', 'PLAN_UNCHANGED'):
                print(json.dumps({'status': result['status'], 'revision': result.get('source_revision')}), file=sys.stderr)
                if kind == 'ExitPlanMode':
                    tell_agent('The approved plan was saved as revision %s (%d items). Check it with dom_plan_tree; '
                               'add the context of each item with dom_plan_ingest(contexts=...) if it is missing.'
                               % (result.get('source_revision'), len(result.get('tasks') or [])))
                return 0
        job = queue(directory, plan, args.source, kind, principal, session)
        print(json.dumps({'status': 'QUEUED', 'job': job}), file=sys.stderr)
        if kind == 'ExitPlanMode':
            tell_agent('The approved plan was queued (%s); dom_plan_status takes it in.' % job)
    except Exception as e:  # never block the agent, never fail silently (F-33)
        print('plan_hook: %s' % type(e).__name__, file=sys.stderr)
        if kind == 'ExitPlanMode':
            tell_agent('Saving the approved plan failed (%s). Call dom_plan_ingest with the approved plan as numbered '
                       'items (1., 1.1, ...) and contexts for each item.' % type(e).__name__)
    return 0


if __name__ == '__main__':
    sys.exit(main())
