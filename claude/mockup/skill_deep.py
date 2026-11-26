"""Deep analysis of skill content for injection.

Reads pre-computed fields from ``SkillRecord`` (no disk I/O at runtime).
Produces ``what`` / ``why`` / ``how`` for each selected skill.

The three invariants of this stage:

* ``what`` answers "что делает" and never grows past ``MAX_WHAT``.
* ``why`` answers "почему подходит" using *mentions* evidence only.  A skill is never
  described as *providing* a capability: ``verified_provides`` is curated card data and
  is spelled ``verified for cap: X`` so a reader can tell the two apart.
* ``how`` answers "как использовать"; a parsed ``## Example`` command is reproduced
  verbatim with its source path, because a rewritten command is a new command.

The module is pure: same inputs -> same :class:`SkillAnalysis`.  It performs no I/O and
imports nothing outside the standard library.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Maximum characters per field.  These are hard limits, enforced in __post_init__.
MAX_WHAT = 80
MAX_WHY = 160
MAX_HOW = 160

# A source path longer than this is cited by its file name only, so the example keeps
# the room it needs inside MAX_HOW.
MAX_SOURCE_CITE = 48

# How many mentions / verified caps a single ``why`` line lists before clipping.
MAX_WHY_ITEMS = 4


# ---------------------------------------------------------------------------
# small text helpers
# ---------------------------------------------------------------------------

def _field(item, name: str, default):
    """Read a field from a SkillRecord or a plain dict, like the rest of the mockup."""
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _norm(value) -> str:
    """Lower-case, whitespace-collapsed text for substring comparison."""
    if isinstance(value, (list, tuple, set)):
        value = ' '.join(str(part) for part in value)
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


def _clip(text, limit: int) -> str:
    """Collapse whitespace and cut to ``limit`` characters, marking the cut."""
    text = ' '.join(str(text or '').split())
    if limit <= 0:
        return ''
    if len(text) <= limit:
        return text
    if limit == 1:
        return '…'
    return text[:limit - 1].rstrip() + '…'


def _skill_id(item) -> str:
    return str(_field(item, 'skill_id', '') or _field(item, 'id', '') or '').strip()


def _short_source(source: str) -> str:
    """Cite a path verbatim while it fits; fall back to the file name, never to a guess."""
    source = str(source or '').strip()
    if not source or len(source) <= MAX_SOURCE_CITE:
        return source
    return Path(source).name or source


# ---------------------------------------------------------------------------
# analysis record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillAnalysis:
    """What/why/how for one skill.  Frozen: an injected block cannot be re-tuned."""

    skill_id: str
    what: str            # Что делает (from name + description, <=MAX_WHAT chars)
    why: str             # Почему подходит (from signals + mentions, <=MAX_WHY chars)
    how: str             # Как использовать (from examples + usage_hint, <=MAX_HOW chars)
    mentions: tuple[str, ...] = ()  # Raw mentions from SkillRecord (for confidence)
    evidence_level: str = 'mentions'  # NEVER 'provides' -- always says 'mentions'

    def __post_init__(self):
        object.__setattr__(self, 'skill_id', str(self.skill_id or '').strip())
        object.__setattr__(self, 'what', _clip(self.what, MAX_WHAT))
        object.__setattr__(self, 'why', _clip(self.why, MAX_WHY))
        object.__setattr__(self, 'how', _clip(self.how, MAX_HOW))
        object.__setattr__(self, 'mentions', _tuple(self.mentions))
        level = str(self.evidence_level or '').strip().lower()
        # A mention is evidence that a skill talks about a topic; it is never evidence
        # that the skill provides a capability.  Keeping the closed value here makes the
        # anti-pattern impossible to introduce by hand.
        if level != 'mentions':
            raise ValueError("evidence_level is always 'mentions', got %r" % (self.evidence_level,))
        object.__setattr__(self, 'evidence_level', 'mentions')


# ---------------------------------------------------------------------------
# the three fields
# ---------------------------------------------------------------------------

def _what(record, skill_id: str) -> str:
    name = str(_field(record, 'name', '') or '').strip()
    description = str(_field(record, 'description', '') or '').strip()
    return _clip(name or description or skill_id, MAX_WHAT)


def _why(record, step_title: str, step_context: str, consensus, step_cap) -> str:
    signals = tuple(getattr(consensus, 'signals', ()) or ()) if consensus is not None else ()
    pieces = []
    if signals:
        pieces.append('matched: ' + ','.join(str(signal) for signal in signals))

    haystack = _norm(' '.join([
        str(step_title or ''),
        str(step_context or ''),
        ' '.join(str(cap) for cap in (step_cap or ())),
    ]))
    # Only mentions that actually overlap the step are quoted; the rest of the record's
    # mentions would be noise in a 160-character line.
    overlaps = [mention for mention in _tuple(_field(record, 'mentions', ()))
                if _norm(mention) and _norm(mention) in haystack]
    if overlaps:
        pieces.append('mentions: ' + ','.join(overlaps[:MAX_WHY_ITEMS]))

    caps = {_norm(cap) for cap in _tuple(step_cap)}
    verified = [cap for cap in _tuple(_field(record, 'verified_provides', ())) if _norm(cap)]
    if caps:
        verified = [cap for cap in verified if _norm(cap) in caps]
    else:
        verified = verified[:MAX_WHY_ITEMS]
    if verified:
        # Curated card evidence is quoted with its own wording.  "verified for cap"
        # is deliberately different from "mentions" so the two levels stay separable.
        pieces.append('verified for cap: ' + ','.join(verified[:MAX_WHY_ITEMS]))

    if not pieces:
        pieces.append('mentions: none; no step signal')
    return _clip('; '.join(pieces), MAX_WHY)


def _how(record, skill_id: str, step_title: str, step_context: str) -> str:
    examples = _tuple(_field(record, 'examples', ()))
    if examples:
        example = str(examples[0]).strip()  # verbatim: no collapsing, no rewording
        source = _short_source(_field(record, 'source_path', '') or '')
        suffix = ' [source: %s]' % source if source else ''
        prefix = 'skill(%s) — ' % skill_id
        room = MAX_HOW - len(prefix) - len(suffix)
        if room >= 10:
            shown = example if len(example) <= room else _clip(example, room)
            return _clip(prefix + shown + suffix, MAX_HOW)
        return _clip(prefix + example + suffix, MAX_HOW)

    context = ' '.join(str(step_context or step_title or '').split())
    return _clip('Use skill(%s) with step context: %s' % (skill_id, context), MAX_HOW)


def analyze(
    skill_record,
    step_title: str,
    step_context: str,
    consensus: 'ConsensusVerdict',
    step_cap: tuple[str, ...],
) -> SkillAnalysis:
    """Produce what/why/how for one skill given step context.

    ``consensus`` is a ``ConsensusVerdict``; its ``signals`` are quoted, its confidence
    is not, because the injected block must not carry a number that changes while the
    content stays the same (I-B).
    """
    skill_id = _skill_id(skill_record)
    return SkillAnalysis(
        skill_id=skill_id,
        what=_what(skill_record, skill_id),
        why=_why(skill_record, step_title, step_context, consensus, step_cap),
        how=_how(skill_record, skill_id, step_title, step_context),
        mentions=_tuple(_field(skill_record, 'mentions', ())),
        evidence_level='mentions',
    )


def analyze_batch(
    skills: list,
    step,
    verdicts: list['ConsensusVerdict'],
    step_cap: tuple[str, ...],
) -> list[SkillAnalysis]:
    """Batch deep analysis for a step, in verdict order.

    Verdict order is the queue order the consensus filter produced, so the returned
    analyses line up with it.  A skill without a verdict was not selected and is
    skipped; a verdict without a record is skipped too.  Duplicate ids keep the first
    analysis only.
    """
    by_id: dict[str, object] = {}
    for skill in skills or ():
        skill_id = _skill_id(skill)
        if skill_id and skill_id not in by_id:
            by_id[skill_id] = skill

    title = str(getattr(step, 'title', '') or '')
    context = str(getattr(step, 'context', '') or '')
    caps = _tuple(step_cap) or _tuple(getattr(step, 'cap', ()))

    analyses: list[SkillAnalysis] = []
    seen: set[str] = set()
    for verdict in verdicts or ():
        skill_id = str(getattr(verdict, 'skill_id', '') or '').strip()
        if not skill_id or skill_id in seen or skill_id not in by_id:
            continue
        seen.add(skill_id)
        analyses.append(analyze(by_id[skill_id], title, context, verdict, caps))
    return analyses


def digest_for(analysis: SkillAnalysis) -> str:
    """Short, deterministic digest of an analysis (used by the renderer's ``id@digest``)."""
    material = '\x1f'.join([
        analysis.skill_id, analysis.what, analysis.why, analysis.how,
    ]).encode('utf-8')
    return hashlib.sha256(material).hexdigest()[:8]


__all__ = [
    'MAX_HOW',
    'MAX_WHAT',
    'MAX_WHY',
    'SkillAnalysis',
    'analyze',
    'analyze_batch',
    'digest_for',
]
