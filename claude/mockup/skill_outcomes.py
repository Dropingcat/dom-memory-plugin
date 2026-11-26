"""Three-track outcome tracker for the skill router.

The three observations of a skill are different signals and are NEVER mixed:

* **READ** (``read``) -- the skill was available/read in context.  A read receipt is
  evidence that the card reached the agent, *not* that the agent used it (C2).
* **CALL** (``call``) -- the skill was actually invoked (a tool call executed).  This is
  the only track that may move learned weights.
* **ACCEPT** (``accept``) -- the step completed (or failed) with the skill in the queue.
  This grades the router's *decision*, not the skill's usefulness.

Cross-track aggregation is the failure mode this module exists to prevent: a READ hit is
not a CALL hit, a CALL hit is not an ACCEPT hit, and an accept-family label recorded on
the read track is a stored track substitution (``tracks_invariant`` reports it).

Labels (fixed vocabulary, no free text):

===========  ==============================================================
``hit``      queued and used / read successfully / step completed as queued
``miss``     invoked but not queued (router missed) / read attempt missed
``error``    the tool call failed after being invoked
``excess``   queued, never called, step still completed
``failure``  queued, step did not complete
``unknown``  abandoned, replanned, or no attribution is possible
===========  ==============================================================

No DB, no network, no model, no clock: ``OutcomeTracker`` is a pure in-memory ledger of
``SkillOutcome`` records, so the same events always produce the same counts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# track and label vocabulary (never cross-track aggregation)
# ---------------------------------------------------------------------------
TRACK_READ = 'read'      # skill was READ (dom_find / dom_context -- L2 receipts)
TRACK_CALL = 'call'      # skill was CALLED (tool execution)
TRACK_ACCEPT = 'accept'  # skill contribution was ACCEPTED (step completed)

TRACKS = (TRACK_READ, TRACK_CALL, TRACK_ACCEPT)

LABEL_HIT = 'hit'
LABEL_MISS = 'miss'
LABEL_ERROR = 'error'
LABEL_EXCESS = 'excess'
LABEL_FAILURE = 'failure'
LABEL_UNKNOWN = 'unknown'

# Which labels a track is allowed to store.  ``hit``/``unknown`` are shared words but
# they live under their own track: they are never summed across tracks.
LABELS_BY_TRACK = {
    TRACK_READ: frozenset({LABEL_HIT, LABEL_MISS, LABEL_UNKNOWN}),
    TRACK_CALL: frozenset({LABEL_HIT, LABEL_MISS, LABEL_ERROR, LABEL_UNKNOWN}),
    TRACK_ACCEPT: frozenset({LABEL_HIT, LABEL_FAILURE, LABEL_EXCESS, LABEL_UNKNOWN}),
}

# Learned-weight deltas.  They are applied to the CALL track only: a skill that was
# never invoked says nothing about which group should be selected next, so the
# accept-family labels (``failure``, ``excess``) deliberately do not move weights.
WEIGHT_DELTAS = {
    LABEL_HIT: 0.05,
    LABEL_MISS: -0.03,
    LABEL_ERROR: -0.05,
}

PRIOR_WEIGHT = 0.5
WEIGHT_FLOOR = 0.0
WEIGHT_CEILING = 1.0


def clamp_weight(value: float) -> float:
    return max(WEIGHT_FLOOR, min(WEIGHT_CEILING, float(value)))


def _bp(value) -> int:
    """Non-negative integer basis points, clamped to the 0..10000 range."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(10000, number))


@dataclass(frozen=True)
class SkillOutcome:
    """One outcome observation.  The ``track`` field is the only aggregation key."""

    track: str               # TRACK_READ | TRACK_CALL | TRACK_ACCEPT
    skill_id: str
    label: str               # hit | miss | error | excess | failure | unknown
    step_id: str
    session_id: str = ''
    group: str = ''
    confidence_bp: int = 0
    timestamp: str = ''

    def __post_init__(self):
        object.__setattr__(self, 'track', str(self.track or '').strip())
        object.__setattr__(self, 'skill_id', str(self.skill_id or '').strip())
        object.__setattr__(self, 'label', str(self.label or '').strip())
        object.__setattr__(self, 'step_id', str(self.step_id or '').strip())
        object.__setattr__(self, 'session_id', str(self.session_id or ''))
        object.__setattr__(self, 'group', str(self.group or '').strip())
        object.__setattr__(self, 'confidence_bp', _bp(self.confidence_bp))
        object.__setattr__(self, 'timestamp', str(self.timestamp or ''))

    def to_dict(self) -> dict:
        return asdict(self)


class OutcomeTracker:
    """Three independent tracks for skill outcomes.

    READ track (from read receipts / dom_find):
        the skill was available in context.  NEVER implies the skill was used or
        useful (C2).

    CALL track (from tool execution / execute.after):
        the skill was actually invoked.
        * ``hit``   -- skill was queued AND called;
        * ``miss``  -- skill was called but NOT queued (the router missed it);
        * ``error`` -- the call failed.

    ACCEPT track (from plan status / step completion):
        * ``hit``     -- step completed with this skill in the queue;
        * ``failure`` -- step NOT completed despite the skill being queued;
        * ``excess``  -- skill queued but never called, step still completed;
        * ``unknown`` -- the skill was not queued, or the step was abandoned.

    NEVER mix tracks (C2 invariant):
        * a READ hit is NOT a CALL hit;
        * a CALL hit is NOT an ACCEPT hit;
        * an accept-family label on the read track is a stored substitution and
          ``tracks_invariant`` reports it.
    """

    def __init__(self):
        self._events: list[SkillOutcome] = []

    # ------------------------------------------------------------- recording ==
    def record_read(self, skill_id: str, step_id: str, *,
                    label: str = LABEL_HIT,
                    session_id: str = '', group: str = '',
                    confidence_bp: int = 0, timestamp: str = '') -> SkillOutcome:
        """Record a READ signal.  NEVER upgrades to call or accept."""
        return self._append(SkillOutcome(
            track=TRACK_READ, skill_id=skill_id, label=self._label(TRACK_READ, label),
            step_id=step_id, session_id=session_id, group=group,
            confidence_bp=confidence_bp, timestamp=timestamp,
        ))

    def record_call(self, skill_id: str, step_id: str, was_queued: bool,
                    success: Optional[bool] = None, *,
                    session_id: str = '', group: str = '',
                    confidence_bp: int = 0, timestamp: str = '') -> SkillOutcome:
        """Record a CALL signal (hit / miss / error).  NEVER upgrades to accept.

        ``success is False`` is an ``error`` regardless of queueing; otherwise a queued
        call is a ``hit`` and a call outside the queue is a ``miss``.
        """
        if success is False:
            label = LABEL_ERROR
        elif was_queued:
            label = LABEL_HIT
        else:
            label = LABEL_MISS
        return self._append(SkillOutcome(
            track=TRACK_CALL, skill_id=skill_id, label=self._label(TRACK_CALL, label),
            step_id=step_id, session_id=session_id, group=group,
            confidence_bp=confidence_bp, timestamp=timestamp,
        ))

    def record_accept(self, skill_id: str, step_id: str, completed: bool,
                      was_queued: bool, *, was_called: Optional[bool] = None,
                      session_id: str = '', group: str = '',
                      confidence_bp: int = 0, timestamp: str = '') -> SkillOutcome:
        """Record an ACCEPT signal (hit / failure / excess / unknown).

        ``was_called=False`` turns a completed, queued step into ``excess`` (the skill
        was queued but never invoked).  A skill outside the queue is ``unknown``: the
        step says nothing about it.
        """
        if not was_queued:
            label = LABEL_UNKNOWN
        elif completed and was_called is False:
            label = LABEL_EXCESS
        elif completed:
            label = LABEL_HIT
        else:
            label = LABEL_FAILURE
        return self._append(SkillOutcome(
            track=TRACK_ACCEPT, skill_id=skill_id, label=self._label(TRACK_ACCEPT, label),
            step_id=step_id, session_id=session_id, group=group,
            confidence_bp=confidence_bp, timestamp=timestamp,
        ))

    # ------------------------------------------------------------- reading ==
    def grouped_counts(self) -> dict:
        """Return ``{track: {label: count}}``; tracks are never summed together."""
        counts: dict[str, dict[str, int]] = {track: {} for track in TRACKS}
        for event in self._events:
            bucket = counts.setdefault(event.track, {})
            bucket[event.label] = bucket.get(event.label, 0) + 1
        return counts

    def update_weights(self, current_weights: dict[str, float]) -> dict[str, float]:
        """Update group weights from the CALL track only.

        ``hit`` +0.05, ``miss`` -0.03, ``error`` -0.05, clipped to 0..1.  READ and
        ACCEPT outcomes never move a weight (C2); a call without a ``group`` cannot be
        attributed and is skipped.  The input mapping is not mutated.
        """
        weights = {}
        for name, value in (current_weights or {}).items():
            try:
                weights[str(name)] = clamp_weight(float(value))
            except (TypeError, ValueError):
                continue
        for event in self._events:
            if event.track != TRACK_CALL:
                continue
            if not event.group:
                continue
            delta = WEIGHT_DELTAS.get(event.label)
            if delta is None:
                continue
            current = weights.get(event.group, PRIOR_WEIGHT)
            weights[event.group] = clamp_weight(round(current + delta, 6))
        return weights

    def tracks_invariant(self) -> bool:
        """Assert the C2 invariant: no track substitution is stored.

        Every event must carry one of the three tracks and a label from that track's
        vocabulary.  A READ event holding ``excess`` (an accept label) or a CALL event
        holding ``failure`` is a stored substitution: no aggregation can repair it, so
        the check fails instead of silently counting it in the wrong bucket.
        """
        for event in self._events:
            allowed = LABELS_BY_TRACK.get(event.track)
            if allowed is None or event.label not in allowed:
                return False
        return True

    # ------------------------------------------------------------ rehydrate ==
    @classmethod
    def from_events(cls, events: Iterable[SkillOutcome]) -> 'OutcomeTracker':
        """Rebuild a tracker from persisted events without validating them.

        Persisted/imported data may be corrupt; the constructor keeps it verbatim so
        ``tracks_invariant`` can report the corruption instead of hiding it.
        """
        tracker = cls()
        for event in events or ():
            if not isinstance(event, SkillOutcome):
                raise TypeError('OutcomeTracker.from_events expects SkillOutcome rows')
            tracker._events.append(event)
        return tracker

    # --------------------------------------------------------------- helpers ==
    @property
    def events(self) -> tuple[SkillOutcome, ...]:
        return tuple(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def _append(self, outcome: SkillOutcome) -> SkillOutcome:
        self._events.append(outcome)
        return outcome

    @staticmethod
    def _label(track: str, label: str) -> str:
        value = str(label or '').strip()
        if value not in LABELS_BY_TRACK[track]:
            raise ValueError('label %r does not belong to the %s track' % (value, track))
        return value


__all__ = [
    'LABELS_BY_TRACK',
    'LABEL_ERROR',
    'LABEL_EXCESS',
    'LABEL_FAILURE',
    'LABEL_HIT',
    'LABEL_MISS',
    'LABEL_UNKNOWN',
    'OutcomeTracker',
    'PRIOR_WEIGHT',
    'SkillOutcome',
    'TRACKS',
    'TRACK_ACCEPT',
    'TRACK_CALL',
    'TRACK_READ',
    'WEIGHT_DELTAS',
    'clamp_weight',
]
