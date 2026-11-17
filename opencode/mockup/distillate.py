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
    if (strong or decision) and not _noun_hit(sentence, nouns) \
            and len(strong) + len(decision) < 2 and not explicit:
        return 0, explicit                    # lone unsupported marker (§12.3)
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
