"""Deterministic project brief for prompt injection.

The brief is a pointer, not a claim dump: root request, the active plan lineage
(root -> branch -> sub-branch -> current block), each level's baseline from the
header claim's `context` property, done/planned, risks, anchors, execution
vector and a short checklist.  It never invents a relationship.
"""
from __future__ import annotations

import json
import sys

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
    if accept is None:
        return []  # no declaration: `gaps: not-declared` is the honest answer
    if isinstance(accept, str) and not accept.strip():
        return []  # cleared by a newer plan revision that declares nothing
    if isinstance(accept, str):
        try:
            accept = json.loads(accept)
        except ValueError:
            print('[dom-memory] brief CAP empty: accept property is not valid JSON', file=sys.stderr)
            return []
    if isinstance(accept, dict):
        accept = [accept]
    if not isinstance(accept, list):
        print('[dom-memory] brief CAP empty: accept property is not a list', file=sys.stderr)
        return []
    if not accept:
        return []  # explicitly empty: a newer plan withdrew the acceptance criteria
    try:
        from .skill_router import capabilities_from_accept
        caps, _unmapped = capabilities_from_accept(accept)
    except (ImportError, TypeError, ValueError) as e:
        print('[dom-memory] brief CAP empty: %s' % type(e).__name__, file=sys.stderr)
        return []
    if not caps:
        print('[dom-memory] brief CAP empty: accept declared but no capability mapping', file=sys.stderr)
    return list(caps)


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
    """Return the compact prompt brief for the agent: current task, next steps, git context.

    Design: the agent reads this in ~2 seconds.  Every line answers one of:
    WHERE AM I / WHAT DO I DO / WHAT'S NEXT / WHERE IS THE CODE.
    No hex ids, no internal metrics, no generic checklist.
    """
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

        # line from root through parents to current
        lineage = []
        cursor = current
        while cursor is not None:
            lineage.append(cursor)
            cursor = by_id.get(cursor.get('parent_id'))
        lineage.reverse()

        # root claim: main_task, meaning, gits, explicit current_task pin
        root_row = c.execute('SELECT request_claim_id FROM project_root WHERE id=1').fetchone()
        root_id = root_row[0] if root_row else None
        root_text = planning._claim_text(c, root_id) if root_id else None
        root_props = planning._properties(c, root_id) if root_id else {}
        main_task = root_props.get('main_task') or root_text or 'not recorded'
        main_meaning = root_props.get('main_task_meaning') or 'not recorded'
        explicit = root_props.get('current_task')
        explicit_active = False
        if explicit:
            found = planning.resolve_task(c, explicit)
            if found is not None:
                found = dict(found)
            if found and not found.get('archived') and found.get('plan_task_id') in by_id:
                candidate = by_id[found['plan_task_id']]
                if candidate.get('status') not in ('done', 'cancelled'):
                    current = candidate
                    explicit_active = True
                    lineage = []
                    cursor = current
                    while cursor is not None:
                        lineage.append(cursor)
                        cursor = by_id.get(cursor.get('parent_id'))
                    lineage.reverse()

        # ======================== BUILD LINES ========================
        lines = []
        SEP = '  ── '  # section separator

        # ── HEADER: project name, plan revision, chain health ──
        summary_compact = 'ПЛАН %s · %d задач' % (revision or '?', len(rows))
        if main_task and main_task != 'not recorded':
            lines.append('── ПАМЯТЬ ПРОЕКТА ──────────────────────────────────')
            lines.append('  ' + summary_compact)
            goal = _short(main_task + (' — ' + main_meaning if main_meaning != 'not recorded' else ''), 240)
            lines.append('  ЦЕЛЬ: ' + goal)
        else:
            lines.append('[DOM MEMORY INDEX] ' + summary_compact)
        # machine-readable plan revision marker for step lock (stepFromBrief reads plan rN)
        if main_task and main_task != 'not recorded' and revision:
            lines.append('  [DOM MEMORY INDEX] plan %s' % revision)

        # ── CURRENT TASK ──
        lines.append('')
        lines.append(SEP + 'ТЕКУЩАЯ ЗАДАЧА ' + SEP)
        # Lineage: synthetic root (main_task) then parents
        indent_curr = '  '
        if main_task and main_task != 'not recorded':
            lines.append('  %s' % (_short(main_task, 100)))
            indent_curr = '    '
        for task in lineage:
            t = task
            h, p = _header(c, t)
            title_show = _short(t.get('title'), 90)
            baseline = p.get('context') or ''
            # Compact: П3 "2. Скрипт сбора" [GH] [pending] → если есть baseline
            base_parts = [t.get('path'), title_show]
            if t.get('branch'):
                base_parts.append('[' + t['branch'] + ']')
            if t.get('status'):
                base_parts.append('[' + t['status'] + ']')
            lines.append(indent_curr + ' '.join(base_parts))
            if baseline:
                lines.append(indent_curr + '  → ' + _short(baseline, 200))

        # machine-readable CURRENT line for step lock (stepFromBrief)
        curr_path = current.get('path', '?')
        curr_title = _short(current.get('title', ''), 90)
        curr_branch = current.get('branch') or ''
        curr_status = current.get('status') or ''
        curr_header, curr_props = _header(c, current)
        curr_context = curr_props.get('context') or ''
        curr_parts = [curr_path]
        if curr_title:
            curr_parts.append('"%s"' % curr_title)
        if curr_branch:
            curr_parts.append('[%s]' % curr_branch)
        if curr_status:
            curr_parts.append('[%s]' % curr_status)
        curr_line = 'CURRENT: ' + ' '.join(curr_parts)
        if curr_context:
            curr_line += '  → ' + _short(curr_context, 120)
        lines.append('  ' + curr_line)
        # machine-readable context for step lock
        if curr_context:
            lines.append('  CURRENT MEANING: ' + _short(curr_context, 240))
        # instruction: after each step, write a claim (the loop closer)
        step_instruction = ('  → После шага запиши dom_claim({plan_task: "%s", '
                           'type: "text", text: "Сейчас: <итог шага>", keywords: ["now"]})') % curr_path
        lines.append(step_instruction)

        # ── NEXT TASKS (siblings of current, up to 4) ──
        siblings = children.get(current.get('parent_id'), [])
        planned = [row for row in children.get(current['plan_task_id'], []) if row.get('status') not in ('done', 'cancelled')]
        planned += [row for row in siblings if row is not current and row.get('status') not in ('done', 'cancelled')]
        if planned:
            lines.append('')
            lines.append(SEP + 'ДАЛЕЕ ПО ПЛАНУ ' + SEP)
            for row in planned[:4]:
                h_next, p_next = _header(c, row)
                base_next = p_next.get('context') or ''
                title_next = _short(row.get('title'), 80)
                # → П2 "1. Какие блоки извлекать" [pending]
                parts_next = ['→', row.get('path'), _short(row.get('title'), 80)]
                if row.get('status'):
                    parts_next.append('[' + row['status'] + ']')
                lines.append('  ' + ' '.join(parts_next))
                if base_next:
                    lines.append('    ' + _short(base_next, 180))

        # ── RISKS (only when non-empty) ──
        ids = [row['plan_task_id'] for row in lineage]
        if ids:
            placeholders = ','.join('?' * len(ids))
            risks = []
            for risk_row in c.execute(
                'SELECT cl.id, cl.content_type, cl.raw FROM task_claims tc '
                'JOIN claims cl ON cl.id=tc.claim_id WHERE tc.plan_task_id IN (%s) '
                "AND tc.role != 'defines' ORDER BY cl.rowid DESC LIMIT 12" % placeholders,
                ids,
            ):
                text = planning._claim_text(c, risk_row['id']) or ''
                if any(word in text.lower() for word in RISK_WORDS):
                    risks.append(_short(text, 160))
            if risks:
                lines.append('')
                lines.append(SEP + 'РИСКИ ' + SEP)
                for r in risks[:2]:
                    lines.append('  ⚠ ' + r)

        # ── CONTEXT: git, optional tools ──
        gits = root_props.get('gits')
        tools_val = root_props.get('tools')
        context_parts = []
        if gits:
            context_parts.append(gits)
        if tools_val:
            context_parts.append(tools_val)
        if context_parts:
            lines.append('')
            lines.append(SEP + 'КОНТЕКСТ ' + SEP)
            for cp in context_parts:
                lines.append('  ' + _short(cp, 200))

    text = '\n'.join(lines)
    return text if len(text) <= max_chars else text[:max_chars - 1] + '…'
