"""Per-variable mini-prompts and deterministic distillation for dom.notes.

Every notes variable has one spec: the mini-prompt that tells an agent what the
value must say, the markers that select the relevant sentence, a hard limit and
the order of sources.  The runtime never calls a model: it takes the curated
property, cuts it on a sentence boundary, and falls back to the plan header
context and the latest linked work claim.  A missing value stays
``not recorded``; the promptizer never invents one.

Full texts stay in DOM: the brief carries the distilled thesis plus an anchor.
"""
from __future__ import annotations

from . import planning

SPECS = {
    'current_meaning': {
        'prompt': 'One sentence: what this block covers, named by its own title and blocks.',
        'limit': 240,
        'markers': ('задач', 'task', 'блок', 'notes', 'schema', 'проблем', 'аудит', 'срез', 'есть', 'инвентариза'),
        'roles': ('implements', 'documents', 'validates'),
    },
    'why': {
        'prompt': 'One sentence: why this block matters for the project goal.',
        'limit': 240,
        'markers': ('помога', 'why', 'нужн', 'цель', 'чтобы', 'для чего', 'зачем', 'purpose'),
        'roles': ('documents', 'implements', 'justifies'),
    },
    'now': {
        'prompt': 'One sentence: what is being done right now, in the current step.',
        'limit': 240,
        'markers': ('сейчас', 'сейчас ', 'now', 'идёт', 'идет', 'делаю', 'планиру', 'текущ', 'шаг', 'этап', 'стадия'),
        'roles': ('implements', 'validates', 'documents'),
    },
    'vector': {
        'prompt': 'One sentence: the path from the current anchor to the finished block.',
        'limit': 260,
        'markers': ('собрать', 'vector', 'порядок', 'затем', 'далее', 'после'),
        'roles': ('implements', 'documents'),
    },
    'boundaries': {
        'prompt': 'One sentence: what this block must not change.',
        'limit': 240,
        'markers': ('не ', 'только', 'boundaries', 'границ'),
        'roles': ('documents',),
    },
    'tools': {
        'prompt': 'Comma-separated tool names used by the current block.',
        'limit': 240,
        'markers': (),
        'roles': (),
    },
    'gits': {
        'prompt': 'Repository, branch and last known commit of the current block.',
        'limit': 240,
        'markers': (),
        'roles': (),
    },
}

CLAUSE_MARKERS = {
    'current_meaning': ('Текущая задача:', 'Task:', 'Базовый срез:', 'Анализ:', 'Сбор:'),
    'why': ('Зачем:', 'Why:', 'Цель:', 'Необходимо для:', 'Для чего:'),
    'now': ('Сейчас:', 'Now:', 'Текущий шаг:', 'Этап:', 'Шаг:'),
    'vector': ('Вектор:', 'Vector:', 'Далее:', 'После:', 'Порядок:'),
    'boundaries': ('Границы:', 'Boundaries:', 'Не:', 'Только:'),
    'tools': ('Инструменты:', 'Tools:'),
    'gits': ('Гиты:', 'Gits:'),
}


def sentences(text):
    """Split on ., !, ? and line breaks; keep the terminator."""
    out, buf = [], ''
    for char in text or '':
        buf += char
        if char in '.!?\n':
            piece = ' '.join(buf.split())
            if piece:
                out.append(piece)
            buf = ''
    piece = ' '.join(buf.split())
    if piece:
        out.append(piece)
    return out


def _clip(text, limit):
    text = ' '.join(str(text or '').split())
    if len(text) <= limit:
        return text
    cut = text[:max(1, limit - 1)]
    space = cut.rfind(' ')
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(' ,;:.') + '…'


def distill(text, limit=240, markers=()):
    """Cut a long value on a sentence boundary, preferring a marked sentence."""
    text = ' '.join(str(text or '').split())
    if not text:
        return ''
    if len(text) <= limit:
        return text
    parts = sentences(text)
    lowered = [part.lower() for part in parts]
    for marker in markers:
        marker = marker.lower()
        for index, part in enumerate(lowered):
            if marker in part:
                picked = parts[index]
                if len(picked) <= limit:
                    return picked
                return _clip(picked, limit)
    if parts:
        return _clip(parts[0], limit)
    return _clip(text, limit)


def clause(context, markers):
    """Return the text after the first marker in a plan header context."""
    text = ' '.join(str(context or '').split())
    for marker in markers:
        position = text.find(marker)
        if position < 0:
            continue
        tail = text[position + len(marker):].strip()
        first = sentences(tail)[0] if tail else ''
        if first:
            return first
    return ''


def linked_claim(c, task_id, roles, limit):
    """Latest linked work claim as (text, claim_id), or ('', '')."""
    if not roles:
        return '', ''
    placeholders = ','.join('?' * len(roles))
    row = c.execute(
        'SELECT cl.id FROM task_claims tc JOIN claims cl ON cl.id=tc.claim_id '
        'WHERE tc.plan_task_id=? AND tc.role IN (%s) ORDER BY cl.rowid DESC LIMIT 1' % placeholders,
        (task_id,) + tuple(roles),
    ).fetchone()
    if row is None:
        return '', ''
    return distill(planning._claim_text(c, row[0]) or '', limit), row[0]


def linked_claim_text(c, task_id, roles, limit):
    return linked_claim(c, task_id, roles, limit)[0]


KEYS = {
    'current_meaning': 'meaning',
    'why': 'why',
    'now': 'now',
    'vector': 'vector',
    'boundaries': 'boundaries',
    'tools': 'tools',
    'gits': 'gits',
}


def resolve(c, name, props, task_id, context='', anchor=''):
    """Resolve one variable and report where the value came from."""
    spec = SPECS[name]
    limit = spec['limit']
    raw = props.get(KEYS[name])
    if raw:
        return {'text': distill(raw, limit, spec['markers']), 'source': 'property', 'anchor': anchor}
    from_context = clause(context, CLAUSE_MARKERS.get(name, ()))
    if from_context:
        return {'text': distill(from_context, limit, spec['markers']), 'source': 'context', 'anchor': anchor}
    text, claim_id = linked_claim(c, task_id, spec.get('roles') or (), limit)
    if text:
        return {'text': text, 'source': 'claim', 'anchor': claim_id}
    # Fallback (2026-11-28): any linked claim regardless of role or marker.
    # Without this the chain breaks: model writes claims but promptizer never
    # finds them because markers don't match, so step context stays "not recorded".
    fallback = c.execute(
        'SELECT tc.role, cl.id FROM task_claims tc JOIN claims cl ON cl.id=tc.claim_id '
        'WHERE tc.plan_task_id=? AND tc.role!=\'defines\' ORDER BY cl.rowid DESC LIMIT 1',
        (task_id,),
    ).fetchone()
    if fallback:
        text = distill(planning._claim_text(c, fallback['id']) or '', limit, spec['markers'])
        if text:
            return {'text': text, 'source': 'claim_fallback', 'anchor': fallback['id']}
    return {'text': '', 'source': 'none', 'anchor': ''}


def value(c, name, props, task_id, context=''):
    """Resolve one variable to plain text: property, context clause, linked claim."""
    return resolve(c, name, props, task_id, context)['text']


def render(c, name, props, task_id, context='', default='not recorded'):
    return value(c, name, props, task_id, context) or default


def mini_prompt(name):
    """The mini-prompt an agent follows to curate this variable."""
    spec = SPECS[name]
    return '%s Max %d chars; anchor it to the current task claim.' % (spec['prompt'], spec['limit'])
