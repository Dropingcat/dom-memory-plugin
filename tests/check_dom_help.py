"""Check the dom_help MCP tool (contract 4.3).

dom_help is a read-only table lookup in ``HELP_DATA.py``: it must be registered on
every layer (the six registration points of TDOM-35), must answer for every tool
that has a help entry, and must refuse an unknown or missing tool with a typed
status instead of an internal error.

The real ``mcp`` package is an optional runtime dependency (absent on a bare
interpreter), so a minimal typed stand-in is installed before importing
``mockup.server`` -- the same approach as ``check_help_data.py``. Only the
registry tables and the pure dispatch path are used; no server is started and no
ledger is opened.

Run: python -B tests/check_dom_help.py
"""
from __future__ import annotations

import json
import sys
import types as pytypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPENCODE = ROOT / 'opencode'
sys.path.insert(0, str(OPENCODE))

TOOL = 'dom_help'
FIELDS = ('usage', 'preconditions', 'postconditions', 'common_errors', 'help')


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


class ReadOnlyService:
    """The handler must not touch the ledger, identity or review policy.

    Only ``profile`` is a real attribute; any other access is a failure.
    """

    def __init__(self, profile='lite'):
        self.profile = profile

    def __getattr__(self, name):
        raise AssertionError('dom_help touched service.%s' % name)


def check_registration() -> None:
    # 1. Tool documentation drives LITE_TOOLS, compose_description and input_schema.
    assert TOOL in claim_entry_mod.TOOL_DOCS, sorted(claim_entry_mod.TOOL_DOCS)
    assert TOOL in claim_entry_mod.LITE_TOOL_NAMES, claim_entry_mod.LITE_TOOL_NAMES

    # 2. The six registration points: visible tool sets (both profiles), exact
    #    signature, routing handler, response record and response statuses.
    assert TOOL in server_mod.LITE_TOOL_NAMES, sorted(server_mod.LITE_TOOL_NAMES)
    assert TOOL in server_mod.STRICT_TOOL_NAMES, sorted(server_mod.STRICT_TOOL_NAMES)
    assert TOOL in server_mod.ALL_TOOL_NAMES, sorted(server_mod.ALL_TOOL_NAMES)
    assert TOOL in server_mod.SIGNATURE, sorted(server_mod.SIGNATURE)
    assert TOOL in server_mod.HANDLERS, sorted(server_mod.HANDLERS)
    assert TOOL in public_dto.LITE_RESPONSES, sorted(public_dto.LITE_RESPONSES)
    assert public_dto.LITE_RESPONSES[TOOL] == ('ToolHelp', ('OK',)), public_dto.LITE_RESPONSES[TOOL]
    assert 'ToolHelp' in public_dto.RECORDS, sorted(public_dto.RECORDS)
    assert 'HelpError' in public_dto.RECORDS, sorted(public_dto.RECORDS)

    # 3. Advertised in both profiles with the documented argument.
    for profile in ('lite', 'strict'):
        tools = {item.name: item for item in server_mod.tools_for(profile)}
        assert TOOL in tools, (profile, sorted(tools))
        schema = tools[TOOL].inputSchema
        assert schema['type'] == 'object' and schema['additionalProperties'] is False, schema
        # BUG-1: tool is optional in the schema; otherwise OpenCode rejects the
        # call before the handler can answer with Russian help.
        assert schema['required'] == [], schema
        assert schema['properties']['tool']['type'] == 'string', schema
        assert 'help' in tools[TOOL].description.lower(), tools[TOOL].description

    # 4. Argument binding: ``tool`` is optional at the transport on purpose (so the
    #    handler can carry a hint and help), but an unknown key is still refused.
    required, optional = server_mod.SIGNATURE[TOOL]
    assert required == {}, required
    assert optional == {'tool': str}, optional
    try:
        server_mod.dispatch(ReadOnlyService(), TOOL, {'tool': 'dom_claim', 'principal': 'me'})
    except server_mod.ToolInput as error:
        assert str(error) == 'INVALID_ARGUMENTS', error
    else:
        raise AssertionError('an unknown argument must be refused')


def check_known_tool() -> None:
    result = server_mod.dispatch(ReadOnlyService(), TOOL, {'tool': 'dom_claim'})
    assert result['status'] == 'OK', result
    assert result['tool'] == 'dom_claim', result
    entry = HELP_DATA['dom_claim']
    for field in FIELDS:
        assert field in result, (field, sorted(result))
        assert result[field] == entry[field], field
    public = public_dto.project_tool(TOOL, result, scope={'main'}, profile='lite')
    assert public == result, (public, result)
    json.dumps(public, ensure_ascii=True, sort_keys=True)

    # The same answer in the strict profile (the tool is advertised there too).
    strict = server_mod.dispatch(ReadOnlyService('strict'), TOOL, {'tool': 'dom_claim'})
    assert strict == result, strict


def check_unknown_tool() -> None:
    result = server_mod.dispatch(ReadOnlyService(), TOOL, {'tool': 'dom_not_a_tool'})
    assert result['status'] == 'INVALID_FIELD', result
    assert result.get('field') == 'tool', result
    assert 'dom_not_a_tool' in result.get('hint', ''), result
    assert result.get('help'), result
    # The refusal keeps the available names, so the caller can choose one.
    assert 'dom_claim' in result.get('help', ''), result
    public = public_dto.project_tool(TOOL, result, scope={'main'}, profile='lite')
    assert public['status'] == 'INVALID_FIELD', public
    assert public.get('field') == 'tool', public


def check_missing_tool() -> None:
    for arguments in ({}, {'tool': ''}):
        result = server_mod.dispatch(ReadOnlyService(), TOOL, arguments)
        assert result['status'] == 'INVALID_ARGUMENTS', (arguments, result)
        assert result.get('field') == 'tool', (arguments, result)
        assert result.get('help'), (arguments, result)
        public = public_dto.project_tool(TOOL, result, scope={'main'}, profile='lite')
        assert public['status'] == 'INVALID_ARGUMENTS', (arguments, public)

    # The handler itself never raises: guarded() turns the refusal into a dict.
    direct = server_mod.handle_dom_help(None)
    assert direct['status'] == 'INVALID_ARGUMENTS', direct
    assert server_mod.handle_dom_help(['dom_claim'])['status'] == 'INVALID_ARGUMENTS'
    assert server_mod.handle_dom_help('dom_find')['status'] == 'OK'


def check_every_entry_answers() -> None:
    assert len(HELP_DATA) > 0, 'HELP_DATA is empty'
    tables = (
        {tool.name for tool in server_mod.TOOLS},
        {tool.name for tool in server_mod.LITE_TOOLS},
        set(server_mod.STRICT_TOOL_NAMES),
        set(server_mod.LITE_TOOL_NAMES),
        set(server_mod.SIGNATURE),
        set(server_mod.HANDLERS),
        set(claim_entry_mod.TOOL_DOCS),
    )
    registered = set().union(*tables)
    assert not missing_tools(registered), missing_tools(registered)
    assert set(HELP_DATA) >= registered, sorted(registered - set(HELP_DATA))

    for name in sorted(HELP_DATA):
        result = server_mod.dispatch(ReadOnlyService(), TOOL, {'tool': name})
        assert result['status'] == 'OK', (name, result)
        assert result['tool'] == name, (name, result)
        for field in FIELDS:
            assert result[field] == HELP_DATA[name][field], (name, field)
        public = public_dto.project_tool(TOOL, result, scope={'main'}, profile='lite')
        assert public == result, (name, public, result)


def main() -> int:
    check_registration()
    check_known_tool()
    check_unknown_tool()
    check_missing_tool()
    check_every_entry_answers()
    print('PASS check_dom_help: dom_help registered in TOOL_DOCS/LITE+STRICT tools/'
          'SIGNATURE/HANDLERS/LITE_RESPONSES/RECORDS; %d help entries reachable; '
          'unknown -> INVALID_FIELD, missing -> INVALID_ARGUMENTS; read-only dispatch'
          % len(HELP_DATA))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
