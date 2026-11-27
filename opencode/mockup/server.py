"""Real stdio MCP server over the synthetic DOM service.

Trust boundary: ``--profile``, ``--role`` and ``--simulation`` are *launch* arguments supplied by
the trusted local launcher (the test harness / demo).  No tool accepts a role,
principal, capability set or work ref override, so a model-facing caller cannot
promote itself.  This is a documented simulation adapter, not authentication:
it proves the protocol path and the domain gates, not host identity.

stdout carries MCP JSON-RPC frames only; diagnostics go to stderr / ``--log-file``.
"""
import argparse
import json
import sys
from pathlib import Path

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

from .admission import Denied, Service, guarded
from . import claim_entry as claim_entry_mod
from . import public_dto
from . import skill_select as skill_select_mod
# Usage help for every tool (contract 4.3).  The launcher and the check scripts
# put the plugin root on sys.path; a host tree synced without HELP_DATA.py still
# starts, and dom_help then reports that no tool is known (never an ImportError).
try:
    from HELP_DATA import HELP_DATA
except ImportError:  # pragma: no cover - packaging edge case, not a runtime path
    HELP_DATA = {}

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / 'runtime').resolve()
ROLES = ('author', 'helper', 'reviewer', 'owner')
# One product: 'prod' (alias 'lite').  'lab' (alias 'strict') is the
# fixed-identity laboratory stand of the older tests, not a second product.
PROFILES = ('prod', 'lab', 'lite', 'strict')
INSTRUCTIONS = ('Synthetic DOM/OMH laboratory. All evidence is simulated; no live '
                'OMH, Git or issuer calls. Identity is assigned at launch, never by a tool call.')
INSTRUCTIONS_LITE = ('DOM project memory. The SQLite ledger in this directory is real, append-only and hash-chained; '
                     'OMH/Git delivery targets are simulated. Start: dom_describe({}), dom_start({"request": "<the request, verbatim>"}), '
                     'dom_plan_status({}) and dom_plan_ingest for the plan; then dom_claim(plan_task=...) per statement, dom_find to look up, '
                     'dom_update for a new version, dom_annotate for comments/properties/archive, dom_context for "where does it come from". '
                     'Under a review policy records wait as candidates: dom_candidate, dom_accept. '
                     'Every refusal carries a hint and next_action. Identity and review policy are fixed at launch; no tool argument can change them.')


class ToolInput(Exception):
    """Bad MCP tool arguments. Reported as a structured transport error."""


TOOLS = [
    types.Tool(
        name='dom_init',
        description='Create/verify the work binding in the given run directory. Simulated.',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'external_work_ref': {'type': 'string'},
                                    'policy_digest': {'type': 'string'}},
                     'required': ['external_work_ref', 'policy_digest']}),
    types.Tool(
        name='dom_resume',
        description='Resume a branch: scoped snapshot + trace + outbox status + pending-work section in one '
                    'read-only call. pending: exact in-grant count + a metadata-only page (limit/after keyset).',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'branch': {'type': 'string'},
                                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500},
                                    'after': {'type': 'string'}},
                     'required': ['branch']}),
    types.Tool(
        name='dom_submit',
        description='Submit a restricted-JSON candidate package (claims or append events). Not admitted.',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'payload_json': {'type': 'string'}},
                     'required': ['payload_json']}),
    types.Tool(
        name='dom_review',
        description='Record a review of an exact candidate digest: decision approve (default) or reject '
                    '(reject needs a note). Read the candidate first with dom_candidate. A rejected candidate cannot be admitted.',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'candidate_id': {'type': 'string'},
                                    'intent_digest': {'type': 'string'},
                                    'decision': {'type': 'string', 'enum': ['approve', 'reject']},
                                    'note': {'type': 'string'}},
                     'required': ['candidate_id', 'intent_digest']}),
    types.Tool(
        name='dom_candidate',
        description='Read one candidate before reviewing it: decoded claim texts (up to 4000 chars each), metadata, '
                    'append events, branch, expected revision and review state. Read-only; needs the review capability.',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'candidate_id': {'type': 'string'}},
                     'required': ['candidate_id']}),
    types.Tool(
        name='dom_admit',
        description='Atomic admission of a reviewed candidate (facts, events, head, outbox).',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'candidate_id': {'type': 'string'},
                                    'review_id': {'type': 'string'},
                                    'idempotency_key': {'type': 'string'},
                                    'intent_digest': {'type': 'string'}},
                     'required': ['candidate_id', 'review_id', 'idempotency_key', 'intent_digest']}),
    types.Tool(
        name='dom_append',
        description='Record an append event batch (comment/link/property/applicability/archive/...) as a candidate. '
                    'It still needs dom_review + dom_admit; it is not an admission bypass.',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'payload_json': {'type': 'string'}},
                     'required': ['payload_json']}),
    types.Tool(
        name='dom_query',
        description='Scoped branch snapshot at the head revision or an exact historical revision.',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'branch': {'type': 'string'},
                                    'revision': {'type': 'integer', 'minimum': 0}},
                     'required': ['branch']}),
    types.Tool(
        name='dom_snapshot',
        description='Compatibility alias of dom_query (kept for existing clients; same result schema).',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'branch': {'type': 'string'},
                                    'revision': {'type': 'integer', 'minimum': 0}},
                     'required': ['branch']}),
    types.Tool(
        name='dom_trace',
        description='Coverage/stale/conflict/pending trace. Readiness only; never closes the owner task.',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'branch': {'type': 'string'}},
                     'required': []}),
    types.Tool(
        name='dom_export',
        description='Deterministic scoped YAML export to a server-fixed path inside the run directory.',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'branch': {'type': 'string'},
                                    'revision': {'type': 'integer', 'minimum': 0}},
                     'required': ['branch']}),
    types.Tool(
        name='dom_sync_status',
        description='Outbox status per intent (pending/attempt/ack/error/ambiguous). Read-only.',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'branch': {'type': 'string'}},
                     'required': ['branch']}),
    types.Tool(
        name='dom_sync',
        description='Deliver the synthetic outbox for a branch (YAML export + simulated OMH attach with read-back). '
                    'Requires the launch-time admit capability; returns SYNCED or SYNC_PENDING.',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'branch': {'type': 'string'}},
                     'required': ['branch']}),
    types.Tool(
        name='dom_recall',
        description='Replay a branch up to a branch-local event seq: active/archived claims, resolved conflicts, '
                    'related branches and the exact baseline range. Read-only.',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'branch': {'type': 'string'},
                                    'at_seq': {'type': 'integer', 'minimum': 0}},
                     'required': ['branch', 'at_seq']}),
    types.Tool(
        name='dom_plan_tree',
        description='Return the OMH plan hierarchy with per-task claim counts. Read-only.',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'root': {'type': 'string'},
                                    'depth': {'type': 'integer', 'minimum': 1, 'maximum': 10},
                                    'at_version': {'type': 'integer', 'minimum': 0},
                                    'require_fresh': {'type': 'boolean'}},
                     'required': ['root']}),
    types.Tool(
        name='dom_claim_versions',
        description='The replacement chain of one logical claim, oldest first, with derived version and current flag.',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'claim_id': {'type': 'string'}},
                     'required': ['claim_id']}),
    types.Tool(
        name='dom_diff',
        description='Compare current claims of two branches by logical claim id: only_a/only_b, changed, identical.',
        inputSchema={'type': 'object', 'additionalProperties': False,
                     'properties': {'branch_a': {'type': 'string'},
                                    'branch_b': {'type': 'string'}},
                     'required': ['branch_a', 'branch_b']}),

    # TD-DOM-22.3: claim-entry block exposed in the strict profile too.
    # dom_start / dom_update are lite-only (they always refuse PROFILE_STRICT),
    # so strict does not advertise them (audit 2026-11-12, minor).
    types.Tool(name='dom_describe', description=claim_entry_mod.compose_description('dom_describe'),
               inputSchema=claim_entry_mod.input_schema('dom_describe')),
    types.Tool(name='dom_claim', description=claim_entry_mod.compose_description('dom_claim'),
               inputSchema=claim_entry_mod.input_schema('dom_claim')),
    types.Tool(name='dom_find', description=claim_entry_mod.compose_description('dom_find'),
               inputSchema=claim_entry_mod.input_schema('dom_find')),
    # Usage help (contract 4.3): read-only table lookup, advertised in both profiles.
    types.Tool(name='dom_help', description=claim_entry_mod.compose_description('dom_help'),
               inputSchema=claim_entry_mod.input_schema('dom_help')),
]

LITE_SIGNATURE = {
    'dom_describe': ({}, {}),
    'dom_start': ({}, {'request': str, 'goal': str, 'branches': list, 'adopt': bool}),
    'dom_claim': ({'type': str, 'text': str}, dict(claim_entry_mod.COMMON_FIELDS, branch=str, **claim_entry_mod.FIELD_TYPES,
                                                  **claim_entry_mod.ANATOMY_ARGS, **claim_entry_mod.LINK_ARGS)),
    'dom_update': ({'claim_id': str, 'text': str, 'reason': str}, {'idempotency_key': str, 'anatomy': dict, 'expected_current': str, 'mode': str}),
    'dom_task_graph': ({'task': str}, {'agent': str, 'include_history': bool, 'include_children': bool, 'limit': int}),
    'dom_find': ({}, {'query': str, 'branch': str, 'type': str, 'requirement_ref': str, 'limit': int,
                      'plan_task': str, 'keyword': str, 'current_only': bool}),
    'dom_plan_ingest': ({'plan': str}, {'source': str, 'format': str, 'contexts': dict, 'source_revision': str, 'mode': str,
                                        'base_revision': str, 'force': bool}),
    'dom_task_claims': ({'task': str}, {'include_children': bool, 'link_role': str, 'include_history': bool}),
    'dom_plan_status': ({}, {'check_every': int}),
    'dom_context': ({'claim_id': str}, {'depth': int}),
    'dom_reconcile': ({}, {}),
    'dom_extract': ({'task': str, 'text': str}, {'provider': str, 'link_role': str}),
    'dom_accept': ({'candidate_id': str}, {'decision': str, 'note': str}),
    'dom_extract_submit': ({'task': str, 'text': str, 'candidates': list}, {'model': str, 'link_role': str}),
    'dom_annotate': ({'claim_id': str, 'kind': str}, {'text': str, 'url': str, 'locator': str, 'name': str, 'value': str,
                                                      'conflict_ref': str, 'reason': str}),
    'dom_distillate': ({'action': str}, {'source': str, 'distillate': str, 'text': str, 'anchors': list, 'by': str, 'note': str}),
    # Read-only skill router: the handler never touches the ledger, so no identity,
    # branch or review argument exists here.
    'dom_skill_select': ({'step': dict}, {'queue': list, 'plan_version': int, 'index_digest': str}),
    # Read-only usage help.  ``tool`` is optional at the transport on purpose (like
    # dom_start's ``request``): a missing name must be refused by the handler with a
    # hint and help text, which a transport-level INVALID_ARGUMENTS cannot carry.
    'dom_help': ({}, {'tool': str}),
}
# dom_start/dom_find accept requests with a missing 'request' so that the refusal can carry a hint
# (a transport-level INVALID_ARGUMENTS cannot); everything else is matched exactly.

SIGNATURE = {
    'dom_init': ({'external_work_ref': str, 'policy_digest': str}, {}),
    'dom_resume': ({'branch': str}, {'limit': int, 'after': str}),
    'dom_submit': ({'payload_json': str}, {}),
    'dom_review': ({'candidate_id': str, 'intent_digest': str}, {'decision': str, 'note': str}),
    'dom_candidate': ({'candidate_id': str}, {}),
    'dom_admit': ({'candidate_id': str, 'review_id': str, 'idempotency_key': str, 'intent_digest': str}, {}),
    'dom_append': ({'payload_json': str}, {}),
    'dom_query': ({'branch': str}, {'revision': int}),
    'dom_snapshot': ({'branch': str}, {'revision': int}),
    'dom_trace': ({}, {'branch': str}),
    'dom_export': ({'branch': str}, {'revision': int}),
    'dom_sync_status': ({'branch': str}, {}),
    'dom_sync': ({'branch': str}, {}),
    'dom_recall': ({'branch': str, 'at_seq': int}, {}),
    'dom_plan_tree': ({'root': str}, {'depth': int, 'at_version': int, 'require_fresh': bool}),
    'dom_claim_versions': ({'claim_id': str}, {}),
    'dom_diff': ({'branch_a': str, 'branch_b': str}, {}),
}


STRICT_TOOL_NAMES = frozenset(t.name for t in TOOLS)
ALL_TOOL_NAMES = None  # filled below once the lite table is known
LITE_TOOLS = [types.Tool(name=n, description=claim_entry_mod.compose_description(n), inputSchema=claim_entry_mod.input_schema(n))
              for n in claim_entry_mod.LITE_TOOL_NAMES]
LITE_TOOL_NAMES = frozenset(claim_entry_mod.LITE_TOOL_NAMES)
SIGNATURE.update(LITE_SIGNATURE)
ALL_TOOL_NAMES = STRICT_TOOL_NAMES | LITE_TOOL_NAMES


def tools_for(profile):
    return LITE_TOOLS if profile == 'lite' else TOOLS


def argument_hint(name):
    if name not in SIGNATURE:
        return 'Unknown tool. Use tools/list (lite profile: dom_describe).'
    required, optional = SIGNATURE[name]
    return ('Arguments of %s are matched exactly (no unknown keys, no wrong types). Required: %s. Optional: %s. '
            'profile, review_level, principal and work are fixed at launch and cannot be passed.'
            % (name, ', '.join(required) or 'none', ', '.join(optional) or 'none'))


def argument_help(name):
    """Russian, imperative help for a transport argument-binding refusal."""
    if name not in SIGNATURE:
        return ('Тебе НУЖНО: 1) Вызвать dom_describe — он вернёт список инструментов профиля. '
                '2) Выбрать имя из списка. 3) Повторить вызов.')
    required, optional = SIGNATURE[name]
    return ('Тебе НУЖНО: 1) Передать аргументы %s строго по сигнатуре: обязательные — %s, необязательные — %s. '
            '2) Убрать неизвестные ключи и не менять типы. 3) Помнить: profile, review_level, principal и work '
            'задаются при запуске сервера. 4) Повторить вызов.'
            % (name, ', '.join(required) or 'нет', ', '.join(optional) or 'нет'))


def strict_arguments(name, arguments):
    """Bind arguments exactly: no coercion, no unknown keys, no defaulted identity."""
    if arguments is None:
        arguments = {}
    if type(arguments) is not dict:
        raise ToolInput('INVALID_ARGUMENTS')
    required, optional = SIGNATURE[name]
    allowed = dict(required, **optional)
    if set(arguments) - set(allowed) or set(required) - set(arguments):
        raise ToolInput('INVALID_ARGUMENTS')
    for key, expected in allowed.items():
        if key in arguments and type(arguments[key]) is not expected:
            raise ToolInput('INVALID_ARGUMENTS')
    # Per-type required fields of dom_claim are a *domain* refusal
    # (MISSING_FIELD with a hint naming the field), not a transport error.
    return arguments


def _handle_describe(service, args):
    return service.describe()


def _handle_start(service, args):
    return service.start(args.get('request'), args.get('goal'), args.get('branches'), args.get('adopt', False))


def _handle_claim(service, args):
    extra = {k: v for k, v in args.items() if k not in ('branch', 'type', 'text')}
    return service.claim(args.get('branch'), args['type'], args['text'], **extra)


def _handle_update(service, args):
    return service.update(args['claim_id'], args['text'], args['reason'], args.get('idempotency_key'), args.get('anatomy'),
                          args.get('expected_current'), args.get('mode', 'auto'))


def _handle_task_graph(service, args):
    return service.task_graph(args['task'], args.get('agent'), args.get('include_history', True),
                              args.get('include_children', True), args.get('limit', 200))


def _handle_find(service, args):
    return service.find(args.get('query'), args.get('branch'), args.get('type'), args.get('requirement_ref'), args.get('limit', 20),
                        args.get('plan_task'), args.get('keyword'), args.get('current_only', False))


def _handle_plan_ingest(service, args):
    return service.plan_ingest(args['plan'], args.get('source', 'agent'), args.get('format', 'auto'), args.get('contexts'),
                               args.get('source_revision'), args.get('mode', 'full'),
                               base_revision=args.get('base_revision'), force=args.get('force', False))


def _handle_task_claims(service, args):
    return service.task_claims(args['task'], args.get('include_children', False), args.get('link_role', 'all'), args.get('include_history', False))


def _handle_plan_status(service, args):
    return service.plan_status(args.get('check_every', 10))


def _handle_context(service, args):
    return service.context_pack(args['claim_id'], args.get('depth', 8))


def _handle_extract(service, args):
    return service.extract(args['task'], args['text'], args.get('provider'), args.get('link_role', 'documents'))


def _handle_extract_submit(service, args):
    return service.extract_submit(args['task'], args['text'], args['candidates'], args.get('model'), args.get('link_role', 'documents'))


def _handle_annotate(service, args):
    extra = {k: v for k, v in args.items() if k not in ('claim_id', 'kind')}
    return service.annotate(args['claim_id'], args['kind'], **extra)


def _handle_accept(service, args):
    return service.accept(args['candidate_id'], args.get('decision', 'approve'), args.get('note'))


def _handle_distillate(service, args):
    return service.distillate(args['action'], args.get('source'), args.get('distillate'), args.get('text'),
                              args.get('anchors'), args.get('by'), args.get('note'))


def _handle_skill_select(service, args):
    # Read-only: the service (ledger, identity, review policy) is not consulted.
    return skill_select_mod.select_step(
        args['step'],
        queue=args.get('queue') or [],
        plan_version=args.get('plan_version', 0),
        index_digest=args.get('index_digest', ''),
    )


@guarded
def handle_dom_help(tool):
    """Usage help for one DOM tool, straight from HELP_DATA (read-only: the ledger,
    identity and review policy are never consulted)."""
    if type(tool) is not str or not tool.strip():
        raise Denied('INVALID_ARGUMENTS', field='tool', hint='tool is required',
                     help=('Тебе НУЖНО: 1) Выбрать инструмент из списка: dom_claim, dom_find, dom_update, ... '
                           '2) Передать его название в tool.'))
    data = HELP_DATA.get(tool)
    if not data:
        raise Denied('INVALID_FIELD', field='tool', hint="Unknown tool '%s'" % tool,
                     help=('Тебе НУЖНО: 1) Вызвать dom_describe — он вернёт точные имена инструментов профиля. '
                           '2) Либо передать существующее имя, например dom_claim, dom_find, dom_update. '
                           '3) Повторить вызов.'))
    return {'status': 'OK', 'tool': tool, 'usage': data['usage'],
            'preconditions': list(data['preconditions']),
            'postconditions': list(data['postconditions']),
            'common_errors': [dict(entry) for entry in data['common_errors']],
            'help': data['help']}


def _handle_help(service, args):
    return handle_dom_help(args.get('tool'))


def _handle_reconcile(service, args):
    return service.reconcile()


def _handle_init(service, args):
    return service.init(dict(args))


def _handle_resume(service, args):
    return service.resume(args['branch'], args.get('limit', 50), args.get('after'))


def _handle_submit(service, args):
    return service.submit(args['payload_json'])


def _handle_review(service, args):
    return service.review(args['candidate_id'], args['intent_digest'], args.get('decision', 'approve'), args.get('note'))


def _handle_candidate(service, args):
    return service.candidate_view(args['candidate_id'])


def _handle_admit(service, args):
    return service.admit(args['candidate_id'], args['review_id'], args['idempotency_key'], args['intent_digest'])


def _handle_append(service, args):
    return service.append(args['payload_json'])


def _handle_query(service, args):
    return service.query(args['branch'], args.get('revision'))


def _handle_trace(service, args):
    return service.trace(args.get('branch', 'master'))


def _handle_export(service, args):
    return service.export(args['branch'], args.get('revision'))


def _handle_sync_status(service, args):
    return service.sync_status(args['branch'])


def _handle_sync(service, args):
    return service.sync(args['branch'])


def _handle_recall(service, args):
    return service.recall(args['branch'], args['at_seq'])


def _handle_plan_tree(service, args):
    return service.plan_tree(args['root'], args.get('depth', 3), args.get('at_version'), args.get('require_fresh', False))


def _handle_claim_versions(service, args):
    return service.claim_versions(args['claim_id'])


def _handle_diff(service, args):
    return service.diff(args['branch_a'], args['branch_b'])


# TD-DOM-35: the single routing table.  A tool is usable only if it is declared
# in SIGNATURE, visible for the launch profile, and has an entry here; the
# drift test in tests/test_tool_registry.py pins the three sets together.
HANDLERS = {
    'dom_describe': _handle_describe,
    'dom_start': _handle_start,
    'dom_claim': _handle_claim,
    'dom_update': _handle_update,
    'dom_find': _handle_find,
    'dom_plan_ingest': _handle_plan_ingest,
    'dom_task_claims': _handle_task_claims,
    'dom_plan_status': _handle_plan_status,
    'dom_context': _handle_context,
    'dom_task_graph': _handle_task_graph,
    'dom_reconcile': _handle_reconcile,
    'dom_extract': _handle_extract,
    'dom_accept': _handle_accept,
    'dom_annotate': _handle_annotate,
    'dom_distillate': _handle_distillate,
    'dom_skill_select': _handle_skill_select,
    'dom_help': _handle_help,
    'dom_extract_submit': _handle_extract_submit,
    'dom_init': _handle_init,
    'dom_resume': _handle_resume,
    'dom_submit': _handle_submit,
    'dom_review': _handle_review,
    'dom_candidate': _handle_candidate,
    'dom_admit': _handle_admit,
    'dom_append': _handle_append,
    'dom_query': _handle_query,
    'dom_snapshot': _handle_query,
    'dom_trace': _handle_trace,
    'dom_export': _handle_export,
    'dom_sync_status': _handle_sync_status,
    'dom_sync': _handle_sync,
    'dom_recall': _handle_recall,
    'dom_plan_tree': _handle_plan_tree,
    'dom_claim_versions': _handle_claim_versions,
    'dom_diff': _handle_diff,
}


def dispatch(service, name, arguments):
    visible = LITE_TOOL_NAMES if service.profile == 'lite' else STRICT_TOOL_NAMES
    if name not in SIGNATURE or name not in visible:
        raise ToolInput('UNKNOWN_TOOL')
    args = strict_arguments(name, arguments)
    handler = HANDLERS.get(name)
    if handler is None:
        # Declared in SIGNATURE but not routed: structured refusal, never None.
        raise ToolInput('UNKNOWN_TOOL')
    return handler(service, args)


def build_server(service, log_path=None):
    server = Server('omh-dom-mockup', version='0.1.0')

    def log(record):
        line = json.dumps(record, sort_keys=True)
        print('[dom-mockup] ' + line, file=sys.stderr, flush=True)
        if log_path is not None:
            with open(log_path, 'a', encoding='utf8') as handle:
                handle.write(line + '\n')

    @server.list_tools()
    async def list_tools():
        return tools_for(service.profile)

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        try:
            result = dispatch(service, name, arguments)
            public = public_dto.project_tool(name, result, scope=service.context.branches, profile=service.profile)
        except ToolInput as error:
            code = str(error)
            if code == 'INVALID_ARGUMENTS':
                meta = public_dto.invalid_arguments(name, help_text=argument_help(name))
                hint = argument_hint(name)
            else:
                meta = public_dto.error_help(code, service.profile, tool=name)
                hint = meta.get('hint', '')
            payload = {'status': code,
                       'error': {'code': code, 'tool': name,
                                 'message': 'Tool call refused by the transport contract; nothing was executed.'},
                       'hint': hint,
                       'help': meta.get('help', '')}
            log({'tool': name, 'status': code, 'principal': service.context.principal})
            return types.CallToolResult(
                content=[types.TextContent(type='text', text=json.dumps(payload, ensure_ascii=True, sort_keys=True))],
                structuredContent=payload, isError=True)
        except Exception as error:
            # Fail closed: a projection refusal or any internal fault becomes a
            # fixed typed enum; raw exception details are never serialized to
            # the caller. The operator log carries the exception type only.
            code = 'PUBLIC_PROJECTION_INVALID' if isinstance(error, public_dto.PublicProjectionError) else 'INTERNAL_ERROR'
            meta = public_dto.error_help(code, service.profile, tool=name)
            payload = {'status': code,
                       'error': {'code': code, 'tool': name,
                                 'message': 'Tool processing refused; no details exposed.'},
                       'hint': meta.get('hint', ''),
                       'help': meta.get('help', '')}
            log({'tool': name, 'status': code, 'principal': service.context.principal,
                 'error_type': type(error).__name__})
            return types.CallToolResult(
                content=[types.TextContent(type='text', text=json.dumps(payload, ensure_ascii=True, sort_keys=True))],
                structuredContent=payload, isError=True)
        record = {'tool': name, 'status': public.get('status'), 'simulated': service.context.simulation,
                  'principal': service.context.principal}
        log(record)
        return types.CallToolResult(
            content=[types.TextContent(type='text', text=json.dumps(public, ensure_ascii=True, sort_keys=True))],
            structuredContent=public, isError=False)

    return server


async def serve(service, log_path):
    server = build_server(service, log_path)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, InitializationOptions(
            server_name='omh-dom-mockup', server_version='0.1.0',
            capabilities=server.get_capabilities(NotificationOptions(), {}),
            instructions=INSTRUCTIONS_LITE if service.profile == 'lite' else INSTRUCTIONS))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='mockup.server', description='Simulated DOM stdio MCP server.')
    parser.add_argument('--directory', required=True, help='run directory inside <project>/runtime')
    parser.add_argument('--role', default=None, choices=ROLES,
                        help='prod: owner (default: submit+review+admit), author (submit) or reviewer (review+admit); lab: fixture identity')
    parser.add_argument('--principal', default=None,
                        help='prod: name of this identity (e.g. claude, local-agent, the user). Different principals are independent reviewers of each other')
    parser.add_argument('--profile', default='prod', choices=PROFILES,
                        help='strict (default, the original behaviour) or lite (local_single_owner: single-call tools, review_level=self)')
    simulation = parser.add_mutually_exclusive_group()
    simulation.add_argument('--simulation', dest='simulation', action='store_true', default=True,
                            help='use the synthetic simulation adapter (default)')
    simulation.add_argument('--no-simulation', dest='simulation', action='store_false',
                            help='fail closed: every init is refused, no live binding exists')
    parser.add_argument('--review', default='auto', choices=('auto', 'manual', 'independent'),
                        help='review policy: auto (the writer admits, labelled self), manual (every record waits for a review), '
                             'independent (the review must come from another principal)')
    parser.add_argument('--log-file', default=None, help='optional JSONL diagnostic log inside runtime/')
    args = parser.parse_args(argv)

    directory = Path(args.directory).resolve()
    from .paths import runtime_root
    RUNTIME = runtime_root()  # DOM_RUNTIME_ROOT (launch environment) or <repo>/runtime
    if not directory.is_relative_to(RUNTIME):
        print('REFUSED: --directory must resolve inside ' + str(RUNTIME), file=sys.stderr)
        return 2
    if directory.exists() and not directory.is_dir():
        print('REFUSED: --directory must be a directory, not a file: ' + str(directory), file=sys.stderr)
        return 2
    log_path = None
    if args.log_file:
        candidate = Path(args.log_file).resolve()
        if not candidate.is_relative_to(RUNTIME):
            print('REFUSED: --log-file must resolve inside ' + str(RUNTIME), file=sys.stderr)
            return 2
        candidate.parent.mkdir(parents=True, exist_ok=True)
        log_path = candidate
    profile = {'prod': 'lite', 'lab': 'strict'}.get(args.profile, args.profile)
    if profile != 'lite':
        directory.mkdir(parents=True, exist_ok=True)
    # lite (QA004 F-08): nothing is created in the project before dom_start (Store.initialize makes the directory)
    role = args.role or ('owner' if profile == 'lite' else 'author')
    if args.principal is not None and (profile != 'lite' or not __import__('re').fullmatch(r'[A-Za-z0-9_.-]{1,48}', args.principal)):
        print('REFUSED: --principal is a prod option, [A-Za-z0-9_.-]{1,48}', file=sys.stderr)
        return 2
    service = Service(directory, role=role, simulation=args.simulation, profile=profile, review_mode=args.review,
                      principal=args.principal)
    anyio.run(serve, service, log_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
