"""Check migration v5: additive claim_sessions + read_receipts tables (L2 of the
trusted-write line, task П15.2).

    python -B tests/check_migration_v5.py

Checks:
  1  fresh store -> user_version == 5 (SCHEMA_VERSION)
  2  claim_sessions table exists and is creatable
  3  read_receipts table exists and is creatable
  4  insert into claim_sessions -> readback
  5  insert into read_receipts -> readback
  6  immutability: INSERT OR REPLACE on claim_sessions with same session_id refuses
  7  immutability: INSERT OR REPLACE on read_receipts with same receipt_id refuses
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'opencode'))

from mockup.storage import Store, SCHEMA_VERSION, MIGRATION_V5_NAME, MIGRATION_V5_DDL, IMMUTABLE, IMMUTABLE_KEYS  # noqa: E402


def main() -> int:
    tmp = tempfile.mkdtemp(prefix='dom-mig5-')
    s = Store(tmp)
    s.initialize('lite:write-migration-v5-test', 'lite-policy-v5', branches=('AA', 'AB'))
    db = Path(tmp) / 'dom.sqlite3'

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    version = con.execute('PRAGMA user_version').fetchone()[0]
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()

    assert version == 5, f'user_version={version} want 5'
    assert 'claim_sessions' in tables, 'claim_sessions missing'
    assert 'read_receipts' in tables, 'read_receipts missing'
    print('  PASS v5 schema_version==5 + both tables present')

    with s.transaction() as c:
        c.execute("INSERT INTO claim_sessions(session_id, principal, branch, created_at, last_write_reason) VALUES(?,?,?,?,?)",
                  ('ses_v5_1', 'orchestrator', 'AA', '2026-11-25T00:00:00Z', 'close 0.3.0 L2'))
        row = c.execute('SELECT principal, branch, last_write_reason FROM claim_sessions WHERE session_id=?', ('ses_v5_1',)).fetchone()
        assert row is not None and row['principal'] == 'orchestrator', dict(row) if row else None
    print('  PASS v5 claim_sessions insert + readback')

    with s.transaction() as c:
        c.execute("INSERT INTO read_receipts(receipt_id, claim_id, session_id, branch, read_at, chain_head) VALUES(?,?,?,?,?,?)",
                  ('rcpt_v5_1', '6ee359c7-a72e-4ad6-a37c-0aecfe454611', 'ses_v5_1', 'AA', '2026-11-25T00:02:00Z', 'abc123'))
        row = c.execute('SELECT claim_id, session_id, chain_head FROM read_receipts WHERE receipt_id=?', ('rcpt_v5_1',)).fetchone()
        assert row is not None and row['chain_head'] == 'abc123', dict(row) if row else None
    print('  PASS v5 read_receipts insert + readback')

    # claim_sessions is mutable operational state: one row per session, updated
    # on every write.  A plain duplicate insert is a UNIQUE error, an upsert
    # must update last_write_at/last_claim_id (no IMMUTABLE trigger).
    try:
        with s.transaction() as c:
            c.execute("INSERT INTO claim_sessions(session_id, principal, branch, created_at) VALUES(?,?,?,?)",
                      ('ses_v5_1', 'someone-else', 'AA', '2026-11-25T00:00:00Z'))
        raise AssertionError('duplicate claim_sessions insert should be refused')
    except sqlite3.IntegrityError as e:
        assert 'UNIQUE' in str(e).upper(), f'unexpected error: {e}'
    with s.transaction() as c:
        c.execute("INSERT INTO claim_sessions(session_id, principal, branch, created_at, last_write_at, last_claim_id) "
                  "VALUES(?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET last_write_at=excluded.last_write_at, "
                  "last_claim_id=excluded.last_claim_id",
                  ('ses_v5_1', 'orchestrator', 'AA', '2026-11-25T00:00:00Z', '2026-11-25T00:05:00Z', 'claim_v5_2'))
        row = c.execute('SELECT principal, last_claim_id FROM claim_sessions WHERE session_id=?', ('ses_v5_1',)).fetchone()
        assert row is not None and row['last_claim_id'] == 'claim_v5_2', dict(row) if row else None
    print('  PASS v5 claim_sessions mutable upsert')

    try:
        with s.transaction() as c:
            c.execute("INSERT OR REPLACE INTO read_receipts(receipt_id, claim_id, session_id, branch, read_at) VALUES(?,?,?,?,?)",
                      ('rcpt_v5_1', 'x', 'ses_v5_1', 'AA', '2026-11-25T00:03:00Z'))
        raise AssertionError('INSERT OR REPLACE into read_receipts should be refused')
    except (sqlite3.IntegrityError, sqlite3.OperationalError) as e:
        assert 'IMMUTABLE' in str(e), f'unexpected error: {e}'
    print('  PASS v5 read_receipts immutability')

    print('PASS migration v5: schema_version=%s name=%s ddl=%d stmts mutable_claim_sessions=%s immutable_receipts=%s' % (
        SCHEMA_VERSION, MIGRATION_V5_NAME, len(MIGRATION_V5_DDL), 'claim_sessions' not in IMMUTABLE, 'read_receipts' in IMMUTABLE))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
