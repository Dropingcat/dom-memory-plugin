"""Deterministic project brief for prompt injection.

The brief is a pointer, not a claim dump: root request, the active plan lineage
(root -> branch -> sub-branch -> current block), each level's baseline from the
header claim's `context` property, done/planned, risks, anchors, execution
vector and a short checklist.  It never invents a relationship.
"""
from __future__ import annotations

from . import planning
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
        if explicit:
            found = planning.resolve_task(c, explicit)
            if found is not None:
                found = dict(found)
            if found and not found.get('archived') and found.get('plan_task_id') in by_id:
                current = by_id[found['plan_task_id']]
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
        lines.append('CURRENT%s: %s "%s" [%s %s] anchor %s' % (
            '' if explicit_active else ' (auto)',
            current['path'], _short(current.get('title'), 100), current.get('branch') or '?',
            current.get('status') or '?', (header or '?')[:8],
        ))
        lines.append('ACTIVE: %s' % (
            'pinned by current_task=%s' % explicit if explicit_active
            else 'not marked; set current_task on the root claim to pin the active block'
        ))
        lines.append('CURRENT MEANING: %s' % _short(props.get('meaning') or 'not recorded', 240))
        lines.append('WHY: %s' % _short(props.get('why') or 'not recorded', 240))
        lines.append('NOW: %s' % _short(props.get('now') or 'not recorded', 240))
        siblings = children.get(current.get('parent_id'), [])
        done = [row for row in siblings if row.get('status') == 'done']
        planned = [row for row in siblings if row is not current and row.get('status') not in ('done', 'cancelled')]
        planned += [row for row in children.get(current['plan_task_id'], []) if row.get('status') not in ('done', 'cancelled')]
        lines.append('DONE: %d done in the current branch; PLANNED: %s' % (
            len(done), ', '.join(row['path'] for row in planned[:6]) or 'none',
        ))

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
        lines.append('RISKS: %s' % ('; '.join(risks[:3]) if risks else 'none linked'))
        lines.append('CONFLICTS: %d open' % len(conflicts))
        recent = []
        for row in c.execute(
            'SELECT cl.id, cl.content_type, cl.raw FROM claims cl '
            "WHERE cl.origin_branch='main' AND NOT EXISTS "
            '(SELECT 1 FROM task_claims tc WHERE tc.claim_id=cl.id) '
            'ORDER BY cl.rowid DESC LIMIT 3'
        ):
            recent.append('%s %s: %s' % (row['content_type'], row['id'][:8], _short(planning._claim_text(c, row['id']), 140)))
        lines.append('RECENT STATEMENTS: %s' % ('; '.join(recent) if recent else 'none'))
        lines.append('ANCHORS: root %s; plan %s; headers %s' % (
            (root_id or '?')[:8], revision or '?', ', '.join(value[:8] for value in anchors),
        ))
        lines.append('VECTOR: %s' % _short(props.get('vector') or 'read current anchor -> dom_task_claims -> claim/update with basis -> verify -> mark status', 260))
        lines.append('BOUNDARIES: %s' % _short(props.get('boundaries') or 'not recorded', 240))
        lines.append('TOOLS: %s' % _short(props.get('tools') or root_tools or 'not recorded', 240))
        lines.append('GITS: %s' % _short(props.get('gits') or root_gits or 'not recorded', 240))
        lines.append('CHECKLIST:')
        for item in (
            'hold root/branch/current baselines before acting',
            'mark the active block with current_task when it changes',
            'no overwrite: dom_update creates a new version',
            'new claim: basis/refines/purpose and plan_task link',
            'branch is native: use the plan branch; variant only by fork/reconcile',
            'verify source/review/audit before accepting',
            'update the current block status after the work',
        ):
            lines.append('  [ ] ' + item)
    text = '\n'.join(lines)
    return text if len(text) <= max_chars else text[:max_chars - 1] + '…'
