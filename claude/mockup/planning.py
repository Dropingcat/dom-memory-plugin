"""Plan memory: a plan becomes a tree of claims, each plan item its own branch.

docs/PLAN_MCP_PROJECT_MEMORY_2026-11-12.md §2.1-2.2.

* A plan (markdown outline, JSON tree or a TodoWrite list) is parsed into items.
* Items are projected append-only (``Service.project_plan_snapshot``, D1.1).
* Every item gets a branch code allocated once (``plan_branches``):
  П1=AA, П2=AB ...; П1.1=AA100, П1.2=AA200; П1.1.1=AA110; П1.1.1.1=AA111;
  deeper levels append ``-<digit>``; variants (fork) append ``-V<n>``.
* Every item becomes a *header claim* (type ``decision``) in its branch, linked
  through ``task_claims`` with role ``defines``; the conversation context and
  the plan path are ``property`` events of that claim.
* A changed item text produces a new header version (replacement), never an
  edit.  A removed item is archived in the projection; its claims stay.

All claim/event writes go through ``claim_entry`` -> submit -> review -> admit;
this module only writes the DOM-owned index tables (``plan_branches``,
``task_claims``, ``plan_checks``) and declares branch heads.
"""
import json
import re
from datetime import datetime, timezone

from . import claim_entry as ce
from .dom_contract import digest, raw_hash

PLAN_PREFIX = 'П'  # 'П'
DIGITS = '123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
ROLES = ('defines', 'justifies', 'implements', 'validates', 'documents', 'depends_on')
CLAIM_ROLES = ROLES[1:]  # 'defines' is reserved for the header claim
STATUS_MAP = {'x': 'done', 'X': 'done', ' ': 'pending', 'completed': 'done', 'done': 'done',
              'in_progress': 'in_progress', 'pending': 'pending', 'todo': 'pending', 'blocked': 'blocked',
              'cancelled': 'cancelled', 'canceled': 'cancelled'}
MAX_ITEMS = 2000


def _denied():
    from .admission import Denied
    return Denied


def _now():
    return datetime.now(timezone.utc).isoformat()


# ================================================================ parsing ==
_ITEM = re.compile(r'^(?P<indent>[ \t]*)(?:(?P<bullet>[-*+])\s+)?(?:\[(?P<box>[ xX])\]\s+)?'
                   r'(?:(?P<prefix>[ПпPp])\.?\s?)?(?P<num>\d+(?:\.\d+)*)[.)]?\s+(?P<title>\S.*?)\s*$')
_BULLET = re.compile(r'^(?P<indent>[ \t]*)(?P<bullet>[-*+])\s+(?:\[(?P<box>[ xX])\]\s+)?(?P<title>\S.*?)\s*$')
_HEADING = re.compile(r'^(?P<hashes>#{1,6})\s+(?P<title>\S.*?)\s*#*\s*$')


_NUMBERED = re.compile(r'^\s*(?:[ПпPp]\.?\s?)?(?:\d+(?:\.\d+)+\.?|\d+[.)])\s+\S')  # "1. x", "1.2 x", "П1.2 x"; not "2026 год"
_EMPHASIS = re.compile(r'(\*\*|__)(?P<inner>\S(?:.*?\S)?)\1')


def _clean_title(title):
    """Drop markdown emphasis around (parts of) an item title: '**Собрать** требования' -> 'Собрать требования'."""
    return _EMPHASIS.sub(lambda m: m.group('inner'), title).strip()


def _node(title, status='pending', ident=None, body=None, purpose=None):
    return {'title': _clean_title(title), 'status': status, 'id': ident, 'body': list(body or []),
            'purpose': purpose, 'children': []}


_CHECKBOX = re.compile(r'^\s*[-*+]\s+\[[ xX]\]\s+\S')


def _split_sections(text):
    """-> (notes, item_text) for a plan written the plan-mode way (QA004 F-01):
    ``## Context`` (prose), ``## Steps`` (1., 2. ...), ``## Verification`` (prose).
    Then headings are sections, not plan items: sections without task lines
    (numbered or checkbox) go to the notes (preamble), the task sections are
    joined without their headings.  When every non-empty section holds tasks
    (``## Phase A`` + checkboxes, ``## Phase B`` + 1.), or there are no task
    lines, headings stay plan items -> (None, text)."""
    sections, current, in_fence = [], {'heading': None, 'lines': [], 'tasks': False, 'filled': False}, False
    for line in text.splitlines():
        if line.strip().startswith('```'):
            in_fence = not in_fence
        elif not in_fence and _HEADING.match(line.rstrip()):
            sections.append(current)
            current = {'heading': line.rstrip(), 'lines': [], 'tasks': False, 'filled': False}
            continue
        elif not in_fence and (_NUMBERED.match(line) or _CHECKBOX.match(line)):
            current['tasks'] = True
        if line.strip():
            current['filled'] = True
        current['lines'].append(line)
    sections.append(current)
    headed = [s for s in sections if s['heading'] is not None and s['filled']]
    if not any(s['tasks'] for s in sections) or not any(not s['tasks'] for s in headed):
        return None, text
    notes, items = [], []
    for s in sections:
        if s['tasks']:
            if s['heading']:
                notes.append(s['heading'])
            items.extend(s['lines'])
        else:
            notes.extend(([s['heading']] if s['heading'] else []) + s['lines'])
    return notes, '\n'.join(items)


def parse_markdown(text):
    """Markdown plan -> (preamble_lines, root nodes).

    Items are numbered lines (``1.``, ``1.2``, ``П1.2``), bullets and headings.
    Nesting: a heading nests by its level; a dotted number gives the depth of
    the item under its heading (``1.2.3`` = 3); any other item nests by
    indentation.  Other lines are the body of the item above them (code fences
    are kept verbatim).  ``[x]`` marks a done item.  When the plan has numbered
    items, headings are sections, not items (``_split_sections``).
    """
    notes, text = _split_sections(text)
    if notes is not None:
        preamble, roots = _parse_outline(text)
        return [n.rstrip() for n in notes if n.strip()] + preamble, roots
    return _parse_outline(text)


def _parse_outline(text):
    roots, preamble = [], []
    stack = []  # dicts: {'node', 'heading': level|None, 'depth', 'indent'}
    last = None
    in_fence = False

    def attach(entry):
        parent = stack[-1]['node'] if stack else None
        (parent['children'] if parent else roots).append(entry['node'])
        stack.append(entry)

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith('```'):
            in_fence = not in_fence
            (last['body'] if last else preamble).append(line)
            continue
        if in_fence:
            (last['body'] if last else preamble).append(line)
            continue
        if not line.strip():
            if last is not None and last['body'] and last['body'][-1] != '':
                last['body'].append('')
            continue
        m = _HEADING.match(line)
        if m:
            level = len(m.group('hashes'))
            while stack and (stack[-1]['heading'] is None or stack[-1]['heading'] >= level):
                stack.pop()
            node = _node(m.group('title'))
            node['heading'] = True
            attach({'node': node, 'heading': level, 'depth': 0, 'indent': -1})
            last = node
            continue
        m = _ITEM.match(line)
        mm = m or _BULLET.match(line)
        if mm:
            indent = len(mm.group('indent').replace('\t', '    '))
            box = mm.group('box')
            node = _node(mm.group('title'), STATUS_MAP.get(box, 'pending') if box is not None else 'pending')
            if m:
                node['num'] = int(m.group('num').split('.')[-1])
            if m and '.' in m.group('num'):
                depth = m.group('num').count('.') + 1
                while stack and stack[-1]['heading'] is None and stack[-1]['depth'] >= depth:
                    stack.pop()
            else:
                while stack and stack[-1]['heading'] is None and stack[-1]['indent'] >= indent:
                    stack.pop()
                depth = stack[-1]['depth'] + 1 if stack and stack[-1]['heading'] is None else 1
            attach({'node': node, 'heading': None, 'depth': depth, 'indent': indent})
            last = node
            continue
        (last['body'] if last else preamble).append(line.strip() if last is None else line)
    # A single top heading with children is the plan's title, not an item.
    if len(roots) == 1 and roots[0].get('heading') and roots[0]['children']:
        title = roots[0]
        preamble = ['# ' + title['title']] + preamble + title['body']
        roots = title['children']
    return preamble, roots


def parse_json(data):
    """JSON plan: a list, or {items|tasks|todos|plan: [...]}; item keys
    title|content|name|text, id, status, context|description|body|activeForm,
    purpose, children|subtasks|items|steps."""
    if isinstance(data, dict):
        for key in ('items', 'tasks', 'todos', 'plan', 'steps'):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise ValueError('PLAN_FORMAT')

    def one(entry):
        if isinstance(entry, str):
            return _node(entry)
        if not isinstance(entry, dict):
            raise ValueError('PLAN_FORMAT')
        title = next((entry[k] for k in ('title', 'content', 'name', 'text') if isinstance(entry.get(k), str) and entry[k].strip()), None)
        if title is None:
            raise ValueError('PLAN_FORMAT')
        body = next((entry[k] for k in ('context', 'description', 'body') if isinstance(entry.get(k), str) and entry[k].strip()), None)
        ident = entry.get('id') if isinstance(entry.get('id'), str) and entry['id'].strip() else None
        status = STATUS_MAP.get(str(entry.get('status', 'pending')), 'pending')
        purpose = entry.get('purpose') if isinstance(entry.get('purpose'), str) and entry['purpose'].strip() else None
        node = _node(title, status, ident, body.splitlines() if body else [], purpose)
        kids = next((entry[k] for k in ('children', 'subtasks', 'items', 'steps') if isinstance(entry.get(k), list)), [])
        node['children'] = [one(k) for k in kids]
        return node
    return [one(e) for e in data]


def parse_plan(plan, fmt='auto'):
    """-> (preamble_text, root nodes).  ``plan`` is markdown text or JSON (text or object)."""
    Denied = _denied()
    if fmt not in ('auto', 'markdown', 'json'):
        raise Denied('INVALID_FIELD', field='format', hint="'format' is auto, markdown or json.")
    obj = None
    if not isinstance(plan, str):
        obj = plan
    elif fmt == 'json' or (fmt == 'auto' and plan.lstrip()[:1] in ('{', '[')):
        try:
            obj = json.loads(plan)
        except ValueError:
            if fmt == 'json':
                raise Denied('INVALID_FIELD', field='plan', hint='The plan is not valid JSON.') from None
    try:
        if obj is not None:
            preamble, roots = '', parse_json(obj)
        else:
            lines, roots = parse_markdown(plan)
            preamble = '\n'.join(lines).strip()
    except ValueError:
        raise Denied('INVALID_FIELD', field='plan',
                     hint='Plan items need a title: markdown numbered/bulleted lines or headings, or JSON [{"title": ..., "children": [...]}].') from None
    if not roots:
        raise Denied('PLAN_EMPTY', field='plan',
                     hint='No plan items were found. Send numbered items (1., 1.1, П1.2), bullets or headings, or JSON.')
    return preamble, roots


def flatten(roots, previous=None):
    """Nodes -> ordered list of dicts with path, plan_task_id, parent_id, level.

    ``previous`` maps (parent_id, path) -> plan_task_id of the current
    projection.  An item without an explicit id whose title changed keeps the
    old identity when the old item at the same place disappeared from the plan
    (a rename, not a new task); children follow their parent.
    """
    if previous:
        naive = {t['plan_task_id'] for t in flatten(roots)}
    out, ids = [], set()

    def norm(title):
        return re.sub(r'\s+', ' ', title).strip().casefold()

    def numbers(nodes):
        # explicit numbers ("3.", "3.1") are kept when they strictly increase (F-40); else positions
        nums = [n.get('num') for n in nodes]
        if nums and all(isinstance(x, int) and x > 0 for x in nums) and all(a < b for a, b in zip(nums, nums[1:])):
            return nums
        return list(range(1, len(nodes) + 1))

    def walk(nodes, parent, prefix, level):
        seen = {}
        for index, node in zip(numbers(nodes), nodes):
            path = '%s%d' % (prefix, index) if prefix else '%s%d' % (PLAN_PREFIX, index)
            if node['id']:
                tid = node['id']
            else:
                base = {'parent': parent, 'title': norm(node['title'])}
                n = seen.get(base['title'], 0)
                seen[base['title']] = n + 1
                if n:
                    base['occurrence'] = n
                tid = 'pt-' + digest(base)[:16]
                if previous and tid not in previous.values():
                    old = previous.get((parent, path))
                    if old is not None and old not in naive and old not in ids:
                        tid = old
            if tid in ids:
                raise _denied()('INVALID_FIELD', field='plan', hint='Duplicate plan item id %r.' % tid[:80])
            ids.add(tid)
            body = '\n'.join(node['body']).strip()
            out.append({'plan_task_id': tid, 'parent_id': parent, 'path': path, 'level': level,
                        'title': node['title'], 'body': body, 'status': node['status'],
                        'purpose': node.get('purpose')})
            if len(out) > MAX_ITEMS:
                raise _denied()('INVALID_FIELD', field='plan', hint='At most %d plan items.' % MAX_ITEMS)
            walk(node['children'], tid, path + '.', level + 1)
    walk(roots, None, '', 1)
    return out


# ======================================================== branch codes ==
def level1_code(slot):
    slot -= 1
    if slot >= 26 * 26:
        raise _denied()('PLAN_TOO_WIDE', hint='At most 676 top-level plan items.')
    return chr(65 + slot // 26) + chr(65 + slot % 26)


def child_code(parent_code, parent_level, slot):
    """Code of the ``slot``-th child (1-based) of a branch at ``parent_level``."""
    if slot > len(DIGITS):
        raise _denied()('PLAN_TOO_WIDE', hint='At most %d sub-items per plan item.' % len(DIGITS))
    digit = DIGITS[slot - 1]
    if parent_level == 1:
        return parent_code + digit + '00'
    if parent_level in (2, 3):
        pos = 2 + parent_level - 1  # digit position inside the 3-digit block
        return parent_code[:pos] + digit + parent_code[pos + 1:]
    return parent_code + '-' + digit


def _allocate(c, task, parent_row):
    """Allocate (once) the branch code of a plan item.  -> branch code."""
    row = c.execute('SELECT branch FROM plan_branches WHERE plan_task_id=?', (task['plan_task_id'],)).fetchone()
    if row:
        return row[0]
    taken = {r[0] for r in c.execute('SELECT branch FROM branch_heads')} | {r[0] for r in c.execute('SELECT branch FROM plan_branches')}
    if parent_row is None:
        slot = (c.execute("SELECT COALESCE(MAX(slot),0) FROM plan_branches WHERE parent_branch IS NULL AND kind='item'").fetchone()[0])
        while True:
            slot += 1
            code = level1_code(slot)
            if code not in taken:
                break
        level = 1
    else:
        level = parent_row['level'] + 1
        slot = c.execute("SELECT COALESCE(MAX(slot),0) FROM plan_branches WHERE parent_branch=? AND kind='item'", (parent_row['branch'],)).fetchone()[0]
        while True:
            slot += 1
            code = child_code(parent_row['branch'], parent_row['level'], slot)
            if code not in taken:
                break
    c.execute('INSERT INTO plan_branches(plan_task_id,branch,parent_branch,level,slot,kind,allocated_at) VALUES(?,?,?,?,?,?,?)',
              (task['plan_task_id'], code, parent_row['branch'] if parent_row else None, level, slot, 'item', _now()))
    c.execute('INSERT OR IGNORE INTO branch_heads VALUES(?,0)', (code,))
    return code


def _allocate_variant(svc, base_branch, base_task_id):
    with svc.store.transaction() as c:
        n = c.execute("SELECT COUNT(*) FROM plan_branches WHERE parent_branch=? AND kind='variant'", (base_branch,)).fetchone()[0] + 1
        taken = {r[0] for r in c.execute('SELECT branch FROM branch_heads')}
        while '%s-V%d' % (base_branch, n) in taken:
            n += 1
        code = '%s-V%d' % (base_branch, n)
        level = c.execute('SELECT level FROM plan_branches WHERE branch=?', (base_branch,)).fetchone()
        c.execute('INSERT INTO plan_branches(plan_task_id,branch,parent_branch,level,slot,kind,allocated_at) VALUES(?,?,?,?,?,?,?)',
                  ('%s#V%d' % (base_task_id, n), code, base_branch, level[0] if level else 1, n, 'variant', _now()))
        c.execute('INSERT OR IGNORE INTO branch_heads VALUES(?,0)', (code,))
    svc.lite_sync()
    return code


# ============================================================ helpers ==
def _current(children, cid):
    seen = set()
    while cid in children and cid not in seen:
        seen.add(cid)
        cid = children[cid]
    return cid


def _header_claim(c, plan_task_id):
    row = c.execute("SELECT claim_id FROM task_claims WHERE plan_task_id=? AND role='defines' ORDER BY linked_at DESC, rowid DESC LIMIT 1",
                    (plan_task_id,)).fetchone()
    return _current(ce._children(c), row[0]) if row else None


def _claim_text(c, claim_id):
    row = c.execute('SELECT raw FROM claims WHERE id=?', (claim_id,)).fetchone()
    return bytes(row[0]).decode('utf-8', 'replace') if row else None


def _properties(c, claim_id):
    out = {}
    for (payload,) in c.execute("SELECT payload FROM events WHERE kind='property' AND claim_ref=? ORDER BY seq", (claim_id,)):
        try:
            value = json.loads(payload)['value']['value']
            out[value['name']] = value['value']
        except (ValueError, KeyError, TypeError):
            continue
    return out


def header_text(task):
    return task['title'] + ('\n\n' + task['body'] if task.get('body') else '')


def semantic_digest(task):
    return digest({'title': task['title'], 'description': task.get('body') or '', 'parent_id': task['parent_id'],
                   'purpose': task.get('purpose') or ''})


def content_digest(task):
    return digest({'path': task['path'], 'parent_id': task['parent_id'], 'status': task['status'], 'title': task['title'],
                   'description': task.get('body') or '', 'purpose': task.get('purpose') or ''})


def _append(svc, branch, events, reason, tag):
    """Admit an append package (property/archive/converge events) on ``branch``."""
    key_base = digest({'tag': tag, 'events': events})[:24]

    def build(revision):
        return ce._package(svc, branch, revision, events, 'plan-%s-%s-r%d' % (tag, key_base, revision), 'append',
                           ce.DEFAULT_ORIGIN_VERSION, reason)
    return ce._commit(svc, branch, build)


def _prop(local, claim_id, name, value, reason):
    return {'local_id': local, 'event_type': 'property', 'claim_ref': claim_id, 'reason': reason,
            'value': {'name': name, 'value': value}, 'supersedes': '', 'ref': 'plan:ingest'}


def link(svc, plan_task_id, claim_id, role):
    """DOM-owned link row (D1.1).  Never touched by plan sync; recoverable from anatomy.plan_ref."""
    with svc.store.transaction() as c:
        row = c.execute('SELECT version,semantic_digest FROM plan_task_versions WHERE plan_task_id=? ORDER BY version DESC LIMIT 1',
                        (plan_task_id,)).fetchone()
        if row is None:
            raise _denied()('TASK_NOT_FOUND', field='plan_task')
        c.execute('INSERT OR IGNORE INTO task_claims(plan_task_id,claim_id,role,linked_at_plan_version,linked_semantic_digest,linked_at) '
                  'VALUES(?,?,?,?,?,?)', (plan_task_id, claim_id, role, row['version'], row['semantic_digest'], _now()))


def resolve_task(c, ref):
    """Current projection row of a plan item by id, display path (П1.2) or branch code."""
    if type(ref) is not str or not ref.strip():
        return None
    ref = ref.strip()
    q = ('SELECT v.*, b.branch, b.level FROM current_plan_tasks v LEFT JOIN plan_branches b ON b.plan_task_id=v.plan_task_id '
         'WHERE %s ORDER BY v.archived, v.observed_at DESC LIMIT 1')
    row = c.execute(q % 'v.plan_task_id=?', (ref,)).fetchone()
    if row is None:
        alt = ref if not ref[:1].isdigit() else PLAN_PREFIX + ref
        alt = PLAN_PREFIX + alt[1:] if alt[:1] in ('P', 'p', 'п') else alt
        row = c.execute(q % 'v.path=?', (alt,)).fetchone()
    if row is None:
        row = c.execute(q % 'b.branch=?', (ref,)).fetchone()
    return row


def subtree_ids(c, plan_task_id):
    rows = c.execute('SELECT plan_task_id,parent_id FROM current_plan_tasks').fetchall()
    kids = {}
    for r in rows:
        kids.setdefault(r['parent_id'], []).append(r['plan_task_id'])
    out, todo = [], [plan_task_id]
    while todo:
        tid = todo.pop(0)
        if tid in out:
            continue
        out.append(tid)
        todo.extend(kids.get(tid, []))
    return out


def branches_of(c, plan_task_ids):
    ids = list(plan_task_ids)
    if not ids:
        return []
    ph = ','.join('?' * len(ids))
    item = [r[0] for r in c.execute('SELECT branch FROM plan_branches WHERE plan_task_id IN (%s)' % ph, ids)]
    variants = [r[0] for r in c.execute("SELECT branch FROM plan_branches WHERE kind='variant' AND parent_branch IN (%s)" % ','.join('?' * len(item)), item)] if item else []
    return item + variants


# ============================================================= ingest ==
def ingest(svc, plan, source='agent', fmt='auto', contexts=None, source_revision=None, mode='full',
           base_revision=None, force=False, session=None):
    """Project a plan, allocate branches, write/refresh header claims.

    ``base_revision`` (T-01, TD-DOM-58): the revision the caller's plan is based on.
    When the plan moved on since, the call is refused with PLAN_CONFLICT (nothing
    written) unless ``force``.  ``session`` (T-02, TD-DOM-57): the host session that
    sent the plan (hooks); the principal is the launch identity.  Both are recorded
    with the revision in ``plan_checks``."""
    Denied = _denied()
    ce._lite_only(svc)
    svc.scope()
    svc.capability('submit')
    svc.ready()
    from .storage import PLAN_SOURCES
    if source not in PLAN_SOURCES:
        raise Denied('INVALID_FIELD', field='source', hint="'source' is 'agent' (a plan from the agent/conversation) or 'OMH'.")
    if contexts is not None and (type(contexts) is not dict or any(type(k) is not str or type(v) is not str for k, v in contexts.items())):
        raise Denied('INVALID_FIELD', field='contexts', hint="'contexts' maps a plan path (П1.2) or item id to the conversation context text.")
    if mode not in ('full', 'partial'):
        raise Denied('INVALID_FIELD', field='mode')
    if base_revision is not None and (type(base_revision) is not str or not base_revision.strip()):
        raise Denied('INVALID_FIELD', field='base_revision', hint="'base_revision' is a source_revision such as 'r3' (dom_plan_status).")
    if type(force) is not bool:
        raise Denied('INVALID_FIELD', field='force')
    if session is not None and (type(session) is not str or not session.strip()):
        session = None
    if contexts and any(not v.strip() for v in contexts.values()):  # QA004 F-15
        raise Denied('INVALID_FIELD', field='contexts', hint="A context must be non-empty text; leave the item out of 'contexts' instead.")
    preamble, roots = parse_plan(plan, fmt)
    with svc.store.read() as c:
        svc.integrity_gate(c)  # F-23: no projection writes into a broken ledger
        previous = {(r['parent_id'], r['path']): r['plan_task_id'] for r in c.execute(
            'SELECT v.plan_task_id,v.parent_id,v.path FROM current_plan_tasks v JOIN plan_tasks t ON t.plan_task_id=v.plan_task_id '
            'WHERE t.source=? AND v.archived=0', (source,))}
    # partial: an item left out is not removed, so no rename heuristic (F-40)
    tasks = flatten(roots, previous if mode == 'full' else None)
    # QA004 F-15: a context key that names no item of this plan is reported, not dropped silently
    known = {t['path'] for t in tasks} | {t['plan_task_id'] for t in tasks}
    warnings = ['contexts[%r] names no item of this plan; it was not stored' % k[:40] for k in sorted(contexts or {}) if k not in known]
    # QA004 F-39: contexts are properties of the headers, not part of the plan revision:
    # resending the same plan without contexts is PLAN_UNCHANGED.
    snapshot_digest = digest({'source': source, 'tasks': tasks})
    with svc.store.read() as c:
        state = c.execute('SELECT source_revision,snapshot_digest FROM plan_projection_state WHERE source=?', (source,)).fetchone()
    if state is not None and state['snapshot_digest'] == snapshot_digest and source_revision in (None, state['source_revision']):
        actions = {}
        if contexts:
            with svc.store.read() as c:
                for t in tasks:
                    b = c.execute('SELECT branch FROM plan_branches WHERE plan_task_id=?', (t['plan_task_id'],)).fetchone()
                    t['branch'] = b[0] if b else None
                root = c.execute('SELECT request_claim_id FROM project_root WHERE id=1').fetchone()
            with svc.system():
                for t in tasks:
                    ctx = contexts.get(t['path']) or contexts.get(t['plan_task_id'])
                    if ctx and t['branch']:
                        actions[t['plan_task_id']] = _sync_header(svc, t, source, state['source_revision'], root[0] if root else None, ctx, tasks)
        record_check(svc, source, snapshot_digest, False, 'ingest', 'unchanged plan', session)
        out = _ingest_result(svc, source, state['source_revision'], snapshot_digest, False, tasks, actions)
        if warnings:
            out['warnings'] = warnings
        return out
    forced_over = None
    if base_revision is not None and state is not None and state['source_revision'] != base_revision:
        conflict = _conflict(svc, source, base_revision, state['source_revision'], tasks, mode, session, force)
        if not force:
            return conflict
        forced_over = state['source_revision']
    previous_headers = _previous_headers(svc, tasks)
    if source_revision is None:
        last = state['source_revision'] if state is not None else None
        n = int(re.findall(r'\d+', last)[-1]) if last and re.findall(r'\d+', last) else 0
        source_revision = 'r%d' % (n + 1)
    elif type(source_revision) is not str or not source_revision.strip():
        raise Denied('INVALID_FIELD', field='source_revision')
    projected = svc.project_plan_snapshot(source=source, source_revision=source_revision, snapshot_digest=snapshot_digest, mode=mode,
                                          tasks=[{'plan_task_id': t['plan_task_id'], 'source': source, 'external_ref': '%s:%s' % (source, t['plan_task_id']),
                                                  'path': t['path'], 'parent_id': t['parent_id'], 'status': t['status'], 'title': t['title'],
                                                  'task_type': 'phase' if t['level'] == 1 else 'task', 'description': t['body'],
                                                  'digest': content_digest(t), 'semantic_digest': semantic_digest(t)} for t in tasks])
    if projected.get('status') not in ('PROJECTED', 'ALREADY_PROJECTED'):
        return projected
    # branches: parents first (flatten order is pre-order)
    with svc.store.transaction() as c:
        for t in tasks:
            parent = c.execute('SELECT * FROM plan_branches WHERE plan_task_id=?', (t['parent_id'],)).fetchone() if t['parent_id'] else None
            t['branch'] = _allocate(c, t, parent)
    svc.lite_sync()
    actions = {}
    with svc.store.read() as c:
        root = c.execute('SELECT request_claim_id FROM project_root WHERE id=1').fetchone()
    root_claim = root[0] if root else None
    contexts = contexts or {}
    with svc.system():  # the plan mirror is the server's record of the plan, not an agent claim
        for t in tasks:
            actions[t['plan_task_id']] = _sync_header(svc, t, source, source_revision, root_claim,
                                                      contexts.get(t['path']) or contexts.get(t['plan_task_id']), tasks)
    record_check(svc, source, snapshot_digest, True, 'ingest',
                 'plan revision %s' % source_revision + (' (forced over %s)' % forced_over if forced_over else ''), session)
    reconcile_variants(svc)
    out = _ingest_result(svc, source, source_revision, snapshot_digest, True, tasks, actions, preamble)
    for row in out['tasks']:  # T-01: an overwrite is visible: whose version this one replaced
        if row['action'] == 'updated' and row['plan_task_id'] in previous_headers:
            row['previous'] = previous_headers[row['plan_task_id']]
    if warnings:
        out['warnings'] = warnings
    return out


def _sync_header(svc, t, source, revision, root_claim, context, tasks):
    """Create or refresh the header claim of one plan item. -> action string."""
    with svc.store.read() as c:
        current = _header_claim(c, t['plan_task_id'])
        parent_header = _header_claim(c, t['parent_id']) if t['parent_id'] else None
        text_now = _claim_text(c, current) if current else None
        props = _properties(c, current) if current else {}
    reason = 'plan item %s (%s plan %s)' % (t['path'], source, revision)
    text = header_text(t)
    action = 'unchanged'
    if current is None:
        raw = ce._text_bytes(text)
        anatomy = {'plan_ref': t['plan_task_id'], 'basis': ['plan:%s:%s' % (source, t['plan_task_id'])] + ([root_claim] if root_claim and not t['parent_id'] else [])}
        if parent_header:
            anatomy['refines'] = parent_header
        if t.get('purpose'):
            anatomy['purpose'] = t['purpose']
        args = {'rationale': context or t.get('purpose') or 'Item %s of the %s plan, revision %s.' % (t['path'], source, revision),
                'attribution': 'plan:' + source}
        item = ce.build_item('decision', text, raw, t['branch'], args, [], reason, 'h1', anatomy=anatomy)
        res = ce._write_claim(svc, t['branch'], item, ce.DEFAULT_ORIGIN_VERSION, reason, 'plan-header', None)
        if 'claim_id' not in res:
            raise _denied()(res.get('status', 'PLAN_WRITE_FAILED'))
        current = res['claim_id']
        link(svc, t['plan_task_id'], current, 'defines')
        action = 'created'
    elif text_now != text:
        res = ce.update(svc, current, text, 'plan item %s changed (%s plan %s)' % (t['path'], source, revision))
        if res.get('status') != 'CLAIM_UPDATED':
            raise _denied()(res.get('status', 'PLAN_WRITE_FAILED'))
        current = res['new_claim_id']
        link(svc, t['plan_task_id'], current, 'defines')
        props = {}
        action = 'updated'
    wanted = {'plan_path': t['path'], 'branch_code': t['branch'], 'plan_status': t['status']}
    if context:
        wanted['context'] = context
    events = [_prop('p%d' % n, current, k, v, reason) for n, (k, v) in enumerate(sorted(wanted.items())) if props.get(k) != v]
    if events:
        receipt = _append(svc, t['branch'], events, reason, 'props')
        if receipt.get('status') != 'DEMO_ADMITTED':
            raise _denied()(receipt.get('status', 'PLAN_WRITE_FAILED'))
        if action == 'unchanged':
            action = 'properties'
    t['header_claim_id'] = current
    return action


def _ingest_result(svc, source, revision, snapshot_digest, changed, tasks, actions, preamble=''):
    with svc.store.read() as c:
        archived = [r['path'] for r in c.execute('SELECT v.path FROM current_plan_tasks v JOIN plan_tasks t ON t.plan_task_id=v.plan_task_id '
                                                   'WHERE v.archived=1 AND t.source=?', (source,))]
        rows = []
        for t in tasks:
            b = c.execute('SELECT branch FROM plan_branches WHERE plan_task_id=?', (t['plan_task_id'],)).fetchone()
            header = _header_claim(c, t['plan_task_id'])
            rows.append({'path': t['path'], 'plan_task_id': t['plan_task_id'], 'title': t['title'], 'status': t['status'],
                         'branch': b[0] if b else None, 'header_claim_id': header,
                         'action': actions.get(t['plan_task_id'], 'unchanged')})
    count = lambda a: sum(1 for r in rows if r['action'] == a)
    first = rows[0] if rows else None
    steps = []
    if first:
        steps.append({'tool': 'dom_claim', 'arguments': {'plan_task': first['path'], 'type': 'text', 'text': '<a statement about this item>'},
                      'why': 'record a finding/statement under a plan item; the branch is chosen from the item'})
        steps.append({'tool': 'dom_plan_tree', 'arguments': {'root': first['path']}, 'why': 'see the item tree with branch codes and claim counts'})
    return {'status': 'PLAN_INGESTED' if changed else 'PLAN_UNCHANGED', 'simulated': True, 'source': source,
            'source_revision': revision, 'snapshot_digest': snapshot_digest, 'changed': changed,
            'created': count('created'), 'updated': count('updated'), 'unchanged': count('unchanged') + count('properties'),
            'archived': archived, 'preamble': preamble or '', 'tasks': rows, 'next_steps': steps}


# ===================================================== duty-mode state ==
def record_check(svc, source, snapshot_digest, changed, kind='check', note=None, session=None):
    with svc.store.transaction() as c:
        c.execute('INSERT INTO plan_checks(source,snapshot_digest,changed,kind,note,at,principal,session) VALUES(?,?,?,?,?,?,?,?)',
                  (source, snapshot_digest, int(bool(changed)), kind, note, _now(), svc.context.principal, session))


def _last_change(c, source):
    return c.execute('SELECT principal,session,at FROM plan_checks WHERE changed=1 AND source=? ORDER BY seq DESC LIMIT 1',
                     (source,)).fetchone()


def _author_of(c, claim_id):
    row = c.execute('SELECT ca.author FROM claims cl JOIN candidates ca ON ca.id=cl.candidate WHERE cl.id=?', (claim_id,)).fetchone()
    return row[0] if row else None


def _previous_headers(svc, tasks):
    """plan_task_id -> {header_claim_id, author, source_revision} of the current header, before this ingest."""
    out = {}
    with svc.store.read() as c:
        for t in tasks:
            header = _header_claim(c, t['plan_task_id'])
            if header is None:
                continue
            row = c.execute('SELECT source_revision FROM current_plan_tasks WHERE plan_task_id=?', (t['plan_task_id'],)).fetchone()
            out[t['plan_task_id']] = {'header_claim_id': header, 'author': _author_of(c, header),
                                      'source_revision': row[0] if row else None}
    return out


def _conflict(svc, source, base, current, tasks, mode, session, forced):
    """Record and describe a plan sent on an older revision (T-01)."""
    with svc.store.read() as c:
        now = {r['plan_task_id']: r for r in c.execute(
            'SELECT v.plan_task_id,v.path,v.digest FROM current_plan_tasks v JOIN plan_tasks t ON t.plan_task_id=v.plan_task_id '
            'WHERE t.source=? AND v.archived=0', (source,))}
        mine = {t['plan_task_id'] for t in tasks}
        paths = {t['path'] for t in tasks if t['plan_task_id'] not in now or now[t['plan_task_id']]['digest'] != content_digest(t)}
        if mode == 'full':
            paths |= {r['path'] for tid, r in now.items() if tid not in mine}
        paths = sorted(paths)
        last = _last_change(c, source)
    with svc.store.transaction() as c:
        c.execute('INSERT INTO plan_conflicts(source,base_revision,current_revision,principal,session,changed_by,item_paths,forced,at) '
                  'VALUES(?,?,?,?,?,?,?,?,?)', (source, base, current, svc.context.principal, session, last['principal'] if last else None,
                                               json.dumps(paths, ensure_ascii=False), int(forced), _now()))
    return {'status': 'PLAN_CONFLICT', 'simulated': True, 'source': source, 'base_revision': base, 'current_revision': current,
            'changed_by': last['principal'] if last else None, 'changed_at': last['at'] if last else None, 'item_paths': paths,
            'next_steps': [{'tool': 'dom_plan_tree', 'arguments': {'root': 'П1'}, 'why': 'read the current plan (revision %s) first' % current},
                           {'tool': 'dom_plan_ingest', 'arguments': {'plan': '<merged plan>', 'base_revision': current},
                            'why': 'send your plan based on the current revision (or force=true to overwrite it)'}]}


def open_conflicts(c, source=None):
    """Unforced conflicts not followed by an ingest of the same principal."""
    out = []
    sql = 'SELECT * FROM plan_conflicts WHERE forced=0' + (' AND source=?' if source else '') + ' ORDER BY seq'
    for r in c.execute(sql, (source,) if source else ()).fetchall():
        later = c.execute("SELECT 1 FROM plan_checks WHERE kind='ingest' AND source=? AND principal IS ? AND at>? LIMIT 1",
                          (r['source'], r['principal'], r['at'])).fetchone()
        if later is None:
            out.append({'source': r['source'], 'principal': r['principal'], 'base_revision': r['base_revision'],
                        'current_revision': r['current_revision'], 'changed_by': r['changed_by'],
                        'item_paths': json.loads(r['item_paths']), 'at': r['at']})
    return out


PLAN_REQUEST_TEXT = ('Send the current project plan to the DOM memory: call dom_plan_ingest with the plan as numbered items '
                     '(1., 1.1, 1.1.1 ...) or JSON [{"title", "context", "children"}]. Include for each item the context from the '
                     'conversation (why it exists, what it is for). If the plan did not change, call dom_plan_ingest with the same plan.')
PLAN_CHECK_TEXT = ('Plan check: has the project plan changed since revision %s? If yes, send the updated plan with dom_plan_ingest '
                   '(same item ids/titles keep their branches; changed items get new versions). If not, resend it unchanged.')


def status(svc, check_every=10):
    """Freshness of the plan projection and whether a plan check / plan request is due."""
    ce._lite_only(svc)
    svc.scope()
    svc.ready()
    if type(check_every) is not int or not 1 <= check_every <= 10000:
        raise _denied()('INVALID_FIELD', field='check_every')
    with svc.store.read() as c:
        svc.integrity_gate(c)  # F-26
        states = [dict(r) for r in c.execute('SELECT source,source_revision,last_checked_at,last_changed_at,snapshot_digest FROM plan_projection_state ORDER BY source')]
        for st in states:  # T-02: who sent the current revision
            last = _last_change(c, st['source'])
            st['ingested_by'] = last['principal'] if last else None
            st['session'] = last['session'] if last else None
        conflicts = open_conflicts(c)
        last_change = c.execute("SELECT COALESCE(MAX(seq),0) FROM plan_checks WHERE changed=1").fetchone()[0]
        since = c.execute('SELECT COUNT(*) FROM plan_checks WHERE seq>? AND kind IN (\'check\',\'ingest\')', (last_change,)).fetchone()[0]
        last_request = c.execute("SELECT seq,at,note FROM plan_checks WHERE kind='request' ORDER BY seq DESC LIMIT 1").fetchone()
        items = c.execute('SELECT COUNT(*) FROM current_plan_tasks WHERE archived=0').fetchone()[0]
        open_items = c.execute("SELECT COUNT(*) FROM current_plan_tasks WHERE archived=0 AND status NOT IN ('done','cancelled')").fetchone()[0]
    from .admission import projection_stale
    for s in states:
        s['stale'] = projection_stale(s, None, svc.plan_ttl_seconds)
    has_plan = bool(states)
    closed = has_plan and items > 0 and open_items == 0
    due = (not has_plan) or (not closed and since >= check_every)
    revision = states[0]['source_revision'] if states else None
    request = None
    if not has_plan:
        request = {'kind': 'plan_request', 'text': PLAN_REQUEST_TEXT}
    elif due:
        request = {'kind': 'plan_check', 'text': PLAN_CHECK_TEXT % revision}
    steps = [{'tool': 'dom_plan_ingest', 'arguments': {'plan': '<the plan>'}, 'why': request['text']}] if request else []
    return {'status': 'PLAN_STATUS', 'simulated': True, 'has_plan': has_plan, 'project_closed': closed,
            'sources': states, 'items': items, 'open_items': open_items, 'iterations_since_change': since,
            'check_every': check_every, 'plan_check_due': due, 'request': request,
            'last_request_at': last_request['at'] if last_request else None, 'conflicts': conflicts, 'next_steps': steps}


# ====================================================== task claims ==
def task_claims(svc, task, include_children=False, role='all', include_history=False):
    """OB-3: claims linked to a plan item (and its subtree), grouped by role."""
    Denied = _denied()
    ce._lite_only(svc)
    svc.scope()
    svc.ready()
    if role != 'all' and role not in ROLES:
        raise Denied('INVALID_FIELD', field='role', hint="'role' is all or one of: %s." % ', '.join(ROLES))
    if type(include_children) is not bool or type(include_history) is not bool:
        raise Denied('INVALID_FIELD', field='include_children')
    with svc.store.read() as c:
        stamp = svc.integrity_gate(c)
        row = resolve_task(c, task)
        if row is None:
            raise Denied('TASK_NOT_FOUND', field='task', hint='No plan item with this id, path (П1.2) or branch code. Call dom_plan_tree.')
        ids = subtree_ids(c, row['plan_task_id']) if include_children else [row['plan_task_id']]
        children = ce._children(c)
        archived = ce._archived(c)
        info = ce._lineage(children)
        current_tasks = {r['plan_task_id']: r for r in c.execute('SELECT * FROM current_plan_tasks')}
        by_role, orphaned, types = {}, [], {}
        seen = set()
        parent = {new: old for old, new in children.items()}

        def chain_of(cid):
            # every version of the record cid belongs to, oldest first (F-10)
            first = cid
            while first in parent:
                first = parent[first]
            out, cur = [first], first
            while cur in children and children[cur] not in out:
                cur = children[cur]
                out.append(cur)
            return out
        by_ref = _claims_by_plan_ref(c, ids)  # T-07: one pass over claims, not one per item
        for tid in ids:
            rows = [dict(r) for r in c.execute('SELECT * FROM task_claims WHERE plan_task_id=? ORDER BY linked_at', (tid,))]
            linked = {r['claim_id'] for r in rows}
            # F-25: a claim whose immutable anatomy names this item but whose link row is gone
            # is still listed (link_recovered=true) instead of silently disappearing.
            sem = current_tasks[tid]['semantic_digest'] if tid in current_tasks else ''
            for cid_r in by_ref.get(tid, ()):
                lineage = chain_of(cid_r)
                if not linked & set(lineage) and cid_r == lineage[0]:
                    rows.append({'claim_id': cid_r, 'role': 'documents', 'linked_semantic_digest': sem,
                                 'linked_at_plan_version': 0, 'recovered': True})
            for link_row in rows:
                if role != 'all' and link_row['role'] != role:
                    continue
                cid = link_row['claim_id']
                cur = _current(children, cid)
                versions = chain_of(cid) if include_history else [cur]
                for shown in versions:
                    key = (link_row['role'], shown)
                    if key in seen:
                        continue
                    seen.add(key)
                    if not include_history and shown in archived:
                        continue  # F-38: a merged/archived claim is not current
                    entry = _task_claim_entry(c, info, archived, current_tasks, tid, link_row, shown)
                    if entry is None:
                        continue
                    if entry['task_archived']:
                        orphaned.append(entry)
                    entry.pop('task_archived')
                    by_role.setdefault(link_row['role'], []).append(entry)
                    if entry['is_current']:
                        types[entry['type']] = types.get(entry['type'], 0) + 1
    from . import public_dto
    return {'status': 'TASK_CLAIMS', 'simulated': True, 'task_path': row['path'], 'plan_task_id': row['plan_task_id'],
            'branch': row['branch'], 'include_children': include_children, 'claims_by_role': by_role,
            'orphaned_links': orphaned, 'summary': {'total_claims': sum(types.values()), 'by_content_type': types},
            'read_snapshot': public_dto.read_snapshot(stamp, sorted(svc.context.branches))}


def _task_claim_entry(c, info, archived, current_tasks, tid, link_row, shown):
    cl = c.execute('SELECT content_type,origin_branch,raw_sha256,raw FROM claims WHERE id=?', (shown,)).fetchone()
    if cl is None:
        return None
    task_row = current_tasks.get(tid)
    stale = task_row is None or bool(task_row['archived']) or task_row['semantic_digest'] != link_row['linked_semantic_digest']
    record_id, version = info(shown)
    entry = {'claim_id': shown, 'logical_claim_id': record_id, 'version': version, 'is_current': shown not in archived,
             'type': cl['content_type'], 'branch': cl['origin_branch'], 'plan_path': task_row['path'] if task_row else None,
             'preview': ce._preview(bytes(cl['raw']).decode('utf-8', 'replace'), None), 'link_stale': stale,
             'linked_at_plan_version': link_row['linked_at_plan_version'],
             'task_archived': task_row is not None and bool(task_row['archived'])}
    if link_row.get('recovered'):
        entry['link_recovered'] = True
    who = c.execute('SELECT ca.author, ca.root FROM claims cl JOIN candidates ca ON ca.id=cl.candidate WHERE cl.id=?', (shown,)).fetchone()
    entry['author'], entry['root'] = (who[0], who[1]) if who else (None, None)  # T-04 / TD-DOM-59
    props = _properties(c, shown)
    if props.get('variant_of'):
        entry['variant_of'] = props['variant_of']
    return entry


def _claims_by_plan_ref(c, task_ids):
    """plan_task_id -> [claim ids whose anatomy.plan_ref names it], in rowid order."""
    ids = list(task_ids)
    out = {}
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        q = ("SELECT id, json_extract(CAST(payload AS TEXT),'$.anatomy.plan_ref') AS ref FROM claims "
             "WHERE json_extract(CAST(payload AS TEXT),'$.anatomy.plan_ref') IN (%s) ORDER BY rowid" % ','.join('?' * len(part)))
        for row in c.execute(q, part):
            out.setdefault(row['ref'], []).append(row['id'])
    return out


# ================================================ fork / convergence ==
def variant_target(svc, branch, item):
    """A current claim in ``branch`` with the same body but other properties, or None."""
    raw = __import__('base64').b64decode(item['body_b64'])
    with svc.store.read() as c:
        archived = ce._archived(c)
        for row in c.execute('SELECT id,payload FROM claims WHERE origin_branch=? AND raw_sha256=? AND content_type=? ORDER BY rowid',
                             (branch, raw_hash(raw), item['content_type'])):
            if row['id'] in archived:
                continue
            other = json.loads(row['payload'])
            if (other.get('metadata'), other.get('anatomy'), sorted(other.get('requirement_refs', []))) != \
                    (item['metadata'], item.get('anatomy'), item['requirement_refs']):
                return row['id']
    return None


def _same(a, b):
    return (a['content_type'], a['body_b64'], a.get('metadata'), a.get('anatomy'), sorted(a.get('requirement_refs', []))) == \
        (b['content_type'], b['body_b64'], b.get('metadata'), b.get('anatomy'), sorted(b.get('requirement_refs', [])))


def reconcile_variants(svc):
    """Converge variant branches whose current claims equal the base again (all properties)."""
    merged = []
    with svc.store.read() as c:
        variants = [dict(r) for r in c.execute("SELECT branch,parent_branch FROM plan_branches WHERE kind='variant'")]
        children = ce._children(c)
        archived = ce._archived(c)
        info = ce._lineage(children)
        plans = []
        for v in variants:
            pairs = []
            for (payload, claim_ref) in c.execute("SELECT payload,claim_ref FROM events WHERE kind='property' AND branch=?", (v['branch'],)):
                value = json.loads(payload)['value']['value']
                if value.get('name') != 'variant_of':
                    continue
                mine = _current(children, info(claim_ref)[0])
                theirs = _current(children, value['value'])
                if mine in archived or theirs in archived:
                    continue
                a = c.execute('SELECT payload FROM claims WHERE id=?', (mine,)).fetchone()
                b = c.execute('SELECT payload FROM claims WHERE id=?', (theirs,)).fetchone()
                if a and b and _same(json.loads(a[0]), json.loads(b[0])):
                    split = _properties(c, info(claim_ref)[0]).get('fork_conflict')
                    base_branch = c.execute('SELECT origin_branch FROM claims WHERE id=?', (theirs,)).fetchone()[0]
                    pairs.append((mine, theirs, split, base_branch))
            if pairs:
                plans.append((v['branch'], pairs))
    svc._system += 1
    try:
        return _merge_variants(svc, plans, merged)
    finally:
        svc._system -= 1


def _merge_variants(svc, plans, merged):
    for branch, pairs in plans:
        events = []
        for n, (mine, theirs, _split, _base) in enumerate(pairs):
            events.append(_prop('m%d' % n, mine, 'merged_into', theirs, 'variant converged with %s' % theirs))
            events.append({'local_id': 'a%d' % n, 'event_type': 'archive', 'claim_ref': mine, 'reason': 'merged into %s' % theirs,
                           'value': {}, 'supersedes': '', 'ref': 'plan:reconcile'})
        receipt = _append(svc, branch, events, 'variant converged', 'merge')
        if receipt.get('status') == 'DEMO_ADMITTED':
            with svc.store.read() as c:
                left = [cl for cl in c.execute('SELECT id FROM claims WHERE origin_branch=?', (branch,)) if cl[0] not in ce._archived(c)]
            if not left:
                _append(svc, branch, [{'local_id': 'cv', 'event_type': 'converge', 'claim_ref': '', 'reason': 'all variant claims merged',
                                       'value': {}, 'supersedes': '', 'ref': 'plan:reconcile'}], 'variant branch converged', 'converge')
            for _m, theirs, split, base in pairs:
                if split:  # T-09: a converged fork closes its open split on the base branch
                    _append(svc, base, [{'local_id': 'rs', 'event_type': 'resolve', 'claim_ref': theirs, 'reason': 'fork converged',
                                         'value': {'conflict_ref': split}, 'supersedes': '', 'ref': 'plan:reconcile'}],
                            'fork converged', 'resolve-' + split)
            merged.extend({'branch': branch, 'claim_id': m, 'merged_into': t} for m, t, _s, _b in pairs)
    return merged


# ============================================================ context ==
def context(svc, claim_id, depth=8):
    """«Откуда взялось А»: the claim, its refinement chain upward, its basis and plan item."""
    Denied = _denied()
    svc.scope()
    svc.ready()
    if type(depth) is not int or not 1 <= depth <= 32:
        raise Denied('INVALID_FIELD', field='depth')
    with svc.store.read() as c:
        stamp = svc.integrity_gate(c)
        row = c.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone() if type(claim_id) is str else None
        if row is None or row['origin_branch'] not in svc.context.branches:
            raise Denied('NO_SUCH_CLAIM', field='claim_id')
        children = ce._children(c)
        archived = ce._archived(c)
        info = ce._lineage(children)

        def card(cid):
            r = c.execute('SELECT id,content_type,origin_branch,raw,payload FROM claims WHERE id=?', (cid,)).fetchone()
            if r is None or r['origin_branch'] not in svc.context.branches:
                return None
            item = json.loads(r['payload'])
            rid, version = info(cid)
            return {'claim_id': cid, 'type': r['content_type'], 'branch': r['origin_branch'], 'version': version,
                    'logical_claim_id': rid, 'is_current': cid not in archived, 'current_claim_id': _current(children, cid),
                    'preview': ce._preview(bytes(r['raw']).decode('utf-8', 'replace'), None),
                    'reason': item.get('reason'), 'anatomy': item.get('anatomy') or {}, 'properties': _properties(c, cid)}
        me = card(claim_id)
        chain, seen, cursor = [], {claim_id}, me
        while cursor and cursor['anatomy'].get('refines') and len(chain) < depth:
            parent = cursor['anatomy']['refines']
            if parent in seen:
                break
            seen.add(parent)
            cursor = card(parent)
            if cursor is None:
                break
            chain.append(cursor)
        basis = []
        for ref in me['anatomy'].get('basis', []):
            found = card(ref)
            basis.append(found if found else {'ref': ref})
        lineage_ids = set()
        cur = info(claim_id)[0]
        while cur:
            lineage_ids.add(cur)
            cur = children.get(cur)
        refinements = []
        for r in c.execute('SELECT id,payload FROM claims ORDER BY rowid'):
            a = json.loads(r['payload']).get('anatomy') or {}
            if a.get('refines') in lineage_ids and r['id'] not in archived:
                found = card(r['id'])
                if found:
                    refinements.append({k: found[k] for k in ('claim_id', 'type', 'branch', 'preview')})
        plan = None
        plan_ref = me['anatomy'].get('plan_ref')
        if plan_ref:
            t = resolve_task(c, plan_ref)
            if t is not None:
                ancestors, pid = [], t['parent_id']
                while pid:
                    p = resolve_task(c, pid)
                    if p is None:
                        break
                    ancestors.insert(0, {'path': p['path'], 'title': p['title'], 'branch': p['branch'], 'header_claim_id': _header_claim(c, pid)})
                    pid = p['parent_id']
                plan = {'path': t['path'], 'title': t['title'], 'status': t['status'], 'branch': t['branch'],
                        'archived': bool(t['archived']), 'header_claim_id': _header_claim(c, t['plan_task_id']), 'ancestors': ancestors}
        root = c.execute('SELECT request_claim_id FROM project_root WHERE id=1').fetchone()
        resolution = ce.resolve_claim(c, claim_id)  # T-09 stitching: where this claim lives now
        if resolution['current_claim_id']:
            me['current_claim_id'] = resolution['current_claim_id']
    from . import public_dto
    return {'status': 'CONTEXT', 'simulated': True, 'claim': me, 'resolution': resolution, 'refines_chain': chain, 'basis': basis,
            'refinements': refinements, 'plan': plan, 'root_request_claim_id': root[0] if root else None,
            'read_snapshot': public_dto.read_snapshot(stamp, sorted(svc.context.branches))}


# ============================================================ task graph ==
def task_graph(svc, task, agent=None, include_history=True, include_children=True, limit=200):
    """T-03: who contributed what to a plan item, as chains of claim versions with edges.

    Read-only; built from claims, candidates, events and task_claims.  Branches stay
    plan items (decision D-1): agents are told apart by the author of each version.
    """
    Denied = _denied()
    ce._lite_only(svc)
    svc.scope()
    svc.ready()
    if agent is not None and (type(agent) is not str or not agent.strip()):
        raise Denied('INVALID_FIELD', field='agent')
    if type(include_history) is not bool or type(include_children) is not bool:
        raise Denied('INVALID_FIELD', field='include_history')
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise Denied('INVALID_FIELD', field='limit', hint="'limit' is 1..1000 chains.")
    with svc.store.read() as c:
        stamp = svc.integrity_gate(c)
        row = resolve_task(c, task)
        if row is None:
            raise Denied('TASK_NOT_FOUND', field='task', hint='No plan item with this id, path (П1.2) or branch code. Call dom_plan_tree.')
        ids = subtree_ids(c, row['plan_task_id']) if include_children else [row['plan_task_id']]
        children = ce._children(c)
        archived = ce._archived(c)
        parent = {new: old for old, new in children.items()}
        paths = {r['plan_task_id']: r['path'] for r in c.execute('SELECT plan_task_id,path FROM current_plan_tasks')}
        links = {}
        for tid in ids:
            for r in c.execute("SELECT claim_id, role FROM task_claims WHERE plan_task_id=? AND role<>'defines'", (tid,)):
                links.setdefault(r['claim_id'], (tid, r['role']))
        for tid, cids in _claims_by_plan_ref(c, ids).items():
            for cid in cids:
                links.setdefault(cid, (tid, 'documents'))
        roots = {}
        for cid, link_info in links.items():
            first = cid
            while first in parent:
                first = parent[first]
            if c.execute('SELECT 1 FROM task_claims WHERE claim_id=? AND role=\'defines\'', (first,)).fetchone():
                continue  # header claims are the plan mirror, not a contribution
            roots.setdefault(first, link_info)
        chains, agents = [], {}
        for first in sorted(roots, key=lambda x: c.execute('SELECT rowid FROM claims WHERE id=?', (x,)).fetchone()[0]):
            tid, role = roots[first]
            versions, cur, seen = [], first, set()
            while cur and cur not in seen:
                seen.add(cur)
                versions.append(cur)
                cur = children.get(cur)
            if not include_history:
                versions = [v for v in versions if v not in archived] or versions[-1:]
            out_versions, edges = [], []
            for n, cid in enumerate(versions):
                cl = c.execute('SELECT cl.content_type, cl.origin_branch, cl.raw, cl.payload, ca.author, ca.root FROM claims cl '
                               'JOIN candidates ca ON ca.id=cl.candidate WHERE cl.id=?', (cid,)).fetchone()
                info = ce._claim_info(svc, c, cid)
                anatomy = json.loads(cl['payload']).get('anatomy') or {}
                props = _properties(c, cid)
                out_versions.append({'claim_id': cid, 'version': info['version'], 'author': cl['author'], 'root': cl['root'],
                                     'review_level': info['review_level'], 'revision': info['revision'], 'branch': cl['origin_branch'],
                                     'type': cl['content_type'], 'is_current': cid not in archived,
                                     'preview': ce._preview(bytes(cl['raw']).decode('utf-8', 'replace'), None)})
                if n:
                    edges.append({'from': versions[n - 1], 'to': cid, 'kind': 'supersedes'})
                for ref in anatomy.get('basis', []):
                    if c.execute('SELECT 1 FROM claims WHERE id=?', (ref,)).fetchone():
                        edges.append({'from': cid, 'to': ref, 'kind': 'basis'})
                if anatomy.get('refines'):
                    edges.append({'from': cid, 'to': anatomy['refines'], 'kind': 'refines'})
                if props.get('variant_of'):
                    edges.append({'from': cid, 'to': props['variant_of'], 'kind': 'variant_of'})
            edges.append({'from': first, 'to': tid, 'kind': 'task_link', 'role': role})
            if agent is not None and all(v['author'] != agent for v in out_versions):
                continue
            for v in out_versions:
                a = agents.setdefault((v['author'], v['root']), {'author': v['author'], 'root': v['root'], 'claim_count': 0})
                a['claim_count'] += 1
            chains.append({'logical_claim_id': first, 'plan_path': paths.get(tid), 'versions': out_versions, 'edges': edges})
    truncated = len(chains) > limit
    from . import public_dto
    return {'status': 'TASK_GRAPH', 'simulated': True,
            'task': {'path': row['path'], 'plan_task_id': row['plan_task_id'], 'branch': row['branch'], 'title': row['title']},
            'agents': sorted(agents.values(), key=lambda a: (-a['claim_count'], str(a['author']))),
            'chains': chains[:limit], 'truncated': truncated,
            'read_snapshot': public_dto.read_snapshot(stamp, sorted(svc.context.branches))}
