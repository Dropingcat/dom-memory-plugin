"""Distil the plan discussion into per-item contexts (stage 2a+2b).

A plan file is already a distillation of the conversation.  The *context* of a
plan item is the part of the conversation that explains why the item exists:
the requirement it answers, the constraint it respects, the finding that
motivated it.  The plan hook used to send only the plan text, so every header
claim was a bare title; this module extracts the context without a model.

The extraction is deterministic and auditable:

* the discussion is split into sentence-sized units (paragraphs, then
  sentences; list markers and role prefixes are stripped);
* a unit is matched to a plan item by the words its title and body share with
  the unit (crude 4-5 character prefixes absorb Russian inflection);
* a word that occurs in many item titles weighs less than a word unique to
  one item (document frequency), so "план" cannot attach a unit to everything;
* a unit that matches four or more items is the whole plan said again, so it
  is kept for its best item only; a unit that explains two neighbouring items
  is kept for both;
* each item keeps at most a few units, in the order they were said.

Nothing here calls a model, and a failure never blocks the plan: the caller
falls back to the plan without contexts.
"""
import re

from . import planning

_WORD = re.compile(r'[0-9a-zа-я]+', re.UNICODE)
_SENTENCE = re.compile(r'(?<=[.!?…])\s+')
_LIST_MARK = re.compile(r'^\s*(?:[-*+•—–]|\d+[.)])\s+')
_ROLE_PREFIX = re.compile(r'^(?:user|assistant|пользователь|ассистент)\s*:\s*', re.IGNORECASE)

# Function words of Russian and English plus words too generic to identify an
# item ("план", "задача"): they are dropped before matching.
STOP = frozenset("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между
план плане плана планы плану планом задач задача задачи задач пункт пункты нужно надо также этот эта эти это который которая которые
the a an and or but if then else for of to in on at by with from as is are was were be been being it its this that these those we you they he she i not no yes do does did done can could should would will may might must have has had into about over under after before more most less least very just only also than there here what which who whom whose when where why how all any some each every both few many much own same so such nor too s t don now
""".split())

MIN_UNIT = 24            # shorter fragments ("Да.", "OK.") carry no context
MAX_UNIT = 900           # a unit is a sentence, not a wall of text
MAX_UNITS_PER_ITEM = 3
MAX_CONTEXT_CHARS = 700  # one context is a commit message, not a transcript
RECAP_MATCHES = 4        # a unit that fits 4+ items is the plan repeated back
MIN_SCORE = 1.0          # one word unique to the item's title is enough


def _norm(text):
    return text.casefold().replace('ё', 'е')


def _tokens(text):
    """Words that can identify an item: no stopwords, no 1-2 letter words (ids kept)."""
    out = []
    for word in _WORD.findall(_norm(text)):
        if any(ch.isdigit() for ch in word):
            if len(word) >= 2:
                out.append(word)
            continue
        if len(word) < 3 or word in STOP:
            continue
        out.append(word)
    return out


def _same(a, b):
    """Same word up to inflection: a 4-5 character shared prefix."""
    if a == b:
        return True
    shortest = min(len(a), len(b))
    if shortest >= 5:
        return a[:5] == b[:5]
    if shortest >= 4:
        return a[:4] == b[:4]
    return False


def _hits(unit_tokens, target_tokens):
    return {t for t in target_tokens if any(_same(t, u) for u in unit_tokens)}


def split_units(discussion):
    """Discussion text -> sentence units with list markers and role prefixes removed."""
    units = []
    for block in re.split(r'\n\s*\n', discussion.replace('\r\n', '\n')):
        lines = [_LIST_MARK.sub('', line) for line in block.splitlines()]
        block = ' '.join(line.strip() for line in lines if line.strip())
        if not block:
            continue
        for sentence in _SENTENCE.split(block):
            sentence = _ROLE_PREFIX.sub('', re.sub(r'\s+', ' ', sentence).strip()).strip()
            if len(sentence) < MIN_UNIT:
                continue
            while len(sentence) > MAX_UNIT:  # a run-on sentence: keep both halves useful
                units.append(sentence[:MAX_UNIT].strip())
                sentence = sentence[MAX_UNIT:].strip()
            if len(sentence) >= MIN_UNIT:
                units.append(sentence)
    return units


def distill_contexts(plan, discussion, max_units=MAX_UNITS_PER_ITEM, max_chars=MAX_CONTEXT_CHARS):
    """-> {plan item path: context text} for the items the discussion explains.

    An empty dict is a normal answer: no discussion, no match, or a plan that
    could not be parsed (the ingest itself reports the parse error).
    """
    if not isinstance(discussion, str) or not discussion.strip():
        return {}
    try:
        _, roots = planning.parse_plan(plan)
        tasks = planning.flatten(roots)
    except Exception:  # an unparseable plan is the ingest's error to report, not ours
        return {}
    if not tasks:
        return {}
    for task in tasks:
        task['_title_tokens'] = _tokens(task['title'])
        task['_body_tokens'] = _tokens(task.get('body') or '')
    frequency = {}
    for task in tasks:
        for token in set(task['_title_tokens']) | set(task['_body_tokens']):
            frequency[token] = frequency.get(token, 0) + 1

    assigned = {}  # path -> [(position, score, unit)]
    for position, unit in enumerate(split_units(discussion)):
        unit_tokens = _tokens(unit)
        if not unit_tokens:
            continue
        scored = []
        for task in tasks:
            title_hits = _hits(unit_tokens, task['_title_tokens'])
            if not title_hits:  # the body alone is too weak to claim a sentence
                continue
            body_hits = _hits(unit_tokens, task['_body_tokens'])
            score = (sum(1.0 / frequency.get(t, 1) for t in title_hits)
                     + 0.5 * sum(1.0 / frequency.get(t, 1) for t in body_hits))
            if score >= MIN_SCORE:
                scored.append((score, task['path']))
        if not scored:
            continue
        scored.sort(key=lambda row: (-row[0], row[1]))
        best_score = scored[0][0]
        # A sentence can genuinely explain two neighbouring items.  A unit that
        # fits four or more is the plan repeated back: keep it for its best item
        # only, so a recap cannot become the context of half the tree.  A much
        # weaker match is noise, not context.
        limit = 1 if len(scored) >= RECAP_MATCHES else 2
        for score, path in scored[:limit]:
            if score >= best_score * 0.75:
                assigned.setdefault(path, []).append((position, score, unit))

    contexts = {}
    for path, rows in assigned.items():
        rows.sort(key=lambda row: (-row[1], row[0]))
        chosen, used = [], 0
        for position, _score, unit in rows:
            if len(chosen) >= max_units:
                break
            if used + len(unit) + 1 > max_chars:
                continue
            chosen.append((position, unit))
            used += len(unit) + 1
        if chosen:
            chosen.sort(key=lambda row: row[0])  # say it in the original order
            contexts[path] = ' '.join(unit for _, unit in chosen)
    return contexts
