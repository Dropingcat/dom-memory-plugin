"""Deterministic step -> skill selection for the 0.3.1 router.

The router has no model: a plan step is normalized into signals, each skill gets a score from
priors, keyword rules, claim types and learned weights, and the top-k become the step queue.
Everything is explainable: every selected skill carries the terms of its score.

This module is pure (no DB, no I/O) so it can be tested on artificial steps and reused by the
plugin context hook, the prompt hook and the panel.
"""
from __future__ import annotations

import hashlib

PRIORS = {
    'phase': 0.25,
    'task': 0.35,
}

# keyword group -> tags it votes for
RULES = {
    'migration': ('migrat', 'миграц', 'schema', 'схем', 'v5', 'columns', 'таблиц'),
    'testing': ('test', 'тест', 'check', 'провер', 'assert', 'pytest', 'node --test'),
    'review': ('review', 'ревью', 'аудит', 'audit', 'diff', 'находк'),
    'docs': ('docs', 'докум', 'readme', 'spec', 'концепц', 'schema.md'),
    'prompt': ('prompt', 'промт', 'инъекц', 'inject', 'brief', 'notes'),
    'planning': ('plan', 'план', 'step', 'шаг', 'queue', 'очеред'),
    'writing': ('report', 'отчёт', 'статья', 'article', 'текст', 'prose'),
    'git': ('git', 'commit', 'коммит', 'branch', 'ветк', 'push'),
    'ledger': ('claim', 'клайм', 'ledger', 'appendix', 'hash', 'chain', 'sqlite'),
    'security': ('security', 'безопасн', 'gate', 'гейт', 'trust', 'session_source', 'mcp-unverified'),
}

RULE_WEIGHT = 0.40
CLAIM_WEIGHT = 0.10
LEARNED_WEIGHT = 0.40
PRIOR_WEIGHT = 0.30
BANNED_PENALTY = -0.60
FORCED_SCORE = 1.50
FALLBACK_BELOW = 0.45
DEFAULT_K = 3

# Closed capability dictionary for 0.3.1. A capability that is not here cannot be routed.
CAPABILITIES = (
    'testing', 'sqlite', 'migration', 'ledger', 'docs', 'ops', 'runtime',
    'security', 'router', 'prompt', 'writer', 'review', 'git', 'panel',
)

# accept kind -> capabilities it implies unless the item names them explicitly
ACCEPT_RULES = {
    'test': ('testing',),
    'artifact': ('docs',),
    'claim': ('ledger',),
    'ops': ('ops', 'runtime'),
    'schema': ('sqlite', 'migration'),
    'route': ('router', 'prompt'),
}


def capabilities_from_accept(accept):
    """Capabilities implied by acceptance items; unmapped items are reported, not guessed."""
    caps = set()
    unmapped = []
    for item in accept or []:
        if not isinstance(item, dict):
            unmapped.append('?')
            continue
        explicit = [str(cap) for cap in (item.get('capabilities') or [])]
        caps.update(cap for cap in explicit if cap in CAPABILITIES)
        kind = str(item.get('type') or '')
        if kind in ACCEPT_RULES:
            caps.update(ACCEPT_RULES[kind])
        elif not explicit:
            unmapped.append(str(item.get('id') or kind or '?'))
    return sorted(caps), unmapped


def step_state(step):
    """ready | awaiting_definition | awaiting_human | blocked_capability | blocked_provider."""
    accept = step.get('accept') or []
    if not accept:
        return {'state': 'awaiting_definition', 'capabilities': [], 'unmapped': [],
                'reason': 'no acceptance criteria; ask the user for what "done" means'}
    caps, unmapped = capabilities_from_accept(accept)
    if unmapped:
        return {'state': 'blocked_capability', 'capabilities': caps, 'unmapped': unmapped,
                'reason': 'accept item has no capability mapping; create a gap claim'}
    if not caps:
        return {'state': 'blocked_capability', 'capabilities': [], 'unmapped': [],
                'reason': 'accept mapped to an empty capability set'}
    if any(item.get('automated') is False for item in accept if isinstance(item, dict)):
        return {'state': 'awaiting_human', 'capabilities': caps, 'unmapped': [],
                'reason': 'accept item requires a human verdict'}
    return {'state': 'ready', 'capabilities': caps, 'unmapped': [], 'reason': 'accept is automated'}



def _text(value):
    return ' '.join(str(value or '').lower().split())


def normalize_step(step):
    """Accept a plan row plus optional header properties and linked claims."""
    return {
        'path': str(step.get('path') or step.get('plan_path') or '?'),
        'title': str(step.get('title') or ''),
        'task_type': str(step.get('task_type') or 'task'),
        'context': str(step.get('context') or ''),
        'boundaries': str(step.get('boundaries') or ''),
        'claims': [str(item) for item in (step.get('claims') or [])],
        'needs': [str(item) for item in (step.get('needs') or [])],
        'accept': list(step.get('accept') or []),
        'hints_skills': [str(item) for item in (step.get('hints_skills') or [])],
        'hints_no_skills': [str(item) for item in (step.get('hints_no_skills') or [])],
    }


def rule_groups(step):
    """Keyword groups present in the step text, with the matched word."""
    haystack = _text(' '.join([step['title'], step['context'], step['boundaries'], *step['claims']]))
    hits = {}
    for group, words in RULES.items():
        for word in words:
            if word in haystack:
                hits[group] = word
                break
    return hits


def coverage(step, skill):
    """Fraction of the step's rule groups that the skill's tags satisfy."""
    groups = rule_groups(step)
    if not groups:
        return 0.0
    tags = set(skill.get('tags') or [])
    matched = [group for group in groups if group in tags]
    return len(matched) / len(groups)


def needs_ratio(step, skill):
    needs = step.get('needs') or []
    if not needs:
        return None
    provides = set(skill.get('provides') or [])
    return len([need for need in needs if need in provides]) / len(needs)


def score(step, skill, weight=0.5):
    """Return (score, why) for one skill and one step."""
    sid = str(skill.get('id') or '')
    if sid in step['hints_no_skills']:
        return BANNED_PENALTY, ['banned by plan hint']
    if sid in step['hints_skills']:
        return FORCED_SCORE, ['forced by plan hint']
    why = []
    needs = step.get('needs') or []
    provides = set(skill.get('provides') or [])
    if needs:
        covered = [need for need in needs if need in provides]
        ratio = len(covered) / len(needs)
        value = 0.90 * ratio + 0.15 * PRIORS.get(step['task_type'], PRIORS['task']) + 0.30 * float(weight)
        why.append('needs=%s (%d/%d)' % (','.join(covered) or 'none', len(covered), len(needs)))
        why.append('weight=%.2f' % float(weight))
        return value, why
    value = PRIOR_WEIGHT * PRIORS.get(step['task_type'], PRIORS['task'])
    why.append('prior(%s)=%.2f' % (step['task_type'], PRIORS.get(step['task_type'], PRIORS['task'])))
    groups = rule_groups(step)
    tags = set(skill.get('tags') or [])
    matched = sorted(group for group in groups if group in tags)
    if matched:
        value += RULE_WEIGHT * (len(matched) / max(1, len(groups)))
        why.append('rules=' + ','.join(matched))
    claim_groups = {group for group in groups if group in tags and any(group in _text(claim) for claim in step['claims'])}
    if claim_groups:
        value += CLAIM_WEIGHT * (len(claim_groups) / max(1, len(groups)))
        why.append('claims=' + ','.join(sorted(claim_groups)))
    value += LEARNED_WEIGHT * max(0.0, min(1.0, float(weight)))
    why.append('weight=%.2f' % float(weight))
    return value, why


def _explore(step):
    digest = hashlib.sha256(step['path'].encode('utf-8')).digest()
    return digest[0] % 10 == 0


def select(step, skills, weights=None, k=DEFAULT_K, explore=None):
    """Top-k skills for one step, with scores and reasons; deterministic."""
    weights = weights or {}
    rows = []
    for skill in skills:
        sid = str(skill.get('id') or '')
        if not sid:
            continue
        if step.get('needs') and needs_ratio(step, skill) == 0:
            continue
        value, why = score(step, skill, weights.get(sid, 0.5))
        rows.append({'id': sid, 'score': round(value, 4), 'why': why, 'tags': sorted(skill.get('tags') or [])})
    rows.sort(key=lambda row: (-row['score'], row['id']))
    if explore is None:
        explore = _explore(step)
    picked = rows[:k]
    if explore and len(rows) > k:
        picked = rows[:k - 1] + [rows[k]]
        picked[-1] = dict(picked[-1], why=picked[-1]['why'] + ['exploration'])
    confidence = picked[0]['score'] if picked else 0.0
    fallback = 'skill_search' if confidence < FALLBACK_BELOW else None
    return {
        'step': step['path'],
        'current': picked,
        'confidence': round(confidence, 4),
        'fallback': fallback,
        'groups': rule_groups(step),
    }


def build_queue(step, next_step, skills, weights=None, k=DEFAULT_K):
    """Queue for a step plus prefetch for the next one."""
    current = select(step, skills, weights, k)
    prefetch = select(next_step, skills, weights, k) if next_step else None
    return {
        'step': current['step'],
        'current': current['current'],
        'confidence': current['confidence'],
        'fallback': current['fallback'],
        'prefetch': [item['id'] for item in (prefetch['current'] if prefetch else [])],
        'next': prefetch['step'] if prefetch else None,
        'groups': current['groups'],
    }
