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

from .plan_context import _same, _tokens
from .dom_contract import raw_hash

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
    selected = [(t, s, e) for (sc, s, t, e, _)
                in sorted(scored, key=lambda x: (-x[0], x[1]))[:3]]
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
    'session:...' and locator 'prompt:...'."""
    meta = (item or {}).get('metadata') or {}
    return (item or {}).get('type') == 'quote' \
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
