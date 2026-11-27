"""Check that every refusal path in the DOM pipeline carries a non-empty ``help``.

    python -B tests/check_error_help.py

Three layers are checked:

  1. Static coverage of ``Denied(...)`` calls in the four pipeline files
     (``claim_entry.py``, ``admission.py``, ``server.py``, ``public_dto.py``):
     how many pass ``help=`` explicitly, how many leave it to ``guarded()`` or to
     the ``_denied_for`` factory (warnings, not failures), and whether any call
     passes an explicitly empty ``help`` (a real failure: ``guarded`` only fills a
     missing key, an empty string would reach the caller).

  2. Every literal status used in a ``Denied(...)`` call resolves, through
     ``public_dto.error_help``, to a non-empty actionable help text (both
     profiles).  That is the guarantee the pipeline makes for a raised refusal.

  3. Integration triggers: real refusals from ``Service`` (missing request,
     invalid argument) and a real ``GATE_NO_READ`` refusal from the read-before-
     write gate.  Each is checked both as the handler dict and after
     ``public_dto.project_tool``, so the public answer also has ``help``.

Throw-away run directories live under the repository runtime root and are removed.
"""
from __future__ import annotations

import ast
import os
import shutil
import sys
import types as pytypes
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPENCODE = ROOT / 'opencode'
MOCKUP = OPENCODE / 'mockup'
sys.path.insert(0, str(OPENCODE))

DENIED_FILES = ('claim_entry.py', 'admission.py', 'server.py', 'public_dto.py')
HELP_PREFIX = 'Тебе НУЖНО:'
HELP_LIMIT = 500


def install_mcp_stub() -> None:
    """Minimal ``mcp`` surface used at import time by mockup.server."""
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
            pass

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
from mockup.admission import Service  # noqa: E402
from mockup.paths import runtime_root  # noqa: E402


# --------------------------------------------------------------- static pass --

def denied_calls(path: Path):
    """Every ``Call`` whose callee is the bare name ``Denied``."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == 'Denied']


def literal_statuses(call: ast.Call) -> list[str]:
    """String statuses passed as the first argument (handles ``A if x else B``)."""
    if not call.args:
        return []
    first = call.args[0]
    values = []
    for candidate in (first.body, first.orelse) if isinstance(first, ast.IfExp) else (first,):
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
            values.append(candidate.value)
    return values


def static_report() -> dict:
    total = with_help = empty_help = 0
    missing_by_file: dict[str, int] = {}
    warnings: list[str] = []
    problems: list[str] = []
    statuses: set[str] = set()
    dynamic: list[str] = []

    for name in DENIED_FILES:
        path = MOCKUP / name
        if not path.exists():
            problems.append('missing source file: %s' % path)
            continue
        calls = denied_calls(path)
        for call in calls:
            total += 1
            keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
            where = '%s:%d' % (name, call.lineno)
            if 'help' not in keywords:
                missing_by_file[name] = missing_by_file.get(name, 0) + 1
            else:
                with_help += 1
                value = keywords['help']
                if isinstance(value, ast.Constant) and (
                        value.value is None
                        or (isinstance(value.value, str) and not value.value.strip())):
                    empty_help += 1
                    problems.append('%s: Denied(...) has an explicitly empty help=' % where)
            codes = literal_statuses(call)
            if not codes:
                dynamic.append(where)
            statuses.update(codes)

    for helper in ('error_help', 'invalid_arguments', 'invalid_field'):
        if not callable(getattr(public_dto, helper, None)):
            problems.append('public_dto.%s is missing (no shared refusal helper)' % helper)
    default_help = getattr(public_dto, 'DEFAULT_HELP', '')
    if not isinstance(default_help, str) or not default_help.strip():
        problems.append('public_dto.DEFAULT_HELP is empty')

    # Every raised status must resolve to non-empty, actionable help in both
    # profiles: this is exactly what ``guarded``/``_refusal`` attach.
    unresolved = 0
    for status in sorted(statuses):
        for profile in ('lite', 'strict'):
            meta = public_dto.error_help(status, profile)
            text = (meta.get('help') or '').strip()
            if not text:
                problems.append('%s: error_help(%r, %r) returns no help' % ('static', status, profile))
            elif len(text) > HELP_LIMIT:
                problems.append('%s: help for %r is %d chars (limit %d)'
                                % ('static', status, len(text), HELP_LIMIT))
            elif not text.startswith(HELP_PREFIX):
                problems.append('%s: help for %r does not start with %r'
                                % ('static', status, HELP_PREFIX))
        if status not in public_dto.KNOWN_REFUSALS:
            unresolved += 1
            warnings.append('status %r is not in public_dto.KNOWN_REFUSALS '
                            '(DEFAULT_HELP is used as a last resort)' % status)

    notes = ['%s: %d Denied(...) call(s) without help= (guarded() or the '
             '_denied_for factory fills it from the status table)'
             % (name, missing_by_file[name]) for name in sorted(missing_by_file)]
    notes += ['%s: Denied(...) status is not a literal (not statically checked)' % where
              for where in dynamic]

    missing_help = sum(missing_by_file.values())
    return {
        'total': total,
        'with_help': with_help,
        'empty_help': empty_help,
        'missing_help': missing_help,
        'warnings': warnings,
        'warning_count': missing_help + len(dynamic) + unresolved,
        'notes': notes,
        'problems': problems,
        'statuses': len(statuses),
    }


# ---------------------------------------------------------- integration pass --

def _project(name, result, service):
    return public_dto.project_tool(name, result,
                                  scope=service.context.branches,
                                  profile=service.profile)


def _assert_help(where: str, result: dict) -> None:
    text = result.get('help')
    assert isinstance(text, str) and text.strip(), '%s: no help in %r' % (where, result)
    assert text.startswith(HELP_PREFIX), '%s: help does not start with %r: %r' % (where, HELP_PREFIX, text)
    assert len(text) <= HELP_LIMIT, '%s: help is %d chars' % (where, len(text))


def check_integration(token: str) -> None:
    saved = {key: os.environ.get(key)
             for key in ('DOM_SESSION', 'DOM_SESSION_SOURCE', 'DOM_ORIGIN')}
    roots = []
    try:
        os.environ['DOM_SESSION'] = 'ses_error_help'
        os.environ['DOM_SESSION_SOURCE'] = 'hook'
        os.environ['DOM_ORIGIN'] = 'hook'

        root = runtime_root() / ('dom-error-help-%s' % uuid.uuid4().hex[:12])
        root.mkdir(parents=True)
        roots.append(root)
        svc = Service(root, profile='lite', review_mode='manual')

        # (1) A typed refusal raised before the project exists: missing request.
        started_bad = svc.start()
        assert started_bad['status'] == 'REQUEST_REQUIRED', started_bad
        _assert_help('dom_start missing request', started_bad)
        _assert_help('dom_start missing request (public)',
                     _project('dom_start', started_bad, svc))

        # (1b) The same refusal through the real MCP argument binding: an unknown
        #      key is refused by the transport, whose payload carries help.
        try:
            server_mod.dispatch(svc, 'dom_help', {'tool': 'dom_claim', 'principal': 'me'})
        except server_mod.ToolInput as error:
            assert str(error) == 'INVALID_ARGUMENTS', error
        else:
            raise AssertionError('an unknown argument must be refused')
        transport = public_dto.invalid_arguments(
            'dom_help', help_text=server_mod.argument_help('dom_help'))
        _assert_help('dom_help transport INVALID_ARGUMENTS', transport)
        assert 'dom_help' in transport['help'], transport

        # (2) A real project so argument refusals happen after the gate check.
        started = svc.start(request='error help check request %s' % token)
        assert started['status'] == 'STARTED', started
        root_id = started['root']['request_claim_id']

        refused = svc.find(limit=0)
        assert refused['status'] == 'INVALID_FIELD', refused
        assert refused.get('field') == 'limit', refused
        _assert_help('dom_find(limit=0)', refused)
        _assert_help('dom_find(limit=0) (public)', _project('dom_find', refused, svc))

        # The same invalid argument through dispatch -> registered handler.
        via_handler = server_mod.dispatch(svc, 'dom_find', {'limit': 0})
        assert via_handler['status'] == 'INVALID_FIELD', via_handler
        _assert_help('dispatch dom_find(limit=0)', via_handler)

        # (3) GATE_NO_READ: a basis claim without a read receipt in this session.
        gated = svc.claim(branch='main', type='text',
                          text='gated error help %s' % token, basis=[root_id])
        assert gated['status'] == 'GATE_NO_READ', gated
        assert gated.get('hint'), gated
        _assert_help('GATE_NO_READ (handler)', gated)
        _assert_help('GATE_NO_READ (public)', _project('dom_claim', gated, svc))
        print('  PASS integration: REQUEST_REQUIRED, INVALID_FIELD and GATE_NO_READ '
              'carry help in handler and public DTO')
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for path in roots:
            shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    report = static_report()
    print('Denied coverage: %d/%d Denied calls have help, %d have empty help, %d warnings'
          % (report['with_help'], report['total'], report['empty_help'], report['warning_count']))
    for warning in report['warnings']:
        print('  WARN', warning)
    for note in report['notes']:
        print('  WARN', note)
    if report['problems']:
        for problem in report['problems']:
            print('  FAIL', problem)
        print('\nFAIL check_error_help: %d problem(s)' % len(report['problems']))
        return 1

    check_integration(uuid.uuid4().hex[:12])
    print('PASS check_error_help: %d/%d Denied calls declare help, %d statuses resolve to '
          'non-empty help in both profiles, %d warnings'
          % (report['with_help'], report['total'], report['statuses'], report['warning_count']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
