"""Deterministic project brief for prompt injection.

The brief is a pointer, not a claim dump: root request, the active plan lineage
(root -> branch -> sub-branch -> current block), each level's baseline from the
header claim's `context` property, done/planned, risks, anchors, execution
vector and a short checklist.  It never invents a relationship.
"""
from __future__ import annotations

import json

from . import planning, promptizer
from .admission import verify_chain

MAX_CHARS = 3400
RISK_WORDS = ('risk', 'blocker', 'аналит', 'analysis', 'decision', 'риск', 'блокер')


def _short(value, limit=260):
    text = ' '.join(str(value or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _header(c, task):
    claim_id = planning._header_claim(c, task['plan_task_id'])
    if not claim_id:
        return None, {}
    return claim_id, planning._properties(c, claim_id)


def _line(task, header, props):
    baseline = props.get('context') or task.get('title') or ''
    return '%s "%s" [%s %s] baseline: %s (claim %s)' % (
        task.get('path'), _short(task.get('title'), 90), task.get('branch') or '?', task.get('status') or '?',
        _short(baseline, 300), (header or '?')[:8],
    )


def _capabilities(props):
    """Capabilities declared by the header's ``accept`` property, never guessed.

    ``dom_annotate`` stores a property value as text, so a JSON-encoded list is
    decoded first; an absent, malformed or unmapped ``accept`` yields no CAP
    line, so the producer receives ``cap: []`` instead of an invented envelope.
    """
    accept = props.get('accept')
    if isinstance(accept, str):
        try:
            accept = json.loads(accept)
        except ValueError:
            return []
    if isinstance(accept, dict):
        accept = [accept]
    if not isinstance(accept, list) or not accept:
        return []
    try:
        from .skill_router import capabilities_from_accept
        caps, _unmapped = capabilities_from_accept(accept)
        return list(caps)
    except (ImportError, TypeError, ValueError):
        return []


def _source(resolved, default='not recorded'):
    if not resolved.get('text'):
        return default
    return '%s %s' % (resolved.get('source'), (resolved.get('anchor') or '?')[:8])


def _choose_current(rows, children):
    in_progress = [row for row in rows if row.get('status') == 'in_progress']
    if in_progress:
        return in_progress[0]
    pending = [row for row in rows if row.get('status') not in ('done', 'cancelled')]
    for row in pending:
        kids = children.get(row['plan_task_id'], [])
        if not any(kid.get('status') not in ('done', 'cancelled') for kid in kids):
            return row
    return pending[0] if pending else None


def project_brief(svc, max_chars=MAX_CHARS):
    """Return the compact prompt brief, or a short no-plan note."""
    with svc.store.read() as c:
        svc.integrity_gate(c)
        state = c.execute(
            'SELECT source_revision FROM plan_projection_state WHERE source=? ORDER BY rowid DESC LIMIT 1',
            ('agent',),
        ).fetchone()
        revision = state[0] if state else None
        rows = [dict(row) for row in c.execute(
            'SELECT v.*, b.branch, b.level FROM current_plan_tasks v '
            'LEFT JOIN plan_branches b ON b.plan_task_id=v.plan_task_id '
            'WHERE v.archived=0 ORDER BY v.path'
        )]
        total_claims = c.execute('SELECT count(*) FROM claims').fetchone()[0]
        integrity = verify_chain(c)['status']
        children = {}
        by_id = {}
        for row in rows:
            by_id[row['plan_task_id']] = row
            children.setdefault(row['parent_id'], []).append(row)
        current = _choose_current(rows, children)
        if current is None:
            return '[DOM MEMORY INDEX] no active plan items; call dom_plan_status/dom_plan_ingest.\n'
        lineage = []
        cursor = current
        while cursor is not None:
            lineage.append(cursor)
            cursor = by_id.get(cursor.get('parent_id'))
        lineage.reverse()

        root_row = c.execute('SELECT request_claim_id FROM project_root WHERE id=1').fetchone()
        root_id = root_row[0] if root_row else None
        root_text = planning._claim_text(c, root_id) if root_id else None
        root_props = planning._properties(c, root_id) if root_id else {}
        main_task = root_props.get('main_task') or root_text or 'not recorded'
        main_meaning = root_props.get('main_task_meaning') or 'not recorded'
        root_gits = root_props.get('gits')
        root_tools = root_props.get('tools')
        explicit = root_props.get('current_task')
        explicit_active = False
        pinned_done = None
        if explicit:
            found = planning.resolve_task(c, explicit)
            if found is not None:
                found = dict(found)
            if found and not found.get('archived') and found.get('plan_task_id') in by_id:
                candidate = by_id[found['plan_task_id']]
                if candidate.get('status') in ('done', 'cancelled'):
                    pinned_done = candidate['path']
                else:
                    current = candidate
                    explicit_active = True
                    lineage = []
                    cursor = current
                    while cursor is not None:
                        lineage.append(cursor)
                        cursor = by_id.get(cursor.get('parent_id'))
                    lineage.reverse()

        lines = [
            '[DOM MEMORY INDEX] plan %s · %d items / %d open · %d claims · %s' % (
                revision or '?', len(rows), sum(1 for row in rows if row.get('status') not in ('done', 'cancelled')),
                total_claims, integrity,
            ),
            'MAIN TASK: %s — %s (root %s)' % (_short(main_task, 140), _short(main_meaning, 240), (root_id or '?')[:8]),
            'ROOT REQUEST: %s (%s)' % (_short(root_text, 180) if root_text else 'not recorded', (root_id or '?')[:8]),
            'GRANDLINE: %s (plan %s)' % (_short(main_task, 140), revision or '?'),
            'LINE:',
        ]
        anchors = []
        for task in lineage:
            header, props = _header(c, task)
            if header:
                anchors.append(header)
            lines.append('  ' + _line(task, header, props))
        header, props = _header(c, current)
        caps = _capabilities(props)
        cap_line = ' CAP: %s' % ','.join(caps) if caps else ''
        lines.append('CURRENT%s: %s "%s" [%s %s] anchor %s%s' % (
            '' if explicit_active else ' (auto)',
            current['path'], _short(current.get('title'), 100), current.get('branch') or '?',
            current.get('status') or '?', (header or '?')[:8], cap_line,
        ))
        lines.append('ACTIVE: %s' % (
            'pinned by current_task=%s' % explicit if explicit_active
            else ('current_task=%s is done; pin the next block' % pinned_done if pinned_done
                  else 'not marked; set current_task on the root claim to pin the active block')
        ))
        sources = ['plan %s' % (revision or '?'), 'root %s' % (root_id or '?')[:8]]
        meaning = promptizer.resolve(c, 'current_meaning', props, current['plan_task_id'], props.get('context'), header or '')
        lines.append('CURRENT MEANING: %s' % (meaning['text'] or 'not recorded'))
        sources.append('meaning=%s' % _source(meaning, 'not recorded'))
        why = promptizer.resolve(c, 'why', props, current['plan_task_id'], props.get('context'), header or '')
        now = promptizer.resolve(c, 'now', props, current['plan_task_id'], props.get('context'), header or '')
        lines.append('WHY: %s' % (why['text'] or 'not recorded'))
        lines.append('NOW: %s' % (now['text'] or 'not recorded'))
        sources.append('why=%s' % _source(why, 'not recorded'))
        sources.append('now=%s' % _source(now, 'not recorded'))
        ids = [row['plan_task_id'] for row in lineage]
        placeholders = ','.join('?' * len(ids))
        risks = []
        for row in c.execute(
            'SELECT cl.id, cl.content_type, cl.raw, tc.role FROM task_claims tc '
            'JOIN claims cl ON cl.id=tc.claim_id WHERE tc.plan_task_id IN (%s) '
            "AND tc.role != 'defines' ORDER BY cl.rowid DESC LIMIT 24" % placeholders,
            ids,
        ):
            text = planning._claim_text(c, row['id']) or ''
            if any(word in text.lower() for word in RISK_WORDS):
                risks.append('%s %s: %s' % (row['content_type'], row['id'][:8], _short(text, 180)))
        conflicts = planning.open_conflicts(c)
        siblings = children.get(current.get('parent_id'), [])
        done = [row for row in siblings if row.get('status') == 'done']
        planned = [row for row in children.get(current['plan_task_id'], []) if row.get('status') not in ('done', 'cancelled')]
        planned += [row for row in siblings if row is not current and row.get('status') not in ('done', 'cancelled')]
        lines.append('DONE: %d done in the current branch' % len(done))
        next_steps = []
        for row in planned[:4]:
            next_steps.append('%s %s [%s]' % (row['path'], _short(row.get('title'), 80), row.get('status')))
        roles = {
            row[0]: row[1]
            for row in c.execute(
                'SELECT role, count(*) FROM task_claims WHERE plan_task_id=? GROUP BY role',
                (current['plan_task_id'],),
            )
        }
        work_roles = {role: count for role, count in roles.items() if role != 'defines'}
        current_version = current.get('version') or 0
        stale_links = c.execute(
            "SELECT count(*) FROM task_claims WHERE plan_task_id=? AND role!='defines' AND linked_at_plan_version<?",
            (current['plan_task_id'], current_version),
        ).fetchone()[0]
        if 'implements' not in work_roles:
            next_steps.append('link an implementing claim to the current task')
        if 'validates' not in work_roles:
            next_steps.append('add validation evidence for the current task')
        if conflicts:
            paths = sorted({path for item in conflicts for path in (item.get('item_paths') or [])})
            next_steps.append('resolve the open plan conflict%s' % ((' on ' + ', '.join(paths[:3])) if paths else ''))
        if risks:
            next_steps.append('mitigate the linked risk')
        next_steps = list(dict.fromkeys(next_steps))[:4]
        lines.append('NEXT: %s' % ('; '.join('%d) %s' % (index, step) for index, step in enumerate(next_steps, 1)) or 'none'))

        lines.append('RISKS: %s' % ('; '.join(risks[:3]) if risks else 'none linked'))
        lines.append('RISK CHECKS: risks=%d; conflicts=%d; stale=%d; work=%d' % (
            len(risks), len(conflicts), stale_links, sum(work_roles.values()),
        ))
        if conflicts:
            paths = sorted({path for item in conflicts for path in (item.get('item_paths') or [])})
            lines.append('CONFLICTS: %d open%s' % (len(conflicts), (' on ' + ', '.join(paths[:6])) if paths else ''))
        else:
            lines.append('CONFLICTS: 0 open')
        lines.append('GRAPH: %d linked work claims (%s); read edges with dom_task_graph' % (
            sum(work_roles.values()), ', '.join('%s=%d' % (role, count) for role, count in sorted(work_roles.items())) or 'none',
        ))
        recent = []
        for row in c.execute(
            'SELECT cl.id, cl.content_type, cl.raw FROM claims cl '
            "WHERE cl.origin_branch='main' AND NOT EXISTS "
            '(SELECT 1 FROM task_claims tc WHERE tc.claim_id=cl.id) '
            'ORDER BY cl.rowid DESC LIMIT 2'
        ):
            recent.append('%s %s: %s' % (row['content_type'], row['id'][:8], _short(planning._claim_text(c, row['id']), 110)))
        lines.append('RECENT STATEMENTS: %s' % ('; '.join(recent) if recent else 'none'))
        lines.append('ANCHORS: root %s; plan %s; headers %s' % (
            (root_id or '?')[:8], revision or '?', ', '.join(value[:8] for value in anchors),
        ))
        vector = promptizer.resolve(c, 'vector', props, current['plan_task_id'], props.get('context'), header or '')
        lines.append('VECTOR: %s' % (vector['text'] or 'read current anchor -> dom_task_claims -> claim/update with basis -> verify -> mark status'))
        boundaries = promptizer.resolve(c, 'boundaries', props, current['plan_task_id'], props.get('context'), header or '')
        lines.append('BOUNDARIES: %s' % (boundaries['text'] or 'not recorded'))
        tools_props, tools_anchor = (props, header or '') if props.get('tools') else (root_props, root_id or '')
        tools = promptizer.resolve(c, 'tools', tools_props, current['plan_task_id'], props.get('context'), tools_anchor)
        lines.append('TOOLS: %s' % (tools['text'] or 'not recorded'))
        gits_props, gits_anchor = (props, header or '') if props.get('gits') else (root_props, root_id or '')
        gits = promptizer.resolve(c, 'gits', gits_props, current['plan_task_id'], props.get('context'), gits_anchor)
        lines.append('GITS: %s' % (gits['text'] or 'not recorded'))
        for name, item in (('vector', vector), ('boundaries', boundaries), ('tools', tools), ('gits', gits)):
            sources.append('%s=%s' % (name, _source(item) if item['text'] else ('default' if name == 'vector' else 'not recorded')))
        lines.append('SOURCES: %s' % '; '.join(sources))
        lines.append('CHECKLIST:')
        checklist = [
            'hold root/branch/current baselines before acting',
            'mark the active block with current_task when it changes',
            'no overwrite: dom_update creates a new version',
            'new claim: basis/refines/purpose and plan_task link',
            'branch is native: use the plan branch; variant only by fork/reconcile',
            'verify source/review/audit before accepting',
            'trace a value through SOURCES before relying on it',
            'update the current block status after the work',
        ]
        if 'implements' not in work_roles:
            checklist.append('link an implementing claim to the current task')
        if 'validates' not in work_roles:
            checklist.append('add validation evidence for the current task')
        if conflicts:
            checklist.append('resolve the open plan conflict')
        if risks:
            checklist.append('mitigate the linked risk')
        if next_steps:
            checklist.append('execute next step 1')
        for item in checklist[:9]:
            lines.append('  [ ] ' + item)
    text = '\n'.join(lines)
    return text if len(text) <= max_chars else text[:max_chars - 1] + '…'
