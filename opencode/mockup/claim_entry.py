"""WR-ADD (L0+L1): the lite (local_single_owner) profile -- single-call tools for a cold agent.

Every function here takes the ``Service`` as ``svc`` and writes **only** through the
existing ``submit -> review -> admit`` methods, so the transaction, the append-only
event chain, the outbox, the idempotency rows and the immutability triggers are the
ones the strict profile already has.  Nothing is inserted into ``claims``/``events``
from this module.  The only direct inserts are the two additive tables of
migration v3 (``project_root``, ``adoptions`` via ``Store.initialize``).

Trust boundary: the profile, the identity (work/task/policy, principal, root,
review level) are launch-time facts of the server.  No tool argument can set them.
"""
import base64
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone

from .dom_contract import digest, raw_hash, valid_commit_ref

LITE_POLICY_DOC = {'version': 'lite-policy/v1', 'profile': 'lite'}
BRANCH_RE = re.compile(r'^[A-Za-z0-9_-]{1,32}$')
MAX_TEXT_BYTES = 1 << 20
MAX_BRANCHES = 64
TYPES = ('quote', 'decision', 'formula', 'code', 'data', 'concept', 'text')
ROOT_REQUEST_REF = 'customer:request'
DEFAULT_ORIGIN_VERSION = 'lite-v1'

# optional per-type arguments of dom_claim (name -> python type) ------------------
FIELD_TYPES = {
    'source_ref': str, 'locator': str, 'source_snapshot_digest': str,
    'attribution': str, 'rationale': str, 'assumptions': list, 'alternatives': list,
    'expression': str, 'notation': dict, 'units': dict, 'derivation_refs': list,
    'repository_ref': str, 'relative_path': str, 'language': str, 'snapshot_digest': str,
    'artifact_ref': str, 'artifact_digest': str, 'method': str, 'columns': dict,
    'provenance': str, 'definition': str, 'scope': str, 'links': list,
    'classification': str, 'evidence': list, 'commit_ref': dict,
}
COMMON_FIELDS = {'requirement_refs': list, 'reason': str, 'origin_version': str, 'idempotency_key': str}
# Claim anatomy arguments (immutable, stored in item['anatomy']) and plan link arguments.
ANATOMY_ARGS = {'basis': list, 'refines': str, 'purpose': str, 'usage': list, 'keywords': list}
LINK_ARGS = {'plan_task': str, 'link_role': str}
TYPE_ARGS = {
    'quote': ('source_ref', 'locator', 'source_snapshot_digest'),
    'decision': ('attribution', 'rationale', 'assumptions', 'alternatives'),
    'formula': ('expression', 'notation', 'units', 'assumptions', 'derivation_refs'),
    'code': ('repository_ref', 'relative_path', 'language', 'snapshot_digest', 'locator', 'commit_ref'),
    'data': ('artifact_ref', 'artifact_digest', 'method', 'columns', 'units', 'provenance'),
    'concept': ('definition', 'scope', 'assumptions', 'attribution', 'links'),
    'text': ('classification', 'evidence', 'attribution', 'assumptions'),
}

# Per-type required fields of dom_claim (TD-DOM-22.4 / TD-DOM-23).
# Server-side validation in _validated_claim_args already enforces these; this
# table makes them discoverable in the schema and in dom_describe.
REQUIRED_FIELDS = {
    'quote': ('source_ref',),
    'decision': ('rationale',),
    # expression defaults to the body text (derived, not invented); notation and
    # units are facts only the author knows -- dom_submit refuses them missing
    # (MISSING_UNIT), so dom_claim refuses too (audit 2026-11-12 #3).
    'formula': ('notation', 'units'),
    'code': ('repository_ref', 'relative_path'),
    'data': ('artifact_ref', 'units'),
    'concept': ('definition',),
    'text': (),  # 'classification'+'evidence' required only when classification=='factual'
}

def identity(directory):
    """work_ref / task_ref / policy_digest of the lite profile, computed from the run directory."""
    h = hashlib.sha256(os.path.normcase(str(directory)).encode('utf8')).hexdigest()[:20]
    return 'lite:' + h, 'lite-task:' + h, digest(LITE_POLICY_DOC)


def _denied():
    from .admission import Denied
    return Denied


def _lite_only(svc):
    if not svc.lite:
        raise _denied()('PROFILE_STRICT')


def _now():
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------- arguments --
def _text_bytes(text, field='text'):
    Denied = _denied()
    if type(text) is not str:
        raise Denied('INVALID_FIELD', field=field)
    if not text.strip():
        raise Denied('REQUEST_REQUIRED' if field == 'request' else 'EMPTY_TEXT', field=field)
    try:
        raw = text.encode('utf-8')
    except UnicodeEncodeError:
        raise Denied('INVALID_TEXT', field=field) from None
    if len(raw) > MAX_TEXT_BYTES:
        raise Denied('TEXT_TOO_LARGE', field=field)
    return raw


def _str_list(value, field, allow_empty=True):
    Denied = _denied()
    if type(value) is not list or any(type(v) is not str or not v for v in value) or (not value and not allow_empty):
        raise Denied('INVALID_FIELD', field=field, hint="'%s' must be an array of non-empty strings." % field)
    return value


def _derived(typ, text, raw):
    """Metadata values that follow the body text (recomputed on dom_update)."""
    if typ == 'quote':
        return {'source_snapshot_digest': raw_hash(raw), 'locator': 'bytes:0-%d' % len(raw),
                'source_b64': base64.b64encode(raw).decode()}
    if typ == 'formula':
        return {'expression': text}
    if typ == 'code':
        return {'snapshot_digest': raw_hash(raw), 'locator': 'lines:1-%d' % (text.count('\n') + 1)}
    if typ == 'data':
        return {'artifact_digest': raw_hash(raw)}
    if typ == 'concept':
        return {'definition': text}
    return {}


_LANGUAGES = {'py': 'python', 'js': 'javascript', 'ts': 'typescript', 'md': 'markdown', 'sql': 'sql', 'sh': 'shell',
              'ps1': 'powershell', 'json': 'json', 'yaml': 'yaml', 'yml': 'yaml', 'toml': 'toml', 'rs': 'rust', 'go': 'go',
              'java': 'java', 'c': 'c', 'h': 'c', 'cpp': 'cpp', 'cs': 'csharp', 'rb': 'ruby', 'txt': 'text'}


def build_metadata(typ, text, raw, args):
    """Typed metadata: explicit arguments first, then values *derived* from the body.

    Audit 2026-11-12 #3: no invented placeholders.  A required field that is
    missing is refused before this point (_validated_claim_args); what remains
    here are derived facts (digests, locators, the body as expression or
    definition) and neutral labels that say "not stated" ('unattributed').
    """
    def pick(key, default):
        value = args.get(key)
        return default if _missing(value) else value
    d = _derived(typ, text, raw)
    if typ == 'quote':
        return {'source_ref': args['source_ref'],
                'source_snapshot_digest': pick('source_snapshot_digest', d['source_snapshot_digest']),
                'locator': pick('locator', d['locator']), 'source_b64': d['source_b64']}
    if typ == 'decision':
        return {'attribution': pick('attribution', 'unattributed'), 'rationale': args['rationale'],
                'assumptions': pick('assumptions', []), 'alternatives': pick('alternatives', [])}
    if typ == 'formula':
        return {'expression': pick('expression', d['expression']),
                'notation': args['notation'],
                'assumptions': pick('assumptions', []), 'units': args['units'],
                'derivation_refs': pick('derivation_refs', [])}
    if typ == 'code':
        ext = args['relative_path'].rsplit('.', 1)[-1].lower() if '.' in args['relative_path'] else ''
        metadata = {'repository_ref': args['repository_ref'],
                    'relative_path': args['relative_path'],
                    'language': pick('language', _LANGUAGES.get(ext, 'unknown')),
                    'snapshot_digest': pick('snapshot_digest', d['snapshot_digest']),
                    'locator': pick('locator', d['locator']), 'dirty': False}
        if args.get('commit_ref') is not None:
            metadata['commit_ref'] = args['commit_ref']
        return metadata
    if typ == 'data':
        return {'artifact_ref': args['artifact_ref'],
                'artifact_digest': pick('artifact_digest', d['artifact_digest']),
                'method': pick('method', 'agent-entered'), 'columns': pick('columns', {}),
                'units': args['units'], 'provenance': pick('provenance', 'agent-entered')}
    if typ == 'concept':
        return {'definition': pick('definition', d['definition']), 'scope': pick('scope', 'project'),
                'assumptions': pick('assumptions', []), 'attribution': pick('attribution', 'unattributed'),
                'links': pick('links', [])}
    evidence = pick('evidence', [])
    classification = pick('classification', 'factual' if evidence else 'interpretation')
    return {'classification': classification, 'evidence': evidence,
            'attribution': pick('attribution', 'unattributed'), 'assumptions': pick('assumptions', [])}


def _missing(value):
    """An empty string, empty list/object or whitespace counts as not given."""
    return value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, (list, dict)) and not value)


def build_item(typ, text, raw, branch, args, requirement_refs, reason, local_id='c1', anatomy=None):
    item = {'local_id': local_id, 'content_type': typ, 'body_b64': base64.b64encode(raw).decode(),
            'encoding': 'utf-8', 'media_type': 'text/plain', 'reason': reason, 'applicability': [branch],
            'metadata': build_metadata(typ, text, raw, args), 'requirement_refs': sorted(set(requirement_refs))}
    if anatomy:
        item['anatomy'] = anatomy
    return item


def _package(svc, branch, revision, items, key, operation, origin_version, reason):
    return {'external_work_ref': svc.context.work, 'external_task_ref': svc.task, 'branch': branch,
            'expected_branch_revision': revision, 'schema_version': 'dom/v1', 'policy_digest': svc.policy,
            'idempotency_key': key, 'operation': operation, 'items': items, 'dependencies': {},
            'origin_version': origin_version, 'reason': reason}


# ------------------------------------------------------------- read helpers --
def _children(c):
    """old claim id -> replacement claim id (replacement events, all branches)."""
    out = {}
    for row in c.execute("SELECT claim_ref, payload FROM events WHERE kind='replacement' AND claim_ref IS NOT NULL ORDER BY seq"):
        try:
            out.setdefault(row[0], json.loads(row[1])['value']['value']['replacement_ref'])
        except (ValueError, TypeError, KeyError):
            continue
    return out


def _archived(c):
    return {r[0] for r in c.execute("SELECT DISTINCT claim_ref FROM events WHERE kind IN ('archive','replacement') AND claim_ref IS NOT NULL")}


def _lineage(children):
    """claim id -> (record_id, version): the record id is the first version's claim id."""
    parent = {new: old for old, new in children.items()}

    def info(cid):
        depth, seen = 1, {cid}
        while cid in parent and parent[cid] not in seen:
            cid = parent[cid]; seen.add(cid); depth += 1
        return cid, depth
    return info


def _level_expr(c):
    cols = {r[1] for r in c.execute('PRAGMA table_info(reviews)')}
    return 'rv.review_level' if 'review_level' in cols else 'NULL'


def _level(row_level, row_payload):
    if isinstance(row_level, str):
        return row_level
    try:
        level = json.loads(row_payload).get('review_level') if row_payload else None
    except (ValueError, TypeError, AttributeError):
        return None
    return level if isinstance(level, str) else None


def _claim_info(svc, c, claim_id):
    children = _children(c)
    parent = {new: old for old, new in children.items()}
    record_id, version = _lineage(children)(claim_id)
    row = c.execute('SELECT s.review_id, t.revision FROM claim_snapshots s JOIN transactions t ON t.id=s.tx WHERE s.claim_id=?', (claim_id,)).fetchone()
    level = None
    if row:
        cols = {r[1] for r in c.execute('PRAGMA table_info(reviews)')}
        rv = c.execute('SELECT %s AS lvl, payload FROM reviews WHERE id=?' % ('review_level' if 'review_level' in cols else 'NULL'), (row['review_id'],)).fetchone()
        level = _level(rv['lvl'], rv['payload']) if rv else None
    sha = c.execute('SELECT raw_sha256 FROM claims WHERE id=?', (claim_id,)).fetchone()
    return {'record_id': record_id, 'logical_claim_id': record_id, 'version': version,
            'supersedes': parent.get(claim_id), 'review_level': level,
            'revision': row['revision'] if row else 0, 'raw_sha256': sha[0] if sha else ''}


def pending_rejected(c, branches):
    if not branches:
        return 0, 0
    ph = ','.join('?' * len(branches))
    pending = rejected = 0
    for r in c.execute(
            "SELECT c.id, EXISTS(SELECT 1 FROM claims cl WHERE cl.candidate=c.id) AS a1, "
            "EXISTS(SELECT 1 FROM idempotency i WHERE i.operation LIKE 'admit:%%' AND i.candidate_id=c.id) AS a2, "
            "(SELECT rr.payload FROM reviews rr WHERE rr.candidate=c.id ORDER BY rr.rowid DESC LIMIT 1) AS rp "
            "FROM candidates c WHERE c.branch IN (%s) AND NOT EXISTS (SELECT 1 FROM reviews sr WHERE sr.candidate=c.id "
            "AND json_extract(sr.payload,'$.decision')='superseded')" % ph, list(branches)):
        if r['a1'] or r['a2']:
            continue
        decision = None
        if r['rp']:
            try:
                decision = json.loads(r['rp']).get('decision')
            except (ValueError, TypeError, AttributeError):
                decision = None
        if decision is not None and decision != 'approve':
            rejected += 1
        else:
            pending += 1
    return pending, rejected


def _find_identical(svc, c, branch, item, after_rowid=0, exclude=None, unlinked=False):
    """A current claim of this branch with the same type, bytes, metadata and requirement refs."""
    raw = base64.b64decode(item['body_b64'])
    archived = _archived(c)
    linked = set(_children(c).values()) if unlinked else set()
    for row in c.execute('SELECT rowid AS rid, id, payload FROM claims WHERE origin_branch=? AND raw_sha256=? AND content_type=? ORDER BY rowid',
                         (branch, raw_hash(raw), item['content_type'])):
        if row['id'] == exclude or row['id'] in archived or row['id'] in linked or row['rid'] <= after_rowid:
            continue
        other = json.loads(row['payload'])
        if (other.get('metadata') == item['metadata'] and other.get('anatomy') == item.get('anatomy')
                and sorted(other.get('requirement_refs', [])) == item['requirement_refs']):
            return row['id']
    return None


# -------------------------------------------------------------------- writes --
def _admitted_receipt(svc, candidate_id):
    with svc.store.read() as c:
        row = c.execute("SELECT result FROM idempotency WHERE external_work_ref=? AND operation LIKE 'admit:%' AND candidate_id=?",
                        (svc.context.work, candidate_id)).fetchone()
    return json.loads(row[0]) if row else None


def _commit(svc, branch, build):
    """submit -> self review -> admit through the existing methods; one bounded retry on a moved head."""
    import time
    res = {'status': 'REVISION_CONFLICT'}
    conflicts = busy = 0
    while True:
        # QA004 F-29/F-30: a second session on the same project makes the head move
        # (REVISION_CONFLICT) or the file busy (STORAGE_BUSY).  Both are retried: every
        # step is idempotent (the package key names the revision it was built on).
        with svc.store.read() as c:
            revision = svc.head(c, branch)
        pkg = build(revision)
        sub = svc.submit(json.dumps(pkg))
        if sub.get('status') == 'STORAGE_BUSY' and busy < 8:
            busy += 1; time.sleep(0.05 * (2 ** min(busy, 5))); continue
        if sub.get('status') != 'RECORDED_CANDIDATE':
            return _surface(sub)
        cid, dig = sub['candidate_id'], sub['intent_digest']
        receipt = _admitted_receipt(svc, cid)
        if receipt is None:
            rv = svc.review(cid, dig)
            if rv.get('status') == 'STORAGE_BUSY' and busy < 8:
                busy += 1; time.sleep(0.05 * (2 ** min(busy, 5))); continue
            if rv.get('status') != 'DEMO_REVIEWED':
                return rv
            res = svc.admit(cid, rv['review_id'], pkg['idempotency_key'], dig)
            if res.get('status') == 'STORAGE_BUSY' and busy < 8:
                busy += 1; time.sleep(0.05 * (2 ** min(busy, 5))); continue
            if res.get('status') in ('REVISION_CONFLICT', 'STALE_REVIEW') and conflicts < 5:
                conflicts += 1; continue
            if res.get('status') != 'DEMO_ADMITTED':
                return res
            receipt = res
        return receipt


def _surface(sub):
    """A validation refusal of the server-built package becomes a field-level hint."""
    if sub.get('status') == 'REJECTED' and sub.get('errors'):
        first = sub['errors'][0]
        return {'status': 'INVALID_FIELD', 'field': str(first.get('path'))[:80],
                'hint': 'Field %s refused (%s). %s' % (first.get('path'), first.get('code'), first.get('fix', ''))}
    return sub


def _busy_retry(fn, tries=6):
    """Run a step that follows an admitted write.  QA004 F-30: a busy database after the
    commit turned into STORAGE_BUSY although the claim was recorded; the caller then
    retried a write that had succeeded.  Busy here is waited out, not reported."""
    import sqlite3
    import time
    for attempt in range(tries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            code = getattr(e, 'sqlite_errorcode', None)
            if code is None or (code & 255) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or attempt == tries - 1:
                raise
            time.sleep(0.05 * (2 ** attempt))


def _info_of(svc, claim_id):
    def read():
        with svc.store.read() as c:
            return _claim_info(svc, c, claim_id)
    return _busy_retry(read)


def _write_claim(svc, branch, item, origin_version, reason, tag, idempotency_key=None):
    """Admit one claim item on ``branch`` (or return the identical current one). -> dict with claim_id | refusal."""
    with svc.store.read() as c:
        existing = _find_identical(svc, c, branch, item)
        if existing:
            info = _claim_info(svc, c, existing)
            return dict(info, claim_id=existing, already=True)
    item_key = digest({'tag': tag, 'branch': branch, 'item': dict(item, local_id='')})[:40]

    def build(revision):
        key = idempotency_key or 'lite-%s-%s-r%d' % (tag, item_key[:24], revision)
        return _package(svc, branch, revision, [item], key, 'claims', origin_version, reason)
    receipt = _commit(svc, branch, build)
    if receipt.get('status') != 'DEMO_ADMITTED':
        return receipt
    claim_id = receipt['claim_ids'][item['local_id']]
    return dict(_info_of(svc, claim_id), claim_id=claim_id, already=False)


MAPPING_FIELDS = ('units', 'notation', 'columns')  # symbol -> meaning/unit/type: both sides must say something


def _check_structured(name, value):
    """Object/array arguments: storable in dom-c14n (QA004 F-35: a key the canonical form
    refuses was an INTERNAL_ERROR) and, for symbol maps, no blank keys or values (F-02)."""
    Denied = _denied()
    from .dom_contract import check_tree
    try:
        check_tree(value)
    except ValueError as e:
        hints = {'INVALID_KEY': 'keys must be text without emoji or other characters outside the Basic Multilingual Plane',
                 'INVALID_NUMBER': 'numbers must be whole and non-negative (no fractions); write 0.5 as the string "0.5"',
                 'INVALID_UNICODE': 'text contains an unpaired surrogate',
                 'INVALID_TYPE': 'only strings, whole numbers, booleans, null, arrays and objects are stored'}
        raise Denied('INVALID_FIELD', field=name, hint="'%s': %s." % (name, hints.get(str(e), str(e)))) from None
    if name in MAPPING_FIELDS and isinstance(value, dict):
        for k, v in value.items():
            if not k.strip() or (isinstance(v, str) and not v.strip()) or v is None:
                raise Denied('INVALID_FIELD', field=name,
                             hint="'%s' maps a symbol to a non-empty value, e.g. {\"t\": \"s\"}; %r -> %r is blank." % (name, k[:40], v if not isinstance(v, str) else v[:40]))


def _validated_claim_args(svc, branch, typ, text, fields):
    """Shared argument checks of dom_claim. -> (raw, args, requirement_refs, reason, origin_version)."""
    Denied = _denied()
    if type(branch) is not str or not branch:
        raise Denied('INVALID_FIELD', field='branch', hint="'branch' must be one of the declared branch codes (see dom_describe).")
    svc.scope(branch)
    if type(typ) is not str or typ not in TYPES:
        raise Denied('UNKNOWN_TYPE', field='type')
    raw = _text_bytes(text)
    args = {}
    anatomy = {}
    for name, value in fields.items():
        if value is None:
            continue
        if name in LINK_ARGS:
            if type(value) is not LINK_ARGS[name] or not value.strip():
                raise Denied('INVALID_FIELD', field=name)
            continue
        if name in ANATOMY_ARGS:
            if type(value) is not ANATOMY_ARGS[name]:
                raise Denied('INVALID_FIELD', field=name, hint="'%s' must be of type %s." % (name, ANATOMY_ARGS[name].__name__))
            if type(value) is str and not value.strip():  # F-04: refused, not silently dropped
                raise Denied('INVALID_FIELD', field=name, hint="'%s' must be a non-empty string; omit it if there is nothing to say." % name)
            if not _missing(value):
                anatomy[name] = sorted(set(value)) if name in ('keywords', 'usage') else value
            continue
        if name in COMMON_FIELDS:
            if type(value) is not COMMON_FIELDS[name]:
                raise Denied('INVALID_FIELD', field=name)
            continue
        if name not in FIELD_TYPES:
            raise Denied('INVALID_FIELD', field=name, hint="Unknown argument '%s'." % name)
        if type(value) is not FIELD_TYPES[name]:
            raise Denied('INVALID_FIELD', field=name, hint="'%s' must be of type %s." % (name, FIELD_TYPES[name].__name__))
        if name == 'commit_ref' and not valid_commit_ref(value):
            raise Denied('INVALID_FIELD', field='commit_ref',
                         hint="'commit_ref' needs {'hash': 40|64 hex, 'branch': non-empty}; optional author/message/committed_at are non-empty strings.")
        if name not in TYPE_ARGS[typ]:
            raise Denied('INVALID_FIELD', field=name,
                         hint="'%s' does not apply to type '%s'. Allowed extra arguments for %s: %s." % (name, typ, typ, ', '.join(TYPE_ARGS[typ])))
        if isinstance(value, (dict, list)):
            _check_structured(name, value)
        args[name] = value
    refs = _str_list(fields.get('requirement_refs') or [], 'requirement_refs')
    reason = fields.get('reason') or 'lite dom_claim'
    origin_version = fields.get('origin_version') or DEFAULT_ORIGIN_VERSION
    if type(reason) is not str or not reason.strip():
        raise Denied('INVALID_FIELD', field='reason')
    if type(origin_version) is not str or not origin_version.strip():
        raise Denied('INVALID_FIELD', field='origin_version')
    for name in REQUIRED_FIELDS.get(typ, ()):
        if _missing(args.get(name)):
            raise Denied('MISSING_FIELD', field=name,
                         hint="type '%s' requires '%s' (non-empty). Required for %s: %s. Nothing was recorded." % (typ, name, typ, ', '.join(REQUIRED_FIELDS[typ])))
    if typ == 'text' and args.get('classification') == 'factual' and not args.get('evidence'):
        raise Denied('INVALID_FIELD', field='evidence', hint="classification 'factual' needs evidence: [{\"ref\": \"...\", \"locator\": \"...\"}]; or omit classification for an interpretation.")
    from .dom_contract import anatomy_errors
    if anatomy:
        bad = anatomy_errors(anatomy)
        if bad:
            raise Denied('INVALID_FIELD', field=bad[0].split('.', 1)[-1],
                         hint='basis/usage/keywords: arrays of non-empty strings (<=64, <=512 chars); refines/purpose: non-empty strings.')
    return raw, args, refs, reason, origin_version, anatomy


def _plan_branch(svc, branch, fields):
    """dom_claim(plan_task=...): the branch comes from the plan item. -> (branch, task_row|None, role)."""
    Denied = _denied()
    ref = fields.get('plan_task')
    role = fields.get('link_role')
    if ref is None:
        if role is not None:
            raise Denied('INVALID_FIELD', field='link_role', hint="'link_role' needs 'plan_task'.")
        return branch, None, None
    from . import planning
    if role is None:
        role = 'documents'
    if role not in planning.CLAIM_ROLES:
        raise Denied('INVALID_FIELD', field='link_role', hint="'link_role' is one of: %s." % ', '.join(planning.CLAIM_ROLES))
    svc.scope()
    with svc.store.read() as c:
        task = planning.resolve_task(c, ref)
    if task is None or task['branch'] is None:
        raise Denied('TASK_NOT_FOUND', field='plan_task', hint='No plan item with this id, path (П1.2) or branch code. Call dom_plan_tree or dom_plan_ingest first.')
    if task['archived']:
        raise Denied('TASK_ARCHIVED', field='plan_task', hint='This plan item was removed from the plan; link the claim to a current item.')
    if branch not in (None, '', task['branch']):
        raise Denied('INVALID_FIELD', field='branch', hint="With 'plan_task' omit 'branch' (it is %s) or pass exactly that code." % task['branch'])
    return task['branch'], task, role


def claim(svc, branch, typ, text, fields):
    svc.capability('submit')
    svc.ready()
    fields = dict(fields)
    # Internal (never an MCP argument: the transport refuses unknown keys):
    # LLM candidates and the manual review mode only submit.
    submit_only = bool(fields.pop('_submit_only', False)) or svc.candidate_only
    branch, task, role = _plan_branch(svc, branch, fields)
    raw, args, refs, reason, origin_version, anatomy = _validated_claim_args(svc, branch, typ, text, fields)
    if 'refines' in anatomy:  # F-03: no dangling refines in an immutable record
        with svc.store.read() as c:
            target = c.execute('SELECT origin_branch FROM claims WHERE id=?', (anatomy['refines'],)).fetchone()
        if target is None or target[0] not in svc.context.branches:
            raise _denied()('INVALID_FIELD', field='refines',
                            hint="'refines' must be the claim_id of an existing claim (dom_find); nothing was recorded.")
    if task is not None:
        anatomy['plan_ref'] = task['plan_task_id']
        if 'refines' not in anatomy:
            from . import planning
            with svc.store.read() as c:
                header = planning._header_claim(c, task['plan_task_id'])
            if header:
                anatomy['refines'] = header
    item = build_item(typ, text, raw, branch, args, refs, reason, anatomy=anatomy or None)
    idempotency_key = fields.get('idempotency_key') or None
    if svc.lite and submit_only:
        with svc.store.read() as c:
            existing = _find_identical(svc, c, branch, item)
            if existing:
                info = _claim_info(svc, c, existing)
                return {'status': 'ALREADY_RECORDED', 'simulated': True, 'claim_id': existing, 'record_id': info['record_id'],
                        'logical_claim_id': info['logical_claim_id'], 'version': info['version'], 'branch': branch, 'type': typ,
                        'revision': info['revision'], 'review_level': info['review_level'], 'origin_version': origin_version,
                        'raw_sha256': info['raw_sha256'], 'next_steps': []}
    if svc.lite and not submit_only:
        forked_from = None
        if task is not None:
            from . import planning
            other = planning.variant_target(svc, branch, item)
            if other is not None:
                # Same body, other properties on the same plan item: a fork
                # into a parallel variant branch, not a duplicate.
                forked_from = other
                branch = planning._allocate_variant(svc, branch, task['plan_task_id'])
                item['applicability'] = [branch]
        res = _write_claim(svc, branch, item, origin_version, reason, 'claim', idempotency_key)
        if 'claim_id' not in res:
            return res
        cid = res['claim_id']
        if task is not None:
            from . import planning
            _busy_retry(lambda: planning.link(svc, task['plan_task_id'], cid, role))
            if forked_from is not None and not res['already']:
                _busy_retry(lambda: planning._append(svc, branch, [planning._prop('v1', cid, 'variant_of', forked_from, 'fork: same body, other properties')],
                                                     'fork: same body, other properties', 'variant'))
        steps = [
            {'tool': 'dom_find', 'arguments': {'branch': branch, 'query': text[:30]}, 'why': 'confirm the claim is stored and findable'},
            {'tool': 'dom_update', 'arguments': {'claim_id': cid, 'text': '<the full new text>', 'reason': '<why it changes>'},
             'why': 'to change this claim later: creates a new version, this one stays in history'},
            {'tool': 'dom_trace', 'arguments': {}, 'why': 'readiness: admitted_total and admitted_independent are separate numbers'},
        ]
        out = {'status': 'ALREADY_RECORDED' if res['already'] else 'CLAIM_ADMITTED', 'simulated': True, 'claim_id': cid,
               'record_id': res['record_id'], 'logical_claim_id': res['logical_claim_id'], 'version': res['version'], 'branch': branch, 'type': typ,
               'revision': res['revision'], 'review_level': res['review_level'], 'origin_version': origin_version,
               'raw_sha256': res['raw_sha256'], 'next_steps': steps}
        if task is not None:
            out.update(plan_task=task['plan_task_id'], plan_path=task['path'], link_role=role)
        if forked_from is not None:
            out['forked_from'] = forked_from
        return out
    # strict profile: independent review required, so only submit a candidate;
    # the caller then runs dom_review (different root) + dom_admit.
    with svc.store.read() as c:
        revision = svc.head(c, branch)
    item_key = digest({'tag': 'claim', 'branch': branch, 'item': dict(item, local_id='')})[:40]
    key = idempotency_key or 'strict-claim-%s-r%d' % (item_key[:24], revision)
    pkg = _package(svc, branch, revision, [item], key, 'claims', origin_version, reason)
    sub = svc.submit(json.dumps(pkg))
    if sub.get('status') != 'RECORDED_CANDIDATE':
        return _surface(sub)
    return {'status': 'RECORDED_CANDIDATE', 'simulated': True, 'candidate_id': sub['candidate_id'],
            'intent_digest': sub['intent_digest'], 'branch': branch, 'type': typ,
            'next_steps': [
                {'tool': 'dom_review', 'arguments': {'candidate_id': sub['candidate_id'], 'intent_digest': sub['intent_digest']},
                 'why': 'independent review required (different root) before admission'},
                {'tool': 'dom_admit', 'arguments': {'candidate_id': sub['candidate_id'], 'review_id': '<review_id>',
                                                    'idempotency_key': key, 'intent_digest': sub['intent_digest']},
                 'why': 'atomic admission after an independent review'}]}


RESERVED_PROPERTIES = frozenset((
    'body', 'body_b64', 'raw', 'text', 'origin_branch', 'origin_version', 'claim_id', 'record_id', 'logical_claim_id',
    'version', 'is_current', 'supersedes', 'review_level', 'archived', 'type', 'content_type', 'branch',
    # the plan mirror writes these on header claims (planning._sync_header); variants/merges use the last two
    'plan_path', 'branch_code', 'plan_status', 'context', 'variant_of', 'merged_into'))

ANNOTATIONS = {'comment': ('text',), 'link': ('url', 'locator'), 'property': ('name', 'value'), 'archive': (),
               'conflict': ('conflict_ref',), 'resolve': ('conflict_ref',)}


def annotate(svc, claim_id, kind, fields):
    """Append one event to a claim: comment, link, property, archive, conflict, resolve.

    The claim itself never changes; the event is a new row of its history.
    Under the manual / independent review policy the event waits for review.
    """
    Denied = _denied()
    _lite_only(svc)
    svc.scope()
    svc.capability('submit')
    svc.ready()
    if kind not in ANNOTATIONS:
        raise Denied('INVALID_FIELD', field='kind', hint="'kind' is one of: %s." % ', '.join(ANNOTATIONS))
    value = {}
    for name in ANNOTATIONS[kind]:
        v = fields.get(name)
        if type(v) is not str or not v.strip():
            raise Denied('MISSING_FIELD', field=name, hint="kind '%s' needs: %s." % (kind, ', '.join(ANNOTATIONS[kind])))
        value[name] = v
    if kind == 'property' and value['name'].strip().casefold() in RESERVED_PROPERTIES:  # QA004 F-11, F-14
        raise Denied('INVALID_FIELD', field='name',
                     hint="'%s' is a server field (body, versions, review label or plan mirror) and cannot be set as a property. "
                          "Use another name, e.g. 'note_%s'." % (value['name'][:40], value['name'].strip()[:20]))
    extra = set(k for k, v in fields.items() if v is not None) - set(ANNOTATIONS[kind]) - {'reason'}
    if extra:
        raise Denied('INVALID_FIELD', field=sorted(extra)[0], hint="kind '%s' takes only: %s (and reason)." % (kind, ', '.join(ANNOTATIONS[kind]) or 'reason'))
    reason = fields.get('reason')
    if kind in ('archive', 'conflict', 'resolve') and (type(reason) is not str or not reason.strip()):
        raise Denied('MISSING_FIELD', field='reason', hint="kind '%s' needs a reason (it is recorded in the event chain)." % kind)
    reason = reason if type(reason) is str and reason.strip() else 'dom_annotate %s' % kind
    with svc.store.read() as c:
        row = c.execute('SELECT origin_branch, origin_version FROM claims WHERE id=?', (claim_id,)).fetchone() if type(claim_id) is str else None
        if row is None or row['origin_branch'] not in svc.context.branches:
            raise Denied('NO_SUCH_CLAIM', field='claim_id')
        if kind == 'archive' and claim_id in _archived(c):
            raise Denied('NOT_CURRENT', claim_id=claim_id)
        branch = row['origin_branch']
        revision = svc.head(c, branch)
    event = {'local_id': 'e1', 'event_type': kind, 'claim_ref': claim_id, 'reason': reason, 'value': value,
             'supersedes': '', 'ref': 'dom_annotate'}
    key_base = digest({'claim': claim_id, 'event': event})[:24]

    def build(rev):
        return _package(svc, branch, rev, [event], 'ann-%s-r%d' % (key_base, rev), 'append', row['origin_version'], reason)
    if svc.candidate_only:
        sub = svc.submit(json.dumps(build(revision)))
        if sub.get('status') != 'RECORDED_CANDIDATE':
            return _surface(sub)
        return {'status': 'RECORDED_CANDIDATE', 'simulated': True, 'candidate_id': sub['candidate_id'], 'intent_digest': sub['intent_digest'],
                'branch': branch, 'type': kind,
                'next_steps': [{'tool': 'dom_accept', 'arguments': {'candidate_id': sub['candidate_id']}, 'why': 'the reviewer approves the event'}]}
    receipt = _commit(svc, branch, build)
    if receipt.get('status') != 'DEMO_ADMITTED':
        return receipt
    return {'status': 'ANNOTATED', 'simulated': True, 'claim_id': claim_id, 'kind': kind, 'branch': branch,
            'revision': receipt['revision'], 'event_id': receipt['event_ids']['e1']}


def accept(svc, candidate_id, decision='approve', note=None):
    """Lite: the owner / local agent decides on a candidate it has read (dom_candidate).

    approve -> review + admit in one call.  When the branch head moved since the
    candidate was submitted (another candidate was admitted first), the same
    items are re-submitted on the current head and admitted; the stale
    candidate gets a 'superseded' review that names the new one, so it leaves
    the pending list without being counted as rejected.
    """
    Denied = _denied()
    _lite_only(svc)
    svc.scope()
    if decision not in ('approve', 'reject'):
        raise Denied('INVALID_FIELD', field='decision')
    view = svc.candidate_view(candidate_id)
    if view.get('status') != 'CANDIDATE':
        return view
    if view['state'] in ('ADMITTED', 'REJECTED', 'SUPERSEDED'):
        raise Denied('CANDIDATE_CLOSED', field='candidate_id', hint='This candidate is already %s.' % view['state'].lower())
    if decision == 'reject':
        return svc.review(candidate_id, view['intent_digest'], 'reject', note)
    with svc.store.read() as c:
        p = json.loads(c.execute('SELECT payload FROM candidates WHERE id=?', (candidate_id,)).fetchone()[0])
        head = svc.head(c, p['branch'])
    if head == p['expected_branch_revision']:
        review = svc.review(candidate_id, view['intent_digest'], 'approve', note)
        if review.get('status') != 'DEMO_REVIEWED':
            return review
        receipt = svc.admit(candidate_id, review['review_id'], 'accept-%s' % candidate_id, view['intent_digest'])
        if receipt.get('status') != 'REVISION_CONFLICT':
            return dict(receipt, candidate_id=candidate_id) if receipt.get('status') == 'DEMO_ADMITTED' else receipt
    if svc.independent:
        # A rebase would make the reviewer the author of the new candidate.
        raise Denied('REVISION_CONFLICT', hint='The branch moved since this candidate was submitted. Under the independent review policy the author must submit it again; reject this one with a note.')
    items = p['items']

    def build(revision):
        return _package(svc, p['branch'], revision, items, 'accept-%s-r%d' % (candidate_id, revision), p['operation'],
                        p['origin_version'], p['reason'])
    receipt = _commit(svc, p['branch'], build)
    if receipt.get('status') != 'DEMO_ADMITTED':
        return receipt
    with svc.store.read() as c:
        rebased = c.execute('SELECT candidate FROM claims WHERE tx=? LIMIT 1', (receipt['tx_id'],)).fetchone()
    svc.review(candidate_id, view['intent_digest'], 'superseded', 'rebased onto revision %d as candidate %s' % (receipt['revision'] - 1, rebased[0] if rebased else '?'), True)
    return dict(receipt, candidate_id=candidate_id, rebased=True)


def _rebuild_for_update(old_item, old_text, old_raw, new_text, new_raw, reason):
    typ = old_item['content_type']
    meta = dict(old_item['metadata'])
    # A new code body is not the content of the old commit: do not carry a
    # stale commit_ref into the new version.
    if typ == 'code':
        meta.pop('commit_ref', None)
    old_d, new_d = _derived(typ, old_text, old_raw), _derived(typ, new_text, new_raw)
    for key, new_value in new_d.items():
        if key == 'source_b64' or meta.get(key) == old_d.get(key):
            meta[key] = new_value
    item = dict(old_item)
    item.update(local_id='u1', body_b64=base64.b64encode(new_raw).decode(), metadata=meta, reason=reason)
    return item


class _LostRace(Exception):
    def __init__(self, winner):
        super().__init__(winner)
        self.winner = winner


# ------------------------------------------------------------ stitching (T-09) --
def _merged_into(c):
    """claim id -> claim it was merged into (property merged_into, written by dom_reconcile)."""
    out = {}
    for row in c.execute("SELECT claim_ref, payload FROM events WHERE kind='property' AND claim_ref IS NOT NULL ORDER BY seq"):
        try:
            value = json.loads(row[1])['value']['value']
        except (ValueError, TypeError, KeyError):
            continue
        if value.get('name') == 'merged_into':
            out.setdefault(row[0], value.get('value'))
    return out


def _step_card(c, kind, claim_id):
    """who / when / why of the event that moved claim_id on."""
    if kind == 'replaced':
        rows = c.execute("SELECT payload FROM events WHERE kind='replacement' AND claim_ref=? ORDER BY seq LIMIT 1", (claim_id,)).fetchall()
    elif kind == 'merged':
        rows = [r for r in c.execute("SELECT payload FROM events WHERE kind='property' AND claim_ref=? ORDER BY seq", (claim_id,))
                if (json.loads(r[0]).get('value', {}).get('value') or {}).get('name') == 'merged_into'][:1]
    else:
        rows = c.execute("SELECT payload FROM events WHERE kind='archive' AND claim_ref=? ORDER BY seq LIMIT 1", (claim_id,)).fetchall()
    if not rows:
        return {'by': None, 'at': None, 'reason': None}
    p = json.loads(rows[0][0])
    return {'by': p.get('actor'), 'at': p.get('server_time'), 'reason': p.get('reason')}


def resolve_claim(c, claim_id, max_steps=64):
    """Where does this claim live now?  Follows replacements and merges to the live version.

    An agent that remembers the id of a claim it wrote must not get a dead end after
    another agent updated it, or after dom_reconcile merged its variant into the base:
    it gets the current version and every step that led there (who, when, why).
    status: current | replaced | merged (the chain contains a merge) | archived (retired,
    no successor).  ``variants``: current forks of the resolved version (open splits)."""
    children = _children(c)
    merged = _merged_into(c)
    archived = _archived(c)
    steps, cur, seen = [], claim_id, {claim_id}
    while len(steps) < max_steps:
        if cur in children:
            kind, nxt = 'replaced', children[cur]
        elif cur in merged and merged[cur]:
            kind, nxt = 'merged', merged[cur]
        else:
            break
        if nxt in seen:
            break
        steps.append(dict({'kind': kind, 'from_claim_id': cur, 'to_claim_id': nxt}, **_step_card(c, kind, cur)))
        seen.add(nxt)
        cur = nxt
    if cur in archived:
        status = 'archived'
        retired = _step_card(c, 'archived', cur)
    else:
        status = 'current' if not steps else ('merged' if any(s['kind'] == 'merged' for s in steps) else 'replaced')
        retired = None
    variants = []
    for row in c.execute("SELECT claim_ref, payload FROM events WHERE kind='property' AND claim_ref IS NOT NULL ORDER BY seq"):
        try:
            value = json.loads(row[1])['value']['value']
        except (ValueError, TypeError, KeyError):
            continue
        if value.get('name') == 'variant_of' and value.get('value') == cur:
            tip = row[0]
            while tip in children:
                tip = children[tip]
            if tip not in archived:
                variants.append({'claim_id': tip, 'author': _author_card(c, tip)[0],
                                 'branch': c.execute('SELECT origin_branch FROM claims WHERE id=?', (tip,)).fetchone()[0]})
    words = {'replaced': 'was replaced by', 'merged': 'was merged into'}
    story = ['%s %s %s%s%s' % (s['from_claim_id'][:8], words[s['kind']], s['to_claim_id'][:8],
                                ' by %s' % s['by'] if s['by'] else '', ' (%s)' % s['reason'] if s['reason'] else '') for s in steps]
    if status == 'archived':
        story.append('%s was retired%s; it has no current version' % (cur[:8], ' (%s)' % retired['reason'] if retired and retired['reason'] else ''))
    elif steps:
        story.append('the current version is %s' % cur)
    out = {'requested_claim_id': claim_id, 'status': status, 'current_claim_id': None if status == 'archived' else cur,
           'last_claim_id': cur, 'steps': steps, 'variants': variants, 'explanation': '; '.join(story) if story else 'this is the current version'}
    if retired:
        out['retired'] = retired
    return out


def resolve_for(svc, claim_id):
    with svc.store.read() as c:
        return resolve_claim(c, claim_id)


def _author_card(c, claim_id):
    row = c.execute('SELECT ca.author, ca.root FROM claims cl JOIN candidates ca ON ca.id=cl.candidate WHERE cl.id=?', (claim_id,)).fetchone()
    return (row[0], row[1]) if row else (None, None)


def update(svc, claim_id, text, reason, idempotency_key=None, anatomy=None, expected_current=None, mode='auto'):
    Denied = _denied()
    if mode not in ('auto', 'update', 'fork'):
        raise Denied('INVALID_FIELD', field='mode', hint="'mode' is auto (default), update or fork.")
    if expected_current is not None and (type(expected_current) is not str or not expected_current.strip()):
        raise Denied('INVALID_FIELD', field='expected_current')
    if expected_current is not None or mode != 'auto':
        return _guarded_update(svc, claim_id, text, reason, idempotency_key, anatomy, expected_current, mode)
    return _plain_update(svc, claim_id, text, reason, idempotency_key, anatomy)


def _guarded_update(svc, claim_id, text, reason, idempotency_key, anatomy, expected_current, mode):
    """T-09 / TD-DOM-60: read-before-write and a fork for a version written by another agent.

    expected_current names the version the caller read.  If the record moved on, the
    answer is CONFLICT_READ_REQUIRED with the current version and nothing is written.
    mode=fork (only after reading, only over another author's version) records the
    caller's text as a parallel variant: both versions are current, linked by
    variant_of and an open conflict that dom_reconcile resolves when they converge."""
    Denied = _denied()
    _lite_only(svc)
    svc.scope()
    svc.capability('submit')
    svc.ready()
    with svc.store.read() as c:
        row = c.execute('SELECT origin_branch FROM claims WHERE id=?', (claim_id,)).fetchone() if type(claim_id) is str else None
        if row is None or row['origin_branch'] not in svc.context.branches:
            raise Denied('NO_SUCH_CLAIM', field='claim_id')
        resolution = resolve_claim(c, claim_id)
        current = resolution['last_claim_id']
        author, root = _author_card(c, current)
        info = _claim_info(svc, c, current)
    if resolution['status'] == 'archived':
        raise Denied('NOT_CURRENT', claim_id=claim_id, resolution=resolution, hint=resolution['explanation'])
    if expected_current is None or expected_current != current:
        return {'status': 'CONFLICT_READ_REQUIRED', 'simulated': True, 'claim_id': claim_id, 'expected_current': expected_current,
                'current_claim_id': current, 'author': author, 'root': root, 'version': info['version'], 'revision': info['revision'],
                'resolution': resolution,
                'next_steps': [{'tool': 'dom_claim_versions', 'arguments': {'claim_id': current}, 'why': 'read the current version first'},
                               {'tool': 'dom_update', 'arguments': {'claim_id': current, 'expected_current': current, 'text': '<text>',
                                                                   'reason': '<why>', 'mode': 'update|fork'},
                                'why': 'update it, or fork if it is another agent\'s version that does not fit'}]}
    if mode == 'fork':
        if author == svc.context.principal:
            raise Denied('FORK_NOT_ALLOWED', field='mode',
                         hint='The current version is your own: update it (mode=update) or leave it. A fork is for another agent\'s version.')
        return _fork(svc, current, text, reason, author)
    return _plain_update(svc, current, text, reason, idempotency_key, anatomy)


def _archived_now(svc):
    with svc.store.read() as c:
        return _archived(c)


def _fork(svc, current, text, reason, base_author):
    Denied = _denied()
    from . import planning
    if type(reason) is not str or not reason.strip():
        raise Denied('INVALID_FIELD', field='reason')
    new_raw = _text_bytes(text)
    with svc.store.read() as c:
        row = c.execute('SELECT * FROM claims WHERE id=?', (current,)).fetchone()
        old_item = json.loads(row['payload'])
        old_raw = bytes(row['raw'])
        branch = row['origin_branch']
    if new_raw == old_raw:
        raise Denied('NO_CHANGE', claim_id=current, hint='Your text equals the current version: there is nothing to fork.')
    anatomy = dict(old_item.get('anatomy') or {})
    anatomy.pop('supersedes', None)
    task_id = anatomy.get('plan_ref')
    variant = planning._allocate_variant(svc, branch, task_id or branch)
    item = _rebuild_for_update(old_item, old_raw.decode('utf-8'), old_raw, text, new_raw, reason)
    item.update(local_id='f1', applicability=[variant])
    if anatomy:
        item['anatomy'] = anatomy
    origin_version = _origin_version_of(svc, current)
    res = _write_claim(svc, variant, item, origin_version, reason, 'fork')
    if 'claim_id' not in res:
        return res
    new_id = res['claim_id']
    split = 'fork-%s' % digest({'base': current, 'fork': new_id})[:16]
    if task_id:
        _busy_retry(lambda: planning.link(svc, task_id, new_id, 'documents'))
    planning._append(svc, variant, [planning._prop('v1', new_id, 'variant_of', current, 'fork: ' + reason),
                                    planning._prop('v2', new_id, 'fork_conflict', split, 'fork: ' + reason),
                                    {'local_id': 'c1', 'event_type': 'conflict', 'claim_ref': new_id, 'reason': reason,
                                     'value': {'conflict_ref': split}, 'supersedes': '', 'ref': 'lite:dom_update fork'}],
                     'fork: ' + reason, 'fork-' + split)
    planning._append(svc, branch, [{'local_id': 'c1', 'event_type': 'conflict', 'claim_ref': current, 'reason': reason,
                                    'value': {'conflict_ref': split}, 'supersedes': '', 'ref': 'lite:dom_update fork'}],
                     'fork: ' + reason, 'fork-base-' + split)
    info = _info_of(svc, new_id)
    return {'status': 'CLAIM_FORKED', 'simulated': True, 'claim_id': new_id, 'forked_from': current, 'forked_from_author': base_author,
            'branch': variant, 'conflict_ref': split, 'version': info['version'], 'revision': info['revision'],
            'review_level': info['review_level'], 'raw_sha256': info['raw_sha256'],
            'next_steps': [{'tool': 'dom_trace', 'arguments': {}, 'why': 'the split stays visible (CONFLICT) until the versions converge'},
                           {'tool': 'dom_reconcile', 'arguments': {}, 'why': 'merges the fork when both versions become equal again'}]}


def _plain_update(svc, claim_id, text, reason, idempotency_key=None, anatomy=None):
    Denied = _denied()
    _lite_only(svc)
    svc.scope()
    svc.capability('submit')
    svc.ready()
    if type(claim_id) is not str or not claim_id:
        raise Denied('INVALID_FIELD', field='claim_id')
    if type(reason) is not str or not reason.strip():
        raise Denied('INVALID_FIELD', field='reason', hint="'reason' is required: say why the claim changes (it is recorded in the event chain).")
    new_raw = _text_bytes(text)
    if anatomy is not None:
        from .dom_contract import ANATOMY_FIELDS, anatomy_errors
        if type(anatomy) is not dict or set(anatomy) - (set(ANATOMY_FIELDS) - {'plan_ref', 'supersedes'}) or anatomy_errors(anatomy):
            raise Denied('INVALID_FIELD', field='anatomy',
                         hint='anatomy: {basis?, refines?, purpose?, usage?, keywords?} with non-empty values; plan_ref cannot change.')
    with svc.store.read() as c:
        row = c.execute('SELECT rowid AS rid, * FROM claims WHERE id=?', (claim_id,)).fetchone()
        # Audit 2026-11-12 #6: absent and out-of-scope give the same answer.
        if row is None or row['origin_branch'] not in svc.context.branches:
            raise Denied('NO_SUCH_CLAIM', field='claim_id')
        branch = row['origin_branch']
        children = _children(c)
        archived = _archived(c)
        old_raw = bytes(row['raw'])
        old_item = json.loads(row['payload'])
        old_rowid = row['rid']
    if claim_id in children:
        current = children[claim_id]
        while current in children:
            current = children[current]
        if idempotency_key and children[claim_id] == current:
            # Replay of a completed update: same new text -> same answer.
            with svc.store.read() as c:
                done = c.execute('SELECT raw FROM claims WHERE id=?', (current,)).fetchone()
                if done is not None and bytes(done[0]) == new_raw:
                    info = _claim_info(svc, c, current)
                    return {'status': 'CLAIM_UPDATED', 'simulated': True, 'new_claim_id': current, 'supersedes': claim_id,
                            'record_id': info['record_id'], 'logical_claim_id': info['logical_claim_id'], 'version': info['version'],
                            'branch': branch, 'revision': info['revision'], 'review_level': info['review_level'],
                            'raw_sha256': info['raw_sha256'], 'next_steps': []}
        resolution = resolve_for(svc, claim_id)
        raise Denied('NOT_CURRENT', claim_id=resolution['current_claim_id'] or resolution['last_claim_id'], resolution=resolution,
                     hint=resolution['explanation'] + '. Update the current version (or pass expected_current to fork it).')
    if claim_id in archived:
        resolution = resolve_for(svc, claim_id)
        raise Denied('NOT_CURRENT', claim_id=resolution['current_claim_id'] or claim_id, resolution=resolution,
                     hint=resolution['explanation'] + ('.' if resolution['status'] == 'archived' else '. Update the current version.'))
    with svc.store.read() as c:
        root = c.execute('SELECT request_claim_id FROM project_root WHERE id=1').fetchone()
    if root is not None and root[0] == claim_id:  # QA004 F-09: the customer request is immutable
        raise Denied('ROOT_IMMUTABLE', field='claim_id',
                     hint='This is the customer request recorded by dom_start; it is never replaced. Record a clarification as a '
                          'new claim (basis=[this claim_id]) or refine the goal claim with dom_update.')
    merged_anatomy = dict(old_item.get('anatomy') or {}, **(anatomy or {}))
    if new_raw == old_raw and merged_anatomy == (old_item.get('anatomy') or {}):
        raise Denied('NO_CHANGE', claim_id=claim_id)
    old_text = old_raw.decode('utf-8')
    new_item = _rebuild_for_update(old_item, old_text, old_raw, text, new_raw, reason)
    if merged_anatomy:
        new_item['anatomy'] = merged_anatomy
    origin_version = _origin_version_of(svc, claim_id)
    if svc.candidate_only:
        # Reviewed update: one candidate that names the version it replaces;
        # the replacement link is recorded by the server when it is admitted.
        new_item['anatomy'] = dict(new_item.get('anatomy') or {}, supersedes=claim_id)
        with svc.store.read() as c:
            revision = svc.head(c, branch)
        key = idempotency_key or 'upd-%s-r%d' % (digest({'old': claim_id, 'item': dict(new_item, local_id='')})[:24], revision)
        sub = svc.submit(json.dumps(_package(svc, branch, revision, [new_item], key, 'claims', origin_version, reason)))
        if sub.get('status') != 'RECORDED_CANDIDATE':
            return _surface(sub)
        return {'status': 'RECORDED_CANDIDATE', 'simulated': True, 'candidate_id': sub['candidate_id'], 'intent_digest': sub['intent_digest'],
                'branch': branch, 'type': new_item['content_type'],
                'next_steps': [{'tool': 'dom_accept', 'arguments': {'candidate_id': sub['candidate_id']},
                                'why': 'the reviewer approves; the old version is then marked replaced automatically'}]}
    # step 1: the new version (an unlinked identical leftover of an interrupted earlier call is reused)
    with svc.store.read() as c:
        leftover = _find_identical(svc, c, branch, new_item, after_rowid=old_rowid, exclude=claim_id, unlinked=True)
    if leftover:
        new_id = leftover
    else:
        key_base = digest({'old': claim_id, 'item': dict(new_item, local_id='')})[:24]

        def build1(revision):
            key = ('%s:version:r%d' % (idempotency_key, revision)) if idempotency_key else 'lite-upd1-%s-r%d' % (key_base, revision)
            return _package(svc, branch, revision, [new_item], key, 'claims', origin_version, reason)
        receipt = _commit(svc, branch, build1)
        if receipt.get('status') != 'DEMO_ADMITTED':
            return receipt
        new_id = receipt['claim_ids']['u1']
    # step 2: mark the old version as replaced (existing 'replacement' event; the old row is untouched)
    event = {'local_id': 'r1', 'event_type': 'replacement', 'claim_ref': claim_id, 'reason': reason,
             'value': {'replacement_ref': new_id}, 'supersedes': '', 'ref': 'lite:dom_update'}

    def build2(revision):
        # QA004 F-29: two sessions may both pass the NOT_CURRENT check above.  The
        # replacement of claim_id is re-checked here, at the branch revision the
        # package is admitted against: a replacement admitted in between moves the
        # head, so admission refuses with REVISION_CONFLICT and this runs again.
        with svc.store.read() as c:
            winner = _children(c).get(claim_id)
        if winner is not None and winner != new_id:
            raise _LostRace(winner)
        # Audit 2026-11-12 #1: step 2 must not reuse the step-1 key (same
        # submit operation, different digest -> IDEMPOTENCY_MISMATCH).
        key = (('%s:replace:r%d' % (idempotency_key, revision)) if idempotency_key
               else 'lite-upd2-%s-r%d' % (digest({'old': claim_id, 'new': new_id})[:24], revision))
        return _package(svc, branch, revision, [event], key, 'append', origin_version, reason)
    try:
        receipt2 = _commit(svc, branch, build2)
    except _LostRace as lost:
        current = lost.winner
        with svc.store.read() as c:
            children = _children(c)
        while current in children:
            current = children[current]
        # our new version was written in step 1 but replaces nothing: retire it (append-only)
        retire = {'local_id': 'x1', 'event_type': 'archive', 'claim_ref': new_id, 'value': {}, 'supersedes': '',
                  'ref': 'lite:dom_update', 'reason': 'concurrent dom_update lost: %s was already replaced by %s' % (claim_id, lost.winner)}
        _commit(svc, branch, lambda revision: _package(svc, branch, revision, [retire], 'lite-upd-lost-%s-r%d' % (new_id, revision),
                                                       'append', origin_version, retire['reason']))
        raise Denied('NOT_CURRENT', claim_id=current,
                     hint='Another session updated this claim first; its version is current. Read it (dom_claim_versions) and '
                          'update that version if your change is still needed.') from None
    if receipt2.get('status') != 'DEMO_ADMITTED':
        return {'status': 'UPDATE_INCOMPLETE', 'claim_id': new_id}
    info = _info_of(svc, new_id)
    steps = [
        {'tool': 'dom_find', 'arguments': {'branch': branch, 'query': text[:30]}, 'why': 'the new version is current; the old one is listed with is_current=false'},
        {'tool': 'dom_trace', 'arguments': {}, 'why': 'readiness counts only current versions'},
    ]
    return {'status': 'CLAIM_UPDATED', 'simulated': True, 'new_claim_id': new_id, 'supersedes': claim_id,
            'record_id': info['record_id'], 'logical_claim_id': info['logical_claim_id'], 'version': info['version'], 'branch': branch,
            'revision': receipt2['revision'], 'review_level': info['review_level'],
            'raw_sha256': info['raw_sha256'], 'next_steps': steps}


def _origin_version_of(svc, claim_id):
    with svc.store.read() as c:
        row = c.execute('SELECT origin_version FROM claims WHERE id=?', (claim_id,)).fetchone()
    return row[0]


# ---------------------------------------------------------------------- find --
def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _strings(value)


def _preview(body, query):
    """At most 200 characters; windowed around the first hit when the hit is deep in a long body."""
    if len(body) <= 200:
        return body
    start = 0
    if query:
        low = body.lower()
        pos = low.find(query.lower()) if len(low) == len(body) else -1
        if pos > 150:
            start = max(0, pos - 60)
    head = '\u2026' if start > 0 else ''
    room = 200 - len(head)
    piece = body[start:start + room]
    if start + room < len(body):
        piece = piece[:-1] + '\u2026'
    return head + piece


def find(svc, query, branch, typ, requirement_ref, limit, plan_task=None, keyword=None, current_only=False):
    Denied = _denied()
    svc.scope()
    svc.ready()
    if query is not None and type(query) is not str:
        raise Denied('INVALID_FIELD', field='query')
    if typ is not None and (type(typ) is not str or typ not in TYPES):
        raise Denied('UNKNOWN_TYPE', field='type')
    if requirement_ref is not None and type(requirement_ref) is not str:
        raise Denied('INVALID_FIELD', field='requirement_ref')
    if type(limit) is not int or not 1 <= limit <= 200:
        raise Denied('INVALID_FIELD', field='limit', hint="'limit' must be an integer 1..200 (default 20).")
    if branch is not None:
        if type(branch) is not str or not branch:
            raise Denied('INVALID_FIELD', field='branch')
        svc.scope(branch)
    if keyword is not None and (type(keyword) is not str or not keyword.strip()):
        raise Denied('INVALID_FIELD', field='keyword')
    if type(current_only) is not bool:
        raise Denied('INVALID_FIELD', field='current_only')
    branches = [branch] if branch else sorted(svc.context.branches)
    if plan_task is not None:
        from . import planning
        with svc.store.read() as c:
            task = planning.resolve_task(c, plan_task)
            if task is None:
                raise Denied('TASK_NOT_FOUND', field='plan_task')
            subtree = set(planning.branches_of(c, planning.subtree_ids(c, task['plan_task_id'])))
        branches = [b for b in branches if b in subtree]
    needle = query.casefold() if query else ''
    matches, total = [], 0
    with svc.store.read() as c:
        stamp = svc.integrity_gate(c)
        archived = _archived(c)
        children = _children(c)
        info = _lineage(children)
        parent = {new: old for old, new in children.items()}
        ph = ','.join('?' * len(branches)) or "''"
        sql = ('SELECT cl.id, cl.content_type, cl.raw, cl.origin_branch, cl.origin_version, cl.payload, t.revision, '
               '%s AS lvl, rv.payload AS rvp FROM claims cl JOIN transactions t ON t.id=cl.tx '
               'LEFT JOIN claim_snapshots s ON s.claim_id=cl.id LEFT JOIN reviews rv ON rv.id=s.review_id '
               'WHERE t.branch IN (%s) ORDER BY cl.rowid DESC' % (_level_expr(c), ph))
        for row in c.execute(sql, branches):
            if typ and row['content_type'] != typ:
                continue
            item = json.loads(row['payload'])
            if requirement_ref and requirement_ref not in item.get('requirement_refs', []):
                continue
            if keyword and keyword.casefold() not in [k.casefold() for k in (item.get('anatomy') or {}).get('keywords', [])]:
                continue
            if current_only and row['id'] in archived:
                continue
            body = bytes(row['raw']).decode('utf-8', 'replace')
            if needle and needle not in body.casefold() and not any(needle in s.casefold() for s in _strings([item.get('metadata', {}), item.get('anatomy', {})])):
                continue
            total += 1
            if len(matches) < limit:
                record_id, version = info(row['id'])
                matches.append({'claim_id': row['id'], 'branch': row['origin_branch'], 'type': row['content_type'],
                                'preview': _preview(body, query), 'origin_version': row['origin_version'],
                                'review_level': _level(row['lvl'], row['rvp']), 'is_current': row['id'] not in archived,
                                'record_id': record_id, 'logical_claim_id': record_id,
                                'version': version, 'supersedes': parent.get(row['id']), 'revision': row['revision']})
    from . import public_dto
    return {'status': 'FOUND', 'simulated': True, 'matches': matches, 'total': total, 'truncated': total > len(matches),
            'read_snapshot': public_dto.read_snapshot(stamp, branches)}


# --------------------------------------------------------------------- start --
def start(svc, request, goal, branches, adopt):
    from .admission import IntegrityFailure, verify_chain
    Denied = _denied()
    _lite_only(svc)
    if not svc.context.simulation:
        raise Denied('OMH_BINDING_UNVERIFIED')
    if request is None:
        raise Denied('REQUEST_REQUIRED', field='request')
    req_raw = _text_bytes(request, 'request')
    goal_raw = None
    if goal is not None:
        if type(goal) is not str or not goal.strip():
            raise Denied('INVALID_FIELD', field='goal', hint="'goal' is optional; when given it must be a non-empty string (your one-line formulation).")
        goal_raw = _text_bytes(goal, 'goal')
    if branches is None:
        branches = ['main']
    if (type(branches) is not list or not branches or len(branches) > MAX_BRANCHES
            or any(type(b) is not str or not BRANCH_RE.match(b) for b in branches)):
        raise Denied('INVALID_FIELD', field='branches',
                     hint="'branches' must be an array of 1..%d codes of [A-Za-z0-9_-]{1,32}, e.g. [\"main\", \"alt\"]." % MAX_BRANCHES)
    branches = list(dict.fromkeys(branches))
    if type(adopt) is not bool:
        raise Denied('INVALID_FIELD', field='adopt')
    main = branches[0]

    state, existing, root, foreign = 'missing', set(), None, None
    if svc.store.path.exists():
        with svc.store.read() as c:
            state = svc.binding_state(c)
            if state in ('own', 'foreign'):
                try:
                    root = c.execute('SELECT * FROM project_root WHERE id=1').fetchone()
                except sqlite3.OperationalError:
                    root = None
            if state == 'own':
                existing = {r[0] for r in c.execute('SELECT branch FROM branch_heads')}
            if state == 'foreign':
                foreign = c.execute('SELECT work_ref FROM binding ORDER BY id LIMIT 1').fetchone()[0]
    adopt_info = None
    if state == 'foreign':
        if not adopt:
            raise Denied('SCOPE_DENIED')
        with svc.store.read() as c:
            chain = verify_chain(c)
        if chain['status'] != 'CHAIN_OK':
            raise IntegrityFailure(dict(status='CHAIN_BROKEN', blockers=['EVENT_CHAIN_BROKEN']))
        adopt_info = {'source_work': foreign, 'chain_hash': chain['head_hash'] if chain['events'] else '0' * 64, 'at': _now()}
    if root is not None:
        if root['request_sha256'] != raw_hash(req_raw):
            raise Denied('ROOT_CONFLICT', field='request')
        if goal_raw is not None and root['goal_sha256'] != raw_hash(goal_raw):
            raise Denied('ROOT_CONFLICT', field='goal')
    try:
        svc.store.initialize(svc.context.work, svc.policy, branches=tuple(branches), declare=tuple(branches), adopt=adopt_info)
    except ValueError:
        raise Denied('SCOPE_DENIED') from None
    from .admission import record_review_mode
    with svc.store.transaction() as c:
        record_review_mode(c, svc.launch_review_mode)
    svc.lite_sync()
    created = root is None
    if created:
        svc._system += 1  # the verbatim request and the goal are the project's own records
    try:
        return _start_records(svc, request, req_raw, goal, goal_raw, main, root, created, state, branches, existing)
    finally:
        if created:
            svc._system -= 1


def _start_records(svc, request, req_raw, goal, goal_raw, main, root, created, state, branches, existing):
    if created:
        req_item = build_item('quote', request, req_raw, main, {'source_ref': ROOT_REQUEST_REF}, [], 'root request (verbatim, immutable)', 'root-request')
        req = _write_claim(svc, main, req_item, DEFAULT_ORIGIN_VERSION, 'root request (verbatim, immutable)', 'root-request')
        if 'claim_id' not in req:
            return req
        goal_id = None
        if goal_raw is not None:
            goal_item = build_item('text', goal, goal_raw, main, {'attribution': 'agent'}, [], 'agent goal (goal_confirmed=false until the customer confirms)', 'root-goal')
            g = _write_claim(svc, main, goal_item, DEFAULT_ORIGIN_VERSION, 'agent goal (goal_confirmed=false)', 'root-goal')
            if 'claim_id' not in g:
                return g
            goal_id = g['claim_id']
        with svc.store.transaction() as c:
            if c.execute('SELECT 1 FROM project_root WHERE id=1').fetchone() is None:
                c.execute('INSERT INTO project_root(id,request_claim_id,goal_claim_id,goal_confirmed,request_sha256,goal_sha256,branch,created_at) VALUES(1,?,?,0,?,?,?,?)',
                          (req['claim_id'], goal_id, raw_hash(req_raw), raw_hash(goal_raw) if goal_raw is not None else None, main, _now()))
        root_info = {'request_claim_id': req['claim_id'], 'goal_claim_id': goal_id, 'goal_confirmed': False}
    else:
        root_info = {'request_claim_id': root['request_claim_id'], 'goal_claim_id': root['goal_claim_id'],
                     'goal_confirmed': bool(root['goal_confirmed'])}
    with svc.store.read() as c:
        all_branches = sorted(r[0] for r in c.execute('SELECT branch FROM branch_heads'))
    added = [b for b in branches if b not in existing] if state == 'own' else []
    use = main if main in all_branches else all_branches[0]
    steps = [
        {'tool': 'dom_claim', 'arguments': {'branch': use, 'type': 'text', 'text': '<one statement or finding, verbatim>'},
         'why': 'record the first claim; dom_describe shows an example for every type'},
        {'tool': 'dom_find', 'arguments': {'branch': use}, 'why': 'list the latest claims of the branch (the root claims are there)'},
    ]
    return {'status': 'STARTED' if created else 'ALREADY_STARTED', 'simulated': True, 'profile': 'lite',
            'work_ref': svc.context.work, 'branches': all_branches, 'main_branch': use,
            'adopted': state == 'foreign' or _was_adopted(svc), 'branches_added': added, 'root': root_info, 'next_steps': steps}


def _was_adopted(svc):
    with svc.store.read() as c:
        try:
            return c.execute('SELECT 1 FROM adoptions WHERE new_work_ref=?', (svc.context.work,)).fetchone() is not None
        except sqlite3.OperationalError:
            return False


# ------------------------------------------------------------ tool documents --
def _arg(type_name, required, text):
    return {'type': type_name, 'required': required, 'description': text}


_TYPE_DOC = ', '.join(TYPES)

TOOL_DOCS = {
    'dom_describe': {
        'summary': 'Describe this server and how to use it: profile (lite/strict), the simulated flag, every tool with its arguments, the 7 claim types with a valid example call each, the write->find->update cycle, what review_level and the two readiness numbers mean. Read-only; takes no arguments. Call it first.',
        'arguments': {},
        'returns': 'status DESCRIBED, state (NOT_STARTED|READY|FOREIGN_BASE), tools, claim_types, cycle, review_level, readiness, limits, next_steps.',
        'example': {},
    },
    'dom_start': {
        'summary': 'Open (or create) the project database in this server directory. Computes work/task/policy itself, creates the branches, and records the customer request verbatim as the immutable root claim (plus your optional one-line goal, marked goal_confirmed=false until the customer confirms). Idempotent: repeating it with the same arguments changes nothing; a different request is refused (ROOT_CONFLICT) and the root is not overwritten.',
        'arguments': {
            'request': _arg('string', True, 'The customer request, verbatim. REQUIRED: there is no empty baseline.'),
            'goal': _arg('string', False, 'Your own one-line formulation of the goal. Stored as a separate claim, goal_confirmed=false.'),
            'branches': _arg('array of strings', False, 'Branch codes to declare, each [A-Za-z0-9_-]{1,32}. Default ["main"]. Repeat the call with more codes to add branches.'),
            'adopt': _arg('boolean', False, 'true only if another call was refused with SCOPE_DENIED because the database in this directory is bound to another work and you are sure it is your own project. Records an append-only adoption (source work, chain hash).'),
        },
        'returns': 'status STARTED|ALREADY_STARTED, work_ref, branches, main_branch, root{request_claim_id, goal_claim_id, goal_confirmed}, next_steps (the exact next call).',
        'example': {'request': 'Compare method X and Y for the report', 'goal': 'Collect evidence on X vs Y', 'branches': ['main', 'alt']},
    },
    'dom_claim': {
        'summary': 'Record one claim in a single call. The server builds the envelope (base64 body, hashes, metadata with sensible defaults, idempotency key), submits it, reviews it as the local owner (review_level=self) and admits it through the normal transaction. The text is stored byte-for-byte and never edited; to change it later use dom_update. Types: ' + _TYPE_DOC + '.',
        'arguments': {
            'branch': _arg('string', False, 'A declared branch code (dom_describe lists them). REQUIRED unless plan_task is given (then the branch of the plan item is used).'),
            'type': _arg('string', True, 'One of: ' + _TYPE_DOC + '.'),
            'plan_task': _arg('string', False, 'Plan item this claim belongs to: id, path (П1.2) or branch code (AA100). The claim goes into the branch of that item and is linked to it.'),
            'link_role': _arg('string', False, 'Link role with plan_task: justifies, implements, validates, documents (default), depends_on.'),
            'basis': _arg('array of strings', False, 'Anatomy - from where: claim ids / source refs this claim stands on.'),
            'refines': _arg('string', False, 'Anatomy - the claim this one refines (default with plan_task: the item header claim).'),
            'purpose': _arg('string', False, 'Anatomy - what this claim is for.'),
            'usage': _arg('array of strings', False, 'Anatomy - where it is used / planned to be used.'),
            'keywords': _arg('array of strings', False, 'Anatomy - keywords for search and graphs.'),
            'text': _arg('string', True, 'The claim body, non-empty, at most 1 MiB UTF-8. Stored exactly as given.'),
            'requirement_refs': _arg('array of strings', False, 'Requirement ids this claim supports, e.g. ["R1"]. Used for coverage in dom_trace.'),
            'reason': _arg('string', False, 'Why this claim is recorded (default "lite dom_claim").'),
            'origin_version': _arg('string', False, 'Version marker of the source/artifact (default "lite-v1").'),
            'idempotency_key': _arg('string', False, 'Optional idempotency key (TD-DOM-22.5, TD-DOM-28): a retry of this exact claim with the same content returns the same claim_id (ALREADY_RECORDED); the same key with different content is refused (IDEMPOTENCY_MISMATCH).'),
            'source_ref': _arg('string', False, 'quote: where the quote comes from (url, file, person).'),
            'locator': _arg('string', False, 'quote/code: position in the source.'),
            'source_snapshot_digest': _arg('string', False, 'quote: sha256 of the source snapshot (default: hash of the quote).'),
            'attribution': _arg('string', False, 'decision/concept/text: who decided or said it (default "agent").'),
            'rationale': _arg('string', False, 'decision: why.'),
            'assumptions': _arg('array', False, 'decision/formula/concept/text: assumptions.'),
            'alternatives': _arg('array', False, 'decision: alternatives considered.'),
            'expression': _arg('string', False, 'formula: the expression (default: text).'),
            'notation': _arg('object', False, 'formula: symbol -> meaning.'),
            'units': _arg('object', False, 'formula/data: symbol -> unit.'),
            'derivation_refs': _arg('array', False, 'formula: where it was derived.'),
            'repository_ref': _arg('string', False, 'code: repository id.'),
            'relative_path': _arg('string', False, 'code: relative path (no leading slash, no "..").'),
            'language': _arg('string', False, 'code: language.'),
            'snapshot_digest': _arg('string', False, 'code: sha256 of the file snapshot.'),
            'commit_ref': _arg('object', False, 'code: optional {hash: 40|64 hex, branch: non-empty, author?, message?, committed_at?}; omit when the code is not tied to a commit.'),
            'artifact_ref': _arg('string', False, 'data: dataset/file id.'),
            'artifact_digest': _arg('string', False, 'data: sha256 of the artifact.'),
            'method': _arg('string', False, 'data: how the data was obtained.'),
            'columns': _arg('object', False, 'data: column -> type.'),
            'provenance': _arg('string', False, 'data: origin of the data.'),
            'definition': _arg('string', False, 'concept: definition (default: text).'),
            'scope': _arg('string', False, 'concept: scope of the definition.'),
            'links': _arg('array', False, 'concept: related references.'),
            'classification': _arg('string', False, 'text: "factual" (needs evidence) or "interpretation" (default when no evidence).'),
            'evidence': _arg('array', False, 'text: [{"ref": "...", "locator": "..."}]; makes the text factual.'),
        },
        'returns': 'status CLAIM_ADMITTED|ALREADY_RECORDED, claim_id, record_id, logical_claim_id, version, revision, review_level ("self"), raw_sha256, next_steps.',
        'example': {'branch': 'main', 'type': 'decision', 'text': 'Use method X for the main run.', 'rationale': 'cheaper and already validated', 'requirement_refs': ['R1']},
    },
    'dom_update': {
        'summary': 'Replace a claim by a new version. The old version is never edited or deleted: it stays in history with is_current=false and the new one supersedes it (replacement event). Only the current version can be updated.',
        'arguments': {
            'claim_id': _arg('string', True, 'The claim to replace (claim_id from dom_claim / dom_find).'),
            'text': _arg('string', True, 'The complete new text (not a diff).'),
            'reason': _arg('string', True, 'Why it changes; recorded in the event chain.'),
            'idempotency_key': _arg('string', False, 'Optional idempotency key (TD-DOM-22.5): replaying the same update returns the same new version instead of appending again.'),
            'anatomy': _arg('object', False, 'Optional new anatomy values {basis?, refines?, purpose?, usage?, keywords?}; merged over the old ones in the new version (plan_ref is fixed). Text may stay the same when anatomy changes.'),
            'expected_current': _arg('string', False, 'Read-before-write: the claim_id of the version you read. If the claim moved on (another agent updated it), the answer is CONFLICT_READ_REQUIRED with the current version and nothing is written.'),
            'mode': _arg('string', False, 'auto (default) | update | fork. fork (needs expected_current): the current version is another agent\'s and does not fit -> your text becomes a parallel variant (both current, variant_of + an open conflict; dom_reconcile merges them when they converge). Not allowed over your own version (FORK_NOT_ALLOWED).'),
        },
        'returns': 'status CLAIM_UPDATED, new_claim_id, supersedes, record_id, logical_claim_id, version, revision, review_level, next_steps | CLAIM_FORKED {claim_id, forked_from, forked_from_author, branch, conflict_ref} | CONFLICT_READ_REQUIRED {current_claim_id, author, version, revision}.',
        'example': {'claim_id': '<claim_id from dom_claim>', 'text': 'Use method Y for the main run.', 'reason': 'X failed validation on 2026-11-09'},
    },
    'dom_find': {
        'summary': 'Read-only search over claim bodies (case-insensitive substring of the UTF-8 text) and metadata values. Without a query it returns the latest claims, newest first. Includes older versions with is_current=false.',
        'arguments': {
            'query': _arg('string', False, 'Substring to look for (case-insensitive).'),
            'branch': _arg('string', False, 'Only this branch.'),
            'type': _arg('string', False, 'Only this claim type.'),
            'requirement_ref': _arg('string', False, 'Only claims supporting this requirement id.'),
            'limit': _arg('integer', False, 'Max matches, 1..200 (default 20).'),
            'plan_task': _arg('string', False, 'Only claims in the branches of this plan item and its sub-items (id, path or branch code).'),
            'keyword': _arg('string', False, 'Only claims whose anatomy keywords contain this keyword (case-insensitive).'),
            'current_only': _arg('boolean', False, 'Only current versions (default false: older versions are listed with is_current=false).'),
        },
        'returns': 'status FOUND, matches[{claim_id, branch, type, preview<=200 chars, origin_version, review_level, is_current, record_id, logical_claim_id, version, supersedes, revision}], total, truncated.',
        'example': {'query': 'method X', 'branch': 'main', 'limit': 10},
    },
    'dom_resume': {
        'summary': 'Everything about one branch in one read-only call: claims (snapshot), trace/readiness, outbox status and the pending list. Use it to re-orient after a pause or a REVISION_CONFLICT.',
        'arguments': {
            'branch': _arg('string', True, 'A declared branch code.'),
            'limit': _arg('integer', False, 'Pending page size 1..500 (default 50).'),
            'after': _arg('string', False, 'Keyset cursor "branch:id" from a previous page.'),
        },
        'returns': 'status RESUMED, snapshot, trace, sync, pending, readiness{admitted_total, admitted_independent, pending, rejected, stale}.',
        'example': {'branch': 'main'},
    },
    'dom_query': {
        'summary': 'Read-only snapshot of one branch at its head or at an exact earlier revision: all claims (with archived flag), event history, conflicts.',
        'arguments': {
            'branch': _arg('string', True, 'A declared branch code.'),
            'revision': _arg('integer', False, 'Historical revision >= 0 (default: head).'),
        },
        'returns': 'status SNAPSHOT, branch, revision, claims, history.',
        'example': {'branch': 'main'},
    },
    'dom_trace': {
        'summary': 'Coverage / stale / conflict / pending trace. In the lite profile readiness is {admitted_total, admitted_independent, pending, rejected, stale}: admitted_total counts every current admitted claim, admitted_independent only those an independent reviewer admitted (lite claims are review_level=self, so it is 0). Never closes the owner task.',
        'arguments': {'branch': _arg('string', False, 'One branch; omit for all branches.')},
        'returns': 'status, readiness{...}, ready_for_owner_review, residual (per requirement_ref), blockers.',
        'example': {},
    },
    'dom_export': {
        'summary': 'Write a deterministic YAML export of one branch (head or exact revision) to a server-fixed path inside the run directory.',
        'arguments': {
            'branch': _arg('string', True, 'A declared branch code.'),
            'revision': _arg('integer', False, 'Historical revision >= 0 (default: head).'),
        },
        'returns': 'status EXPORTED, handle, sha256, revision.',
        'example': {'branch': 'main'},
    },
    'dom_sync_status': {
        'summary': 'Read-only outbox status per intent (pending / ambiguous / ack) for one branch.',
        'arguments': {'branch': _arg('string', True, 'A declared branch code.')},
        'returns': 'status SYNC_STATUS, intents[].',
        'example': {'branch': 'main'},
    },
    'dom_sync': {
        'summary': 'Deliver the outbox of one branch (YAML export + simulated OMH attach with read-back).',
        'arguments': {'branch': _arg('string', True, 'A declared branch code.')},
        'returns': 'status SYNCED|SYNC_PENDING.',
        'example': {'branch': 'main'},
    },
}

TOOL_DOCS.update({
    'dom_plan_ingest': {
        'summary': 'Send the project plan to the memory. Each plan item becomes a header claim in its own branch: П1=AA, П2=AB, П1.1=AA100, П1.2=AA200, П1.1.1=AA110, П1.1.1.1=AA111 (codes are allocated once per item and never move). Sub-items become sub-branches; the conversation context of an item is stored as a property of its header claim. Idempotent: the same plan changes nothing; a changed item gets a new header version (the old one stays in history); an item removed from a full plan is archived in the projection, its claims stay. Explicit numbers are kept ("3." is П3) when they increase. A plan written the plan-mode way (## Context / ## Steps with 1., 2. / ## Verification) takes its items from the numbered steps; the other sections become the preamble. mode=partial only adds or changes the items given (nothing is archived or renamed). Limits: 676 top-level items, 35 sub-items per item, 2000 items in all. JSON item keys: title (or content/name/text), id, status, context, purpose, children.',
        'arguments': {
            'plan': _arg('string', True, 'The plan: markdown numbered items (1., 1.1, П1.2), bullets or headings; or JSON [{"title", "id"?, "status"?, "context"?, "purpose"?, "children"?}] or a TodoWrite list {"todos": [...]}.'),
            'source': _arg('string', False, '"agent" (default: a plan from the agent/conversation) or "OMH" (an OMH plan export).'),
            'format': _arg('string', False, 'auto (default), markdown or json.'),
            'contexts': _arg('object', False, 'Plan path or item id -> context text from the conversation (why the item exists, what it is for). Stored as the "context" property of the header claim.'),
            'source_revision': _arg('string', False, 'Revision label of the plan source (default: r1, r2, ... on every change).'),
            'mode': _arg('string', False, 'full (default: items missing from the plan are archived) or partial (only the given items are touched).'),
            'base_revision': _arg('string', False, 'The plan revision your plan is based on (source_revision from dom_plan_status/dom_plan_ingest). If another agent changed the plan since, the call is refused with PLAN_CONFLICT and nothing is written: read the current plan (dom_plan_tree) and send again.'),
            'force': _arg('boolean', False, 'true: write over a newer revision anyway (with base_revision); recorded as a forced conflict.'),
        },
        'returns': 'status PLAN_INGESTED|PLAN_UNCHANGED|PLAN_CONFLICT, source_revision, ingested_by, created, updated, unchanged, archived[], tasks[{path, plan_task_id, title, status, branch, header_claim_id, action, previous?{header_claim_id, author, source_revision}}], warnings?, next_steps. PLAN_CONFLICT: base_revision, current_revision, changed_by, changed_at, item_paths[].',
        'example': {'plan': '1. Collect requirements\n1.1 Interview the customer\n2. Build the prototype', 'contexts': {'П1': 'the customer asked for a written spec first'}},
    },
    'dom_plan_tree': {
        'summary': 'The plan as a tree from one item: path, title, branch code, header claim, status and the number of linked claims per item. Read-only.',
        'arguments': {
            'root': _arg('string', True, 'Plan item id, path (П1, П2.1) or branch code (AA, AB100).'),
            'depth': _arg('integer', False, 'Levels to show, 1..10 (default 3).'),
            'at_version': _arg('integer', False, 'Historical view: per item the newest projection version not above this number.'),
            'require_fresh': _arg('boolean', False, 'Refuse with PLAN_PROJECTION_STALE instead of answering from a stale projection.'),
        },
        'returns': 'status PLAN_TREE, root, depth, source, synced_at, stale, tasks[{path, title, plan_task_id, branch, claim_id, status, claim_count, children}].',
        'example': {'root': 'П1', 'depth': 3},
    },
    'dom_task_claims': {
        'summary': 'Claims linked to one plan item (optionally its whole subtree), grouped by role (defines = the header claim, justifies, implements, validates, documents, depends_on). Current versions by default; link_stale marks links made before the item changed its meaning; orphaned_links are links of removed items.',
        'arguments': {
            'task': _arg('string', True, 'Plan item id, path (П1.2) or branch code (AA200).'),
            'include_children': _arg('boolean', False, 'Include all sub-items (default false).'),
            'link_role': _arg('string', False, 'all (default) or one role.'),
            'include_history': _arg('boolean', False, 'List replaced versions too (default false).'),
        },
        'returns': 'status TASK_CLAIMS, task_path, branch, claims_by_role{role: [{claim_id, version, is_current, type, branch, plan_path, preview, link_stale}]}, orphaned_links, summary.',
        'example': {'task': 'П1', 'include_children': True},
    },
    'dom_plan_status': {
        'summary': 'Is the plan projection fresh, is the project closed, and is a plan check due? When no plan is known it returns the plan request to send to the agent; every check_every looks without a change it returns a plan-check request. Read-only.',
        'arguments': {'check_every': _arg('integer', False, 'Looks without a change after which a plan check is due (default 10).')},
        'returns': 'status PLAN_STATUS, has_plan, project_closed, sources[], items, open_items, iterations_since_change, plan_check_due, request{kind, text}|null, inbox_processed, inbox_queued, inbox_failed, next_steps.',
        'example': {},
    },
    'dom_task_graph': {
        'summary': 'Who contributed what to a plan item: chains of claim versions (author, root, review level, revision, current flag) with edges supersedes / basis / refines / variant_of / task_link, and a per-agent count. Branches stay plan items; agents are told apart by authorship. Read-only.',
        'arguments': {
            'task': _arg('string', True, 'Plan item id, path (П1.2) or branch code.'),
            'agent': _arg('string', False, 'Only chains with a version written by this principal.'),
            'include_history': _arg('boolean', False, 'All versions of each chain (default true); false = current versions only.'),
            'include_children': _arg('boolean', False, 'Include sub-items (default true).'),
            'limit': _arg('integer', False, 'Max chains, 1..1000 (default 200).'),
        },
        'returns': 'status TASK_GRAPH, task{path, plan_task_id, branch, title}, agents[{author, root, claim_count}], chains[{logical_claim_id, plan_path, versions[], edges[]}], truncated.',
        'example': {'task': 'П1', 'agent': 'subagent-1'},
    },
    'dom_context': {
        'summary': 'Where does claim A come from? The claim with its anatomy and properties, the chain of claims it refines up to the plan item header, its basis (resolved claim ids and source refs), the claims that refine it, and its plan item with ancestors. Read-only.',
        'arguments': {
            'claim_id': _arg('string', True, 'Any version of the claim.'),
            'depth': _arg('integer', False, 'Max length of the refines chain, 1..32 (default 8).'),
        },
        'returns': 'status CONTEXT, claim, refines_chain[], basis[], refinements[], plan{path, title, branch, header_claim_id, ancestors[]}, root_request_claim_id.',
        'example': {'claim_id': '<claim_id>'},
    },
    'dom_extract': {
        'summary': 'LLM module: split material (conversation excerpt, notes, a document) into claim candidates for one plan item. Candidates are only submitted - nothing is admitted until you or the local agent read (dom_candidate), review (dom_review) and admit (dom_admit) them. Wording is copied from the material; a candidate whose text is not in the material is dropped. Provider from configuration (deterministic rules, Claude via the anthropic SDK, or an external command for Hermes/OpenCode); the deterministic rules are the fallback.',
        'arguments': {
            'task': _arg('string', True, 'Plan item id, path (П1.2) or branch code.'),
            'text': _arg('string', True, 'The material to split, at most 200 kB.'),
            'provider': _arg('string', False, 'Override the configured provider: deterministic, anthropic, command, or client (you are the model: the answer is EXTRACTION_REQUEST with the rules and the schema, and you reply with dom_extract_submit).'),
            'link_role': _arg('string', False, 'Link role of the claims: documents (default), justifies, implements, validates, depends_on.'),
        },
        'returns': 'status EXTRACTED, provider, model, prompt_digest, checkpoint_ref, candidates[{candidate_id, intent_digest, type, preview, branch}], already_recorded[], refused[], dropped, warning, next_steps.',
        'example': {'task': 'П1.1', 'text': 'We decided to store claims in SQLite.'},
    },
    'dom_extract_submit': {
        'summary': 'Second half of dom_extract with provider=client: submit the candidates you split out of the material yourself. The server applies the same checks as for any provider: each text must be a verbatim part of the material (otherwise it is dropped), a typed candidate without its required facts becomes text marked suggested:<type>. Candidates are submitted only; review them with dom_candidate / dom_accept.',
        'arguments': {
            'task': _arg('string', True, 'Plan item id, path (П1.2) or branch code.'),
            'text': _arg('string', True, 'The same material you split.'),
            'candidates': _arg('array', True, 'Array of {type, text, fields?: {source_ref, rationale, repository_ref, relative_path, artifact_ref, ...}, units?: "x=unit;y=unit", notation?: "x=meaning", purpose?, keywords?: []}.'),
            'model': _arg('string', False, 'Your model id, recorded in the provenance (default "calling-agent").'),
            'link_role': _arg('string', False, 'documents (default), justifies, implements, validates, depends_on.'),
        },
        'returns': 'status EXTRACTED, provider "client", model, candidates[{candidate_id, intent_digest, type, preview, branch}], already_recorded[], refused[], dropped, next_steps.',
        'example': {'task': 'П1.1', 'text': 'We decided to store claims in SQLite.', 'candidates': [{'type': 'text', 'text': 'We decided to store claims in SQLite.', 'keywords': ['storage']}]},
    },
    'dom_annotate': {
        'summary': 'Add one event to a claim without changing it: a comment, a link, a property (name/value), an archive mark, a conflict or its resolution. Events are append-only rows of the claim history; the latest property value wins. Use archive (with a reason) to retire a claim - nothing is deleted.',
        'arguments': {
            'claim_id': _arg('string', True, 'The claim.'),
            'kind': _arg('string', True, 'comment | link | property | archive | conflict | resolve.'),
            'text': _arg('string', False, 'comment: the comment text.'),
            'url': _arg('string', False, 'link: the target (url, file, claim id).'),
            'locator': _arg('string', False, 'link: position in the target.'),
            'name': _arg('string', False, 'property: name (body/origin fields cannot be set).'),
            'value': _arg('string', False, 'property: value.'),
            'conflict_ref': _arg('string', False, 'conflict/resolve: an id naming the conflict.'),
            'reason': _arg('string', False, 'Why; required for archive, conflict and resolve.'),
        },
        'returns': 'status ANNOTATED (claim_id, kind, branch, revision, event_id) | RECORDED_CANDIDATE under a review policy.',
        'example': {'claim_id': '<claim_id>', 'kind': 'comment', 'text': 'confirmed by the customer on the call'},
    },
    'dom_accept': {
        'summary': 'Decide on a candidate you have read (dom_candidate) in one call: approve = review + admit (re-submitted on the current branch head if another candidate was admitted first), reject = a rejecting review with your note. For the project owner and the local agent (lite profile).',
        'arguments': {
            'candidate_id': _arg('string', True, 'Candidate id (dom_extract, dom_resume pending, manual-mode dom_claim).'),
            'decision': _arg('string', False, 'approve (default) or reject.'),
            'note': _arg('string', False, 'Why; required for reject.'),
        },
        'returns': 'status DEMO_ADMITTED (tx_id, revision, claim_ids, rebased?) | REVIEW_REJECTED.',
        'example': {'candidate_id': '<candidate_id>', 'decision': 'approve'},
    },
    'dom_claim_versions': {
        'summary': 'Version history of one claim: every version of its replacement chain, oldest first, with the current flag. Read-only.',
        'arguments': {'claim_id': _arg('string', True, 'Any version of the claim.')},
        'returns': 'status CLAIM_VERSIONS, logical_claim_id, versions[{claim_id, version, supersedes, raw_sha256, revision, is_current}].',
        'example': {'claim_id': '<claim_id>'},
    },
    'dom_recall': {
        'summary': 'The state of one branch as it was after its N-th event (every claim, update, property or archive is one event): active and archived claims at that point. Use it to see the baseline before a change. Read-only.',
        'arguments': {
            'branch': _arg('string', True, 'A branch code (plan item branch, e.g. AA100).'),
            'at_seq': _arg('integer', True, 'Branch-local event number, 0..head (0 = empty).'),
        },
        'returns': 'status RECALL, revision, active_claims[], archived_claims[], resolved_conflicts, related_branches, baseline_range.',
        'example': {'branch': 'AA100', 'at_seq': 1},
    },
    'dom_diff': {
        'summary': 'Compare the current claims of two branches by logical claim: only in A, only in B, changed (same claim, other body) and identical. Read-only.',
        'arguments': {
            'branch_a': _arg('string', True, 'First branch code.'),
            'branch_b': _arg('string', True, 'Second branch code.'),
        },
        'returns': 'status DIFF, comparable, common_ancestor, only_a[], only_b[], changed[], identical[].',
        'example': {'branch_a': 'AA100', 'branch_b': 'AA100-V1'},
    },
    'dom_reconcile': {
        'summary': 'Converge variant branches: a variant claim (same body, other properties, forked into <code>-V<n>) whose current version again equals the base claim in every property is archived as merged_into the base; a variant branch with nothing left gets a converge event. Runs after every plan ingest too.',
        'arguments': {},
        'returns': 'status RECONCILED, merged[{branch, claim_id, merged_into}].',
        'example': {},
    },
    'dom_candidate': {
        'summary': 'Read one pending candidate before reviewing it: decoded texts, metadata, anatomy, branch and review state.',
        'arguments': {'candidate_id': _arg('string', True, 'Candidate id from dom_resume pending or dom_extract.')},
        'returns': 'status CANDIDATE, state (AWAITING_REVIEW|REVIEWED|REJECTED|ADMITTED), items[{content_type, text, metadata, anatomy, ...}].',
        'example': {'candidate_id': '<candidate_id>'},
    },
    'dom_review': {
        'summary': 'Review a pending candidate: approve (default) or reject with a note. An approved candidate is admitted with dom_admit.',
        'arguments': {
            'candidate_id': _arg('string', True, 'Candidate id.'),
            'intent_digest': _arg('string', True, 'The digest shown by dom_candidate / the submit result.'),
            'decision': _arg('string', False, 'approve (default) or reject.'),
            'note': _arg('string', False, 'Why; required for reject.'),
        },
        'returns': 'status DEMO_REVIEWED|REVIEW_REJECTED, review_id, decision.',
        'example': {'candidate_id': '<candidate_id>', 'intent_digest': '<digest>', 'decision': 'approve'},
    },
    'dom_admit': {
        'summary': 'Admit an approved candidate (atomic transaction: claims, events, branch head, outbox).',
        'arguments': {
            'candidate_id': _arg('string', True, 'Candidate id.'),
            'review_id': _arg('string', True, 'Approving review id.'),
            'idempotency_key': _arg('string', True, 'Any stable key for this admission (retry-safe).'),
            'intent_digest': _arg('string', True, 'Candidate digest.'),
        },
        'returns': 'status DEMO_ADMITTED, tx_id, branch, revision, claim_ids.',
        'example': {'candidate_id': '<candidate_id>', 'review_id': '<review_id>', 'idempotency_key': 'admit-1', 'intent_digest': '<digest>'},
    },
    'dom_distillate': {
        'summary': 'Statement distillate chain (stage 3): read the source quote, the version chain, the computed states and the effective version (show), or improve the deterministic extractive v1 only through propose -> validate (V1-V8) -> accept. The source is ground truth and immutable; a user correction is a new source. Every gate is computed from the ledger; show reports next_steps.',
        'arguments': {
            'action': _arg('string', True, 'show | propose | validate | accept | withdraw | dispute | resolve.'),
            'source': _arg('string', False, 'The statement quote id (capture source).'),
            'distillate': _arg('string', False, 'Any version id of the logical distillate (alternative to source).'),
            'text': _arg('string', False, 'propose: the candidate text (1-3 sentences, grounded in the anchors).'),
            'anchors': _arg('array', False, 'propose: one inner array per candidate sentence, each a list of verbatim quotes from the source.'),
            'by': _arg('string', False, 'accept: agent (default) or user. Recorded, not authenticated.'),
            'note': _arg('string', False, 'accept/withdraw/dispute/resolve: the reason (required for withdraw, dispute, resolve).'),
        },
        'returns': 'status DISTILLATE_SHOWN | PROPOSED | ALREADY_RECORDED | VALIDATED | REVIEW_FAILED | ACCEPTED | WITHDRAWN | DISPUTED | RESOLVED; source_id, source_state, distillate_id, logical_claim_id, effective{claim_id,version,origin}, versions[{claim_id,version,state,origin,text,...}], open_conflicts, failed, review, next_steps. Refusals: STATEMENT_NOT_FOUND, SPAN_NOT_FOUND, SPAN_AMBIGUOUS, NO_IMPROVEMENT, PROPOSAL_CONFLICT, REVIEW_REQUIRED, REVIEW_STALE, NOT_CURRENT, DISTILLATE_DISPUTED, DISTILLATE_LOOP.',
        'example': {'action': 'show', 'source': '<source_id>'},
    },
})

TOOL_DOCS['dom_claim']['summary'] += (' Required per type (refused with MISSING_FIELD otherwise; empty strings count as missing): '
                                      + '; '.join('%s: %s' % (t, ', '.join(REQUIRED_FIELDS[t]) or 'nothing extra') for t in TYPES)
                                      + '. text with classification=factual also needs evidence.')

LITE_TOOL_NAMES = tuple(TOOL_DOCS)


def compose_description(name):
    doc = TOOL_DOCS[name]
    args = '; '.join('%s (%s, %s) - %s' % (a, d['type'], 'REQUIRED' if d['required'] else 'optional', d['description'])
                     for a, d in doc['arguments'].items()) or 'none'
    return '%s Arguments: %s. Returns: %s Example: %s(%s)' % (doc['summary'], args, doc['returns'], name, json.dumps(doc['example'], ensure_ascii=False))


_JSON_TYPES = {'string': 'string', 'integer': 'integer', 'boolean': 'boolean', 'array of strings': 'array', 'array': 'array', 'object': 'object'}


def input_schema(name):
    doc = TOOL_DOCS[name]
    props = {}
    for arg, d in doc['arguments'].items():
        prop = {'type': _JSON_TYPES[d['type']], 'description': d['description']}
        if d['type'] == 'array of strings':
            prop['items'] = {'type': 'string'}
        if arg == 'type' and name == 'dom_claim':
            prop['enum'] = list(TYPES)
        if name == 'dom_plan_tree' and arg == 'depth':
            prop.update(minimum=1, maximum=10)
        if name == 'dom_plan_tree' and arg == 'at_version':
            prop['minimum'] = 0
        if arg == 'limit':
            prop.update(minimum=1, maximum=500 if name == 'dom_resume' else 200)
        if arg == 'at_seq':
            prop['minimum'] = 0
        if arg == 'revision':
            prop['minimum'] = 0
        props[arg] = prop
    base_required = [a for a, d in doc['arguments'].items() if d['required']]
    # Audit 2026-11-12 #2: one flat object schema for every tool.  A top-level
    # oneOf/anyOf is refused by several providers (the Anthropic tool
    # input_schema among them) and the per-type branches overlapped.  Per-type
    # required fields are stated in the description and enforced by the server
    # (MISSING_FIELD), not by the schema.
    return {'type': 'object', 'additionalProperties': False, 'properties': props,
            'required': base_required}


_CLAIM_EXAMPLES = {
    'quote': {'branch': 'main', 'type': 'quote', 'text': 'The measured value was 4.2 mol/L.', 'source_ref': 'https://example.org/report.pdf', 'locator': 'p.3'},
    'decision': {'branch': 'main', 'type': 'decision', 'text': 'Use method X for the main run.', 'rationale': 'cheaper and already validated', 'attribution': 'team lead'},
    'formula': {'branch': 'main', 'type': 'formula', 'text': 'E = m * c**2', 'notation': {'E': 'energy', 'm': 'mass', 'c': 'speed of light'}, 'units': {'E': 'J', 'm': 'kg', 'c': 'm/s'}},
    'code': {'branch': 'main', 'type': 'code', 'text': 'def add(a, b):\n    return a + b', 'relative_path': 'src/add.py', 'language': 'python', 'repository_ref': 'local:project'},
    'data': {'branch': 'main', 'type': 'data', 'text': 'x,y\n1,2\n3,4', 'artifact_ref': 'file:results.csv', 'units': {'x': 'mol/L', 'y': 's'}, 'method': 'measured'},
    'concept': {'branch': 'main', 'type': 'concept', 'text': 'Baseline: the first accepted version of the plan.', 'definition': 'the first accepted version of the plan', 'scope': 'this project'},
    'text': {'branch': 'main', 'type': 'text', 'text': 'Method X is probably faster on small inputs.'},
}
_CLAIM_WHEN = {
    'quote': 'a verbatim passage from a source; the text is the quote itself',
    'decision': 'a choice that was made, with rationale',
    'formula': 'an equation or expression; give units for the symbols if you know them',
    'code': 'a code fragment or file content',
    'data': 'a table or measurement; give units if you know them',
    'concept': 'a definition of a term used in the project',
    'text': 'anything else; add evidence=[{"ref":..,"locator":..}] to make it a factual statement, otherwise it is an interpretation',
}


def claim_type_examples():
    out = {}
    for t in TYPES:
        allowed = list(TYPE_ARGS[t])
        out[t] = {
            'use_for': _CLAIM_WHEN[t],
            'required': ['branch', 'type', 'text'] + list(REQUIRED_FIELDS.get(t, ())),
            'optional_common': ['requirement_refs', 'reason', 'origin_version', 'idempotency_key'],
            'optional_type_specific': allowed,
            'example_call': {'tool': 'dom_claim', 'arguments': _CLAIM_EXAMPLES[t]},
        }
    return out


def describe(svc):
    state, work, branches, integrity = 'NOT_STARTED', None, [], None
    if svc.store.path.exists():
        from .admission import verify_chain
        with svc.store.read() as c:
            st = svc.binding_state(c)
            if st == 'own':
                state, work = 'READY', svc.context.work
                branches = sorted(r[0] for r in c.execute('SELECT branch FROM branch_heads'))
                # QA004 F-26: the first call of a session says when the ledger was edited outside the server.
                # T-07: re-verified only when the event log changed since the last describe of this process.
                key = tuple(c.execute('SELECT COUNT(*),COALESCE(MAX(seq),0),(SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1) FROM events').fetchone())
                cached = getattr(svc, '_describe_chain', None)
                if not cached or cached[0] != key:
                    cached = (key, verify_chain(c)['status'])
                    svc._describe_chain = cached
                integrity = cached[1]
                from .admission import stored_review_mode, REVIEW_STRICTNESS
                stored = stored_review_mode(c)
                if REVIEW_STRICTNESS.get(stored, 0) > REVIEW_STRICTNESS[svc.review_mode]:
                    svc.review_mode = stored
                if integrity != 'CHAIN_OK':
                    state = 'CHAIN_BROKEN'
            elif st == 'foreign':
                state = 'FOREIGN_BASE'
    if svc.lite:
        profile = 'lite'
        summary = ('Lite (local_single_owner) profile of a SIMULATED DOM server: the local SQLite database is real and append-only, '
                   'OMH/Git targets are simulated. One owner writes; every admitted claim is labelled review_level=self.')
        tools = [{'name': n, 'summary': TOOL_DOCS[n]['summary'], 'arguments': TOOL_DOCS[n]['arguments'],
                  'returns': TOOL_DOCS[n]['returns'], 'example': {'tool': n, 'arguments': TOOL_DOCS[n]['example']}} for n in TOOL_DOCS]
    else:
        profile = 'strict'
        summary = 'Strict profile.'
        tools = []
    cycle = [
        '1. dom_describe({}) - read this once.',
        '2. dom_start({"request": "<customer request verbatim>"}) - opens the project, records the immutable root request.',
        '3. dom_claim({"branch": "main", "type": "decision", "text": "..."}) - one call per claim; repeat for each fact, quote, decision.',
        '4. dom_find({"query": "..."}) - look a claim up before you write a similar one.',
        '5. dom_update({"claim_id": "...", "text": "<full new text>", "reason": "..."}) - changes a claim by a NEW version; the old one stays in history.',
        '6. dom_trace({}) - readiness; dom_resume({"branch": "main"}) - re-orient after a pause or after REVISION_CONFLICT.',
    ]
    next_steps = ([{'tool': 'dom_trace', 'arguments': {}, 'why': 'CHAIN_BROKEN: the database was edited outside the server. '
                    'Writes are refused. Stop and tell the user; dom_trace shows where the chain breaks.'}]
                  if state == 'CHAIN_BROKEN' else
                  [{'tool': 'dom_start', 'arguments': {'request': '<customer request verbatim>', 'branches': ['main']}, 'why': 'no project is open in this directory yet'}]
                  if state == 'NOT_STARTED' else
                  [{'tool': 'dom_start', 'arguments': {'request': '<customer request>', 'adopt': True}, 'why': 'the database here is bound to another work; adopt it only if it is yours'}]
                  if state == 'FOREIGN_BASE' else
                  [{'tool': 'dom_find', 'arguments': {}, 'why': 'see the latest claims'},
                   {'tool': 'dom_claim', 'arguments': {'branch': branches[0] if branches else 'main', 'type': 'text', 'text': '<statement>'}, 'why': 'add a claim'}])
    return {
        'status': 'DESCRIBED', 'profile': profile, 'simulated': svc.context.simulation, 'state': state, 'summary': summary,
        'directory': str(svc.store.path.parent),
        'db_path': os.path.realpath(str(svc.store.path)),
        'integrity': integrity,
        # QA004 F-19: the launch policy is visible from the session (it cannot be changed from here)
        'review_policy': {'mode': svc.review_mode, 'launch_mode': svc.launch_review_mode, 'principal': svc.context.principal,
                          'meaning': {'auto': 'every write is admitted at once, review_level=self',
                                      'manual': 'every write waits as a candidate for dom_accept',
                                      'independent': 'a different principal (DOM_PRINCIPAL) must review and admit'}[svc.review_mode]},
        'work_ref': work, 'branches': branches, 'tools': tools, 'claim_types': claim_type_examples() if svc.lite else {},
        'cycle': cycle,
        'review_level': {
            'self': 'admitted by the same local owner who wrote it (lite profile). Honest label: nobody independent has looked at it.',
            'independent': 'admitted after review by a different identity root (strict profile).',
            'null': 'row written before the label existed; it is never counted as independent.'},
        'readiness': {
            'admitted_total': 'current (not replaced) admitted claims, whoever admitted them',
            'admitted_independent': 'of those, admitted after an independent review; 0 in the lite profile - do not read admitted_total as independently checked',
            'pending': 'submitted candidates not yet admitted or rejected',
            'rejected': 'candidates whose latest review was not an approval',
            'stale': 'admitted claims whose dependency branch moved on'},
        'limits': [
            'Simulated server: OMH delivery and Git are simulated; the SQLite database in this directory is real.',
            'Claims and quotes are immutable; there is no delete. dom_update adds a version.',
            'goal_confirmed stays false until the customer confirms; there is no confirm tool in this slice.',
            'profile, review_level, principal and work are fixed at server launch and cannot be passed as arguments.',
            'Branch codes are [A-Za-z0-9_-]{1,32}; declare them with dom_start.'],
        'next_steps': next_steps,
    }
