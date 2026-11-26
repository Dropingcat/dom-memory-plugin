"""
Multi-marker classifier for skill router.
Uses >=3 independent signals to derive category_hint and markers.
Single marker is NEVER sufficient (H1/C3 anti-pattern).

The classifier is pure: no DB, no I/O, no model. The same input always produces the
same output, so a queue built from it can be hashed and compared byte-for-byte.
"""
from __future__ import annotations

# Domain keywords -> category mapping (closed, curated)
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    'databases': ('sqlite', 'migration', 'schema', 'table', 'query', 'index', 'column',
                  'база', 'таблиц', 'схем', 'миграц'),
    'chemistry': ('molecule', 'smiles', 'drug', 'compound', 'admet', 'toxicity',
                  'binding', 'молекул', 'хими'),
    'physics': ('pde', 'simulation', 'differential', 'equation', 'fluid', 'wave',
                'физик', 'уравнен'),
    'coding': ('test', 'code', 'debug', 'implement', 'refactor', 'function',
               'програм', 'код', 'функц'),
    'writing': ('document', 'article', 'report', 'spec', 'describe', 'readme',
                'документ', 'статья', 'отчет'),
    'research': ('paper', 'literature', 'review', 'survey', 'methodology',
                 'исследова', 'статья', 'метод'),
    'data-engineering': ('pipeline', 'etl', 'data', 'batch', 'processing',
                         'данн', 'обработ'),
    'visualization': ('plot', 'chart', 'graph', 'figure', 'visual',
                      'график', 'визуализ'),
    'problem-solving': ('triz', 'ariz', 'contradiction', 'solution', 'principle',
                        'решен', 'противореч'),
    'review': ('audit', 'review', 'critique', 'check', 'inspect',
               'аудит', 'ревью', 'провер'),
}

# Action type markers (from verbs and nouns)
ACTION_MARKERS: dict[str, tuple[str, ...]] = {
    'inspect': ('read', 'find', 'check', 'look', 'show', 'list', 'describe',
                'прочита', 'найди', 'провер', 'покаж'),
    'modify': ('add', 'change', 'create', 'write', 'remove', 'migrate', 'update', 'implement',
               'добав', 'измен', 'созда', 'напиш', 'напис', 'удали', 'мигрир'),
    'verify': ('test', 'run', 'validate', 'assert', 'benchmark', 'measure',
               'прогон', 'тест', 'провер', 'валидир'),
    'document': ('document', 'describe', 'explain', 'write-docs', 'spec',
                 'опиши', 'документируй', 'поясни'),
    'decide': ('choose', 'design', 'compare', 'decide', 'evaluate',
               'выбер', 'спроектируй', 'сравн', 'реши'),
}

# Capability -> category mapping. Covers every capability in capabilities.json; a
# capability that is not here simply casts no category vote.
CAPABILITY_CATEGORY: dict[str, str] = {
    'testing': 'coding',
    'sqlite': 'databases',
    'migration': 'databases',
    'ledger': 'databases',
    'docs': 'writing',
    'ops': 'coding',
    'runtime': 'coding',
    'security': 'review',
    'router': 'coding',
    'prompt': 'writing',
    'writer': 'writing',
    'review': 'review',
    'git': 'coding',
    'panel': 'visualization',
}

# Only unambiguous action -> category affinities. inspect/modify/decide are neutral:
# "add a column" may be coding or a database migration, so the verb must not decide.
ACTION_CATEGORY: dict[str, str] = {
    'document': 'writing',
    'verify': 'coding',
}

# The five independent signal sources. Confidence is matched / total.
SIGNAL_NAMES: tuple[str, ...] = ('title', 'context', 'claims', 'capabilities', 'action')

# Exported constants for tests and other modules
CATEGORIES = tuple(sorted(CATEGORY_KEYWORDS.keys()))
ACTION_TYPES = tuple(sorted(ACTION_MARKERS.keys()))


def _normalize(value) -> str:
    """Lowercase and collapse whitespace so matching is stable across formatting."""
    return ' '.join(str(value or '').lower().split())


def _keyword_hits(text: str) -> dict[str, str]:
    """Categories present in text, mapped to the first keyword that matched."""
    hits: dict[str, str] = {}
    for category in CATEGORIES:
        for word in CATEGORY_KEYWORDS[category]:
            if word in text:
                hits[category] = word
                break
    return hits


def action_markers_from_title(title: str) -> list[str]:
    """Extract action markers from step title using ACTION_MARKERS (sorted, deduped)."""
    text = _normalize(title)
    found: list[str] = []
    for action in ACTION_TYPES:
        for word in ACTION_MARKERS[action]:
            if word in text:
                found.append(action)
                break
    return found


def multi_marker_hint(
    title: str,
    context: str,
    claims: list[str],
    capabilities: list[str],
) -> tuple[str, list[str], float]:
    """
    Derive (category_hint, markers, confidence) from multiple signals.

    Signals (each contributes 1 vote):
    1. Keyword match: title/context/claims -> CATEGORY_KEYWORDS
    2. Capability -> category mapping (CAPABILITY_CATEGORY)
    3. Action markers from title verb/noun

    A category is returned only when at least two independent sources vote for it and
    no other category ties it. One word, one verb or one capability is therefore never
    enough: the explicit unknown is ''.

    Returns:
    - category_hint: str -- best category or '' when the evidence is insufficient
    - markers: list[str] -- matched action markers
    - confidence: 0.0-1.0 -- fraction of matched signals out of the five sources
    """
    signals: dict[str, bool] = {}
    votes: dict[str, list[str]] = {}

    def vote(category: str, source: str) -> None:
        votes.setdefault(category, []).append(source)

    text_fields = (
        ('title', _normalize(title)),
        ('context', _normalize(context)),
        ('claims', _normalize(' '.join(str(item) for item in (claims or [])))),
    )
    for source, text in text_fields:
        hits = _keyword_hits(text)
        if hits:
            signals[source] = True
            for category in sorted(hits):
                vote(category, source)

    known_caps = [str(cap) for cap in (capabilities or []) if str(cap) in CAPABILITY_CATEGORY]
    cap_categories = sorted({CAPABILITY_CATEGORY[cap] for cap in known_caps})
    if cap_categories:
        signals['capabilities'] = True
        for category in cap_categories:
            vote(category, 'capabilities')

    markers = action_markers_from_title(title)
    if markers:
        signals['action'] = True
        for action in markers:
            category = ACTION_CATEGORY.get(action)
            if category:
                vote(category, 'action')

    # Deterministic ranking: most independent sources first, then category name.
    ranked = sorted(votes.items(), key=lambda item: (-len(item[1]), item[0]))
    category_hint = ''
    if ranked:
        top_category, top_sources = ranked[0]
        runner_up = len(ranked[1][1]) if len(ranked) > 1 else 0
        if len(top_sources) >= 2 and len(top_sources) > runner_up:
            category_hint = top_category

    confidence = round(len(signals) / len(SIGNAL_NAMES), 3)
    return category_hint, markers, confidence
