"""Check the Python-side tool error tracker (``mockup/tracker.py``).

    python -B tests/check_tracker.py

Covers the unit contract (one line per record, append, caps, scrubbing,
fail-closed, circuit breaker) and the two integration seams that ``server.py``
uses: a projected refusal and a transport fault.  Also checks static parity with
``observability.js`` / ``monitor/server.py``: same directory, same file name
shape, same 64 KiB line budget.
"""
from __future__ import annotations

import anyio
import json
import os
import re
import subprocess
import sys
import tempfile
import types as pytypes
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPENCODE = ROOT / 'opencode'
sys.path.insert(0, str(OPENCODE))

NAME_RE = re.compile(r'hooks-\d{4}-\d{2}-\d{2}-\d+\.jsonl$')


def install_mcp_stub() -> None:
    """Minimal ``mcp`` surface used at import time by mockup.server (see check_error_help.py)."""
    mcp = pytypes.ModuleType('mcp')
    types_mod = pytypes.ModuleType('mcp.types')

    class Tool:
        def __init__(self, name=None, description=None, inputSchema=None):
            self.name = name
            self.description = description
            self.inputSchema = inputSchema

    class TextContent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class CallToolResult:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    types_mod.Tool = Tool
    types_mod.TextContent = TextContent
    types_mod.CallToolResult = CallToolResult
    mcp.types = types_mod

    server_pkg = pytypes.ModuleType('mcp.server')
    server_pkg.__path__ = []
    lowlevel = pytypes.ModuleType('mcp.server.lowlevel')

    class Server:
        def __init__(self, *args, **kwargs):
            self.list_tools_fn = None
            self.call_tool_fn = None

        def list_tools(self):
            def register(fn):
                self.list_tools_fn = fn
                return fn
            return register

        def call_tool(self, validate_input=False):
            def register(fn):
                self.call_tool_fn = fn
                return fn
            return register

    class NotificationOptions:
        pass

    lowlevel.Server = Server
    lowlevel.NotificationOptions = NotificationOptions
    models = pytypes.ModuleType('mcp.server.models')

    class InitializationOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    models.InitializationOptions = InitializationOptions
    stdio = pytypes.ModuleType('mcp.server.stdio')

    async def stdio_server():
        return None

    stdio.stdio_server = stdio_server

    for name, module in {
        'mcp': mcp,
        'mcp.types': types_mod,
        'mcp.server': server_pkg,
        'mcp.server.lowlevel': lowlevel,
        'mcp.server.models': models,
        'mcp.server.stdio': stdio,
    }.items():
        sys.modules[name] = module


install_mcp_stub()

from mockup import public_dto  # noqa: E402
from mockup import server as server_mod  # noqa: E402
from mockup import tracker  # noqa: E402
from mockup.admission import Service  # noqa: E402


# ------------------------------------------------------------------ helpers --

def files(directory: Path):
    return sorted(Path(directory).glob('hooks-*.jsonl'))


def lines(path: Path):
    return path.read_text(encoding='utf-8').splitlines()


def events(directory: Path):
    out = []
    for path in files(directory):
        for line in lines(path):
            out.append(json.loads(line))
    return out


def expect(condition, message):
    assert condition, message


def fresh(tmp: Path, name: str):
    tracker._reset()
    directory = tmp / name
    tracker.configure(directory=directory, force=True)
    return directory


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)

        # 1. one record -> one well-formed line -------------------------------
        obs = fresh(tmp, 'one')
        written = tracker.record(tracker.ToolError(
            tool='dom_claim', status='EMPTY_TEXT', hint='empty text',
            help='Тебе НУЖНО: 1) Передать непустой text. 2) Повторить.',
            session_id='sess-1', duration_ms=12))
        expect(written is True, 'record() must report a successful write')
        found = files(obs)
        expect(len(found) == 1, found)
        expect(NAME_RE.match(found[0].name), found[0].name)
        raw = found[0].read_text(encoding='utf-8')
        expect(raw.endswith('\n'), 'a record must end with a newline')
        body = lines(found[0])
        expect(len(body) == 1, body)
        event = json.loads(body[0])
        expect(event['ts'].endswith('Z'), event['ts'])
        expect(event['pid'] == os.getpid(), event['pid'])
        expect(event['hook'] == 'tool', event)
        expect(event['phase'] == 'after', event)
        expect(event['source'] == 'dom.mcp', event)
        expect(event['tool'] == 'dom_claim', event)
        expect(event['status'] == 'EMPTY_TEXT', event)
        expect(event['hint'] == 'empty text', event)
        expect(event['session_id'] == 'sess-1', event)
        expect(event['duration_ms'] == 12, event)
        expect(event['injected']['chars'] == len(event['help']), event)
        print('  PASS unit: one record -> one JSONL line')

        # 2. multiple records append to the same file -------------------------
        tracker.record(tracker.ToolError(tool='dom_find', status='INVALID_FIELD',
                                         hint='limit', help='fix limit'))
        expect(len(files(obs)) == 1, files(obs))
        expect(len(lines(files(obs)[0])) == 2, lines(files(obs)[0]))
        print('  PASS unit: records append to the same file')

        # 3. circuit breaker rotates at MAX_LINES -----------------------------
        rot = fresh(tmp, 'rot')
        saved_max = tracker.MAX_LINES
        try:
            tracker.MAX_LINES = 3
            for index in range(4):
                tracker.record(tracker.ToolError(tool='dom_find', status='INVALID_FIELD',
                                                 hint='n%d' % index))
        finally:
            tracker.MAX_LINES = saved_max
        parts = files(rot)
        expect(len(parts) == 2, [p.name for p in parts])
        counts = sorted(len(lines(p)) for p in parts)
        expect(counts == [1, 3], counts)
        expect(all(NAME_RE.match(p.name) for p in parts), [p.name for p in parts])
        print('  PASS unit: circuit breaker rotates after MAX_LINES')

        # 4. disabled -> no write, no directory -------------------------------
        off = tmp / 'off'
        tracker._reset()
        tracker.configure(directory=off, force=False)
        expect(tracker.enabled() is False, 'force=False must disable the tracker')
        expect(tracker.record(tracker.ToolError(tool='dom_claim', status='EMPTY_TEXT')) is False,
               'a disabled tracker must return False')
        expect(not off.exists(), 'a disabled tracker must not create the directory')
        print('  PASS unit: disabled tracker writes nothing')

        # 5. fail-closed on an unwritable path --------------------------------
        blocker = tmp / 'blocker'
        blocker.write_text('not a directory', encoding='utf-8')
        tracker._reset()
        tracker.configure(directory=blocker, force=True)
        before = tracker.statistics()['dropped']
        expect(tracker.record(tracker.ToolError(tool='dom_claim', status='EMPTY_TEXT')) is False,
               'a bad output path must return False')
        expect(tracker.statistics()['dropped'] == before + 1, tracker.statistics())
        print('  PASS unit: fail-closed on a bad output path')

        # 6. caps and secret scrubbing ---------------------------------------
        cap = fresh(tmp, 'caps')
        big = 'x' * 10_000
        tracker.record(tracker.ToolError(
            tool='dom_claim', status='INVALID_FIELD',
            hint='token=abc123 sk-ABCDEFGH1234 ghp_ABCDEFGHIJKLMNOPQRST ' + big,
            help='Bearer abc.def.ghi ' + big))
        event = events(cap)[0]
        expect(len(event['hint']) <= 1000, len(event['hint']))
        expect(len(event['help']) <= 500, len(event['help']))
        expect(event['hint'].endswith('\u2026') and event['help'].endswith('\u2026'), event)
        for secret in ('abc123', 'sk-', 'ghp_', 'Bearer abc.def.ghi'):
            expect(secret not in event['hint'] + event['help'], secret)
        expect('[REDACTED]' in event['hint'], event['hint'])
        expect('[REDACTED]' in event['help'], event['help'])
        print('  PASS unit: caps (1000/500) and secret scrubbing')

        # 7. static parity with observability.js / the monitor ----------------
        js = (OPENCODE / 'observability.js').read_text(encoding='utf-8')
        expect('hooks-${value}-${pid}.jsonl' in js, 'JS file-name pattern changed')
        expect('MAX_LINE_BYTES = 64 * 1024' in js, 'JS line budget changed')
        monitor = (OPENCODE / 'monitor' / 'server.py').read_text(encoding='utf-8')
        expect('hooks-*.jsonl' in monitor, 'the monitor glob changed')
        base = tracker._file_for(Path('.'), '2026-11-28', 0).name
        rotated = tracker._file_for(Path('.'), '2026-11-28', 1).name
        expect(NAME_RE.match(base) and NAME_RE.match(rotated), (base, rotated))
        expect(len(json.dumps(events(cap)[0])) <= tracker.MAX_LINE_BYTES, 'line budget')
        print('  PASS parity: directory, file-name shape, 64 KiB line budget')

        # 8. end-to-end: a real refusal becomes exactly one line --------------
        project = tmp / 'e2e'
        run_dir = project / '.dom-memory' / 'main'
        run_dir.mkdir(parents=True)
        previous_runtime = os.environ.get('DOM_RUNTIME_ROOT')
        os.environ['DOM_RUNTIME_ROOT'] = str(project / '.dom-memory')
        try:
            svc = Service(run_dir, profile='lite', review_mode='auto')
            started = svc.start(request='tracker e2e refusal')
            expect(started['status'] == 'STARTED', started)

            refusal_obs = fresh(tmp, 'e2e-obs')
            refused = svc.claim(branch='main', type='text', text='')
            expect(refused.get('status') == 'EMPTY_TEXT', refused)
            public = public_dto.project_tool('dom_claim', refused, scope=svc.context.branches, profile='lite')
            expect(public.get('status') == 'EMPTY_TEXT' and public.get('help'), public)
            server_mod._track_refusal('dom_claim', public, svc.session, 7)
            recorded = events(refusal_obs)
            expect(len(recorded) == 1, recorded)
            expect(recorded[0]['status'] == 'EMPTY_TEXT', recorded[0])
            expect(recorded[0]['help'] == public['help'], 'help must be byte-identical')
            expect(recorded[0]['session_id'] == svc.session, recorded[0])
            expect(recorded[0]['duration_ms'] == 7, recorded[0])

            # 8b. a successful call is silent --------------------------------
            found = svc.find(branch='main', limit=20)
            expect(found.get('status') == 'FOUND', found)
            success_public = public_dto.project_tool('dom_find', found, scope=svc.context.branches, profile='lite')
            expect(success_public.get('status') not in public_dto.KNOWN_REFUSALS, success_public)
            server_mod._track_refusal('dom_find', success_public, svc.session, 3)
            expect(len(events(refusal_obs)) == 1, 'a success must not add a line')
            print('  PASS e2e: refusal -> one line, success -> zero lines')

            # 8c. GATE_NO_READ also produces one line ------------------------
            gate_dir = project / '.dom-memory' / 'gate'
            gate_dir.mkdir(parents=True)
            gate_svc = Service(gate_dir, profile='lite', review_mode='manual')
            gate_started = gate_svc.start(request='tracker e2e gate')
            expect(gate_started['status'] == 'STARTED', gate_started)
            root_id = gate_started['root']['request_claim_id']
            gated = gate_svc.claim(branch='main', type='text', text='gated tracker',
                                   basis=[root_id])
            expect(gated.get('status') == 'GATE_NO_READ', gated)
            gate_public = public_dto.project_tool('dom_claim', gated, scope=gate_svc.context.branches,
                                                  profile='lite')
            gate_obs = fresh(tmp, 'gate-obs')
            server_mod._track_refusal('dom_claim', gate_public, gate_svc.session, 9)
            gate_event = events(gate_obs)
            expect(len(gate_event) == 1, gate_event)
            expect(gate_event[0]['status'] == 'GATE_NO_READ', gate_event[0])
            expect(gate_event[0]['help'] == gate_public['help'], gate_event)
            print('  PASS e2e: GATE_NO_READ -> one line with help')

            # 8d. the real call_tool closure, one line per refusal -----------
            async_obs = fresh(tmp, 'async-obs')
            server = server_mod.build_server(svc)
            with redirect_stderr(StringIO()):
                refused_result = anyio.run(server.call_tool_fn, 'dom_claim',
                                           {'branch': 'main', 'type': 'text', 'text': ''})
                ok_result = anyio.run(server.call_tool_fn, 'dom_find',
                                      {'branch': 'main', 'limit': 20})
                unknown_result = anyio.run(server.call_tool_fn, 'dom_nope', {})
                invalid_result = anyio.run(server.call_tool_fn, 'dom_find', {'limit': '20'})
            expect(refused_result.isError is False, refused_result.__dict__)
            expect(ok_result.isError is False, ok_result.__dict__)
            expect(unknown_result.isError is True, unknown_result.__dict__)
            expect(invalid_result.isError is True, invalid_result.__dict__)
            statuses = [event['status'] for event in events(async_obs)]
            expect(statuses == ['EMPTY_TEXT', 'UNKNOWN_TOOL', 'INVALID_ARGUMENTS'], statuses)
            print('  PASS e2e: real call_tool writes one line per refusal, none for success')
        finally:
            if previous_runtime is None:
                os.environ.pop('DOM_RUNTIME_ROOT', None)
            else:
                os.environ['DOM_RUNTIME_ROOT'] = previous_runtime

        # 9. transport fault is tracked with its type -------------------------
        transport = fresh(tmp, 'transport')
        server_mod._track_failure('dom_nope', 'UNKNOWN_TOOL', 'unknown tool',
                                  'Тебе НУЖНО: 1) Вызвать dom_describe.',
                                  'sess-2', 2, error_type='ToolInput')
        fault = events(transport)
        expect(len(fault) == 1, fault)
        expect(fault[0]['status'] == 'UNKNOWN_TOOL', fault[0])
        expect(fault[0]['error_type'] == 'ToolInput', fault[0])
        print('  PASS e2e: transport fault -> one line with error_type')

        # 10. no ledger coupling ---------------------------------------------
        source = Path(tracker.__file__).read_text(encoding='utf-8')
        expect('sqlite3' not in source, 'tracker must not open sqlite3')
        expect('import storage' not in source, 'tracker must not import storage')
        expect('import admission' not in source, 'tracker must not import admission')
        code = "import mockup.tracker, sys; print('mockup.storage' in sys.modules)"
        env = dict(os.environ, PYTHONPATH=str(OPENCODE))
        out = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True,
                             text=True, env=env)
        expect(out.returncode == 0, out.stderr)
        expect(out.stdout.strip() == 'False', out.stdout)
        print('  PASS static: no storage/ledger coupling at import time')

    print('PASS check_tracker')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
