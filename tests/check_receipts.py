"""L3/L4/L5 of the trusted-write line: claim/read receipts, session identity,
database-path health.

    python -B tests/check_receipts.py

Checks:
  a  V5 columns: claim_sessions(session_source, origin, db_realpath, gate_status)
     and read_receipts(session_source, origin, db_realpath)
  b  resolve_session: no DOM_SESSION -> mcp-unverified; hook default;
     mcp-plugin kept; bogus / mcp-unverified source -> unknown
  c  a real write -> exactly one claim_sessions row with session/source/origin/
     db_realpath/last_claim_id
  d  rollback at before_commit leaves no claim row and no claim_sessions row
     (the two commit together, L3)
  e  find() writes read_receipts in the same transaction (same read_at/head)
  f  planning.context() writes a receipt for the requested claim id
  g  health: same path -> OK, a different path -> DB_PATH_MISMATCH, a case
     variant -> OK on a case-insensitive host; describe exposes session/db/health

Uses a throw-away run directory under the repository runtime root and removes it.
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
from mockup.public_dto import project_tool  # noqa: E402
from mockup.session import (  # noqa: E402
    SessionIdentity, normalize_db_path, resolve_session, same_db_path,
)


def _pass(name):
    print('  PASS %s' % name)


def _con(svc):
    con = sqlite3.connect(str(svc.store.path))
    con.row_factory = sqlite3.Row
    return con


def _columns(db, table):
    con = sqlite3.connect(str(db))
    try:
        return {row[1] for row in con.execute('PRAGMA table_info(' + table + ')')}
    finally:
        con.close()


def main() -> int:
    tmp = runtime_root() / ('dom-receipts-%s' % uuid.uuid4().hex[:12])
    tmp.mkdir(parents=True)
    saved = {key: os.environ.get(key) for key in ('DOM_SESSION', 'DOM_SESSION_SOURCE', 'DOM_ORIGIN')}
    try:
        # ------------------------------------------------------------- (b) --
        assert resolve_session({}) == SessionIdentity('mcp-unverified', 'mcp-unverified', 'mcp'), resolve_session({})
        assert resolve_session({'DOM_SESSION': 'ses_x'}) == SessionIdentity('ses_x', 'hook', 'hook')
        assert resolve_session({'DOM_SESSION': 'ses_x', 'DOM_SESSION_SOURCE': 'mcp-plugin'}) == SessionIdentity('ses_x', 'mcp-plugin', 'hook')
        assert resolve_session({'DOM_SESSION': 'ses_x', 'DOM_SESSION_SOURCE': 'director', 'DOM_ORIGIN': 'director'}) == SessionIdentity('ses_x', 'director', 'director')
        for bad in ('bogus', 'mcp-unverified'):
            got = resolve_session({'DOM_SESSION': 'ses_x', 'DOM_SESSION_SOURCE': bad})
            assert got == SessionIdentity('ses_x', 'unknown', 'hook'), (bad, got)
        _pass('b resolve_session sources (mcp-unverified / hook / mcp-plugin / unknown)')

        # ---------------------------------------------- a real Service ------
        os.environ['DOM_SESSION'] = 'ses_l3_receipts'
        os.environ['DOM_SESSION_SOURCE'] = 'hook'
        os.environ['DOM_ORIGIN'] = 'hook'
        try:
            svc = Service(tmp, profile='lite')
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        assert svc.session == 'ses_l3_receipts' and svc.session_source == 'hook' and svc.origin == 'hook', (
            svc.session, svc.session_source, svc.origin)
        expected = os.path.realpath(str(svc.store.path))
        assert svc.db_realpath == expected, (svc.db_realpath, expected)

        started = svc.start(request='trusted-write receipts self-check')
        assert started['status'] == 'STARTED', started
        db = svc.store.path

        # ------------------------------------------------------------- (a) --
        cs_cols = _columns(db, 'claim_sessions')
        rr_cols = _columns(db, 'read_receipts')
        assert {'session_source', 'origin', 'db_realpath', 'gate_status'} <= cs_cols, sorted(cs_cols)
        assert {'session_source', 'origin', 'db_realpath'} <= rr_cols, sorted(rr_cols)
        _pass('a V5 columns on claim_sessions and read_receipts')

        # ------------------------------------------------------------- (c) --
        con = _con(svc)
        try:
            rows = con.execute('SELECT * FROM claim_sessions WHERE session_id=?', (svc.session,)).fetchall()
            assert len(rows) == 1, [dict(r) for r in rows]
            row = rows[0]
            assert row['principal'] == svc.context.principal, (row['principal'], svc.context.principal)
            assert row['branch'] == 'main', row['branch']
            assert row['session_source'] == 'hook' and row['origin'] == 'hook', dict(row)
            assert row['db_realpath'] == expected, (row['db_realpath'], expected)
            assert row['last_claim_id'] and con.execute('SELECT 1 FROM claims WHERE id=?', (row['last_claim_id'],)).fetchone(), row['last_claim_id']
            assert row['created_at'] and row['last_write_at'] and row['updated_at'], dict(row)
        finally:
            con.close()
        _pass('c start() wrote one claim_sessions row with identity + db_realpath')

        token = uuid.uuid4().hex[:12]
        claim_text = 'L3 receipt needle alpha %s' % token
        written = svc.claim(branch='main', type='text', text=claim_text)
        assert written.get('status') == 'CLAIM_ADMITTED', written
        claim_id = written['claim_id']
        con = _con(svc)
        try:
            count = con.execute('SELECT COUNT(*) FROM claim_sessions WHERE session_id=?', (svc.session,)).fetchone()[0]
            assert count == 1, count
            row = con.execute('SELECT * FROM claim_sessions WHERE session_id=?', (svc.session,)).fetchone()
            assert row['last_claim_id'] == claim_id, (row['last_claim_id'], claim_id)
        finally:
            con.close()
        _pass('c claim() upserts the same session row, last_claim_id = new claim')

        # ------------------------------------------------------------- (d) --
        with svc.store.transaction() as c:
            c.execute('DELETE FROM claim_sessions')
            before_claims = c.execute('SELECT COUNT(*) FROM claims').fetchone()[0]

        def _fault(stage):
            if stage == 'before_commit':
                raise RuntimeError('injected before_commit')

        svc.fault_hook = _fault
        failed = False
        try:
            svc.claim(branch='main', type='text', text='L3 rollback needle beta %s' % token)
        except Exception:
            failed = True
        finally:
            svc.fault_hook = lambda stage: None
        assert failed, 'the injected before_commit fault did not propagate'
        with svc.store.read() as c:
            after_claims = c.execute('SELECT COUNT(*) FROM claims').fetchone()[0]
            sessions = c.execute('SELECT COUNT(*) FROM claim_sessions').fetchone()[0]
        assert after_claims == before_claims, (before_claims, after_claims)
        assert sessions == 0, sessions
        _pass('d rollback removes both the claim and the claim_sessions row')

        # ------------------------------------------------------------- (e) --
        found = svc.find(query='needle alpha %s' % token, branch='main')
        assert found['status'] == 'FOUND' and found['matches'], found
        matched_ids = {m['claim_id'] for m in found['matches']}
        assert claim_id in matched_ids, (claim_id, matched_ids)
        with svc.store.read() as c:
            chain_head = verify_chain(c)['head_hash']
        con = _con(svc)
        try:
            receipts = con.execute('SELECT * FROM read_receipts WHERE claim_id=? AND session_id=?',
                                   (claim_id, svc.session)).fetchall()
            assert receipts, 'no read receipt for the returned match'
            receipt = receipts[-1]
            assert receipt['branch'] == 'main', dict(receipt)
            assert receipt['read_at'] == found['read_snapshot']['read_at'], (receipt['read_at'], found['read_snapshot']['read_at'])
            assert receipt['chain_head'] == chain_head, (receipt['chain_head'], chain_head)
            assert receipt['admitted_at'] is None, dict(receipt)
            assert receipt['session_source'] == 'hook' and receipt['origin'] == 'hook', dict(receipt)
            assert receipt['db_realpath'] == expected, (receipt['db_realpath'], expected)
        finally:
            con.close()
        _pass('e find() receipt: same read_at, same chain_head, no admitted_at')

        # ------------------------------------------------------------- (f) --
        ctx = svc.context_pack(claim_id)
        assert ctx['status'] == 'CONTEXT', ctx
        con = _con(svc)
        try:
            receipt = con.execute('SELECT * FROM read_receipts WHERE claim_id=? AND session_id=? ORDER BY rowid DESC LIMIT 1',
                                  (claim_id, svc.session)).fetchone()
            assert receipt is not None, 'no context receipt for the requested claim'
            assert receipt['branch'] == 'main', dict(receipt)
            assert receipt['read_at'] == ctx['read_snapshot']['read_at'], (receipt['read_at'], ctx['read_snapshot']['read_at'])
            assert receipt['chain_head'] == chain_head, (receipt['chain_head'], chain_head)
        finally:
            con.close()
        _pass('f planning.context() receipt for the requested claim id')

        # ------------------------------------------------------------- (g) --
        described = svc.describe()
        assert described['session'] == {'id': svc.session, 'source': svc.session_source, 'origin': svc.origin}, described['session']
        assert described['db']['realpath'] == expected, described['db']
        assert described['health']['status'] == 'OK', described['health']
        assert described['health']['expected'] == expected, described['health']
        assert all(same_db_path(p, expected) for p in described['health']['seen']), described['health']
        assert described['db_path'] == expected, described['db_path']
        public = project_tool('dom_describe', described, profile='lite')
        assert public['session'] == described['session'] and public['db'] == described['db'] and public['health'] == described['health']
        _pass('g describe exposes session/db/health; same path -> OK')

        fake = str(Path(expected).parent / 'elsewhere' / 'dom.sqlite3')
        with svc.store.transaction() as c:
            c.execute('INSERT INTO claim_sessions(session_id, principal, branch, created_at, db_realpath) VALUES(?,?,?,?,?)',
                      ('ses_health_fake', 'health-check', 'main', '2026-11-26T00:00:00+00:00', fake))
        mismatched = svc.describe()['health']
        assert mismatched['status'] == 'DB_PATH_MISMATCH', mismatched
        assert fake in mismatched['seen'] and fake in mismatched['mismatch'], mismatched
        assert expected not in mismatched['mismatch'], mismatched
        _pass('g a different recorded path -> DB_PATH_MISMATCH')

        case_variant = expected.upper()
        if case_variant == expected:
            case_variant = expected.swapcase()
        with svc.store.transaction() as c:
            c.execute('UPDATE claim_sessions SET db_realpath=? WHERE session_id=?', (case_variant, 'ses_health_fake'))
        cased = svc.describe()['health']
        if os.path.normcase('A') == 'a':
            assert cased['status'] == 'OK', cased
            assert same_db_path(expected, case_variant)
            _pass('g a case variant of the same path -> OK (case-insensitive host)')
        else:
            assert cased['status'] == 'DB_PATH_MISMATCH', cased
            _pass('g a case variant is a different path on a case-sensitive host')

        with svc.store.transaction() as c:
            c.execute('DELETE FROM claim_sessions WHERE session_id=?', ('ses_health_fake',))
        assert svc.describe()['health']['status'] == 'OK'
        assert normalize_db_path(expected) == normalize_db_path(case_variant) or os.path.normcase('A') != 'a'
        _pass('g health returns to OK; normalize_db_path is the comparison basis')

        print('PASS receipts: L3 same-transaction receipts, L4 session identity, L5 db-path health')
        return 0
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
