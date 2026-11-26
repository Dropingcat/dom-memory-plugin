"""Group-level skill selector.

Level 1 selects categories (groups), Level 2 filters skills inside the selected groups.
Scoring uses four factors, so a single keyword can never pick a group:

* 0.4 direct category match (``step_category_hint``)
* 0.3 capability alignment (a step cap maps to the group)
* 0.2 learned group weight (from outcomes, persisted between sessions)
* 0.1 marker alignment (``domain:<group>`` evidence on the step)

A group with no evidence at all is not returned -- the selector reports "no group" instead
of dressing up the prior as a match.  Exploration fires deterministically on every tenth
``select_groups`` call (or on demand) and appends one low-ranked eligible group marked
``exploration`` in its reason.

Weights are derived data: a missing/corrupt file is an empty prior, and a failed save is
ignored rather than breaking routing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

if __package__:  # normal use: ``from mockup import skill_group``
    from . import skill_markers as _skill_markers
else:  # direct import from opencode/ on sys.path
    import skill_markers as _skill_markers  # type: ignore

GROUP_WEIGHTS_SCHEMA = 1
PRIOR_WEIGHT = 0.5
EXPLORATION_RATE = 0.1
EXPLORATION_MIN_WEIGHT = 0.1
TOP_GROUPS = 3

# The two-level score is a fixed linear combination; the shares sum to 1.0.
FACTOR_WEIGHTS = {
    'category': 0.4,
    'capability': 0.3,
    'learned': 0.2,
    'marker': 0.1,
}

# Learned weight is an exponential moving average: each outcome moves it by at most 2 %.
LEARNING_RATE = 0.02
WEIGHT_DECAY = 1.0 - LEARNING_RATE


@dataclass(frozen=True)
class GroupWeights:
    """Per-group weights learned from outcomes."""

    group: str
    weight: float = PRIOR_WEIGHT   # 0.0-1.0, initialized to prior
    hit_count: int = 0
    miss_count: int = 0
    last_updated: str = ''         # ISO timestamp

    @property
    def hit_rate(self) -> float:
        if self.hit_count + self.miss_count == 0:
            return 0.0
        return self.hit_count / (self.hit_count + self.miss_count)

    def to_dict(self) -> dict:
        return {
            'weight': self.weight,
            'hit_count': self.hit_count,
            'miss_count': self.miss_count,
            'last_updated': self.last_updated,
        }


def _norm(value) -> str:
    return ' '.join(str(value or '').lower().split())


def _tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        value = (value,)
    try:
        return tuple(str(item).strip() for item in value if str(item).strip())
    except TypeError:
        return (str(value).strip(),)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def exploration_trigger(step_count: int, rate: float = EXPLORATION_RATE) -> bool:
    """Deterministic exploration trigger: 1 in 10 steps (``round(1/rate)`` period)."""
    if rate <= 0.0:
        raise ValueError('exploration rate must be positive')
    period = max(1, int(round(1.0 / rate)))
    return (int(step_count) % period) == 0


class GroupSelector:
    """Two-level group+skill selector.

    Level 1: from the step's category hint, capabilities and markers, select groups.
    Level 2: keep skills inside those groups and let ``ConsensusFilter`` rank them.
    """

    def __init__(self, categories: tuple[str, ...], weights_path: Optional[Path] = None):
        seen = []
        for category in _tuple(categories):
            text = _norm(category)
            if text and text not in seen:
                seen.append(text)
        self._categories = tuple(seen)
        self._weights_path = Path(weights_path) if weights_path is not None else None
        self._weights: dict[str, GroupWeights] = {}
        self._step_count = 0
        self._load_weights()

    # ------------------------------------------------------------- level 1 ==
    def select_groups(
        self,
        step_category_hint: str,
        step_cap: tuple[str, ...],
        step_markers: tuple[str, ...],
        explore: Optional[bool] = None,
    ) -> list[tuple[str, float, str]]:
        """Level 1: select categories as ``(category, score, reason)``, best first.

        ``explore=None`` uses the deterministic 1-in-10 trigger; ``True``/``False`` force
        or suppress it.  An empty list means the step has no group evidence at all.
        """
        self._step_count += 1
        if explore is None:
            explore = exploration_trigger(self._step_count)
        if not self._categories:
            return []

        hint = _norm(step_category_hint)
        caps = {_norm(cap) for cap in _tuple(step_cap) if _norm(cap)}
        cap_groups = {_skill_markers.CAPABILITY_CATEGORY[cap]
                      for cap in caps if cap in _skill_markers.CAPABILITY_CATEGORY}
        cap_groups.update(cap for cap in caps if cap in self._categories)
        marker_groups = self._marker_groups(step_markers)

        rows: list[tuple[str, float, str]] = []
        for category in self._categories:
            category_match = 1.0 if hint == category else 0.0
            capability = 1.0 if category in cap_groups else 0.0
            marker = 1.0 if category in marker_groups else 0.0
            if not (category_match or capability or marker):
                continue  # prior alone is not a match
            weight = self._group_weight(category).weight
            score = (FACTOR_WEIGHTS['category'] * category_match
                     + FACTOR_WEIGHTS['capability'] * capability
                     + FACTOR_WEIGHTS['learned'] * weight
                     + FACTOR_WEIGHTS['marker'] * marker)
            reason = 'category=%d cap=%d weight=%.2f marker=%d' % (
                int(category_match), int(capability), weight, int(marker))
            rows.append((category, round(score, 6), reason))

        rows.sort(key=lambda row: (-row[1], row[0]))

        if explore and rows:
            picked = self._exploration_pick(rows)
            if picked is not None:
                category, score, reason = picked
                rows.append((category, score, reason + '; exploration'))
                rows.sort(key=lambda row: (-row[1], row[0]))
        return rows

    # ------------------------------------------------------------- level 2 ==
    def select_skills(
        self,
        skills: list,
        step,
        cards: list[dict],
        consensus_filter,
        groups: list[tuple[str, float, str]],
    ) -> dict:
        """Level 2: keep skills in the selected groups and run consensus on them.

        Skills outside the selected groups are not candidates at all: their ids are
        reported as gaps so the caller can see what the group filter excluded.
        """
        names = {str(group[0]).strip().lower() for group in groups or () if group}
        ids = sorted({self._skill_id(skill) for skill in skills or () if self._skill_id(skill)})
        if not names:
            return {'queue': [], 'advisory': [], 'gaps': ids}

        inside = []
        for skill in skills or ():
            categories = {_norm(item) for item in _tuple(self._field(skill, 'categories'))}
            if categories & names:
                inside.append(skill)

        if not inside:
            return {'queue': [], 'advisory': [], 'gaps': ids}
        return consensus_filter.filter_batch(inside, step, cards)

    # -------------------------------------------------------------- learning ==
    def record_outcome(self, group: str, skill_id: str, hit: bool):
        """Update one group weight from an outcome and persist it.

        ``skill_id`` is accepted because the outcome hook knows which skill was loaded;
        group weights aggregate over skills, so it is not keyed by it yet.
        """
        name = _norm(group)
        if not name:
            return
        current = self._group_weight(name)
        target = 1.0 if hit else 0.0
        weight = _clamp(round(WEIGHT_DECAY * current.weight + LEARNING_RATE * target, 6))
        self._weights[name] = replace(
            current,
            weight=weight,
            hit_count=current.hit_count + (1 if hit else 0),
            miss_count=current.miss_count + (0 if hit else 1),
            last_updated=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        )
        self._save_weights()

    def weight_for(self, group: str) -> float:
        """Current learned weight for a group (the prior when unseen)."""
        return self._group_weight(_norm(group)).weight

    # ------------------------------------------------------------ internals ==
    @staticmethod
    def _field(skill, name: str):
        if isinstance(skill, dict):
            return skill.get(name)
        return getattr(skill, name, None)

    @classmethod
    def _skill_id(cls, skill) -> str:
        return str(cls._field(skill, 'skill_id') or cls._field(skill, 'id') or '').strip()

    def _group_weight(self, category: str) -> GroupWeights:
        if category not in self._weights:
            self._weights[category] = GroupWeights(group=category)
        return self._weights[category]

    def _marker_groups(self, step_markers) -> set[str]:
        groups = set()
        for marker in _tuple(step_markers):
            text = _norm(marker)
            if text.startswith('domain:'):
                groups.add(text.split(':', 1)[1])
            elif text.startswith('group:'):
                groups.add(text.split(':', 1)[1])
            elif text in self._categories:
                groups.add(text)
        return groups & set(self._categories)

    def _exploration_pick(self, rows: list[tuple[str, float, str]]):
        """Highest-learned eligible group outside the top-3; deterministic, no RNG."""
        top = {row[0] for row in rows[:TOP_GROUPS]}
        candidates = [category for category in self._categories
                      if category not in top
                      and self._group_weight(category).weight > EXPLORATION_MIN_WEIGHT]
        if not candidates:
            return None
        category = sorted(
            candidates, key=lambda name: (-self._group_weight(name).weight, name))[0]
        weight = self._group_weight(category).weight
        score = round(FACTOR_WEIGHTS['learned'] * weight, 6)
        reason = 'category=0 cap=0 weight=%.2f marker=0 (not in top-%d)' % (weight, TOP_GROUPS)
        return category, score, reason

    def _weights_payload(self) -> dict:
        return {
            'schema': GROUP_WEIGHTS_SCHEMA,
            'weights': {name: weights.to_dict() for name, weights in sorted(self._weights.items())},
        }

    def _load_weights(self):
        """Load persisted weights from file.  A missing/corrupt file is an empty prior."""
        if self._weights_path is None:
            return
        try:
            payload = json.loads(self._weights_path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        rows = payload.get('weights')
        if not isinstance(rows, dict):
            return
        for name, row in rows.items():
            if not isinstance(row, dict):
                continue
            group = _norm(name)
            if not group:
                continue
            try:
                weight = _clamp(float(row.get('weight', PRIOR_WEIGHT)))
                hit_count = max(0, int(row.get('hit_count') or 0))
                miss_count = max(0, int(row.get('miss_count') or 0))
            except (TypeError, ValueError):
                continue
            self._weights[group] = GroupWeights(
                group=group,
                weight=weight,
                hit_count=hit_count,
                miss_count=miss_count,
                last_updated=str(row.get('last_updated') or ''),
            )

    def _save_weights(self):
        """Save weights beside the index cache; derived data, so a write failure is ignored."""
        if self._weights_path is None:
            return
        try:
            self._weights_path.parent.mkdir(parents=True, exist_ok=True)
            self._weights_path.write_text(
                json.dumps(self._weights_payload(), ensure_ascii=False, sort_keys=True, indent=2) + '\n',
                encoding='utf-8')
        except OSError:
            return


__all__ = [
    'EXPLORATION_MIN_WEIGHT',
    'EXPLORATION_RATE',
    'FACTOR_WEIGHTS',
    'GROUP_WEIGHTS_SCHEMA',
    'GroupSelector',
    'GroupWeights',
    'PRIOR_WEIGHT',
    'TOP_GROUPS',
    'exploration_trigger',
]
