"""Single admission path; model input never supplies authority."""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .dom_contract import canonical,digest,raw_hash,decode,strict_loads,validate,normalized
from .fixtures import WORK,TASK,POLICY
from .ports import fixture_context
from .storage import Store,MigrationRequired,SCHEMA_VERSION as STORE_SCHEMA_VERSION
from .session import resolve_session
from . import delivery as delivery_facts
from . import public_dto
from functools import wraps
from dataclasses import replace as _replace
from . import claim_entry as claim_entry_mod

REVIEW_STRICTNESS={'auto':0,'manual':1,'independent':2}

def stored_review_mode(c):
    """Strictest review policy this project was ever run under, or None."""
    try: rows=[r[0] for r in c.execute('SELECT mode FROM review_policy_log')]
    except sqlite3.OperationalError as e:
        if 'no such table' in str(e): return None
        raise
    return max(rows,key=lambda m:REVIEW_STRICTNESS.get(m,0),default=None)

def record_review_mode(c,mode):
    if REVIEW_STRICTNESS[mode]>REVIEW_STRICTNESS.get(stored_review_mode(c),-1):
        c.execute('INSERT INTO review_policy_log(mode,set_at) VALUES(?,?)',(mode,datetime.now(timezone.utc).isoformat()))

# a candidate whose latest review is a rejection is closed, not pending
OPEN_REVIEW_SQL = ("COALESCE((SELECT json_extract(r.payload,'$.decision') FROM reviews r WHERE r.candidate=c.id "
                   "ORDER BY r.rowid DESC LIMIT 1),'approve') = 'approve'")

class IntegrityFailure(Exception):
    def __init__(self,result): self.result=result

def storage_error(error):
    code=getattr(error,'sqlite_errorcode',None)
    primary=code & 255 if code is not None else None
    if primary in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED): status='STORAGE_BUSY'
    elif primary in (sqlite3.SQLITE_CORRUPT,sqlite3.SQLITE_NOTADB): status='STORAGE_CORRUPT'
    elif primary==sqlite3.SQLITE_READONLY: status='STORAGE_READ_ONLY'
    elif primary==sqlite3.SQLITE_CONSTRAINT: status='STORAGE_CONSTRAINT'
    elif primary==sqlite3.SQLITE_ERROR and ('no such table' in str(error) or 'no such column' in str(error)): status='MIGRATION_REQUIRED'
    else: status='STORAGE_UNAVAILABLE'
    return {'status':status,'error':{'code':status,'sqlite_code':code,'sqlite_name':getattr(error,'sqlite_errorname',None)},
            'help':public_dto.error_help(status,'lite')['help']}

class Denied(Exception):
    """Typed refusal.  ``extra`` carries values the caller already owns (own branch
    names, a field name, a claim id) into the public DomainError; it is empty for
    every refusal that existed before WR-ADD.  ``help`` is advisory: ``guarded``
    fills it from HELP_DATA / the status table when a raise does not set it."""
    def __init__(self,status,**extra):
        super().__init__(status); self.extra=extra

_UNSET=object()

def _help(status,tool=None,text=None):
    """Actionable Russian ``help`` for a refusal (never empty, <=500 chars)."""
    return public_dto.error_help(status,'lite',tool=tool,help_text=text)['help']

def guarded(fn):
    @wraps(fn)
    def run(*a,**kw):
        try: out=fn(*a,**kw)
        except IntegrityFailure as e: return public_dto.broken(e.result)
        except Denied as e:
            extra=dict(e.extra)
            extra.setdefault('help',_help(str(e)))
            return dict({'status':str(e)},**extra)
        except MigrationRequired as e: return {'status':str(e),'help':_help(str(e))}
        except sqlite3.DatabaseError as e: return storage_error(e)
        # A refusal returned as a plain DomainError shape gets the same advisory
        # help as a raised one.  Richer results (Rejected/UpdateConflict/...) are
        # projected by their own closed record, which adds help itself, so they
        # are left untouched here.
        if (isinstance(out,dict) and 'help' not in out and set(out) <= public_dto.DOMAIN_ERROR_FIELDS):
            status=out.get('status')
            if isinstance(status,str) and status in public_dto.KNOWN_REFUSALS:
                out=dict(out,help=_help(status))
        return out
    return run

HASH_SCHEME=2

def claim_row_digest(row):
    """Digest of every served column of a ``claims`` row (body via ``raw_sha256``)."""
    payload=bytes(row['payload']) if not isinstance(row['payload'],str) else row['payload'].encode('utf8')
    return digest({'id':row['id'],'candidate':row['candidate'],'tx':row['tx'],'content_type':row['content_type'],
                   'raw_sha256':row['raw_sha256'],'encoding':row['encoding'],'media_type':row['media_type'],
                   'source_ref':row['source_ref'],'origin_branch':row['origin_branch'],'origin_version':row['origin_version'],
                   'payload_sha256':raw_hash(payload)})

def review_row_digest(row):
    """Digest of the review row an admission rests on (QA004 F-24): its decision payload
    and the stored review_level are bound into the event chain through the hashed
    provenance, so a direct UPDATE of ``reviews`` breaks the chain."""
    payload=bytes(row['payload']) if not isinstance(row['payload'],str) else row['payload'].encode('utf8')
    level=row['review_level'] if 'review_level' in row.keys() else None
    return digest({'id':row['id'],'candidate':row['candidate'],'payload_sha256':raw_hash(payload),'review_level':level})

def event_hash_v2(previous,provenance,tx,branch,event_id,kind,revision,claim_ref,claim_digest):
    """Audit 2026-11-12 #4: the v2 hash binds every column served from ``events``
    plus the digest of the claim row the event introduces."""
    return digest({'scheme':HASH_SCHEME,'previous':previous,'payload':provenance,'tx':tx,'branch':branch,'id':event_id,
                   'kind':kind,'revision':revision,'claim_ref':claim_ref,'claim':claim_digest})

def verify_chain(c):
    """Recompute the append-only event hash chain and cross-check served rows.

    v1 events (written before audit 2026-11-12 #4) hash ``{previous, payload,
    tx, branch}``; v2 events (``payload.hash_scheme == 2``) also bind ``id``,
    ``kind``, ``revision``, ``claim_ref`` and the claim row digest.  A v1 event
    after a v2 one is a downgrade and breaks the chain.

    For *every* event, v1 included, the rows the projection serves are checked
    against the hashed payload: ``kind`` matches the hashed event type, the
    revision/branch match the transaction, a claim row equals the hashed item
    (payload bytes, body bytes, ``raw_sha256``, origin) and its snapshot equals
    the hashed dependencies/review id.  So editing ``events.kind`` or the
    ``claims`` bytes directly in SQLite is detected.  Recomputing the whole
    chain from scratch is not (no external anchor yet: P5 checkpoint).
    """
    rows=c.execute('SELECT seq,id,tx,claim_ref,branch,revision,kind,payload,previous_hash,event_hash FROM events ORDER BY seq').fetchall()
    claims={r['id']:r for r in c.execute('SELECT * FROM claims')}
    snaps={r['claim_id']:r for r in c.execute('SELECT * FROM claim_snapshots')}
    txs={r['id']:r for r in c.execute('SELECT id,branch,revision FROM transactions')}
    reviews={r['id']:r for r in c.execute('SELECT * FROM reviews')}
    previous='0'*64; seen_v2=False
    def broken(row,reason):
        return {'status':'CHAIN_BROKEN','events':len(rows),'broken_at_seq':row['seq'],'broken_event_id':row['id'],'reason':reason}
    from .dom_contract import EVENTS,EVENT_FIELDS
    for row in rows:
        try:
            payload=strict_loads(row['payload'])
        except (ValueError,TypeError,UnicodeError): return broken(row,'EVENT_SHAPE_INVALID')
        if row['previous_hash']!=previous: return broken(row,'PREVIOUS_HASH_MISMATCH')
        scheme=payload.get('hash_scheme') if type(payload) is dict else None
        claim=claims.get(row['claim_ref']) if row['claim_ref'] is not None else None
        try:
            if scheme==HASH_SCHEME:
                seen_v2=True
                expected=event_hash_v2(previous,payload,row['tx'],row['branch'],row['id'],row['kind'],row['revision'],row['claim_ref'],
                                       claim_row_digest(claim) if row['kind']=='claim' and claim is not None else None)
            elif scheme is None:
                expected=digest({'previous':previous,'payload':payload,'tx':row['tx'],'branch':row['branch']})
            else: return broken(row,'EVENT_SHAPE_INVALID')
        except (ValueError,TypeError,UnicodeError): return broken(row,'EVENT_SHAPE_INVALID')
        if row['event_hash']!=expected: return broken(row,'EVENT_HASH_MISMATCH')
        # A correctly v1-hashed event after a v2 one is a forged downgrade.
        if scheme is None and seen_v2: return broken(row,'HASH_SCHEME_DOWNGRADE')
        required={'value':dict,'actor':str,'root':str,'server_time':str,'origin_version':str,'context_revision':int,'reason':str,'review_id':str,'dependencies':dict}
        if type(payload) is not dict or any(type(payload.get(k)) is not t for k,t in required.items()): return broken(row,'EVENT_SHAPE_INVALID')
        if 'review_digest' in payload:
            rv=reviews.get(payload['review_id'])
            if type(payload['review_digest']) is not str or rv is None or review_row_digest(rv)!=payload['review_digest']: return broken(row,'REVIEW_ROW_MISMATCH')
        tx=txs.get(row['tx'])
        if tx is None or tx['branch']!=row['branch'] or tx['revision']!=row['revision'] or payload['context_revision']!=row['revision']: return broken(row,'EVENT_ROW_MISMATCH')
        value=payload['value']; kind=row['kind']
        if kind=='claim':
            if type(value.get('item')) is not dict or type(value.get('origin_version')) is not str: return broken(row,'EVENT_SHAPE_INVALID')
            if claim is None: return broken(row,'CLAIM_ROW_MISMATCH')
            try:
                item_bytes=canonical(value['item'])
                raw=decode(value['item'].get('body_b64'))
            except (ValueError,TypeError): return broken(row,'EVENT_SHAPE_INVALID')
            stored=bytes(claim['payload']) if not isinstance(claim['payload'],str) else claim['payload'].encode('utf8')
            if (stored!=item_bytes or bytes(claim['raw'])!=raw or claim['raw_sha256']!=raw_hash(raw) or claim['tx']!=row['tx']
                    or claim['origin_branch']!=row['branch'] or claim['origin_version']!=value['origin_version']
                    or claim['content_type']!=value['item'].get('content_type')):
                return broken(row,'CLAIM_ROW_MISMATCH')
            snap=snaps.get(row['claim_ref'])
            if (snap is None or snap['tx']!=row['tx'] or snap['branch']!=row['branch'] or snap['revision']!=row['revision']
                    or snap['review_id']!=payload['review_id'] or snap['dependencies']!=canonical(payload['dependencies']).decode('utf8')):
                return broken(row,'CLAIM_SNAPSHOT_MISMATCH')
        else:
            if kind not in EVENTS or any(type(value.get(k)) is not t for k,t in EVENT_FIELDS.items()): return broken(row,'EVENT_SHAPE_INVALID')
            if value['event_type']!=kind or (value['claim_ref'] or None)!=row['claim_ref']: return broken(row,'EVENT_ROW_MISMATCH')
            fields={'property':('name','value'),'applicability':('branch',),'conflict':('conflict_ref',),'resolve':('conflict_ref',),'exclude':('branch',)}
            if any(type(value['value'].get(k)) is not str for k in fields.get(kind,())): return broken(row,'EVENT_SHAPE_INVALID')
        previous=row['event_hash']
    return {'status':'CHAIN_OK','events':len(rows),'head_hash':previous}

def claim_snapshot_gaps(c):
    """Claim ids that have no immutable dependency snapshot (P0 invariant).

    The admission transaction inserts both rows together; this read-only check
    makes a future bypass visible without pretending SQLite can enforce a
    cross-row invariant (the snapshot values come from the envelope, not from
    ``claims.payload``).
    """
    return [r[0] for r in c.execute(
        'SELECT c.id FROM claims c LEFT JOIN claim_snapshots s ON s.claim_id=c.id '
        'WHERE s.claim_id IS NULL ORDER BY c.id')]

def revision_key(revision):
    """Natural order of source revisions: ``rev-9`` < ``rev-10``; ints compare as ints."""
    import re as _re
    if revision is None: return ()
    return tuple((0,int(p),'') if p.isdigit() else (1,0,p) for p in _re.findall(r'\d+|\D+',str(revision)))

def _current_claim(children,cid):
    seen=set()
    while cid in children and cid not in seen: seen.add(cid); cid=children[cid]
    return cid

def projection_stale(state,newest_task_revision,ttl_seconds):
    """D1.1: stale = no cursor, the cursor lags behind stored task data, or the last check is older than the TTL."""
    if state is None or state['source_revision'] is None: return True
    if newest_task_revision is not None and revision_key(state['source_revision'])<revision_key(newest_task_revision): return True
    try: checked=datetime.fromisoformat(state['last_checked_at'])
    except (TypeError,ValueError): return True
    return (datetime.now(timezone.utc)-checked).total_seconds()>ttl_seconds

def _resolve_locals(ids,p):
    """Intra-package anatomy references (statement capture §12.11): an item's
    ``anatomy.basis`` naming a sibling package-local id ('c1') resolves to that
    sibling's real claim id before the claims are recorded.  Applies only to
    names inside this package; anything else is left as the caller wrote it.
    The reviewed candidate keeps the local names (the package bytes are
    already digested); the recorded facts carry the resolved ones."""
    for i in p['items']:
        a=i.get('anatomy')
        if not isinstance(a,dict): continue
        basis=a.get('basis')
        if isinstance(basis,list):
            a['basis']=[ids.get(x,x) if isinstance(x,str) else x for x in basis]

class Service:
    plan_ttl_seconds=24*3600
    def __init__(self,directory,role='author',simulation=True,branches=('AA','AB'),profile='strict',review_mode='auto',principal=None,root=None):
        # One product ('prod', historically 'lite').  'lab' ('strict') is the
        # fixed-identity laboratory stand the older tests run on: the same
        # code with fixture identities, a fixed AA/AB grant and independent
        # review -- not a second product.
        profile={'prod':'lite','lab':'strict'}.get(profile,profile)
        if profile not in ('strict','lite'): raise ValueError('UNKNOWN_PROFILE')
        if review_mode not in ('auto','manual','independent'): raise ValueError('UNKNOWN_REVIEW_MODE')
        self._system=0
        # lite only: 'manual' = dom_claim records a candidate; the owner or the
        # local agent reads, reviews and admits it (audit 2026-11-12 Q5).
        self.review_mode=review_mode; self.launch_review_mode=review_mode; self._chain_cache=None; self.role=role
        self.directory=Path(directory).resolve()
        from .paths import runtime_root
        if not self.directory.is_relative_to(runtime_root()): raise ValueError('RUNTIME_PATH_REQUIRED')
        self.context=fixture_context(role,simulation,branches,profile,principal,root)
        self.profile=profile; self.lite=profile=='lite'
        if self.lite:
            # work/task/policy are computed by the server (never typed by the model)
            work,task,policy=claim_entry_mod.identity(self.directory)
            self.context=_replace(self.context,work=work,task=task,policy=policy,branches=frozenset())
        self.store=Store(self.directory)
        # L4: identity comes from the launch environment, never from a tool
        # argument.  L5: the real database path this writer saw is recorded
        # with every write/read receipt so health can compare channels.
        self.session_identity=resolve_session()
        self.session=self.session_identity.session
        self.session_source=self.session_identity.source
        self.origin=self.session_identity.origin
        self.db_realpath=os.path.realpath(str(self.store.path))
        self.fault_hook=lambda stage: None
        self.omh_mode='available'
    @property
    def independent(self):
        """Review must come from another identity root (policy 'independent'; always on the lab stand)."""
        return (not self.lite or self.review_mode=='independent') and not self._system
    @property
    def candidate_only(self):
        """Agent-entered records wait for review instead of being admitted in the same call."""
        return self.review_mode in ('manual','independent') and not self._system
    @property
    def effective_policy(self):
        """The review policy the read-before-write gate actually runs under.

        A project that was ever run under a stricter policy keeps it
        (``stored_review_mode``), exactly as ``_gate_check`` resolves it.
        ``auto`` means the gate is off: read receipts are not checked and every
        write (``dom_claim`` / ``dom_update`` / ``dom_annotate``) is admitted in
        place.  ``manual`` / ``independent`` enforce the gate
        (``GATE_NO_READ``).  Read-only: never records or promotes a policy.
        """
        mode=self.review_mode
        if not self.store.path.exists(): return mode
        try:
            with self.store.read() as c: stored=stored_review_mode(c)
        except (sqlite3.OperationalError,MigrationRequired):
            return mode
        if stored and REVIEW_STRICTNESS.get(stored,0)>REVIEW_STRICTNESS.get(mode,0): return stored
        return mode
    def system(self):
        """Server-authored records (the verbatim root request, the plan mirror, replacement links of an
        admitted update, variant convergence) are admitted in place and labelled review_level='self'."""
        from contextlib import contextmanager
        @contextmanager
        def scope():
            self._system+=1
            try: yield
            finally: self._system-=1
        return scope()
    task=property(lambda self: self.context.task)
    policy=property(lambda self: self.context.policy)
    def known_branches(self):
        return tuple(sorted(self.context.branches)) if self.lite else ('AA','AB')
    def binding_state(self,c):
        """'missing' | 'own' | 'foreign' -- the persisted binding against this launch (D-02).
        Reveals nothing about a foreign binding beyond the word 'foreign'."""
        try: row=c.execute('SELECT work_ref FROM binding ORDER BY id LIMIT 1').fetchone()
        except sqlite3.OperationalError as e:
            # Only a database without the table is 'missing'; a busy/locked one is
            # STORAGE_BUSY, never NOT_INITIALIZED ("call dom_start") -- QA004 F-30.
            if 'no such table' in str(e): return 'missing'
            raise
        if row is None: return 'missing'
        if row[0]==self.context.work: return 'own'
        try:
            if c.execute('SELECT 1 FROM adoptions WHERE new_work_ref=? AND source_work_ref=?',(self.context.work,row[0])).fetchone(): return 'own'
        except sqlite3.OperationalError: pass
        return 'foreign'
    def lite_sync(self,branch=None):
        """Lite: branches are the ones declared in the own/adopted database, not a launch constant."""
        if not self.store.path.exists(): raise Denied('NOT_INITIALIZED')
        with self.store.read() as c:
            state=self.binding_state(c)
            if state=='missing': raise Denied('NOT_INITIALIZED')
            if state=='foreign': raise Denied('SCOPE_DENIED')
            branches=frozenset(r[0] for r in c.execute('SELECT branch FROM branch_heads'))
            stored=stored_review_mode(c)
        # QA004 F-18: a project started under a stricter review policy is never written under a
        # weaker one by a later launch (e.g. the plugin's default environment).
        if REVIEW_STRICTNESS.get(stored,0)>REVIEW_STRICTNESS[self.review_mode]: self.review_mode=stored
        self.context=_replace(self.context,branches=branches)
        if branch is not None and branch not in branches: raise Denied('UNKNOWN_BRANCH',branches=sorted(branches))
    def scope(self,branch=None,work=_UNSET):
        if self.lite: self.lite_sync(branch)
        if work is _UNSET: work=self.context.work
        if work!=self.context.work or self.context.principal=='unknown' or branch is not None and branch not in self.context.branches: raise Denied('SCOPE_DENIED')
    def capability(self,cap):
        if not self.context.simulation: raise Denied('INDEPENDENCE_NOT_ENFORCED')
        # server-authored records (system scope) do not depend on the caller's role
        if cap not in self.context.capabilities and not self._system: raise Denied('INDEPENDENCE_NOT_ENFORCED')
    def ready(self):
        if not self.store.path.exists(): raise Denied('NOT_INITIALIZED')
        with self.store.read() as c:
            if not delivery_facts.installed(c): raise MigrationRequired('MIGRATION_REQUIRED')
            # D-02 / AN009 H3: the persisted binding must be this launch's (or an adopted one)
            # BEFORE any scoped data is read.  A binding-less file stays readable as before.
            if self.binding_state(c)=='foreign': raise Denied('SCOPE_DENIED')
            behind=self.lite and self.binding_state(c)=='own' and c.execute('PRAGMA user_version').fetchone()[0]<STORE_SCHEMA_VERSION
        if behind: self.store.migrate()  # v4: additive, recorded in schema_migrations
        if self.lite:
            with self.store.read() as c:  # F-18: the project's stored policy before any write decision is taken
                stored=stored_review_mode(c)
            if REVIEW_STRICTNESS.get(stored,0)>REVIEW_STRICTNESS[self.review_mode]: self.review_mode=stored
    def integrity(self,c):
        """Verify once, return ``(stamp, chain)`` so read paths can reuse the result.

        Inside ``sync`` (QA004 F-31) the result is reused while the event log is the
        same (count, last seq, last hash): delivery rows are not events, so one sync
        verified the whole chain three times per outbox intent -- O(n^2)."""
        cache=self._chain_cache
        if cache is not None:
            key=tuple(c.execute('SELECT COUNT(*),COALESCE(MAX(seq),0),(SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1) FROM events').fetchone())
            if cache.get('key')!=key: cache.update(key=key,chain=verify_chain(c))
            chain=cache['chain']
        else: chain=verify_chain(c)
        stamp={'read_at':datetime.now(timezone.utc).isoformat(),'chain_head':chain.get('head_hash'),
               'revisions':dict(c.execute('SELECT branch,revision FROM branch_heads'))}
        if chain['status']!='CHAIN_OK':
            # Only identity locators from intact claims, not a projection of bad events.
            origins={r['id']:{'origin_branch':r['origin_branch'],'origin_version':r['origin_version']}
                     for r in c.execute('SELECT id,origin_branch,origin_version FROM claims') if r['origin_branch'] in self.context.branches}
            raise IntegrityFailure(dict(status='CHAIN_BROKEN',chain=chain,read_snapshot=stamp,
                                        readiness='NOT_READY',ready_for_owner_review=False,
                                        blockers=['EVENT_CHAIN_BROKEN'],origins=origins))
        return stamp,chain
    def integrity_gate(self,c):
        return self.integrity(c)[0]
    @guarded
    def init(self,request):
        if not self.context.simulation: raise Denied('OMH_BINDING_UNVERIFIED')
        if self.lite: raise Denied('PROFILE_LITE')
        if type(request) is not dict or set(request)!={'external_work_ref','policy_digest'}: raise Denied('REJECTED')
        self.scope(work=request['external_work_ref'])
        if request['policy_digest']!=POLICY: raise Denied('POLICY_MISMATCH')
        self.store.initialize(WORK,POLICY)
        return {'status':'INITIALIZED','simulated':True,'work_ref':WORK}
    def head(self,c,branch):
        # WS-F04: granted-but-absent branch is a typed absence, not an internal
        # failure. The single physical check lives here (query/resume/trace/export
        # all reach _snapshot -> head); sync/sync_status call require_branch
        # explicitly because they do not go through head().
        row=c.execute('SELECT revision FROM branch_heads WHERE branch=?',(branch,)).fetchone()
        if not row: raise Denied('NO_SUCH_BRANCH')
        return row[0]
    def require_branch(self,c,branch):
        if c.execute('SELECT 1 FROM branch_heads WHERE branch=?',(branch,)).fetchone() is None:
            raise Denied('NO_SUCH_BRANCH')
    def dedupe(self,c,operation,key,d,binding):
        # WS-F02 L2: replay identity = (key, candidate_id, branch, review_id,
        # digest, result_scope). A cached result is served ONLY when the full
        # binding matches and result_scope remains within the current grant.
        rows=c.execute('SELECT * FROM idempotency WHERE external_work_ref=? AND operation=? AND key=?',
                       (self.context.work,operation,key)).fetchall()
        if not rows: return None
        if any(r['candidate_id'] is None for r in rows):
            # Legacy (pre-migration) row on this key: inert. Never served,
            # never rewritten, never authorizes this key for a new binding.
            raise Denied('IDEMPOTENCY_BINDING_UNKNOWN')
        if binding.get('candidate_id') is None:
            pick=rows[0]  # submit-style replay: key alone selects its operation
        else:
            pick=next((r for r in rows if r['candidate_id']==binding['candidate_id'] and r['branch']==binding['branch']),None)
        if pick is None: raise Denied('IDEMPOTENCY_BINDING_MISMATCH')
        if pick['review_id']!=binding.get('review_id'): raise Denied('IDEMPOTENCY_REVIEW_MISMATCH')
        if pick['digest']!=d: raise Denied('IDEMPOTENCY_MISMATCH')
        stored_scope=set(json.loads(pick['result_scope']))
        if not stored_scope<=set(self.context.branches): raise Denied('SCOPE_DENIED')
        return json.loads(pick['result'])
    def remember(self,c,operation,key,d,result,binding):
        c.execute('INSERT INTO idempotency(external_work_ref,operation,key,scope_digest,digest,result,candidate_id,review_id,branch,result_scope) VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (self.context.work,operation,key,binding['scope_digest'],d,canonical(result),
                   binding['candidate_id'],binding.get('review_id'),binding['branch'],
                   json.dumps(sorted(binding['result_scope']))))
    def scope_digest(self): return digest(sorted(self.context.branches))
    @guarded
    def submit(self,payload_json,session=None):
        self.scope(); self.capability('submit'); self.ready()
        try: p=strict_loads(payload_json)
        except (ValueError,TypeError): return {'status':'REJECTED','accepted_count':0,'errors':[{'local_id':None,'path':'$','code':'INVALID_JSON','required':True,'fix':'Use restricted JSON.'}],'rejected_local_ids':[]}
        if type(p) is not dict: raise Denied('REJECTED',help=_help('REJECTED','dom_submit'))
        if type(p.get('branch')) is not str or type(p.get('external_work_ref')) is not str:
            errors=validate(p,self.known_branches()) or [{'local_id':None,'path':'$.branch','code':'INVALID_TYPE','required':True,'fix':'Supply schema-conforming field; no coercion.'}]
            return {'status':'REJECTED','accepted_count':0,'errors':errors,'rejected_local_ids':[]}
        self.scope(p.get('branch'),p.get('external_work_ref'))
        for branch in p.get('dependencies',{}) if type(p.get('dependencies')) is dict else []: self.scope(branch)
        errors=validate(p,self.known_branches())
        if errors: return {'status':'REJECTED','accepted_count':0,'errors':errors,'rejected_local_ids':[i.get('local_id') for i in p.get('items',[]) if isinstance(i,dict)]}
        if p['external_task_ref']!=self.task: raise Denied('SCOPE_DENIED',help=_help('SCOPE_DENIED','dom_submit'))
        if p['policy_digest']!=self.policy: raise Denied('POLICY_MISMATCH',help=_help('POLICY_MISMATCH','dom_submit'))
        for i in p['items']:
            for branch in i.get('applicability',[]): self.scope(branch)
            if i.get('event_type')=='exclude' and 'owner' not in self.context.capabilities: raise Denied('OWNER_AUTHORITY_REQUIRED',help=_help('OWNER_AUTHORITY_REQUIRED','dom_submit'))
            if i.get('event_type')=='applicability' and i['value']['branch']!=p['branch']: raise Denied('SCOPE_DENIED',help=_help('SCOPE_DENIED','dom_submit'))
        with self.store.read() as c: self.check_references(c,p)
        q=normalized(p,self.context.principal,self.context.root); d=digest(q)
        # L8: the candidate names the (trusted) session that submitted it; a
        # call without one stores NULL, never an invented value.
        candidate_session=session if type(session) is str and session.strip() else None
        with self.store.transaction() as c:
            binding={'candidate_id':None,'review_id':None,'branch':p['branch'],
                     'scope_digest':self.scope_digest(),'result_scope':{p['branch']}|set(p['dependencies'])}
            old=self.dedupe(c,'submit:'+self.context.principal,p['idempotency_key'],d,binding)
            if old: return old
            candidate=str(uuid.uuid4())
            if 'candidate_session' in {r[1] for r in c.execute('PRAGMA table_info(candidates)')}:
                c.execute('INSERT INTO candidates(id,digest,payload,author,root,branch,created_at,candidate_session) VALUES(?,?,?,?,?,?,?,?)',
                          (candidate,d,canonical(q),self.context.principal,self.context.root,p['branch'],datetime.now(timezone.utc).isoformat(),candidate_session))
            else:  # a pre-L8 database read without migration: no column to fill
                c.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?)',(candidate,d,canonical(q),self.context.principal,self.context.root,p['branch'],datetime.now(timezone.utc).isoformat()))
            result={'status':'RECORDED_CANDIDATE','candidate_id':candidate,'intent_digest':d,'accepted_count':0,'simulated':True}
            self.remember(c,'submit:'+self.context.principal,p['idempotency_key'],d,result,dict(binding,candidate_id=candidate))
        return result
    def check_references(self,c,p):
        if p['operation']!='append': return
        from .projections import snapshot
        visible={cl['claim_id'] for b in self.context.branches for cl in snapshot(c,b,self.head(c,b))['claims']}
        scoped={cl['claim_id'] for cl in snapshot(c,p['branch'],self.head(c,p['branch']))['claims']}
        for i in p['items']:
            cid=i['claim_ref']; ev=i['event_type']
            if cid and cid not in (visible if ev=='applicability' else scoped): raise Denied('SCOPE_DENIED',help=_help('SCOPE_DENIED'))
            if ev=='replacement' and i['value']['replacement_ref'] not in scoped: raise Denied('SCOPE_DENIED',help=_help('SCOPE_DENIED'))
            if i['supersedes']:
                row=c.execute('SELECT branch,claim_ref FROM events WHERE id=?',(i['supersedes'],)).fetchone()
                if not row or row['branch']!=p['branch'] or row['claim_ref']!=cid: raise Denied('SCOPE_DENIED',help=_help('SCOPE_DENIED'))
    def candidate(self,c,ref):
        row=c.execute('SELECT * FROM candidates WHERE id=?',(ref,)).fetchone()
        # QA004 F-22: lite says "no such candidate" (as NO_SUCH_CLAIM), not "adopt the database"
        if not row: raise Denied('NO_SUCH_CANDIDATE' if self.lite else 'SCOPE_DENIED',help=_help('NO_SUCH_CANDIDATE' if self.lite else 'SCOPE_DENIED','dom_candidate'))
        self.scope(row['branch'])
        p=json.loads(row['payload'])
        for b in p['dependencies']: self.scope(b)
        return row,p
    @guarded
    def candidate_view(self,candidate_id):
        """Audit 2026-11-12 #5: a reviewer reads the exact candidate before deciding."""
        self.capability('review'); self.ready()
        if type(candidate_id) is not str or not candidate_id: raise Denied('INVALID_FIELD',field='candidate_id',
            help=_help('INVALID_FIELD','dom_candidate','Тебе НУЖНО: 1) Взять candidate_id из dom_submit/dom_extract/dom_resume. 2) Передать его непустой строкой. 3) Повторить вызов.'))
        with self.store.read() as c:
            row,p=self.candidate(c,candidate_id)
            last=c.execute('SELECT payload FROM reviews WHERE candidate=? ORDER BY rowid DESC LIMIT 1',(candidate_id,)).fetchone()
            admitted=c.execute('SELECT 1 FROM claims WHERE candidate=?',(candidate_id,)).fetchone() is not None or c.execute(
                "SELECT 1 FROM idempotency WHERE external_work_ref=? AND operation LIKE 'admit:%' AND candidate_id=?",(self.context.work,candidate_id)).fetchone() is not None
        decision=json.loads(last['payload']).get('decision') if last else None
        items=[]
        for i in p['items']:
            entry={k:i.get(k) for k in ('local_id','content_type','event_type','claim_ref','reason','requirement_refs','applicability','metadata','value','anatomy') if k in i}
            if 'body_b64' in i:
                body=decode(i['body_b64']).decode('utf-8','replace')
                entry['text']=body[:4000]; entry['text_truncated']=len(body)>4000
            items.append(entry)
        state='ADMITTED' if admitted else 'REJECTED' if decision=='reject' else 'SUPERSEDED' if decision=='superseded' else 'REVIEWED' if decision else 'AWAITING_REVIEW'
        return {'status':'CANDIDATE','candidate_id':candidate_id,'intent_digest':row['digest'],'branch':row['branch'],
                'operation':p['operation'],'state':state,'author':row['author'],'expected_branch_revision':p['expected_branch_revision'],
                'reason':p.get('reason'),'items':items,'simulated':True}
    @guarded
    def review(self,candidate_id,intent_digest,decision='approve',note=None,_internal=False,session=_UNSET):
        self.capability('review'); self.ready()
        # 'superseded' is written only by dom_accept when it rebases a stale candidate.
        if decision not in ('approve','reject') and not (_internal and decision=='superseded'): raise Denied('INVALID_FIELD',field='decision',
            help=_help('INVALID_FIELD','dom_review','Тебе НУЖНО: 1) Передать decision=approve или decision=reject. 2) Для reject добавить непустой note. 3) Повторить вызов.'))
        if note is not None and (type(note) is not str or len(note)>4000): raise Denied('INVALID_FIELD',field='note',
            help=_help('INVALID_FIELD','dom_review','Тебе НУЖНО: 1) Передать note строкой до 4000 символов. 2) Убрать лишнее из текста. 3) Повторить вызов.'))
        if decision=='reject' and not (note or '').strip(): raise Denied('INVALID_FIELD',field='note',hint='A rejection needs a note saying why (it is recorded with the review).',
            help=_help('INVALID_FIELD','dom_review','Тебе НУЖНО: 1) Написать в note причину отклонения. 2) Передать decision=reject вместе с note. 3) Повторить вызов.'))
        with self.store.read() as c:
            row,p=self.candidate(c,candidate_id)
            # Lite (local_single_owner): author and reviewer are the same local owner; the
            # record is then honestly labelled 'self'.  Strict still refuses same-root review.
            if row['root']==self.context.root and self.independent: raise Denied('INDEPENDENCE_NOT_ENFORCED',help=_help('INDEPENDENCE_NOT_ENFORCED','dom_review'))
            if row['digest']!=intent_digest: raise Denied('DIGEST_MISMATCH',help=_help('DIGEST_MISMATCH','dom_review'))
            level='self' if row['root']==self.context.root else 'independent'
            review=dict(review_level=level,candidate_digest=intent_digest,snapshot_digest=digest(p['dependencies']),dependencies=p['dependencies'],expected_revision=p['expected_branch_revision'],policy=p['policy_digest'],reviewer=self.context.principal,root=self.context.root,decision=decision,note=note or '',simulated=True,proof_ref='launcher-fixture-only')
        rid=str(uuid.uuid4())
        # L8: the review names the session that decided it.  Default is this
        # process' trusted session (DOM_SESSION); an explicit None records a
        # missing reviewer session, which admit() then refuses.
        reviewer_session=self.session if session is _UNSET else (session if type(session) is str and session.strip() else None)
        with self.store.transaction() as c:
            rv_cols={r[1] for r in c.execute('PRAGMA table_info(reviews)')}
            if 'review_level' in rv_cols and 'reviewer_session' in rv_cols:
                c.execute('INSERT INTO reviews(id,candidate,payload,review_level,reviewer_session) VALUES(?,?,?,?,?)',(rid,candidate_id,canonical(review),level,reviewer_session))
            elif 'review_level' in rv_cols:  # pre-v3 database not yet migrated by dom_init/dom_start: label lives in the payload only
                c.execute('INSERT INTO reviews(id,candidate,payload,review_level) VALUES(?,?,?,?)',(rid,candidate_id,canonical(review),level))
            elif 'reviewer_session' in rv_cols:
                c.execute('INSERT INTO reviews(id,candidate,payload,reviewer_session) VALUES(?,?,?,?)',(rid,candidate_id,canonical(review),reviewer_session))
            else:
                c.execute('INSERT INTO reviews(id,candidate,payload) VALUES(?,?,?)',(rid,candidate_id,canonical(review)))
        if decision!='approve': return {'status':'REVIEW_REJECTED','review_id':rid,'decision':decision,'simulated':True}
        return {'status':'DEMO_REVIEWED','review_id':rid,'decision':'approve','simulated':True}
    def _gate_check(self,c,branch,text,basis):
        """L6 trusted-write gate (0.3.0: advisory).

        A write may stand on claim ids (``basis`` / the claim it updates): the
        gate allows it only when this same trusted session already holds a
        ``read_receipts`` row for every such claim id.  ``review_policy='auto'``
        has no gate; a project that was ever run under a stricter policy keeps
        it (``stored_review_mode``), as everywhere else.

        The refusal is typed (``GATE_NO_READ``) and the transaction the check
        runs in is rolled back, so nothing is written.  ``claim_sessions.
        gate_status`` is reserved for enforce mode and is not written here.
        """
        stored=stored_review_mode(c)
        mode=self.review_mode
        if REVIEW_STRICTNESS.get(stored,0)>REVIEW_STRICTNESS.get(mode,0): mode=stored
        if mode=='auto': return
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='read_receipts'").fetchone() is None: return
        ids=[b for b in basis if type(b) is str and b.strip()] if isinstance(basis,(list,tuple)) else []
        if not ids: return
        missing=[claim_id for claim_id in ids
                 if c.execute('SELECT 1 FROM read_receipts WHERE claim_id=? AND session_id=? LIMIT 1',
                              (claim_id,self.session)).fetchone() is None]
        if missing:
            raise Denied('GATE_NO_READ',hint=(
                'This write stands on claim(s) this session has not read: %s. Read them first (dom_find / dom_context). '
                'The trusted-write gate is advisory in 0.3.0 and writes nothing without a read receipt.' % ', '.join(missing[:3])),
                help=('Тебе НУЖНО: 1) Вызвать dom_find или dom_context для каждого claim в basis. '
                      '2) Получить read receipt. 3) Повторить вызов.'))
    def _gate_write(self,basis):
        """Run the L6 gate in its own transaction before the delegated write."""
        if not self.store.path.exists(): return
        if self.review_mode=='auto':
            with self.store.read() as c:
                if REVIEW_STRICTNESS.get(stored_review_mode(c),0)<=REVIEW_STRICTNESS['auto']: return
        with self.store.transaction() as c:
            self._gate_check(c,None,'',basis)
    def _record_session(self,c,branch,claim_id,reason):
        """Upsert this session's single row inside the CALLER'S transaction (L3).

        ``claim_sessions`` is mutable operational state: one row per session,
        advanced on every write.  Being in the caller's transaction means a
        rolled-back admission leaves neither the claim nor the session row.
        ``created_at`` is set on first insert and never rewritten.
        """
        now=datetime.now(timezone.utc).isoformat()
        c.execute('INSERT INTO claim_sessions(session_id, principal, branch, created_at, last_write_at, '
                  'last_claim_id, last_write_reason, updated_at, session_source, origin, db_realpath) '
                  'VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET '
                  'principal=excluded.principal, branch=excluded.branch, last_write_at=excluded.last_write_at, '
                  'last_claim_id=excluded.last_claim_id, last_write_reason=excluded.last_write_reason, '
                  'updated_at=excluded.updated_at, session_source=excluded.session_source, '
                  'origin=excluded.origin, db_realpath=excluded.db_realpath',
                  (self.session,self.context.principal,branch,now,now,claim_id,reason,now,
                   self.session_source,self.origin,self.db_realpath))
    def _record_reads(self,c,pairs,stamp):
        """Append one immutable receipt per (claim_id, branch) in the caller's
        transaction (L3).  ``admitted_at`` stays NULL: a read never invents the
        admission time.  ``stamp`` is the integrity stamp of the same read."""
        for claim_id,branch in pairs:
            c.execute('INSERT INTO read_receipts(receipt_id, claim_id, session_id, branch, read_at, '
                      'admitted_at, chain_head, session_source, origin, db_realpath) '
                      'VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (str(uuid.uuid4()),claim_id,self.session,branch,stamp['read_at'],None,
                       stamp.get('chain_head'),self.session_source,self.origin,self.db_realpath))
    def _record_claim(self,c,claim_id,candidate_id,tx,p,item,revision,review_id):
        """Insert the claim and its immutable snapshot as one pair (TD-DOM-36).

        The snapshot values come from the admitted envelope (dependencies and
        review id), not from ``claims.payload``; a SQLite trigger cannot derive
        them.  Keeping the two inserts in one helper makes the invariant true by
        construction; ``claim_snapshot_gaps`` is the read-only guard.
        """
        raw=decode(item['body_b64'])
        metadata=item.get('metadata') if type(item.get('metadata')) is dict else {}
        source_ref=metadata.get('source_ref') or metadata.get('artifact_ref') or metadata.get('repository_ref') or ''
        c.execute('INSERT INTO claims VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(claim_id,candidate_id,tx,item['content_type'],raw,raw_hash(raw),item['encoding'],item['media_type'],source_ref,p['branch'],p['origin_version'],canonical(item)))
        c.execute('INSERT INTO claim_snapshots VALUES(?,?,?,?,?,?)',(claim_id,tx,p['branch'],revision,canonical(p['dependencies']).decode('utf8'),review_id))
    @guarded
    def admit(self,candidate_id,review_id,idempotency_key,intent_digest):
        self.capability('admit'); self.ready()
        with self.store.transaction() as c:
            # QA004 F-23: never extend a chain that no longer verifies ("stop writing").
            # Inside the write transaction, so the check and the append see the same state.
            self.integrity_gate(c)
            row,p=self.candidate(c,candidate_id)
            # WS-F02 L1: candidate digest vs claimed intent BEFORE any cache lookup.
            if row['digest']!=intent_digest or digest(p)!=intent_digest: raise Denied('DIGEST_MISMATCH',help=_help('DIGEST_MISMATCH','dom_admit'))
            binding={'candidate_id':candidate_id,'review_id':review_id,'branch':p['branch'],
                     'scope_digest':self.scope_digest(),'result_scope':{p['branch']}|set(p['dependencies'])}
            old=self.dedupe(c,'admit:'+self.context.principal,idempotency_key,intent_digest,binding)
            if old: return old
            rr=c.execute('SELECT * FROM reviews WHERE id=? AND candidate=?',(review_id,candidate_id)).fetchone()
            if not rr: raise Denied('STALE_REVIEW',help=_help('STALE_REVIEW','dom_admit'))
            r=json.loads(rr['payload'])
            if r.get('decision','approve')!='approve': raise Denied('REVIEW_REJECTED',help=_help('REVIEW_REJECTED','dom_admit'))
            if (r['root']==row['root'] and self.independent) or r['reviewer']!=self.context.principal: raise Denied('INDEPENDENCE_NOT_ENFORCED',help=_help('INDEPENDENCE_NOT_ENFORCED','dom_admit'))
            if r['candidate_digest']!=intent_digest or r['policy']!=self.policy or p['policy_digest']!=self.policy or r['snapshot_digest']!=digest(p['dependencies']): raise Denied('STALE_REVIEW',help=_help('STALE_REVIEW','dom_admit'))
            if r.get('review_level','self' if r['root']==row['root'] else 'independent')!=('self' if r['root']==row['root'] else 'independent'): raise Denied('STALE_REVIEW',help=_help('STALE_REVIEW','dom_admit'))
            # L8: an approval with no reviewer session is not admissible.
            reviewer_session=rr['reviewer_session'] if 'reviewer_session' in rr.keys() else None
            if reviewer_session is None:
                raise Denied('REVIEWER_SESSION_REQUIRED',hint='This review carries no reviewer session (reviewer_session is NULL); nothing was admitted. Review again from a trusted session.',
                    help=('Тебе НУЖНО: 1) Убедиться, что review вызван с корректной session. '
                          '2) Вызвать dom_review с явным session_id.'))
            if any(self.head(c,b)!=v for b,v in p['dependencies'].items()): raise Denied('STALE_REVIEW',help=_help('STALE_REVIEW','dom_admit'))
            if self.head(c,p['branch'])!=p['expected_branch_revision'] or r['expected_revision']!=p['expected_branch_revision']: raise Denied('REVISION_CONFLICT',help=_help('REVISION_CONFLICT','dom_admit'))
            self.check_references(c,p)
            tx=str(uuid.uuid4()); revision=p['expected_branch_revision']+1
            ids={i['local_id']:str(uuid.uuid4()) for i in p['items']}
            _resolve_locals(ids,p)
            receipt=dict(status='DEMO_ADMITTED',simulated=True,tx_id=tx,branch=p['branch'],revision=revision,intent_digest=intent_digest,claim_ids=ids if p['operation']=='claims' else {},event_ids=ids if p['operation']=='append' else {})
            c.execute('INSERT INTO transactions VALUES(?,?,?,?)',(tx,p['branch'],revision,canonical(receipt)))
            for i in p['items']:
                if p['operation']=='claims':
                    # N1: the claim's dependency snapshot and reviewing id are bound
                    # to the claim inside the same transaction (TD-DOM-36).
                    self._record_claim(c,ids[i['local_id']],candidate_id,tx,p,i,revision,review_id)
                    value={'item':i,'origin_version':p['origin_version']}; kind='claim'; claim=ids[i['local_id']]
                else:
                    value=i; kind=i['event_type']; claim=i['claim_ref'] or None
                provenance=dict(value=value,actor=row['author'],root=row['root'],server_time=datetime.now(timezone.utc).isoformat(),origin_version=p['origin_version'],context_revision=revision,reason=i['reason'],review_id=review_id,dependencies=p['dependencies'],hash_scheme=HASH_SCHEME,
                                review_digest=review_row_digest(rr))
                prev=c.execute('SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
                previous=prev[0] if prev else '0'*64
                event_id=str(uuid.uuid4())
                claim_digest=claim_row_digest(c.execute('SELECT * FROM claims WHERE id=?',(claim,)).fetchone()) if kind=='claim' else None
                c.execute('INSERT INTO events(id,tx,claim_ref,branch,revision,kind,payload,previous_hash,event_hash) VALUES(?,?,?,?,?,?,?,?,?)',(event_id,tx,claim,p['branch'],revision,kind,canonical(provenance),previous,
                          event_hash_v2(previous,provenance,tx,p['branch'],event_id,kind,revision,claim,claim_digest)))
            # L3: the session row commits or rolls back with the claim/events.
            # ``claim`` is the last admitted claim id for 'claims', else the last
            # event's claim_ref (None when an event names no claim).
            self._record_session(c,p['branch'],claim if p['items'] else None,p.get('reason'))
            self.fault_hook('after_facts')
            changed=c.execute('UPDATE branch_heads SET revision=? WHERE branch=? AND revision=?',(revision,p['branch'],p['expected_branch_revision'])).rowcount
            if changed!=1: raise Denied('REVISION_CONFLICT',help=_help('REVISION_CONFLICT','dom_admit'))
            self.remember(c,'admit:'+self.context.principal,idempotency_key,intent_digest,receipt,binding)
            for kind,target in [('OMH_EVIDENCE',self.task),('YAML_EXPORT','scoped-snapshot')]:
                payload={'tx_id':tx,'branch':p['branch'],'revision':revision,'intent_digest':intent_digest,'target_ref':target}
                c.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),tx,kind,target,p['branch'],revision,digest(payload),canonical(payload)))
            self.fault_hook('before_commit')
        self.fault_hook('after_commit')
        if p['operation']=='claims':
            self._link_planned(p,ids)
            self._replace_superseded(p,ids)
        with self.store.read() as c: return json.loads(c.execute('SELECT receipt FROM transactions WHERE id=?',(tx,)).fetchone()[0])
    def _replace_superseded(self,p,ids):
        """A reviewed update: the admitted item names the version it replaces
        (anatomy.supersedes); the replacement link follows as a server record."""
        for i in p['items']:
            old=(i.get('anatomy') or {}).get('supersedes')
            if not old: continue
            with self.store.read() as c:
                row=c.execute('SELECT origin_branch FROM claims WHERE id=?',(old,)).fetchone()
                stale=row is None or row[0]!=p['branch'] or old in claim_entry_mod._children(c) or old in claim_entry_mod._archived(c)
            if stale: continue
            event={'local_id':'r1','event_type':'replacement','claim_ref':old,'reason':i['reason'],
                   'value':{'replacement_ref':ids[i['local_id']]},'supersedes':'','ref':'dom_update'}
            with self.system():
                claim_entry_mod._commit(self,p['branch'],lambda rev: claim_entry_mod._package(
                    self,p['branch'],rev,[event],'replace-%s-r%d'%(ids[i['local_id']],rev),'append',p['origin_version'],i['reason']))
    def _link_planned(self,p,ids):
        """An admitted claim whose anatomy names a plan item is linked to it (a recoverable index)."""
        from . import planning
        for i in p['items']:
            ref=(i.get('anatomy') or {}).get('plan_ref')
            if not ref or i['local_id']=='u1': continue  # a new version is reached through the replacement chain
            try: planning.link(self,ref,ids[i['local_id']],'defines' if i['local_id']=='h1' else 'documents')
            except Denied: pass
    @guarded
    def query(self,branch,revision=None):
        self.scope(branch); self.ready()
        with self.store.read() as c:
            stamp=self.integrity_gate(c)
            snap=self._snapshot(c,branch,revision)
            return public_dto.project(
                dict(snap, read_snapshot=public_dto.read_snapshot(stamp, [branch])),
                'QueryResponse', scope=self.context.branches)
    def _snapshot(self,c,branch,revision=None):
        head=self.head(c,branch)
        revision=head if revision is None else revision
        if type(revision) is not int or revision<0 or revision>head:
            # QA004 F-28: lite names the field and the valid range (as dom_recall does)
            if self.lite: raise Denied('REVISION_OUT_OF_RANGE',field='revision',hint='revision must be a whole number 0..%d (the branch head).' % head)
            raise Denied('REJECTED')
        from .projections import snapshot
        return snapshot(c,branch,revision,self.lite)

    @guarded
    def claim_versions(self,claim_id):
        """P1.5: the replacement chain of one logical claim, oldest first."""
        if type(claim_id) is not str or not claim_id:
            raise Denied('INVALID_FIELD',field='claim_id')
        self.scope(); self.ready()
        with self.store.read() as c:
            row=c.execute('SELECT origin_branch FROM claims WHERE id=?',(claim_id,)).fetchone()
            # Audit 2026-11-12 #6 / SPEC invariant 6: absent and out-of-scope
            # are the same answer, checked before anything else is read.
            if row is None or row['origin_branch'] not in self.context.branches: raise Denied('NO_SUCH_CLAIM',field='claim_id')
            stamp=self.integrity_gate(c)
            children=claim_entry_mod._children(c)
            archived=claim_entry_mod._archived(c)
            info=claim_entry_mod._lineage(children)
            parent={new:old for old,new in children.items()}
            root,_=info(claim_id)
            versions=[]; cid=root; seen=set()
            while cid is not None and cid not in seen:
                seen.add(cid)
                found=c.execute('SELECT cl.raw_sha256,t.revision FROM claims cl '
                                'JOIN transactions t ON t.id=cl.tx WHERE cl.id=?',(cid,)).fetchone()
                if found is None: break
                _,version=info(cid)
                versions.append({'claim_id':cid,'version':version,'supersedes':parent.get(cid),
                                 'raw_sha256':found['raw_sha256'],'revision':found['revision'],
                                 'is_current':cid not in archived})
                cid=children.get(cid)
            body={'status':'CLAIM_VERSIONS','logical_claim_id':root,'versions':versions,'resolution':claim_entry_mod.resolve_claim(c,claim_id),
                  'read_snapshot':public_dto.read_snapshot(stamp,[row['origin_branch']])}
            return public_dto.project(body,'ClaimVersions',scope=self.context.branches)

    @guarded
    def diff(self,branch_a,branch_b):
        """P1.5: compare current claims of two branches by logical claim id."""
        self.scope(branch_a); self.scope(branch_b); self.ready()
        with self.store.read() as c:
            self.require_branch(c,branch_a); self.require_branch(c,branch_b)
            stamp=self.integrity_gate(c)
            a=self._snapshot(c,branch_a); b=self._snapshot(c,branch_b)

        def current(snap):
            out={}
            for claim in snap['claims']:
                if not claim['archived']:
                    out[claim['logical_claim_id']]=claim
            return out

        def deepest(snap):
            out={}
            for claim in snap['claims']:
                lid=claim['logical_claim_id']
                out[lid]=max(out.get(lid,0),claim['version'])
            return out

        ca,cb=current(a),current(b)
        shared=set(deepest(a))&set(deepest(b))
        only_a=sorted(set(ca)-set(cb)); only_b=sorted(set(cb)-set(ca))
        changed=[]; identical=[]
        for lid in sorted(set(ca)&set(cb)):
            left,right=ca[lid],cb[lid]
            if left['raw_sha256']==right['raw_sha256']:
                identical.append(lid)
            else:
                changed.append({'logical_id':lid,
                                'a':{'claim_id':left['claim_id'],'version':left['version'],'sha':left['raw_sha256']},
                                'b':{'claim_id':right['claim_id'],'version':right['version'],'sha':right['raw_sha256']}})
        da,db=deepest(a),deepest(b)
        common=max(shared,key=lambda lid:(max(da[lid],db[lid]),lid)) if shared else None
        body={'status':'DIFF','comparable':bool(shared),'common_ancestor':common,
              'only_a':only_a,'only_b':only_b,'changed':changed,'identical':identical,
              'read_snapshot':public_dto.read_snapshot(stamp,[branch_a,branch_b])}
        return public_dto.project(body,'Diff',scope=self.context.branches)

    @guarded
    def recall(self,branch,at_seq):
        """P1.4: branch-local replay up to an event seq (archive-aware)."""
        self.scope(branch); self.ready()
        if type(at_seq) is not int or at_seq < 0:
            raise Denied('INVALID_FIELD',field='at_seq')
        with self.store.read() as c:
            self.require_branch(c,branch)
            stamp=self.integrity_gate(c)
            from .projections import snapshot_at_seq
            snap=snapshot_at_seq(c,branch,at_seq,self.lite)
            if snap is None:
                raise Denied('REVISION_OUT_OF_RANGE',field='at_seq')
            active=[cl for cl in snap['claims'] if not cl['archived']]
            archived=[cl['claim_id'] for cl in snap['claims'] if cl['archived']]
            conflicts=set(); resolved=set()
            for event in snap['history']:
                if event['kind']=='conflict':
                    ref=event['provenance']['value']['value']['conflict_ref']
                    conflicts.add(ref); resolved.discard(ref)
                elif event['kind']=='resolve':
                    ref=event['provenance']['value']['value']['conflict_ref']
                    conflicts.discard(ref); resolved.add(ref)
            related={b for cl in active for b in cl['applicable_branches'] if b != branch}
            related |= {b for cl in active for b in cl['dependencies'] if b != branch}
            active_ids={cl['claim_id'] for cl in active}
            if active_ids:
                for ref,target in c.execute(
                        "SELECT claim_ref,branch FROM events WHERE kind='applicability' AND seq<=?",
                        (snap['event_seq'],)):
                    if ref in active_ids and target != branch:
                        related.add(target)
            related=sorted(related)
            body={'status':'RECALL','branch':branch,'at_seq':at_seq,'revision':snap['revision'],
                  'active_claims':active,'archived_claims':archived,
                  # Candidate/review rows have no event seq, so historical pending
                  # is not reconstructible from the append-only log.  Documented
                  # as null until candidates become events.
                  'pending_decisions':None,
                  'resolved_conflicts':sorted(resolved),'related_branches':related,
                  'as_of':datetime.now(timezone.utc).isoformat(),
                  'baseline_range':{'branch':branch,'from_seq':1 if at_seq else 0,'to_seq':at_seq},
                  'read_snapshot':public_dto.read_snapshot(stamp,[branch])}
            return public_dto.project(body,'Recall',scope=self.context.branches)

    @guarded
    def append(self,payload_json):
        try: p=strict_loads(payload_json)
        except (ValueError,TypeError): raise Denied('REJECTED')
        if type(p) is not dict or p.get('operation')!='append': raise Denied('REJECTED')
        return self.submit(payload_json,session=self.session)

    @guarded
    def sync_status(self,branch):
        self.scope(branch); self.ready()
        with self.store.read() as c:
            self.require_branch(c,branch)
            stamp=self.integrity_gate(c)
            body=dict(delivery_facts.status(c,branch),
                      read_snapshot=public_dto.read_snapshot(stamp,[branch]))
            return public_dto.project(body,'SyncStatusResponse',scope=self.context.branches)

    @guarded
    def project_plan_snapshot(self,source='OMH',source_revision=None,snapshot_digest=None,tasks=None,mode='full'):
        """P2.1: idempotent OMH plan projection (D1.1).

        The DOM never owns task status: this writer copies it from the OMH
        snapshot. Content identity is the stable ``plan_task_id``; version history
        is append-only and ``task_claims`` is deliberately untouched here.
        """
        self.scope(); self.ready()
        from .storage import PLAN_SOURCES
        if source not in PLAN_SOURCES: raise Denied('SCOPE_DENIED',field='source')
        if type(source_revision) is not str or not source_revision: raise Denied('INVALID_FIELD',field='source_revision')
        if type(tasks) is not list: raise Denied('INVALID_FIELD',field='tasks')
        if mode not in ('full','partial'): raise Denied('INVALID_FIELD',field='mode')
        now=datetime.now(timezone.utc).isoformat()
        with self.store.transaction() as c:
            state=c.execute('SELECT source_revision,snapshot_digest,last_changed_at FROM plan_projection_state WHERE source=?',(source,)).fetchone()
            if state is not None and revision_key(source_revision)<revision_key(state['source_revision']): raise Denied('STALE_PROJECTION_SNAPSHOT',field='source_revision')
            if state is not None and source_revision==state['source_revision']:
                c.execute('UPDATE plan_projection_state SET last_checked_at=? WHERE source=?',(now,source))
                return {'status':'ALREADY_PROJECTED','source':source,'source_revision':source_revision,'snapshot_digest':state['snapshot_digest'] or snapshot_digest,'tasks':[],'task_claims':[],'last_checked_at':now,'last_changed_at':state['last_changed_at'],'stale':False}
            task_ids=set(); changed=False; task_versions={}
            for task in tasks:
                if type(task) is not dict: raise Denied('INVALID_FIELD',field='tasks')
                tid=task.get('plan_task_id')
                if type(tid) is not str or not tid or tid in task_ids: raise Denied('INVALID_FIELD',field='plan_task_id')
                task_ids.add(tid)
                task_digest=task.get('digest') if type(task.get('digest')) is str and task.get('digest') else digest({'path':task.get('path'),'parent_id':task.get('parent_id'),'status':task.get('status'),'title':task.get('title'),'task_type':task.get('task_type')})
                semantic_content={k:task.get(k) for k in ('title','description','acceptance_criteria','task_type','required_roles','parent_id') if k in task}
                task_semantic_digest=task.get('semantic_digest') if type(task.get('semantic_digest')) is str and task.get('semantic_digest') else digest(semantic_content)
                task_revision=task.get('source_revision') if type(task.get('source_revision')) is str and task.get('source_revision') else source_revision
                archived=int(bool(task.get('archived')))
                current=c.execute('SELECT version,digest,archived FROM plan_task_versions WHERE plan_task_id=? ORDER BY version DESC LIMIT 1',(tid,)).fetchone()
                if current is None:
                    c.execute('INSERT INTO plan_tasks(plan_task_id,source,external_ref,created_at) VALUES(?,?,?,?)',(tid,task.get('source') or source,task.get('external_ref') or tid,task.get('created_at') or now))
                    c.execute('INSERT INTO plan_task_versions(plan_task_id,version,path,parent_id,status,title,task_type,source_revision,digest,semantic_digest,archived,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(tid,1,task.get('path'),task.get('parent_id'),task.get('status'),task.get('title'),task.get('task_type'),task_revision,task_digest,task_semantic_digest,archived,now))
                    task_versions[tid]=1; changed=True
                elif task_digest==current['digest'] and archived==current['archived']:
                    task_versions[tid]=current['version']
                else:
                    version=current['version']+1
                    c.execute('INSERT INTO plan_task_versions(plan_task_id,version,path,parent_id,status,title,task_type,source_revision,digest,semantic_digest,archived,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(tid,version,task.get('path'),task.get('parent_id'),task.get('status'),task.get('title'),task.get('task_type'),task_revision,task_digest,task_semantic_digest,archived,now))
                    task_versions[tid]=version; changed=True
            if mode=='full':
                incoming=set(task_ids)
                latest={}
                for row in c.execute('SELECT v.plan_task_id,v.version,v.path,v.parent_id,v.status,v.title,v.task_type,v.source_revision,v.digest,v.semantic_digest,v.archived FROM plan_task_versions v JOIN plan_tasks t ON t.plan_task_id=v.plan_task_id WHERE t.source=? ORDER BY v.plan_task_id,v.version',(source,)).fetchall():
                    latest[row['plan_task_id']]=row
                for tid,row in latest.items():
                    if tid in incoming or row['archived']: continue
                    version=row['version']+1
                    c.execute('INSERT INTO plan_task_versions(plan_task_id,version,path,parent_id,status,title,task_type,source_revision,digest,semantic_digest,archived,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(tid,version,row['path'],row['parent_id'],row['status'],row['title'],row['task_type'],source_revision,row['digest'],row['semantic_digest'],1,now))
                    task_versions[tid]=version; changed=True
            last_changed_at=now if changed else state['last_changed_at'] if state is not None else now
            if state is None:
                c.execute('INSERT INTO plan_projection_state(source,source_revision,last_checked_at,last_changed_at,snapshot_digest) VALUES(?,?,?,?,?)',(source,source_revision,now,last_changed_at or now,snapshot_digest))
            else:
                c.execute('UPDATE plan_projection_state SET source_revision=?,last_checked_at=?,last_changed_at=?,snapshot_digest=? WHERE source=?',(source_revision,now,last_changed_at or now,snapshot_digest or source_revision,source))
        return {'status':'PROJECTED','source':source,'source_revision':source_revision,'snapshot_digest':snapshot_digest,'tasks':tasks,'task_claims':task_versions,'last_checked_at':now,'last_changed_at':last_changed_at,'stale':False}

    @guarded
    def plan_tree(self,root,depth=3,at_version=None,require_fresh=False):
        self.scope(); self.ready()
        if type(root) is not str or not root: raise Denied('INVALID_FIELD',field='root')
        if type(depth) is not int or not 1 <= depth <= 10: raise Denied('INVALID_FIELD',field='depth')
        if at_version is not None and (type(at_version) is not int or at_version < 0): raise Denied('INVALID_FIELD',field='at_version')
        if type(require_fresh) is not bool: raise Denied('INVALID_FIELD',field='require_fresh')
        now=datetime.now(timezone.utc).isoformat()
        with self.store.read() as c:
            self.integrity_gate(c)  # QA004 F-26: the start-of-session reads report a broken chain too
            state=c.execute('SELECT source_revision,last_checked_at FROM plan_projection_state ORDER BY last_checked_at DESC LIMIT 1').fetchone()
            # Current tree: the max version of every task.  Historical tree:
            # per task, the newest version not above ``at_version``.
            cap='' if at_version is None else ' AND p.version<=?'
            task_sql=('SELECT v.plan_task_id,v.version,v.path,v.parent_id,v.status,v.title,v.task_type,v.source_revision,v.digest,v.semantic_digest,v.archived,t.source,t.external_ref,t.created_at '
                      'FROM plan_task_versions v JOIN plan_tasks t ON t.plan_task_id=v.plan_task_id '
                      'WHERE v.version=(SELECT MAX(p.version) FROM plan_task_versions p WHERE p.plan_task_id=v.plan_task_id'+cap+') '
                      'AND v.archived=0 ORDER BY v.path')
            tasks=c.execute(task_sql,() if at_version is None else (at_version,)).fetchall()
            newest=max((r['source_revision'] for r in tasks),key=revision_key,default=None)
            stale=projection_stale(state,newest,self.plan_ttl_seconds)
            if require_fresh and stale: raise Denied('PLAN_PROJECTION_STALE',field='source_revision')
            task_by_id={r['plan_task_id']:r for r in tasks}; task_by_path={r['path']:r for r in tasks}
            children_map=claim_entry_mod._children(c)
            coded=c.execute('SELECT plan_task_id FROM plan_branches WHERE branch=?',(root,)).fetchone()
            if coded and root not in task_by_id and root not in task_by_path: root=coded[0]
            if root not in task_by_id and root not in task_by_path and root[:1] in ('P','p','п') : root='П'+root[1:]
            if root not in task_by_id and root not in task_by_path and root[:1].isdigit(): root='П'+root
            resolved=task_by_id.get(root) or task_by_path.get(root)
            if resolved is None: raise Denied('PLAN_ROOT_NOT_FOUND',field='root')
            task_id=resolved['plan_task_id']

            def walk(tid,task_depth,ancestors):
                task=task_by_id.get(tid)
                if task is None or task['archived']: raise Denied('PLAN_ROOT_NOT_FOUND',field='root')
                if tid in ancestors: raise Denied('PLAN_CYCLE',field='task_id')
                claim_rows=c.execute('SELECT tc.claim_id,tc.role,tc.linked_at_plan_version,tc.linked_semantic_digest,c.origin_branch FROM task_claims tc JOIN claims c ON c.id=tc.claim_id WHERE tc.plan_task_id=? AND tc.linked_at_plan_version<=? ORDER BY tc.linked_at_plan_version,tc.claim_id,tc.role',(tid,task['version'])).fetchall()
                header=[r for r in claim_rows if r['role']=='defines']
                first=(header or claim_rows or [None])[-1 if header else 0]
                coded=c.execute('SELECT branch FROM plan_branches WHERE plan_task_id=?',(tid,)).fetchone()
                claim_id=_current_claim(children_map,first['claim_id']) if first else task['plan_task_id']
                branch=coded[0] if coded else (first['origin_branch'] if first else task['source'])
                node={'path':task['path'],'title':task['title'],'plan_task_id':tid,'claim_id':claim_id,'branch':branch,'status':task['status'],'claim_count':len(claim_rows),'children':[]}
                for child in tasks:
                    if child['parent_id']!=tid or child['archived']: continue
                    child_depth=task_depth+1
                    if child_depth>depth: continue
                    node['children'].append(walk(child['plan_task_id'],child_depth,ancestors|{tid}))
                return node

            root_task=task_by_id[task_id]; tree=walk(task_id,1,set()); task_list=[]
            def collect(node):
                task_list.append(node)
                for child in node['children']: collect(child)
            collect(tree)
            from .planning import _last_change
            last=_last_change(c,root_task['source'] or 'OMH') if 'principal' in {r[1] for r in c.execute('PRAGMA table_info(plan_checks)')} else None
            body={'status':'PLAN_TREE','root':root_task['path'],'depth':depth,'source':root_task['source'] or 'OMH','synced_at':(state['last_checked_at'] if state is not None else None) or now,'stale':stale,'tasks':task_list,
                  'source_revision':state['source_revision'] if state is not None else None,'ingested_by':last['principal'] if last else None}
        return public_dto.project(body,'PlanTree',scope=self.context.branches)

    @guarded
    def trace(self,branch='master'):
        self.scope()
        self.ready()
        if branch!='master': self.scope(branch)
        with self.store.read() as c:
            stamp,chain=self.integrity(c)
            if branch=='master' and not self.lite and not {'AA','AB'}<=self.context.branches:
                scope=[]
            elif branch=='master':
                scope=sorted(self.context.branches)
            else:
                scope=[branch]
            body=dict(self._trace(c,branch,chain),
                      read_snapshot=public_dto.read_snapshot(stamp,scope))
            return public_dto.project(body,'TraceResponse',scope=self.context.branches)
    def _trace(self,c,branch,chain=None):
        if branch=='master':
            if self.lite: branches=sorted(self.context.branches)
            elif not {'AA','AB'}<=self.context.branches: return {'status':'TRACE','readiness':'UNKNOWN','ready_for_owner_review':False,'simulated':True}
            else: branches=['AA','AB']
        else: branches=[branch]
        from .projections import trace_branch
        chain=public_dto.chain(chain if chain is not None else verify_chain(c))
        snaps=[self._snapshot(c,b) for b in branches]
        excluded=sorted({b for s in snaps for b in s['excluded_branches']})
        def reqs(s):
            # strict: the two fixture requirements; lite: whatever requirement_refs the claims actually carry
            return tuple(sorted({r for cl in s['claims'] for r in cl['item']['requirement_refs']})) if self.lite else ('R1','R2')
        traces=[trace_branch(c,s,any(i['state']!='ack' for i in delivery_facts.status(c,s['branch'])['intents']),reqs(s)) for s in snaps if s['branch'] not in excluded]
        residual=[r for t in traces for r in t['residual']]
        origins={k:v for t in traces for k,v in t.get('origins',{}).items()}
        blockers=sorted({b for t in traces for b in t['blockers']})
        ready=not blockers and bool(traces)
        readiness='READY' if ready else 'NOT_READY'
        if self.lite: readiness=self._readiness_counts(c,[s for s in snaps if s['branch'] not in excluded])
        return dict(status='ready_for_owner_review' if ready else 'TRACE',readiness=readiness,simulated=True,ready_for_owner_review=ready,residual=residual,origins=origins,blockers=blockers,chain=chain,excluded_branches=excluded,owner_close_called=False)

    def _readiness_counts(self,c,snaps):
        """Lite: admitted by anybody vs admitted by an independent reviewer, kept apart (O1).
        Rows without a label (written before v3) count as admitted but never as independent."""
        heads=dict(c.execute('SELECT branch,revision FROM branch_heads'))
        total=independent=stale=0
        for s in snaps:
            for cl in s['claims']:
                if cl['archived']: continue
                total+=1
                if cl.get('review_level')=='independent': independent+=1
                if any(heads.get(b)!=v for b,v in cl['dependencies'].items()): stale+=1
        pending,rejected=claim_entry_mod.pending_rejected(c,[s['branch'] for s in snaps])
        return dict(admitted_total=total,admitted_independent=independent,pending=pending,rejected=rejected,stale=stale)

    @guarded
    def export(self,branch,revision=None):
        self.scope(branch); self.ready()
        with self.store.read() as c:
            stamp=self.integrity_gate(c)
            info,raw=self._export_bytes(c,branch,revision)
        # File I/O happens after the read transaction: no read lock is held
        # across the filesystem (TD-DOM-34).
        if self._write_export(info['handle'],raw):
            result=dict(status='EXPORTED',handle=info['handle'],sha256=info['sha256'],
                        revision=info['revision'],simulated=True)
        else:
            result={'status':'EXPORT_PENDING','simulated':True}
        body=dict(result,read_snapshot=public_dto.read_snapshot(stamp,[branch]))
        return public_dto.project(body,'ExportResponse',scope=self.context.branches)
    def _export_bytes(self,c,branch,revision=None):
        import yaml
        # AU004-F-01: the YAML bytes are the same public Snapshot object the MCP
        # query returns -- branch-keyed dependency maps filtered by the caller's
        # launch grant.  Internal claim_snapshots / events keep the full maps.
        snap=public_dto.project(self._snapshot(c,branch,revision),'Snapshot',scope=self.context.branches)
        # QA004 F-31: libyaml when present (same bytes as the pure-Python emitter, ~6x faster)
        raw=yaml.dump(snap,Dumper=getattr(yaml,'CSafeDumper',yaml.SafeDumper),sort_keys=True,allow_unicode=False).encode('utf8')
        sha=raw_hash(raw); handle=f"exports/{branch}-{snap['revision']}-{sha}.yaml"
        return dict(handle=handle,sha256=sha,revision=snap['revision']),raw
    def _write_export(self,handle,raw):
        import os
        dest=self.directory/handle
        try:
            dest.parent.mkdir(parents=True,exist_ok=True)
            temp=dest.with_name(dest.name+'.'+str(uuid.uuid4())+'.tmp')
            with open(temp,'xb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
            self.fault_hook('before_rename')
            os.replace(temp,dest)
            self.fault_hook('after_rename')
            if dest.read_bytes()!=raw: raise OSError('readback mismatch')
        except OSError: return False
        return True

    @guarded
    def _delivery(self,intent,state,payload):
        # Compatibility surface: state may never be assigned by a caller, including
        # reviewers.  Capability/scope/state are validated before the refusal so
        # that no caller can forge a delivery state (TD-DOM-13, F-AU-03/F-AU-04).
        if state not in ('pending','ambiguous','ack'): raise Denied('REJECTED')
        self.scope(); self.capability('admit'); self.ready()
        with self.store.read() as c:
            row=c.execute('SELECT branch FROM outbox WHERE id=?',(intent,)).fetchone()
            if not row: raise Denied('SCOPE_DENIED')
            self.scope(row['branch'])
        raise Denied('DELIVERY_ENGINE_ONLY')

    @guarded
    def sync(self,branch):
        self._chain_cache={}
        try: return self._sync(branch)
        finally: self._chain_cache=None
    def _sync(self,branch):
        self.scope(branch); self.capability('admit'); self.ready()
        from .ports import OMHPort
        with self.store.read() as c:
            self.require_branch(c,branch)
            self.integrity_gate(c)
            intents=[dict(r) for r in c.execute('SELECT * FROM outbox WHERE branch=? ORDER BY revision,kind',(branch,))]
        for intent in intents:
            attempt=str(uuid.uuid4())
            with self.store.transaction() as c:
                self.integrity_gate(c)
                if delivery_facts.confirmed(c,intent): continue
                c.execute('INSERT INTO delivery_attempts VALUES(?,?,?,?,?,?,?,?)',
                          (attempt,intent['id'],intent['tx'],intent['digest'],intent['target'],self.context.principal,self.context.root,datetime.now(timezone.utc).isoformat()))
            self.fault_hook('after_attempt')
            exact=False; raw=b''; report='not_sent'
            # No read transaction around the external effect: the intent row is
            # immutable and the recording transaction below re-verifies the
            # chain.  Holding a read lock here would block writers until
            # busy_timeout under journal_mode=DELETE (TD-DOM-34).
            if intent['kind']=='YAML_EXPORT':
                with self.store.read() as c:
                    self.integrity_gate(c)
                    info,exported=self._export_bytes(c,branch,intent['revision'])
                if self._write_export(info['handle'],exported):
                    raw=exported
                    exact=raw_hash(raw)==info['sha256']
                    report='EXPORTED'
                else:
                    report='EXPORT_PENDING'
            else:
                port=OMHPort(self.directory,self.omh_mode)
                payload=json.loads(intent['payload'])
                expected={'digest':intent['digest'],'payload':payload}
                try:
                    observed=port.read_target(intent['target']).get(intent['tx'])
                    exact=observed==expected
                    if not exact:
                        report=port.attach(intent['target'],intent['tx'],intent['digest'],payload)
                        self.fault_hook('after_external_send')
                        observed=port.read_target(intent['target']).get(intent['tx'])
                        exact=observed==expected
                    raw=canonical(observed)
                except (OSError,ValueError):
                    report='TARGET_UNAVAILABLE'; exact=False; raw=b''
            observed_id=str(uuid.uuid4())
            with self.store.transaction() as c:
                stamp=self.integrity_gate(c)
                stamp['read_at']=datetime.now(timezone.utc).isoformat()
                stamp['port_reported']=report
                c.execute('INSERT INTO delivery_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (observed_id,attempt,intent['id'],intent['tx'],intent['digest'],intent['target'],int(exact),raw_hash(raw),len(raw),stamp['read_at'],self.context.principal,self.context.root,canonical(stamp),raw))
                # An explicitly lost reply remains ambiguous until a fresh reconcile.
                if exact and report!='ambiguous' and not delivery_facts.confirmed(c,intent):
                    c.execute('INSERT INTO delivery_acks VALUES(?,?,?,?,?)',
                              (intent['id'],observed_id,intent['tx'],intent['digest'],intent['target']))
            self.fault_hook('after_observation')
        with self.store.read() as c:
            stamp=self.integrity_gate(c)
            status=delivery_facts.status(c,branch)
        pending=any(i['state']!='ack' for i in status['intents'])
        return public_dto.project({'status':'SYNC_PENDING' if pending else 'SYNCED','simulated':True,
                                   'read_snapshot':public_dto.read_snapshot(stamp,[branch])},'Sync',scope=self.context.branches)

    @staticmethod
    def _parse_after(after):
        """Strict keyset cursor (F1): exactly ``'branch:id'``, both components
        non-empty, no extra ``':'`` inside the id (candidate ids are UUIDs).
        Anything else is a typed ``REJECTED`` before any DB work, so an empty
        branch can never reach the ``IN (...)`` keyset comparison."""
        if after is None:
            return None
        if type(after) is not str:
            raise Denied('REJECTED')
        parts = after.split(':', 1)
        if len(parts) != 2 or not parts[0] or not parts[1] or ':' in parts[1]:
            raise Denied('REJECTED')
        return (parts[0], parts[1])

    @guarded
    def resume(self, branch, limit=50, after=None):
        self.scope(branch); self.ready()
        # Pagination args are validated here (typed refusals, never raw
        # exceptions): limit must be an int in [1,500]; after a 'branch:id'
        # keyset cursor when present, with BOTH components non-empty and no
        # stray ':' inside the id (F1: ':x', 'AA:', 'AA:x:y', '', ints ->
        # REJECTED).
        if type(limit) is not int or not 1 <= limit <= 500: raise Denied('REJECTED')
        if after is not None:
            self._parse_after(after)
        with self.store.read() as c:
            stamp,chain=self.integrity(c)
            trace=self._trace(c,branch,chain)
            body={'status':'RESUMED','snapshot':self._snapshot(c,branch),
                  'trace':trace,
                  'sync':delivery_facts.status(c,branch),'simulated':True,
                  # QA004 F-21: lite resume(branch) lists that branch's open candidates only
                  'pending':self._pending_page(c, limit, after, only=branch if self.lite else None),
                  'read_snapshot':public_dto.read_snapshot(stamp,[branch])}
            if self.lite: body['readiness']=trace['readiness']
            return public_dto.project(body,'Resume',scope=self.context.branches)
    def _pending_page(self, c, limit, after, only=None):
        """Pending section page (RS007 W1, option A): metadata ONLY, never payload text.

        Visible to grant G iff candidate.branch in G AND payload.dependencies keys
        ⊆ G (same scope rule as candidate()/submit). Admitted predicate matches
        storage: EXISTS claims.candidate OR EXISTS idempotency admit-row. State is
        derived WITHOUT LLM: REVIEWED = has review; REJECTED = last review
        decision != 'approve'. ``count`` is EXACT and scope-filtered (its own pass,
        so no exists-oracle and no dependence on the page position). Keyset
        pagination ORDER BY (branch, id).

        AB007 (F2/F3): the visibility filters (deps ⊆ grant, admitted excluded)
        are applied IN SQL BEFORE LIMIT, so a page is always computed over the
        already-filtered set — an empty page cannot occur while visible rows
        remain, every visible row is reachable by following the last returned
        item's keyset, and ``has_more`` (fetch limit+1) counts only rows the
        reader can actually see (no exists-signal about hidden rows).
        """
        grant = set(self.context.branches)
        g_sorted = sorted(grant)
        ph = ','.join('?' * len(g_sorted))
        where = ['c.branch IN (%s)' % ph]
        if only is not None:
            where.append('c.branch = ?')
            where.append(OPEN_REVIEW_SQL)
        params = [self.context.work] + list(g_sorted)   # idem_key subquery + branch IN
        if after is not None:
            parsed = self._parse_after(after)   # F1: strict cursor
            assert parsed is not None
            ab, aid = parsed
            where.append('(c.branch, c.id) > (?, ?)')
            params.extend([ab, aid])
        if only is not None:
            params.insert(1 + len(g_sorted), only)
        where.append('NOT EXISTS (SELECT 1 FROM claims cl WHERE cl.candidate = c.id)')
        where.append("NOT EXISTS (SELECT 1 FROM reviews sr WHERE sr.candidate = c.id AND json_extract(sr.payload, '$.decision') = 'superseded')")  # rebased by dom_accept: the new candidate is the live one
        where.append("NOT EXISTS (SELECT 1 FROM idempotency i WHERE i.external_work_ref = ? "
                     "AND i.operation LIKE 'admit:%' AND i.candidate_id = c.id)")
        params.append(self.context.work)
        where.append("NOT EXISTS (SELECT 1 FROM json_each(json_extract(c.payload, '$.dependencies')) je "
                     'WHERE je.key NOT IN (%s))' % ph)
        params.extend(g_sorted)
        rows = c.execute(
            'SELECT c.id, c.digest, c.author, c.root, c.branch, LENGTH(c.payload) AS plen, c.created_at, '
            "json_extract(c.payload,'$.dependencies') AS deps_json, "
            "json_extract(c.payload,'$.expected_branch_revision') AS expected_revision, "
            "json_extract(c.payload,'$.operation') AS operation, "
            '(SELECT r.id FROM reviews r WHERE r.candidate=c.id ORDER BY r.rowid DESC LIMIT 1) AS last_review_id, '
            '(SELECT r.payload FROM reviews r WHERE r.candidate=c.id ORDER BY r.rowid DESC LIMIT 1) AS last_review_payload, '
            "(SELECT i.key FROM idempotency i WHERE i.external_work_ref=? AND i.operation LIKE 'submit:%' "
            '       AND i.candidate_id=c.id ORDER BY i.scope_digest LIMIT 1) AS idem_key '
            'FROM candidates c WHERE {where} ORDER BY c.branch, c.id LIMIT ?'.format(where=' AND '.join(where)),
            params + [limit + 1]).fetchall()
        items = []
        has_more = len(rows) > limit   # F3: post-filter — visible rows only
        for r in rows[:limit]:
            deps = self._scoped_deps(r['deps_json'], grant)
            state = 'AWAITING_REVIEW'
            review_id = None
            if r['last_review_id'] is not None:
                decision = None
                try:
                    decision = json.loads(r['last_review_payload']).get('decision') if r['last_review_payload'] else None
                except (ValueError, TypeError):
                    decision = None
                state = 'REJECTED' if (decision is not None and decision != 'approve') else 'REVIEWED'
                review_id = r['last_review_id']
            item = {
                'candidate_id': r['id'],
                'intent_digest': r['digest'],
                'state': state,
                'branch': r['branch'],
                'operation': r['operation'],
                'origin': {'author': r['author'], 'root': r['root']},
                'dependencies': deps,
                'expected_branch_revision': int(r['expected_revision'] or 0),
                'payload_length': int(r['plen']),
                'idempotency_key': r['idem_key'],
                'review_id': review_id,
            }
            if r['created_at'] is not None:
                try:
                    created = datetime.fromisoformat(r['created_at'])
                    age = int((datetime.now(timezone.utc) - created).total_seconds())
                    item['age_seconds'] = max(0, age)
                except (ValueError, TypeError):
                    pass  # legacy/unparseable: age omitted (never 0)
            items.append(item)
        status = 'PENDING_PRESENT' if items else 'NO_PENDING'
        return {'status': status, 'count': self._pending_count(c, only), 'has_more': has_more,
                'items': items, 'page': {'limit': limit, 'after': after}}
    @staticmethod
    def _scoped_deps(deps_json_text, grant):
        if not deps_json_text:
            return {}
        d = json.loads(deps_json_text)
        return {k: int(v) for k, v in d.items() if isinstance(v, int) and not isinstance(v, bool)}
    def _pending_count(self, c, only=None):
        """Exact pending count over the grant: in-branch, deps ⊆ grant, NOT admitted.

        Scope-filtered exactly like the page items (anti-exists-oracle: hidden
        candidates contribute neither items nor counters). A separate pass keeps
        the count exact at any page position (independent of after/limit).
        """
        grant = set(self.context.branches)
        rows = c.execute(
            "SELECT c.branch, json_extract(c.payload,'$.dependencies') AS deps_json, "
            'EXISTS(SELECT 1 FROM claims cl WHERE cl.candidate=c.id) AS admitted_by_claims, '
            "EXISTS(SELECT 1 FROM idempotency i WHERE i.external_work_ref=? AND i.operation LIKE 'admit:%' "
            '       AND i.candidate_id=c.id) AS admitted_by_idem '
            'FROM candidates c WHERE c.branch IN ({ph}) AND NOT EXISTS (SELECT 1 FROM reviews sr WHERE sr.candidate = c.id AND json_extract(sr.payload, \'$.decision\') = \'superseded\')'.format(ph=','.join('?' * len(grant)))
            + ('' if only is None else ' AND c.branch = ? AND ' + OPEN_REVIEW_SQL),
            [self.context.work] + list(sorted(grant)) + ([] if only is None else [only])).fetchall()
        n = 0
        for r in rows:
            if r['admitted_by_claims'] or r['admitted_by_idem']:
                continue
            d = self._scoped_deps(r['deps_json'], grant)
            if set(d).issubset(grant):
                n += 1
        return n

    # ---- WR-ADD (L0+L1): lite tools.  Logic lives in mockup/lite.py; every write goes
    # through the same submit -> review -> admit methods above (no bypass).
    @guarded
    def describe(self): return claim_entry_mod.describe(self)
    @guarded
    def start(self,request=None,goal=None,branches=None,adopt=False): return claim_entry_mod.start(self,request,goal,branches,adopt)
    @guarded
    def claim(self,branch,type,text,**fields):
        self._gate_write(fields.get('basis'))
        return claim_entry_mod.claim(self,branch,type,text,fields)
    @guarded
    def update(self,claim_id,text,reason,idempotency_key=None,anatomy=None,expected_current=None,mode='auto'):
        self._gate_write([claim_id])
        return claim_entry_mod.update(self,claim_id,text,reason,idempotency_key,anatomy,expected_current,mode)
    @guarded
    def task_graph(self,task,agent=None,include_history=True,include_children=True,limit=200):
        from . import planning; return planning.task_graph(self,task,agent,include_history,include_children,limit)
    @guarded
    def find(self,query=None,branch=None,type=None,requirement_ref=None,limit=20,plan_task=None,keyword=None,current_only=False):
        return claim_entry_mod.find(self,query,branch,type,requirement_ref,limit,plan_task,keyword,current_only)
    # ---- plan memory (mockup/planning.py)
    @guarded
    def plan_ingest(self,plan,source='agent',format='auto',contexts=None,source_revision=None,mode='full',base_revision=None,force=False,session=None):
        from . import planning; return planning.ingest(self,plan,source,format,contexts,source_revision,mode,base_revision,force,session)
    @guarded
    def plan_status(self,check_every=10):
        from . import planning, watcher
        # Plans queued by hooks while no duty process runs are taken in here.
        jobs=watcher.drain_inbox(self, log=lambda record: watcher.log_record(self.directory, record)) if self.lite and self.store.path.exists() else []
        result=planning.status(self,check_every)
        if result.get('status')=='PLAN_STATUS':
            result['inbox_processed']=len(jobs)
            inbox=watcher.inbox_state(self)  # 2d: what is still waiting, what could not be taken in
            result['inbox_queued']=inbox['queued']
            result['inbox_failed']=inbox['failed']
        return result
    @guarded
    def task_claims(self,task,include_children=False,role='all',include_history=False):
        from . import planning; return planning.task_claims(self,task,include_children,role,include_history)
    @guarded
    def context_pack(self,claim_id,depth=8):
        from . import planning; return planning.context(self,claim_id,depth)
    @guarded
    def accept(self,candidate_id,decision='approve',note=None): return claim_entry_mod.accept(self,candidate_id,decision,note)
    @guarded
    def annotate(self,claim_id,kind,**fields):
        self._gate_write([claim_id])
        return claim_entry_mod.annotate(self,claim_id,kind,fields)
    @guarded
    def extract(self,task,text,provider=None,link_role='documents'):
        from . import llm; return llm.extract(self,task,text,provider,link_role)
    @guarded
    def extract_submit(self,task,text,candidates,model=None,link_role='documents'):
        from . import llm; return llm.extract_submit(self,task,text,candidates,model,link_role)
    @guarded
    def reconcile(self):
        from . import planning
        claim_entry_mod._lite_only(self); self.scope(); self.ready()
        merged=planning.reconcile_variants(self)
        return {'status':'RECONCILED','simulated':True,'merged':merged}
    @guarded
    def distillate(self,action,source=None,distillate=None,text=None,anchors=None,by=None,note=None):
        from . import distillate as distillate_mod
        return distillate_mod.distillate(self,action,source,distillate,text,anchors,by,note)
