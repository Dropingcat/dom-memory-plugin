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

# ------------------------------------------------------------- help data --
# The per-tool help dictionary (opencode/HELP_DATA.py) is advisory: a tool call
# never fails and no refusal changes because the file is absent (the Claude tree
# ships no HELP_DATA.py).  Loaded once, by sys.path (the launcher puts the
# plugin root there) and, failing that, by path next to ``mockup/``.
HELP_LIMIT = 500
HELP_PREFIX = 'Тебе НУЖНО:'


def _load_tool_help():
    try:
        from HELP_DATA import HELP_DATA as data  # type: ignore
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    try:
        import importlib.util
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / 'HELP_DATA.py'
        spec = importlib.util.spec_from_file_location('_dom_help_data', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        data = getattr(module, 'HELP_DATA', {})
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


TOOL_HELP_DATA = _load_tool_help()

DEFAULT_HELP = ('Тебе НУЖНО: 1) Прочитать status и hint — там причина отказа. '
                '2) Исправить аргументы по hint. 3) Повторить вызов. '
                '4) Если отказ повторяется — вызвать dom_describe и сверить аргументы инструмента.')


def tool_help(tool):
    """``help`` text of one tool from HELP_DATA, or '' when it is not known."""
    entry = TOOL_HELP_DATA.get(tool) if isinstance(tool, str) else None
    text = entry.get('help') if isinstance(entry, dict) else None
    return text.strip()[:HELP_LIMIT] if isinstance(text, str) and text.strip() else ''


# Russian, imperative fallback per refusal status: used when the tool carries no
# HELP_DATA entry (or no explicit help).  Never invented codes, only real ones.
_STATUS_HELP = {
    'STORAGE_BUSY': 'Тебе НУЖНО: 1) Подождать мгновение. 2) Повторить тот же вызов. 3) Если STORAGE_BUSY не проходит — остановиться и сообщить оператору.',
    'STORAGE_CORRUPT': 'Тебе НУЖНО: 1) Остановиться: файл базы повреждён или это не DOM-база. 2) Не править базу вручную. 3) Сообщить оператору путь базы и код ошибки.',
    'STORAGE_READ_ONLY': 'Тебе НУЖНО: 1) Остановиться: база доступна только на чтение. 2) Проверить права на файл и каталог. 3) Сообщить оператору.',
    'STORAGE_CONSTRAINT': 'Тебе НУЖНО: 1) Прочитать hint: сработало правило неизменяемости или целостности. 2) Не обходить правило прямым SQL. 3) Записать изменение новым claim/событием и повторить.',
    'STORAGE_UNAVAILABLE': 'Тебе НУЖНО: 1) Повторить вызов один раз. 2) Если отказ повторяется — остановиться и сообщить оператору. 3) Не пересоздавать базу самостоятельно.',
    'CHAIN_BROKEN': 'Тебе НУЖНО: 1) Остановить запись: цепочка событий не проходит проверку. 2) Не править базу прямым SQL. 3) Вызвать dom_trace и сообщить оператору разрыв.',
    'MIGRATION_REQUIRED': 'Тебе НУЖНО: 1) В strict-профиле вызвать dom_init. 2) В lite-профиле вызвать dom_start с тем же request. 3) Повторить исходный вызов.',
    'INTERNAL_ERROR': 'Тебе НУЖНО: 1) Повторить вызов один раз. 2) Если ошибка повторяется — сообщить оператору. 3) Не пытаться обойти инструмент прямым SQL.',
    'PUBLIC_PROJECTION_INVALID': 'Тебе НУЖНО: 1) Не повторять тот же вызов вслепую. 2) Сообщить оператору: сервер отказал в сериализации собственного ответа. 3) Приложить имя инструмента.',
    'PROFILE_STRICT': 'Тебе НУЖНО: 1) Понять профиль: этот инструмент есть только в lite. 2) Использовать strict-аналог (dom_init/dom_submit/dom_review/dom_admit) или перезапустить сервер в lite. 3) Свериться с dom_describe.',
    'PROFILE_LITE': 'Тебе НУЖНО: 1) Понять профиль: этот инструмент есть только в strict. 2) В lite использовать dom_start. 3) Свериться с dom_describe.',
    'SCOPE_DENIED': 'Тебе НУЖНО: 1) Использовать только свои ветки и идентификаторы. 2) Если база в каталоге — твой проект, вызвать dom_start с исходным request и adopt=true. 3) Иначе сменить каталог или сообщить оператору.',
    'NOT_INITIALIZED': 'Тебе НУЖНО: 1) Вызвать dom_describe и посмотреть state. 2) При state=NOT_STARTED вызвать dom_start с запросом заказчика. 3) Повторить исходный вызов.',
    'UNKNOWN_BRANCH': 'Тебе НУЖНО: 1) Вызвать dom_describe — он вернёт список branches. 2) Выбрать код из списка. 3) Передать его в branch.',
    'NO_SUCH_BRANCH': 'Тебе НУЖНО: 1) Вызвать dom_describe и проверить state. 2) Убедиться, что ветка объявлена и в базе есть её голова. 3) При необходимости вызвать dom_start.',
    'REVISION_OUT_OF_RANGE': 'Тебе НУЖНО: 1) Прочитать hint: там допустимый диапазон revision. 2) Взять revision в 0..head из dom_resume. 3) Повторить вызов.',
    'REVISION_CONFLICT': 'Тебе НУЖНО: 1) Перечитать состояние через dom_resume. 2) Собрать кандидата заново от новой головы ветки. 3) Повторить submit/review/admit.',
    'STALE_REVIEW': 'Тебе НУЖНО: 1) Перечитать кандидата и ветку через dom_resume. 2) Выполнить review заново на актуальных данных. 3) Адмитить новый review_id.',
    'DIGEST_MISMATCH': 'Тебе НУЖНО: 1) Взять intent_digest без изменений из dom_candidate/dom_submit. 2) Передать его тем же значением. 3) Повторить вызов.',
    'INDEPENDENCE_NOT_ENFORCED': 'Тебе НУЖНО: 1) Проверить review_policy в dom_describe. 2) Под independent ревьюер не должен быть автором кандидата. 3) Читать кандидата через dom_candidate и решать через dom_accept/dom_review.',
    'REJECTED': 'Тебе НУЖНО: 1) Прочитать errors[] (path/code/fix) или hint. 2) Исправить указанные поля без приведения типов. 3) Повторить вызов.',
    'INVALID_FIELD': 'Тебе НУЖНО: 1) Посмотреть поле field и hint. 2) Привести аргумент к допустимому виду. 3) Свериться с dom_describe и повторить вызов.',
    'INVALID_ARGUMENTS': 'Тебе НУЖНО: 1) Передать аргументы строго по сигнатуре инструмента. 2) Убрать неизвестные ключи и не менять типы. 3) Помнить: profile, review_level, principal и work задаются при запуске. 4) Повторить вызов.',
    'UNKNOWN_TYPE': 'Тебе НУЖНО: 1) Выбрать type из quote, decision, formula, code, data, concept, text. 2) Посмотреть пример для своего type в dom_describe. 3) Повторить вызов.',
    'MISSING_FIELD': 'Тебе НУЖНО: 1) Посмотреть поле field. 2) Добавить обязательное для type поле (dom_describe покажет пример). 3) Повторить вызов.',
    'EMPTY_TEXT': 'Тебе НУЖНО: 1) Передать непустой text. 2) Не обрезать материал до пробелов. 3) Повторить вызов.',
    'TEXT_TOO_LARGE': 'Тебе НУЖНО: 1) Разбить материал на части до 1 MiB. 2) Записать каждую часть отдельным claim. 3) Связать части через basis/refines.',
    'INVALID_TEXT': 'Тебе НУЖНО: 1) Передать обычный UTF-8 текст без одиночных суррогатов. 2) Перекодировать материал. 3) Повторить вызов.',
    'NO_SUCH_CLAIM': 'Тебе НУЖНО: 1) Найти claim_id через dom_find. 2) Убедиться, что claim в твоих ветках. 3) Повторить вызов с найденным id.',
    'NO_SUCH_CANDIDATE': 'Тебе НУЖНО: 1) Взять candidate_id из dom_resume.pending или dom_extract. 2) Прочитать кандидата через dom_candidate. 3) Повторить вызов.',
    'NOT_CURRENT': 'Тебе НУЖНО: 1) Прочитать dom_claim_versions. 2) Взять claim_id с is_current=true. 3) Повторить update для текущей версии.',
    'CONFLICT_READ_REQUIRED': 'Тебе НУЖНО: 1) Прочитать текущую версию через dom_claim_versions. 2) Передать expected_current=current_claim_id. 3) Повторить dom_update или сделать fork, если версия чужая.',
    'FORK_NOT_ALLOWED': 'Тебе НУЖНО: 1) Понять, что это твоя же текущая версия. 2) Обновить её mode=update или оставить как есть. 3) Fork применять только к чужой версии.',
    'ROOT_IMMUTABLE': 'Тебе НУЖНО: 1) Не менять корневой request заказчика. 2) Записать уточнение новым claim с basis=[root claim_id]. 3) Goal уточнять отдельным dom_update goal-claim.',
    'NO_CHANGE': 'Тебе НУЖНО: 1) Изменить text или anatomy. 2) Либо не вызывать update: записывать нечего. 3) Повторить только при реальном изменении.',
    'CANDIDATE_CLOSED': 'Тебе НУЖНО: 1) Прочитать состояние через dom_candidate. 2) Не решать кандидата повторно. 3) Для исправления записать новый claim и пройти review заново.',
    'REVIEW_REJECTED': 'Тебе НУЖНО: 1) Прочитать note отклонения через dom_candidate. 2) Записать исправленный claim. 3) Провести review заново.',
    'REVIEW_REQUIRED': 'Тебе НУЖНО: 1) Выполнить validate этой версии. 2) Только после успешного review вызывать accept. 3) Повторить последовательность.',
    'REVIEW_FAILED': 'Тебе НУЖНО: 1) Исправить text или anchors. 2) Предложить версию заново (propose). 3) Повторить validate.',
    'REVIEW_STALE': 'Тебе НУЖНО: 1) Понять, что text или anchors изменились. 2) Выполнить validate заново. 3) Только потом accept.',
    'GATE_NO_READ': 'Тебе НУЖНО: 1) Вызвать dom_find или dom_context для каждого claim в basis. 2) Получить read receipt. 3) Повторить вызов.',
    'REVIEWER_SESSION_REQUIRED': 'Тебе НУЖНО: 1) Убедиться, что review вызван с корректной session. 2) Вызвать dom_review с явным session_id.',
    'TASK_NOT_FOUND': 'Тебе НУЖНО: 1) Вызвать dom_plan_tree и взять существующий id/путь/код. 2) Либо загрузить план через dom_plan_ingest. 3) Повторить вызов.',
    'TASK_ARCHIVED': 'Тебе НУЖНО: 1) Вызвать dom_plan_tree. 2) Выбрать текущий (не архивный) пункт плана. 3) Привязать claim к нему.',
    'PLAN_CONFLICT': 'Тебе НУЖНО: 1) Прочитать текущий план через dom_plan_tree. 2) Слить изменения. 3) Повторить dom_plan_ingest с base_revision=current_revision.',
    'PLAN_EMPTY': 'Тебе НУЖНО: 1) Оформить план нумерованными пунктами (1., 1.1), списком или заголовками. 2) Либо передать JSON [{"title": ...}]. 3) Повторить вызов.',
    'PLAN_ROOT_NOT_FOUND': 'Тебе НУЖНО: 1) Взять корень из dom_plan_status или dom_plan_tree. 2) Передать id, путь (П1.2) или код ветки. 3) Повторить вызов.',
    'PLAN_PROJECTION_STALE': 'Тебе НУЖНО: 1) Переслать план через dom_plan_ingest. 2) Дождаться свежей проекции. 3) Повторить dom_plan_tree.',
    'PLAN_CYCLE': 'Тебе НУЖНО: 1) Остановить обход. 2) Проверить структуру плана на цикл. 3) Исправить и переслать план.',
    'STALE_PROJECTION_SNAPSHOT': 'Тебе НУЖНО: 1) Переслать план через dom_plan_ingest с актуальным source_revision. 2) Дождаться свежей проекции. 3) Повторить dom_plan_tree.',
    'PLAN_TOO_WIDE': 'Тебе НУЖНО: 1) Сократить план до допустимого числа пунктов и подпунктов. 2) Разбить работу на несколько планов по этапам. 3) Повторить dom_plan_ingest.',
    'STATEMENT_NOT_FOUND': 'Тебе НУЖНО: 1) Взять source/distillate из строки [DOM project memory]. 2) Либо вызвать dom_distillate(action="show"). 3) Повторить вызов.',
    'PROPOSAL_INVALID': 'Тебе НУЖНО: 1) Передать text в 1-3 предложения. 2) Передать anchors: по одному массиву дословных цитат на предложение. 3) Повторить propose.',
    'SPAN_NOT_FOUND': 'Тебе НУЖНО: 1) Скопировать предложение источника дословно. 2) Не пересказывать текст. 3) Повторить вызов.',
    'SPAN_AMBIGUOUS': 'Тебе НУЖНО: 1) Взять более длинную уникальную цитату. 2) Убедиться, что она встречается один раз. 3) Повторить вызов.',
    'DISTILLATE_DISPUTED': 'Тебе НУЖНО: 1) Закрыть открытый спор через action=resolve с note. 2) Дождаться снятия блокировки. 3) Повторить действие.',
    'DISTILLATE_LOOP': 'Тебе НУЖНО: 1) Остановиться: три неудачных review закрыли цепочку. 2) Спросить пользователя о новом указании. 3) Новый источник начнёт новую цепочку.',
    'NO_IMPROVEMENT': 'Тебе НУЖНО: 1) Предложить реально изменённый text. 2) Либо оставить эффективную версию. 3) Повторить только при улучшении.',
    'REQUEST_REQUIRED': 'Тебе НУЖНО: 1) Передать запрос заказчика дословно: dom_start({"request": "<запрос>"}). 2) Не передавать пустую строку. 3) Повторить вызов.',
    'ROOT_CONFLICT': 'Тебе НУЖНО: 1) Повторить dom_start с исходным request. 2) Goal уточнять через dom_update, не перезаписывать корень. 3) Для нового проекта сменить каталог.',
    'UNKNOWN_TOOL': 'Тебе НУЖНО: 1) Вызвать dom_describe — он вернёт список инструментов профиля. 2) Выбрать имя из списка. 3) Повторить вызов.',
    'OMH_BINDING_UNVERIFIED': 'Тебе НУЖНО: 1) Понять, что сервер запущен с --no-simulation. 2) Перезапустить без этого флага. 3) Сообщить оператору.',
    'POLICY_MISMATCH': 'Тебе НУЖНО: 1) Взять policy_digest из launch-конфигурации без изменений. 2) Не вычислять его самостоятельно. 3) Повторить вызов.',
    'OWNER_AUTHORITY_REQUIRED': 'Тебе НУЖНО: 1) Понять, что операция доступна только роли owner. 2) Перезапустить сервер с ролью owner. 3) Повторить вызов.',
    'DELIVERY_ENGINE_ONLY': 'Тебе НУЖНО: 1) Не писать состояние доставки вручную. 2) Вызвать dom_sync. 3) Прочитать результат через dom_sync_status.',
    'IDEMPOTENCY_MISMATCH': 'Тебе НУЖНО: 1) Для нового содержимого взять новый idempotency_key. 2) Для повтора использовать исходный ключ и те же данные. 3) Повторить вызов.',
    'IDEMPOTENCY_BINDING_MISMATCH': 'Тебе НУЖНО: 1) Взять новый idempotency_key. 2) Либо повторить исходный вызов с теми же candidate/branch. 3) Не смешивать ключи между кандидатами.',
    'IDEMPOTENCY_REVIEW_MISMATCH': 'Тебе НУЖНО: 1) Использовать исходный review_id. 2) Либо взять новый idempotency_key. 3) Повторить вызов.',
    'IDEMPOTENCY_BINDING_UNKNOWN': 'Тебе НУЖНО: 1) Взять новый idempotency_key. 2) Не переиспользовать старый ключ. 3) Повторить вызов.',
}

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
        'simulated': 'bool', 'hint': ('opt', 'str'), 'help': ('opt', 'str'),
    },
    # WR-ADD: a refusal now carries a self-describing hint (what is wrong and the
    # exact next call).  The optional fields are the whole extension; every one is
    # server-built or a value the caller already owns (own branch names, own ids).
    'DomainError': {
        'status': 'str', 'hint': ('opt', 'str'), 'next_action': ('opt', 'str'),
        'branches': ('opt', ('list', 'str')), 'field': ('opt', 'str'),
        'claim_id': ('opt', 'str'), 'resolution': ('opt', ('opaque',)),
        'help': ('opt', 'str'),
    },
    'StorageErrorBody': {'code': 'str', 'sqlite_code': ('null', 'int'), 'sqlite_name': ('null', 'str')},
    'StorageError': {'status': 'str', 'error': ('rec', 'StorageErrorBody'), 'hint': ('opt', 'str'),
                     'help': ('opt', 'str')},
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
        'directory': 'str', 'db_path': 'str',
        # L4/L5: session channel and cross-channel database-path health.
        'session': ('opaque',), 'db': ('opaque',), 'health': ('opaque',),
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
        'inbox_processed': ('opt', 'int'), 'inbox_queued': ('opt', 'int'), 'inbox_failed': ('opt', 'int'),
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
    # Read-only skill router (dom_skill_select): every field is derived data, no ledger
    # proof is involved.  ``signals`` maps a queued skill id to its consensus signals.
    'SkillSelection': {
        'status': 'str', 'step_id': 'str', 'plan_version': 'int', 'index_digest': 'str',
        'fingerprint': 'str', 'block': 'str',
        'queue': ('list', 'str'), 'advisory': ('list', 'str'), 'gaps': ('list', 'str'),
        'prefetch': ('list', 'str'), 'missing_caps': ('list', 'str'), 'groups': ('list', 'str'),
        'confidence_bp': 'int', 'signals': ('map', ('list', 'str')),
    },
    # Usage help (dom_help): the strings are the server's own HELP_DATA table,
    # never ledger content, so the record is closed but carries no proof.
    'HelpError': {'code': 'str', 'cause': 'str', 'fix': 'str'},
    'ToolHelp': {
        'status': 'str', 'tool': 'str', 'usage': 'str',
        'preconditions': ('list', 'str'), 'postconditions': ('list', 'str'),
        'common_errors': ('list', 'HelpError'), 'help': 'str',
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
    'dom_skill_select': ('SkillSelection', ('SKILLS_SELECTED',)),
    'dom_help': ('ToolHelp', ('OK',)),
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


def error_help(status, profile='strict', tool=None, help_text=None):
    """``{'hint'?, 'next_action'?, 'help'}`` for a refusal status.

    ``help`` is always non-empty and actionable: the explicit ``help_text`` wins,
    then the tool's HELP_DATA entry, then the status fallback, then
    ``DEFAULT_HELP``.  ``hint``/``next_action`` keep the closed ``_HELP`` wording
    (``profile`` selects strict or lite)."""
    entry = _HELP.get(status)
    out = {}
    if entry is not None:
        hint, action = entry[1] if profile == 'lite' else entry[0]
        if hint:
            out['hint'] = hint
        if action:
            out['next_action'] = action
    text = (help_text or '').strip()
    if not text:
        # Infrastructure refusals (storage, chain, internal) are about the server,
        # not about the tool's arguments: the status text is the actionable one.
        if status.startswith('STORAGE_') or status in ('CHAIN_BROKEN', 'MIGRATION_REQUIRED',
                                                       'INTERNAL_ERROR', 'PUBLIC_PROJECTION_INVALID'):
            text = _STATUS_HELP.get(status) or ''
        text = text or tool_help(tool) or _STATUS_HELP.get(status) or DEFAULT_HELP
    out['help'] = text[:HELP_LIMIT]
    return out


# Every status that is a refusal (raised as DomainError or returned as a value):
# ``guarded`` uses it to attach help to a refusal returned instead of raised.
KNOWN_REFUSALS = frozenset(_HELP) | frozenset(_STATUS_HELP)


def invalid_arguments(tool=None, help_text=None):
    """Error helper for transport argument binding (INVALID_ARGUMENTS)."""
    return error_help('INVALID_ARGUMENTS', 'lite', tool=tool, help_text=help_text)


def invalid_field(field, tool=None, hint=None, help_text=None):
    """Error helper for one refused field (INVALID_FIELD)."""
    out = error_help('INVALID_FIELD', 'lite', tool=tool, help_text=help_text)
    out['field'] = field
    if hint:
        out['hint'] = hint
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
            'hint': _HELP['CHAIN_BROKEN'][0][0],
            'help': _STATUS_HELP['CHAIN_BROKEN']}


# ------------------------------------------------------- tool serializer --

def _refusal(status, profile, extra=None, tool=None):
    """DomainError with the table hint (unless the caller supplied its own) and
    an actionable ``help`` (explicit, per tool, else per status)."""
    body = {'status': status}
    for key, value in (extra or {}).items():
        if key in DOMAIN_ERROR_FIELDS and key != 'status':
            body[key] = value
    for key, value in error_help(status, profile, tool=tool).items():
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
        out = _project(result, ('rec', 'Broken'), '$', scope)
        out.setdefault('help', _STATUS_HELP['CHAIN_BROKEN'])
        return out
    if isinstance(result.get('error'), dict) and set(result) == {'status', 'error'}:
        out = _project(result, ('rec', 'StorageError'), '$', scope)
        meta = error_help(out['error']['code'], profile, tool=name)
        hint = meta.get('hint')
        if hint:
            out['hint'] = hint
        out['help'] = meta['help']
        return out
    if set(result) <= DOMAIN_ERROR_FIELDS:
        # uniform domain refusal (SCOPE_DENIED / REJECTED / NOT_INITIALIZED /
        # MIGRATION_REQUIRED / INDEPENDENCE_NOT_ENFORCED / POLICY_MISMATCH /
        # DIGEST_MISMATCH / STALE_REVIEW / REVISION_CONFLICT / ...): DomainError + hint
        return _refusal(status, profile, result, tool=name)
    if name == 'dom_extract' and status == 'EXTRACTION_REQUEST':
        return _project(result, ('rec', 'ExtractionRequest'), '$', scope)
    if name == 'dom_update' and status == 'CLAIM_FORKED':
        return _project(result, ('rec', 'ClaimForked'), '$', scope)
    if name == 'dom_update' and status == 'CONFLICT_READ_REQUIRED':
        out = _project(result, ('rec', 'UpdateConflict'), '$', scope)
        out.update(error_help('CONFLICT_READ_REQUIRED', profile, tool=name))
        return out
    if name == 'dom_plan_ingest' and status == 'PLAN_CONFLICT':
        out = _project(result, ('rec', 'PlanConflict'), '$', scope)
        out.update(error_help('PLAN_CONFLICT', profile, tool=name))
        return out
    if name in ('dom_claim', 'dom_update', 'dom_annotate') and status == 'RECORDED_CANDIDATE':
        return _project(result, ('rec', 'ClaimCandidate'), '$', scope)
    if name in LITE_RESPONSES:
        record, ok_statuses = LITE_RESPONSES[name]
        if status in ok_statuses:
            return _project(result, ('rec', record), '$', scope)
        return _refusal(status, profile, tool=name)
    if name in TOOL_RESPONSES:
        return _project(result, ('rec', TOOL_RESPONSES[name]), '$', scope)
    if name == 'dom_init':
        if status == 'INITIALIZED':
            return _project(result, ('rec', 'Initialized'), '$', scope)
        return _refusal(status, profile, tool=name)
    if name in ('dom_submit', 'dom_append'):
        if status == 'RECORDED_CANDIDATE':
            return _project(result, ('rec', 'Candidate'), '$', scope)
        if status == 'REJECTED' and 'errors' in result:
            out = _project(result, ('rec', 'Rejected'), '$', scope)
            out.update(error_help('REJECTED', profile, tool=name))
            return out
        return _refusal(status, profile, tool=name)
    if name == 'dom_review':
        if status in ('DEMO_REVIEWED', 'REVIEW_REJECTED') and 'review_id' in result:
            return _project(result, ('rec', 'Reviewed'), '$', scope)
        return _refusal(status, profile, tool=name)
    if name == 'dom_admit':
        if status == 'DEMO_ADMITTED':
            return _project(result, ('rec', 'Admitted'), '$', scope)
        return _refusal(status, profile, tool=name)
    if isinstance(result.get('error'), dict):
        out = _project(result, ('rec', 'StorageError'), '$', scope)
        out['help'] = _STATUS_HELP.get(status) or DEFAULT_HELP
        return out
    return _refusal(status, profile, tool=name)
