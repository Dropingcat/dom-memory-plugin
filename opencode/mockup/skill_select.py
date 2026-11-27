"""Deterministic skill selection pipeline: one micro-step -> one queue block.

The pipeline is a thin, pure composition of the already-tested stages:

    index (cached in memory) -> BM25 corpus -> multi-marker hint -> consensus
    -> group filter (two-level) -> deep analysis -> positional render

Nothing here writes to the ledger and nothing here re-implements a stage: every
rule (queue >= 3 independent signals, mentions never become provides, the 800-char
block budget) lives in the stage module that owns it.  The same step, skills and
cards therefore always produce the same ids and the same block bytes.

``select_step`` is the read-only entry point used by the ``dom_skill_select`` MCP
tool (``mockup/server.py``) and by ``tests/check_skill_pipeline.py``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace as _replace
from pathlib import Path
from typing import Optional

if __package__:  # normal use: ``from mockup import skill_select``
    from . import skill_consensus as _consensus
    from . import skill_deep as _deep
    from . import skill_group as _group
    from . import skill_index as _index
    from . import skill_markers as _markers
    from . import skill_render as _render
    from . import skill_text as _text
    from . import step_registry as _steps
else:  # direct import from opencode/mockup on sys.path
    import skill_consensus as _consensus  # type: ignore
    import skill_deep as _deep  # type: ignore
    import skill_group as _group  # type: ignore
    import skill_index as _index  # type: ignore
    import skill_markers as _markers  # type: ignore
    import skill_render as _render  # type: ignore
    import skill_text as _text  # type: ignore
    import step_registry as _steps  # type: ignore

CARDS_PATH = Path(__file__).with_name('skills.card.json')
MAX_PREFETCH = 3                 # the window is current + up to three follow-ups
FALLBACK_SKILL = 'skill_search'
NO_SIGNAL_GAP = 'no-signal'
STATUS = 'SKILLS_SELECTED'

# BUG-4 (STEP-BOUNDS): a micro-step is a label, not a plan body.  Without these
# bounds one oversized title produced a ~55 KB answer, so the pipeline clips the
# step before any stage sees it.  CONTEXT_LIMIT in step_registry.py is stricter
# (600 chars); its limit wins, and MAX_CONTEXT is the pipeline's own ceiling.
MAX_TITLE = 500
MAX_CONTEXT = 2000
MAX_GAPS = 20                    # gaps point at skill_search; they are not a skill dump

# The index is derived data and expensive to collect (one walk of every skill root),
# so it is cached for the life of the process.  The key is the root tuple, so two
# callers asking for different roots never share an entry.
_SKILLS_CACHE: dict[tuple[str, ...], tuple] = {}
# The BM25 corpus is a pure function of the records, so it is cached by their digest
# and never rebuilt for an unchanged index.
_CORPUS_CACHE: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# small adapters
# ---------------------------------------------------------------------------

def _field(item, name: str, default=None):
    """Read a field from a SkillRecord or a plain dict."""
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        value = (value,)
    try:
        return tuple(str(item).strip() for item in value if str(item).strip())
    except TypeError:
        return (str(value).strip(),)


def _skill_id(item) -> str:
    return str(_field(item, 'skill_id', '') or _field(item, 'id', '') or '').strip()


def _categories(item) -> set[str]:
    return {str(value).strip().lower() for value in _tuple(_field(item, 'categories', ()))
            if str(value).strip()}


# ---------------------------------------------------------------------------
# inputs: skills, cards, index digest
# ---------------------------------------------------------------------------

def collect_skills(roots=None) -> list:
    """Skills from the documented roots, collected once per process per root set."""
    root_list = tuple(Path(root) for root in (roots if roots is not None else _index.SKILL_ROOTS))
    key = tuple(str(root) for root in root_list)
    if key not in _SKILLS_CACHE:
        _SKILLS_CACHE[key] = tuple(_index.SkillIndex().collect(list(root_list)))
    return list(_SKILLS_CACHE[key])


def reset_cache():
    """Forget the in-memory index (tests only; the process cache is otherwise static)."""
    _SKILLS_CACHE.clear()
    _CORPUS_CACHE.clear()


def load_cards(path=None) -> list[dict]:
    """Curated skill cards; a missing or corrupt file is an empty card set."""
    try:
        payload = json.loads(Path(path if path is not None else CARDS_PATH).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return []
    if isinstance(payload, dict):
        rows = payload.get('cards')
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = None
    return [dict(row) for row in (rows or ()) if isinstance(row, dict)]


def index_digest_for(skills) -> str:
    """Canonical sha256 of the skill index: identity + content digest + card data.

    Card-only skills have no SKILL.md and therefore no ``content_digest``, so the
    curated card fields (``verified_provides``, ``card_only``) are part of the row.
    Without them, editing ``skills.card.json`` would leave ``indexDigest`` unchanged
    and the tau-gate would keep serving a block built from the old cards.
    """
    rows = []
    for skill in skills or ():
        sid = _skill_id(skill)
        if not sid:
            continue
        provides = sorted(_tuple(_field(skill, 'verified_provides', ())))
        rows.append([sid, str(_field(skill, 'content_digest', '') or ''), provides,
                     bool(_field(skill, 'card_only', False))])
    rows.sort()
    encoded = json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _bounded_step(step):
    """Clip an already-built MicroStep to the BUG-4 budgets (frozen dataclass -> replace)."""
    title = str(step.title or '')[:MAX_TITLE]
    context = str(step.context or '')[:MAX_CONTEXT]
    if title == step.title and context == step.context:
        return step
    return _replace(step, title=title, context=context)


def micro_step(step, plan_version: int = 0):
    """Normalize a plain dict (or return an existing MicroStep) without inventing fields.

    Explicit ``category_hint`` / ``markers`` win.  When the hint is absent it is derived
    by ``skill_markers.multi_marker_hint``, which itself needs at least two independent
    signals, and the derived action markers are added as ``action:*``.
    """
    if isinstance(step, _steps.MicroStep):
        step = _bounded_step(step)
        return step
    if not isinstance(step, dict):
        raise TypeError('step must be a MicroStep or a dict, got %r' % (type(step).__name__,))

    title = str(step.get('title') or '')[:MAX_TITLE]
    context = str(step.get('context') or '')[:_steps.CONTEXT_LIMIT][:MAX_CONTEXT]
    cap = _tuple(step.get('cap') if step.get('cap') is not None else step.get('capabilities'))
    claims = _tuple(step.get('claims'))
    markers = _tuple(step.get('markers'))
    category_hint = str(step.get('category_hint') or '').strip()

    if not category_hint:
        hint, action_markers, _confidence = _markers.multi_marker_hint(
            title, context, list(claims), list(cap))
        category_hint = hint
        if not markers and action_markers:
            markers = tuple('action:' + marker for marker in action_markers)

    declared = step.get('plan_version', 0)
    try:
        version = int(plan_version or declared or 0)
    except (TypeError, ValueError):
        version = 0

    return _steps.MicroStep(
        step_id=str(step.get('step_id') or step.get('id') or step.get('path') or 'current'),
        title=title,
        parent_plan_task_id=str(step.get('parent_plan_task_id') or step.get('parent_id') or ''),
        plan_version=max(0, version),
        cap=cap,
        category_hint=category_hint,
        markers=markers,
        context=context,
    )


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------

def _verdicts_for(step_obj, skills, cards, corpus) -> tuple[dict, list]:
    """Consensus verdicts for the skills that belong to the step's selected groups."""
    consensus = _consensus.ConsensusFilter(
        bm25_search_fn=lambda query, top_k: _text.search(corpus, query, top_k))
    selector = _group.GroupSelector(_markers.CATEGORIES)
    # Exploration is disabled: a queue must be a pure function of the step, not of
    # how many times this process has routed before.
    groups = selector.select_groups(step_obj.category_hint, step_obj.cap,
                                    step_obj.markers, explore=False)
    names = {str(group[0]).strip().lower() for group in groups if group}
    inside = [skill for skill in skills if names & _categories(skill)] if names else []
    return consensus.filter_batch(inside, step_obj, cards), groups


def _analysis_for(skill, step_obj, verdict):
    return _deep.analyze(skill, step_obj.title, step_obj.context, verdict, step_obj.cap)


def select_step(step, queue=None, plan_version: int = 0, index_digest: str = '',
                skills=None, cards=None, corpus=None) -> dict:
    """Run the whole pipeline for one step.

    ``step``        MicroStep or dict (title, context, cap[], markers, category_hint,
                    claims[], step_id, plan_version).
    ``queue``       next 0..3 steps (same shape) used only for prefetch.
    ``plan_version``/``index_digest``  tau-gate invalidation keys.
    ``skills``/``cards``/``corpus``    explicit inputs (tests, callers with a cache);
                    omitted skills are collected once per process and cached.

    Returns a JSON-safe dict: the rendered ``block`` under the 800-char budget plus the
    machine-readable ids the caller needs.  Read-only: it never touches the ledger.
    """
    step_obj = micro_step(step, plan_version)
    skill_list = list(skills) if skills is not None else collect_skills()
    card_list = list(cards) if cards is not None else load_cards()
    computed_digest = index_digest_for(skill_list)
    digest = str(index_digest or computed_digest)
    if corpus is None:
        if computed_digest not in _CORPUS_CACHE:
            _CORPUS_CACHE[computed_digest] = _text.build_corpus(skill_list)
        corpus = _CORPUS_CACHE[computed_digest]

    verdicts, groups = _verdicts_for(step_obj, skill_list, card_list, corpus)
    queue_verdicts = list(verdicts['queue'])
    advisory = [verdict.skill_id for verdict in verdicts['advisory']]
    queue_ids = [verdict.skill_id for verdict in queue_verdicts]

    kept = set(queue_ids) | set(advisory)
    gaps = sorted({_skill_id(skill) for skill in skill_list} - kept)
    # BUG-4: never return the whole skill index as a gap dump.  The tail is a
    # pointer, so the caller still sees that the list was cut and can search.
    if len(gaps) > MAX_GAPS:
        gaps = gaps[:MAX_GAPS] + ['... and %d more' % (len(gaps) - MAX_GAPS)]

    by_id = {}
    for skill in skill_list:
        sid = _skill_id(skill)
        if sid and sid not in by_id:
            by_id[sid] = skill

    # A requirement is covered only by a curated ``verified_provides`` value.  Mentions
    # are evidence that a skill talks about a topic, never that it supplies the capability.
    covered = set()
    for sid in queue_ids:
        record = by_id.get(sid)
        covered.update(str(cap).strip().lower() for cap in _tuple(_field(record, 'verified_provides', ())))
    missing_caps = [str(cap) for cap in step_obj.cap if str(cap).strip().lower() not in covered]

    # Prefetch: the top queue entry of each following step (ids only in the result).
    prefetch_entries: list[tuple[object, Optional[object]]] = []
    prefetch_ids: list[str] = []
    for raw in list(queue or ())[:MAX_PREFETCH]:
        next_step = micro_step(raw, plan_version)
        next_verdicts, _groups = _verdicts_for(next_step, skill_list, card_list, corpus)
        if next_verdicts['queue']:
            verdict = next_verdicts['queue'][0]
            record = by_id.get(verdict.skill_id)
            analysis = _analysis_for(record, next_step, verdict) if record is not None else None
            prefetch_entries.append((next_step, analysis))
            prefetch_ids.append(verdict.skill_id)
        else:
            prefetch_entries.append((next_step, None))

    if queue_verdicts:
        analyses = _deep.analyze_batch(skill_list, step_obj, queue_verdicts, step_obj.cap)
        current_analysis = analyses[0]
    else:
        current_analysis = _deep.SkillAnalysis(
            skill_id=FALLBACK_SKILL,
            what='no skill selected',
            why='no consensus queue for this step',
            how='use skill_search with the step context',
        )

    # The gap line is never dropped: a real missing capability is named, and an empty
    # queue with no declared capability still says so instead of rendering "gaps: none".
    block_gaps = list(missing_caps)
    if not queue_ids and not block_gaps:
        block_gaps = [NO_SIGNAL_GAP]

    fingerprint = _steps.BlockFingerprint.from_step(
        step_obj, digest, plan_version=step_obj.plan_version).digest()
    block = _render.render_block(step_obj, current_analysis, prefetch_entries, [], [],
                                 block_gaps, fingerprint, step_cap=list(step_obj.cap))

    return {
        'status': STATUS,
        'step_id': step_obj.step_id,
        'plan_version': int(step_obj.plan_version),
        'index_digest': digest,
        'fingerprint': fingerprint,
        'block': block.text,
        'queue': queue_ids,
        'advisory': advisory,
        'gaps': gaps,
        'prefetch': prefetch_ids,
        'missing_caps': missing_caps,
        'groups': [str(group[0]) for group in groups],
        'confidence_bp': int(queue_verdicts[0].confidence_bp) if queue_verdicts else 0,
        'signals': {verdict.skill_id: list(verdict.signals) for verdict in queue_verdicts},
    }


# The architecture document names the stable entry point ``select_pipeline``.
select_pipeline = select_step


__all__ = [
    'CARDS_PATH',
    'FALLBACK_SKILL',
    'MAX_CONTEXT',
    'MAX_GAPS',
    'MAX_PREFETCH',
    'MAX_TITLE',
    'NO_SIGNAL_GAP',
    'STATUS',
    'collect_skills',
    'index_digest_for',
    'load_cards',
    'micro_step',
    'reset_cache',
    'select_pipeline',
    'select_step',
]
