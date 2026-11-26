"""Multi-marker consensus for skill selection.

A skill is routed only when several *independent* signals agree.  The five signals are:

1. ``group``  -- the step's ``category_hint`` names one of the skill's categories
2. ``bm25``   -- the skill is in the BM25 top list for the step text (optional)
3. ``marker`` -- the step's action markers overlap the skill's own action markers
4. ``cap``    -- a step capability is covered by ``verified_provides`` or ``mentions``
5. ``card``   -- the skill has a curated card whose state is ``verified``

No single signal can force queue mode: queue needs >= 3 signals, two signals are
advisory, everything else is a gap.  Confidence is expressed in integer basis points
(0..10000) because floats would make an otherwise identical queue hash differently.

The filter is pure: no DB, no I/O, no model.  Same inputs -> same verdicts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

if __package__:  # normal use: ``from mockup import skill_consensus``
    from . import skill_markers as _skill_markers
else:  # direct import from opencode/ on sys.path
    import skill_markers as _skill_markers  # type: ignore

# Signal identity and weight.  The weights sum to 10000, so a verdict that matched every
# signal reports full confidence; a three-signal queue is 5000..7500 depending on which.
SIGNAL_NAMES: tuple[str, ...] = ('group', 'bm25', 'marker', 'cap', 'card')
SIGNAL_WEIGHTS: dict[str, int] = {
    'group': 2500,
    'bm25': 2000,
    'marker': 2000,
    'cap': 2500,
    'card': 1000,
}

QUEUE_MIN_SIGNALS = 3
ADVISORY_SIGNALS = 2
MAX_CONFIDENCE_BP = 10000
BM25_TOP_K = 15


def verdict_mode(signal_count: int) -> str:
    """Map a signal count to the queue mode.  The only place the threshold lives."""
    if signal_count >= QUEUE_MIN_SIGNALS:
        return 'queue'
    if signal_count == ADVISORY_SIGNALS:
        return 'advisory'
    return 'gap'


@dataclass(frozen=True)
class ConsensusVerdict:
    """One skill's consensus state for one step.  Frozen: a verdict cannot be re-tuned."""

    skill_id: str
    queue_mode: str           # 'queue' | 'advisory' | 'gap'
    signals: tuple[str, ...]  # which signals matched: {'group', 'bm25', 'marker', 'cap', 'card'}
    signal_count: int         # len(signals)
    confidence_bp: int        # 0-10000, integer only
    group_match: bool
    bm25_in_top: bool
    marker_overlap: bool
    cap_provided: bool
    card_verified: bool

    def __post_init__(self):
        object.__setattr__(self, 'skill_id', str(self.skill_id))
        signals = tuple(sorted({str(signal) for signal in (self.signals or ())}))
        unknown = [signal for signal in signals if signal not in SIGNAL_WEIGHTS]
        if unknown:
            raise ValueError('unknown consensus signal(s): %r' % (unknown,))
        object.__setattr__(self, 'signals', signals)
        object.__setattr__(self, 'signal_count', len(signals))
        # The mode is derived, never declared: a hand-built verdict cannot claim 'queue'
        # on one signal, because the invariant is checked here as well as in evaluate().
        if self.queue_mode != verdict_mode(len(signals)):
            raise ValueError('queue_mode %r does not match %d signal(s)'
                             % (self.queue_mode, len(signals)))
        if isinstance(self.confidence_bp, bool) or not isinstance(self.confidence_bp, int):
            if isinstance(self.confidence_bp, float) and self.confidence_bp.is_integer():
                object.__setattr__(self, 'confidence_bp', int(self.confidence_bp))
            else:
                raise ValueError('confidence_bp must be an integer, got %r' % (self.confidence_bp,))
        if not 0 <= self.confidence_bp <= MAX_CONFIDENCE_BP:
            raise ValueError('confidence_bp out of range: %r' % (self.confidence_bp,))
        for flag in ('group_match', 'bm25_in_top', 'marker_overlap', 'cap_provided', 'card_verified'):
            object.__setattr__(self, flag, bool(getattr(self, flag)))


def confidence_bp_for(signals) -> int:
    """Integer confidence for a signal set: sum of the matched signal weights."""
    total = sum(SIGNAL_WEIGHTS[signal] for signal in set(signals or ()))
    return max(0, min(MAX_CONFIDENCE_BP, int(total)))


# ---------------------------------------------------------------------------
# skill/step adapters (duck-typed: SkillRecord, dict or a read-only row)
# ---------------------------------------------------------------------------

def _field(skill, name: str, default):
    if isinstance(skill, dict):
        return skill.get(name, default)
    return getattr(skill, name, default)


def _text(value) -> str:
    if isinstance(value, (list, tuple, set)):
        value = ' '.join(str(item) for item in value)
    return ' '.join(str(value or '').lower().split())


def _tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        value = (value,)
    try:
        return tuple(str(item) for item in value if str(item).strip())
    except TypeError:
        return (str(value),)


def _skill_id(skill) -> str:
    return str(_field(skill, 'skill_id', '') or _field(skill, 'id', '') or '').strip()


def _skill_categories(skill) -> set[str]:
    return {str(item).strip().lower() for item in _tuple(_field(skill, 'categories', ())) if str(item).strip()}


def _skill_actions(skill) -> set[str]:
    """Action markers the skill itself carries (name + description + tags)."""
    text = ' '.join([
        _text(_field(skill, 'name', '')),
        _text(_field(skill, 'description', '')),
        _text(_field(skill, 'tags', ())),
    ])
    return set(_skill_markers.action_markers_from_title(text))


def _step_actions(step_markers) -> set[str]:
    """Step markers that speak about *action* only.

    ``domain:*`` and free group names are deliberately ignored here: the group signal
    already votes on category, and letting a domain marker vote twice would fake the
    multi-signal agreement the filter exists to require.
    """
    actions = set()
    for marker in _tuple(step_markers):
        text = str(marker).strip().lower()
        if text.startswith('action:'):
            actions.add(text.split(':', 1)[1])
        elif text in _skill_markers.ACTION_TYPES:
            actions.add(text)
    return actions


def _cap_tokens(skill) -> set[str]:
    tokens = {str(item).strip().lower() for item in _tuple(_field(skill, 'verified_provides', ()))}
    tokens.update(str(item).strip().lower() for item in _tuple(_field(skill, 'mentions', ())))
    return {token for token in tokens if token}


def _card_for(skill_id: str, cards) -> Optional[dict]:
    """First card whose ``id``/``skill_id`` is this skill (cards are curated, not free text)."""
    for card in cards or ():
        if not isinstance(card, dict):
            continue
        card_id = str(card.get('id') or card.get('skill_id') or '').strip()
        if card_id == skill_id:
            return card
    return None


def _card_verified(card: Optional[dict]) -> bool:
    return isinstance(card, dict) and str(card.get('state') or '').strip().lower() == 'verified'


class ConsensusFilter:
    """Multi-signal consensus filter.

    Each signal is INDEPENDENT -- no single signal can force queue mode.  The optional
    ``bm25_search_fn`` keeps the module usable when the BM25 index (``skill_text``) is
    unavailable: an absent or failing search contributes no BM25 signal.
    """

    def __init__(self, bm25_search_fn: Optional[Callable] = None):
        """
        bm25_search_fn: function(query_text, top_k) -> list of {skill_id, total_score}
        Optional; if None, the BM25 signal always returns False.
        """
        self._bm25 = bm25_search_fn

    def evaluate(
        self,
        skill_id: str,
        step_cap: tuple[str, ...],
        step_category_hint: str,
        step_markers: tuple[str, ...],
        bm25_top_skills: set[str],
        skill_record,
        skill_card: Optional[dict],
    ) -> ConsensusVerdict:
        """Evaluate ONE skill against step signals (see module docstring for signals)."""
        skill_id = str(skill_id or '').strip()
        hint = str(step_category_hint or '').strip().lower()
        categories = _skill_categories(skill_record)

        group_match = bool(hint) and hint in categories

        bm25_ids = {str(item).strip().casefold() for item in (bm25_top_skills or ()) if str(item).strip()}
        bm25_in_top = skill_id.casefold() in bm25_ids

        action_overlap = _step_actions(step_markers) & _skill_actions(skill_record)
        marker_overlap = bool(action_overlap)

        caps = {str(cap).strip().lower() for cap in _tuple(step_cap) if str(cap).strip()}
        cap_provided = bool(caps & _cap_tokens(skill_record))

        card = skill_card if isinstance(skill_card, dict) else None
        card_id = str((card or {}).get('id') or (card or {}).get('skill_id') or '').strip()
        # A card paired with the wrong skill is not a signal, only evidence about another skill.
        card_verified = _card_verified(card) and (not card_id or card_id == skill_id)

        flags = {
            'group': group_match,
            'bm25': bm25_in_top,
            'marker': marker_overlap,
            'cap': cap_provided,
            'card': card_verified,
        }
        signals = tuple(sorted(name for name, matched in flags.items() if matched))
        return ConsensusVerdict(
            skill_id=skill_id,
            queue_mode=verdict_mode(len(signals)),
            signals=signals,
            signal_count=len(signals),
            confidence_bp=confidence_bp_for(signals),
            group_match=group_match,
            bm25_in_top=bm25_in_top,
            marker_overlap=marker_overlap,
            cap_provided=cap_provided,
            card_verified=card_verified,
        )

    def filter_batch(
        self,
        skills: list,
        step,
        cards: list[dict],
        top_k_queue: int = 3,
        top_k_advisory: int = 5,
    ) -> dict:
        """Run consensus on all skills for one step; sorted by confidence, then id.

        Returns ``{'queue': [...], 'advisory': [...], 'gaps': [skill_id, ...]}``.  Items
        beyond ``top_k_queue``/``top_k_advisory`` are reported as gaps: the caller must
        not inject more than it asked for, and a dropped candidate is still visible.
        """
        cards = list(cards or ())
        by_id: dict[str, object] = {}
        for skill in skills or ():
            sid = _skill_id(skill)
            if sid and sid not in by_id:  # first record wins; input order is not a signal
                by_id[sid] = skill

        bm25_top = self._bm25_top(step) if self._bm25 is not None else set()

        verdicts = []
        for sid in sorted(by_id):
            verdicts.append(self.evaluate(
                skill_id=sid,
                step_cap=_tuple(getattr(step, 'cap', ())),
                step_category_hint=str(getattr(step, 'category_hint', '') or ''),
                step_markers=_tuple(getattr(step, 'markers', ())),
                bm25_top_skills=bm25_top,
                skill_record=by_id[sid],
                skill_card=_card_for(sid, cards),
            ))

        verdicts.sort(key=lambda verdict: (-verdict.confidence_bp, verdict.skill_id))
        queue = [v for v in verdicts if v.queue_mode == 'queue'][:max(0, top_k_queue)]
        advisory = [v for v in verdicts if v.queue_mode == 'advisory'][:max(0, top_k_advisory)]
        kept = {v.skill_id for v in queue} | {v.skill_id for v in advisory}
        gaps = [v.skill_id for v in verdicts if v.skill_id not in kept]
        return {'queue': queue, 'advisory': advisory, 'gaps': gaps}

    # ------------------------------------------------------------- internals ==
    def _bm25_top(self, step) -> set[str]:
        """Top BM25 ids for the step text; a missing or broken index is a silent miss."""
        query = ' '.join([
            str(getattr(step, 'title', '') or ''),
            str(getattr(step, 'context', '') or ''),
            str(getattr(step, 'category_hint', '') or ''),
        ]).strip()
        if not query:
            return set()
        try:
            rows = self._bm25(query, BM25_TOP_K) or []
        except Exception:  # an optional index must never break routing
            return set()
        top = set()
        for row in rows:
            if isinstance(row, dict):
                sid = row.get('skill_id') or row.get('id')
            else:
                sid = row
            if sid:
                top.add(str(sid).strip())
        return top


__all__ = [
    'ADVISORY_SIGNALS',
    'BM25_TOP_K',
    'ConsensusFilter',
    'ConsensusVerdict',
    'MAX_CONFIDENCE_BP',
    'QUEUE_MIN_SIGNALS',
    'SIGNAL_NAMES',
    'SIGNAL_WEIGHTS',
    'confidence_bp_for',
    'verdict_mode',
]
