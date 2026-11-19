"""TD19: closed public DTO boundary for MCP responses.

The full internal integrity proof (``verify_chain`` result: events/head_hash/
broken locators; ``integrity_gate`` stamp: chain_head + ALL branch revisions;
delivery observation snapshot with port_reported) is never serialized to any
MCP response.  Public objects are rebuilt field-by-field from an explicit
recursive allowlist matching the closed structural schemas recorded in
artifacts/RESEARCHER_AB002_A.json (``public_schemas`` / ``tool_output_allowlists``
/ ``recursive_schema_rules``).  Unknown structural keys fail closed
(``PublicProjectionError``) instead of being copied through; the internal proof
is never redacted in place.

AU004-F-01: maps keyed by BRANCH (``('bmap', ...)``: Claim.dependencies,
Provenance.dependencies, ReadSnapshot.revisions) are filtered on output by the
caller's launch-time branch grant (``scope``); keys of other branches are
REMOVED (no marker, no counter).  Internal storage / admission / stale checks /
delivery stamps keep the full maps and are never rewritten here.

Documented opaque user-data slots (claim metadata by content_type, event value
by kind, properties/dependencies maps, raw bytes b64) are preserved exactly as
admitted data and are never merged with server proof.

Public wire contracts (per tool_output_allowlists):
- healthy      -> Snapshot/Trace/Resume/SyncStatus/Export/Sync + ReadSnapshot
- chain broken -> Broken exactly (no snapshot/chain/origins/exports)
- other errors -> typed fixed enums only (DomainError | StorageError)
"""
import copy

# ---------------------------------------------------------------- schema --

# field spec tokens:
#   'str' | 'int' | 'bool'            -> scalar
#   ('rec', NAME)                     -> nested closed record
#   ('list', NAME_or_token)           -> list of ...
#   ('map', NAME_or_token)            -> dict str->... (keys not filtered; NOT keyed by branch)
#   ('bmap', NAME_or_token)           -> dict branch->... filtered to the caller's launch grant (scope);
#                                        scope=None fails closed (SCOPE_REQUIRED)
#   ('opt', SPEC)                     -> absent allowed, present must project
#   ('null', NAME)                    -> None allowed, else project
#   ('opaque',)                       -> documented user data, preserved as-is

EVENT_VALUE_RECORDS = {
    'comment': {'text': 'str'},
    'link': {'url': 'str', 'locator': 'str'},
    'property': {'name': 'str', 'value': 'str'},
    'applicability': {'branch': 'str'},
    'archive': {},
    'replacement': {'replacement_ref': 'str'},
    'conflict': {'conflict_ref': 'str'},
    'resolve': {'conflict_ref': 'str'},
    'converge': {},
    'exclude': {'branch': 'str'},
}

RECORDS = {
    'ReadSnapshot': {
        'read_at': 'str',
        'revisions': ('bmap', 'int'),
        'integrity_status': 'str',
    },
    'Chain': {'status': 'str'},
    'Broken': {
        'status': 'str', 'readiness': 'str',
        'ready_for_owner_review': 'bool', 'blockers': ('list', 'str'),
        'simulated': 'bool', 'hint': ('opt', 'str'),
    },
    # WR-ADD: a refusal now carries a self-describing hint (what is wrong and the
    # exact next call).  The optional fields are the whole extension; every one is
    # server-built or a value the caller already owns (own branch names, own ids).
    'DomainError': {
        'status': 'str', 'hint': ('opt', 'str'), 'next_action': ('opt', 'str'),
        'branches': ('opt', ('list', 'str')), 'field': ('opt', 'str'),
        'claim_id': ('opt', 'str'), 'resolution': ('opt', ('opaque',)),
    },
    'StorageErrorBody': {'code': 'str', 'sqlite_code': ('null', 'int'), 'sqlite_name': ('null', 'str')},
    'StorageError': {'status': 'str', 'error': ('rec', 'StorageErrorBody'), 'hint': ('opt', 'str')},
    'ClaimItem': {
        'local_id': 'str', 'content_type': 'str', 'body_b64': 'str',
        'encoding': 'str', 'media_type': 'str', 'reason': 'str',
        'applicability': ('list', 'str'), 'metadata': ('opaque',),
        'requirement_refs': ('list', 'str'),
        'anatomy': ('opt', ('opaque',)),
    },
    'ClaimValue': {'item': ('rec', 'ClaimItem'), 'origin_version': 'str'},
    'AppendValue': {
        'local_id': 'str', 'event_type': 'str', 'claim_ref': 'str',
        'reason': 'str', 'value': ('event_value',), 'supersedes': 'str', 'ref': 'str',
    },
    'Provenance': {
        'value': ('discriminate_value',), 'actor': 'str', 'root': 'str', 'server_time': 'str',
        'origin_version': 'str', 'context_revision': 'int', 'reason': 'str',
        'review_id': 'str', 'dependencies': ('bmap', 'int'),
        # audit 2026-11-12 #4: present on v2-hashed events only.
        'hash_scheme': ('opt', 'int'),
        # QA004 F-24: digest of the review row (decision + review_level) bound into the chain.
        'review_digest': ('opt', 'str'),
    },
    'Claim': {
        'claim_id': 'str', 'logical_claim_id': 'str', 'version': 'int',
        'supersedes': ('null', 'str'),
        'origin_branch': 'str', 'origin_version': 'str',
        'body_b64': 'str', 'raw_sha256': 'str', 'item': ('rec', 'ClaimItem'),
        'archived': 'bool', 'properties': ('map', 'str'),
        'dependencies': ('bmap', 'int'), 'review_id': 'str',
        'applicable_branches': ('list', 'str'),
        # WR-ADD: present only in the lite profile; None = row without a label.
        'review_level': ('null', 'str'),
    },
    'History': {
        'event_id': 'str', 'kind': 'str', 'revision': 'int',
        'claim_ref': ('null', 'str'),
        'provenance': ('rec', 'Provenance'),
        'origin_branch': ('opt', 'str'), 'origin_version': ('opt', 'str'),
    },
    'Snapshot': {
        'status': 'str', 'branch': 'str', 'revision': 'int', 'simulated': 'bool',
        'claims': ('list', 'Claim'), 'history': ('list', 'History'),
        'conflicts': ('list', 'str'), 'converged': 'bool',
        'excluded_branches': ('list', 'str'),
    },
    'Origin': {'origin_branch': 'str', 'origin_version': 'str'},
    'ClaimOrigin': {'claim_id': 'str', 'origin_branch': 'str', 'origin_version': 'str'},
    'Residual': {
        'requirement_ref': 'str', 'branch': 'str', 'claim_refs': ('list', 'str'),
        'check_refs': ('list', 'str'), 'claim_origins': ('list', 'ClaimOrigin'),
        'reasons': ('list', 'str'),
    },
    # WR-ADD (lite): the two numbers are kept apart on purpose -- admitted by
    # anybody vs admitted by an independent reviewer (decision O1).
    'Readiness': {
        'admitted_total': 'int', 'admitted_independent': 'int',
        'pending': 'int', 'rejected': 'int', 'stale': 'int',
    },
    'Trace': {
        'status': 'str', 'readiness': ('oneof', 'str', ('rec', 'Readiness')), 'simulated': 'bool',
        'ready_for_owner_review': 'bool',
        'residual': ('opt', ('list', 'Residual')),
        'origins': ('opt', ('map', 'Origin')),
        'blockers': ('opt', ('list', 'str')),
        'chain': ('opt', ('rec', 'Chain')),
        'excluded_branches': ('opt', ('list', 'str')),
        'owner_close_called': ('opt', 'bool'),
    },
    'IntentStatus': {
        'intent_id': 'str', 'kind': 'str', 'tx_id': 'str', 'revision': 'int',
        'state': 'str', 'observed_id': ('null', 'str'),
    },
    'SyncStatus': {
        'status': 'str', 'simulated': 'bool', 'branch': 'str',
        'intents': ('list', 'IntentStatus'),
    },
    'Export': {
        'status': 'str', 'handle': 'str', 'sha256': 'str', 'revision': 'int',
        'simulated': 'bool',
    },
    'Resume': {
        'status': 'str', 'snapshot': ('rec', 'Snapshot'), 'trace': ('rec', 'Trace'),
        'sync': ('rec', 'SyncStatus'), 'simulated': 'bool',
        'pending': ('rec', 'PendingPage'),
        'read_snapshot': ('rec', 'ReadSnapshot'),
        'readiness': ('opt', ('rec', 'Readiness')),
    },
    'PendingOrigin': {'author': 'str', 'root': 'str'},
    'PendingItem': {
        'candidate_id': 'str', 'intent_digest': 'str', 'state': 'str',
        'branch': 'str', 'operation': 'str',
        'origin': ('rec', 'PendingOrigin'),
        'dependencies': ('bmap', 'int'),
        'expected_branch_revision': 'int', 'payload_length': 'int',
        'idempotency_key': ('null', 'str'),
        'review_id': ('null', 'str'),
        # age_seconds is OMITTED for legacy/unparseable created_at (never 0).
        'age_seconds': ('opt', 'int'),
    },
    'PendingPageMeta': {'limit': 'int', 'after': ('null', 'str')},
    'PendingPage': {
        'status': 'str', 'count': 'int', 'has_more': 'bool',
        'items': ('list', 'PendingItem'),
        'page': ('rec', 'PendingPageMeta'),
    },
    'Sync': {'status': 'str', 'simulated': 'bool', 'read_snapshot': ('rec', 'ReadSnapshot')},
    'Initialized': {'status': 'str', 'simulated': 'bool', 'work_ref': 'str'},
    'Candidate': {
        'status': 'str', 'candidate_id': 'str', 'intent_digest': 'str',
        'accepted_count': 'int', 'simulated': 'bool',
    },
    'InputError': {
        'local_id': ('null', 'str'), 'path': 'str', 'code': 'str',
        'required': 'bool', 'fix': 'str',
    },
    'Rejected': {
        'status': 'str', 'accepted_count': 'int', 'errors': ('list', 'InputError'),
        'rejected_local_ids': ('list', 'str'), 'hint': ('opt', 'str'),
    },
    'Reviewed': {'status': 'str', 'review_id': 'str', 'simulated': 'bool', 'decision': ('opt', 'str')},
    # audit 2026-11-12 #5: what a reviewer reads before deciding.
    'CandidateView': {
        'status': 'str', 'candidate_id': 'str', 'intent_digest': 'str', 'branch': 'str',
        'operation': 'str', 'state': 'str', 'author': 'str', 'expected_branch_revision': 'int',
        'reason': ('null', 'str'), 'items': ('opaque',), 'simulated': 'bool',
    },
    'Admitted': {
        'status': 'str', 'simulated': 'bool', 'tx_id': 'str', 'branch': 'str',
        'revision': 'int', 'intent_digest': 'str',
        'claim_ids': ('map', 'str'), 'event_ids': ('map', 'str'),
    },
}

# ---- WR-ADD (L0+L1): records of the lite tools --------------------------------
RECORDS.update({
    'NextStep': {'tool': 'str', 'why': 'str', 'arguments': ('opaque',)},
    'RootInfo': {
        'request_claim_id': 'str', 'goal_claim_id': ('null', 'str'), 'goal_confirmed': 'bool',
    },
    'Started': {
        'status': 'str', 'simulated': 'bool', 'profile': 'str', 'work_ref': 'str',
        'branches': ('list', 'str'), 'main_branch': 'str', 'adopted': 'bool',
        'branches_added': ('list', 'str'), 'root': ('rec', 'RootInfo'),
        'next_steps': ('list', 'NextStep'),
    },
    'Described': {
        'status': 'str', 'profile': 'str', 'simulated': 'bool', 'state': 'str',
        'summary': 'str', 'work_ref': ('null', 'str'), 'branches': ('list', 'str'),
        'tools': ('opaque',), 'claim_types': ('opaque',), 'cycle': ('list', 'str'),
        'review_level': ('opaque',), 'readiness': ('opaque',), 'limits': ('list', 'str'),
        'next_steps': ('list', 'NextStep'),
        'integrity': ('null', 'str'), 'review_policy': ('opaque',),
    },
    'ClaimWritten': {
        'status': 'str', 'simulated': 'bool', 'claim_id': 'str', 'record_id': 'str',
        'logical_claim_id': 'str', 'version': 'int', 'branch': 'str', 'type': 'str', 'revision': 'int',
        'review_level': ('null', 'str'), 'origin_version': 'str', 'raw_sha256': 'str',
        'next_steps': ('list', 'NextStep'),
        'plan_task': ('opt', 'str'), 'plan_path': ('opt', 'str'), 'link_role': ('opt', 'str'), 'forked_from': ('opt', 'str'),
    },
    'Updated': {
        'status': 'str', 'simulated': 'bool', 'new_claim_id': 'str', 'supersedes': 'str',
        'record_id': 'str', 'logical_claim_id': 'str', 'version': 'int', 'branch': 'str', 'revision': 'int',
        'review_level': ('null', 'str'), 'raw_sha256': 'str',
        'next_steps': ('list', 'NextStep'),
    },
    'FoundMatch': {
        'claim_id': 'str', 'branch': 'str', 'type': 'str', 'preview': 'str',
        'origin_version': 'str', 'review_level': ('null', 'str'), 'is_current': 'bool',
        'record_id': 'str', 'logical_claim_id': 'str', 'version': 'int',
        'supersedes': ('null', 'str'), 'revision': 'int',
    },
    'ClaimVersion': {
        'claim_id': 'str', 'version': 'int', 'supersedes': ('null', 'str'),
        'raw_sha256': 'str', 'revision': 'int', 'is_current': 'bool',
    },
    'ClaimVersions': {
        'status': 'str', 'logical_claim_id': 'str',
        'versions': ('list', 'ClaimVersion'),
        'resolution': ('opt', ('opaque',)),
        'read_snapshot': ('rec', 'ReadSnapshot'),
    },
    'DiffSide': {'claim_id': 'str', 'version': 'int', 'sha': 'str'},
    'DiffChanged': {
        'logical_id': 'str', 'a': ('rec', 'DiffSide'), 'b': ('rec', 'DiffSide'),
    },
    'Diff': {
        'status': 'str', 'comparable': 'bool', 'common_ancestor': ('null', 'str'),
        'only_a': ('list', 'str'), 'only_b': ('list', 'str'),
        'changed': ('list', 'DiffChanged'), 'identical': ('list', 'str'),
        'read_snapshot': ('rec', 'ReadSnapshot'),
    },
    'BaselineRange': {'branch': 'str', 'from_seq': 'int', 'to_seq': 'int'},
    'Recall': {
        'status': 'str', 'branch': 'str', 'at_seq': 'int', 'revision': 'int',
        'active_claims': ('list', 'Claim'), 'archived_claims': ('list', 'str'),
        'pending_decisions': ('null', ('list', 'str')),
        'resolved_conflicts': ('list', 'str'), 'related_branches': ('list', 'str'),
        'as_of': 'str', 'baseline_range': ('rec', 'BaselineRange'),
        'read_snapshot': ('rec', 'ReadSnapshot'),
    },
    'Found': {
        'status': 'str', 'simulated': 'bool', 'matches': ('list', 'FoundMatch'),
        'total': 'int', 'truncated': 'bool', 'read_snapshot': ('rec', 'ReadSnapshot'),
    },
    'PlanTreeTask': {
        'path': 'str', 'claim_id': 'str', 'branch': 'str', 'status': 'str',
        'claim_count': 'int', 'children': ('list', 'PlanTreeTask'),
        'title': ('opt', 'str'), 'plan_task_id': ('opt', 'str'),
    },
    # plan memory (mockup/planning.py); lite profile only (single owner sees every branch)
    'PlanIngested': {
        'status': 'str', 'simulated': 'bool', 'source': 'str', 'source_revision': 'str', 'snapshot_digest': 'str',
        'changed': 'bool', 'created': 'int', 'updated': 'int', 'unchanged': 'int', 'archived': ('list', 'str'),
        'preamble': 'str', 'tasks': ('opaque',), 'next_steps': ('list', 'NextStep'),
        'warnings': ('opt', ('list', 'str')),
    },
    'TaskClaims': {
        'status': 'str', 'simulated': 'bool', 'task_path': 'str', 'plan_task_id': 'str', 'branch': ('null', 'str'),
        'include_children': 'bool', 'claims_by_role': ('opaque',), 'orphaned_links': ('opaque',),
        'summary': ('opaque',), 'read_snapshot': ('rec', 'ReadSnapshot'),
    },
    'PlanStatus': {
        'status': 'str', 'simulated': 'bool', 'has_plan': 'bool', 'project_closed': 'bool', 'sources': ('opaque',),
        'items': 'int', 'open_items': 'int', 'iterations_since_change': 'int', 'check_every': 'int',
        'plan_check_due': 'bool', 'request': ('opaque',), 'last_request_at': ('null', 'str'),
        'inbox_processed': ('opt', 'int'),
        'conflicts': ('opt', ('opaque',)),
        'next_steps': ('list', 'NextStep'),
    },
    'ContextPack': {
        'status': 'str', 'simulated': 'bool', 'claim': ('opaque',), 'refines_chain': ('opaque',), 'basis': ('opaque',),
        'refinements': ('opaque',), 'plan': ('opaque',), 'root_request_claim_id': ('null', 'str'), 'resolution': ('opt', ('opaque',)),
        'read_snapshot': ('rec', 'ReadSnapshot'),
    },
    'Reconciled': {'status': 'str', 'simulated': 'bool', 'merged': ('opaque',)},
    'Annotated': {'status': 'str', 'simulated': 'bool', 'claim_id': 'str', 'kind': 'str', 'branch': 'str',
                  'revision': 'int', 'event_id': 'str'},
    'ExtractionRequest': {
        'status': 'str', 'simulated': 'bool', 'provider': 'str', 'task_path': 'str', 'task_title': 'str',
        'task_context': ('null', 'str'), 'branch': 'str', 'instructions': 'str', 'candidate_schema': ('opaque',),
        'prompt_digest': 'str', 'next_steps': ('list', 'NextStep'),
    },
    'Accepted': {
        'status': 'str', 'simulated': 'bool', 'tx_id': 'str', 'branch': 'str', 'revision': 'int', 'intent_digest': 'str',
        'claim_ids': ('map', 'str'), 'event_ids': ('map', 'str'), 'candidate_id': 'str', 'rebased': ('opt', 'bool'),
    },
    'ClaimCandidate': {
        'status': 'str', 'simulated': 'bool', 'candidate_id': 'str', 'intent_digest': 'str', 'branch': 'str',
        'type': 'str', 'next_steps': ('list', 'NextStep'),
    },
    'Extracted': {
        'status': 'str', 'simulated': 'bool', 'provider': 'str', 'model': 'str', 'prompt_digest': 'str',
        'checkpoint_ref': 'str', 'task_path': 'str', 'branch': 'str', 'candidates': ('opaque',),
        'already_recorded': ('list', 'str'), 'refused': ('opaque',), 'dropped': 'int', 'warning': ('null', 'str'),
        'next_steps': ('list', 'NextStep'),
    },
    'PlanTree': {
        'status': 'str', 'root': 'str', 'depth': 'int', 'source': 'str',
        'synced_at': 'str', 'stale': 'bool', 'tasks': ('list', 'PlanTreeTask'),
        'source_revision': ('opt', ('null', 'str')), 'ingested_by': ('opt', ('null', 'str')),
    },
    'TaskGraph': {
        'status': 'str', 'simulated': 'bool', 'task': ('opaque',), 'agents': ('opaque',), 'chains': ('opaque',),
        'truncated': 'bool', 'read_snapshot': ('rec', 'ReadSnapshot'),
    },
    'ClaimForked': {
        'status': 'str', 'simulated': 'bool', 'claim_id': 'str', 'forked_from': 'str', 'forked_from_author': ('null', 'str'),
        'branch': 'str', 'conflict_ref': 'str', 'version': 'int', 'revision': 'int', 'review_level': ('null', 'str'),
        'raw_sha256': 'str', 'next_steps': ('list', 'NextStep'),
    },
    'UpdateConflict': {
        'status': 'str', 'simulated': 'bool', 'claim_id': 'str', 'expected_current': ('null', 'str'), 'current_claim_id': 'str',
        'author': ('null', 'str'), 'root': ('null', 'str'), 'version': 'int', 'revision': 'int', 'next_steps': ('list', 'NextStep'),
        'resolution': ('opt', ('opaque',)),
    },
    'PlanConflict': {
        'status': 'str', 'simulated': 'bool', 'source': 'str', 'base_revision': 'str', 'current_revision': 'str',
        'changed_by': ('null', 'str'), 'changed_at': ('null', 'str'), 'item_paths': ('list', 'str'),
        'next_steps': ('list', 'NextStep'),
    },
    'DistillateState': {
        'status': 'str', 'simulated': 'bool', 'action': 'str',
        'source_id': ('null', 'str'), 'source_state': 'str',
        'distillate_id': ('null', 'str'), 'logical_claim_id': ('null', 'str'),
        'effective': ('null', ('opaque',)), 'versions': ('list', ('opaque',)),
        'open_conflicts': ('list', ('opaque',)), 'failed': ('list', 'str'),
        'detail': ('opaque',), 'review': ('null', ('opaque',)), 'repaired': 'bool', 'next_steps': ('list', 'NextStep'),
    },
})

# tool -> top-level public schema (healthy or fixed error surface)
TOOL_RESPONSES = {
    'dom_query': 'QueryResponse',
    'dom_snapshot': 'QueryResponse',
    'dom_trace': 'TraceResponse',
    'dom_resume': 'Resume',
    'dom_sync_status': 'SyncStatusResponse',
    'dom_export': 'ExportResponse',
    'dom_sync': 'Sync',
    'dom_plan_tree': 'PlanTree',
    'dom_candidate': 'CandidateView',
    'dom_claim_versions': 'ClaimVersions',
    'dom_diff': 'Diff',
    'dom_recall': 'Recall',
}
LITE_RESPONSES = {
    'dom_start': ('Started', ('STARTED', 'ALREADY_STARTED')),
    'dom_describe': ('Described', ('DESCRIBED',)),
    'dom_claim': ('ClaimWritten', ('CLAIM_ADMITTED', 'ALREADY_RECORDED')),
    'dom_update': ('Updated', ('CLAIM_UPDATED',)),
    'dom_find': ('Found', ('FOUND',)),
    'dom_plan_ingest': ('PlanIngested', ('PLAN_INGESTED', 'PLAN_UNCHANGED')),
    'dom_task_claims': ('TaskClaims', ('TASK_CLAIMS',)),
    'dom_plan_status': ('PlanStatus', ('PLAN_STATUS',)),
    'dom_context': ('ContextPack', ('CONTEXT',)),
    'dom_task_graph': ('TaskGraph', ('TASK_GRAPH',)),
    'dom_reconcile': ('Reconciled', ('RECONCILED',)),
    'dom_extract': ('Extracted', ('EXTRACTED',)),
    'dom_accept': ('Accepted', ('DEMO_ADMITTED',)),
    'dom_extract_submit': ('Extracted', ('EXTRACTED',)),
    'dom_annotate': ('Annotated', ('ANNOTATED',)),
    'dom_distillate': ('DistillateState', ('DISTILLATE_SHOWN', 'PROPOSED', 'ALREADY_RECORDED', 'VALIDATED',
                                           'REVIEW_FAILED', 'ACCEPTED', 'WITHDRAWN', 'DISPUTED', 'RESOLVED')),
}
DOMAIN_ERROR_FIELDS = frozenset(RECORDS['DomainError'])

# ------------------------------------------------------------ error help --
# (hint, next_action) per refusal status; the first pair is the strict wording,
# the second the lite wording.  Text only: it names tools and arguments the
# caller already holds, never another scope's data.
_HELP = {
    'NOT_INITIALIZED': (
        ('The database is not initialized. Call dom_init with the launch-fixture external_work_ref and policy_digest.', 'dom_init'),
        ('No project is open in this directory yet. Call dom_start with the customer request, e.g. dom_start({"request": "<customer request verbatim>"}).', 'dom_start')),
    'SCOPE_DENIED': (
        ('The work, branch or id is outside this server launch grant, or the database is bound to a different work. Nothing about the other scope is disclosed. Use only granted branches (AA, AB).', None),
        ('This database belongs to another work, or the branch/candidate is outside your grant. If the database in this directory is your own project, adopt it with one argument: dom_start({"request": "<customer request>", "adopt": true}). Otherwise call dom_describe to see your branches.', 'dom_start')),
    'UNKNOWN_BRANCH': (
        ('The branch is not declared in this project. Pick one of the listed branches.', 'dom_describe'),
        ('The branch is not declared in this project. Use one of the listed branches, or declare new ones with dom_start({"request": "<same request>", "branches": ["main", "<new>"]}).', 'dom_describe')),
    'NO_SUCH_BRANCH': (
        ('The granted branch has no head in this database.', 'dom_resume'),
        ('The branch has no head in this database. Call dom_describe for the declared branches.', 'dom_describe')),
    'REVISION_CONFLICT': (
        ('The branch moved after the candidate was built. Re-read with dom_resume, rebuild the candidate from the new head and submit again.', 'dom_resume'),
        ('The branch moved while writing. Re-read with dom_resume and repeat the call.', 'dom_resume')),
    'STALE_REVIEW': (
        ('The review no longer matches the candidate, policy or dependency heads. Re-read with dom_resume and review again.', 'dom_resume'),
        ('The review no longer matches the branch head. Re-read with dom_resume and repeat the call.', 'dom_resume')),
    'DIGEST_MISMATCH': (
        ('The intent_digest does not match the stored candidate. Copy intent_digest exactly from the dom_submit/dom_resume answer.', 'dom_resume'),
        ('The intent_digest does not match the stored candidate. Copy it exactly from dom_resume.', 'dom_resume')),
    'IDEMPOTENCY_MISMATCH': (('This idempotency_key was already used for different content. Use a fresh key for new content.', None), ('This key was already used for different content.', None)),
    'IDEMPOTENCY_BINDING_MISMATCH': (('The idempotency_key is bound to another candidate or branch. Use a fresh key.', None), ('The key is bound to another candidate or branch.', None)),
    'IDEMPOTENCY_REVIEW_MISMATCH': (('The idempotency_key was used with another review. Reuse the original review_id or a fresh key.', None), ('The key was used with another review.', None)),
    'IDEMPOTENCY_BINDING_UNKNOWN': (('A legacy idempotency row blocks this key. Use a fresh key.', None), ('A legacy idempotency row blocks this key. Use a fresh key.', None)),
    'POLICY_MISMATCH': (
        ('policy_digest is not the policy of this server. It is a launch value, not something to compute.', 'dom_init'),
        ('policy_digest is computed by the server in the lite profile; do not pass it.', 'dom_describe')),
    'REJECTED': (
        ('The input was refused. Read the listed field errors (path/code/fix), correct them and resubmit.', None),
        ('The input was refused. Read the listed field errors, correct them and repeat the call.', 'dom_describe')),
    'INDEPENDENCE_NOT_ENFORCED': (
        ('Review/admit needs a different identity root than the author (and the launch capability). Use a reviewer launch.', None),
        ('Refused by the review policy of this launch (dom_describe -> review_policy). Under "independent" the author cannot accept '
         'its own candidate and the reviewer does not write claims: the reviewer reads the candidate (dom_candidate) and decides with dom_accept.', 'dom_describe')),
    'OWNER_AUTHORITY_REQUIRED': (('Only the owner launch may do this.', None), ('Not available in the lite profile.', 'dom_describe')),
    'OMH_BINDING_UNVERIFIED': (('The server was launched with --no-simulation: no live binding exists, every init is refused.', None), ('The server was launched with --no-simulation: no live binding exists.', None)),
    'DELIVERY_ENGINE_ONLY': (('Delivery states are written only by the engine through dom_sync.', 'dom_sync'), ('Delivery states are written only by dom_sync.', 'dom_sync')),
    'MIGRATION_REQUIRED': (
        ('The database layout needs a migration. Run dom_init once to migrate it additively.', 'dom_init'),
        ('The database layout needs a migration. Call dom_start once to migrate it additively.', 'dom_start')),
    'PROFILE_STRICT': (('This operation exists only in the lite profile.', None), ('This operation exists only in the lite profile.', None)),
    'PROFILE_LITE': (('This operation exists only in the strict profile.', None), ('This operation is not available in the lite profile; use dom_start.', 'dom_start')),
    'REQUEST_REQUIRED': (('', None), ('dom_start needs the customer request verbatim (non-empty string): dom_start({"request": "<customer request>"}). It becomes the immutable root claim; there is no empty baseline.', 'dom_start')),
    'ROOT_CONFLICT': (('', None), ('A different root request/goal is already recorded in this database. The root is immutable and was not changed. Repeat dom_start with the original request, or refine the goal with dom_update on the goal claim.', 'dom_find')),
    'INVALID_FIELD': (('', None), ('An argument is missing or has the wrong value; see the field name. Call dom_describe for valid examples.', 'dom_describe')),
    'UNKNOWN_TYPE': (('', None), ('Unknown claim type. Valid: quote, decision, formula, code, data, concept, text. dom_describe shows an example call for each.', 'dom_describe')),
    'EMPTY_TEXT': (('', None), ('text must be a non-empty string. The text is stored byte-for-byte as the claim body.', None)),
    'TEXT_TOO_LARGE': (('', None), ('text is larger than 1 MiB (UTF-8). Split it into several claims.', None)),
    'INVALID_TEXT': (('', None), ('text is not valid Unicode text (for example lone surrogates). Send plain UTF-8 text.', None)),
    'REVISION_OUT_OF_RANGE': (('', None), ('The revision is outside 0..head of this branch; see the hint for the range.', 'dom_resume')),
    'CONFLICT_READ_REQUIRED': (('', None), ('The claim changed since the version you read (current_claim_id, author). Nothing was written. Read it with dom_claim_versions, then update it or fork (mode=fork) if it is another agent\'s version that does not fit.', 'dom_claim_versions')),
    'FORK_NOT_ALLOWED': (('', None), ('A fork is for another agent\'s version. Your own current version is updated (mode=update) or left as is.', 'dom_update')),
    'PLAN_CONFLICT': (('', None), ('The plan changed since your base_revision (see changed_by, item_paths). Nothing was written. Read the current plan with dom_plan_tree, merge, and send it with base_revision=current_revision; force=true overwrites on purpose.', 'dom_plan_tree')),
    'ROOT_IMMUTABLE': (('', None), ('The customer request (root claim) is never replaced. Add a clarifying claim with basis=[root claim_id] instead.', 'dom_claim')),
    'NO_SUCH_CANDIDATE': (('', None), ('No candidate with this id in this project. Pending candidates are listed by dom_resume.', 'dom_resume')),
    'NO_SUCH_CLAIM': (('', None), ('No claim with this id in this project. Find the id with dom_find.', 'dom_find')),
    'NOT_CURRENT': (('', None), ('This claim version was already replaced. Update the current version (claim_id in this answer, or find it with dom_find).', 'dom_find')),
    'STATEMENT_NOT_FOUND': (
        ('The id is not a captured statement source or a distillate version of one. Take the ids from the capture line or the statement ledger.', 'dom_distillate'),
        ('No captured statement with this id. Take source/distillate from the [DOM project memory] statement line, or call dom_distillate(action="show", source=<id>).', 'dom_distillate')),
    'PROPOSAL_INVALID': (
        ('The proposal is malformed: text and one inner anchors array per sentence are required.', 'dom_distillate'),
        ('The proposal needs text (1-3 sentences) and anchors: one inner array per sentence, each a list of verbatim quotes from the source.', 'dom_distillate')),
    'PROPOSAL_CONFLICT': (
        ('The current version has the same normalized text with different anchors. Withdraw it or supply the original anchors.', 'dom_distillate'),
        ('The current version already has this text with other anchors. Withdraw it or supply the original anchors.', 'dom_distillate')),
    'SPAN_NOT_FOUND': (
        ('An anchor is not a verbatim substring of the source.', 'dom_distillate'),
        ('An anchor is not a verbatim part of the source. Quote the source exactly (copy the sentence).', 'dom_distillate')),
    'SPAN_AMBIGUOUS': (
        ('An anchor occurs more than once in the source; use a longer unique quote.', 'dom_distillate'),
        ('An anchor occurs more than once in the source; quote a longer, unique piece.', 'dom_distillate')),
    'NO_IMPROVEMENT': (
        ('The candidate text equals the effective version (normalized). Propose a real improvement or keep the effective version.', 'dom_distillate'),
        ('The candidate is the same text as the effective version. Propose a real improvement or keep it.', 'dom_distillate')),
    'REVIEW_REQUIRED': (
        ('Acceptance needs a passing review of this exact version: run validate first.', 'dom_distillate'),
        ('Acceptance needs a passing validate on this exact version: call dom_distillate(action="validate", source=<id>) first.', 'dom_distillate')),
    'REVIEW_FAILED': (
        ('The latest review of this version failed; fix the text/anchors and propose again.', 'dom_distillate'),
        ('The latest review failed; fix the text or anchors and propose again.', 'dom_distillate')),
    'REVIEW_STALE': (
        ('The review does not match the current text or anchors (they changed). Run validate again.', 'dom_distillate'),
        ('The review no longer matches the text or anchors. Run validate again.', 'dom_distillate')),
    'DISTILLATE_DISPUTED': (
        ('An open conflict on the source or this version blocks the action; resolve it first.', 'dom_distillate'),
        ('An open dispute blocks this action. Resolve it with dom_distillate(action="resolve", ...).', 'dom_distillate')),
    'DISTILLATE_LOOP': (
        ('Three failed reviews for this logical claim; the chain is stopped. Ask the user for a new instruction (a new source starts a new chain).', 'dom_distillate'),
        ('Three failed reviews: the chain is stopped, there is no reset. Ask the user; a new instruction becomes a new source.', 'dom_distillate')),
    'NO_CHANGE': (('', None), ('The new text equals the current text; nothing to update.', None)),
    'UPDATE_INCOMPLETE': (('', None), ('The new version was admitted but the old one is not yet marked as replaced. Repeat the same dom_update call to finish; it will not create a duplicate.', 'dom_update')),
    'INVALID_ARGUMENTS': (('Arguments are matched exactly: no unknown keys, no wrong types.', 'dom_describe'), ('Arguments are matched exactly: no unknown keys, no wrong types. profile, review_level, principal and work are fixed at server launch and cannot be passed.', 'dom_describe')),
    'UNKNOWN_TOOL': (('Unknown tool name. Use tools/list.', None), ('Unknown tool in this profile. Call dom_describe for the tool list.', 'dom_describe')),
    'STORAGE_BUSY': (('The database is busy. Retry the same call in a moment.', None), ('The database is busy. Retry the same call in a moment.', None)),
    'STORAGE_CORRUPT': (('The database file is not a valid DOM database. Stop and tell the operator.', None), ('The database file is not a valid DOM database. Stop and tell the operator.', None)),
    'STORAGE_READ_ONLY': (('The database is read-only. Tell the operator.', None), ('The database is read-only. Tell the operator.', None)),
    'STORAGE_CONSTRAINT': (('A storage constraint refused the write (immutability or integrity rule).', None), ('A storage constraint refused the write (immutability or integrity rule).', None)),
    'STORAGE_UNAVAILABLE': (('The database could not be used. Retry once; if it persists tell the operator.', None), ('The database could not be used. Retry once; if it persists tell the operator.', None)),
    'CHAIN_BROKEN': (('The event hash chain failed verification. Stop writing and tell the operator.', None), ('The event hash chain failed verification: the database was edited outside the server. Writes are refused. Stop and tell the user.', None)),
    'MISSING_FIELD': (
        ('A field required for this claim type is missing or empty. Nothing was recorded; supply it and retry.', 'dom_describe'),
        ('A field required for this claim type is missing or empty. Nothing was recorded. dom_describe lists the required fields per type with an example call.', 'dom_describe')),
    'REVIEW_REJECTED': (
        ('The latest review of this candidate rejected it. It cannot be admitted; submit a corrected candidate.', 'dom_candidate'),
        ('The candidate was rejected in review. Read the note with dom_candidate, then record a corrected claim.', 'dom_candidate')),
    'CANDIDATE_CLOSED': (
        ('The candidate is already admitted or rejected.', 'dom_candidate'),
        ('The candidate is already admitted or rejected; nothing to decide.', 'dom_candidate')),
    'TASK_NOT_FOUND': (
        ('No plan item with this id, path or branch code.', 'dom_plan_tree'),
        ('No plan item with this id, path (П1.2) or branch code (AA200). Send the plan with dom_plan_ingest or look at dom_plan_tree.', 'dom_plan_tree')),
    'TASK_ARCHIVED': (
        ('The plan item was removed from the plan.', 'dom_plan_tree'),
        ('The plan item was removed from the plan; link the claim to a current item (dom_plan_tree).', 'dom_plan_tree')),
    'PLAN_EMPTY': (
        ('No plan items were found in the plan text.', 'dom_plan_ingest'),
        ('No plan items were found. Send numbered items (1., 1.1), bullets or headings, or JSON [{"title": ...}].', 'dom_plan_ingest')),
    'PROFILE_STRICT': (
        ('This tool belongs to the lite (local single owner) profile.', 'dom_describe'),
        ('This tool belongs to the lite profile.', 'dom_describe')),
    'PLAN_ROOT_NOT_FOUND': (
        ('The requested plan root is not in the OMH projection. Use a valid task id or path.', 'dom_plan_tree'),
        ('The requested plan root is not in the OMH projection. Use a task id or path from the current plan.', 'dom_plan_tree')),
    'PLAN_CYCLE': (
        ('The plan hierarchy contains a cycle. Stop traversal and inspect the task graph.', 'dom_plan_tree'),
        ('The plan hierarchy contains a cycle. Stop traversal and inspect the task graph.', 'dom_plan_tree')),
    'PLAN_PROJECTION_STALE': (
        ('The OMH plan projection is stale. Re-run the plan sync before reading the tree.', 'dom_plan_tree'),
        ('The OMH plan projection is stale. Re-run the plan sync before reading the tree.', 'dom_plan_tree')),
    'INTERNAL_ERROR': (('Internal fault; no details are exposed. Retry once, then tell the operator.', None), ('Internal fault; no details are exposed. Retry once, then tell the operator.', None)),
    'PUBLIC_PROJECTION_INVALID': (('The server refused to serialize its own answer. Tell the operator.', None), ('The server refused to serialize its own answer. Tell the operator.', None)),
}


def error_help(status, profile='strict'):
    """``{'hint', 'next_action'?}`` for a refusal status, or ``{}`` if none is defined."""
    entry = _HELP.get(status)
    if entry is None:
        return {}
    hint, action = entry[1] if profile == 'lite' else entry[0]
    out = {}
    if hint:
        out['hint'] = hint
    if action:
        out['next_action'] = action
    return out

# composite records: base record + read_snapshot (built programmatically)
_COMPOSITE_EXTRA = {'read_snapshot': ('rec', 'ReadSnapshot')}
for _name, _base in {
    'QueryResponse': 'Snapshot',
    'TraceResponse': 'Trace',
    'SyncStatusResponse': 'SyncStatus',
    'ExportResponse': 'Export',
}.items():
    RECORDS.setdefault(_name, dict(RECORDS[_base]))
    RECORDS[_name].update(_COMPOSITE_EXTRA)


class PublicProjectionError(Exception):
    """Fail-closed projection refusal. Never a raw value dump."""

    def __init__(self, code, path):
        self.code = code
        self.path = path
        super().__init__(code + ' ' + path)


def _check_scalar(value, token, path):
    types = {'str': str, 'int': int, 'bool': bool, 'json': None}[token]
    if token == 'json':
        if value is None or type(value) in (str, int, bool, float):
            return value
        raise PublicProjectionError('INVALID_TYPE', path)
    if type(value) is not types:
        raise PublicProjectionError('INVALID_TYPE', path)
    return value


def _project_record_dict(value, fields, path, scope=None):
    """Validate a dict against an inline closed field map (event values)."""
    if type(value) is not dict:
        raise PublicProjectionError('NOT_RECORD', path)
    out = {}
    for field, fspec in fields.items():
        if field not in value:
            raise PublicProjectionError('MISSING_FIELD', path + '.' + field)
        out[field] = _project(value[field], fspec, path + '.' + field, scope)
    for key in value:
        if key not in fields:
            raise PublicProjectionError('UNKNOWN_FIELD', path + '.' + str(key))
    return out


def _project(value, spec, path, scope=None):
    if spec == 'str' or spec == 'int' or spec == 'bool' or spec == 'json':
        return _check_scalar(value, spec, path)
    kind = spec[0]
    if kind == 'rec':
        name = spec[1]
        if type(value) is not dict:
            raise PublicProjectionError('NOT_RECORD', path)
        record = RECORDS.get(name)
        if record is None:
            raise PublicProjectionError('UNKNOWN_SCHEMA', path)
        out = {}
        for field, fspec in record.items():
            if field not in value:
                if isinstance(fspec, tuple) and fspec[0] in ('opt', 'null'):
                    continue
                raise PublicProjectionError('MISSING_FIELD', path + '.' + field)
            if name == 'AppendValue' and field == 'value':
                # The per-kind event value record is resolved here, once
                # event_type is already projected (it precedes 'value' in the
                # record), instead of instantiating the ('event_value',)
                # placeholder which would hard-fail every append in history.
                et = out.get('event_type')
                fields = EVENT_VALUE_RECORDS.get(et) if isinstance(et, str) else None
                if fields is None:
                    raise PublicProjectionError('UNKNOWN_EVENT_KIND', path + '.event_type')
                out[field] = _project_record_dict(value[field], fields, path + '.' + field, scope)
                continue
            out[field] = _project(value[field], fspec, path + '.' + field, scope)
        for key in value:
            if key not in record:
                raise PublicProjectionError('UNKNOWN_FIELD', path + '.' + str(key))
        return out
    if kind == 'list':
        if type(value) is not list:
            raise PublicProjectionError('NOT_LIST', path)
        elem  = spec[1] if (isinstance(spec[1], tuple) or spec[1] in ('str','int','bool','json')) else ('rec', spec[1])
        return [_project(v, elem, path + '[]', scope) for v in value]
    if kind == 'map':
        if type(value) is not dict:
            raise PublicProjectionError('NOT_MAP', path)
        vspec = spec[1] if (isinstance(spec[1], tuple) or spec[1] in ('str','int','bool','json')) else ('rec', spec[1])
        return {str(k): _project(v, vspec, path + '.' + str(k), scope) for k, v in value.items()}
    if kind == 'bmap':
        # AU004-F-01: branch-keyed map, scope-first.  Keys outside the caller's
        # launch grant are dropped (values of dropped keys are not even looked
        # at).  No grant known -> refuse instead of copying everything.
        if type(value) is not dict:
            raise PublicProjectionError('NOT_MAP', path)
        if scope is None:
            raise PublicProjectionError('SCOPE_REQUIRED', path)
        vspec = spec[1] if (isinstance(spec[1], tuple) or spec[1] in ('str','int','bool','json')) else ('rec', spec[1])
        out = {}
        for k, v in value.items():
            if type(k) is not str:
                raise PublicProjectionError('INVALID_KEY', path)
            if k in scope:
                out[k] = _project(v, vspec, path + '.' + k, scope)
        return out
    if kind == 'opt':
        if value is None:
            return None
        return _project(value, spec[1], path, scope)
    if kind == 'null':
        if value is None:
            return None
        return _project(value, spec[1], path, scope)
    if kind == 'oneof':
        for alt in spec[1:]:
            try:
                return _project(value, alt, path, scope)
            except PublicProjectionError:
                continue
        raise PublicProjectionError('INVALID_TYPE', path)
    if kind == 'opaque':
        return copy.deepcopy(value)
    if kind == 'discriminate_value':
        # Provenance.value: claims carry ClaimValue {item,...}; appends carry
        # AppendValue (closed per-kind value record).
        if type(value) is dict and 'item' in value:
            return _project(value, ('rec', 'ClaimValue'), path, scope)
        return _project(value, ('rec', 'AppendValue'), path, scope)
    if kind == 'event_value':
        raise PublicProjectionError('UNRESOLVED_EVENT_VALUE', path)
    raise PublicProjectionError('BAD_SPEC', path)


def project(obj, name, scope=None):
    """Rebuild ``obj`` against the closed structural schema ``name``.

    ``scope``: the caller's launch-time branch grant (iterable of branch names).
    Required for any schema that contains a branch-keyed map (``bmap``); it is
    normalised to a frozenset here so callers cannot pass a mutable view."""
    return _project(obj, ('rec', name), '$', None if scope is None else frozenset(scope))


# ------------------------------------------------------------- builders --

def read_snapshot(stamp, scope):
    """Public ReadSnapshot: read_at + verified head revisions of the requested
    authorized scope only; integrity_status CHAIN_OK after the global gate
    passed. chain_head and all out-of-scope revisions are never copied."""
    scope = set(scope)
    revisions = {k: int(v) for k, v in (stamp.get('revisions') or {}).items()
                 if k in scope and isinstance(v, int) and not isinstance(v, bool)}
    return {'read_at': stamp['read_at'], 'revisions': revisions,
            'integrity_status': 'CHAIN_OK'}


def chain(verify_result):
    """Public Chain: status only. events/head_hash/broken locators stay internal."""
    return {'status': verify_result['status']}


def broken(result):
    """Public Broken DTO: fixed safe shape, no chain/read_snapshot/origins/
    foreign locators. The global gate result is available to the operator
    oracle, never to an MCP caller through this object."""
    return {'status': 'CHAIN_BROKEN', 'readiness': 'NOT_READY',
            'ready_for_owner_review': False,
            'blockers': list(result.get('blockers') or ['EVENT_CHAIN_BROKEN']),
            'simulated': True,
            'hint': _HELP['CHAIN_BROKEN'][0][0]}


# ------------------------------------------------------- tool serializer --

def _refusal(status, profile, extra=None):
    """DomainError with the table hint (unless the caller supplied its own)."""
    body = {'status': status}
    for key, value in (extra or {}).items():
        if key in DOMAIN_ERROR_FIELDS and key != 'status':
            body[key] = value
    for key, value in error_help(status, profile).items():
        body.setdefault(key, value)
    return _project(body, ('rec', 'DomainError'), '$', None)


def project_tool(name, result, scope=None, profile='strict'):
    """Validate/serialize a domain result for one MCP tool into a single
    public object. Fixed typed errors for everything else; never a raw value
    dump, never str(untrusted_exception).

    WR-ADD: every refusal carries ``hint`` / ``next_action`` from the closed
    ``_HELP`` table (``profile`` selects the strict or the lite wording)."""
    scope = None if scope is None else frozenset(scope)
    if type(result) is not dict:
        raise PublicProjectionError('NOT_RECORD', '$')
    status = result.get('status')
    if not isinstance(status, str):
        raise PublicProjectionError('MISSING_STATUS', '$')
    if status == 'CHAIN_BROKEN':
        return _project(result, ('rec', 'Broken'), '$', scope)
    if isinstance(result.get('error'), dict) and set(result) == {'status', 'error'}:
        out = _project(result, ('rec', 'StorageError'), '$', scope)
        hint = error_help(out['error']['code'], profile).get('hint')
        if hint:
            out['hint'] = hint
        return out
    if set(result) <= DOMAIN_ERROR_FIELDS:
        # uniform domain refusal (SCOPE_DENIED / REJECTED / NOT_INITIALIZED /
        # MIGRATION_REQUIRED / INDEPENDENCE_NOT_ENFORCED / POLICY_MISMATCH /
        # DIGEST_MISMATCH / STALE_REVIEW / REVISION_CONFLICT / ...): DomainError + hint
        return _refusal(status, profile, result)
    if name == 'dom_extract' and status == 'EXTRACTION_REQUEST':
        return _project(result, ('rec', 'ExtractionRequest'), '$', scope)
    if name == 'dom_update' and status == 'CLAIM_FORKED':
        return _project(result, ('rec', 'ClaimForked'), '$', scope)
    if name == 'dom_update' and status == 'CONFLICT_READ_REQUIRED':
        out = _project(result, ('rec', 'UpdateConflict'), '$', scope)
        out.update(error_help('CONFLICT_READ_REQUIRED', profile))
        return out
    if name == 'dom_plan_ingest' and status == 'PLAN_CONFLICT':
        out = _project(result, ('rec', 'PlanConflict'), '$', scope)
        out.update(error_help('PLAN_CONFLICT', profile))
        return out
    if name in ('dom_claim', 'dom_update', 'dom_annotate') and status == 'RECORDED_CANDIDATE':
        return _project(result, ('rec', 'ClaimCandidate'), '$', scope)
    if name in LITE_RESPONSES:
        record, ok_statuses = LITE_RESPONSES[name]
        if status in ok_statuses:
            return _project(result, ('rec', record), '$', scope)
        return _refusal(status, profile)
    if name in TOOL_RESPONSES:
        return _project(result, ('rec', TOOL_RESPONSES[name]), '$', scope)
    if name == 'dom_init':
        if status == 'INITIALIZED':
            return _project(result, ('rec', 'Initialized'), '$', scope)
        return _refusal(status, profile)
    if name in ('dom_submit', 'dom_append'):
        if status == 'RECORDED_CANDIDATE':
            return _project(result, ('rec', 'Candidate'), '$', scope)
        if status == 'REJECTED' and 'errors' in result:
            out = _project(result, ('rec', 'Rejected'), '$', scope)
            out.update(error_help('REJECTED', profile))
            return out
        return _refusal(status, profile)
    if name == 'dom_review':
        if status in ('DEMO_REVIEWED', 'REVIEW_REJECTED') and 'review_id' in result:
            return _project(result, ('rec', 'Reviewed'), '$', scope)
        return _refusal(status, profile)
    if name == 'dom_admit':
        if status == 'DEMO_ADMITTED':
            return _project(result, ('rec', 'Admitted'), '$', scope)
        return _refusal(status, profile)
    if isinstance(result.get('error'), dict):
        return _project(result, ('rec', 'StorageError'), '$', scope)
    return _refusal(status, profile)
