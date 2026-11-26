"""L6/L7/L8 of the trusted-write line: the read-before-write gate, the plugin
hook registration and reviewer-session attribution.

    python -B tests/check_gate.py

Checks:
  h  a manual-policy session with no read_receipts cannot claim with basis=[id]:
     GATE_NO_READ and nothing is written (no claim, candidate or event)
  i  after dom_find returns that claim (a receipt exists) the same session can
     claim it: the gate passes and the candidate is recorded
  auto  review_policy='auto' has no gate: the same basis write is admitted
  k  L8: candidates.candidate_session and reviews.reviewer_session are stored;
     admit of a review with reviewer_session NULL fails REVIEWER_SESSION_REQUIRED
     and writes nothing; a review from this session admits normally
  j  index.js registers ctx.tool.hook('execute.before') with the advisory reason
  s  claim_sessions.gate_status stays NULL (reserved for enforce mode, L6)

Throw-away run directories live under the repository runtime root and are removed.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'opencode'))

from mockup.admission import Service, verify_chain  # noqa: E402
from mockup.paths import runtime_root  # noqa: E402


def _pass(name):
    print('  PASS %s' % name)


def _con(svc):
    con = sqlite3.connect(str(svc.store.path))
    con.row_factory = sqlite3.Row
    return con


def _counts(svc):
    con = _con(svc)
    try:
        return {table: con.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                for table in ('claims', 'candidates', 'events')}
    finally:
        con.close()


def main() -> int:
    saved = {key: os.environ.get(key) for key in ('DOM_SESSION', 'DOM_SESSION_SOURCE', 'DOM_ORIGIN')}
    roots = []
    try:
        # One fixed trusted session for the whole check.
        os.environ['DOM_SESSION'] = 'ses_l6_gate'
        os.environ['DOM_SESSION_SOURCE'] = 'hook'
        os.environ['DOM_ORIGIN'] = 'hook'
        token = uuid.uuid4().hex[:12]

        # ------------------------------------------------------------- (h/i) --
        tmp = runtime_root() / ('dom-gate-basis-%s' % uuid.uuid4().hex[:12])
        tmp.mkdir(parents=True)
        roots.append(tmp)
        svc = Service(tmp, profile='lite', review_mode='manual')
        assert svc.session == 'ses_l6_gate', svc.session
        started = svc.start(request='gate basis check request %s' % token)
        assert started['status'] == 'STARTED', started
        root_id = started['root']['request_claim_id']
        assert root_id, started['root']

        before = _counts(svc)
        refused = svc.claim(branch='main', type='text', text='gate basis without read %s' % token, basis=[root_id])
        assert refused.get('status') == 'GATE_NO_READ', refused
        assert refused.get('hint'), refused
        after = _counts(svc)
        assert after == before, (before, after)
        _pass('h claim with basis=[unread claim] -> GATE_NO_READ, nothing written')

        found = svc.find(branch='main', limit=20)
        assert found['status'] == 'FOUND' and any(m['claim_id'] == root_id for m in found['matches']), found
        con = _con(svc)
        try:
            receipts = con.execute('SELECT COUNT(*) FROM read_receipts WHERE claim_id=? AND session_id=?',
                                   (root_id, svc.session)).fetchone()[0]
        finally:
            con.close()
        assert receipts >= 1, receipts
        ok = svc.claim(branch='main', type='text', text='gate basis after read %s' % token, basis=[root_id])
        assert ok.get('status') == 'RECORDED_CANDIDATE', ok
        _pass('i after dom_find the same session passes the gate (receipt exists)')

        # -------------------------------------------------------------- (auto) --
        tmp_auto = runtime_root() / ('dom-gate-auto-%s' % uuid.uuid4().hex[:12])
        tmp_auto.mkdir(parents=True)
        roots.append(tmp_auto)
        auto = Service(tmp_auto, profile='lite', review_mode='auto')
        auto_started = auto.start(request='gate auto policy request %s' % token)
        auto_root = auto_started['root']['request_claim_id']
        admitted = auto.claim(branch='main', type='text', text='auto policy has no gate %s' % token, basis=[auto_root])
        assert admitted.get('status') == 'CLAIM_ADMITTED', admitted
        _pass("auto review_policy='auto' has no gate")

        # --------------------------------------------------------------- (k) --
        tmp_rev = runtime_root() / ('dom-gate-review-%s' % uuid.uuid4().hex[:12])
        tmp_rev.mkdir(parents=True)
        roots.append(tmp_rev)
        rev = Service(tmp_rev, profile='lite', review_mode='manual')
        rev.start(request='reviewer attribution request %s' % token)

        first = rev.claim(branch='main', type='text', text='candidate one %s' % token)
        assert first.get('status') == 'RECORDED_CANDIDATE', first
        cid1, dig1 = first['candidate_id'], first['intent_digest']
        second = rev.claim(branch='main', type='text', text='candidate two %s' % token)
        assert second.get('status') == 'RECORDED_CANDIDATE', second
        cid2, dig2 = second['candidate_id'], second['intent_digest']
        con = _con(rev)
        try:
            sessions = {row['id']: row['candidate_session'] for row in
                        con.execute('SELECT id, candidate_session FROM candidates WHERE id IN (?,?)', (cid1, cid2))}
        finally:
            con.close()
        assert sessions.get(cid1) == rev.session and sessions.get(cid2) == rev.session, sessions
        _pass('k candidates.candidate_session records the submitting session')

        review1 = rev.review(cid1, dig1)
        assert review1.get('status') == 'DEMO_REVIEWED', review1
        con = _con(rev)
        try:
            stored = con.execute('SELECT reviewer_session FROM reviews WHERE id=?', (review1['review_id'],)).fetchone()[0]
        finally:
            con.close()
        assert stored == rev.session, stored
        _pass('k reviews.reviewer_session records the reviewing session')

        missing = rev.review(cid2, dig2, session=None)
        assert missing.get('status') == 'DEMO_REVIEWED', missing
        con = _con(rev)
        try:
            stored = con.execute('SELECT reviewer_session FROM reviews WHERE id=?', (missing['review_id'],)).fetchone()[0]
        finally:
            con.close()
        assert stored is None, stored
        before_admit = _counts(rev)
        refused_admit = rev.admit(cid2, missing['review_id'], 'gate-missing-%s' % token, dig2)
        assert refused_admit.get('status') == 'REVIEWER_SESSION_REQUIRED', refused_admit
        assert _counts(rev) == before_admit
        _pass('k admit without reviewer_session -> REVIEWER_SESSION_REQUIRED, nothing written')

        admitted_review = rev.admit(cid1, review1['review_id'], 'gate-ok-%s' % token, dig1)
        assert admitted_review.get('status') == 'DEMO_ADMITTED', admitted_review
        with rev.store.read() as c:
            assert verify_chain(c)['status'] == 'CHAIN_OK'
        _pass('k a review from this session admits normally; chain stays OK')

        # --------------------------------------------------------------- (j) --
        index_js = (REPO / 'opencode' / 'index.js').read_text(encoding='utf8')
        assert 'ctx.tool' in index_js and 'execute.before' in index_js, 'no execute.before registration'
        assert 'GATE_NO_READ advisory' in index_js, 'no advisory reason'
        assert 'dom_claim' in index_js and 'dom_update' in index_js and 'dom_annotate' in index_js
        _pass("j index.js registers ctx.tool.hook('execute.before') for the write tools")

        # --------------------------------------------------------------- (s) --
        con = _con(rev)
        try:
            gate_written = con.execute('SELECT COUNT(*) FROM claim_sessions WHERE gate_status IS NOT NULL').fetchone()[0]
        finally:
            con.close()
        assert gate_written == 0, gate_written
        _pass('s claim_sessions.gate_status stays NULL (reserved for enforce mode)')

        print('PASS gate: L6 GATE_NO_READ (advisory), auto exempt, L8 reviewer sessions, L7 hook present')
        return 0
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for root in roots:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
