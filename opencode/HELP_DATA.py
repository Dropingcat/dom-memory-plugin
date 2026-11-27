"""Help data for every registered DOM MCP tool.

Каждый entry содержит:
- usage: сигнатура вызова
- preconditions: что сделать ДО (список шагов)
- postconditions: что будет ПОСЛЕ
- common_errors: список типичных ошибок с решением ({code, cause, fix})
- help: краткая справка (<=500 символов), «Тебе НУЖНО: 1) ... 2) ...»

The module is intentionally standalone: it imports nothing from ``mockup`` so the
MCP server, the plugin bridge and the check scripts can all import it as
``HELP_DATA`` when ``opencode/`` is on ``sys.path``.

Error codes are the real typed refusals of the server (``Denied``/``ToolInput``
in ``mockup/`` and the statuses of ``public_dto._HELP``); nothing here is invented.
"""
from __future__ import annotations

HELP_DATA: dict[str, dict] = {
    'dom_describe': {
        'usage': 'dom_describe()',
        'preconditions': [
            'Это первый вызов в новой сессии — других предусловий нет',
            'Не передавать аргументов: identity и профиль фиксируются при запуске сервера',
        ],
        'postconditions': [
            'Получен state (READY | NOT_STARTED | FOREIGN_BASE | CHAIN_BROKEN) и profile (lite | strict)',
            'Получен список tools с аргументами и примерами',
            'Получены branches, review_level, readiness, limits и next_steps',
            'Для каждого claim type показан валидный пример вызова',
        ],
        'common_errors': [
            {'code': 'INVALID_ARGUMENTS',
             'cause': 'передан неизвестный ключ или аргумент неверного типа',
             'fix': 'вызвать dom_describe({}) без аргументов'},
            {'code': 'STORAGE_BUSY',
             'cause': 'база занята другой транзакцией',
             'fix': 'повторить вызов через мгновение'},
            {'code': 'STORAGE_CORRUPT',
             'cause': 'файл базы повреждён или это не DOM-база',
             'fix': 'остановиться и сообщить оператору, базу не править'},
        ],
        'help': ('Тебе НУЖНО: 1) Вызвать dom_describe({}) первым делом в новой сессии. '
                 '2) Запомнить state, profile, branches и review_level. '
                 '3) Если state=NOT_STARTED — вызвать dom_start с запросом заказчика. '
                 '4) Если state=FOREIGN_BASE — решить, твоя ли это база (adopt). '
                 '5) Брать коды веток только из branches, не придумывать свои.'),
    },
    'dom_start': {
        'usage': 'dom_start(request, goal?, branches?, adopt?)',
        'preconditions': [
            'dom_describe показал state=NOT_STARTED (или FOREIGN_BASE, если нужен adopt)',
            'Текст запроса заказчика готов дословно (непустая строка)',
            'branches, если задаются, — коды [A-Za-z0-9_-]{1,32}, например ["main", "alt"]',
        ],
        'postconditions': [
            'Получен status STARTED | ALREADY_STARTED и work_ref',
            'Созданы ветки; main_branch — первая из branches',
            'Запрос сохранён неизменным корневым claim (request_claim_id)',
            'goal сохранён отдельным claim с goal_confirmed=false',
            'Получены next_steps с точным следующим вызовом',
        ],
        'common_errors': [
            {'code': 'REQUEST_REQUIRED',
             'cause': 'request отсутствует или пустой',
             'fix': 'передать запрос заказчика дословно: dom_start({"request": "<запрос>"})'},
            {'code': 'INVALID_FIELD',
             'cause': 'неверный goal, branches или adopt (тип/формат)',
             'fix': 'branches — массив кодов [A-Za-z0-9_-]{1,32}, goal — непустая строка, adopt — bool'},
            {'code': 'SCOPE_DENIED',
             'cause': 'база в каталоге привязана к другой работе, adopt не передан',
             'fix': 'если это твой проект — повторить с adopt=true, иначе сменить каталог'},
            {'code': 'ROOT_CONFLICT',
             'cause': 'в базе уже записан другой корневой request или goal',
             'fix': 'повторить с исходным request; goal уточнять через dom_update, не перезаписывать корень'},
            {'code': 'OMH_BINDING_UNVERIFIED',
             'cause': 'сервер запущен с --no-simulation, живой привязки нет',
             'fix': 'перезапустить сервер без --no-simulation или сообщить оператору'},
            {'code': 'CHAIN_BROKEN',
             'cause': 'цепочка событий базы не проходит проверку',
             'fix': 'остановить запись, вызвать dom_trace и сообщить пользователю'},
        ],
        'help': ('Тебе НУЖНО: 1) Передать request дословно — он станет неизменным корневым claim. '
                 '2) При необходимости добавить goal одной строкой и branches (по умолчанию ["main"]). '
                 '3) Помнить: повтор с тем же request безопасен, другой request даст ROOT_CONFLICT. '
                 '4) adopt=true — только если база в каталоге действительно твой проект. '
                 '5) Взять main_branch из ответа и писать claims в него.'),
    },
    'dom_claim': {
        'usage': 'dom_claim(branch?, type, text, plan_task?, basis?, refines?, purpose?, usage?, keywords?, requirement_refs?, ...)',
        'preconditions': [
            'Вызван dom_describe — известны ветки и профиль',
            'Выбран type: quote | decision | formula | code | data | concept | text',
            'Если задан plan_task — пункт плана существует (dom_plan_tree)',
            'Если задан basis — эти claim_id уже прочитаны (dom_find/dom_context)',
        ],
        'postconditions': [
            'Получен status CLAIM_ADMITTED | ALREADY_RECORDED (или RECORDED_CANDIDATE под review-политикой)',
            'Получены claim_id, logical_claim_id, version и revision — сохранить claim_id как basis для следующих',
            'Текст сохранён байт-в-байт; изменение — только через dom_update',
        ],
        'common_errors': [
            {'code': 'UNKNOWN_TYPE',
             'cause': 'type не из семи допустимых',
             'fix': 'использовать quote, decision, formula, code, data, concept или text'},
            {'code': 'MISSING_FIELD',
             'cause': 'не заполнено обязательное для типа поле (decision→rationale, quote→source_ref, formula→notation+units, code→repository_ref+relative_path, data→artifact_ref+units, concept→definition)',
             'fix': 'вызвать dom_describe и посмотреть пример для своего type, затем повторить'},
            {'code': 'EMPTY_TEXT',
             'cause': 'text пустой или состоит только из пробелов',
             'fix': 'передать непустой text дословно'},
            {'code': 'TEXT_TOO_LARGE',
             'cause': 'text больше 1 MiB UTF-8',
             'fix': 'разбить материал на несколько claims'},
            {'code': 'INVALID_FIELD',
             'cause': 'неизвестный аргумент, неверный тип, пустая строка в basis/usage/keywords или branch не объявлена',
             'fix': 'свериться с аргументами dom_describe и убрать лишние/пустые значения'},
            {'code': 'TASK_NOT_FOUND',
             'cause': 'plan_task не найден среди пунктов плана',
             'fix': 'вызвать dom_plan_tree или dom_plan_ingest и взять существующий id/путь/код'},
            {'code': 'TASK_ARCHIVED',
             'cause': 'пункт плана удалён и заархивирован',
             'fix': 'привязать claim к текущему пункту из dom_plan_tree'},
            {'code': 'GATE_NO_READ',
             'cause': 'claim из basis не прочитан этой сессией (read receipt отсутствует)',
             'fix': 'прочитать каждый claim basis через dom_find или dom_context, затем повторить'},
        ],
        'help': ('Тебе НУЖНО: 1) Взять branch из dom_describe (или plan_task — тогда branch вычислится сам). '
                 '2) Выбрать type и передать непустой text дословно. '
                 '3) Добавить обязательные для типа поля: decision→rationale, quote→source_ref, formula→notation+units, '
                 'code→repository_ref+relative_path, data→artifact_ref+units, concept→definition. '
                 '4) Сохранить claim_id: он нужен для dom_update, basis и dom_context.'),
    },
    'dom_find': {
        'usage': 'dom_find(query?, branch?, type?, requirement_ref?, limit?, plan_task?, keyword?, current_only?)',
        'preconditions': [
            'Вызван dom_describe — известны коды веток',
            'Для поиска по пункту плана известен plan_task (dom_plan_tree)',
        ],
        'postconditions': [
            'Получены matches (claim_id, branch, type, preview, is_current) и total',
            'При пустом результате total=0, matches=[] — это не ошибка',
            'truncated=true означает, что совпадений больше limit',
            'Найденные claim_id пригодны для basis, dom_update и dom_context',
        ],
        'common_errors': [
            {'code': 'INVALID_FIELD',
             'cause': 'limit вне 1..200, пустой keyword или неверный тип аргумента',
             'fix': 'держать limit в 1..200 (по умолчанию 20) и передавать строки непустыми'},
            {'code': 'UNKNOWN_TYPE',
             'cause': 'type не из семи допустимых',
             'fix': 'убрать type или использовать quote, decision, formula, code, data, concept, text'},
            {'code': 'UNKNOWN_BRANCH',
             'cause': 'branch не объявлена в этом проекте',
             'fix': 'вызвать dom_describe и взять код из branches'},
            {'code': 'TASK_NOT_FOUND',
             'cause': 'plan_task не найден',
             'fix': 'взять существующий пункт из dom_plan_tree'},
        ],
        'help': ('Тебе НУЖНО: 1) Передать query (подстрока без учёта регистра) или оставить пустым — '
                 'тогда вернутся последние claims. 2) Сузить поиск через branch/type/plan_task/keyword/current_only. '
                 '3) Держать limit в 1..200. 4) Взять claim_id из matches для basis, dom_update или dom_context. '
                 '5) Если total=0 — изменить query, а не повторять тот же вызов.'),
    },
    'dom_update': {
        'usage': 'dom_update(claim_id, text, reason, expected_current?, mode?, idempotency_key?, anatomy?)',
        'preconditions': [
            'claim_id найден через dom_find и claim прочитан (dom_context или dom_claim_versions)',
            'Подготовлен полный новый text (не diff) и reason изменения',
            'Для защиты от гонки известен claim_id прочитанной версии (expected_current)',
        ],
        'postconditions': [
            'Создана новая версия; получены new_claim_id и supersedes',
            'Старая версия осталась в истории с is_current=false',
            'При mode=fork получен CLAIM_FORKED, variant-ветка и conflict_ref',
            'При расхождении вернулся CONFLICT_READ_REQUIRED и ничего не записано',
        ],
        'common_errors': [
            {'code': 'NO_SUCH_CLAIM',
             'cause': 'claim_id не существует или вне твоего scope',
             'fix': 'найти id через dom_find'},
            {'code': 'NOT_CURRENT',
             'cause': 'обновляется уже заменённая или заархивированная версия',
             'fix': 'взять current claim_id из ответа/dom_claim_versions и обновить его'},
            {'code': 'CONFLICT_READ_REQUIRED',
             'cause': 'claim изменился после прочитанной версии (expected_current устарел)',
             'fix': 'прочитать current_claim_id через dom_claim_versions и повторить update или fork'},
            {'code': 'FORK_NOT_ALLOWED',
             'cause': 'mode=fork над собственной текущей версией',
             'fix': 'обновить свою версию mode=update или оставить как есть'},
            {'code': 'ROOT_IMMUTABLE',
             'cause': 'попытка заменить корневой request заказчика',
             'fix': 'добавить уточняющий claim с basis=[root claim_id]; goal можно уточнить отдельно'},
            {'code': 'NO_CHANGE',
             'cause': 'новый text и anatomy совпадают с текущими',
             'fix': 'изменить text или anatomy, либо не вызывать update'},
            {'code': 'INVALID_FIELD',
             'cause': 'неверный mode/expected_current/reason/anatomy',
             'fix': 'mode: auto|update|fork; reason непустой; anatomy: {basis?, refines?, purpose?, usage?, keywords?}'},
            {'code': 'GATE_NO_READ',
             'cause': 'обновляемый claim не прочитан этой сессией',
             'fix': 'прочитать его через dom_context и повторить'},
        ],
        'help': ('Тебе НУЖНО: 1) Найти claim_id через dom_find и прочитать текущую версию. '
                 '2) Передать полный новый text и непустой reason. '
                 '3) Передать expected_current=claim_id прочитанной версии — иначе при гонке придёт CONFLICT_READ_REQUIRED. '
                 '4) Если текущая версия чужая и не подходит — mode=fork. '
                 '5) Корневой request не обновляется (ROOT_IMMUTABLE).'),
    },
    'dom_annotate': {
        'usage': 'dom_annotate(claim_id, kind, text?, url?, locator?, name?, value?, conflict_ref?, reason?)',
        'preconditions': [
            'claim_id прочитан через dom_context или dom_find',
            'Выбран kind: comment | link | property | archive | conflict | resolve',
            'Для archive/conflict/resolve подготовлен reason',
        ],
        'postconditions': [
            'Получен ANNOTATED с event_id и revision (или RECORDED_CANDIDATE под review-политикой)',
            'Сам claim не изменён: событие добавлено в append-only историю',
            'Для property действует последнее записанное значение',
        ],
        'common_errors': [
            {'code': 'INVALID_FIELD',
             'cause': 'kind вне списка, лишний аргумент для kind или имя серверного поля в property (body/text/branch/...)',
             'fix': 'использовать допустимый kind и его аргументы; для property выбрать своё имя, например note_<что>' },
            {'code': 'MISSING_FIELD',
             'cause': 'нет обязательного аргумента kind или reason для archive/conflict/resolve',
             'fix': 'comment→text, link→url+locator, property→name+value, conflict/resolve→conflict_ref; archive/conflict/resolve→reason'},
            {'code': 'NO_SUCH_CLAIM',
             'cause': 'claim_id не существует или вне scope',
             'fix': 'найти claim через dom_find'},
            {'code': 'NOT_CURRENT',
             'cause': 'kind=archive для уже заархивированного claim',
             'fix': 'проверить состояние через dom_context; архив повторно не ставится'},
            {'code': 'GATE_NO_READ',
             'cause': 'claim не прочитан этой сессией',
             'fix': 'прочитать его через dom_context и повторить'},
        ],
        'help': ('Тебе НУЖНО: 1) Прочитать claim через dom_context/dom_find и взять claim_id. '
                 '2) Выбрать kind: comment (text), link (url+locator), property (name+value), archive, '
                 'conflict/resolve (conflict_ref); для archive, conflict и resolve обязателен reason. '
                 '3) Не использовать серверные имена (body, text, branch) как property. '
                 '4) Помнить: claim не меняется, событие добавляется в историю.'),
    },
    'dom_context': {
        'usage': 'dom_context(claim_id, depth?)',
        'preconditions': [
            'Известен claim_id любой версии (dom_find/dom_claim)',
            'Вызван dom_describe, если проект ещё не открыт',
        ],
        'postconditions': [
            'Получен claim с anatomy и properties',
            'Получены refines_chain, basis, refinements и plan с предками',
            'Возвращены root_request_claim_id и resolution текущей версии',
        ],
        'common_errors': [
            {'code': 'NO_SUCH_CLAIM',
             'cause': 'claim_id не существует или вне scope',
             'fix': 'найти id через dom_find'},
            {'code': 'INVALID_FIELD',
             'cause': 'depth вне 1..32 или неверный тип',
             'fix': 'передать depth целым числом 1..32 (по умолчанию 8)'},
            {'code': 'INVALID_ARGUMENTS',
             'cause': 'неизвестный ключ или claim_id не строка',
             'fix': 'вызывать dom_context({"claim_id": "<id>", "depth": 8})'},
        ],
        'help': ('Тебе НУЖНО: 1) Передать claim_id любой версии (из dom_find/dom_claim). '
                 '2) При необходимости увеличить depth (1..32), чтобы увидеть более длинную цепочку refines. '
                 '3) Прочитать refines_chain, basis и plan — откуда claim произошёл. '
                 '4) Использовать этот вызов перед dom_update/dom_annotate, чтобы получить актуальную версию.'),
    },
    'dom_plan_status': {
        'usage': 'dom_plan_status(check_every?)',
        'preconditions': [
            'Проект открыт (dom_start) — иначе PLAN_STATUS нечего показывать',
            'При первом обращении вызвать dom_describe',
        ],
        'postconditions': [
            'Получены has_plan, project_closed, items и open_items',
            'Получены plan_check_due, iterations_since_change и request (plan_request | plan_check)',
            'Получены conflicts и next_steps',
            'Очередь inbox обработана: inbox_processed, inbox_queued, inbox_failed',
        ],
        'common_errors': [
            {'code': 'INVALID_FIELD',
             'cause': 'check_every вне 1..10000 или не int',
             'fix': 'передать целое check_every (по умолчанию 10)'},
            {'code': 'PROFILE_STRICT',
             'cause': 'вызов в strict-профиле',
             'fix': 'работать через strict-инструменты или перезапустить сервер в lite'},
            {'code': 'NOT_INITIALIZED',
             'cause': 'база в каталоге ещё не создана',
             'fix': 'вызвать dom_start с запросом заказчика'},
            {'code': 'CHAIN_BROKEN',
             'cause': 'цепочка событий не проходит проверку',
             'fix': 'остановить запись и сообщить пользователю; dom_trace покажет разрыв'},
        ],
        'help': ('Тебе НУЖНО: 1) Вызвать без аргументов (check_every по умолчанию 10). '
                 '2) Посмотреть has_plan, open_items, plan_check_due, conflicts и request. '
                 '3) Если has_plan=false или request.kind=plan_request — отправить план через dom_plan_ingest. '
                 '4) Если plan_check_due=true — проверить, изменился ли план, и переслать его. '
                 '5) Если conflicts непусты — прочитать dom_plan_tree и свести изменения.'),
    },
    'dom_plan_ingest': {
        'usage': 'dom_plan_ingest(plan, source?, format?, contexts?, source_revision?, mode?, base_revision?, force?)',
        'preconditions': [
            'Вызван dom_plan_status и взят base_revision (source_revision)',
            'План подготовлен нумерованными пунктами (1., 1.1) или JSON [{"title", "children"}]',
            'Для важных пунктов собран контекст из разговора (contexts)',
        ],
        'postconditions': [
            'Получен PLAN_INGESTED | PLAN_UNCHANGED и новый source_revision',
            'Пункты получили ветки (П1=AA, П1.1=AA100) и header claims',
            'Удалённые из full-плана пункты заархивированы, их claims сохранены',
            'При PLAN_CONFLICT ничего не записано; возвращены base/current revision и item_paths',
        ],
        'common_errors': [
            {'code': 'PLAN_CONFLICT',
             'cause': 'план изменился после твоего base_revision',
             'fix': 'прочитать dom_plan_tree, слить изменения и повторить с base_revision=current_revision; force=true — осознанно'},
            {'code': 'PLAN_EMPTY',
             'cause': 'в plan не найдено ни одного пункта',
             'fix': 'передать нумерованные пункты (1., 1.1), bullets/заголовки или JSON [{"title": ...}]'},
            {'code': 'INVALID_FIELD',
             'cause': 'неверный plan/format/source/contexts/mode/base_revision/force',
             'fix': 'format: auto|markdown|json; mode: full|partial; contexts: {путь: непустой текст}'},
            {'code': 'PROFILE_STRICT',
             'cause': 'вызов в strict-профиле',
             'fix': 'использовать lite-профиль продукта'},
            {'code': 'NOT_INITIALIZED',
             'cause': 'база не создана',
             'fix': 'сначала dom_start с запросом заказчика'},
        ],
        'help': ('Тебе НУЖНО: 1) Взять source_revision из dom_plan_status — это base_revision. '
                 '2) Передать plan нумерованными пунктами (1., 1.1) или JSON [{"title","context","children"}]. '
                 '3) Добавить contexts: путь пункта → контекст из разговора. '
                 '4) При PLAN_CONFLICT прочитать dom_plan_tree, слить изменения и повторить с base_revision=current_revision. '
                 '5) force=true — только осознанно, когда перезапись чужих изменений действительно нужна.'),
    },
    'dom_plan_tree': {
        'usage': 'dom_plan_tree(root, depth?, at_version?, require_fresh?)',
        'preconditions': [
            'План загружен (dom_plan_status → has_plan=true) или известен корневой пункт',
            'Известен root: id пункта, путь (П1.2) или код ветки (AA100)',
        ],
        'postconditions': [
            'Получено дерево tasks с path, branch, claim_id, status и claim_count',
            'Получены source, synced_at, stale и source_revision',
            'plan_task_id и branch пригодны для dom_claim(plan_task=...) и dom_task_claims',
        ],
        'common_errors': [
            {'code': 'PLAN_ROOT_NOT_FOUND',
             'cause': 'root не найден в проекции плана',
             'fix': 'взять id/путь из dom_plan_status → dom_plan_tree или переслать план'},
            {'code': 'PLAN_PROJECTION_STALE',
             'cause': 'require_fresh=true, а проекция устарела',
             'fix': 'переслать план через dom_plan_ingest и повторить'},
            {'code': 'PLAN_CYCLE',
             'cause': 'в иерархии плана обнаружен цикл',
             'fix': 'остановить обход и проверить структуру плана'},
            {'code': 'INVALID_FIELD',
             'cause': 'root пустой, depth вне 1..10, at_version < 0 или require_fresh не bool',
             'fix': 'depth 1..10 (по умолчанию 3), at_version >= 0, require_fresh — bool'},
        ],
        'help': ('Тебе НУЖНО: 1) Передать root — id пункта (П1), путь (П1.2) или код ветки (AA100). '
                 '2) Задать depth 1..10 при необходимости. '
                 '3) require_fresh=true, если нельзя отвечать по устаревшей проекции. '
                 '4) Взять plan_task_id и branch из tasks для dom_claim(plan_task=...) и dom_task_claims. '
                 '5) Если stale=true — переслать план через dom_plan_ingest.'),
    },
    'dom_task_claims': {
        'usage': 'dom_task_claims(task, include_children?, link_role?, include_history?)',
        'preconditions': [
            'План загружен и пункт существует (dom_plan_tree)',
            'Известен task — id, путь (П1.2) или код ветки (AA200)',
        ],
        'postconditions': [
            'Получены claims_by_role (defines/justifies/implements/validates/documents/depends_on)',
            'Получены orphaned_links и summary',
            'link_stale помечает связи, сделанные до изменения смысла пункта',
        ],
        'common_errors': [
            {'code': 'TASK_NOT_FOUND',
             'cause': 'пункт плана не найден по id/пути/коду',
             'fix': 'вызвать dom_plan_tree и взять существующий пункт'},
            {'code': 'INVALID_FIELD',
             'cause': 'link_role вне списка или include_children/include_history не bool',
             'fix': 'link_role: all или одна из ролей; флаги — bool'},
            {'code': 'PROFILE_STRICT',
             'cause': 'вызов в strict-профиле',
             'fix': 'использовать lite-профиль продукта'},
        ],
        'help': ('Тебе НУЖНО: 1) Передать task — id, путь (П1.2) или код ветки. '
                 '2) include_children=true, если нужны подпункты. '
                 '3) link_role отфильтрует роли (defines/justifies/implements/validates/documents/depends_on). '
                 '4) include_history=true покажет заменённые версии. '
                 '5) Проверить link_stale и orphaned_links, прежде чем опираться на связи.'),
    },
    'dom_task_graph': {
        'usage': 'dom_task_graph(task, agent?, include_history?, include_children?, limit?)',
        'preconditions': [
            'План загружен и пункт существует (dom_plan_tree)',
            'Известен task — id, путь или код ветки',
        ],
        'postconditions': [
            'Получены chains с версиями и рёбрами supersedes/basis/refines/variant_of/task_link',
            'Получены agents[{author, root, claim_count}] и truncated',
        ],
        'common_errors': [
            {'code': 'TASK_NOT_FOUND',
             'cause': 'пункт плана не найден',
             'fix': 'взять существующий пункт из dom_plan_tree'},
            {'code': 'INVALID_FIELD',
             'cause': 'agent пустой, limit вне 1..1000 или флаг не bool',
             'fix': 'agent — непустая строка; limit 1..1000 (по умолчанию 200)'},
            {'code': 'PROFILE_STRICT',
             'cause': 'вызов в strict-профиле',
             'fix': 'использовать lite-профиль продукта'},
        ],
        'help': ('Тебе НУЖНО: 1) Передать task (id, путь или код ветки). '
                 '2) При необходимости agent — только цепи этого автора. '
                 '3) limit 1..1000; include_history/include_children — по задаче. '
                 '4) Читать chains[].edges (supersedes/basis/refines/variant_of/task_link), '
                 'чтобы понять, кто что внёс и на чём это стоит.'),
    },
    'dom_reconcile': {
        'usage': 'dom_reconcile()',
        'preconditions': [
            'Ветки-варианты уже сведены по содержанию (например, после mode=fork в dom_update)',
            'Проект открыт и база не повреждена',
        ],
        'postconditions': [
            'Получен RECONCILED и merged[{branch, claim_id, merged_into}]',
            'Совпавшие variant-claims заархивированы как merged_into базового',
            'Пустые variant-ветки получили converge-событие',
        ],
        'common_errors': [
            {'code': 'PROFILE_STRICT',
             'cause': 'вызов в strict-профиле',
             'fix': 'использовать lite-профиль продукта'},
            {'code': 'NOT_INITIALIZED',
             'cause': 'база не создана',
             'fix': 'сначала dom_start'},
            {'code': 'SCOPE_DENIED',
             'cause': 'база привязана к другой работе',
             'fix': 'проверить dom_describe; adopt только для своего проекта'},
            {'code': 'CHAIN_BROKEN',
             'cause': 'цепочка событий не проходит проверку',
             'fix': 'остановить запись и сообщить оператору'},
        ],
        'help': ('Тебе НУЖНО: 1) Вызвать после сведения расходящихся версий (например, после fork из dom_update). '
                 '2) Ничего не передавать. '
                 '3) Прочитать merged[]: какие variant-ветки слиты в базовый claim. '
                 '4) Повторить, когда обе версии станут равны по всем свойствам; '
                 'сначала убедиться, что тексты действительно совпали.'),
    },
    'dom_extract': {
        'usage': 'dom_extract(task, text, provider?, link_role?)',
        'preconditions': [
            'План загружен, пункт существует (dom_plan_tree)',
            'Материал для разбора готов (непустой, до 200 kB)',
            'При provider=client — готовность ответить через dom_extract_submit',
        ],
        'postconditions': [
            'Получен EXTRACTED с candidates[{candidate_id, intent_digest, type, preview}]',
            'Кандидаты только submitted — ничего не принято',
            'Получены already_recorded, refused, dropped и next_steps',
            'При provider=client получен EXTRACTION_REQUEST со схемой и инструкциями',
        ],
        'common_errors': [
            {'code': 'TASK_NOT_FOUND',
             'cause': 'task не найден среди пунктов плана',
             'fix': 'взять существующий пункт из dom_plan_tree'},
            {'code': 'TEXT_TOO_LARGE',
             'cause': 'материал больше лимита извлечения',
             'fix': 'разбить материал на части и вызвать dom_extract для каждой'},
            {'code': 'INVALID_FIELD',
             'cause': 'provider вне deterministic|anthropic|command|client или пустой текст',
             'fix': 'убрать provider (возьмётся из конфигурации) или выбрать допустимый'},
            {'code': 'PROFILE_STRICT',
             'cause': 'вызов в strict-профиле',
             'fix': 'использовать lite-профиль продукта'},
        ],
        'help': ('Тебе НУЖНО: 1) Убедиться, что план загружен (dom_plan_status) и пункт существует. '
                 '2) Передать task и text (материал, до 200 kB). '
                 '3) При необходимости provider=deterministic|anthropic|command|client; client вернёт EXTRACTION_REQUEST для dom_extract_submit. '
                 '4) Разобрать candidates: они только submitted, затем dom_candidate → dom_accept.'),
    },
    'dom_extract_submit': {
        'usage': 'dom_extract_submit(task, text, candidates, model?, link_role?)',
        'preconditions': [
            'Получен EXTRACTION_REQUEST от dom_extract(provider="client")',
            'Подготовлен непустой массив candidates по candidate_schema',
            'Тексты кандидатов скопированы из материала дословно',
        ],
        'postconditions': [
            'Получен EXTRACTED, provider="client" и candidates[]',
            'Кандидаты, чей текст не найден в материале, попали в dropped',
            'Типизированный кандидат без обязательных фактов стал text с suggested:<type>',
            'Далее — dom_candidate и dom_accept',
        ],
        'common_errors': [
            {'code': 'TASK_NOT_FOUND',
             'cause': 'task не найден среди пунктов плана',
             'fix': 'взять существующий пункт из dom_plan_tree'},
            {'code': 'INVALID_FIELD',
             'cause': 'candidates не массив/пустой, model пустой или кандидат не по схеме',
             'fix': 'передать [{type, text, fields?, units?, notation?, purpose?, keywords?}]'},
            {'code': 'TEXT_TOO_LARGE',
             'cause': 'материал больше допустимого',
             'fix': 'разбить материал и повторить'},
            {'code': 'EMPTY_TEXT',
             'cause': 'text пустой',
             'fix': 'передать тот же непустой материал, что и в dom_extract'},
        ],
        'help': ('Тебе НУЖНО: 1) Взять task и text из EXTRACTION_REQUEST (тот же материал). '
                 '2) Передать candidates — непустой массив {type, text, fields?, units?, notation?, purpose?, keywords?}. '
                 '3) Тексты копировать дословно из материала, иначе они попадут в dropped. '
                 '4) Затем прочитать dom_candidate и решить dom_accept.'),
    },
    'dom_accept': {
        'usage': 'dom_accept(candidate_id, decision?, note?)',
        'preconditions': [
            'Кандидат прочитан через dom_candidate и state=AWAITING_REVIEW',
            'Для reject подготовлен note с причиной',
        ],
        'postconditions': [
            'approve → DEMO_ADMITTED с tx_id, revision, claim_ids (возможен rebased=true)',
            'reject → REVIEW_REJECTED с review_id',
            'Кандидат уходит из pending-списка dom_resume',
        ],
        'common_errors': [
            {'code': 'NO_SUCH_CANDIDATE',
             'cause': 'candidate_id не существует или вне scope',
             'fix': 'взять id из dom_extract или pending в dom_resume'},
            {'code': 'CANDIDATE_CLOSED',
             'cause': 'кандидат уже admitted/rejected/superseded',
             'fix': 'прочитать состояние через dom_candidate и не решать повторно'},
            {'code': 'INVALID_FIELD',
             'cause': 'decision не approve/reject или reject без note',
             'fix': 'decision: approve|reject; для reject передать непустой note'},
            {'code': 'REVISION_CONFLICT',
             'cause': 'ветка ушла вперёд, а политика independent запрещает rebase',
             'fix': 'отклонить кандидата с note и пересдать материал автором'},
            {'code': 'INDEPENDENCE_NOT_ENFORCED',
             'cause': 'нет capability review или отключена симуляция',
             'fix': 'запустить сервер с нужной ролью/политикой; см. dom_describe.review_level'},
        ],
        'help': ('Тебе НУЖНО: 1) Прочитать кандидата через dom_candidate. '
                 '2) Вызвать dom_accept(candidate_id) для approve — review+admit одним вызовом, '
                 'или decision=reject с note. '
                 '3) Не принимать уже закрытого кандидата. '
                 '4) При REVISION_CONFLICT под политикой independent автор должен пересдать кандидата.'),
    },
    'dom_distillate': {
        'usage': 'dom_distillate(action, source?, distillate?, text?, anchors?, by?, note?)',
        'preconditions': [
            'Известен source (id цитаты) или distillate (id версии) из строки statement',
            'Для propose подготовлен text (1-3 предложения) и anchors по одному на предложение',
            'Для withdraw/dispute/resolve подготовлен note',
        ],
        'postconditions': [
            'show → DISTILLATE_SHOWN: цепочка версий, effective, open_conflicts, next_steps',
            'propose → PROPOSED или ALREADY_RECORDED',
            'validate → VALIDATED или REVIEW_FAILED',
            'accept → ACCEPTED; withdraw/dispute/resolve → WITHDRAWN/DISPUTED/RESOLVED',
        ],
        'common_errors': [
            {'code': 'STATEMENT_NOT_FOUND',
             'cause': 'id не является цитатой источника или версией дистиллята',
             'fix': 'взять source/distillate из строки [DOM project memory] или вызвать action=show'},
            {'code': 'SPAN_NOT_FOUND',
             'cause': 'anchor не является дословной подстрокой источника',
             'fix': 'скопировать предложение источника точно, без пересказа'},
            {'code': 'SPAN_AMBIGUOUS',
             'cause': 'anchor встречается в источнике несколько раз',
             'fix': 'взять более длинную уникальную цитату'},
            {'code': 'PROPOSAL_INVALID',
             'cause': 'нет text или anchors не по одному массиву на предложение',
             'fix': 'передать text и anchors: [[цитата], [цитата]] по предложениям'},
            {'code': 'REVIEW_REQUIRED',
             'cause': 'accept без успешного validate этой версии',
             'fix': 'сначала dom_distillate(action="validate", source=<id>)'},
            {'code': 'DISTILLATE_DISPUTED',
             'cause': 'открытый спор блокирует действие',
             'fix': 'закрыть спор через action=resolve с note'},
        ],
        'help': ('Тебе НУЖНО: 1) Взять source (id цитаты) или distillate (id версии) из строки statement. '
                 '2) action=show — прочитать цепочку и next_steps. '
                 '3) Для улучшения: propose с text (1-3 предложения) и anchors (дословные цитаты по одной на предложение), '
                 'затем validate, затем accept. '
                 '4) withdraw/dispute/resolve требуют note. '
                 '5) После трёх неудачных review цепочка останавливается (DISTILLATE_LOOP).'),
    },
    'dom_skill_select': {
        'usage': 'dom_skill_select(step, queue?, plan_version?, index_digest?)',
        'preconditions': [
            'Сформирован micro-step: объект с title и, желательно, context, cap[], markers, category_hint',
            'Опционально подготовлена очередь следующих 0-3 шагов для prefetch',
        ],
        'postconditions': [
            'Получен block (<=800 символов) для инъекции в промпт',
            'Получены queue, advisory, gaps, prefetch, missing_caps, confidence_bp',
            'Одинаковый step и индекс всегда дают одинаковый результат (детерминированно)',
        ],
        'common_errors': [
            {'code': 'INVALID_ARGUMENTS',
             'cause': 'step не объект, неизвестный ключ или queue не массив',
             'fix': 'вызывать dom_skill_select({"step": {"title": "...", "context": "...", "cap": [...]}}) '},
        ],
        'help': ('Тебе НУЖНО: 1) Передать step — объект с title и, желательно, context, cap[], markers, category_hint. '
                 '2) При необходимости queue — до 3 следующих шагов для prefetch. '
                 '3) Вставить block (<=800 символов) в промпт. '
                 '4) Учесть gaps/missing_caps: если нужного скила нет, не выдумывать его. '
                 '5) Помнить: вызов read-only, ledger не трогает.'),
    },
    'dom_init': {
        'usage': 'dom_init(external_work_ref, policy_digest)',
        'preconditions': [
            'Сервер запущен в strict/lab-профиле (в lite вместо него dom_start)',
            'external_work_ref и policy_digest взяты из launch-конфигурации без изменений',
        ],
        'postconditions': [
            'Получен INITIALIZED и work_ref',
            'Привязка work проверена или создана; повторный вызов безопасен',
            'Старая схема базы мигрирована аддитивно',
        ],
        'common_errors': [
            {'code': 'PROFILE_LITE',
             'cause': 'вызов в lite-профиле',
             'fix': 'использовать dom_start с запросом заказчика'},
            {'code': 'POLICY_MISMATCH',
             'cause': 'policy_digest не совпадает с политикой сервера',
             'fix': 'взять policy_digest из launch-конфигурации; не вычислять самостоятельно'},
            {'code': 'OMH_BINDING_UNVERIFIED',
             'cause': 'сервер запущен с --no-simulation',
             'fix': 'перезапустить без --no-simulation'},
            {'code': 'REJECTED',
             'cause': 'request не объект или содержит лишние/недостающие ключи',
             'fix': 'передать ровно {"external_work_ref": "...", "policy_digest": "..."}'},
            {'code': 'SCOPE_DENIED',
             'cause': 'work_ref вне grant этого запуска',
             'fix': 'использовать work_ref из launch-конфигурации'},
        ],
        'help': ('Тебе НУЖНО: 1) Использовать только в strict/lab-профиле (в lite вместо него dom_start). '
                 '2) Передать external_work_ref и policy_digest ровно из launch-конфигурации. '
                 '3) Не пытаться вычислить policy_digest. '
                 '4) Один вызов создаёт/проверяет привязку; повтор с теми же аргументами безопасен.'),
    },
    'dom_resume': {
        'usage': 'dom_resume(branch, limit?, after?)',
        'preconditions': [
            'Вызван dom_describe — известны коды веток',
            'Для следующей страницы есть after="branch:id" из предыдущего ответа',
        ],
        'postconditions': [
            'Получен RESUMED со snapshot, trace, sync и pending',
            'pending.count точен, pending.items — только метаданные страницы',
            'Получен readiness (admitted_total, admitted_independent, pending, rejected, stale)',
        ],
        'common_errors': [
            {'code': 'UNKNOWN_BRANCH',
             'cause': 'branch не объявлена в проекте',
             'fix': 'взять код из dom_describe.branches'},
            {'code': 'NO_SUCH_BRANCH',
             'cause': 'ветка объявлена, но головы в базе нет',
             'fix': 'вызвать dom_describe и проверить state; при необходимости dom_start'},
            {'code': 'REJECTED',
             'cause': 'limit вне 1..500 или after не формата "branch:id"',
             'fix': 'limit 1..500 (по умолчанию 50); after — cursor из предыдущей страницы'},
            {'code': 'NOT_INITIALIZED',
             'cause': 'база не создана',
             'fix': 'сначала dom_start'},
            {'code': 'CHAIN_BROKEN',
             'cause': 'цепочка событий не проходит проверку',
             'fix': 'остановить запись и сообщить пользователю'},
        ],
        'help': ('Тебе НУЖНО: 1) Передать branch из dom_describe. '
                 '2) При необходимости limit 1..500 и after="branch:id" из предыдущей страницы. '
                 '3) Прочитать snapshot, trace, sync, pending и readiness — это полная ориентация после паузы или REVISION_CONFLICT. '
                 '4) Не ждать здесь текстов кандидатов: pending — только метаданные.'),
    },
    'dom_submit': {
        'usage': 'dom_submit(payload_json)',
        'preconditions': [
            'Собран restricted-JSON пакет (operation claims|append, branch, items с body_b64, policy_digest, dependencies, expected_branch_revision, origin_version)',
            'Пакет сериализован в строку payload_json',
            'У launch есть capability submit',
        ],
        'postconditions': [
            'Получен RECORDED_CANDIDATE с candidate_id и intent_digest — кандидат ждёт review',
            'Ничего не принято: дальше dom_candidate → dom_review → dom_admit',
            'При повторе с тем же idempotency_key вернётся тот же результат',
        ],
        'common_errors': [
            {'code': 'REJECTED',
             'cause': 'payload_json не restricted JSON или не проходит схему',
             'fix': 'прочитать errors[] (path/code/fix) и исправить поля; без приведения типов'},
            {'code': 'NOT_INITIALIZED',
             'cause': 'база не создана',
             'fix': 'сначала dom_start (lite) или dom_init (strict)'},
            {'code': 'SCOPE_DENIED',
             'cause': 'branch/work вне grant этого запуска',
             'fix': 'использовать только свои branch и external_work_ref'},
            {'code': 'INDEPENDENCE_NOT_ENFORCED',
             'cause': 'нет capability submit или отключена симуляция',
             'fix': 'запустить сервер с ролью, у которой есть submit'},
            {'code': 'IDEMPOTENCY_MISMATCH',
             'cause': 'idempotency_key уже использован для другого содержимого',
             'fix': 'для нового содержимого взять свежий ключ'},
            {'code': 'CHAIN_BROKEN',
             'cause': 'цепочка событий не проходит проверку',
             'fix': 'остановить запись и сообщить оператору'},
        ],
        'help': ('Тебе НУЖНО: 1) Собрать restricted-JSON пакет (operation claims|append, branch, items с body_b64, '
                 'policy_digest, dependencies, expected_branch_revision). '
                 '2) Передать его строкой в payload_json. '
                 '3) При REJECTED прочитать errors[] и исправить поля. '
                 '4) Затем dom_candidate → dom_review → dom_admit: submit сам ничего не принимает. '
                 '5) Для повтора использовать тот же idempotency_key.'),
    },
    'dom_review': {
        'usage': 'dom_review(candidate_id, intent_digest, decision?, note?)',
        'preconditions': [
            'Кандидат прочитан через dom_candidate',
            'intent_digest скопирован из ответа dom_candidate/dom_submit точно',
            'Для decision=reject подготовлен note',
        ],
        'postconditions': [
            'approve → DEMO_REVIEWED с review_id (нужен для dom_admit)',
            'reject → REVIEW_REJECTED с review_id; кандидат нельзя адмитить',
            'Review записан с reviewer_session этой сессии',
        ],
        'common_errors': [
            {'code': 'DIGEST_MISMATCH',
             'cause': 'intent_digest не совпадает с сохранённым кандидатом',
             'fix': 'скопировать intent_digest без изменений из dom_candidate/dom_submit'},
            {'code': 'NO_SUCH_CANDIDATE',
             'cause': 'candidate_id не найден или вне scope',
             'fix': 'взять id из dom_resume.pending или dom_extract'},
            {'code': 'INVALID_FIELD',
             'cause': 'decision не approve/reject, reject без note или note длиннее 4000',
             'fix': 'decision: approve|reject; для reject — непустой note до 4000 символов'},
            {'code': 'INDEPENDENCE_NOT_ENFORCED',
             'cause': 'нет capability review или под independent ревьюится свой кандидат',
             'fix': 'ревьюить должен другой principal; см. dom_describe.review_level'},
        ],
        'help': ('Тебе НУЖНО: 1) Прочитать кандидата через dom_candidate и взять intent_digest. '
                 '2) Вызвать с decision=approve (по умолчанию) или reject — reject требует note. '
                 '3) Передать review_id из ответа в dom_admit. '
                 '4) Не ревьюить свой же кандидат под политикой independent. '
                 '5) При DIGEST_MISMATCH скопировать digest точно из ответа.'),
    },
    'dom_candidate': {
        'usage': 'dom_candidate(candidate_id)',
        'preconditions': [
            'Известен candidate_id из dom_submit, dom_extract или pending в dom_resume',
            'У launch есть capability review',
        ],
        'postconditions': [
            'Получены texts (до 4000 символов), metadata, anatomy, branch и intent_digest',
            'Получен state: AWAITING_REVIEW | REVIEWED | REJECTED | SUPERSEDED | ADMITTED',
            'Вызов read-only: ledger не меняется',
        ],
        'common_errors': [
            {'code': 'NO_SUCH_CANDIDATE',
             'cause': 'candidate_id не найден или вне scope',
             'fix': 'взять id из dom_resume.pending или dom_extract'},
            {'code': 'INVALID_FIELD',
             'cause': 'candidate_id пустой или не строка',
             'fix': 'передать непустой candidate_id строкой'},
            {'code': 'INDEPENDENCE_NOT_ENFORCED',
             'cause': 'нет capability review или отключена симуляция',
             'fix': 'запустить сервер с ролью reviewer'},
        ],
        'help': ('Тебе НУЖНО: 1) Взять candidate_id из dom_submit/dom_extract/dom_resume pending. '
                 '2) Передать его в dom_candidate и прочитать texts, metadata, anatomy и state. '
                 '3) Убедиться, что state=AWAITING_REVIEW, прежде чем решать. '
                 '4) Использовать intent_digest из ответа для dom_review/dom_admit.'),
    },
    'dom_admit': {
        'usage': 'dom_admit(candidate_id, review_id, idempotency_key, intent_digest)',
        'preconditions': [
            'Получен review_id одобряющего review (dom_review)',
            'candidate_id и intent_digest взяты без изменений',
            'Придуман стабильный idempotency_key для безопасного повтора',
        ],
        'postconditions': [
            'Получен DEMO_ADMITTED: tx_id, branch, revision, claim_ids',
            'Claims, события, head ветки и outbox записаны одной транзакцией',
            'Повтор с тем же idempotency_key вернёт тот же результат',
        ],
        'common_errors': [
            {'code': 'DIGEST_MISMATCH',
             'cause': 'intent_digest не совпадает с кандидатом',
             'fix': 'скопировать digest точно из dom_candidate'},
            {'code': 'STALE_REVIEW',
             'cause': 'review не соответствует кандидату, политике или головам веток',
             'fix': 'перечитать dom_resume и выполнить review заново'},
            {'code': 'REVIEW_REJECTED',
             'cause': 'последний review отклонил кандидата',
             'fix': 'не адмитить; записать исправленный claim и провести review заново'},
            {'code': 'REVIEWER_SESSION_REQUIRED',
             'cause': 'у review нет reviewer_session (NULL)',
             'fix': 'выполнить review из доверенной сессии и адмитить его review_id'},
            {'code': 'REVISION_CONFLICT',
             'cause': 'голова ветки сдвинулась после сборки кандидата',
             'fix': 'перечитать dom_resume и повторить submit/review/admit'},
            {'code': 'INDEPENDENCE_NOT_ENFORCED',
             'cause': 'нет capability admit или ревьюер не совпадает с сессией адмита',
             'fix': 'адмитить должен тот же principal, что одобрил review'},
        ],
        'help': ('Тебе НУЖНО: 1) Получить review_id одобряющего review (dom_review). '
                 '2) Взять candidate_id и intent_digest без изменений. '
                 '3) Придумать стабильный idempotency_key — повтор с ним безопасен. '
                 '4) При STALE_REVIEW перечитать dom_resume и повторить review. '
                 '5) Не адмитить отклонённого кандидата.'),
    },
    'dom_append': {
        'usage': 'dom_append(payload_json)',
        'preconditions': [
            'Собран restricted-JSON пакет operation="append" с событиями (comment/link/property/applicability/archive)',
            'Известны claim_ref, branch и expected_branch_revision',
        ],
        'postconditions': [
            'Получен RECORDED_CANDIDATE с candidate_id и intent_digest',
            'События ещё не приняты: нужны dom_review и dom_admit',
            'Для одиночной аннотации проще dom_annotate',
        ],
        'common_errors': [
            {'code': 'REJECTED',
             'cause': 'payload_json не JSON, не объект или operation != append',
             'fix': 'передать restricted-JSON объект с operation="append"'},
            {'code': 'SCOPE_DENIED',
             'cause': 'claim/branch вне grant',
             'fix': 'использовать только свои branch и claim_ref'},
            {'code': 'INDEPENDENCE_NOT_ENFORCED',
             'cause': 'нет capability submit или отключена симуляция',
             'fix': 'запустить сервер с ролью, у которой есть submit'},
            {'code': 'NOT_INITIALIZED',
             'cause': 'база не создана',
             'fix': 'сначала dom_start'},
            {'code': 'CHAIN_BROKEN',
             'cause': 'цепочка событий не проходит проверку',
             'fix': 'остановить запись и сообщить оператору'},
        ],
        'help': ('Тебе НУЖНО: 1) Собрать пакет operation="append" с событиями (comment/link/property/applicability/archive) '
                 'в payload_json. 2) Помнить: это кандидат, а не обход review. '
                 '3) Пройти dom_candidate → dom_review → dom_admit. '
                 '4) Для одиночной аннотации использовать dom_annotate.'),
    },
    'dom_query': {
        'usage': 'dom_query(branch, revision?)',
        'preconditions': [
            'Вызван dom_describe — известны коды веток',
            'Для исторического среза известен revision в диапазоне 0..head',
        ],
        'postconditions': [
            'Получен SNAPSHOT с claims (включая archived), history и conflicts',
            'revision указывает, какой срез прочитан (по умолчанию голова)',
            'Вызов read-only',
        ],
        'common_errors': [
            {'code': 'UNKNOWN_BRANCH',
             'cause': 'branch не объявлена',
             'fix': 'взять код из dom_describe.branches'},
            {'code': 'NO_SUCH_BRANCH',
             'cause': 'у объявленной ветки нет головы в базе',
             'fix': 'проверить state через dom_describe; при необходимости dom_start'},
            {'code': 'REVISION_OUT_OF_RANGE',
             'cause': 'revision вне 0..head этой ветки',
             'fix': 'взять допустимый диапазон из hint или revision из dom_resume'},
            {'code': 'NOT_INITIALIZED',
             'cause': 'база не создана',
             'fix': 'сначала dom_start'},
        ],
        'help': ('Тебе НУЖНО: 1) Передать branch из dom_describe. '
                 '2) Не указывать revision для головы или задать 0..head для исторического среза. '
                 '3) Прочитать claims и history. '
                 '4) Для полной ориентации (trace/sync/pending) использовать dom_resume.'),
    },
    'dom_snapshot': {
        'usage': 'dom_snapshot(branch, revision?)',
        'preconditions': [
            'Вызван dom_describe — известны коды веток',
            'Помнить, что dom_snapshot — совместимый синоним dom_query',
        ],
        'postconditions': [
            'Получен SNAPSHOT с той же схемой, что у dom_query',
            'Прочитаны claims, history и conflicts на выбранном revision',
        ],
        'common_errors': [
            {'code': 'UNKNOWN_BRANCH',
             'cause': 'branch не объявлена',
             'fix': 'взять код из dom_describe.branches'},
            {'code': 'NO_SUCH_BRANCH',
             'cause': 'у ветки нет головы в базе',
             'fix': 'проверить state через dom_describe'},
            {'code': 'REVISION_OUT_OF_RANGE',
             'cause': 'revision вне 0..head',
             'fix': 'использовать revision из hint/dom_resume'},
            {'code': 'NOT_INITIALIZED',
             'cause': 'база не создана',
             'fix': 'сначала dom_start'},
        ],
        'help': ('Тебе НУЖНО: 1) Помнить, что dom_snapshot — синоним dom_query с той же схемой. '
                 '2) Передать branch и, при необходимости, revision. '
                 '3) Предпочитать dom_query в новом коде. '
                 '4) Для истории версий claim использовать dom_claim_versions.'),
    },
    'dom_trace': {
        'usage': 'dom_trace(branch?)',
        'preconditions': [
            'Проект открыт (dom_start), иначе readiness будет UNKNOWN/NOT_READY',
            'Вызван dom_describe — известны ветки',
        ],
        'postconditions': [
            'Получены readiness, ready_for_owner_review, residual, blockers, chain',
            'admitted_total и admitted_independent — разные числа',
            'owner_close_called всегда false: trace не закрывает задачу владельца',
        ],
        'common_errors': [
            {'code': 'UNKNOWN_BRANCH',
             'cause': 'branch не объявлена',
             'fix': 'убрать branch (все ветки) или взять код из dom_describe'},
            {'code': 'NO_SUCH_BRANCH',
             'cause': 'у ветки нет головы в базе',
             'fix': 'проверить state через dom_describe'},
            {'code': 'NOT_INITIALIZED',
             'cause': 'база не создана',
             'fix': 'сначала dom_start'},
            {'code': 'CHAIN_BROKEN',
             'cause': 'цепочка событий не проходит проверку',
             'fix': 'остановить запись и сообщить пользователю; trace покажет разрыв'},
        ],
        'help': ('Тебе НУЖНО: 1) Вызвать без branch (все ветки) или с одной веткой. '
                 '2) Прочитать readiness: admitted_total и admitted_independent — разные числа. '
                 '3) Проверить blockers и residual по requirement_ref. '
                 '4) Помнить: readiness не закрывает задачу владельца.'),
    },
    'dom_export': {
        'usage': 'dom_export(branch, revision?)',
        'preconditions': [
            'Вызван dom_describe — известны коды веток',
            'Для исторического среза известен revision 0..head',
        ],
        'postconditions': [
            'Получен EXPORTED с handle, sha256 и revision',
            'YAML записан по фиксированному пути внутри run-каталога',
            'При EXPORT_PENDING файл не подтверждён — повторить вызов',
        ],
        'common_errors': [
            {'code': 'NO_SUCH_BRANCH',
             'cause': 'у ветки нет головы в базе',
             'fix': 'проверить state через dom_describe'},
            {'code': 'REVISION_OUT_OF_RANGE',
             'cause': 'revision вне 0..head',
             'fix': 'взять допустимый revision из hint или dom_resume'},
            {'code': 'UNKNOWN_BRANCH',
             'cause': 'branch не объявлена',
             'fix': 'взять код из dom_describe.branches'},
            {'code': 'NOT_INITIALIZED',
             'cause': 'база не создана',
             'fix': 'сначала dom_start'},
        ],
        'help': ('Тебе НУЖНО: 1) Передать branch; revision — только для исторического среза. '
                 '2) Забрать handle и sha256 из ответа. '
                 '3) При EXPORT_PENDING повторить вызов. '
                 '4) Не пытаться задать путь: он фиксирован внутри run-каталога.'),
    },
    'dom_sync_status': {
        'usage': 'dom_sync_status(branch)',
        'preconditions': [
            'Вызван dom_describe — известны коды веток',
            'Ветка существует (dom_resume/dom_query)',
        ],
        'postconditions': [
            'Получен SYNC_STATUS с intents[] (pending/ambiguous/ack)',
            'Видно, что ещё не доставлено в OMH/YAML-экспорт',
            'Вызов read-only',
        ],
        'common_errors': [
            {'code': 'NO_SUCH_BRANCH',
             'cause': 'у ветки нет головы в базе',
             'fix': 'проверить state через dom_describe'},
            {'code': 'UNKNOWN_BRANCH',
             'cause': 'branch не объявлена',
             'fix': 'взять код из dom_describe.branches'},
            {'code': 'NOT_INITIALIZED',
             'cause': 'база не создана',
             'fix': 'сначала dom_start'},
            {'code': 'CHAIN_BROKEN',
             'cause': 'цепочка событий не проходит проверку',
             'fix': 'остановить запись и сообщить оператору'},
        ],
        'help': ('Тебе НУЖНО: 1) Передать branch. '
                 '2) Прочитать intents[]: pending/ambiguous/ack — что ещё не доставлено. '
                 '3) При ambiguous не переписывать состояние вручную, а вызвать dom_sync. '
                 '4) Помнить: вызов read-only.'),
    },
    'dom_sync': {
        'usage': 'dom_sync(branch)',
        'preconditions': [
            'У launch есть capability admit',
            'Ветка существует и цепочка событий цела',
        ],
        'postconditions': [
            'Получен SYNCED (всё доставлено) или SYNC_PENDING (есть незавершённые intent)',
            'Сделан YAML-экспорт и симулированная доставка в OMH с read-back',
            'Состояние доставки записано только этим инструментом',
        ],
        'common_errors': [
            {'code': 'NO_SUCH_BRANCH',
             'cause': 'у ветки нет головы в базе',
             'fix': 'проверить state через dom_describe'},
            {'code': 'INDEPENDENCE_NOT_ENFORCED',
             'cause': 'нет capability admit или отключена симуляция',
             'fix': 'запустить сервер с ролью, у которой есть admit'},
            {'code': 'CHAIN_BROKEN',
             'cause': 'цепочка событий не проходит проверку',
             'fix': 'остановить запись и сообщить оператору'},
            {'code': 'NOT_INITIALIZED',
             'cause': 'база не создана',
             'fix': 'сначала dom_start'},
        ],
        'help': ('Тебе НУЖНО: 1) Убедиться, что у launch есть право admit и ветка существует. '
                 '2) Вызвать dom_sync(branch) — он сделает YAML-экспорт и симулированную доставку с read-back. '
                 '3) SYNCED — всё доставлено; SYNC_PENDING — повторить позже. '
                 '4) Состояние доставки пишет только этот инструмент, не dom_append.'),
    },
    'dom_recall': {
        'usage': 'dom_recall(branch, at_seq)',
        'preconditions': [
            'Вызван dom_describe — известны коды веток',
            'at_seq — номер события ветки из dom_resume/trace или 0',
        ],
        'postconditions': [
            'Получен RECALL с active_claims, archived_claims и resolved_conflicts',
            'Получены related_branches и baseline_range',
            'pending_decisions может быть null: история кандидатов не восстанавливается',
        ],
        'common_errors': [
            {'code': 'INVALID_FIELD',
             'cause': 'at_seq не целое или отрицательное',
             'fix': 'передать целое at_seq >= 0'},
            {'code': 'REVISION_OUT_OF_RANGE',
             'cause': 'at_seq больше головы ветки',
             'fix': 'взять at_seq из dom_resume/trace (0..head)'},
            {'code': 'NO_SUCH_BRANCH',
             'cause': 'у ветки нет головы в базе',
             'fix': 'проверить state через dom_describe'},
            {'code': 'UNKNOWN_BRANCH',
             'cause': 'branch не объявлена',
             'fix': 'взять код из dom_describe.branches'},
        ],
        'help': ('Тебе НУЖНО: 1) Передать branch и at_seq — номер события в ветке (0 = пусто, head = текущее). '
                 '2) Прочитать active_claims/archived_claims и baseline_range. '
                 '3) Использовать для просмотра состояния до изменения. '
                 '4) При REVISION_OUT_OF_RANGE взять at_seq из dom_resume/trace.'),
    },
    'dom_claim_versions': {
        'usage': 'dom_claim_versions(claim_id)',
        'preconditions': [
            'Известен claim_id любой версии (dom_find/dom_context)',
        ],
        'postconditions': [
            'Получены versions[] от старой к новой с is_current и supersedes',
            'Получены logical_claim_id и resolution',
            'claim_id с is_current=true пригоден для dom_update',
        ],
        'common_errors': [
            {'code': 'NO_SUCH_CLAIM',
             'cause': 'claim_id не существует или вне scope',
             'fix': 'найти id через dom_find'},
            {'code': 'INVALID_FIELD',
             'cause': 'claim_id пустой или не строка',
             'fix': 'передать непустой claim_id строкой'},
            {'code': 'NOT_INITIALIZED',
             'cause': 'база не создана',
             'fix': 'сначала dom_start'},
        ],
        'help': ('Тебе НУЖНО: 1) Передать claim_id любой версии (из dom_find/dom_context). '
                 '2) Прочитать versions[] от старой к новой и is_current. '
                 '3) Взять is_current=true claim_id для dom_update. '
                 '4) Не обновлять заменённую версию — придёт NOT_CURRENT.'),
    },
    'dom_diff': {
        'usage': 'dom_diff(branch_a, branch_b)',
        'preconditions': [
            'Вызван dom_describe — обе ветки объявлены и видны',
            'Для сравнения вариантов известны коды базовой и variant-ветки',
        ],
        'postconditions': [
            'Получены only_a, only_b, changed и identical по logical claim id',
            'Получены comparable и common_ancestor',
            'Вызов read-only',
        ],
        'common_errors': [
            {'code': 'UNKNOWN_BRANCH',
             'cause': 'одна из веток не объявлена',
             'fix': 'взять коды из dom_describe.branches'},
            {'code': 'NO_SUCH_BRANCH',
             'cause': 'у одной из веток нет головы в базе',
             'fix': 'проверить state через dom_describe'},
            {'code': 'SCOPE_DENIED',
             'cause': 'ветка вне grant этого запуска',
             'fix': 'сравнивать только свои ветки'},
            {'code': 'NOT_INITIALIZED',
             'cause': 'база не создана',
             'fix': 'сначала dom_start'},
        ],
        'help': ('Тебе НУЖНО: 1) Передать branch_a и branch_b из dom_describe. '
                 '2) Прочитать only_a/only_b/changed/identical. '
                 '3) comparable=false означает, что у веток нет общих logical claim. '
                 '4) Для одной ветки использовать dom_query/dom_recall.'),
    },
}


def missing_tools(registered) -> list[str]:
    """Return the tool names from *registered* that have no HELP_DATA entry.

    Accepts an iterable of names, or of objects carrying a ``name`` attribute
    (``mcp.types.Tool``), so a registry table can be passed directly.
    The result is sorted and de-duplicated.
    """
    names = {str(getattr(item, 'name', item)) for item in registered}
    return sorted(names - set(HELP_DATA))
