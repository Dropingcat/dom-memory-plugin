"""Statement capture and distillate pipeline (stage 3, plan items П4.1/П4.6).

Spec: docs/claim-brief.md §12.1-§12.14.  This module owns three layers:

* the deterministic primitives of §12.3/§12.14 -- a versioned marker pack, a
  hardened sentence splitter with raw-text offsets, and the capture scoring
  (this is all the hook ever uses, and it never calls a model);
* the ``capture`` writer: the source quote (verbatim prompt) plus the
  deterministic extractive distillate v1 with resolved anchors;
* the ``dom_distillate`` chain ``propose -> validate(V1-V8) -> accept`` with
  ``show`` / ``withdraw`` / ``dispute`` / ``resolve`` -- every gate computed
  from the ledger, never remembered.

Determinism rule (§12.2): no model, no network, no randomness.  The same
database state and the same marker pack produce the same JSON, so a capture
and every gate can be replayed and audited.  The marker pack is versioned:
``pack_digest()`` is recorded on the source claim as ``capture_pack``, so
"why was this captured" stays answerable after the pack grows (§12.14).

Everything a caller passes is text from the user or from the agent; nothing
here executes it, and a failure never blocks a prompt (the hook catches).
"""
import hashlib
import json
import re
from datetime import datetime, timezone

from .plan_context import _same, _tokens
from .dom_contract import digest, raw_hash

# ---------------------------------------------------------------------------
# The marker pack (§12.3).  Appending words is a release change: bump
# PACK_VERSION so a recorded capture_pack tells which pack made the decision.
# Stems are lowercase and ё-free; matching is prefix matching, so inflections
# of a known stem are caught without a pack change.
# ---------------------------------------------------------------------------

PACK_VERSION = 'v1'

# Strong markers: explicit requests and emphasis.  The first two are *explicit
# requests* -- they capture alone, so a dictionary false negative is cheap to
# repair (§12.3).
STRONG_MARKERS = (
    'запомн', 'запиш', 'важн', 'это факт', 'утвержда', 'наш правил',
    'не так', 'ошибк', 'должн быть', 'всегда', 'никогда', 'потому что',
    'значит',
)
# Explicit request markers: capture alone (§12.3).
EXPLICIT_MARKERS = ('запомн', 'запиш')
# Decision/requirement markers.
DECISION_MARKERS = (
    'дела', 'реша', 'фиксир', 'соглас', 'подтвер', 'требова', 'правило',
    'правила', 'нельзя', 'обязательн', 'лучше сделать', 'базлайн', 'baseline',
)
# Noise markers: pure commands.  A sentence carrying nothing but these is not
# a statement (noise-only = -3).
NOISE_MARKERS = ('сдела', 'покаж', 'запуст')

_STRONG_SET = frozenset(STRONG_MARKERS)
_DECISION_SET = frozenset(DECISION_MARKERS)
_NOISE_SET = frozenset(NOISE_MARKERS)
_EXPLICIT_SET = frozenset(EXPLICIT_MARKERS)


def _pattern_for(marker):
    """One marker -> one regex arm: word-bounded stems joined by whitespace.

    ``\\b`` keeps stem 'сдела' off 'высдела'-shaped words, so a noise stem
    cannot swallow a decision word and vice versa (§12.14 noise valves).
    """
    body = r'\s+'.join(re.escape(w) + r'\w*' for w in marker.split())
    return r'\b' + body


_ALL_MARKERS = _STRONG_SET | _DECISION_SET | _NOISE_SET
MARKER_RE = re.compile('|'.join(sorted(_pattern_for(m) for m in _ALL_MARKERS)))
_MARKER_PATTERNS = {m: re.compile(_pattern_for(m)) for m in _ALL_MARKERS}

# Russian stress marks are typographic noise for matching (u0301 acute,
# u0300 grave: "За́помни", "це́лую/целу́ю" -- the user shows the homonym
# problem this family has).  Meaning is never disambiguated here; matching
# must simply be stable under them.
_STRESS_MARKS = {0x301: None, 0x300: None}


def _norm_text(text):
    """Casefold, ё->е, stress marks off, collapsed whitespace: the matching
    surface."""
    text = text.translate(_STRESS_MARKS)
    return re.sub(r'\s+', ' ', text).strip().casefold().replace('ё', 'е')


def _marker_hits(sentence):
    """The set of marker keys the sentence hits (per-marker search)."""
    text = _norm_text(sentence)
    if not MARKER_RE.search(text):
        return set()
    return {key for key, pat in _MARKER_PATTERNS.items() if pat.search(text)}


def normalize(text):
    """The dedupe surface of a prompt (§12.3): whitespace and case folded,
    stress marks off -- "За́помни" and "Запомни" are one statement."""
    return re.sub(r'\s+', ' ', text.translate(_STRESS_MARKS)).strip().casefold()


def pack_digest():
    """sha256 of the canonical marker pack (§12.3: recorded as capture_pack)."""
    payload = json.dumps(
        {'version': PACK_VERSION, 'strong': sorted(STRONG_MARKERS),
         'decision': sorted(DECISION_MARKERS), 'noise': sorted(NOISE_MARKERS),
         'explicit': sorted(EXPLICIT_MARKERS)},
        ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf8')).hexdigest()


# ---------------------------------------------------------------------------
# The hardened sentence splitter (§12.3, shared with §12.6).  Offsets are
# always into the RAW text: source[start:end] == sentence text.
# ---------------------------------------------------------------------------

MAX_SENTENCE = 300   # a longer run-on is not a clean source sentence (V6 cap)

_SENT_END = re.compile(r'(?<=[.!?…])(?=\s)')
_FENCE_LINE = re.compile(r'(?m)^[ \t]*(?:```|~~~)')
_URL = re.compile(r'https?://\S+')
# Line starts that always begin a new unit: Markdown headings, list markers,
# numbered items ("1. Публ..."), but never a decimal ("3.14 ..." needs no
# whitespace after the dot).
_SEPARATOR_LINE = re.compile(r'(?m)^(?:#{1,6}\s|\s*(?:[-*+•—–]\s|\d+[.)]\s))')


def _protected(text):
    """Sorted non-overlapping ranges a sentence boundary may not cross."""
    out = []
    fences = [(m.start(), m.end()) for m in _FENCE_LINE.finditer(text)]
    for i in range(0, len(fences) - 1, 2):
        out.append((fences[i][0], fences[i + 1][1]))
    if len(fences) % 2:
        out.append((fences[-1][0], len(text)))
    for m in _URL.finditer(text):
        s, e = m.span()
        while e > s and text[e - 1] in '.!?…,;:)"\'':   # the sentence dot is not the URL
            e -= 1
        out.append((s, e))
    out.sort()
    return out


def _inside(pos, ranges):
    return any(s <= pos < e for s, e in ranges)


def _abbr_guard(text, pos):
    """True when the token ending at ``pos`` is a single letter + dot ('т.',
    'Л.', 'д.'): not a sentence end (§12.3 splitter hardening)."""
    start = max(text.rfind(' ', 0, pos), text.rfind('\n', 0, pos),
                text.rfind('\t', 0, pos)) + 1
    return re.fullmatch(r'\S\.', text[start:pos]) is not None


def _decimal_guard(text, pos):
    """True for a digit decimal point ('3. 5'): not a sentence end."""
    return (text[pos - 1] == '.' and text[pos - 2].isdigit()
            and text[pos:pos + 2].strip()[:1].isdigit())


def split_spans(text):
    """Raw prompt text -> [(sentence, start, end)] with hardened boundaries."""
    if not text or not text.strip():
        return []
    protected = _protected(text)
    cuts = {len(text)}
    for m in _SEPARATOR_LINE.finditer(text):
        if not _inside(m.start(), protected):
            cuts.add(m.start())
    for m in _SENT_END.finditer(text):
        pos = m.start()
        if _inside(pos, protected) or _abbr_guard(text, pos) or _decimal_guard(text, pos):
            continue
        cuts.add(pos)
    out, prev = [], 0
    for cut in sorted(cuts):
        if cut <= prev:
            continue
        chunk = text[prev:cut]
        lead = len(chunk) - len(chunk.lstrip())
        trail = len(chunk) - len(chunk.rstrip())
        if lead < len(chunk) - trail:
            out.append((chunk[lead:len(chunk) - trail], prev + lead, cut - trail))
        prev = cut
    return out


# ---------------------------------------------------------------------------
# Capture scoring (§12.3).  strong-or-decision marker = 2; declarative
# sentence with a project noun = 1; noise-only = -3; capture at >= 2; a lone
# marker without support does not capture unless it is an explicit request.
# ---------------------------------------------------------------------------

def _noun_hit(sentence, nouns):
    if not nouns:
        return False
    words = _WORD.findall(_norm_text(sentence))
    return any(any(_same(w, n) for w in words) for n in nouns)


_WORD = re.compile(r'[0-9a-zа-я]+', re.UNICODE)   # plan_context._WORD (kept local: no import cost)


def _score(sentence, nouns):
    """(score, explicit) of one sentence."""
    hits = _marker_hits(sentence)
    strong = hits & _STRONG_SET
    decision = hits & _DECISION_SET
    noise = hits & _NOISE_SET
    explicit = bool(hits & _EXPLICIT_SET)
    if sentence.rstrip().endswith('?'):
        return 0, explicit                    # a question is never a statement
    if noise and not (strong or decision) and not _noun_hit(sentence, nouns):
        return -3, explicit                   # noise-only command (§12.3)
    # §12.3: a marker must be supported.  Generic *emphasis* is the noise
    # source: a lone strong marker (no noun, no second marker) does not
    # capture; an explicit request captures alone.  A decision marker is a
    # statement by itself ("Делаем X.") -- it needs no support, and a prompt
    # with a decision marker captures even next to a command (§12.3).
    if strong and not decision and not explicit and not _noun_hit(sentence, nouns) and len(strong) == 1:
        return 0, explicit                    # lone unsupported emphasis (§12.3)
    return (2 if (strong or decision) else 0) + (1 if _noun_hit(sentence, nouns) else 0), explicit


def capture_decision(source_text, project_nouns):
    """The capture filter (§12.3): does this prompt hold a statement, and
    which 1-3 sentences are the distillate candidates (best first by score,
    then position).  Pure: same inputs -> same result, always."""
    units = split_spans(source_text or '')
    scored = []
    for (text, start, end) in units:
        score, explicit = _score(text, project_nouns)
        if score >= 2 and len(text) <= MAX_SENTENCE:
            scored.append((score, start, text, end, explicit))
    best = sorted(scored, key=lambda x: (-x[0], x[1]))[:3]
    # the distillate joins the selected sentences in source order (a reorder
    # could turn "A, потому что B" into "B. A")
    selected = [(t, s, e) for (_sc, s, t, e, _)
                in sorted(best, key=lambda x: x[1])]
    return {'capture': bool(selected),
            'explicit': any(x[4] for x in scored),
            'selected': selected,
            'score': max([sc for sc, *_ in scored], default=0)}


# ---------------------------------------------------------------------------
# The capture writer (§12.4/§12.11): the hook calls capture() and nothing
# else.  One two-item claims package (source quote + extractive v1), then one
# append package with both properties.  A repeat replays the original receipt
# and repairs a missing property.
# ---------------------------------------------------------------------------

STMT_SOURCE_PREFIX = 'session:'
STMT_LOCATOR_PREFIX = 'prompt:'
STMT_EXTRACT_VERSION = 'stmt-extract-v1'
DISTILLATE_WITHDRAW = 'distillate_withdraw'   # property name of a withdrawn version


def _result(status, **kw):
    out = {'status': status, 'source_id': None, 'distillate_id': None, 'repaired': False, 'line': None}
    out.update(kw)
    return out


def _session_id(session):
    return session if isinstance(session, str) and session.strip() else 'unknown'


def _sha(normalized):
    return hashlib.sha256(normalized.encode('utf8')).hexdigest()


def _top_tokens(text, cap):
    """Content tokens of the text, most frequent first (ties: first said)."""
    seen, order = {}, []
    for tok in _tokens(text):
        if tok not in seen:
            seen[tok] = [0, len(order)]
            order.append(tok)
        seen[tok][0] += 1
    return sorted(order, key=lambda t: (-seen[t][0], seen[t][1]))[:cap]


def _root_and_nouns(svc):
    """The root request (text, branch) and the project nouns: tokens of the
    root request text plus the tokens of every current plan item title."""
    root_id, root_text, branch, titles = None, '', 'main', []
    with svc.store.read() as c:
        row = c.execute('SELECT request_claim_id, branch FROM project_root WHERE id=1').fetchone()
        if row is not None:
            root_id, branch = row['request_claim_id'], row['branch'] or 'main'
            body = c.execute('SELECT raw FROM claims WHERE id=?', (root_id,)).fetchone()
            if body is not None and isinstance(body['raw'], bytes):
                root_text = body['raw'].decode('utf8', 'replace')
        titles = [r['title'] for r in c.execute('SELECT title FROM current_plan_tasks WHERE archived=0')]
    nouns = _tokens(root_text)
    for title in titles:
        nouns.extend(_tokens(title))
    return root_id, root_text, branch, nouns


def _property_value(c, claim_id, name):
    """The latest value of a property event on the claim (parsed when JSON)."""
    if not claim_id:
        return None
    row = c.execute("SELECT payload FROM events WHERE kind='property' AND claim_ref=? ORDER BY seq DESC LIMIT 1",
                    (claim_id,)).fetchone()
    if row is None:
        return None
    try:
        event = json.loads(row['payload']).get('value') or {}
        value = (event.get('value') or {}).get('value')
    except ValueError:
        return None
    if isinstance(value, str) and value[:1] in ('{', '['):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _existing_receipt(svc, key):
    """A prior admission of this exact statement package -> (receipt, True).

    The idempotency ledger is the identity: the same key over an admitted
    candidate replays its original receipt (and claim ids), so a repeat never
    writes a second source.  The reviewed candidate keeps the local item
    names; the record of truth resolves them at admit."""
    with svc.store.read() as c:
        sub = c.execute("SELECT result FROM idempotency WHERE external_work_ref=? AND operation=? AND key=?",
                        (svc.context.work, 'submit:' + svc.context.principal, key)).fetchone()
        if sub is None:
            return None, False
        try:
            candidate = (json.loads(sub['result']) or {}).get('candidate_id')
        except ValueError:
            return None, False
        if not candidate:
            return None, False
        admitted = c.execute('SELECT COUNT(*) FROM claims WHERE candidate=?', (candidate,)).fetchone()[0]
        adm = c.execute("SELECT result FROM idempotency WHERE external_work_ref=? AND operation=? AND candidate_id=?",
                        (svc.context.work, 'admit:' + svc.context.principal, candidate)).fetchone()
        if admitted and adm is not None:
            try:
                receipt = json.loads(adm['result'])
            except ValueError:
                return None, False
            return (receipt or None), True
    return None, False


def is_statement_source(item):
    """A current source quote produced by the capture: metadata source_ref
    'session:...' and locator 'prompt:...' (accepts a claim payload or a
    candidate item -- 'content_type' or 'type')."""
    meta = (item or {}).get('metadata') or {}
    typ = (item or {}).get('content_type') or (item or {}).get('type')
    return typ == 'quote' \
        and str(meta.get('source_ref', '')).startswith(STMT_SOURCE_PREFIX) \
        and str(meta.get('locator', '')).startswith(STMT_LOCATOR_PREFIX)


def capture(svc, prompt, session=None):
    """The hook-side capture (§12.3/§12.4/§12.11).

    Filter -> one two-item claims package (the source quote and the
    deterministic extractive v1; the v1's anatomy.basis names the
    package-local source 'c1', which resolves to the real id at admit) -> one
    append package with both properties (``capture_pack`` on the source,
    ``basis_spans`` on the v1).  A repeat of the same statement (same
    normalized prompt) replays the original receipt and repairs a missing
    property.  Deterministic; the hook catches everything, so a failure never
    blocks a prompt."""
    if not isinstance(prompt, str) or not prompt.strip():
        return _result('SKIPPED')
    normalized = normalize(prompt)
    root_id, root_text, branch, nouns = _root_and_nouns(svc)
    if root_id is not None and normalize(root_text) == normalized:
        return _result('SKIPPED')      # the root request is already the verbatim record
    picked = capture_decision(prompt, nouns)
    if not picked['capture']:
        # §12.3/§12.4: no sentence qualified, so there is nothing to distill
        # and no source to write.  The capture-side status is NO_STATEMENT; it
        # is not a tool refusal (the public refusal for an unknown id is
        # STATEMENT_NOT_FOUND).
        return _result('NO_STATEMENT')
    from . import claim_entry as ce
    sid = _session_id(session)
    sha = _sha(normalized)
    key = 'stmt:%s:%s' % (sid, sha)
    selected = picked['selected']
    v1_text = ' '.join(t for (t, s, e) in selected)
    spans = {'v': 1, 'origin': 'extract', 'candidate_digest': raw_hash(v1_text.encode('utf8')),
             'sentences': [{'i': k, 'anchors': [{'quote': t, 'start': s, 'end': e}]}
                           for k, (t, s, e) in enumerate(selected)]}
    src_raw, src_args, _, src_reason, _, _ = ce._validated_claim_args(
        svc, branch, 'quote', prompt,
        {'source_ref': STMT_SOURCE_PREFIX + sid, 'locator': STMT_LOCATOR_PREFIX + sha[:12],
         'reason': 'user statement capture'})
    cand_raw, cand_args, _, cand_reason, _, cand_anatomy = ce._validated_claim_args(
        svc, branch, 'text', v1_text,
        {'classification': 'interpretation', 'attribution': 'deterministic',
         'keywords': _top_tokens(v1_text, 6), 'purpose': 'distilled comment', 'basis': ['c1'],
         'reason': 'user statement distillate (extractive v1)'})
    source_item = ce.build_item('quote', prompt, src_raw, branch, src_args, [], src_reason, 'c1')
    v1_item = ce.build_item('text', v1_text, cand_raw, branch, cand_args, [], cand_reason, 'c2', anatomy=cand_anatomy)

    def build(revision):
        return ce._package(svc, branch, revision, [source_item, v1_item], key, 'claims',
                           STMT_EXTRACT_VERSION, src_reason)

    receipt, already = _existing_receipt(svc, key)
    if receipt is None:
        receipt = ce._commit(svc, branch, build)
        if receipt.get('status') != 'DEMO_ADMITTED':
            return _result(receipt.get('status', 'REFUSED'))
        already = False
    ids = receipt.get('claim_ids') or {}
    source_id, distillate_id = ids.get('c1'), ids.get('c2')
    if not source_id:
        return _result('REFUSED')
    with svc.store.read() as c:
        have = (_property_value(c, source_id, 'capture_pack') is not None,
                _property_value(c, distillate_id, 'basis_spans') is not None)
    repaired = bool(already and not all(have))
    if not all(have):
        events = [
            {'local_id': 'p1', 'event_type': 'property', 'claim_ref': source_id, 'reason': 'statement capture pack',
             'supersedes': '', 'ref': 'dom_distillate',
             'value': {'name': 'capture_pack',
                       'value': json.dumps({'pack': PACK_VERSION, 'digest': pack_digest()}, ensure_ascii=False)}},
            {'local_id': 'p2', 'event_type': 'property', 'claim_ref': distillate_id, 'reason': 'statement anchors',
             'supersedes': '', 'ref': 'dom_distillate',
             'value': {'name': 'basis_spans', 'value': json.dumps(spans, ensure_ascii=False, sort_keys=True)}},
        ]
        from . import planning
        planning._append(svc, branch, events, 'statement capture properties', 'stmt-anchors:%s' % sha[:12])
    line = None
    if not already:
        if distillate_id:
            line = ('[DOM project memory] statement %s; distillate %s (extracted). The source is ground truth; '
                    'improve it with dom_distillate(action="propose", source="%s", ...).'
                    % (source_id, distillate_id, source_id))
        else:
            line = '[DOM project memory] statement %s; no distillate (no qualifying sentence).' % source_id
    return _result('ALREADY_RECORDED' if already else 'STATEMENT_RECORDED',
                   source_id=source_id, distillate_id=distillate_id, repaired=repaired, line=line)


# ---------------------------------------------------------------------------
# The read side (§12.5/§12.7): versions, states and the effective version are
# always recomputed from the ledger.  Nothing here is a stored status.
# ---------------------------------------------------------------------------

DISTILLATE_ACTIONS = ('show', 'propose', 'validate', 'accept', 'withdraw', 'dispute', 'resolve')


def _denied():
    from .admission import Denied
    return Denied


def _json_value(value):
    """A property value stored as JSON text -> the object; anything else as is."""
    if isinstance(value, str) and value[:1] in ('{', '['):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _properties(c, claim_id):
    """claim_id -> {name: parsed value}; the latest write wins."""
    out = {}
    for row in c.execute("SELECT payload FROM events WHERE claim_ref=? AND kind='property' ORDER BY seq",
                         (claim_id,)):
        try:
            event = json.loads(row['payload'])['value']['value']
        except (ValueError, TypeError, KeyError):
            continue
        name = event.get('name')
        if isinstance(name, str) and name:
            out[name] = _json_value(event.get('value'))
    return out


def _chain(c, claim_id):
    """The whole version chain containing claim_id, oldest -> newest."""
    from .claim_entry import _children
    children = _children(c)
    parent = {new: old for old, new in children.items()}
    root, seen = claim_id, set()
    while root in parent and root not in seen:
        seen.add(root)
        root = parent[root]
    out, cur, seen = [], root, set()
    while cur and cur not in seen:
        seen.add(cur)
        row = c.execute('SELECT * FROM claims WHERE id=?', (cur,)).fetchone()
        if row is None:
            break
        out.append(row)
        cur = children.get(cur)
    return out


def _versions(c, claim_id):
    """Oldest -> newest: claim_id, version, text, raw_sha256, payload."""
    out = []
    for k, row in enumerate(_chain(c, claim_id), 1):
        try:
            payload = json.loads(row['payload'])
        except (ValueError, TypeError):
            payload = {}
        out.append({'claim_id': row['id'], 'version': k,
                    'text': bytes(row['raw']).decode('utf8', 'replace'),
                    'raw_sha256': row['raw_sha256'], 'payload': payload})
    return out


def _open_conflicts(c, claim_id):
    """conflict events on claim_id with no later resolve of the same ref."""
    open_refs = []
    for row in c.execute("SELECT kind, payload FROM events WHERE claim_ref=? AND kind IN ('conflict','resolve') ORDER BY seq",
                         (claim_id,)):
        try:
            event = json.loads(row['payload'])['value']['value']
        except (ValueError, TypeError, KeyError):
            continue
        ref = event.get('conflict_ref')
        if not isinstance(ref, str) or not ref:
            continue
        if row['kind'] == 'conflict':
            if ref not in open_refs:
                open_refs.append(ref)
        elif ref in open_refs:
            open_refs.remove(ref)
    return [{'conflict_ref': ref} for ref in open_refs]


def _anchors_digest(spans):
    return digest({k: v for k, v in spans.items() if k != 'candidate_digest'})


def _review_digest(review):
    return digest({k: v for k, v in review.items() if k != 'at'})


def _review_pass(version, review, spans, source_text):
    if not isinstance(review, dict) or review.get('verdict') != 'pass':
        return False
    if review.get('candidate_digest') != version['raw_sha256']:
        return False
    if review.get('source_digest') != raw_hash(source_text.encode('utf8')):
        return False
    return isinstance(spans, dict) and review.get('anchors_digest') == _anchors_digest(spans)


def _accept_matches(version, review, accept):
    return (isinstance(accept, dict) and isinstance(review, dict)
            and accept.get('candidate_digest') == version['raw_sha256']
            and accept.get('review_digest') == _review_digest(review))


def _origin_of(props):
    spans = props.get('basis_spans')
    return spans.get('origin') if isinstance(spans, dict) else None


def _explicit_archived(c):
    """Claims retired by an explicit archive event (not by a version link)."""
    return {r[0] for r in c.execute(
        "SELECT DISTINCT claim_ref FROM events WHERE kind='archive' AND claim_ref IS NOT NULL")}


def _refuse_archived(c, source_row):
    """A retired source stays readable but takes no new versions or reviews:
    otherwise a proposal would be born ``superseded`` and the chain could
    never become effective.  Dispute/resolve stay allowed as cleanup."""
    if source_row['id'] in _explicit_archived(c):
        raise _denied()('STATEMENT_NOT_FOUND', field='source',
                        hint='This source is archived (superseded): it stays readable with action="show" '
                             'but takes no new versions. Capture the new instruction as a new statement.')


def _state(c, version, is_v1, source_text, source_replaced=False):
    """§12.5 precedence: superseded > withdrawn > disputed > accepted >
    validated > rejected > extracted > proposed."""
    cid = version['claim_id']
    if source_replaced or cid in _explicit_archived(c):
        return 'superseded'
    props = _properties(c, cid)
    if 'distillate_withdraw' in props:
        return 'withdrawn'
    if _open_conflicts(c, cid):
        return 'disputed'
    review = props.get('distillate_review')
    spans = props.get('basis_spans')
    if _review_pass(version, review, spans, source_text) and _accept_matches(version, review, props.get('distillate_accept')):
        return 'accepted'
    if isinstance(review, dict) and review.get('verdict') == 'pass':
        return 'validated'
    if isinstance(review, dict) and review.get('verdict') == 'fail':
        return 'rejected'
    if is_v1 and isinstance(spans, dict) and spans.get('origin') == 'extract':
        return 'extracted'
    return 'proposed'


def _effective(c, source_id, versions, states):
    """§12.5 effective: no source conflict; newest accepted, else extracted v1."""
    if _open_conflicts(c, source_id):
        return None
    for version, state in zip(reversed(versions), reversed(states)):
        if state == 'accepted' and not _open_conflicts(c, version['claim_id']):
            return {'claim_id': version['claim_id'], 'version': version['version'],
                    'origin': _origin_of(_properties(c, version['claim_id']))}
    if versions and states[0] == 'extracted' and not _open_conflicts(c, versions[0]['claim_id']):
        return {'claim_id': versions[0]['claim_id'], 'version': 1, 'origin': 'extract'}
    return None


def _root_for_source(c, source_id):
    """The v1 claim of the logical distillate captured for this source."""
    from .claim_entry import _children
    replaced = set(_children(c).values())
    for row in c.execute("SELECT id, payload FROM claims WHERE content_type='text' AND origin_version=?",
                         (STMT_EXTRACT_VERSION,)):
        if row['id'] in replaced:
            continue                     # a later version, not the root of a chain
        try:
            payload = json.loads(row['payload'])
        except (ValueError, TypeError):
            continue
        if ((payload.get('anatomy') or {}).get('basis') or []) == [source_id]:
            return row['id']
    return None


def _statement_source_row(c, claim_id, field):
    row = c.execute('SELECT id, content_type, raw, payload FROM claims WHERE id=?', (claim_id,)).fetchone()
    if row is None or row['content_type'] != 'quote':
        raise _denied()('STATEMENT_NOT_FOUND', field=field)
    try:
        payload = json.loads(row['payload'])
    except (ValueError, TypeError):
        payload = None
    if not is_statement_source(payload):
        raise _denied()('STATEMENT_NOT_FOUND', field=field)
    return row


def _resolve_target(c, source, distillate):
    """(source_row, root_id) for show and every write action;
    STATEMENT_NOT_FOUND when the id is not a captured statement."""
    Denied = _denied()
    if source is None and distillate is None:
        raise Denied('INVALID_FIELD', field='source',
                     hint="'show' needs 'source' (the statement quote) or 'distillate' (any version id).")
    source_id = root_id = None
    if distillate is not None:
        if type(distillate) is not str or not distillate.strip():
            raise Denied('INVALID_FIELD', field='distillate')
        row = c.execute('SELECT id, content_type FROM claims WHERE id=?', (distillate,)).fetchone()
        if row is None or row['content_type'] != 'text':
            raise Denied('STATEMENT_NOT_FOUND', field='distillate')
        root_id = _chain(c, distillate)[0]['id']
        root_payload = json.loads(c.execute('SELECT payload FROM claims WHERE id=?', (root_id,)).fetchone()['payload'])
        basis = (root_payload.get('anatomy') or {}).get('basis') or []
        if len(basis) != 1:
            raise Denied('STATEMENT_NOT_FOUND', field='distillate')
        source_id = basis[0]
    if source is not None:
        if type(source) is not str or not source.strip():
            raise Denied('INVALID_FIELD', field='source')
        if source_id is not None and source_id != source:
            raise Denied('STATEMENT_NOT_FOUND', field='source',
                         hint='source and distillate belong to different statements.')
        source_id = source
    source_row = _statement_source_row(c, source_id, 'source')
    if root_id is None:
        root_id = _root_for_source(c, source_id)
        if root_id is None:
            raise Denied('STATEMENT_NOT_FOUND', field='source')
    return source_row, root_id


def _record(action, status, **kw):
    """The uniform answer of every action (§12.5): state, effective, next steps."""
    out = {'status': status, 'simulated': True, 'action': action,
           'source_id': None, 'source_state': 'active',
           'distillate_id': None, 'logical_claim_id': None,
           'effective': None, 'versions': [], 'open_conflicts': [],
           'failed': [], 'detail': {}, 'review': None, 'repaired': False, 'next_steps': []}
    out.update(kw)
    return out


def _public_version(c, version, state):
    props = _properties(c, version['claim_id'])
    out = {'claim_id': version['claim_id'], 'version': version['version'], 'state': state,
           'origin': _origin_of(props), 'raw_sha256': version['raw_sha256'], 'text': version['text']}
    for name, key in (('distillate_review', 'review'), ('distillate_accept', 'accept'),
                      ('distillate_withdraw', 'withdraw')):
        if name in props:
            out[key] = props[name]
    return out


def _show_next(out):
    sid = out['source_id']
    if out['source_state'] == 'superseded':
        return [{'tool': 'dom_find', 'arguments': {'query': '<statement>', 'type': 'quote'},
                 'why': 'the source was replaced; read the new source and its distillate'}]
    if out['source_state'] == 'disputed':
        return [{'tool': 'dom_distillate', 'arguments': {'action': 'resolve', 'source': sid, 'note': '<why the dispute is closed>'},
                 'why': 'the source is disputed; no distillate is effective until it is resolved'}]
    if out['effective'] is None:
        return [{'tool': 'dom_distillate',
                 'arguments': {'action': 'propose', 'source': sid, 'text': '<text>', 'anchors': [['<verbatim quote>']]},
                 'why': 'no version is effective; propose a grounded candidate'}]
    state = out['versions'][-1]['state'] if out['versions'] else None
    if state in ('proposed',):
        return [{'tool': 'dom_distillate', 'arguments': {'action': 'validate', 'source': sid},
                 'why': 'run the V1-V8 gates on the current version'}]
    if state == 'rejected':
        return [{'tool': 'dom_distillate',
                 'arguments': {'action': 'propose', 'source': sid, 'text': '<text>', 'anchors': [['<verbatim quote>']]},
                 'why': 'the current version failed the gates; fix the failed checks and propose again'}]
    if state == 'validated':
        return [{'tool': 'dom_distillate', 'arguments': {'action': 'accept', 'source': sid, 'by': 'agent'},
                 'why': 'the current version passed the gates; accept it to make it effective'}]
    return []


def _require_current(distillate, current):
    """validate/accept/withdraw are current-version actions: naming another
    version is NOT_CURRENT, never a silent write on the wrong version."""
    if distillate is not None and distillate != current['claim_id']:
        raise _denied()('NOT_CURRENT', claim_id=current['claim_id'],
                        hint='Only the current version can be validated, accepted or withdrawn; '
                             'read it with dom_distillate(action="show").')


def _show(svc, source=None, distillate=None):
    from .claim_entry import _lite_only
    _lite_only(svc)
    svc.scope()
    svc.ready()
    with svc.store.read() as c:
        source_row, root_id = _resolve_target(c, source, distillate)
        source_text = bytes(source_row['raw']).decode('utf8', 'replace')
        source_replaced = source_row['id'] in _explicit_archived(c)
        versions = _versions(c, root_id)
        states = [_state(c, v, k == 1, source_text, source_replaced) for k, v in enumerate(versions, 1)]
        current = versions[-1]
        props = _properties(c, current['claim_id'])
        review = props.get('distillate_review')
        out = _record('show', 'DISTILLATE_SHOWN', source_id=source_row['id'],
                      source_state=('superseded' if source_replaced else
                                    'disputed' if _open_conflicts(c, source_row['id']) else 'active'),
                      distillate_id=current['claim_id'], logical_claim_id=root_id,
                      effective=_effective(c, source_row['id'], versions, states),
                      versions=[_public_version(c, v, states[k - 1]) for k, v in enumerate(versions, 1)],
                      open_conflicts=_open_conflicts(c, source_row['id']),
                      failed=list(review.get('failed') or []) if isinstance(review, dict) else [],
                      detail=review.get('detail') or {} if isinstance(review, dict) else {},
                      review=review if isinstance(review, dict) else None)
        out['next_steps'] = _show_next(out)
        return out


# ---------------------------------------------------------------------------
# Checkpoints V1-V8 (§12.6) and the propose writer (§12.5/§12.9).  Every
# check is pure over the ledger snapshot: same state, same verdict.
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r'\d+(?:[.,]\d+)?')
_WORD_CASED = re.compile(r'[0-9A-Za-zА-Яа-яЁё]+')
_NEGATION = ('не', 'нет', 'never', 'not', 'no')
_QUANTIFIER = ('всегда', 'никогда', 'только', 'always', 'never', 'only')
_BOUNDED = {w: re.compile(r'(?<!\w)' + re.escape(w) + r'(?!\w)') for w in set(_NEGATION + _QUANTIFIER)}


def _same_text(text):
    """§12.6: normalized text -- whitespace and case are not a difference."""
    return re.sub(r'\s+', ' ', text or '').strip().casefold()


def _candidate_sentences(text):
    return [t for (t, _start, _end) in split_spans(text or '')]


def resolve_anchors(source_text, anchors):
    """§12.6 V3 at propose time: one inner array per candidate sentence, each
    quote a non-empty verbatim substring that occurs exactly once in the
    source.  Stored spans carry offsets, so a repeated source sentence can be
    anchored by distinct offsets; the once-rule applies to the caller's quote."""
    Denied = _denied()
    if type(anchors) is not list or not anchors:
        raise Denied('PROPOSAL_INVALID', field='anchors',
                     hint="'anchors' is one inner array per sentence, each a list of verbatim quotes from the source.")
    groups = []
    for i, group in enumerate(anchors):
        if type(group) is not list or not group or any(type(q) is not str or not q.strip() for q in group):
            raise Denied('PROPOSAL_INVALID', field='anchors',
                         hint='anchors[%d] must be a non-empty list of non-empty strings.' % i)
        resolved = []
        for quote in group:
            count = source_text.count(quote)
            if count == 0:
                raise Denied('SPAN_NOT_FOUND', field='anchors',
                             hint='An anchor is not a verbatim part of the source; quote the source exactly.')
            if count > 1:
                raise Denied('SPAN_AMBIGUOUS', field='anchors',
                             hint='An anchor occurs more than once in the source; quote a longer, unique piece.')
            start = source_text.find(quote)
            resolved.append({'quote': quote, 'start': start, 'end': start + len(quote)})
        groups.append({'i': i, 'anchors': resolved})
    return groups


def _anchor_text(group):
    return ' '.join(a.get('quote') or '' for a in (group.get('anchors') or []))


def _check_v1(c, source_row, candidate, source_text):
    problems = []
    try:
        payload = json.loads(source_row['payload'])
    except (ValueError, TypeError):
        payload = {}
    if not is_statement_source(payload) or source_row['id'] in _explicit_archived(c):
        problems.append('source')
    meta = payload.get('metadata') or {}
    if meta.get('source_snapshot_digest') != raw_hash(source_text.encode('utf8')):
        problems.append('digest')
    basis = (candidate['payload'].get('anatomy') or {}).get('basis') or []
    if basis != [source_row['id']]:
        problems.append('basis')
    return {'reason': problems[0]} if problems else None


def _check_v2(c, candidate, spans):
    from .claim_entry import _children
    payload = candidate['payload']
    meta = payload.get('metadata') or {}
    anatomy = payload.get('anatomy') or {}
    problems = []
    if payload.get('content_type') != 'text':
        problems.append('type')
    if meta.get('classification') != 'interpretation':
        problems.append('classification')
    if not str(anatomy.get('purpose') or '').strip():
        problems.append('purpose')
    if not isinstance(spans, dict) or spans.get('origin') != 'agent':
        problems.append('origin')
    if candidate['claim_id'] in _explicit_archived(c) or candidate['claim_id'] in _children(c):
        problems.append('current')
    return {'reason': problems[0]} if problems else None


def _check_v3(candidate_text, spans, sentences, source_text):
    if not 1 <= len(sentences) <= 3:
        return {'reason': 'sentences', 'count': len(sentences)}
    stored = spans.get('sentences') if isinstance(spans, dict) else None
    if not isinstance(stored, list) or len(stored) != len(sentences):
        return {'reason': 'groups', 'sentences': len(sentences),
                'groups': len(stored) if isinstance(stored, list) else 0}
    for i, group in enumerate(stored):
        anchors = group.get('anchors') if isinstance(group, dict) else None
        if not isinstance(anchors, list) or not anchors:
            return {'sentence': i, 'reason': 'empty'}
        for anchor in anchors:
            quote = anchor.get('quote') if isinstance(anchor, dict) else None
            start, end = (anchor.get('start'), anchor.get('end')) if isinstance(anchor, dict) else (None, None)
            if type(quote) is not str or not quote:
                return {'sentence': i, 'reason': 'quote'}
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source_text) \
                    or source_text[start:end] != quote:
                return {'sentence': i, 'reason': 'offset', 'quote': quote[:40]}
    if not isinstance(spans, dict) or spans.get('candidate_digest') != raw_hash(candidate_text.encode('utf8')):
        return {'reason': 'candidate_digest'}
    return None


def _check_v4(spans, sentences):
    stored = spans.get('sentences') if isinstance(spans, dict) else None
    if not isinstance(stored, list) or len(stored) != len(sentences):
        return {'reason': 'anchors'}
    for i, sentence in enumerate(sentences):
        tokens = _tokens(sentence)
        pool = _tokens(_anchor_text(stored[i]))
        novel = [t for t in tokens if not any(_same(t, other) for other in pool)]
        coverage = (len(tokens) - len(novel)) / len(tokens) if tokens else 1.0
        if len(tokens) <= 3:
            if novel:
                return {'sentence': i, 'novel': novel[:5]}
        elif len(novel) > 2 or coverage < 0.6:
            return {'sentence': i, 'novel': novel[:5], 'coverage': round(coverage, 2)}
    return None


def _count_markers(text, words):
    folded = text.casefold().replace('ё', 'е')
    return {w: len(_BOUNDED[w].findall(folded)) for w in words}


def _numeral_forms():
    """Surface forms of Russian cardinal numerals, for V5 word-number checks.

    A word is a number only if it is in this pack: ``и`` and every other
    ordinary word stay out of the fact check.  Surface forms are compared,
    not values, so ``10`` and ``десять`` are different tokens (§12.6 V5,
    §12.14 word-numbers).  The pack is deliberately small and explicit; a new
    inflection is a pack change with a test, not a regex guess.
    """
    forms = set()

    def add(*words):
        forms.update(words)

    add('ноль', 'нуля', 'нулю', 'нулем', 'нуле')
    add('один', 'одна', 'одно', 'одного', 'одной', 'одному', 'одним', 'одном', 'одну')
    add('два', 'две', 'двух', 'двум', 'двумя')
    add('три', 'трех', 'трем', 'тремя')
    add('четыре', 'четырех', 'четырем', 'четырьмя')
    for stem in ('пят', 'шест', 'сем', 'восем', 'девят'):
        add(stem + 'ь', stem + 'и', stem + 'ью')
    for stem in ('одиннадцат', 'двенадцат', 'тринадцат', 'четырнадцат', 'пятнадцат',
                 'шестнадцат', 'семнадцат', 'восемнадцат', 'девятнадцат',
                 'двадцат', 'тридцат'):
        add(stem + 'ь', stem + 'и', stem + 'ью')
    add('сорок', 'сорока')
    add('десять', 'десяти', 'десятью')
    add('пятьдесят', 'пятидесяти', 'пятьюдесятью')
    add('шестьдесят', 'шестидесяти', 'шестьюдесятью')
    add('семьдесят', 'семидесяти', 'семьюдесятью')
    add('восемьдесят', 'восьмидесяти', 'восемьюдесятью')
    add('девяносто', 'девяноста')
    add('сто', 'ста', 'стам', 'стами', 'стах')
    add('двести', 'двухсот', 'двумстам', 'двумястами', 'двухстах')
    add('триста', 'трехсот', 'тремстам', 'тремястами', 'трехстах')
    add('четыреста', 'четырехсот', 'четыремстам', 'четырьмястами', 'четырехстах')
    add('пятьсот', 'пятисот', 'пятистам', 'пятьюстами', 'пятистах')
    add('шестьсот', 'шестисот', 'шестистам', 'шестьюстами', 'шестистах')
    add('семьсот', 'семисот', 'семистам', 'семьюстами', 'семистах')
    add('восемьсот', 'восьмисот', 'восьмистам', 'восемьюстами', 'восьмистах')
    add('девятьсот', 'девятисот', 'девятистам', 'девятьюстами', 'девятистах')
    add('тысяча', 'тысячи', 'тысяче', 'тысячу', 'тысячей', 'тысяч',
        'тысячам', 'тысячами', 'тысячах')
    add('миллион', 'миллиона', 'миллиону', 'миллионом', 'миллионе',
        'миллионы', 'миллионов', 'миллионам', 'миллионами', 'миллионах')
    add('миллиард', 'миллиарда', 'миллиарду', 'миллиардом', 'миллиарде',
        'миллиарды', 'миллиардов', 'миллиардам', 'миллиардами', 'миллиардах')
    return frozenset(forms)


_NUMERAL_WORDS = _numeral_forms()


def _numeral_tokens(text):
    """Number surfaces of a text: normalized digit tokens plus the numeral
    words of the pack.  Ordinary words are ignored; the comparison is by
    surface form, so a digit and a word for the same value are a V5 failure
    (§12.6 V5 / §12.14 word-numbers)."""
    text = text.translate(_STRESS_MARKS)
    out = []
    for m in _NUMBER.finditer(text):
        out.append(m.group(0).replace(',', '.'))
    for w in _WORD_CASED.findall(text):
        low = w.lower().replace('ё', 'е')
        if low in _NUMERAL_WORDS:
            out.append(low)
    return out


def _check_v5(spans, sentences, source_text):
    stored = spans.get('sentences') if isinstance(spans, dict) else None
    if not isinstance(stored, list) or len(stored) != len(sentences):
        return {'reason': 'anchors'}
    source_fold = source_text.casefold().replace('ё', 'е')
    for i, sentence in enumerate(sentences):
        anchor = _anchor_text(stored[i])
        for token in _numeral_tokens(sentence):
            if token not in _numeral_tokens(anchor):
                return {'sentence': i, 'numbers': [token]}
        words = _WORD_CASED.findall(sentence)
        capitals = [w for w in words[1:] if w[:1].isupper()]
        absent = [w for w in capitals if w.casefold().replace('ё', 'е') not in source_fold]
        if absent:
            return {'sentence': i, 'capitals': absent[:5]}
        for label, marks in (('negation', _NEGATION), ('quantifier', _QUANTIFIER)):
            here, there = _count_markers(sentence, marks), _count_markers(anchor, marks)
            differing = [w for w in marks if here[w] != there[w]]
            if differing:
                return {'sentence': i, label: differing[:5], 'sentence_counts': {w: here[w] for w in differing},
                        'anchor_counts': {w: there[w] for w in differing}}
    return None


def _check_v6(sentences, candidate_text, source_text, effective_text):
    if not 1 <= len(sentences) <= 3:
        return {'reason': 'sentences', 'count': len(sentences)}
    for i, sentence in enumerate(sentences):
        if len(sentence) > 300:
            return {'sentence': i, 'chars': len(sentence)}
    total, source_len = len(candidate_text), len(source_text)
    if source_len >= 200:
        if total > 0.6 * source_len:
            return {'reason': 'long', 'chars': total, 'limit': int(0.6 * source_len)}
    elif total > source_len:
        return {'reason': 'long', 'chars': total, 'source': source_len}
    if effective_text is not None and _same_text(candidate_text) == _same_text(effective_text):
        return {'reason': 'same_as_effective'}
    return None


def _check_v7(c, source_id, candidate_id):
    if _open_conflicts(c, source_id):
        return {'target': 'source'}
    if _open_conflicts(c, candidate_id):
        return {'target': 'candidate'}
    return None


def run_checks(c, source_row, candidate, spans, effective_text, fail_count):
    """V1-V8 exactly per §12.6.  Pure over the snapshot ``c``; returns the
    review body's ``checks`` / ``failed`` / ``detail`` (no timestamp)."""
    source_text = bytes(source_row['raw']).decode('utf8', 'replace')
    sentences = _candidate_sentences(candidate['text'])
    detail = {}
    for code, problem in (
        ('V1_SOURCE', _check_v1(c, source_row, candidate, source_text)),
        ('V2_PROVENANCE', _check_v2(c, candidate, spans)),
        ('V3_ANCHORS', _check_v3(candidate['text'], spans, sentences, source_text)),
        ('V4_GROUNDING', _check_v4(spans, sentences)),
        ('V5_FACTS', _check_v5(spans, sentences, source_text)),
        ('V6_COMPRESSION', _check_v6(sentences, candidate['text'], source_text, effective_text)),
        ('V7_DISPUTE', _check_v7(c, source_row['id'], candidate['claim_id'])),
        ('V8_LOOP', {'failed': fail_count} if fail_count >= 3 else None),
    ):
        if problem is not None:
            detail[code] = problem
    checks = {('V%d' % i): (next((code for code in detail if code.startswith('V%d_' % i)), 'pass'))
              for i in range(1, 9)}
    return {'checks': checks, 'failed': list(detail), 'detail': detail}


def _fail_count(c, root_id):
    """Failed reviews over the whole logical claim: the latest review of each
    version counts once (§12.6 V8)."""
    count = 0
    for version in _versions(c, root_id):
        review = _properties(c, version['claim_id']).get('distillate_review')
        if isinstance(review, dict) and review.get('verdict') == 'fail':
            count += 1
    return count


def _effective_version(c, source_row, versions, source_replaced):
    """The raw effective version row (newest accepted, else the extracted v1)."""
    source_text = bytes(source_row['raw']).decode('utf8', 'replace')
    states = [_state(c, v, k == 1, source_text, source_replaced) for k, v in enumerate(versions, 1)]
    ref = _effective(c, source_row['id'], versions, states)
    if ref is None:
        return None
    for version in versions:
        if version['claim_id'] == ref['claim_id']:
            return version
    return None


def _anchor_quotes(anchors):
    if type(anchors) is not list:
        return None
    out = []
    for group in anchors:
        if type(group) is not list or any(type(q) is not str for q in group):
            return None
        out.append(list(group))
    return out


def _stored_quotes(spans):
    return [[a.get('quote') for a in (g.get('anchors') or [])] for g in (spans.get('sentences') or [])]


def _write_property(svc, claim_id, name, value, reason):
    """A property event; a refusal leaves the version unanchored (repair path)."""
    from . import claim_entry as ce
    from .admission import Denied
    try:
        out = ce.annotate(svc, claim_id, 'property', {'name': name, 'value': value, 'reason': reason})
    except Denied:
        return False
    return isinstance(out, dict) and out.get('status') == 'ANNOTATED'


def _proposed_answer(svc, source_id, status, repaired=False):
    out = _show(svc, source_id, None)
    out['status'] = status
    out['action'] = 'propose'
    out['repaired'] = repaired
    return out


def _finish(svc, source_id, status, action):
    out = _show(svc, source_id, None)
    out['status'] = status
    out['action'] = action
    return out


def _propose(svc, source=None, distillate=None, text=None, anchors=None):
    """§12.5/§12.9: a new version with anchors, or a replay/repair of the
    current one.  A proposal never displaces the effective version."""
    from . import claim_entry as ce
    from .claim_entry import _lite_only
    Denied = _denied()
    _lite_only(svc)
    svc.scope()
    svc.capability('submit')
    svc.ready()
    if type(text) is not str or not text.strip():
        raise Denied('PROPOSAL_INVALID', field='text', hint="'text' is the candidate distillate (1-3 sentences).")
    with svc.store.read() as c:
        source_row, root_id = _resolve_target(c, source, distillate)
        source_text = bytes(source_row['raw']).decode('utf8', 'replace')
        _refuse_archived(c, source_row)
        source_replaced = False       # a retired source never reaches the writer
        versions = _versions(c, root_id)
        current = versions[-1]
        props = _properties(c, current['claim_id'])
        fails = _fail_count(c, root_id)
        if fails >= 3:
            raise Denied('DISTILLATE_LOOP',
                         hint='Three failed reviews for this logical claim; the chain is stopped. Ask the user for a new instruction.')
        if _open_conflicts(c, source_row['id']):
            raise Denied('DISTILLATE_DISPUTED',
                         hint='The source is disputed; resolve it before proposing a new version.')
        same_text = _same_text(text) == _same_text(current['text'])
        stored = props.get('basis_spans')
        if same_text and isinstance(stored, dict):
            supplied = _anchor_quotes(anchors)
            if supplied is not None and supplied == _stored_quotes(stored):
                return _proposed_answer(svc, source_row['id'], 'ALREADY_RECORDED')
            raise Denied('PROPOSAL_CONFLICT',
                         hint='The current version has this text with other anchors; withdraw it or supply the original anchors.')
        effective_row = None if same_text else _effective_version(c, source_row, versions, source_replaced)
        if not same_text and effective_row is not None and _same_text(text) == _same_text(effective_row['text']):
            raise Denied('NO_IMPROVEMENT',
                         hint='The candidate equals the effective version; propose a real improvement or keep it.')
        groups = stored.get('sentences') if (same_text and isinstance(stored, dict)) else None
        if not isinstance(groups, list):
            groups = resolve_anchors(source_text, anchors)
        spans = {'v': 1, 'origin': 'agent', 'candidate_digest': raw_hash(text.encode('utf8')),
                 'sentences': groups}
    value = json.dumps(spans, ensure_ascii=False, sort_keys=True)
    if same_text:
        repaired = _write_property(svc, current['claim_id'], 'basis_spans', value,
                                   'distillate proposal anchors repair')
        return _proposed_answer(svc, source_row['id'], 'ALREADY_RECORDED', repaired=repaired)
    updated = ce.update(svc, current['claim_id'], text, 'distillate proposal',
                        idempotency_key='stmt-propose:%s:%s:%s' % (
                            source_row['id'], spans['candidate_digest'], _anchors_digest(spans)))
    new_id = updated.get('new_claim_id')
    if not new_id:
        return _record('propose', updated.get('status', 'REFUSED'))
    _write_property(svc, new_id, 'basis_spans', value, 'distillate proposal anchors')
    return _proposed_answer(svc, source_row['id'], 'PROPOSED')


VALIDATOR = 'dom-distillate/1'


def _validate(svc, source=None, distillate=None):
    """§12.6: run V1-V8 on the current version and store the review."""
    from .claim_entry import _lite_only
    Denied = _denied()
    _lite_only(svc)
    svc.scope()
    svc.ready()
    with svc.store.read() as c:
        source_row, root_id = _resolve_target(c, source, distillate)
        source_text = bytes(source_row['raw']).decode('utf8', 'replace')
        _refuse_archived(c, source_row)
        source_replaced = False       # a retired source never reaches the reviewer
        versions = _versions(c, root_id)
        current = versions[-1]
        _require_current(distillate, current)
        props = _properties(c, current['claim_id'])
        fails = _fail_count(c, root_id)
        if fails >= 3:
            raise Denied('DISTILLATE_LOOP',
                         hint='Three failed reviews for this logical claim; the chain is stopped. Ask the user for a new instruction.')
        spans = props.get('basis_spans')
        spans = spans if isinstance(spans, dict) else {}
        effective_row = _effective_version(c, source_row, versions, source_replaced)
        result = run_checks(c, source_row, current, spans,
                            effective_row['text'] if effective_row else None, fails)
        source_digest = raw_hash(source_text.encode('utf8'))
        candidate_digest = current['raw_sha256']
        anchors_digest = _anchors_digest(spans) if spans else digest({})
        existing = props.get('distillate_review')
        if isinstance(existing, dict) and existing.get('validator') == VALIDATOR \
                and existing.get('candidate_digest') == candidate_digest \
                and existing.get('anchors_digest') == anchors_digest \
                and existing.get('source_digest') == source_digest:
            return _finish(svc, source_row['id'], 'ALREADY_RECORDED', 'validate')
        review = {'v': 1, 'validator': VALIDATOR, 'at': datetime.now(timezone.utc).isoformat(),
                  'source_id': source_row['id'], 'source_digest': source_digest,
                  'candidate_digest': candidate_digest, 'anchors_digest': anchors_digest,
                  'checks': result['checks'], 'failed': result['failed'], 'detail': result['detail'],
                  'verdict': 'pass' if not result['failed'] else 'fail'}
    if not _write_property(svc, current['claim_id'], 'distillate_review',
                           json.dumps(review, ensure_ascii=False, sort_keys=True), 'distillate validation'):
        raise Denied('REVIEW_FAILED', hint='The review could not be stored; retry validate.')
    return _finish(svc, source_row['id'],
                   'VALIDATED' if review['verdict'] == 'pass' else 'REVIEW_FAILED', 'validate')


def _accept(svc, source=None, distillate=None, by=None, note=None):
    """§12.7: acceptance needs a passing review of this exact version."""
    from .claim_entry import _lite_only
    Denied = _denied()
    _lite_only(svc)
    svc.scope()
    svc.ready()
    by = 'agent' if by is None else by
    if by not in ('agent', 'user'):
        raise Denied('INVALID_FIELD', field='by', hint="'by' is agent (default) or user.")
    with svc.store.read() as c:
        source_row, root_id = _resolve_target(c, source, distillate)
        _refuse_archived(c, source_row)
        source_text = bytes(source_row['raw']).decode('utf8', 'replace')
        versions = _versions(c, root_id)
        current = versions[-1]
        _require_current(distillate, current)
        props = _properties(c, current['claim_id'])
        if _fail_count(c, root_id) >= 3:
            raise Denied('DISTILLATE_LOOP',
                         hint='Three failed reviews for this logical claim; the chain is stopped. Ask the user for a new instruction.')
        review = props.get('distillate_review')
        if not isinstance(review, dict):
            raise Denied('REVIEW_REQUIRED',
                         hint='Acceptance needs a passing validate on this exact version; call validate first.')
        if review.get('verdict') != 'pass':
            raise Denied('REVIEW_FAILED',
                         hint='The latest review of this version failed; fix the text or anchors and propose again.')
        if not _review_pass(current, review, props.get('basis_spans'), source_text):
            raise Denied('REVIEW_STALE',
                         hint='The review no longer matches the text or anchors; run validate again.')
        if _open_conflicts(c, source_row['id']) or _open_conflicts(c, current['claim_id']):
            raise Denied('DISTILLATE_DISPUTED',
                         hint='An open dispute blocks acceptance; resolve it first.')
        review_digest = _review_digest(review)
        existing = props.get('distillate_accept')
        if isinstance(existing, dict) and existing.get('candidate_digest') == current['raw_sha256'] \
                and existing.get('review_digest') == review_digest \
                and existing.get('accepted_by') == by:
            return _finish(svc, source_row['id'], 'ALREADY_RECORDED', 'accept')
        accepted = {'v': 1, 'candidate_digest': current['raw_sha256'], 'review_digest': review_digest,
                    'accepted_by': by, 'note': note if type(note) is str else ''}
    if not _write_property(svc, current['claim_id'], 'distillate_accept',
                           json.dumps(accepted, ensure_ascii=False, sort_keys=True), 'distillate acceptance'):
        raise Denied('REVIEW_FAILED', hint='The acceptance could not be stored; retry accept.')
    return _finish(svc, source_row['id'], 'ACCEPTED', 'accept')


def _withdraw(svc, source=None, distillate=None, note=None):
    """§12.8: withdraw the current version; the effective version falls back."""
    from .claim_entry import _lite_only
    Denied = _denied()
    _lite_only(svc)
    svc.scope()
    svc.ready()
    if type(note) is not str or not note.strip():
        raise Denied('INVALID_FIELD', field='note', hint="'note' is required: say why the version is withdrawn.")
    with svc.store.read() as c:
        source_row, root_id = _resolve_target(c, source, distillate)
        _refuse_archived(c, source_row)
        versions = _versions(c, root_id)
        current = versions[-1]
        _require_current(distillate, current)
        if len(versions) == 1:
            raise Denied('INVALID_FIELD', field='action',
                         hint='The extracted v1 cannot be withdrawn; propose a corrected version or dispute the source.')
        existing = _properties(c, current['claim_id']).get('distillate_withdraw')
        if isinstance(existing, dict) and existing.get('note') == note:
            return _finish(svc, source_row['id'], 'ALREADY_RECORDED', 'withdraw')
    value = json.dumps({'v': 1, 'note': note}, ensure_ascii=False, sort_keys=True)
    _write_property(svc, current['claim_id'], 'distillate_withdraw', value, 'distillate withdraw')
    return _finish(svc, source_row['id'], 'WITHDRAWN', 'withdraw')


def _target_claim(c, source_row, distillate, field):
    Denied = _denied()
    if distillate is None:
        return source_row['id']
    if type(distillate) is not str or not distillate.strip():
        raise Denied('INVALID_FIELD', field=field)
    row = c.execute('SELECT id FROM claims WHERE id=?', (distillate,)).fetchone()
    if row is None:
        raise Denied('STATEMENT_NOT_FOUND', field=field)
    return distillate


def _dispute(svc, source=None, distillate=None, note=None):
    """§12.8: a conflict on the source (only source given) or on a version."""
    from . import claim_entry as ce
    from .claim_entry import _lite_only
    Denied = _denied()
    _lite_only(svc)
    svc.scope()
    svc.ready()
    if type(note) is not str or not note.strip():
        raise Denied('INVALID_FIELD', field='note', hint="'note' is required: say why the statement or version is disputed.")
    with svc.store.read() as c:
        source_row, root_id = _resolve_target(c, source, distillate)
        target = _target_claim(c, source_row, distillate, 'distillate')
        ref = 'stmt:%s' % source_row['id']
        if any(x['conflict_ref'] == ref for x in _open_conflicts(c, target)):
            return _finish(svc, source_row['id'], 'ALREADY_RECORDED', 'dispute')
    ce.annotate(svc, target, 'conflict', {'conflict_ref': ref, 'reason': note})
    return _finish(svc, source_row['id'], 'DISPUTED', 'dispute')


def _resolve(svc, source=None, distillate=None, note=None):
    """§12.8: close every open conflict on the target; never edits a text."""
    from . import claim_entry as ce
    from .claim_entry import _lite_only
    Denied = _denied()
    _lite_only(svc)
    svc.scope()
    svc.ready()
    if type(note) is not str or not note.strip():
        raise Denied('INVALID_FIELD', field='note', hint="'note' is required: say why the dispute is closed.")
    with svc.store.read() as c:
        source_row, root_id = _resolve_target(c, source, distillate)
        target = _target_claim(c, source_row, distillate, 'distillate')
        open_refs = [x['conflict_ref'] for x in _open_conflicts(c, target)]
    if not open_refs:
        return _finish(svc, source_row['id'], 'ALREADY_RECORDED', 'resolve')
    for ref in open_refs:
        ce.annotate(svc, target, 'resolve', {'conflict_ref': ref, 'reason': note})
    return _finish(svc, source_row['id'], 'RESOLVED', 'resolve')


def distillate(svc, action, source=None, distillate=None, text=None, anchors=None, by=None, note=None):
    """The dom_distillate tool (§12.5).  Show is read-only; every write action
    is lite-only and goes through the same scope/ready gates."""
    Denied = _denied()
    if type(action) is not str or action not in DISTILLATE_ACTIONS:
        raise Denied('INVALID_FIELD', field='action',
                     hint="'action' is one of: %s." % ', '.join(DISTILLATE_ACTIONS))
    if action == 'show':
        return _show(svc, source, distillate)
    if action == 'propose':
        return _propose(svc, source, distillate, text, anchors)
    if action == 'validate':
        return _validate(svc, source, distillate)
    if action == 'accept':
        return _accept(svc, source, distillate, by, note)
    if action == 'withdraw':
        return _withdraw(svc, source, distillate, note)
    if action == 'dispute':
        return _dispute(svc, source, distillate, note)
    return _resolve(svc, source, distillate, note)
