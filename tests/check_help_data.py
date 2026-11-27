"""Check that HELP_DATA covers and describes every registered DOM MCP tool.

The real ``mcp`` package is an optional runtime dependency (absent on a bare
interpreter), so a minimal typed stand-in is installed before importing
``mockup.server`` -- the same approach as ``check_skill_mcp_registration.py``.
Only the registry tables are used; no server is started and no ledger is opened.

Run: python -B tests/check_help_data.py
"""
from __future__ import annotations

import re
import sys
import types as pytypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPENCODE = ROOT / 'opencode'
sys.path.insert(0, str(OPENCODE))

FIELDS = ('usage', 'preconditions', 'postconditions', 'common_errors', 'help')
HELP_LIMIT = 500
HELP_PREFIX = 'Тебе НУЖНО:'
CODE_SHAPE = re.compile(r'^[A-Z][A-Z0-9_]+$')


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

from HELP_DATA import HELP_DATA, missing_tools  # noqa: E402
from mockup import claim_entry as claim_entry_mod  # noqa: E402
from mockup import public_dto  # noqa: E402
from mockup import server as server_mod  # noqa: E402


def registry_tables() -> dict[str, set[str]]:
    """Every source of truth that declares a DOM tool, by table name."""
    return {
        'server.TOOLS': {tool.name for tool in server_mod.TOOLS},
        'server.LITE_TOOLS': {tool.name for tool in server_mod.LITE_TOOLS},
        'server.STRICT_TOOL_NAMES': set(server_mod.STRICT_TOOL_NAMES),
        'server.LITE_TOOL_NAMES': set(server_mod.LITE_TOOL_NAMES),
        'server.ALL_TOOL_NAMES': set(server_mod.ALL_TOOL_NAMES),
        'server.SIGNATURE': set(server_mod.SIGNATURE),
        'server.LITE_SIGNATURE': set(server_mod.LITE_SIGNATURE),
        'server.HANDLERS': set(server_mod.HANDLERS),
        'claim_entry.TOOL_DOCS': set(claim_entry_mod.TOOL_DOCS),
    }


def known_error_codes() -> set[str]:
    """Codes that really exist in the server: _HELP statuses + typed refusals.

    Help must not invent a code, so every common_errors[].code is checked
    against this set.  Only mockup/ sources are scanned (never HELP_DATA.py).
    """
    codes = set(public_dto._HELP)
    literal = re.compile(r"'([A-Z][A-Z0-9_]{2,})'")
    for path in sorted((OPENCODE / 'mockup').glob('*.py')):
        codes |= set(literal.findall(path.read_text(encoding='utf-8')))
    return codes


def check() -> list[str]:
    problems: list[str] = []

    tables = registry_tables()
    registered = set().union(*tables.values())

    for name, table in tables.items():
        absent = sorted(table - set(HELP_DATA))
        if absent:
            problems.append('%s: no HELP_DATA entry for %s' % (name, ', '.join(absent)))

    absent = missing_tools(registered)
    if absent:
        problems.append('missing_tools(registered) = %s' % (absent,))
    if missing_tools(['dom_claim', 'dom_not_a_tool']) != ['dom_not_a_tool']:
        problems.append('missing_tools() does not report an unknown name correctly')
    if missing_tools([]) != []:
        problems.append('missing_tools([]) must be []')

    codes = known_error_codes()
    usages: dict[str, str] = {}
    helps: dict[str, str] = {}

    for tool in sorted(HELP_DATA):
        entry = HELP_DATA[tool]
        if not isinstance(entry, dict):
            problems.append('%s: entry is not a dict' % tool)
            continue
        for field in FIELDS:
            if field not in entry:
                problems.append('%s: missing field %r' % (tool, field))
        if any(field not in entry for field in FIELDS):
            continue

        usage = entry['usage']
        if not isinstance(usage, str) or not usage.strip():
            problems.append('%s: usage is empty' % tool)
        elif not usage.startswith(tool + '('):
            problems.append("%s: usage must start with '%s(' got %r" % (tool, tool, usage))
        elif usage in usages:
            problems.append('%s: usage duplicates %s' % (tool, usages[usage]))
        else:
            usages[usage] = tool

        for field in ('preconditions', 'postconditions'):
            value = entry[field]
            if not isinstance(value, list) or not value:
                problems.append('%s: %s must be a non-empty list' % (tool, field))
                continue
            for item in value:
                if not isinstance(item, str) or not item.strip():
                    problems.append('%s: %s has an empty item' % (tool, field))

        errors = entry['common_errors']
        if not isinstance(errors, list) or not errors:
            problems.append('%s: common_errors must be a non-empty list' % tool)
        else:
            for index, error in enumerate(errors):
                where = '%s: common_errors[%d]' % (tool, index)
                if not isinstance(error, dict) or set(error) != {'code', 'cause', 'fix'}:
                    problems.append('%s must be {code, cause, fix}' % where)
                    continue
                for key in ('code', 'cause', 'fix'):
                    if not isinstance(error[key], str) or not error[key].strip():
                        problems.append('%s.%s is empty' % (where, key))
                code = error['code']
                if isinstance(code, str) and code.strip():
                    if not CODE_SHAPE.match(code):
                        problems.append('%s: code %r is not an upper-case code' % (where, code))
                    elif code not in codes:
                        problems.append('%s: code %r exists nowhere in mockup/' % (where, code))

        help_text = entry['help']
        if not isinstance(help_text, str) or not help_text.strip():
            problems.append('%s: help is empty' % tool)
        else:
            if len(help_text) > HELP_LIMIT:
                problems.append('%s: help is %d chars (limit %d)' % (tool, len(help_text), HELP_LIMIT))
            if not help_text.startswith(HELP_PREFIX):
                problems.append('%s: help must start with %r' % (tool, HELP_PREFIX))
            if '1)' not in help_text or '2)' not in help_text:
                problems.append('%s: help must contain numbered steps 1) and 2)' % tool)
            if help_text in helps:
                problems.append('%s: help duplicates %s' % (tool, helps[help_text]))
            else:
                helps[help_text] = tool

    return problems


def main() -> int:
    problems = check()
    if problems:
        for problem in problems:
            print('FAIL', problem)
        print('\nFAIL: %d problem(s)' % len(problems))
        return 1
    strict = len(server_mod.STRICT_TOOL_NAMES)
    lite = len(server_mod.LITE_TOOL_NAMES)
    print('PASS check_help_data: %d tools covered (%d strict, %d lite, union %d)'
          % (len(HELP_DATA), strict, lite, len(server_mod.ALL_TOOL_NAMES)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
