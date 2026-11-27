"""Positional gradation renderer for the skill injection block.

The block is ordered by distance from the active micro-step (sigma), and the amount of
detail is a function of that distance **only**:

    sigma      current    full analysis (what/why/how)     ~400 chars
    sigma+1    next       short analysis + skill@digest    ~120 chars
    sigma+2    then       one skill id                     ~80 chars
    sigma+3/4  future     just the title                   ~50 chars each
    gaps       always     missing capability -> search      ~60 chars

Two invariants are enforced here, not by the caller:

* **I-A** -- the format depends on position, never on confidence.  The renderer does not
  even accept a verdict, so a confidence change cannot invalidate the prompt cache.
* **I-B** -- no floats in the block.  Confidence lives in the panel as integer basis
  points; what is injected is text, ids and a short hex digest.

A gap section is always emitted (``gaps: none`` when there is nothing missing), because
a hidden gap looks like a satisfied requirement.  ``render_block`` is pure: the same
inputs and the same fingerprint produce identical bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

if __package__:  # normal use: ``from mockup import skill_render``
    from . import skill_deep as _skill_deep
else:  # direct import from opencode/ on sys.path
    import skill_deep as _skill_deep  # type: ignore

# Budget per position (chars).
BUDGET_CURRENT = 400   # current step: full analysis
BUDGET_NEXT = 120      # sigma+1: short analysis + skill ids
BUDGET_THEN = 80       # sigma+2: one skill id + brief
BUDGET_FUTURE = 50     # sigma+3, sigma+4: just the title
BUDGET_GAPS = 60       # gap section
TOTAL_BUDGET = 800

# Per-line caps inside the current block.  Their sum plus the two newlines stays under
# BUDGET_CURRENT, so a single long field cannot push the block over its position budget.
CURRENT_TITLE_LIMIT = 110
CURRENT_USE_LIMIT = 150
CURRENT_HOW_LIMIT = 130

MAX_FUTURE_LINES = 2  # sigma+3 and sigma+4 only; later steps are not injected


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


def _title(step) -> str:
    title = str(getattr(step, 'title', '') or '').strip()
    return ' '.join(title.split()) or str(getattr(step, 'step_id', '') or '').strip()


def _caps(step) -> str:
    caps = getattr(step, 'cap', ()) or ()
    return ','.join(str(cap) for cap in caps) or 'no-cap'


def _category(step) -> str:
    return str(getattr(step, 'category_hint', '') or '').strip() or 'no-category'


def _digest(analysis) -> str:
    return _skill_deep.digest_for(analysis)


# ---------------------------------------------------------------------------
# per-position renderers
# ---------------------------------------------------------------------------

def render_current(step, analysis: 'SkillAnalysis') -> str:
    """Render the current sigma: ``step: TITLE [cap | category] / use / how``.

    Returned as a short multi-line string: a 400-character single line is unreadable
    and, more importantly, impossible to diff in a prompt cache.  The three lines are
    clipped independently so the total stays within ``BUDGET_CURRENT``.
    """
    head = _clip('step: %s [%s | %s]' % (_title(step), _caps(step), _category(step)),
                 CURRENT_TITLE_LIMIT)
    why = ' '.join(str(getattr(analysis, 'why', '') or '').split())
    use = 'use: %s%s' % (getattr(analysis, 'skill_id', '') or '',
                         (' — ' + why) if why else '')
    how = ' '.join(str(getattr(analysis, 'how', '') or '').split())
    how_line = 'how: %s' % how if how else 'how: skill(%s)' % (getattr(analysis, 'skill_id', '') or '')
    return '\n'.join([
        head,
        _clip(use, CURRENT_USE_LIMIT),
        _clip(how_line, CURRENT_HOW_LIMIT),
    ])


def render_next(step, analysis: Optional['SkillAnalysis']) -> str:
    """Render sigma+1: ``next: TITLE — skill_id@digest`` (<= BUDGET_NEXT)."""
    if analysis is None:
        return _clip('next: %s' % _title(step), BUDGET_NEXT)
    skill_id = str(getattr(analysis, 'skill_id', '') or '').strip()
    if not skill_id:
        return _clip('next: %s' % _title(step), BUDGET_NEXT)
    return _clip('next: %s — %s@%s' % (_title(step), skill_id, _digest(analysis)),
                 BUDGET_NEXT)


def render_then(step, skill_id: str) -> str:
    """Render sigma+2: ``then: TITLE — skill_id`` (<= BUDGET_THEN)."""
    skill_id = str(skill_id or '').strip()
    if not skill_id:
        return _clip('then: %s' % _title(step), BUDGET_THEN)
    return _clip('then: %s — %s' % (_title(step), skill_id), BUDGET_THEN)


def render_future(step) -> str:
    """Render sigma+3 / sigma+4: just ``step: TITLE`` (<= BUDGET_FUTURE)."""
    return _clip('step: %s' % _title(step), BUDGET_FUTURE)


def render_gaps(gaps: list[str], step_cap: Optional[list[str]] = None) -> str:
    """Render missing capabilities: ``gap: CAP -> skill_search`` (<= BUDGET_GAPS).

    An empty list is not an empty string: the block always carries an explicit
    ``gaps: none`` so the section cannot be mistaken for a truncated one.
    When step_cap is empty (no capabilities declared), renders
    ``gaps: not-declared`` instead of ``gaps: none`` (false green guard).
    """
    if step_cap is not None and not step_cap:
        return 'gaps: not-declared (step has no capability declaration)'
    names = [str(gap).strip() for gap in (gaps or ()) if str(gap).strip()]
    if not names:
        return 'gaps: none'
    prefix = 'gap: '
    suffix = ' -> skill_search'
    room = BUDGET_GAPS - len(prefix) - len(suffix)
    if room >= 3:
        return prefix + _clip(','.join(names), room) + suffix
    return _clip(prefix + ','.join(names) + suffix, BUDGET_GAPS)


# ---------------------------------------------------------------------------
# the block
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QueueBlock:
    """The final rendered block, ready for injection."""

    lines: tuple[str, ...]     # Rendered lines
    total_chars: int           # <= TOTAL_BUDGET
    has_gaps: bool             # Whether any real gap was rendered
    fingerprint: str = ''      # sha256 for the tau gate

    def __post_init__(self):
        lines = tuple(str(line) for line in (self.lines or ()))
        object.__setattr__(self, 'lines', lines)
        total = len('\n'.join(lines))
        if total > TOTAL_BUDGET:
            raise ValueError('rendered block is %d chars, budget is %d'
                             % (total, TOTAL_BUDGET))
        # total_chars is derived, not declared: a caller cannot under-report the block.
        object.__setattr__(self, 'total_chars', total)
        object.__setattr__(self, 'has_gaps', bool(self.has_gaps))
        object.__setattr__(self, 'fingerprint', str(self.fingerprint or ''))

    @property
    def text(self) -> str:
        """The block as the exact bytes that go into the prompt."""
        return '\n'.join(self.lines)


def _render_middle(
    next_steps: list[tuple['MicroStep', Optional['SkillAnalysis']]],
    then_steps: list[tuple['MicroStep', str]],
    future_steps: list['MicroStep'],
) -> list[str]:
    """Middle lines, graded by position.  ``next_steps`` in order become next/then/future.

    A ``StepWindow`` may hold up to three follow-ups; the first is sigma+1 (short
    analysis), the second sigma+2 (id only) and the third sigma+3 (title only).  The
    explicit ``then_steps``/``future_steps`` lists add further positions after those.
    """
    middle: list[str] = []
    for index, row in enumerate(next_steps or ()):
        step_i, analysis_i = row
        if index == 0:
            middle.append(render_next(step_i, analysis_i))
        elif index == 1:
            middle.append(render_then(step_i, getattr(analysis_i, 'skill_id', '') if analysis_i else ''))
        else:
            middle.append(render_future(step_i))
    for step_i, skill_id in then_steps or ():
        middle.append(render_then(step_i, skill_id))
    for step_i in (future_steps or ())[:MAX_FUTURE_LINES]:
        middle.append(render_future(step_i))
    return middle


def render_block(
    current_step,
    current_analysis: 'SkillAnalysis',
    next_steps: list[tuple['MicroStep', Optional['SkillAnalysis']]],
    then_steps: list[tuple['MicroStep', str]],          # (step, skill_id)
    future_steps: list['MicroStep'],
    gaps: list[str],
    fingerprint: str,
    step_cap: Optional[list[str]] = None,
) -> QueueBlock:
    """Full block render with positional gradation.

    Layout (all sizes are budget ceilings, not targets)::

        sigma (current):      ~400 chars (step + what/why/how)
        sigma+1 (next):       ~120 chars (step + skill ids)
        sigma+2 (then):       ~80 chars  (step + one skill id)
        sigma+3, sigma+4:     ~50 chars each (just titles)
        gaps:                 ~60 chars  (gap list)
                              ----------
                              760 <= 800 chars

    When a caller supplies more steps than the budget can hold, the *farthest* lines
    are dropped first and the gap section is never dropped: a shorter window is honest,
    a hidden gap is not.
    """
    head = render_current(current_step, current_analysis).split('\n')
    middle = _render_middle(next_steps, then_steps, future_steps)
    tail = [render_gaps(gaps, step_cap)]

    def joined(parts: list[str]) -> int:
        return len('\n'.join(parts))

    # Farthest position first: future, then sigma+2, then sigma+1.
    while middle and joined(head + middle + tail) > TOTAL_BUDGET:
        middle.pop()

    lines = head + middle + tail
    # Defensive: a pathological title could still overflow.  Drop the least important
    # current line (how, then use), never the gap line.
    while len(lines) > len(tail) + 1 and joined(lines) > TOTAL_BUDGET:
        head = head[:-1]
        lines = head + middle + tail
    if joined(lines) > TOTAL_BUDGET:
        room = TOTAL_BUDGET - len(tail[0]) - 1
        head = [_clip(head[0], max(0, room))] if head else []
        lines = head + middle + tail

    return QueueBlock(
        lines=tuple(lines),
        total_chars=len('\n'.join(lines)),  # recomputed in __post_init__
        has_gaps=bool([gap for gap in (gaps or ()) if str(gap).strip()]),
        fingerprint=fingerprint,
    )


__all__ = [
    'BUDGET_CURRENT',
    'BUDGET_FUTURE',
    'BUDGET_GAPS',
    'BUDGET_NEXT',
    'BUDGET_THEN',
    'QueueBlock',
    'TOTAL_BUDGET',
    'render_block',
    'render_current',
    'render_future',
    'render_gaps',
    'render_next',
    'render_then',
]
