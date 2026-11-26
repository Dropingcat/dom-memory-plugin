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

Plan-file consumption (OpenCode context hook): ``--plan-file <path>`` marks the
ingest as coming from a plan file.  The hook then (1) refuses to overwrite a
newer plan revision with a stale file (the file is older than the last plan
change -> queued, not written), (2) passes the current revision as
``base_revision`` so a concurrent change answers PLAN_CONFLICT instead of a
silent overwrite, and (3) writes a consumption stamp ``<file>.dom.json``
(session, principal, revision, digest) next to the file so another session
does not pick it up as its own plan.

Discussion context (stage 2a+2b): a host that knows the conversation that
produced the plan sends it as ``dom_discussion`` in the payload (OpenCode: the
context hook passes the transcript text).  Without it, a Claude Code payload's
``transcript_path`` is read.  The discussion is distilled into per-item contexts
(``mockup.plan_context``) and sent with the plan, so a header claim carries not
only its title but the reason it exists.  Distillation never blocks the plan:
on any failure the plan is ingested without contexts and the agent is asked for
them, as before.
"""
import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


APPROVED_MARK = '## Approved Plan:'
PLAN_FILE_KEYS = ('filePath', 'file_path', 'planFilePath', 'plan_file_path', 'planFile', 'plan_file')
PLAN_FILE_MAX_AGE = 15 * 60  # only a plan file written just now is the plan just approved
DISCUSSION_KEYS = ('dom_discussion', 'discussion', 'plan_discussion')
DISCUSSION_MAX_CHARS = 24000  # the plan discussion, not the whole session history
DISCUSSION_MAX_MESSAGES = 80


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


def _blocks_text(content):
    """Claude content blocks -> their text (tool_use/tool_result are not discussion)."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ''
    parts = [block['text'] for block in content
             if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str)]
    return '\n'.join(parts).strip()


def _from_transcript_discussion(path):
    """Claude Code transcript (JSONL) -> the last user/assistant text, role-marked."""
    try:
        p = Path(path).expanduser()
        if not p.is_file():
            return None
        lines = p.read_text(encoding='utf8', errors='replace').splitlines()[-800:]
    except (OSError, ValueError):
        return None
    messages = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get('message') if isinstance(entry, dict) else None
        if not isinstance(message, dict) or message.get('role') not in ('user', 'assistant'):
            continue
        text = _blocks_text(message.get('content'))
        if text:
            messages.append(('User: ' if message['role'] == 'user' else 'Assistant: ') + text)
    if not messages:
        return None
    kept, total = [], 0
    for message in reversed(messages[-DISCUSSION_MAX_MESSAGES:]):
        if kept and total + len(message) + 2 > DISCUSSION_MAX_CHARS:
            break
        if not kept and len(message) > DISCUSSION_MAX_CHARS:
            message = message[-DISCUSSION_MAX_CHARS:]  # the newest message is the plan itself
        kept.append(message)
        total += len(message) + 2
    return '\n\n'.join(reversed(kept))


def discussion_from_hook(raw):
    """The conversation that produced the plan: the host's text or its transcript."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    for key in DISCUSSION_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    path = data.get('transcript_path')
    return _from_transcript_discussion(path) if isinstance(path, str) else None


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


def queue(directory, plan, source, kind, principal=None, session=None, contexts=None):
    inbox = directory / 'inbox'
    inbox.mkdir(parents=True, exist_ok=True)
    name = 'plan-%s-%s.json' % (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'), uuid.uuid4().hex[:6])
    tmp = inbox / (name + '.tmp')
    spec = {'op': 'ingest', 'plan': plan, 'source': source, 'hook': kind, 'principal': principal, 'session': session}
    if contexts:
        spec['contexts'] = contexts  # a queued plan keeps the context it was queued with
    tmp.write_text(json.dumps(spec, ensure_ascii=False), encoding='utf8')
    tmp.replace(inbox / name)
    try:  # 2d: a queued plan leaves a trace in the run log, not only on stderr
        from mockup.runlog import log_record
        log_record(directory, {'event': 'queued', 'job': name, 'hook': kind, 'source': source,
                               'session': session, 'principal': principal, 'contexts': len(contexts or {})})
    except Exception:
        pass  # the job is what matters; the log is a trace
    return name


# ---------------------------------------------- plan-file consumption stamp --
def stamp_path(plan_file):
    """Sidecar next to the plan file: ``<file>.dom.json`` (host-level, survives restarts)."""
    return Path(plan_file).with_name(Path(plan_file).name + '.dom.json')


def read_stamp(plan_file):
    try:
        data = json.loads(stamp_path(plan_file).read_text(encoding='utf8'))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_stamp(plan_file, session, principal, revision, digest):
    """Record who consumed this plan file and into which revision."""
    stamp = {'file': str(Path(plan_file).resolve()), 'session': session, 'principal': principal,
             'revision': revision, 'digest': digest, 'at': datetime.now(timezone.utc).isoformat()}
    path = stamp_path(plan_file)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(stamp, ensure_ascii=False), encoding='utf8')
    tmp.replace(path)


def file_digest(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def current_revision(svc):
    """Current source_revision of the 'agent' plan, or None when no plan is known."""
    return plan_source_info(svc).get('source_revision')


def plan_source_info(svc):
    """The 'agent' plan source: {source_revision, last_changed_at} (bad signal -> empty dict)."""
    try:
        status = svc.plan_status(1)
        for s in status.get('sources') or []:
            if s.get('source') == 'agent':
                return {k: s.get(k) for k in ('source_revision', 'last_changed_at')}
    except Exception:
        pass
    return {}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='plan_hook')
    parser.add_argument('--directory', default=None, help='DOM run directory (default: <project>/.dom-memory/main)')
    parser.add_argument('--source', default='agent', choices=('agent', 'OMH'))
    parser.add_argument('--now', action='store_true', help='ingest directly; falls back to the inbox when the project is not started')
    parser.add_argument('--todos', action='store_true', help='also treat TodoWrite lists as the plan')
    parser.add_argument('--plan-file', default=None, help='plan file path (OpenCode context hook): enables the consumption stamp and stale-file protection')
    args = parser.parse_args(argv)
    kind = None
    try:
        raw = sys.stdin.read()
        plan, kind = plan_from_hook(raw, args.todos)
        principal, role = launch_identity()
        session = session_of(raw) or (os.environ.get('DOM_SESSION') or None)
        if session:  # L4: the host hook's sessionID is the session identity here
            os.environ['DOM_SESSION'] = session
            os.environ['DOM_SESSION_SOURCE'] = 'hook'
            os.environ['DOM_ORIGIN'] = 'hook'
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
        # 2a+2b: the plan file is the distillation, the discussion is the reason.
        # A distillation failure must never cost the plan itself.
        contexts = {}
        discussion = discussion_from_hook(raw)
        if discussion:
            try:
                from mockup.plan_context import distill_contexts
                contexts = distill_contexts(plan, discussion)
            except Exception as e:
                print('plan_hook: context distillation skipped (%s)' % type(e).__name__, file=sys.stderr)
        if args.now:
            from mockup.admission import Service
            svc = Service(directory, profile='lite', role=role, principal=principal)
            if not svc.store.path.exists():
                # First task of this project: no memory yet, the plan itself is the request
                # (a host plugin needs no init step; the prompt hook does the same with the prompt).
                directory.mkdir(parents=True, exist_ok=True)
                svc.start(request=plan)
            stale = None
            if args.plan_file:
                # B+D: never let a stale plan file silently overwrite a newer plan revision.
                # The file must be at least as fresh as the last plan change, and it must
                # not already be consumed by another session (a foreign plan is queued, not written).
                stamp = read_stamp(args.plan_file)
                if stamp and stamp.get('session') and stamp.get('session') != session:
                    stale = 'consumed by another session (%s)' % (stamp.get('session') or '?')
                else:
                    import time
                    mtime = Path(args.plan_file).stat().st_mtime
                    last_changed = plan_source_info(svc).get('last_changed_at')
                    if last_changed:
                        try:
                            changed_epoch = datetime.fromisoformat(last_changed).timestamp()
                            if mtime < changed_epoch - 1:  # 1 s slack for filesystem clocks
                                stale = 'plan file is older than the last plan change (%s)' % last_changed
                        except (ValueError, TypeError):
                            stale = None
            base_revision = current_revision(svc)  # B+D: every hook ingest is a based-on write
            if stale:
                job = queue(directory, plan, args.source, kind, principal, session, contexts)
                print(json.dumps({'status': 'QUEUED_STALE', 'job': job, 'reason': stale}), file=sys.stderr)
                if kind == 'ExitPlanMode':
                    tell_agent('The approved plan file was not written into the memory: %s. It was queued; '
                               'read the current plan with dom_plan_tree and send the merged plan with dom_plan_ingest '
                               '(base_revision=<current revision>).' % stale)
                return 0
            result = svc.plan_ingest(plan, args.source, session=session, base_revision=base_revision, contexts=contexts or None)
            if result.get('status') == 'PLAN_CONFLICT':
                # B+D: a concurrent change is never silently overwritten by the hook.
                job = queue(directory, plan, args.source, kind, principal, session, contexts)
                print(json.dumps({'status': 'QUEUED_CONFLICT', 'job': job,
                                  'base_revision': result.get('base_revision'), 'current_revision': result.get('current_revision'),
                                  'changed_by': result.get('changed_by'), 'item_paths': result.get('item_paths')}), file=sys.stderr)
                if kind == 'ExitPlanMode':
                    tell_agent('The approved plan was not written: the plan changed since revision %s (by %s, items %s). '
                               'It was queued. Read the current plan with dom_plan_tree, merge, and send it with '
                               'dom_plan_ingest(base_revision=<current revision>).'
                               % (result.get('base_revision'), result.get('changed_by') or '?', ', '.join(result.get('item_paths') or [])))
                return 0
            if result.get('status') in ('PLAN_INGESTED', 'PLAN_UNCHANGED'):
                print(json.dumps({'status': result['status'], 'revision': result.get('source_revision')}), file=sys.stderr)
                if args.plan_file:  # B+D: the consumption stamp binds this file to this session/project
                    try:
                        write_stamp(args.plan_file, session, principal, result.get('source_revision'), file_digest(args.plan_file))
                    except OSError as e:
                        print('plan_hook: stamp %s' % type(e).__name__, file=sys.stderr)
                if kind == 'ExitPlanMode':
                    saved = ('The approved plan was saved as revision %s (%d items'
                             % (result.get('source_revision'), len(result.get('tasks') or [])))
                    if contexts:
                        saved += ', %d with the discussion context' % len(contexts)
                    saved += ('). Check it with dom_plan_tree; add the context of each item with '
                              'dom_plan_ingest(contexts=...) if it is missing.')
                    tell_agent(saved)
                return 0
        job = queue(directory, plan, args.source, kind, principal, session, contexts)
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
