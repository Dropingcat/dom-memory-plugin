"""BM25 text retrieval for the skill router. Pure stdlib, no model, no I/O.

Character 3-5-gram tokenization handles RU inflection without a stemmer:
``миграция`` / ``миграции`` / ``миграцию`` share the ``мигр``/``игр``/``гра`` grams.
The same tokenizer keeps code identifiers usable (``check_skill_index`` survives,
``.py`` becomes a separate token).

Each skill is indexed as **four weighted fields** so one strong signal is not drowned
by a long body:

    name (2.0) > tags (1.5) > description (1.0) > body (0.3)

The index is built once per process (``build_corpus``) and queried with
``search``/``score``.  Both are pure functions of their arguments: the same corpus and
the same query produce the same floats, so a queue or a rendered block can be hashed.

The index is derived data.  It never enters the ledger and carries no ``provides``:
a lexical hit is evidence that a skill *mentions* a topic, never that it provides a
capability.  Admission stays the exact capability join's job.
"""
from __future__ import annotations

import math
import re
from collections import Counter

# Default BM25 parameters (the analysis document's k1=1.5 is a tuning variant; the
# router contract fixes 1.2/0.75 here so the offline calibrator and the runtime agree).
K1 = 1.2
B = 0.75

# Fields, in scoring order.  The order is part of the contract: field scores are
# reported in this order and the weighted sum is taken in this order.
FIELD_NAMES: tuple[str, ...] = ('name', 'tags', 'description', 'body')

# Field weights for multi-field BM25.  A skill name is the strongest signal; the body
# is the weakest, because it repeats the description and contains examples.
FIELD_WEIGHTS: dict[str, float] = {
    'name': 2.0,
    'tags': 1.5,
    'description': 1.0,
    'body': 0.3,
}

# Everything that is not a letter, digit, underscore or whitespace becomes a separator.
# ``\w`` is Unicode-aware for ``str`` patterns, so Cyrillic survives untouched.
_SEPARATOR_RE = re.compile(r'[^\w\s]+', re.UNICODE)


# ---------------------------------------------------------------------------
# tokenization
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace.

    Keeps letters, digits and underscores, so technical identifiers stay whole:
    ``"Check_Skill_Index.py!"`` -> ``"check_skill_index py"``.
    Russian punctuation is removed the same way: ``"Миграция схемы!"`` ->
    ``"миграция схемы"``.
    """
    if text is None:
        return ''
    lowered = str(text).lower()
    spaced = _SEPARATOR_RE.sub(' ', lowered)
    return ' '.join(spaced.split())


def char_ngrams(text: str, n_min: int = 3, n_max: int = 5) -> list[str]:
    """Overlapping character n-grams of every token, ``n_min``..``n_max`` inclusive.

    Multiplicity is preserved (a repeated token yields repeated grams) because BM25
    needs true term frequency.  Query callers dedupe with ``dict.fromkeys``.

    A token shorter than ``n_min`` contributes nothing: 1-2 character words carry no
    retrieval signal and would otherwise match everywhere.

    Examples::

        char_ngrams('migrate') == [
            'mig', 'igr', 'gra', 'rat', 'ate',        # 3-grams
            'migr', 'igra', 'grat', 'rate',           # 4-grams
            'migra', 'igrat', 'grate',                # 5-grams
        ]
    """
    if n_min < 1 or n_max < n_min:
        raise ValueError('need 1 <= n_min <= n_max, got %r..%r' % (n_min, n_max))
    grams: list[str] = []
    for token in normalize(text).split():
        for size in range(n_min, n_max + 1):
            if len(token) < size:
                break
            grams.extend(token[index:index + size] for index in range(len(token) - size + 1))
    return grams


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------

def _read(item, key: str):
    """Read a field from a SkillRecord-like object or from a plain dict."""
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _as_text(value) -> str:
    """Join list-like fields (tags, examples) into one string; None becomes ''."""
    if value is None:
        return ''
    if isinstance(value, (list, tuple, set)):
        return ' '.join(str(part) for part in value if str(part).strip())
    return str(value)


def _skill_id(item) -> str:
    for key in ('skill_id', 'id', 'name'):
        value = _read(item, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def _field_text(item, field: str) -> str:
    """Raw text of one field, normalized.

    ``SkillRecord`` stores no ``body``.  The fallback composes one from ``usage_hint``
    and ``examples`` -- the only body-like text the parser keeps -- instead of
    inventing content.  A dict may carry ``body`` directly.
    """
    raw = _read(item, field)
    if field == 'body' and not raw:
        raw = ' '.join([_as_text(_read(item, 'usage_hint')),
                        _as_text(_read(item, 'examples'))])
    if field == 'name' and not str(raw or '').strip():
        raw = _skill_id(item)
    return normalize(_as_text(raw))


def build_corpus(skills: list) -> dict:
    """Build the BM25 index from SkillRecord-like objects (or dicts).

    Each item needs ``skill_id`` (or ``id``/``name``), ``name``, ``tags``,
    ``description`` and optionally ``body``; a missing field is an empty field.

    A later item with the same ``skill_id`` overrides an earlier one, mirroring the
    loader's root precedence.

    Returns::

        {
          'N': int,
          'avgdl': float,                 # mean total token count per skill
          'field_weights': {field: weight},
          'k1': float, 'b': float,
          'skill_ids': tuple[str, ...],   # index order = first appearance
          'doc_len': {skill_id: int},     # total grams across all four fields
          'field_len': {skill_id: {field: int}},
          'field_avgdl': {field: float},
          'term_idf': {field: {term: float}},
          'field_terms': {skill_id: {field: {term: tf}}},
        }
    """
    ordered: dict[str, object] = {}
    for item in skills or ():
        skill_id = _skill_id(item)
        if not skill_id:
            continue
        ordered[skill_id] = item  # a later declaration wins, as in skill_index.collect
    skill_ids = tuple(ordered)

    field_terms: dict[str, dict[str, Counter]] = {}
    field_len: dict[str, dict[str, int]] = {}
    doc_len: dict[str, int] = {}
    for skill_id, item in ordered.items():
        per_field: dict[str, Counter] = {}
        lengths: dict[str, int] = {}
        for field in FIELD_NAMES:
            counts = Counter(char_ngrams(_field_text(item, field)))
            per_field[field] = counts
            lengths[field] = sum(counts.values())
        field_terms[skill_id] = per_field
        field_len[skill_id] = lengths
        doc_len[skill_id] = sum(lengths.values())

    total = len(skill_ids)
    field_avgdl = {
        field: (sum(lengths[field] for lengths in field_len.values()) / total if total else 0.0)
        for field in FIELD_NAMES
    }
    term_idf: dict[str, dict[str, float]] = {field: {} for field in FIELD_NAMES}
    for field in FIELD_NAMES:
        document_frequency: Counter = Counter()
        for per_field in field_terms.values():
            document_frequency.update(per_field[field].keys())
        term_idf[field] = {
            term: math.log(1.0 + (total - df + 0.5) / (df + 0.5))
            for term, df in document_frequency.items()
        }

    return {
        'N': total,
        'avgdl': (sum(doc_len.values()) / total if total else 0.0),
        'field_weights': dict(FIELD_WEIGHTS),
        'k1': K1,
        'b': B,
        'skill_ids': skill_ids,
        'doc_len': doc_len,
        'field_len': field_len,
        'field_avgdl': field_avgdl,
        'term_idf': term_idf,
        'field_terms': field_terms,
    }


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def _query_terms(query_terms) -> list[str]:
    """Dedupe query grams; a raw string is accepted and tokenized for convenience."""
    if isinstance(query_terms, str):
        query_terms = char_ngrams(query_terms)
    return list(dict.fromkeys(str(term) for term in (query_terms or ()) if str(term)))


def score(corpus: dict, query_terms: list[str], skill_id: str) -> dict:
    """BM25 score of one skill against the query, per field plus the weighted total.

    Returns ``{field: score, ..., 'total': weighted_sum}`` with all four field keys
    present (0.0 when unmatched).  Field scores are rounded to 6 decimals; ``total``
    is the weighted sum of the *unrounded* field scores and is rounded once, so
    ``total`` is the authoritative ranking value.
    """
    result = {field: 0.0 for field in FIELD_NAMES}
    result['total'] = 0.0
    if not corpus or not corpus.get('N'):
        return result
    counted = corpus.get('field_terms', {}).get(str(skill_id))
    terms = _query_terms(query_terms)
    if not counted or not terms:
        return result

    k1 = corpus.get('k1', K1)
    b = corpus.get('b', B)
    weights = corpus.get('field_weights') or FIELD_WEIGHTS
    lengths = corpus.get('field_len', {}).get(str(skill_id)) or {}
    averages = corpus.get('field_avgdl') or {}
    term_idf = corpus.get('term_idf') or {}

    total = 0.0
    for field in FIELD_NAMES:
        frequencies = counted.get(field) or {}
        idf = term_idf.get(field) or {}
        length = lengths.get(field, 0)
        average = averages.get(field) or 0.0
        # Length normalization for THIS field: a long body is penalised without
        # shrinking a short, precise name.
        norm = 1.0 - b + (b * length / average if average else 0.0)
        subtotal = 0.0
        for term in terms:
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            subtotal += idf.get(term, 0.0) * (
                frequency * (k1 + 1.0)) / (frequency + k1 * norm)
        result[field] = round(subtotal, 6)
        total += weights.get(field, 0.0) * subtotal
    result['total'] = round(total, 6)
    return result


def search(corpus: dict, query_text: str, top_k: int = 15) -> list[dict]:
    """Rank skills by the weighted total; return at most ``top_k`` positive hits.

    A zero score is *no lexical evidence* and is omitted, so ``[]`` is the honest
    answer for an out-of-vocabulary query (the router renders that as a gap instead
    of a decoy).  Ties break on ``skill_id`` so the order is deterministic.

    Each result: ``{skill_id, total_score, field_scores: {name, tags, description,
    body}}``.
    """
    if not corpus or not corpus.get('N') or top_k is None or int(top_k) <= 0:
        return []
    terms = _query_terms(query_text)
    if not terms:
        return []

    results = []
    for skill_id in corpus.get('skill_ids') or ():
        row = score(corpus, terms, skill_id)
        if row['total'] <= 0.0:
            continue
        results.append({
            'skill_id': skill_id,
            'total_score': row['total'],
            'field_scores': {field: row[field] for field in FIELD_NAMES},
        })
    results.sort(key=lambda item: (-item['total_score'], item['skill_id']))
    return results[:int(top_k)]


__all__ = [
    'B',
    'FIELD_NAMES',
    'FIELD_WEIGHTS',
    'K1',
    'build_corpus',
    'char_ngrams',
    'normalize',
    'score',
    'search',
]
