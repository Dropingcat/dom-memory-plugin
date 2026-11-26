"""Micro-step registry (sigma) and the tau fingerprint gate.

Addendum A (``docs/concepts/2026-11-27-skill-selector-addendum.md`` §1) separates three
scales: **pi** is a plan item, **sigma** is a micro-step taken from the registry, and **tau**
is one agent turn.  ``pi = sigma_1..sigma_n``; one sigma may span several tau.

This module owns two additive pieces, with no DB, no I/O and no model:

* ``MicroStep`` / ``StepWindow`` -- the sigma schema.  ``cap`` is inherited from the plan
  item's acceptance criteria (``skill_router.capabilities_from_accept``), never inferred
  from free text; ``category_hint`` is a *hint* derived from at least two independent
  evidence families and is ``''`` when the evidence is thin or contradictory.
* ``BlockFingerprint`` -- the tau gate.  A block is re-emitted only when its fingerprint
  changes; an unchanged fingerprint is an *idle re-issue*, counted but not re-emitted.

Phase classification proper (the closed verb dictionary, `skill_phase.py`) is a separate
module; here verbs only mark ``action:*`` evidence on the step.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Optional

if __package__:  # normal use: ``from mockup import step_registry``
    from . import skill_router as _skill_router
else:  # direct import from opencode/ on sys.path
    import skill_router as _skill_router  # type: ignore

CONTEXT_LIMIT = 600          # sigma context is distilled, not the whole plan body
MAX_NEXT_STEPS = 3           # the window is current + up to 3 prefetched sigma
MAX_ANCESTRY = 16            # defensive bound for the parent walk

# Keyword group (skill_router.RULES) -> capability category.  'planning' is the
# plan/queue vocabulary, whose capability in the closed dictionary is 'router'.
GROUP_CATEGORY = {
    'migration': 'migration',
    'testing': 'testing',
    'review': 'review',
    'docs': 'docs',
    'prompt': 'prompt',
    'planning': 'router',
    'writing': 'writer',
    'git': 'git',
    'ledger': 'ledger',
    'security': 'security',
}

# Domain stems are deliberately NOT a copy of RULES: they carry the specific tokens a
# generic verb group would miss (identifiers, file names, product terms).  Overlap with
# RULES would let one word vote twice and fake multi-signal evidence.
DOMAIN_STEMS = {
    'testing': ('unittest', 'fixture', 'fixtures', 'smoke_', 'tdd', 'тдд'),
    'sqlite': ('pragma', 'колонк', 'in_force'),
    'migration': ('ddl', 'alembic', 'schema_version', 'v4→v5', 'v4->v5'),
    'ledger': ('dom_claim', 'claim_sessions', 'read_receipts', 'append-only', 'hash-chain', 'hash chain'),
    'docs': ('markdown', 'руководств', 'guide'),
    'ops': ('deploy', 'deployment', 'деплой', 'docker', 'systemd', 'pipeline'),
    'runtime': ('runtime', 'traceback', 'исключен', 'exception', 'memory leak'),
    'security': ('auth', 'authentication', 'token', 'tokens', 'permission', 'permissions', 'разрешени'),
    'router': ('skill_router', 'routing', 'маршрут', 'селектор', 'selector'),
    'prompt': ('hook', 'hooks', 'хук', 'system-prompt', 'инструкц'),
    'writer': ('essay', 'essays', 'эссе', 'лонгрид', 'черновик'),
    'review': ('критик', 'critique', 'critiques'),
    'git': ('merge', 'rebase', 'tag', 'тег'),
    'panel': ('panel', 'dashboard', 'панель', 'ui', 'монитор'),
}

# Action markers (addendum §2.3 phase verbs).  They are markers only: the authoritative
# phase classifier is a separate stage, so an ambiguous verb cannot silently pick a phase.
ACTION_MARKERS = {
    'inspect': ('прочита', 'прочти', 'изуч', 'посмотр', 'найти', 'найди', 'провер', 'свер',
                'осмотр', 'inspect', 'examine', 'review', 'read', 'find', 'check', 'look'),
    'modify': ('добав', 'измен', 'созда', 'напиш', 'напис', 'удал', 'перенес', 'мигрир', 'обнов',
               'add', 'change', 'create', 'write', 'remove', 'move', 'migrate', 'update', 'implement'),
    'verify': ('прогн', 'протест', 'замер', 'валид', 'убед', 'test', 'tests', 'testing', 'run',
               'measure', 'validate', 'assert', 'benchmark'),
    'document': ('опиш', 'опиши', 'задокумент', 'оформ', 'поясн', 'document', 'describe', 'explain'),
    'decide': ('выбер', 'выбери', 'спроект', 'соглас', 'реши', 'сравн', 'choose', 'design', 'agree',
               'decide', 'compare'),
}

_STATUS_ALIASES = {
    'x': 'done', 'X': 'done', 'completed': 'done', 'done': 'done',
    'in_progress': 'in_progress', 'pending': 'pending', 'todo': 'pending', 'blocked': 'blocked',
    'cancelled': 'cancelled', 'canceled': 'cancelled',
}
_CLOSED_STATUSES = frozenset(('done', 'cancelled'))


def _text(value) -> str:
    """Lower-cased, whitespace-collapsed text for marker matching."""
    if isinstance(value, (list, tuple)):
        value = ' '.join(str(item) for item in value)
    return ' '.join(str(value or '').lower().split())


def _as_str(value, default='') -> str:
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        return '\n'.join(str(item) for item in value if str(item).strip())
    return str(value)


def _mapping(value):
    """Accept a dict or a read-only row object (e.g. sqlite3.Row) as a plan row."""
    if isinstance(value, dict):
        return value
    keys = getattr(value, 'keys', None)
    if callable(keys):
        try:
            return {key: value[key] for key in keys()}
        except (KeyError, IndexError, TypeError):
            return None
    return None


def _status(value) -> str:
    return _STATUS_ALIASES.get(str(value or 'pending').strip().lower(), 'pending')


def _match_stem(stem: str, haystack: str) -> bool:
    """ASCII words match whole words; RU stems and identifiers match by prefix/substring.

    Whole-word matching keeps 'auth' out of 'author', 'run' out of 'runtime' and 'tag' out
    of 'stage'.  Identifiers (``dom_claim``, ``smoke_``) and non-ASCII stems keep their
    prefix semantics.
    """
    if stem.isascii() and stem.replace('-', '').isalpha():
        return re.search(r'(?<![a-z0-9_])%s(?![a-z0-9_])' % re.escape(stem), haystack) is not None
    return stem in haystack


def _accept_list(value) -> list:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, dict)]
    return []


def _explicit_caps(task: dict, allowed) -> set:
    caps = set()
    for key in ('capabilities', 'cap'):
        raw = task.get(key)
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, (list, tuple)):
            caps.update(str(cap) for cap in raw if str(cap) in allowed)
    return caps


def _norm_path(value) -> str:
    """'1.2', 'P1.2', 'п1.2' -> 'П1.2'; anything else is returned stripped."""
    text = str(value or '').strip()
    if not text:
        return ''
    if text[:1].isdigit():
        return 'П' + text
    if text[:1] in ('P', 'p', 'п'):
        return 'П' + text[1:]
    return text


def _plan_version(task: dict) -> int:
    for key in ('plan_version', 'version'):
        value = task.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
    digits = re.findall(r'\d+', str(task.get('source_revision') or ''))
    return int(digits[-1]) if digits else 0


@dataclass(frozen=True)
class MicroStep:
    """One sigma: a micro-action decomposed from a plan item (pi).

    ``step_id`` is the stable identity (the plan task id when the registry comes from the
    live projection).  ``cap`` is the authority inherited from the plan item; ``markers``
    is evidence, not a decision.  ``category_hint`` stays ``''`` unless at least two
    independent evidence families agree (see ``RegistryExtractor.multi_marker_hint``).
    """

    step_id: str
    title: str
    parent_plan_task_id: str
    plan_version: int
    cap: tuple[str, ...] = ()
    category_hint: str = ''
    markers: tuple[str, ...] = ()
    context: str = ''

    def __post_init__(self):
        object.__setattr__(self, 'cap', tuple(sorted({str(cap) for cap in (self.cap or ())})))
        object.__setattr__(self, 'markers', tuple(sorted({str(marker) for marker in (self.markers or ())})))
        object.__setattr__(self, 'step_id', str(self.step_id))
        object.__setattr__(self, 'title', str(self.title))
        object.__setattr__(self, 'parent_plan_task_id', str(self.parent_plan_task_id))
        object.__setattr__(self, 'plan_version', int(self.plan_version))
        object.__setattr__(self, 'category_hint', str(self.category_hint or ''))
        object.__setattr__(self, 'context', str(self.context or ''))
        if len(self.context) > CONTEXT_LIMIT:
            raise ValueError('MicroStep.context exceeds %d chars' % CONTEXT_LIMIT)


@dataclass(frozen=True)
class StepWindow:
    """The active sigma plus up to three prefetched sigma (addendum §1, §5)."""

    current: MicroStep
    next_steps: tuple[MicroStep, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'next_steps', tuple(self.next_steps))
        if len(self.next_steps) > MAX_NEXT_STEPS:
            raise ValueError('StepWindow.next_steps holds at most %d steps' % MAX_NEXT_STEPS)

    @property
    def steps(self) -> tuple[MicroStep, ...]:
        return (self.current,) + self.next_steps


class RegistryExtractor:
    """Build sigma windows from plan rows; pure and deterministic.

    ``router_module`` must expose ``capabilities_from_accept`` (and may expose ``RULES``);
    the default is ``mockup.skill_router``.  Injection keeps the capability dictionary in
    one place and lets a host pass a versioned router.
    """

    def __init__(self, router_module=None):
        self.router = router_module if router_module is not None else _skill_router
        if not hasattr(self.router, 'capabilities_from_accept'):
            raise TypeError('router_module must expose capabilities_from_accept()')
        self.rules = getattr(self.router, 'RULES', _skill_router.RULES)
        self.capabilities = tuple(getattr(self.router, 'CAPABILITIES', _skill_router.CAPABILITIES))

    # ------------------------------------------------------------ extraction ==
    def from_plan_tasks(self, plan_tasks: list[dict], current_id: str) -> StepWindow:
        """Extract the active sigma and up to three follow-ups from plan rows.

        Accepts the live projection shape (``current_plan_tasks``: ``plan_task_id``,
        ``path``, ``parent_id``, ``version``, ``status``, ``title`` ...) and the lighter
        mockup shape (``path``, ``title``, ``context``, ``accept``, optional ``children``).
        Nested ``children``/``subtasks`` are flattened in pre-order, so the children of the
        active item naturally become its sigma queue.  Items with status ``done`` or
        ``cancelled`` are not prefetched.
        """
        records = self._records(plan_tasks or [])
        if not records:
            raise ValueError('PLAN_EMPTY: no plan tasks to extract from')
        index = self._find(records, current_id)
        if index is None:
            raise ValueError('PLAN_TASK_NOT_FOUND: %r matches no plan task' % (current_id,))
        active = records[index]
        current = self._micro_step(active, records)
        following = []
        for record in records[index + 1:]:
            if len(following) >= MAX_NEXT_STEPS:
                break
            if _status(record['task'].get('status')) in _CLOSED_STATUSES:
                continue
            if record['archived']:
                continue
            following.append(self._micro_step(record, records))
        return StepWindow(current=current, next_steps=tuple(following))

    def multi_marker_hint(self, step_title: str, step_context: str,
                          step_claims: list[str], accept=None, capabilities=None) -> str:
        """Derive ``category_hint`` from independent evidence families.

        Signals (each family votes at most once per category):

        1. ``lexical``   -- keyword groups from ``skill_router.RULES`` over title/context/claims
        2. ``domain``    -- specific domain stems over the same text
        3. ``accept``    -- acceptance item types mapped to capabilities
        4. ``capability``-- explicit ``capabilities`` on the row

        A category is returned only when at least two *different* families vote for it and
        it is the unique maximum; otherwise ``''``.  A single verb, a single accept type or
        a single identifier is therefore never enough.
        """
        haystack = _text(' '.join([str(step_title or ''), str(step_context or ''),
                                   *[str(item) for item in step_claims or ()]]))
        votes = {}

        def vote(category, family):
            votes.setdefault(category, set()).add(family)

        for group in self._rule_hits(haystack):
            category = GROUP_CATEGORY.get(group)
            if category:
                vote(category, 'lexical')
        for category, stems in DOMAIN_STEMS.items():
            if any(_match_stem(stem, haystack) for stem in stems):
                vote(category, 'domain')
        caps, _unmapped = self.router.capabilities_from_accept(_accept_list(accept))
        allowed = set(self.capabilities)
        for cap in caps:
            if cap in allowed:
                vote(cap, 'accept')
        explicit = capabilities
        if isinstance(explicit, str):
            explicit = [explicit]
        for cap in explicit or ():
            cap = str(cap)
            if cap in allowed:
                vote(cap, 'capability')
        if not votes:
            return ''
        best = max(len(families) for families in votes.values())
        if best < 2:
            return ''
        winners = [category for category, families in votes.items() if len(families) == best]
        return winners[0] if len(winners) == 1 else ''

    # ------------------------------------------------------------- internals ==
    def _rule_hits(self, haystack: str) -> set:
        return {group for group, words in self.rules.items() if any(word in haystack for word in words)}

    def _records(self, plan_tasks: list[dict]) -> list[dict]:
        records, used = [], {}

        def walk(items, parent_id, depth):
            for item in items:
                task = _mapping(item)
                if task is None:
                    continue
                ident = self._identifier(task, len(records), used)
                declared_parent = task.get('parent_id')
                parent = str(declared_parent) if declared_parent not in (None, '') else parent_id
                records.append({
                    'id': ident,
                    'parent': parent,
                    'depth': depth,
                    'archived': bool(task.get('archived')),
                    'task': task,
                })
                children = next((task[key] for key in ('children', 'subtasks', 'sub_items', 'steps')
                                 if isinstance(task.get(key), list)), [])
                walk(children, ident, depth + 1)

        walk(plan_tasks, None, 0)
        return records

    def _identifier(self, task: dict, slot: int, used: dict) -> str:
        base = ''
        for key in ('step_id', 'plan_task_id', 'id'):
            value = task.get(key)
            if isinstance(value, str) and value.strip():
                base = value.strip()
                break
        if not base:
            base = str(task.get('path') or 'sigma-%d' % (slot + 1)).strip()
        seen = used.get(base, 0)
        used[base] = seen + 1
        return base if seen == 0 else '%s#%d' % (base, seen + 1)

    def _find(self, records: list[dict], current_id) -> Optional[int]:
        wanted = str(current_id or '').strip()
        if not wanted:
            return None
        for index, record in enumerate(records):
            if record['id'] == wanted:
                return index
        path = _norm_path(wanted)
        for index, record in enumerate(records):
            if _norm_path(record['task'].get('path')) == path:
                return index
        for index, record in enumerate(records):
            if str(record['task'].get('branch') or '').strip() == wanted:
                return index
        folded = wanted.casefold()
        for index, record in enumerate(records):
            if _as_str(record['task'].get('title')).strip().casefold() == folded:
                return index
        return None

    def _micro_step(self, record: dict, records: list[dict]) -> MicroStep:
        task = record['task']
        context = _as_str(task.get('context') or task.get('description') or task.get('body'))[:CONTEXT_LIMIT].strip()
        title = (_as_str(task.get('title') or task.get('name') or task.get('label') or task.get('text')).strip()
                 or context.split('\n', 1)[0][:120]
                 or str(task.get('path') or record['id']))
        accept = _accept_list(task.get('accept') or task.get('acceptance'))
        explicit = _explicit_caps(task, set(self.capabilities))
        claims = task.get('claims') or []
        if isinstance(claims, str):
            claims = [claims]
        markers = self._markers(title, context, claims)
        return MicroStep(
            step_id=record['id'],
            title=title,
            parent_plan_task_id=str(record['parent'] or ''),
            plan_version=_plan_version(task),
            cap=tuple(self._inherited_caps(record, records)),
            category_hint=self.multi_marker_hint(title, context, [str(item) for item in claims],
                                                 accept=accept, capabilities=sorted(explicit)),
            markers=markers,
            context=context,
        )

    def _marks(self, title: str, context: str, claims) -> tuple[set, set, set]:
        haystack = _text(' '.join([str(title or ''), str(context or ''),
                                   *[str(item) for item in claims or ()]]))
        return self._rule_hits(haystack), self._actions(haystack), self._domains(haystack)

    def _actions(self, haystack: str) -> set:
        return {name for name, stems in ACTION_MARKERS.items() if any(_match_stem(stem, haystack) for stem in stems)}

    def _domains(self, haystack: str) -> set:
        return {category for category, stems in DOMAIN_STEMS.items() if any(_match_stem(stem, haystack) for stem in stems)}

    def _markers(self, title: str, context: str, claims) -> tuple[str, ...]:
        groups, actions, domains = self._marks(title, context, claims)
        markers = set(groups)
        markers.update('action:' + name for name in actions)
        markers.update('domain:' + category for category in domains)
        return tuple(sorted(markers))

    def _own_caps(self, task: dict) -> set:
        caps, _unmapped = self.router.capabilities_from_accept(_accept_list(task.get('accept') or task.get('acceptance')))
        allowed = set(self.capabilities)
        caps = {cap for cap in caps if cap in allowed}
        caps.update(_explicit_caps(task, allowed))
        return caps

    def _inherited_caps(self, record: dict, records: list[dict]) -> set:
        """Own caps plus the plan item's (pi) caps, walked up the parent chain."""
        by_id = {item['id']: item for item in records}
        caps, seen, node = set(), set(), record
        while node is not None and node['id'] not in seen and len(seen) < MAX_ANCESTRY:
            seen.add(node['id'])
            caps.update(self._own_caps(node['task']))
            node = by_id.get(node['parent']) if node['parent'] else None
        return caps


@dataclass(frozen=True)
class BlockFingerprint:
    """tau-gate state: identity of the injected skill block.

    The fingerprint deliberately excludes ``idle_reissue_count``: an idle re-issue must not
    look like a legitimate change.  There is no total cap on legitimate re-emissions (H1);
    only idle re-issues are counted, so a real change never leaks into the counter.
    """

    step_id: str
    cap_set: frozenset[str]
    category_hint: str
    markers: frozenset[str]
    plan_version: int
    index_digest: str
    idle_reissue_count: int = 0

    def __post_init__(self):
        object.__setattr__(self, 'step_id', str(self.step_id))
        object.__setattr__(self, 'cap_set', frozenset(str(cap) for cap in self.cap_set))
        object.__setattr__(self, 'category_hint', str(self.category_hint or ''))
        object.__setattr__(self, 'markers', frozenset(str(marker) for marker in self.markers))
        object.__setattr__(self, 'plan_version', int(self.plan_version))
        object.__setattr__(self, 'index_digest', str(self.index_digest or ''))
        if int(self.idle_reissue_count) < 0:
            raise ValueError('idle_reissue_count cannot be negative')
        object.__setattr__(self, 'idle_reissue_count', int(self.idle_reissue_count))

    def _material(self) -> tuple:
        return (self.step_id, tuple(sorted(self.cap_set)), self.category_hint,
                tuple(sorted(self.markers)), self.plan_version, self.index_digest)

    def should_reemit(self, previous: Optional['BlockFingerprint']) -> bool:
        """True only when the fingerprint materially changed (first emission included).

        An unchanged fingerprint is an idle re-issue: this returns False and the caller
        carries the increment returned by ``advance``.
        """
        if previous is None:
            return True
        return self._material() != previous._material()

    def advance(self, previous: Optional['BlockFingerprint']) -> 'BlockFingerprint':
        """Return the state to carry after this tau turn.

        Changed (or first) fingerprint -> ``idle_reissue_count=0``; unchanged -> previous
        count + 1.  The dataclass stays frozen, so the counter travels in the returned
        value instead of mutating in place.
        """
        if previous is None or self.should_reemit(previous):
            return replace(self, idle_reissue_count=0)
        return replace(self, idle_reissue_count=previous.idle_reissue_count + 1)

    def digest(self) -> str:
        """Stable sha256 of the fingerprint core (idle count excluded)."""
        payload = {
            'step_id': self.step_id,
            'cap_set': sorted(self.cap_set),
            'category_hint': self.category_hint,
            'markers': sorted(self.markers),
            'plan_version': self.plan_version,
            'index_digest': self.index_digest,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def from_step(cls, step: MicroStep, index_digest: str,
                  plan_version: Optional[int] = None,
                  cap=None) -> 'BlockFingerprint':
        """Build a fingerprint from a sigma; ``cap`` defaults to the inherited cap set."""
        return cls(
            step_id=step.step_id,
            cap_set=frozenset(step.cap if cap is None else cap),
            category_hint=step.category_hint,
            markers=frozenset(step.markers),
            plan_version=step.plan_version if plan_version is None else int(plan_version),
            index_digest=str(index_digest or ''),
        )


def skill_index_digest(skills) -> str:
    """Canonical sha256 of a skill index (id + declared digest), order-independent.

    The index digest is the third invalidation key of the tau gate: when the curated
    index changes, the block may legitimately change even for the same sigma.
    """
    rows = []
    for skill in skills or ():
        if isinstance(skill, dict):
            rows.append([str(skill.get('id') or ''), str(skill.get('digest') or skill.get('sha256') or '')])
        else:
            rows.append([str(skill), ''])
    rows.sort()
    encoded = json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()
