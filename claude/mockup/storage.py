"""Local SQLite authority. All historical tables have SQL immutability triggers."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3

from .delivery import DDL as DELIVERY_DDL, KEYS as DELIVERY_KEYS

class MigrationRequired(Exception): pass

# WS-F02: idempotency v2 layout (extended PK + binding columns). Marker stored in
# PRAGMA user_version; a DB with the old layout and user_version >= this value is
# inconsistent and refuses (MIGRATION_REQUIRED, never a silent rebuild).
IDEMPOTENCY_SCHEMA_VERSION = 1

# AB006 (RS007 W1, option A): migration v2 — immutable candidate birthmark
# ``created_at`` plus the two join indexes the pending view needs
# (admitted-predicate on claims/idempotency). ``user_version`` is a monotone
# schema level: >= 2 means v2 is expected, so a v2-level DB whose candidates
# table lost ``created_at`` refuses (MIGRATION_REQUIRED) instead of silently
# rebuilding. Additive on legacy DBs; old rows keep created_at NULL (legacy →
# age_seconds omitted, never 0).
CANDIDATES_SCHEMA_VERSION = 2

# WR-ADD (L0+L1): migration v3 — additive ``reviews.review_level`` (NULL for rows
# written before v3; never backfilled, never guessed) plus the immutable
# ``project_root`` (D-01 root request/goal) and ``adoptions`` (D-02) tables.
# Same rule as v2: ``user_version`` is a monotone level; a v3-stamped DB that
# lost the column refuses (MIGRATION_REQUIRED) instead of being silently rebuilt.
SCHEMA_VERSION = 4

# v4 (independent review T-01/T-02, TD-DOM-37 policy: _migrate_vN + ledger + DOWN):
# who sent a plan revision (plan_checks.principal/session), plan-item conflicts
# (plan_conflicts) and the review policy log; recorded in schema_migrations.
# Additive only: old rows keep principal/session NULL (never guessed).
MIGRATION_V4_NAME = 'plan attribution, plan conflicts, review policy log'
MIGRATION_V4_DOWN = ('ALTER TABLE plan_checks DROP COLUMN session; ALTER TABLE plan_checks DROP COLUMN principal; '
                     'DROP TABLE plan_conflicts; DROP TABLE review_policy_log; DELETE FROM schema_migrations WHERE version=4 '
                     '(drop the immutable_* triggers of these tables first; then PRAGMA user_version=3)')

MIGRATION_V2_DDL = [
    "ALTER TABLE candidates ADD COLUMN created_at TEXT",
    "CREATE INDEX IF NOT EXISTS idx_idempotency_candidate ON idempotency(candidate_id)",
    "CREATE INDEX IF NOT EXISTS idx_claims_candidate ON claims(candidate)",
]

IMMUTABLE=('schema_migrations','plan_conflicts','review_policy_log','binding','candidates','claims','claim_snapshots','events','reviews','transactions','idempotency','outbox','delivery_events','project_root','adoptions','plan_tasks','plan_task_versions','plan_branches','plan_checks')
PLAN_SOURCES=('OMH','agent')
# Every UNIQUE constraint of each immutable table.  ``INSERT OR REPLACE`` /
# ``OR IGNORE`` / plain ``REPLACE`` resolve a conflict by deleting the stored
# row, which the ``BEFORE DELETE`` trigger does not see while
# ``recursive_triggers`` is off (the default).  The conflict can be on the PK
# *or* on a secondary UNIQUE key, so both are guarded.
IMMUTABLE_KEYS={
    'review_policy_log':(('seq',),),
    'plan_conflicts':(('seq',),),
    'schema_migrations':(('version',),),
    'binding':(('id',),('work_ref',)),
    'candidates':(('id',),),
    'claims':(('id',),),
    'claim_snapshots':(('claim_id',),),
    'events':(('seq',),('id',)),
    'reviews':(('id',),),
    'transactions':(('id',),('branch','revision')),
    'idempotency':(('external_work_ref','operation','key','scope_digest'),),
    'outbox':(('id',),('tx','kind','target')),
    'delivery_events':(('seq',),),
    'project_root':(('id',),),
    'adoptions':(('seq',),('new_work_ref','source_work_ref')),
    'plan_tasks':(('plan_task_id',),),
    'plan_task_versions':(('plan_task_id','version'),),
    'plan_branches':(('plan_task_id',),('branch',)),
    'plan_checks':(('seq',),),
}
DDL='''
CREATE TABLE IF NOT EXISTS binding(id INTEGER PRIMARY KEY CHECK(id=1), work_ref TEXT UNIQUE NOT NULL, policy TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS branch_heads(branch TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision>=0));
CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, digest TEXT NOT NULL, payload BLOB NOT NULL, author TEXT NOT NULL, root TEXT NOT NULL, branch TEXT NOT NULL REFERENCES branch_heads(branch), created_at TEXT);
CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, candidate TEXT NOT NULL REFERENCES candidates(id), payload BLOB NOT NULL, review_level TEXT);
CREATE TABLE IF NOT EXISTS transactions(id TEXT PRIMARY KEY, branch TEXT NOT NULL REFERENCES branch_heads(branch), revision INTEGER NOT NULL, receipt BLOB NOT NULL, UNIQUE(branch,revision));
CREATE TABLE IF NOT EXISTS claims(id TEXT PRIMARY KEY, candidate TEXT NOT NULL REFERENCES candidates(id), tx TEXT NOT NULL REFERENCES transactions(id), content_type TEXT NOT NULL, raw BLOB NOT NULL, raw_sha256 TEXT NOT NULL, encoding TEXT NOT NULL, media_type TEXT NOT NULL, source_ref TEXT NOT NULL, origin_branch TEXT NOT NULL, origin_version TEXT NOT NULL, payload BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, tx TEXT NOT NULL REFERENCES transactions(id), claim_ref TEXT REFERENCES claims(id), branch TEXT NOT NULL, revision INTEGER NOT NULL, kind TEXT NOT NULL, payload BLOB NOT NULL, previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claim_snapshots(claim_id TEXT PRIMARY KEY REFERENCES claims(id), tx TEXT NOT NULL REFERENCES transactions(id), branch TEXT NOT NULL, revision INTEGER NOT NULL, dependencies TEXT NOT NULL, review_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS idempotency(external_work_ref TEXT NOT NULL, operation TEXT NOT NULL, key TEXT NOT NULL, scope_digest TEXT NOT NULL DEFAULT '', digest TEXT NOT NULL, result BLOB NOT NULL, candidate_id TEXT, review_id TEXT, branch TEXT, result_scope TEXT, PRIMARY KEY(external_work_ref,operation,key,scope_digest));
CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, tx TEXT NOT NULL REFERENCES transactions(id), kind TEXT NOT NULL, target TEXT NOT NULL, branch TEXT NOT NULL, revision INTEGER NOT NULL, digest TEXT NOT NULL, payload BLOB NOT NULL, UNIQUE(tx,kind,target));
CREATE TABLE IF NOT EXISTS delivery_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, intent TEXT NOT NULL REFERENCES outbox(id), state TEXT NOT NULL, payload BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS project_root(id INTEGER PRIMARY KEY CHECK(id=1), request_claim_id TEXT NOT NULL REFERENCES claims(id), goal_claim_id TEXT REFERENCES claims(id), goal_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(goal_confirmed IN (0,1)), request_sha256 TEXT NOT NULL, goal_sha256 TEXT, branch TEXT NOT NULL, created_at TEXT NOT NULL);
-- QA004 F-18: the review policy a project was run under; a later launch never runs it under a weaker one.
CREATE TABLE IF NOT EXISTS review_policy_log(seq INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT NOT NULL CHECK(mode IN ('auto','manual','independent')), set_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS adoptions(seq INTEGER PRIMARY KEY AUTOINCREMENT, new_work_ref TEXT NOT NULL, source_work_ref TEXT NOT NULL, source_policy TEXT NOT NULL, chain_hash TEXT NOT NULL, user_version_at INTEGER NOT NULL, adopted_at TEXT NOT NULL, UNIQUE(new_work_ref,source_work_ref));

-- P2 (D1.1): OMH plan projection. Identity is stable; content history is
-- append-only; current state is a view. task_claims is insert-only (QA004 F-25:
-- UPDATE/DELETE triggers); a relink adds a row, the old one stays as history.
-- plan_projection_state is a mutable sync cursor.
CREATE TABLE IF NOT EXISTS plan_tasks(
  plan_task_id TEXT PRIMARY KEY,
  source TEXT NOT NULL CHECK(source IN ('OMH','agent')),
  external_ref TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_task_versions(
  plan_task_id TEXT NOT NULL REFERENCES plan_tasks(plan_task_id),
  version INTEGER NOT NULL CHECK(version>=1),
  path TEXT NOT NULL,
  parent_id TEXT,
  status TEXT NOT NULL,
  title TEXT NOT NULL,
  task_type TEXT,
  source_revision TEXT NOT NULL,
  digest TEXT NOT NULL,
  semantic_digest TEXT NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
  observed_at TEXT NOT NULL,
  PRIMARY KEY(plan_task_id,version)
);
CREATE VIEW IF NOT EXISTS current_plan_tasks AS
SELECT t.plan_task_id, t.source, t.external_ref, t.created_at, v.archived,
       v.version, v.path, v.parent_id, v.status, v.title, v.task_type,
       v.source_revision, v.digest, v.semantic_digest, v.observed_at
FROM plan_tasks t
JOIN plan_task_versions v ON v.plan_task_id=t.plan_task_id
 AND v.version=(SELECT MAX(version) FROM plan_task_versions p
                WHERE p.plan_task_id=t.plan_task_id);
CREATE TABLE IF NOT EXISTS task_claims(
  plan_task_id TEXT NOT NULL REFERENCES plan_tasks(plan_task_id),
  claim_id TEXT NOT NULL REFERENCES claims(id),
  role TEXT NOT NULL CHECK(role IN ('defines','justifies','implements','validates','documents','depends_on')),
  linked_at_plan_version INTEGER NOT NULL,
  linked_semantic_digest TEXT NOT NULL,
  linked_at TEXT NOT NULL,
  PRIMARY KEY(plan_task_id,claim_id,role)
);
CREATE TABLE IF NOT EXISTS plan_projection_state(
  source TEXT PRIMARY KEY CHECK(source IN ('OMH','agent')),
  source_revision TEXT,
  last_checked_at TEXT NOT NULL,
  last_changed_at TEXT,
  snapshot_digest TEXT
);
-- Plan memory (docs/PLAN_MCP_PROJECT_MEMORY_2026-11-12.md): the branch code
-- of a plan item is allocated once and never re-assigned (identity, not
-- position).  kind='variant' rows are parallel branches of an item (fork).
CREATE TABLE IF NOT EXISTS plan_branches(
  plan_task_id TEXT PRIMARY KEY,
  branch TEXT UNIQUE NOT NULL,
  parent_branch TEXT,
  level INTEGER NOT NULL CHECK(level>=1),
  slot INTEGER NOT NULL CHECK(slot>=1),
  kind TEXT NOT NULL DEFAULT 'item' CHECK(kind IN ('item','variant')),
  allocated_at TEXT NOT NULL
);
-- Every look at a plan source (changed or not): the duty mode counts
-- iterations since the last change from here and decides when to ask.
CREATE TABLE IF NOT EXISTS plan_checks(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  snapshot_digest TEXT,
  changed INTEGER NOT NULL CHECK(changed IN (0,1)),
  kind TEXT NOT NULL DEFAULT 'check' CHECK(kind IN ('check','request','ingest')),
  note TEXT,
  at TEXT NOT NULL,
  principal TEXT,
  session TEXT
);
-- v4 / T-01: a plan sent on an older revision than the current one; open until the
-- same principal ingests again (read the current plan, then send or force).
CREATE TABLE IF NOT EXISTS plan_conflicts(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  base_revision TEXT NOT NULL,
  current_revision TEXT NOT NULL,
  principal TEXT,
  session TEXT,
  changed_by TEXT,
  item_paths TEXT NOT NULL,
  forced INTEGER NOT NULL DEFAULT 0 CHECK(forced IN (0,1)),
  at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL, down TEXT NOT NULL, applied_at TEXT NOT NULL);
-- P1.3: baseline is a projection of the append-only event log, never a table.
-- ``baseline_view`` numbers claim chunks per branch (D3); ``branch_event_view``
-- numbers every branch-local event so recall can stop before an archive or a
-- replacement (the event, not the claim, is the recall boundary).
CREATE VIEW IF NOT EXISTS baseline_view AS
SELECT branch, ROW_NUMBER() OVER (PARTITION BY branch ORDER BY seq) AS seq,
       claim_ref AS claim_id,
       ROW_NUMBER() OVER (PARTITION BY branch ORDER BY seq) - 1 AS prev_seq,
       event_hash AS chunk_hash, revision
FROM events WHERE kind='claim';
CREATE VIEW IF NOT EXISTS branch_event_view AS
SELECT branch, seq AS event_seq,
       ROW_NUMBER() OVER (PARTITION BY branch ORDER BY seq) AS seq,
       id AS event_id, claim_ref AS claim_id, kind, revision,
       event_hash AS chunk_hash,
       ROW_NUMBER() OVER (PARTITION BY branch ORDER BY seq) - 1 AS prev_seq
FROM events;
'''

# Two sessions on one project share the file (journal_mode=DELETE): a reader waits for
# a writer's fsync.  2 s was too short under load (QA004 F-30); 10 s still fails fast
# on a really stuck lock.
BUSY_TIMEOUT_MS=10000

class Store:
    def __init__(self,directory): self.path=Path(directory)/'dom.sqlite3'
    def connect(self):
        if getattr(self,'read_only',False):
            c=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=BUSY_TIMEOUT_MS/1000,isolation_level=None)
        else: c=sqlite3.connect(self.path,timeout=BUSY_TIMEOUT_MS/1000,isolation_level=None)
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA synchronous=FULL'); c.execute('PRAGMA busy_timeout=%d' % BUSY_TIMEOUT_MS)
        # Invariants are checked explicitly: ``assert`` would vanish under ``python -O``.
        if c.execute('PRAGMA foreign_keys').fetchone()[0]!=1: raise RuntimeError('PRAGMA_FOREIGN_KEYS_NOT_ENFORCED')
        if c.execute('PRAGMA synchronous').fetchone()[0]!=2: raise RuntimeError('PRAGMA_SYNCHRONOUS_NOT_FULL')
        return c
    def initialize(self,work,policy,branches=('AA','AB'),declare=(),adopt=None):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(self.connect()) as c:
            if c.execute('PRAGMA journal_mode=DELETE').fetchone()[0]!='delete': raise RuntimeError('JOURNAL_MODE_NOT_DELETE')
            # Refuse incompatible legacy layouts before changing any schema.
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required={'claims':{'id','content_type','encoding','media_type','source_ref'},
                      'claim_snapshots':{'claim_id','dependencies','review_id'},
                      'outbox':{'id','tx','kind','target','branch','revision','digest','payload'}}
            if tables and any(t not in tables or not cols <= {r[1] for r in c.execute('PRAGMA table_info('+t+')')} for t,cols in required.items()):
                raise MigrationRequired('MIGRATION_REQUIRED')
            # WS-F02 idempotency v2 migration (user_version gate): the old PK
            # (work,operation,key) makes a legal retry after a legacy refusal
            # impossible (UNIQUE/IMMUTABLE). Rebuild the table with the extended
            # PK; legacy rows become scope_digest='' + NULL binding (inert).
            if 'idempotency' in tables:
                cols={r[1] for r in c.execute('PRAGMA table_info(idempotency)')}
                if 'scope_digest' not in cols:
                    version=c.execute('PRAGMA user_version').fetchone()[0]
                    if version>=IDEMPOTENCY_SCHEMA_VERSION:
                        raise MigrationRequired('MIGRATION_REQUIRED')
                    c.execute('BEGIN IMMEDIATE')
                    try:
                        self._migrate_idempotency(c)
                        c.commit()
                    except BaseException:
                        c.rollback(); raise
            c.executescript(DDL+DELIVERY_DDL)
            self._create_triggers(c)
            # AB006 migration v2 (created_at + join indexes). Additive; idempotent
            # on re-run. The v2 level is monotone, so this must run before the
            # final user_version stamp below.
            cand_cols={r[1] for r in c.execute('PRAGMA table_info(candidates)')}
            if 'created_at' not in cand_cols:
                version=c.execute('PRAGMA user_version').fetchone()[0]
                if version>=CANDIDATES_SCHEMA_VERSION:
                    raise MigrationRequired('MIGRATION_REQUIRED')
                c.execute('BEGIN IMMEDIATE')
                try:
                    for stmt in MIGRATION_V2_DDL: c.execute(stmt)
                    c.commit()
                except BaseException:
                    c.rollback(); raise
                # Defensive re-creation (IF NOT EXISTS, idempotent): a future
                # table-rebuild step may drop triggers with the old table.
                self._create_triggers(c)
            else:
                # created_at already present (fresh DB or prior run): only the
                # indexes may still be missing on legacy copies — ensure them.
                for stmt in MIGRATION_V2_DDL[1:]: c.execute(stmt)
            # WR-ADD migration v3 (reviews.review_level). Additive, idempotent.
            review_cols={r[1] for r in c.execute('PRAGMA table_info(reviews)')}
            if 'review_level' not in review_cols:
                version=c.execute('PRAGMA user_version').fetchone()[0]
                if version>=SCHEMA_VERSION:
                    raise MigrationRequired('MIGRATION_REQUIRED')
                c.execute('BEGIN IMMEDIATE')
                try:
                    c.execute('ALTER TABLE reviews ADD COLUMN review_level TEXT')
                    c.commit()
                except BaseException:
                    c.rollback(); raise
                self._create_triggers(c)
            self._migrate_v4(c)
            c.execute('BEGIN IMMEDIATE')
            try:
                row=c.execute('SELECT * FROM binding').fetchone()
                if row and (row['work_ref']!=work or row['policy']!=policy):
                    # D-02: a foreign base can only be taken over by an explicit,
                    # append-only adoption record naming the exact source work.
                    already=c.execute('SELECT 1 FROM adoptions WHERE new_work_ref=? AND source_work_ref=?',(work,row['work_ref'])).fetchone()
                    if already: pass
                    elif adopt and adopt.get('source_work')==row['work_ref']:
                        c.execute('INSERT INTO adoptions(new_work_ref,source_work_ref,source_policy,chain_hash,user_version_at,adopted_at) VALUES(?,?,?,?,?,?)',
                                  (work,row['work_ref'],row['policy'],adopt['chain_hash'],c.execute('PRAGMA user_version').fetchone()[0],adopt['at']))
                    else: raise ValueError('BINDING_CONFLICT')
                if not row:
                    c.execute('INSERT INTO binding VALUES(1,?,?)',(work,policy))
                    c.executemany('INSERT INTO branch_heads VALUES(?,0)',[(b,) for b in branches])
                for b in declare:
                    c.execute('INSERT OR IGNORE INTO branch_heads VALUES(?,0)',(b,))
                c.commit()
            except BaseException: c.rollback(); raise
            c.execute('PRAGMA user_version = %d' % SCHEMA_VERSION)
    def _migrate_v4(self,c):
        """v4: additive columns on plan_checks + ledger row.  The tables come from DDL
        (IF NOT EXISTS).  A v4-stamped DB that lost the columns refuses (MIGRATION_REQUIRED)."""
        from datetime import datetime, timezone
        cols={r[1] for r in c.execute('PRAGMA table_info(plan_checks)')}
        c.execute('BEGIN IMMEDIATE')
        try:
            if 'principal' not in cols or 'session' not in cols:
                if c.execute('PRAGMA user_version').fetchone()[0]>=4: raise MigrationRequired('MIGRATION_REQUIRED')
                if 'principal' not in cols: c.execute('ALTER TABLE plan_checks ADD COLUMN principal TEXT')
                if 'session' not in cols: c.execute('ALTER TABLE plan_checks ADD COLUMN session TEXT')
            if c.execute('SELECT 1 FROM schema_migrations WHERE version=4').fetchone() is None:
                c.execute('INSERT INTO schema_migrations(version,name,down,applied_at) VALUES(4,?,?,?)',
                          (MIGRATION_V4_NAME,MIGRATION_V4_DOWN,datetime.now(timezone.utc).isoformat()))
            c.commit()
        except BaseException:
            c.rollback(); raise
        self._create_triggers(c)
    def migrate(self):
        """Schema-only upgrade of an existing database (no binding change): DDL, triggers,
        v4.  Lite opens call it when user_version is below SCHEMA_VERSION."""
        with closing(self.connect()) as c:
            version=c.execute('PRAGMA user_version').fetchone()[0]
            if version>=SCHEMA_VERSION or version<3: return False  # older layouts migrate through dom_start
            c.executescript(DDL+DELIVERY_DDL)
            self._create_triggers(c)
            self._migrate_v4(c)
            c.execute('PRAGMA user_version = %d' % SCHEMA_VERSION)
        return True
    def _create_triggers(self,c):
        for table in IMMUTABLE+tuple(DELIVERY_KEYS):
            for verb in ('UPDATE','DELETE'):
                c.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{verb} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'IMMUTABLE'); END")
            # R2-1: ``INSERT OR REPLACE`` resolves a PK/UNIQUE conflict by
            # deleting the stored row, and with ``recursive_triggers`` off
            # that delete never reaches the trigger above.  Refusing any
            # insert whose key already exists closes the REPLACE/IGNORE
            # path for every client: the guard is stored in the schema, so
            # it also applies to raw connections that never run
            # ``Store.connect()`` and never switch the PRAGMA on.
            parts=[]
            for key in (DELIVERY_KEYS.get(table) or IMMUTABLE_KEYS[table]):
                test=' AND '.join(f'{col}=NEW.{col}' for col in key)
                # ``seq`` is a rowid alias: undefined for an insert that
                # does not supply it, so that clause must also test not-null.
                if 'seq' in key: test='NEW.seq IS NOT NULL AND '+test
                parts.append(f'EXISTS(SELECT 1 FROM {table} WHERE {test})')
            c.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_INSERT BEFORE INSERT ON {table} "
                      f"WHEN {' OR '.join(parts)} BEGIN SELECT RAISE(ABORT,'IMMUTABLE'); END")
        # QA004 F-25: links of claims to plan items are append-only too.  No INSERT
        # guard: planning.link relies on INSERT OR IGNORE being a no-op for a known link.
        for verb in ('UPDATE','DELETE'):
            c.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_task_claims_{verb} BEFORE {verb} ON task_claims BEGIN SELECT RAISE(ABORT,'IMMUTABLE'); END")
        # TD-DOM-12: branch_heads is mutable but MONOTONIC.  A rewind of the
        # head (UPDATE ... SET revision=lower) hides transactions already
        # stored and lets STALE_CHECK disappear, producing false readiness.
        # Block any decrease, and any head below the max transaction revision.
        c.execute("CREATE TRIGGER IF NOT EXISTS branch_heads_monotonic_update BEFORE UPDATE ON branch_heads "
                  "WHEN NEW.revision < OLD.revision "
                  "BEGIN SELECT RAISE(ABORT,'BRANCH_HEAD_REWIND'); END")
        c.execute("CREATE TRIGGER IF NOT EXISTS branch_heads_invariant_update BEFORE UPDATE ON branch_heads "
                  "WHEN NEW.revision < COALESCE((SELECT MAX(revision) FROM transactions WHERE branch=NEW.branch),0) "
                  "BEGIN SELECT RAISE(ABORT,'BRANCH_HEAD_BELOW_TX'); END")
        # A branch head may never be deleted: that would also hide history and
        # break the FK from transactions.
        c.execute("CREATE TRIGGER IF NOT EXISTS branch_heads_no_delete BEFORE DELETE ON branch_heads "
                  "BEGIN SELECT RAISE(ABORT,'BRANCH_HEAD_DELETE'); END")
        # Append-only chain guard: a new event must extend the stored hash chain.
        # It does not replace verification (see admission.verify_chain), it only
        # blocks inserts that do not continue the current chain state.
        c.execute("CREATE TRIGGER IF NOT EXISTS chain_previous_hash BEFORE INSERT ON events "
                  "BEGIN SELECT RAISE(ABORT,'CHAIN_INTEGRITY') WHERE NEW.previous_hash <> "
                  "COALESCE((SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1),'" + '0' * 64 + "'); END")
    def _migrate_idempotency(self,c):
        # Destructive rebuild of the immutable idempotency table (extended PK):
        # legacy rows are copied as-is with scope_digest='' and NULL binding --
        # never rewritten, never served, never deleted. Immutable triggers are
        # dropped with the old table and recreated by initialize() afterwards.
        c.execute('CREATE TABLE idempotency_v2(external_work_ref TEXT NOT NULL, operation TEXT NOT NULL, '
                  "key TEXT NOT NULL, scope_digest TEXT NOT NULL DEFAULT '', digest TEXT NOT NULL, "
                  'result BLOB NOT NULL, candidate_id TEXT, review_id TEXT, branch TEXT, result_scope TEXT, '
                  'PRIMARY KEY(external_work_ref,operation,key,scope_digest))')
        c.execute("INSERT INTO idempotency_v2(external_work_ref,operation,key,scope_digest,digest,result) "
                  "SELECT external_work_ref,operation,key,'',digest,result FROM idempotency")
        c.execute('DROP TABLE idempotency')
        c.execute('ALTER TABLE idempotency_v2 RENAME TO idempotency')
        c.execute('PRAGMA user_version = %d' % IDEMPOTENCY_SCHEMA_VERSION)
    @contextmanager
    def transaction(self):
        c=self.connect()
        try:
            c.execute('BEGIN IMMEDIATE'); yield c; c.commit()
        except BaseException: c.rollback(); raise
        finally: c.close()
    @contextmanager
    def read(self):
        c=self.connect()
        try:
            c.execute('BEGIN'); yield c
        finally:
            c.rollback(); c.close()
