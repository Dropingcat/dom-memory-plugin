"""IClaimExtractionPort: turn plan-item text into claim *candidates* (D6).

The LLM inside the MCP never writes the ledger.  A port returns candidates;
``extract`` submits them (submit only: no review, no admission), so every one
waits for the owner or the local agent to read (dom_candidate), decide
(dom_review approve|reject) and admit (dom_admit).

Providers (configuration, never code):
* ``deterministic`` - mandatory fallback, no model: paragraphs / bullets, code
  fences, formulas, quoted passages, decision markers.
* ``anthropic``     - Claude Messages API through the official ``anthropic``
  SDK (optional dependency); model ``DOM_LLM_MODEL`` (default claude-opus-5-5),
  JSON-schema structured output, refusal-aware.
* ``command``       - any external program (Hermes / OpenCode / a local model
  runner): receives the request JSON on stdin, prints {"candidates": [...]}.

Configuration: ``<run dir>/dom-llm.json`` ({"provider", "model", "command",
"effort", "timeout"}) or env ``DOM_LLM_PROVIDER`` / ``DOM_LLM_MODEL`` /
``DOM_LLM_COMMAND``.  Every candidate carries provenance
{provider, model, prompt_digest, checkpoint_ref}.
"""
import json
import os
import re
import shlex
import subprocess

from . import claim_entry as ce
from .dom_contract import digest

DEFAULT_MODEL = 'claude-opus-5-5'
MAX_CANDIDATES = 50
MAX_INPUT = 200_000
PROMPT_VERSION = 'dom-extract/v1'

SYSTEM_PROMPT = (
    'You split project material into claims for a project-memory ledger. A claim is one primary unit of information '
    'kept in its original wording: a quote, a decision, a formula, a code fragment, a data table, a definition '
    '(concept) or any other statement (text). Rules: copy the wording from the input, never paraphrase or invent; '
    'one claim per statement; give type-specific fields only when the input states them (source_ref for a quote, '
    'rationale for a decision, notation and units for a formula, repository_ref and relative_path for code, '
    'artifact_ref and units for data); purpose = what the claim is for in the plan item, if the input says so; '
    'keywords = 1-5 search words. Skip greetings, filler and questions.')

CANDIDATE_SCHEMA = {
    'type': 'object',
    'properties': {
        'candidates': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'type': {'type': 'string', 'enum': list(ce.TYPES)},
                    'text': {'type': 'string'},
                    'fields': {
                        'type': 'object',
                        'properties': {k: {'type': 'string'} for k in
                                       ('source_ref', 'locator', 'attribution', 'rationale', 'expression', 'repository_ref',
                                        'relative_path', 'language', 'artifact_ref', 'method', 'definition', 'scope')},
                        'additionalProperties': False,
                    },
                    'units': {'type': 'string', 'description': 'symbol=unit pairs separated by ";" (formula/data)'},
                    'notation': {'type': 'string', 'description': 'symbol=meaning pairs separated by ";" (formula)'},
                    'purpose': {'type': 'string'},
                    'keywords': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['type', 'text', 'fields', 'units', 'notation', 'purpose', 'keywords'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['candidates'],
    'additionalProperties': False,
}


class ProviderUnavailable(Exception):
    """The configured provider cannot run (missing SDK, credentials, command)."""


# ============================================================ config ==
def load_config(directory):
    cfg = {}
    path = os.path.join(str(directory), 'dom-llm.json')
    try:
        with open(path, encoding='utf8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except (OSError, ValueError):
        pass
    for key, env in (('provider', 'DOM_LLM_PROVIDER'), ('model', 'DOM_LLM_MODEL'), ('command', 'DOM_LLM_COMMAND'),
                     ('effort', 'DOM_LLM_EFFORT')):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    cfg.setdefault('provider', 'deterministic')
    cfg.setdefault('timeout', 600)
    return cfg


# ========================================================= providers ==
_FENCE = re.compile(r'```([\w+-]*)\n(.*?)```', re.S)
_QUOTE = re.compile(r'[«"“]([^»"”]{12,})[»"”]')
_DECISION = re.compile(r'\b(решили|решено|выбираем|выбрали|используем|будем|принято|decided|we will|use |choose|chosen)', re.I)
_FORMULA = re.compile(r'^[\w\s().^*/+\-−·√]{1,40}=[^=].{0,200}$')
_BULLET = re.compile(r'^\s*(?:[-*+]|\d+[.)])\s+')


def deterministic(request):
    """Rule-based extraction; never invents a field the text does not hold."""
    text = request['text']
    out = []
    for lang, body in _FENCE.findall(text):
        if body.strip():
            out.append({'type': 'code', 'text': body.rstrip('\n'), 'fields': {'language': lang} if lang else {},
                        'units': '', 'notation': '', 'purpose': '', 'keywords': []})
    rest = _FENCE.sub('\n', text)
    for quote in _QUOTE.findall(rest):
        out.append({'type': 'text', 'text': quote.strip(), 'fields': {}, 'units': '', 'notation': '', 'purpose': '',
                    'keywords': [], 'note': 'quoted passage without a source reference'})
    blocks = []
    for para in re.split(r'\n\s*\n', rest):
        lines = [l for l in para.splitlines() if l.strip()]
        if lines and all(_BULLET.match(l) for l in lines):
            blocks.extend(_BULLET.sub('', l).strip() for l in lines)
        elif lines:
            blocks.append(' '.join(l.strip() for l in lines))
    for block in blocks:
        if len(block) < 8 or block.endswith('?'):
            continue
        typ = 'text'
        if _FORMULA.match(block):
            typ = 'formula'
        elif _DECISION.search(block):
            typ = 'decision'
        out.append({'type': typ, 'text': block, 'fields': {}, 'units': '', 'notation': '', 'purpose': '', 'keywords': []})
    return {'candidates': out[:MAX_CANDIDATES], 'model': 'rules/v1'}


def anthropic_provider(request, cfg):
    try:
        import anthropic
    except ImportError:
        raise ProviderUnavailable('the anthropic SDK is not installed (pip install anthropic)') from None
    model = cfg.get('model') or DEFAULT_MODEL
    client = anthropic.Anthropic(timeout=float(cfg.get('timeout', 600)))
    user = ('Plan item: %s\nItem context: %s\n\nMaterial:\n%s' % (request['task_title'], request.get('task_context') or '-', request['text']))
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            betas=['server-side-fallback-2026-07-01'],
            fallbacks='default',
            system=SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': user}],
            output_config={'effort': cfg.get('effort') or 'medium',
                           'format': {'type': 'json_schema', 'schema': CANDIDATE_SCHEMA}},
        )
    except anthropic.AuthenticationError:
        raise ProviderUnavailable('no valid Anthropic credentials (ANTHROPIC_API_KEY or `ant auth login`)') from None
    except anthropic.APIConnectionError:
        raise ProviderUnavailable('the Anthropic API is not reachable') from None
    if response.stop_reason == 'refusal':
        raise ProviderUnavailable('the model declined the request')
    if response.stop_reason == 'max_tokens':
        raise ProviderUnavailable('the answer was cut at max_tokens; send a shorter text')
    text = next((b.text for b in response.content if b.type == 'text'), '')
    data = json.loads(text)
    data['model'] = response.model
    return data


def command_provider(request, cfg):
    command = cfg.get('command')
    if not command:
        raise ProviderUnavailable("provider 'command' needs 'command' (DOM_LLM_COMMAND)")
    argv = command if isinstance(command, list) else shlex.split(command, posix=os.name != 'nt')
    try:
        done = subprocess.run(argv, input=json.dumps(dict(request, schema=CANDIDATE_SCHEMA, system=SYSTEM_PROMPT)),
                              capture_output=True, text=True, encoding='utf8', timeout=float(cfg.get('timeout', 600)))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ProviderUnavailable('command failed: %s' % type(e).__name__) from None
    if done.returncode != 0:
        raise ProviderUnavailable('command exited with %d' % done.returncode)
    try:
        data = json.loads(done.stdout)
    except ValueError:
        raise ProviderUnavailable('command did not print JSON') from None
    data.setdefault('model', cfg.get('model') or 'command')
    return data


class ClientTurn(Exception):
    """Provider 'client': the calling agent (Claude Code, OpenCode, Hermes) is the model."""


def client_provider(request, cfg):
    raise ClientTurn()


PROVIDERS = {'deterministic': lambda req, cfg: deterministic(req), 'anthropic': anthropic_provider,
             'command': command_provider, 'client': client_provider}


# ======================================================= normalisation ==
def _pairs(value):
    out = {}
    for part in (value or '').split(';'):
        if '=' in part:
            k, v = part.split('=', 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out


def normalise(raw, material):
    """Provider output -> dom_claim argument dicts.  A candidate whose type
    lacks a required field the model did not state becomes an honest ``text``
    interpretation; a text that is not in the material is dropped (no invention)."""
    out, dropped = [], 0
    candidates = raw.get('candidates') if isinstance(raw, dict) else None
    if not isinstance(candidates, list):
        raise ProviderUnavailable('provider answer has no candidates list')
    folded = re.sub(r'\s+', ' ', material).casefold()
    for c in candidates[:MAX_CANDIDATES]:
        if not isinstance(c, dict) or not isinstance(c.get('text'), str) or not c['text'].strip():
            dropped += 1
            continue
        text = c['text'].strip()
        if re.sub(r'\s+', ' ', text).casefold() not in folded:
            dropped += 1
            continue
        typ = c.get('type') if c.get('type') in ce.TYPES else 'text'
        fields = {k: v for k, v in (c.get('fields') or {}).items() if isinstance(v, str) and v.strip() and k in ce.TYPE_ARGS[typ]}
        if typ in ('formula', 'data'):
            units = _pairs(c.get('units'))
            if units:
                fields['units'] = units
        if typ == 'formula':
            notation = _pairs(c.get('notation'))
            if notation:
                fields['notation'] = notation
        suggested = None
        if any(ce._missing(fields.get(f)) for f in ce.REQUIRED_FIELDS.get(typ, ())):
            # the required facts are not in the material: keep it as text and
            # tell the reviewer what it looked like (no invented fields)
            suggested = typ
            typ, fields = 'text', {k: v for k, v in fields.items() if k in ce.TYPE_ARGS['text']}
        args = dict(fields)
        if isinstance(c.get('purpose'), str) and c['purpose'].strip():
            args['purpose'] = c['purpose'].strip()
        kws = [k.strip() for k in (c.get('keywords') or []) if isinstance(k, str) and k.strip()][:8]
        if suggested:
            kws.append('suggested:' + suggested)
        if kws:
            args['keywords'] = kws
        out.append((typ, text, args))
    return out, dropped


# ============================================================ extract ==
def extract(svc, task, text, provider=None, link_role='documents'):
    """Run the configured port on ``text`` for a plan item; submit candidates (not admitted)."""
    from . import planning
    Denied = ce._denied()
    ce._lite_only(svc)
    svc.scope()
    svc.capability('submit')
    svc.ready()
    raw_text = ce._text_bytes(text)
    if len(raw_text) > MAX_INPUT:
        raise Denied('TEXT_TOO_LARGE', field='text', hint='At most %d bytes per extraction; split the material.' % MAX_INPUT)
    with svc.store.read() as c:
        row = planning.resolve_task(c, task)
        if row is None or row['branch'] is None:
            raise Denied('TASK_NOT_FOUND', field='task')
        header = planning._header_claim(c, row['plan_task_id'])
        props = planning._properties(c, header) if header else {}
    cfg = load_config(svc.directory)
    if provider is not None:
        if provider not in PROVIDERS:
            raise Denied('INVALID_FIELD', field='provider', hint="provider is one of: %s." % ', '.join(PROVIDERS))
        cfg['provider'] = provider
    request = {'prompt_version': PROMPT_VERSION, 'task_path': row['path'], 'task_title': row['title'],
               'task_context': props.get('context'), 'text': text}
    prompt_digest = digest({'v': PROMPT_VERSION, 'system': SYSTEM_PROMPT, 'request': request})
    used, warning = cfg['provider'], None
    try:
        answer = PROVIDERS[used](request, cfg)
        candidates, dropped = normalise(answer, text)
    except ClientTurn:
        # No model call here: hand the rules and the schema to the caller, who
        # answers with dom_extract_submit; the same checks run on its answer.
        return {'status': 'EXTRACTION_REQUEST', 'simulated': True, 'provider': 'client', 'task_path': row['path'],
                'task_title': row['title'], 'task_context': props.get('context'), 'branch': row['branch'],
                'instructions': SYSTEM_PROMPT, 'candidate_schema': CANDIDATE_SCHEMA, 'prompt_digest': prompt_digest,
                'next_steps': [{'tool': 'dom_extract_submit',
                                'arguments': {'task': row['path'], 'text': '<the same material>', 'candidates': '<array per candidate_schema.candidates>'},
                                'why': 'split the material yourself by the instructions and submit the candidates; they still wait for review'}]}
    except (ProviderUnavailable, ValueError, KeyError, TypeError) as e:
        if used == 'deterministic':
            raise
        warning = '%s provider unavailable (%s); used the deterministic fallback' % (used, e)
        used, answer = 'deterministic', deterministic(request)
        candidates, dropped = normalise(answer, text)
    return _submit(svc, row, text, used, str(answer.get('model') or used)[:120], prompt_digest, candidates, dropped, warning, link_role)


def extract_submit(svc, task, text, candidates, model=None, link_role='documents'):
    """Provider 'client', second half: the agent's own candidates, checked like any provider's."""
    from . import planning
    Denied = ce._denied()
    ce._lite_only(svc)
    svc.scope()
    svc.capability('submit')
    svc.ready()
    ce._text_bytes(text)
    if type(candidates) is not list or not candidates:
        raise Denied('INVALID_FIELD', field='candidates', hint='candidates: a non-empty array of {type, text, fields?, units?, notation?, purpose?, keywords?}.')
    if model is not None and (type(model) is not str or not model.strip()):
        raise Denied('INVALID_FIELD', field='model')
    with svc.store.read() as c:
        row = planning.resolve_task(c, task)
        if row is None or row['branch'] is None:
            raise Denied('TASK_NOT_FOUND', field='task')
        header = planning._header_claim(c, row['plan_task_id'])
        props = planning._properties(c, header) if header else {}
    request = {'prompt_version': PROMPT_VERSION, 'task_path': row['path'], 'task_title': row['title'],
               'task_context': props.get('context'), 'text': text}
    prompt_digest = digest({'v': PROMPT_VERSION, 'system': SYSTEM_PROMPT, 'request': request})
    try:
        normalised, dropped = normalise({'candidates': candidates}, text)
    except ProviderUnavailable:
        raise Denied('INVALID_FIELD', field='candidates') from None
    return _submit(svc, row, text, 'client', (model or 'calling-agent').strip()[:120], prompt_digest, normalised, dropped, None, link_role)


def _submit(svc, row, text, used, model, prompt_digest, candidates, dropped, warning, link_role):
    checkpoint = 'plan:%s@%s' % (row['plan_task_id'], row['version'])
    provenance = 'extracted by %s/%s (prompt %s, %s)' % (used, model, prompt_digest[:16], checkpoint)
    submitted, already, refused = [], [], []
    for n, (typ, body, args) in enumerate(candidates):
        fields = dict(args, plan_task=row['plan_task_id'], link_role=link_role, reason=provenance,
                      basis=['llm:%s:%s:%s' % (used, model, prompt_digest[:16]), checkpoint])
        res = ce.claim(svc, None, typ, body, dict(fields, _submit_only=True))
        if res.get('status') == 'RECORDED_CANDIDATE':
            submitted.append({'candidate_id': res['candidate_id'], 'intent_digest': res['intent_digest'], 'type': typ,
                              'preview': ce._preview(body, None), 'branch': res['branch']})
        elif res.get('status') == 'ALREADY_RECORDED':
            already.append(res['claim_id'])
        else:
            refused.append({'index': n, 'status': res.get('status'), 'field': res.get('field')})
    steps = []
    if submitted:
        first = submitted[0]
        steps = [{'tool': 'dom_candidate', 'arguments': {'candidate_id': first['candidate_id']}, 'why': 'read a candidate before deciding'},
                 {'tool': 'dom_review', 'arguments': {'candidate_id': first['candidate_id'], 'intent_digest': first['intent_digest'], 'decision': 'approve'},
                  'why': 'approve (or reject with a note); then dom_admit with the review_id'}]
    return {'status': 'EXTRACTED', 'simulated': True, 'provider': used, 'model': model, 'prompt_digest': prompt_digest,
            'checkpoint_ref': checkpoint, 'task_path': row['path'], 'branch': row['branch'], 'candidates': submitted,
            'already_recorded': already, 'refused': refused, 'dropped': dropped, 'warning': warning, 'next_steps': steps}
