# Справочник ошибок DOM

Полный справочник отказов MCP-инструментов DOM: код, инструменты, причина, решение, а также предусловия и постусловия каждого инструмента. Составлен по реальным путям отказа сервера, а не по документации «на память».

**Источники (ground truth):**

- `opencode/HELP_DATA.py` — словарь справки: `usage`, `preconditions`, `postconditions`, `common_errors`, `help` для каждого инструмента;
- `opencode/mockup/claim_entry.py`, `admission.py`, `planning.py`, `distillate.py`, `llm.py` — фактические `Denied(...)`/`ToolInput(...)`/`ValueError` пути;
- `opencode/mockup/public_dto.py` — таблицы `_HELP`, `_STATUS_HELP`, `DEFAULT_HELP` и проекция ответа;
- `opencode/mockup/server.py` — привязка аргументов (`strict_arguments`), `UNKNOWN_TOOL`, `INTERNAL_ERROR`, `PUBLIC_PROJECTION_INVALID`.

**Покрытие:** 35 инструментов, 71 код отказа (из них 50 описаны в `common_errors` HELP_DATA, остальные 21 видны в коде и перечислены в Приложении A).

## Содержание

1. [Общий алгоритм при ошибке](#1-общий-алгоритм-при-ошибке)
2. [Таблица ошибок](#2-таблица-ошибок) — по категориям, без дублей
3. [Инструменты](#3-инструменты) — 35 карточек: preconditions, postconditions, ошибки
4. [Что делать, если help пустой](#4-что-делать-если-help-пустой)
A. [Приложение A. Коды в коде, но не в HELP_DATA](#приложение-a-коды-в-коде-но-не-в-help_data)
B. [Приложение B. Статусы, которые не являются ошибками](#приложение-b-статусы-которые-не-являются-ошибками)
C. [Приложение C. FAQ](#приложение-c-faq)

## 1. Общий алгоритм при ошибке

Отказ — это ответ, у которого `status` не входит в список успешных статусов инструмента. Доменный отказ всегда содержит `status`, `hint` и `help`; транспортный и инфраструктурный — `status`, `error.code`, `error.tool`, `error.message`, `hint`, `help`.

### 1.1. Поля ответа с ошибкой

| Поле | Что означает |
|---|---|
| `status` | Код отказа (см. §2). |
| `hint` | Конкретная причина; часто содержит точный следующий вызов. |
| `next_action` | Имя инструмента, который нужно вызвать следующим (если задан). |
| `field` | Отклонённое поле аргумента (`INVALID_FIELD`, `MISSING_FIELD`). |
| `branches` | Допустимые коды веток (`UNKNOWN_BRANCH`, `SCOPE_DENIED`). |
| `claim_id`, `resolution` | Текущая версия и объяснение (`NOT_CURRENT`, `CONFLICT_READ_REQUIRED`). |
| `error.code`, `error.tool`, `error.message` | Только у транспортных (`ToolInput`) и внутренних сбоев. `sqlite_code`/`sqlite_name` — у `STORAGE_*`. |
| `help` | Императивная инструкция «Тебе НУЖНО: 1) … 2) …». Есть **всегда** (см. §4). |

### 1.2. Flowchart (текстом)

```text
Ошибка в ответе инструмента
   │
   ├─ 1. Прочитай status + hint (+ field / branches / claim_id)
   │
   ├─ 2. Прочитай help в ответе — это готовая инструкция; выполни её шаги
   │
   ├─ 3. Нужны детали? → dom_help({"tool": "<tool>"})
   │        └─ usage, preconditions, postconditions, common_errors, help
   │
   ├─ 4. Проверь preconditions инструмента (§3)
   │        ├─ проект открыт?       → dom_describe → dom_start
   │        ├─ ветка объявлена?     → dom_describe.branches
   │        ├─ claim прочитан?      → dom_find / dom_context
   │        └─ план загружен?       → dom_plan_status / dom_plan_tree
   │
   ├─ 5. Исправь аргументы или состояние и повтори ТОТ ЖЕ вызов
   │
   └─ 6. Отказ повторяется? → dom_describe, сверить аргументы, сообщить оператору
```

### 1.3. Четыре класса отказов

| Класс | Как выглядит | Что делать |
|---|---|---|
| Транспортный (`ToolInput`) | `INVALID_ARGUMENTS`, `UNKNOWN_TOOL`; `isError=true` | Сверить аргументы с сигнатурой; ничего не выполнено, повтор безопасен. |
| Доменный (`DomainError`) | Любой код §2; `hint` + `help` | Исправить аргумент/состояние; транзакция либо не начиналась, либо откатилась целиком. |
| Хранилище (`StorageError`) | `STORAGE_*`, `MIGRATION_REQUIRED`; есть `error.sqlite_code` | Это не аргументы: повторить/мигрировать/сообщить оператору. |
| Целостность (`Broken`) | `CHAIN_BROKEN`; `readiness=NOT_READY` | Остановить запись, `dom_trace`, сообщить оператору. |

### 1.4. Частые циклы исправления

| Отказ | Что сделать |
|---|---|
| `INVALID_FIELD` / `MISSING_FIELD` | Посмотреть `field` и `hint` → `dom_describe` (пример вызова для type) → повторить. |
| `NOT_CURRENT` | `dom_claim_versions` → взять `is_current=true` → `dom_update` этой версии. |
| `CONFLICT_READ_REQUIRED` | Прочитать `current_claim_id` → повторить с `expected_current` или `mode=fork`. |
| `GATE_NO_READ` | `dom_find`/`dom_context` по каждому claim из `basis` → повторить. |
| `UNKNOWN_BRANCH` / `NO_SUCH_BRANCH` | `dom_describe` → взять ветку из `branches`; при необходимости `dom_start`. |
| `PLAN_CONFLICT` | `dom_plan_tree` → слить → `dom_plan_ingest(base_revision=current_revision)`. |
| `REVISION_CONFLICT` / `STALE_REVIEW` | `dom_resume` → собрать кандидата от новой головы → submit/review/admit. |
| `DIGEST_MISMATCH` | Скопировать `intent_digest` из `dom_candidate`/`dom_submit` без изменений. |
| `STORAGE_BUSY` | Повторить тот же вызов через мгновение. |
| `CHAIN_BROKEN` | Остановиться; `dom_trace`; сообщить оператору. |

## 2. Таблица ошибок

Один код — одна строка, даже если он встречается у нескольких инструментов. Точная причина для конкретного инструмента — в его карточке (§3) и в `hint` ответа.

### 2.1 Аргументы и текст

| Код | Инструменты | Причина | Решение |
|---|---|---|---|
| INVALID_ARGUMENTS | все (транспорт) | Аргументы не совпали с сигнатурой: неизвестный ключ, отсутствует обязательный аргумент, неверный тип. Ничего не выполнено. | Передать аргументы строго по сигнатуре из `dom_describe`; убрать лишние ключи; в `hint` перечислены required/optional. |
| INVALID_FIELD | dom_start, dom_claim, dom_find, dom_update, dom_annotate, dom_context, dom_plan_status, dom_plan_ingest, dom_plan_tree, dom_task_claims, dom_task_graph, dom_extract, dom_extract_submit, dom_accept, dom_review, dom_candidate, dom_recall, dom_claim_versions, dom_help | Значение поля недопустимо: `branch` не из branches, `limit`/`depth`/`check_every` вне диапазона, `mode`/`kind`/`format`/`provider` не из списка, пустая строка, битый `anatomy`/`refines`, серверное имя в `property` и т. п. Поле — в `field`, причина — в `hint`. | Исправить поле по `hint` и примеру в `dom_describe`, затем повторить. |
| MISSING_FIELD | dom_claim, dom_annotate | Нет обязательного поля: для типа claim (decision→rationale, quote→source_ref, formula→notation+units, code→repository_ref+relative_path, data→artifact_ref+units, concept→definition) или для kind аннотации (comment→text, link→url+locator, property→name+value, archive/conflict/resolve→reason). Ничего не записано. | Добавить поле; `dom_describe` показывает валидный пример на каждый type. |
| EMPTY_TEXT | dom_claim, dom_update, dom_extract, dom_extract_submit | `text` пустой или состоит только из пробелов. | Передать непустой текст дословно. |
| INVALID_TEXT | dom_start, dom_claim, dom_update, dom_extract, dom_extract_submit | Текст не кодируется в UTF-8 (одиночные суррогаты). | Перекодировать материал в обычный UTF-8 и повторить. |
| TEXT_TOO_LARGE | dom_claim, dom_extract, dom_extract_submit | `text` больше 1 MiB UTF-8 (claim) или материал больше 200 kB (extract). | Разбить на части: отдельные claims или отдельные вызовы `dom_extract`. |
| UNKNOWN_TYPE | dom_claim, dom_find | `type` не из семи допустимых. | Использовать `quote`, `decision`, `formula`, `code`, `data`, `concept`, `text`. |
| UNKNOWN_TOOL | все (транспорт) | Имя инструмента не объявлено в SIGNATURE или недоступно в профиле запуска. | `dom_describe` → список инструментов профиля; в strict — `tools/list`. |

### 2.2 Состояние проекта и ветки

| Код | Инструменты | Причина | Решение |
|---|---|---|---|
| NOT_INITIALIZED | все, кто открывает ledger (кроме dom_describe, dom_start, dom_init) | База в каталоге ещё не создана либо привязка work отсутствует. | `dom_describe` → state; при NOT_STARTED — `dom_start({"request": "<запрос дословно>"})`. |
| MIGRATION_REQUIRED | все | Схема БД старше кода: нет нужной таблицы или колонки (в т.ч. после обновления плагина). | Один раз `dom_start` (lite) или `dom_init` (strict) — миграция аддитивная; повторить исходный вызов. |
| SCOPE_DENIED | dom_start, dom_init, dom_submit, dom_append, dom_diff, dom_reconcile + любой scoped-вызов | База привязана к другой работе, ветка/кандидат/claim вне grant этого запуска, principal неизвестен. Чужая область не раскрывается. | Работать только со своими ветками; если база — твой проект: `dom_start` с исходным request и `adopt=true`, иначе сменить каталог. |
| PROFILE_STRICT | dom_update, dom_annotate, dom_accept, dom_distillate, dom_extract, dom_extract_submit, dom_plan_status, dom_plan_ingest, dom_task_claims, dom_task_graph, dom_reconcile | Инструмент существует только в lite-профиле. | Перезапустить сервер в lite или использовать strict-аналог (`dom_init`/`dom_submit`/`dom_review`/`dom_admit`). |
| PROFILE_LITE | dom_init | `dom_init` существует только в strict/lab. | В lite использовать `dom_start`. |
| UNKNOWN_BRANCH | dom_find, dom_resume, dom_query, dom_snapshot, dom_trace, dom_export, dom_sync_status, dom_recall, dom_diff | Ветка не объявлена в этом проекте. | Взять код из `dom_describe.branches`. |
| NO_SUCH_BRANCH | dom_resume, dom_query, dom_snapshot, dom_trace, dom_export, dom_sync_status, dom_sync, dom_recall, dom_diff | Ветка объявлена, но головы в базе нет. | Проверить state через `dom_describe`; при необходимости `dom_start`. |
| REVISION_OUT_OF_RANGE | dom_query, dom_snapshot, dom_export, dom_recall | `revision`/`at_seq` вне диапазона 0..head этой ветки. | Взять допустимый диапазон из `hint` или revision из `dom_resume`. |
| REQUEST_REQUIRED | dom_start | `request` отсутствует или пустой: пустого baseline нет. | Передать запрос заказчика дословно непустой строкой. |
| ROOT_CONFLICT | dom_start | В базе уже записан другой корневой request или goal; корень неизменяем. | Повторить с исходным request; уточнения — новым claim с basis=[root] или `dom_update` goal-claim. |
| OMH_BINDING_UNVERIFIED | dom_start, dom_init | Сервер запущен с `--no-simulation`: живой привязки нет. | Перезапустить без `--no-simulation` или сообщить оператору. |
| POLICY_MISMATCH | dom_init, dom_submit | `policy_digest` не совпадает с политикой запуска сервера. | Взять digest из launch-конфигурации без изменений; в lite не передавать. |
| REJECTED | dom_submit, dom_append, dom_init, dom_resume | Пакет не прошёл restricted-JSON схему (см. `errors[]`: path/code/fix), request не той формы, `limit`/`after` неверны. | Исправить перечисленные поля без приведения типов и повторить. |

### 2.3 Жизненный цикл claim

| Код | Инструменты | Причина | Решение |
|---|---|---|---|
| NO_SUCH_CLAIM | dom_update, dom_annotate, dom_context, dom_claim_versions | `claim_id` не существует или вне scope. | Найти id через `dom_find`. |
| NOT_CURRENT | dom_update, dom_annotate | Версия уже заменена или заархивирована. | `dom_claim_versions` → взять claim_id с `is_current=true`. |
| CONFLICT_READ_REQUIRED | dom_update | `expected_current` устарел: claim изменился после прочитанной версии. Ничего не записано. | Прочитать `current_claim_id` через `dom_claim_versions` и повторить update (или fork, если версия чужая). |
| FORK_NOT_ALLOWED | dom_update | `mode=fork` над собственной текущей версией. | Обновить её `mode=update` или оставить как есть. |
| ROOT_IMMUTABLE | dom_update | Попытка заменить корневой request заказчика. | Добавить уточняющий claim с `basis=[root claim_id]`; goal уточнять отдельным update. |
| NO_CHANGE | dom_update | Новый `text` и `anatomy` совпадают с текущими. | Изменить text/anatomy либо не вызывать update. |
| UPDATE_INCOMPLETE | dom_update | Новая версия принята, но старая ещё не помечена заменённой (сбой между шагами). | Повторить тот же `dom_update`: дубликата не будет, шаг завершится. |
| GATE_NO_READ | dom_claim, dom_update, dom_annotate | `basis` (или обновляемый claim) не прочитан этой сессией: нет read receipt, а проект работает под политикой manual/independent. | Прочитать каждый claim basis через `dom_find`/`dom_context` и повторить. |
| TASK_NOT_FOUND | dom_claim, dom_find, dom_task_claims, dom_task_graph, dom_extract, dom_extract_submit | Пункт плана не найден по id, пути (П1.2) или коду ветки. | `dom_plan_tree` → взять существующий пункт, либо `dom_plan_ingest`. |
| TASK_ARCHIVED | dom_claim | Пункт плана удалён из плана и заархивирован. | Привязать claim к текущему пункту из `dom_plan_tree`. |
| IDEMPOTENCY_MISMATCH | dom_submit, dom_claim, dom_update (и любой write с idempotency_key) | Тот же `idempotency_key` уже использован для другого содержимого. | Для нового содержимого взять свежий ключ; для повтора — тот же ключ и те же данные. |

### 2.3.1 Read-gate и режимы policy

Gate работает только в manual/independent режиме. В auto mode read receipts не проверяются — все write-операции проходят без gate. Это относится к `dom_claim`, `dom_update` и `dom_annotate`: под `auto` они адмитятся сразу, `GATE_NO_READ` не возникает, даже если claim из `basis` не читался этой сессией. `dom_describe.read_gate` показывает фактический режим (launch-политика либо более строгая, под которой проект уже работал); `dom_describe.review_policy.mode` — режим запуска. `GATE_NO_READ` — только диагностика: отказ типизированный, ничего не записано, повтор после `dom_find`/`dom_context` безопасен.

### 2.4 Review и admission

| Код | Инструменты | Причина | Решение |
|---|---|---|---|
| NO_SUCH_CANDIDATE | dom_accept, dom_review, dom_candidate | `candidate_id` не существует или вне scope. | Взять id из `dom_resume.pending` или `dom_extract`. |
| CANDIDATE_CLOSED | dom_accept | Кандидат уже admitted/rejected/superseded. | Прочитать состояние через `dom_candidate`; не решать повторно. |
| DIGEST_MISMATCH | dom_review, dom_admit | `intent_digest` не совпадает с сохранённым кандидатом. | Скопировать digest без изменений из `dom_candidate`/`dom_submit`. |
| STALE_REVIEW | dom_admit | Review не соответствует кандидату, политике, зависимостям или головам веток. | Перечитать `dom_resume` и выполнить review заново. |
| REVIEW_REJECTED | dom_admit | Последний review отклонил кандидата. | Не адмитить; записать исправленный claim и пройти review заново. |
| REVIEWER_SESSION_REQUIRED | dom_admit | У review нет `reviewer_session` (NULL). | Выполнить review из доверенной сессии и адмитить его `review_id`. |
| REVISION_CONFLICT | dom_accept, dom_admit | Голова ветки сдвинулась после сборки кандидата. | `dom_resume` → повторить submit/review/admit; под independent автор пересдаёт материал. |
| INDEPENDENCE_NOT_ENFORCED | dom_accept, dom_admit, dom_candidate, dom_review, dom_submit, dom_append, dom_sync | Нет нужной capability (submit/review/admit) или под политикой independent ревьюер совпадает с автором. | Запустить сервер с нужной ролью/principal; см. `dom_describe.review_policy`. |
| OWNER_AUTHORITY_REQUIRED | dom_submit, dom_append | Событие `exclude` прислано без capability `owner`. | Такие события пишет только owner-launch. |
| IDEMPOTENCY_BINDING_MISMATCH | dom_submit, dom_admit, dom_append | `idempotency_key` уже привязан к другому кандидату или ветке. | Взять новый ключ. |
| IDEMPOTENCY_BINDING_UNKNOWN | dom_submit, dom_admit, dom_append | Legacy-строка идемпотентности блокирует этот ключ. | Взять новый ключ. |
| IDEMPOTENCY_REVIEW_MISMATCH | dom_admit | `idempotency_key` использован с другим review. | Использовать исходный `review_id` либо новый ключ. |
| DELIVERY_ENGINE_ONLY | — (внутренний compatibility-путь) | Попытка записать состояние доставки вручную, минуя движок. | Состояние пишет только `dom_sync`; читать — `dom_sync_status`. |

### 2.5 План

| Код | Инструменты | Причина | Решение |
|---|---|---|---|
| PLAN_EMPTY | dom_plan_ingest | В переданном плане не найдено ни одного пункта. | Нумерованные пункты (1., 1.1), bullets/заголовки или JSON `[{"title": ...}]`. |
| PLAN_CONFLICT | dom_plan_ingest | План изменился после твоего `base_revision`; ничего не записано. | `dom_plan_tree` → слить изменения → повторить с `base_revision=current_revision`; `force=true` — осознанно. |
| PLAN_TOO_WIDE | dom_plan_ingest | План шире лимитов: 676 верхних пунктов, 35 подпунктов на пункт, 2000 всего. | Сократить план или разбить работу на этапы. |
| PLAN_ROOT_NOT_FOUND | dom_plan_tree | `root` не найден в проекции плана. | Взять id/путь из `dom_plan_status`/`dom_plan_tree` или переслать план. |
| PLAN_PROJECTION_STALE | dom_plan_tree | `require_fresh=true`, а проекция устарела. | Переслать план через `dom_plan_ingest` и повторить. |
| STALE_PROJECTION_SNAPSHOT | dom_plan_ingest | `source_revision` старше текущей проекции плана. | Переслать план с актуальным `source_revision` из `dom_plan_status`. |
| PLAN_CYCLE | dom_plan_tree | В иерархии плана обнаружен цикл. | Остановить обход и исправить структуру плана. |

### 2.6 Дистиллят утверждений

| Код | Инструменты | Причина | Решение |
|---|---|---|---|
| STATEMENT_NOT_FOUND | dom_distillate | id не является цитатой источника или версией дистиллята. | Взять `source`/`distillate` из строки `[DOM project memory]` или вызвать `action=show`. |
| SPAN_NOT_FOUND | dom_distillate | Anchor не является дословной подстрокой источника. | Скопировать предложение источника точно, без пересказа. |
| SPAN_AMBIGUOUS | dom_distillate | Anchor встречается в источнике несколько раз. | Взять более длинную уникальную цитату. |
| PROPOSAL_INVALID | dom_distillate | Нет `text` или `anchors` не по одному массиву на предложение. | Передать text (1–3 предложения) и anchors: `[[цитата], [цитата]]`. |
| PROPOSAL_CONFLICT | dom_distillate | У текущей версии тот же нормализованный text, но другие anchors. | Отозвать версию (`withdraw`) или передать исходные anchors. |
| NO_IMPROVEMENT | dom_distillate | Кандидат совпадает с эффективной версией. | Предложить реально изменённый text или оставить эффективную версию. |
| REVIEW_REQUIRED | dom_distillate | `accept` без успешного `validate` этой версии. | Сначала `action=validate`, затем `accept`. |
| REVIEW_FAILED | dom_distillate | Последний review этой версии провалился. | Исправить text/anchors и предложить версию заново. |
| REVIEW_STALE | dom_distillate | Review не соответствует изменившимся text/anchors. | Выполнить `validate` заново, затем `accept`. |
| DISTILLATE_DISPUTED | dom_distillate | Открытый спор по источнику или версии блокирует действие. | Закрыть спор через `action=resolve` с `note`. |
| DISTILLATE_LOOP | dom_distillate | Три неудачных review — цепочка остановлена, сброса нет. | Спросить пользователя; новый источник начнёт новую цепочку. |

### 2.7 Целостность, хранилище и внутренние сбои

| Код | Инструменты | Причина | Решение |
|---|---|---|---|
| CHAIN_BROKEN | все, кто читает или пишет ledger | Хеш-цепочка событий не проходит проверку: база правилась вне сервера. Запись остановлена. | Остановиться, вызвать `dom_trace`, сообщить оператору; базу не править. |
| STORAGE_BUSY | все | База занята другой транзакцией (SQLITE_BUSY/LOCKED). | Повторить тот же вызов через мгновение. |
| STORAGE_CORRUPT | все | Файл повреждён или это не DOM-база (SQLITE_CORRUPT/NOTADB). | Остановиться, не править файл, сообщить оператору путь базы. |
| STORAGE_READ_ONLY | все | База доступна только на чтение (SQLITE_READONLY). | Проверить права на файл и каталог; сообщить оператору. |
| STORAGE_CONSTRAINT | все | Сработало ограничение неизменяемости или целостности (SQLITE_CONSTRAINT). | Не обходить правило SQL: записать изменение новым claim/событием. |
| STORAGE_UNAVAILABLE | все | Иная ошибка SQLite: базу не удалось использовать. | Повторить один раз; если повторяется — сообщить оператору, базу не пересоздавать. |
| INTERNAL_ERROR | все | Внутренний сбой сервера; детали наружу не отдаются (fail-closed). | Повторить один раз; при повторе — сообщить оператору (`error_type` есть в логе сервера). |
| PUBLIC_PROJECTION_INVALID | все | Сервер отказал в сериализации собственного ответа (fail-closed проекция). | Не повторять вслепую; сообщить оператору имя инструмента и ответ. |

Инфраструктурные коды (`STORAGE_*`, `MIGRATION_REQUIRED`, `CHAIN_BROKEN`, `INTERNAL_ERROR`, `PUBLIC_PROJECTION_INVALID`) возможны у любого инструмента, который открывает ledger; они не повторяются в каждой карточке §3.

## 3. Инструменты

Для каждого инструмента: назначение, `usage`, предусловия, постусловия и его `common_errors` из `HELP_DATA` (плюс коды, реально достижимые по коду, — строка «Также возможны»).

**Общие коды для всех инструментов** (здесь не дублируются): `INVALID_ARGUMENTS`, `STORAGE_BUSY`, `STORAGE_CORRUPT`, `STORAGE_READ_ONLY`, `STORAGE_CONSTRAINT`, `STORAGE_UNAVAILABLE`, `MIGRATION_REQUIRED`, `CHAIN_BROKEN`, `INTERNAL_ERROR`, `PUBLIC_PROJECTION_INVALID` — см. §2.1, §2.2 и §2.7.

### 3.1. `dom_describe`

Первая ориентация: state, profile, ветки, инструменты, review_level, readiness.

**Вызов:** `dom_describe()`

**Preconditions**

- Это первый вызов в новой сессии — других предусловий нет
- Не передавать аргументов: identity и профиль фиксируются при запуске сервера

**Postconditions**

- Получен state (READY | NOT_STARTED | FOREIGN_BASE | CHAIN_BROKEN) и profile (lite | strict)
- Получен список tools с аргументами и примерами
- Получены branches, review_level, readiness, limits и next_steps
- Для каждого claim type показан валидный пример вызова

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| INVALID_ARGUMENTS | передан неизвестный ключ или аргумент неверного типа | вызвать dom_describe({}) без аргументов |
| STORAGE_BUSY | база занята другой транзакцией | повторить вызов через мгновение |
| STORAGE_CORRUPT | файл базы повреждён или это не DOM-база | остановиться и сообщить оператору, базу не править |

### 3.2. `dom_start`

Открыть/создать проект в каталоге и записать неизменяемый корневой request.

**Вызов:** `dom_start(request, goal?, branches?, adopt?)`

**Preconditions**

- dom_describe показал state=NOT_STARTED (или FOREIGN_BASE, если нужен adopt)
- Текст запроса заказчика готов дословно (непустая строка)
- branches, если задаются, — коды [A-Za-z0-9_-]{1,32}, например ["main", "alt"]

**Postconditions**

- Получен status STARTED | ALREADY_STARTED и work_ref
- Созданы ветки; main_branch — первая из branches
- Запрос сохранён неизменным корневым claim (request_claim_id)
- goal сохранён отдельным claim с goal_confirmed=false
- Получены next_steps с точным следующим вызовом

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| REQUEST_REQUIRED | request отсутствует или пустой | передать запрос заказчика дословно: dom_start({"request": "<запрос>"}) |
| INVALID_FIELD | неверный goal, branches или adopt (тип/формат) | branches — массив кодов [A-Za-z0-9_-]{1,32}, goal — непустая строка, adopt — bool |
| SCOPE_DENIED | база в каталоге привязана к другой работе, adopt не передан | если это твой проект — повторить с adopt=true, иначе сменить каталог |
| ROOT_CONFLICT | в базе уже записан другой корневой request или goal | повторить с исходным request; goal уточнять через dom_update, не перезаписывать корень |
| OMH_BINDING_UNVERIFIED | сервер запущен с --no-simulation, живой привязки нет | перезапустить сервер без --no-simulation или сообщить оператору |
| CHAIN_BROKEN | цепочка событий базы не проходит проверку | остановить запись, вызвать dom_trace и сообщить пользователю |

**Также возможны:** `INVALID_TEXT` — коды есть в коде, но не в `common_errors` этого инструмента; причина и решение в §2.

### 3.3. `dom_claim`

Записать один claim (7 типов) за один вызов.

**Вызов:** `dom_claim(branch?, type, text, plan_task?, basis?, refines?, purpose?, usage?, keywords?, requirement_refs?, ...)`

**Preconditions**

- Вызван dom_describe — известны ветки и профиль
- Выбран type: quote | decision | formula | code | data | concept | text
- Если задан plan_task — пункт плана существует (dom_plan_tree)
- Если задан basis — эти claim_id уже прочитаны (dom_find/dom_context)

**Postconditions**

- Получен status CLAIM_ADMITTED | ALREADY_RECORDED (или RECORDED_CANDIDATE под review-политикой)
- Получены claim_id, logical_claim_id, version и revision — сохранить claim_id как basis для следующих
- Текст сохранён байт-в-байт; изменение — только через dom_update

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| UNKNOWN_TYPE | type не из семи допустимых | использовать quote, decision, formula, code, data, concept или text |
| MISSING_FIELD | не заполнено обязательное для типа поле (decision→rationale, quote→source_ref, formula→notation+units, code→repository_ref+relative_path, data→artifact_ref+units, concept→definition) | вызвать dom_describe и посмотреть пример для своего type, затем повторить |
| EMPTY_TEXT | text пустой или состоит только из пробелов | передать непустой text дословно |
| TEXT_TOO_LARGE | text больше 1 MiB UTF-8 | разбить материал на несколько claims |
| INVALID_FIELD | неизвестный аргумент, неверный тип, пустая строка в basis/usage/keywords или branch не объявлена | свериться с аргументами dom_describe и убрать лишние/пустые значения |
| TASK_NOT_FOUND | plan_task не найден среди пунктов плана | вызвать dom_plan_tree или dom_plan_ingest и взять существующий id/путь/код |
| TASK_ARCHIVED | пункт плана удалён и заархивирован | привязать claim к текущему пункту из dom_plan_tree |
| GATE_NO_READ | claim из basis не прочитан этой сессией (read receipt отсутствует) | прочитать каждый claim basis через dom_find или dom_context, затем повторить |

**Также возможны:** `INVALID_TEXT`, `IDEMPOTENCY_MISMATCH` — коды есть в коде, но не в `common_errors` этого инструмента; причина и решение в §2.

### 3.4. `dom_find`

Поиск claims по тексту и метаданным; выдаёт read receipts.

**Вызов:** `dom_find(query?, branch?, type?, requirement_ref?, limit?, plan_task?, keyword?, current_only?)`

**Preconditions**

- Вызван dom_describe — известны коды веток
- Для поиска по пункту плана известен plan_task (dom_plan_tree)

**Postconditions**

- Получены matches (claim_id, branch, type, preview, is_current) и total
- При пустом результате total=0, matches=[] — это не ошибка
- truncated=true означает, что совпадений больше limit
- Найденные claim_id пригодны для basis, dom_update и dom_context

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| INVALID_FIELD | limit вне 1..200, пустой keyword или неверный тип аргумента | держать limit в 1..200 (по умолчанию 20) и передавать строки непустыми |
| UNKNOWN_TYPE | type не из семи допустимых | убрать type или использовать quote, decision, formula, code, data, concept, text |
| UNKNOWN_BRANCH | branch не объявлена в этом проекте | вызвать dom_describe и взять код из branches |
| TASK_NOT_FOUND | plan_task не найден | взять существующий пункт из dom_plan_tree |

### 3.5. `dom_update`

Заменить claim новой версией; старая остаётся в истории.

**Вызов:** `dom_update(claim_id, text, reason, expected_current?, mode?, idempotency_key?, anatomy?)`

**Preconditions**

- claim_id найден через dom_find и claim прочитан (dom_context или dom_claim_versions)
- Подготовлен полный новый text (не diff) и reason изменения
- Для защиты от гонки известен claim_id прочитанной версии (expected_current)

**Postconditions**

- Создана новая версия; получены new_claim_id и supersedes
- Старая версия осталась в истории с is_current=false
- При mode=fork получен CLAIM_FORKED, variant-ветка и conflict_ref
- При расхождении вернулся CONFLICT_READ_REQUIRED и ничего не записано

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| NO_SUCH_CLAIM | claim_id не существует или вне твоего scope | найти id через dom_find |
| NOT_CURRENT | обновляется уже заменённая или заархивированная версия | взять current claim_id из ответа/dom_claim_versions и обновить его |
| CONFLICT_READ_REQUIRED | claim изменился после прочитанной версии (expected_current устарел) | прочитать current_claim_id через dom_claim_versions и повторить update или fork |
| FORK_NOT_ALLOWED | mode=fork над собственной текущей версией | обновить свою версию mode=update или оставить как есть |
| ROOT_IMMUTABLE | попытка заменить корневой request заказчика | добавить уточняющий claim с basis=[root claim_id]; goal можно уточнить отдельно |
| NO_CHANGE | новый text и anatomy совпадают с текущими | изменить text или anatomy, либо не вызывать update |
| INVALID_FIELD | неверный mode/expected_current/reason/anatomy | mode: auto\|update\|fork; reason непустой; anatomy: {basis?, refines?, purpose?, usage?, keywords?} |
| GATE_NO_READ | обновляемый claim не прочитан этой сессией | прочитать его через dom_context и повторить |

**Также возможны:** `EMPTY_TEXT`, `INVALID_TEXT`, `IDEMPOTENCY_MISMATCH`, `UPDATE_INCOMPLETE`, `PROFILE_STRICT` — коды есть в коде, но не в `common_errors` этого инструмента; причина и решение в §2.

### 3.6. `dom_annotate`

Добавить событие к claim: comment/link/property/archive/conflict/resolve.

**Вызов:** `dom_annotate(claim_id, kind, text?, url?, locator?, name?, value?, conflict_ref?, reason?)`

**Preconditions**

- claim_id прочитан через dom_context или dom_find
- Выбран kind: comment | link | property | archive | conflict | resolve
- Для archive/conflict/resolve подготовлен reason

**Postconditions**

- Получен ANNOTATED с event_id и revision (или RECORDED_CANDIDATE под review-политикой)
- Сам claim не изменён: событие добавлено в append-only историю
- Для property действует последнее записанное значение

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| INVALID_FIELD | kind вне списка, лишний аргумент для kind или имя серверного поля в property (body/text/branch/...) | использовать допустимый kind и его аргументы; для property выбрать своё имя, например note_<что> |
| MISSING_FIELD | нет обязательного аргумента kind или reason для archive/conflict/resolve | comment→text, link→url+locator, property→name+value, conflict/resolve→conflict_ref; archive/conflict/resolve→reason |
| NO_SUCH_CLAIM | claim_id не существует или вне scope | найти claim через dom_find |
| NOT_CURRENT | kind=archive для уже заархивированного claim | проверить состояние через dom_context; архив повторно не ставится |
| GATE_NO_READ | claim не прочитан этой сессией | прочитать его через dom_context и повторить |

**Также возможны:** `PROFILE_STRICT` — коды есть в коде, но не в `common_errors` этого инструмента; причина и решение в §2.

### 3.7. `dom_context`

Откуда claim: anatomy, цепочка refines, basis, предки в плане.

**Вызов:** `dom_context(claim_id, depth?)`

**Preconditions**

- Известен claim_id любой версии (dom_find/dom_claim)
- Вызван dom_describe, если проект ещё не открыт

**Postconditions**

- Получен claim с anatomy и properties
- Получены refines_chain, basis, refinements и plan с предками
- Возвращены root_request_claim_id и resolution текущей версии

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| NO_SUCH_CLAIM | claim_id не существует или вне scope | найти id через dom_find |
| INVALID_FIELD | depth вне 1..32 или неверный тип | передать depth целым числом 1..32 (по умолчанию 8) |
| INVALID_ARGUMENTS | неизвестный ключ или claim_id не строка | вызывать dom_context({"claim_id": "<id>", "depth": 8}) |

### 3.8. `dom_plan_status`

Свежесть плана, due-чек, очередь inbox, конфликты.

**Вызов:** `dom_plan_status(check_every?)`

**Preconditions**

- Проект открыт (dom_start) — иначе PLAN_STATUS нечего показывать
- При первом обращении вызвать dom_describe

**Postconditions**

- Получены has_plan, project_closed, items и open_items
- Получены plan_check_due, iterations_since_change и request (plan_request | plan_check)
- Получены conflicts и next_steps
- Очередь inbox обработана: inbox_processed, inbox_queued, inbox_failed

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| INVALID_FIELD | check_every вне 1..10000 или не int | передать целое check_every (по умолчанию 10) |
| PROFILE_STRICT | вызов в strict-профиле | работать через strict-инструменты или перезапустить сервер в lite |
| NOT_INITIALIZED | база в каталоге ещё не создана | вызвать dom_start с запросом заказчика |
| CHAIN_BROKEN | цепочка событий не проходит проверку | остановить запись и сообщить пользователю; dom_trace покажет разрыв |

### 3.9. `dom_plan_ingest`

Загрузить или обновить план: пункты → header claims и ветки.

**Вызов:** `dom_plan_ingest(plan, source?, format?, contexts?, source_revision?, mode?, base_revision?, force?)`

**Preconditions**

- Вызван dom_plan_status и взят base_revision (source_revision)
- План подготовлен нумерованными пунктами (1., 1.1) или JSON [{"title", "children"}]
- Для важных пунктов собран контекст из разговора (contexts)

**Postconditions**

- Получен PLAN_INGESTED | PLAN_UNCHANGED и новый source_revision
- Пункты получили ветки (П1=AA, П1.1=AA100) и header claims
- Удалённые из full-плана пункты заархивированы, их claims сохранены
- При PLAN_CONFLICT ничего не записано; возвращены base/current revision и item_paths

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| PLAN_CONFLICT | план изменился после твоего base_revision | прочитать dom_plan_tree, слить изменения и повторить с base_revision=current_revision; force=true — осознанно |
| PLAN_EMPTY | в plan не найдено ни одного пункта | передать нумерованные пункты (1., 1.1), bullets/заголовки или JSON [{"title": ...}] |
| INVALID_FIELD | неверный plan/format/source/contexts/mode/base_revision/force | format: auto\|markdown\|json; mode: full\|partial; contexts: {путь: непустой текст} |
| PROFILE_STRICT | вызов в strict-профиле | использовать lite-профиль продукта |
| NOT_INITIALIZED | база не создана | сначала dom_start с запросом заказчика |

**Также возможны:** `PLAN_TOO_WIDE`, `STALE_PROJECTION_SNAPSHOT` — коды есть в коде, но не в `common_errors` этого инструмента; причина и решение в §2.

### 3.10. `dom_plan_tree`

Дерево плана от пункта: пути, ветки, счётчики claims.

**Вызов:** `dom_plan_tree(root, depth?, at_version?, require_fresh?)`

**Preconditions**

- План загружен (dom_plan_status → has_plan=true) или известен корневой пункт
- Известен root: id пункта, путь (П1.2) или код ветки (AA100)

**Postconditions**

- Получено дерево tasks с path, branch, claim_id, status и claim_count
- Получены source, synced_at, stale и source_revision
- plan_task_id и branch пригодны для dom_claim(plan_task=...) и dom_task_claims

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| PLAN_ROOT_NOT_FOUND | root не найден в проекции плана | взять id/путь из dom_plan_status → dom_plan_tree или переслать план |
| PLAN_PROJECTION_STALE | require_fresh=true, а проекция устарела | переслать план через dom_plan_ingest и повторить |
| PLAN_CYCLE | в иерархии плана обнаружен цикл | остановить обход и проверить структуру плана |
| INVALID_FIELD | root пустой, depth вне 1..10, at_version < 0 или require_fresh не bool | depth 1..10 (по умолчанию 3), at_version >= 0, require_fresh — bool |

### 3.11. `dom_task_claims`

Claims пункта плана по ролям (defines/justifies/implements/...).

**Вызов:** `dom_task_claims(task, include_children?, link_role?, include_history?)`

**Preconditions**

- План загружен и пункт существует (dom_plan_tree)
- Известен task — id, путь (П1.2) или код ветки (AA200)

**Postconditions**

- Получены claims_by_role (defines/justifies/implements/validates/documents/depends_on)
- Получены orphaned_links и summary
- link_stale помечает связи, сделанные до изменения смысла пункта

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| TASK_NOT_FOUND | пункт плана не найден по id/пути/коду | вызвать dom_plan_tree и взять существующий пункт |
| INVALID_FIELD | link_role вне списка или include_children/include_history не bool | link_role: all или одна из ролей; флаги — bool |
| PROFILE_STRICT | вызов в strict-профиле | использовать lite-профиль продукта |

### 3.12. `dom_task_graph`

Кто что внёс в пункт: цепи версий и рёбра (supersedes/basis/...).

**Вызов:** `dom_task_graph(task, agent?, include_history?, include_children?, limit?)`

**Preconditions**

- План загружен и пункт существует (dom_plan_tree)
- Известен task — id, путь или код ветки

**Postconditions**

- Получены chains с версиями и рёбрами supersedes/basis/refines/variant_of/task_link
- Получены agents[{author, root, claim_count}] и truncated

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| TASK_NOT_FOUND | пункт плана не найден | взять существующий пункт из dom_plan_tree |
| INVALID_FIELD | agent пустой, limit вне 1..1000 или флаг не bool | agent — непустая строка; limit 1..1000 (по умолчанию 200) |
| PROFILE_STRICT | вызов в strict-профиле | использовать lite-профиль продукта |

### 3.13. `dom_reconcile`

Свести variant-ветки, когда версии снова совпали.

**Вызов:** `dom_reconcile()`

**Preconditions**

- Ветки-варианты уже сведены по содержанию (например, после mode=fork в dom_update)
- Проект открыт и база не повреждена

**Postconditions**

- Получен RECONCILED и merged[{branch, claim_id, merged_into}]
- Совпавшие variant-claims заархивированы как merged_into базового
- Пустые variant-ветки получили converge-событие

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| PROFILE_STRICT | вызов в strict-профиле | использовать lite-профиль продукта |
| NOT_INITIALIZED | база не создана | сначала dom_start |
| SCOPE_DENIED | база привязана к другой работе | проверить dom_describe; adopt только для своего проекта |
| CHAIN_BROKEN | цепочка событий не проходит проверку | остановить запись и сообщить оператору |

### 3.14. `dom_extract`

Разбить материал на кандидаты для пункта плана.

**Вызов:** `dom_extract(task, text, provider?, link_role?)`

**Preconditions**

- План загружен, пункт существует (dom_plan_tree)
- Материал для разбора готов (непустой, до 200 kB)
- При provider=client — готовность ответить через dom_extract_submit

**Postconditions**

- Получен EXTRACTED с candidates[{candidate_id, intent_digest, type, preview}]
- Кандидаты только submitted — ничего не принято
- Получены already_recorded, refused, dropped и next_steps
- При provider=client получен EXTRACTION_REQUEST со схемой и инструкциями

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| TASK_NOT_FOUND | task не найден среди пунктов плана | взять существующий пункт из dom_plan_tree |
| TEXT_TOO_LARGE | материал больше лимита извлечения | разбить материал на части и вызвать dom_extract для каждой |
| INVALID_FIELD | provider вне deterministic\|anthropic\|command\|client или пустой текст | убрать provider (возьмётся из конфигурации) или выбрать допустимый |
| PROFILE_STRICT | вызов в strict-профиле | использовать lite-профиль продукта |

**Также возможны:** `EMPTY_TEXT`, `INVALID_TEXT` — коды есть в коде, но не в `common_errors` этого инструмента; причина и решение в §2.

### 3.15. `dom_extract_submit`

Вторая половина extract(provider="client"): прислать свои кандидаты.

**Вызов:** `dom_extract_submit(task, text, candidates, model?, link_role?)`

**Preconditions**

- Получен EXTRACTION_REQUEST от dom_extract(provider="client")
- Подготовлен непустой массив candidates по candidate_schema
- Тексты кандидатов скопированы из материала дословно

**Postconditions**

- Получен EXTRACTED, provider="client" и candidates[]
- Кандидаты, чей текст не найден в материале, попали в dropped
- Типизированный кандидат без обязательных фактов стал text с suggested:<type>
- Далее — dom_candidate и dom_accept

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| TASK_NOT_FOUND | task не найден среди пунктов плана | взять существующий пункт из dom_plan_tree |
| INVALID_FIELD | candidates не массив/пустой, model пустой или кандидат не по схеме | передать [{type, text, fields?, units?, notation?, purpose?, keywords?}] |
| TEXT_TOO_LARGE | материал больше допустимого | разбить материал и повторить |
| EMPTY_TEXT | text пустой | передать тот же непустой материал, что и в dom_extract |

**Также возможны:** `INVALID_TEXT`, `PROFILE_STRICT` — коды есть в коде, но не в `common_errors` этого инструмента; причина и решение в §2.

### 3.16. `dom_accept`

Решение по прочитанному кандидату: approve = review + admit одним вызовом.

**Вызов:** `dom_accept(candidate_id, decision?, note?)`

**Preconditions**

- Кандидат прочитан через dom_candidate и state=AWAITING_REVIEW
- Для reject подготовлен note с причиной

**Postconditions**

- approve → DEMO_ADMITTED с tx_id, revision, claim_ids (возможен rebased=true)
- reject → REVIEW_REJECTED с review_id
- Кандидат уходит из pending-списка dom_resume

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| NO_SUCH_CANDIDATE | candidate_id не существует или вне scope | взять id из dom_extract или pending в dom_resume |
| CANDIDATE_CLOSED | кандидат уже admitted/rejected/superseded | прочитать состояние через dom_candidate и не решать повторно |
| INVALID_FIELD | decision не approve/reject или reject без note | decision: approve\|reject; для reject передать непустой note |
| REVISION_CONFLICT | ветка ушла вперёд, а политика independent запрещает rebase | отклонить кандидата с note и пересдать материал автором |
| INDEPENDENCE_NOT_ENFORCED | нет capability review или отключена симуляция | запустить сервер с нужной ролью/политикой; см. dom_describe.review_level |

**Также возможны:** `PROFILE_STRICT` — коды есть в коде, но не в `common_errors` этого инструмента; причина и решение в §2.

### 3.17. `dom_distillate`

Цепочка дистиллята утверждения: show/propose/validate/accept/withdraw/dispute/resolve.

**Вызов:** `dom_distillate(action, source?, distillate?, text?, anchors?, by?, note?)`

**Preconditions**

- Известен source (id цитаты) или distillate (id версии) из строки statement
- Для propose подготовлен text (1-3 предложения) и anchors по одному на предложение
- Для withdraw/dispute/resolve подготовлен note

**Postconditions**

- show → DISTILLATE_SHOWN: цепочка версий, effective, open_conflicts, next_steps
- propose → PROPOSED или ALREADY_RECORDED
- validate → VALIDATED или REVIEW_FAILED
- accept → ACCEPTED; withdraw/dispute/resolve → WITHDRAWN/DISPUTED/RESOLVED

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| STATEMENT_NOT_FOUND | id не является цитатой источника или версией дистиллята | взять source/distillate из строки [DOM project memory] или вызвать action=show |
| SPAN_NOT_FOUND | anchor не является дословной подстрокой источника | скопировать предложение источника точно, без пересказа |
| SPAN_AMBIGUOUS | anchor встречается в источнике несколько раз | взять более длинную уникальную цитату |
| PROPOSAL_INVALID | нет text или anchors не по одному массиву на предложение | передать text и anchors: [[цитата], [цитата]] по предложениям |
| REVIEW_REQUIRED | accept без успешного validate этой версии | сначала dom_distillate(action="validate", source=<id>) |
| DISTILLATE_DISPUTED | открытый спор блокирует действие | закрыть спор через action=resolve с note |

**Также возможны:** `NOT_CURRENT`, `NO_IMPROVEMENT`, `PROPOSAL_CONFLICT`, `REVIEW_FAILED`, `REVIEW_STALE`, `DISTILLATE_LOOP`, `PROFILE_STRICT` — коды есть в коде, но не в `common_errors` этого инструмента; причина и решение в §2.

### 3.18. `dom_skill_select`

Детерминированный выбор top-3 skills для micro-step (read-only).

**Вызов:** `dom_skill_select(step, queue?, plan_version?, index_digest?)`

**Preconditions**

- Сформирован micro-step: объект с title и, желательно, context, cap[], markers, category_hint
- Опционально подготовлена очередь следующих 0-3 шагов для prefetch

**Postconditions**

- Получен block (<=800 символов) для инъекции в промпт
- Получены queue, advisory, gaps, prefetch, missing_caps, confidence_bp
- Одинаковый step и индекс всегда дают одинаковый результат (детерминированно)

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| INVALID_ARGUMENTS | step не объект, неизвестный ключ или queue не массив | вызывать dom_skill_select({"step": {"title": "...", "context": "...", "cap": [...]}})  |

### 3.19. `dom_help`

Справка по инструменту из HELP_DATA (read-only).

**Вызов:** `dom_help(tool)`

**Preconditions**

- Известно имя инструмента либо его нужно выбрать из списка доступных
- Проект, identity и ledger не нужны: вызов read-only

**Postconditions**

- Получена справка: usage, preconditions, postconditions, common_errors и help
- В ledger ничего не записано: вызов можно повторять свободно

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| INVALID_FIELD | tool не найден среди зарегистрированных инструментов | взять имя из списка доступных инструментов или из dom_describe |
| INVALID_ARGUMENTS | tool не передан или передан не строкой | вызывать dom_help({"tool": "dom_claim"}) |

### 3.20. `dom_init`

strict/lab: создать или проверить привязку work.

**Вызов:** `dom_init(external_work_ref, policy_digest)`

**Preconditions**

- Сервер запущен в strict/lab-профиле (в lite вместо него dom_start)
- external_work_ref и policy_digest взяты из launch-конфигурации без изменений

**Postconditions**

- Получен INITIALIZED и work_ref
- Привязка work проверена или создана; повторный вызов безопасен
- Старая схема базы мигрирована аддитивно

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| PROFILE_LITE | вызов в lite-профиле | использовать dom_start с запросом заказчика |
| POLICY_MISMATCH | policy_digest не совпадает с политикой сервера | взять policy_digest из launch-конфигурации; не вычислять самостоятельно |
| OMH_BINDING_UNVERIFIED | сервер запущен с --no-simulation | перезапустить без --no-simulation |
| REJECTED | request не объект или содержит лишние/недостающие ключи | передать ровно {"external_work_ref": "...", "policy_digest": "..."} |
| SCOPE_DENIED | work_ref вне grant этого запуска | использовать work_ref из launch-конфигурации |

### 3.21. `dom_resume`

Полная ориентация по ветке: snapshot + trace + sync + pending.

**Вызов:** `dom_resume(branch, limit?, after?)`

**Preconditions**

- Вызван dom_describe — известны коды веток
- Для следующей страницы есть after="branch:id" из предыдущего ответа

**Postconditions**

- Получен RESUMED со snapshot, trace, sync и pending
- pending.count точен, pending.items — только метаданные страницы
- Получен readiness (admitted_total, admitted_independent, pending, rejected, stale)

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| UNKNOWN_BRANCH | branch не объявлена в проекте | взять код из dom_describe.branches |
| NO_SUCH_BRANCH | ветка объявлена, но головы в базе нет | вызвать dom_describe и проверить state; при необходимости dom_start |
| REJECTED | limit вне 1..500 или after не формата "branch:id" | limit 1..500 (по умолчанию 50); after — cursor из предыдущей страницы |
| NOT_INITIALIZED | база не создана | сначала dom_start |
| CHAIN_BROKEN | цепочка событий не проходит проверку | остановить запись и сообщить пользователю |

### 3.22. `dom_submit`

strict: прислать restricted-JSON кандидат (ничего не адмитит).

**Вызов:** `dom_submit(payload_json)`

**Preconditions**

- Собран restricted-JSON пакет (operation claims|append, branch, items с body_b64, policy_digest, dependencies, expected_branch_revision, origin_version)
- Пакет сериализован в строку payload_json
- У launch есть capability submit

**Postconditions**

- Получен RECORDED_CANDIDATE с candidate_id и intent_digest — кандидат ждёт review
- Ничего не принято: дальше dom_candidate → dom_review → dom_admit
- При повторе с тем же idempotency_key вернётся тот же результат

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| REJECTED | payload_json не restricted JSON или не проходит схему | прочитать errors[] (path/code/fix) и исправить поля; без приведения типов |
| NOT_INITIALIZED | база не создана | сначала dom_start (lite) или dom_init (strict) |
| SCOPE_DENIED | branch/work вне grant этого запуска | использовать только свои branch и external_work_ref |
| INDEPENDENCE_NOT_ENFORCED | нет capability submit или отключена симуляция | запустить сервер с ролью, у которой есть submit |
| IDEMPOTENCY_MISMATCH | idempotency_key уже использован для другого содержимого | для нового содержимого взять свежий ключ |
| CHAIN_BROKEN | цепочка событий не проходит проверку | остановить запись и сообщить оператору |

**Также возможны:** `OWNER_AUTHORITY_REQUIRED`, `IDEMPOTENCY_BINDING_MISMATCH`, `IDEMPOTENCY_BINDING_UNKNOWN`, `IDEMPOTENCY_REVIEW_MISMATCH`, `POLICY_MISMATCH` — коды есть в коде, но не в `common_errors` этого инструмента; причина и решение в §2.

### 3.23. `dom_review`

strict: записать review кандидата по его digest.

**Вызов:** `dom_review(candidate_id, intent_digest, decision?, note?)`

**Preconditions**

- Кандидат прочитан через dom_candidate
- intent_digest скопирован из ответа dom_candidate/dom_submit точно
- Для decision=reject подготовлен note

**Postconditions**

- approve → DEMO_REVIEWED с review_id (нужен для dom_admit)
- reject → REVIEW_REJECTED с review_id; кандидат нельзя адмитить
- Review записан с reviewer_session этой сессии

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| DIGEST_MISMATCH | intent_digest не совпадает с сохранённым кандидатом | скопировать intent_digest без изменений из dom_candidate/dom_submit |
| NO_SUCH_CANDIDATE | candidate_id не найден или вне scope | взять id из dom_resume.pending или dom_extract |
| INVALID_FIELD | decision не approve/reject, reject без note или note длиннее 4000 | decision: approve\|reject; для reject — непустой note до 4000 символов |
| INDEPENDENCE_NOT_ENFORCED | нет capability review или под independent ревьюится свой кандидат | ревьюить должен другой principal; см. dom_describe.review_level |

### 3.24. `dom_candidate`

Прочитать кандидата перед review (read-only).

**Вызов:** `dom_candidate(candidate_id)`

**Preconditions**

- Известен candidate_id из dom_submit, dom_extract или pending в dom_resume
- У launch есть capability review

**Postconditions**

- Получены texts (до 4000 символов), metadata, anatomy, branch и intent_digest
- Получен state: AWAITING_REVIEW | REVIEWED | REJECTED | SUPERSEDED | ADMITTED
- Вызов read-only: ledger не меняется

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| NO_SUCH_CANDIDATE | candidate_id не найден или вне scope | взять id из dom_resume.pending или dom_extract |
| INVALID_FIELD | candidate_id пустой или не строка | передать непустой candidate_id строкой |
| INDEPENDENCE_NOT_ENFORCED | нет capability review или отключена симуляция | запустить сервер с ролью reviewer |

### 3.25. `dom_admit`

strict: атомарно адмитить одобренного кандидата.

**Вызов:** `dom_admit(candidate_id, review_id, idempotency_key, intent_digest)`

**Preconditions**

- Получен review_id одобряющего review (dom_review)
- candidate_id и intent_digest взяты без изменений
- Придуман стабильный idempotency_key для безопасного повтора

**Postconditions**

- Получен DEMO_ADMITTED: tx_id, branch, revision, claim_ids
- Claims, события, head ветки и outbox записаны одной транзакцией
- Повтор с тем же idempotency_key вернёт тот же результат

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| DIGEST_MISMATCH | intent_digest не совпадает с кандидатом | скопировать digest точно из dom_candidate |
| STALE_REVIEW | review не соответствует кандидату, политике или головам веток | перечитать dom_resume и выполнить review заново |
| REVIEW_REJECTED | последний review отклонил кандидата | не адмитить; записать исправленный claim и провести review заново |
| REVIEWER_SESSION_REQUIRED | у review нет reviewer_session (NULL) | выполнить review из доверенной сессии и адмитить его review_id |
| REVISION_CONFLICT | голова ветки сдвинулась после сборки кандидата | перечитать dom_resume и повторить submit/review/admit |
| INDEPENDENCE_NOT_ENFORCED | нет capability admit или ревьюер не совпадает с сессией адмита | адмитить должен тот же principal, что одобрил review |

**Также возможны:** `IDEMPOTENCY_BINDING_MISMATCH`, `IDEMPOTENCY_BINDING_UNKNOWN`, `IDEMPOTENCY_REVIEW_MISMATCH` — коды есть в коде, но не в `common_errors` этого инструмента; причина и решение в §2.

### 3.26. `dom_append`

strict: пакет append-событий как кандидат.

**Вызов:** `dom_append(payload_json)`

**Preconditions**

- Собран restricted-JSON пакет operation="append" с событиями (comment/link/property/applicability/archive)
- Известны claim_ref, branch и expected_branch_revision

**Postconditions**

- Получен RECORDED_CANDIDATE с candidate_id и intent_digest
- События ещё не приняты: нужны dom_review и dom_admit
- Для одиночной аннотации проще dom_annotate

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| REJECTED | payload_json не JSON, не объект или operation != append | передать restricted-JSON объект с operation="append" |
| SCOPE_DENIED | claim/branch вне grant | использовать только свои branch и claim_ref |
| INDEPENDENCE_NOT_ENFORCED | нет capability submit или отключена симуляция | запустить сервер с ролью, у которой есть submit |
| NOT_INITIALIZED | база не создана | сначала dom_start |
| CHAIN_BROKEN | цепочка событий не проходит проверку | остановить запись и сообщить оператору |

**Также возможны:** `OWNER_AUTHORITY_REQUIRED`, `IDEMPOTENCY_BINDING_MISMATCH`, `IDEMPOTENCY_BINDING_UNKNOWN`, `IDEMPOTENCY_REVIEW_MISMATCH`, `POLICY_MISMATCH` — коды есть в коде, но не в `common_errors` этого инструмента; причина и решение в §2.

### 3.27. `dom_query`

Снимок ветки на голове или на историческом revision.

**Вызов:** `dom_query(branch, revision?)`

**Preconditions**

- Вызван dom_describe — известны коды веток
- Для исторического среза известен revision в диапазоне 0..head

**Postconditions**

- Получен SNAPSHOT с claims (включая archived), history и conflicts
- revision указывает, какой срез прочитан (по умолчанию голова)
- Вызов read-only

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| UNKNOWN_BRANCH | branch не объявлена | взять код из dom_describe.branches |
| NO_SUCH_BRANCH | у объявленной ветки нет головы в базе | проверить state через dom_describe; при необходимости dom_start |
| REVISION_OUT_OF_RANGE | revision вне 0..head этой ветки | взять допустимый диапазон из hint или revision из dom_resume |
| NOT_INITIALIZED | база не создана | сначала dom_start |

### 3.28. `dom_snapshot`

Совместимый синоним `dom_query`.

**Вызов:** `dom_snapshot(branch, revision?)`

**Preconditions**

- Вызван dom_describe — известны коды веток
- Помнить, что dom_snapshot — совместимый синоним dom_query

**Postconditions**

- Получен SNAPSHOT с той же схемой, что у dom_query
- Прочитаны claims, history и conflicts на выбранном revision

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| UNKNOWN_BRANCH | branch не объявлена | взять код из dom_describe.branches |
| NO_SUCH_BRANCH | у ветки нет головы в базе | проверить state через dom_describe |
| REVISION_OUT_OF_RANGE | revision вне 0..head | использовать revision из hint/dom_resume |
| NOT_INITIALIZED | база не создана | сначала dom_start |

### 3.29. `dom_trace`

Coverage/stale/conflict/pending; readiness, не закрывает задачу владельца.

**Вызов:** `dom_trace(branch?)`

**Preconditions**

- Проект открыт (dom_start), иначе readiness будет UNKNOWN/NOT_READY
- Вызван dom_describe — известны ветки

**Postconditions**

- Получены readiness, ready_for_owner_review, residual, blockers, chain
- admitted_total и admitted_independent — разные числа
- owner_close_called всегда false: trace не закрывает задачу владельца

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| UNKNOWN_BRANCH | branch не объявлена | убрать branch (все ветки) или взять код из dom_describe |
| NO_SUCH_BRANCH | у ветки нет головы в базе | проверить state через dom_describe |
| NOT_INITIALIZED | база не создана | сначала dom_start |
| CHAIN_BROKEN | цепочка событий не проходит проверку | остановить запись и сообщить пользователю; trace покажет разрыв |

### 3.30. `dom_export`

Детерминированный YAML-экспорт ветки в фиксированный путь.

**Вызов:** `dom_export(branch, revision?)`

**Preconditions**

- Вызван dom_describe — известны коды веток
- Для исторического среза известен revision 0..head

**Postconditions**

- Получен EXPORTED с handle, sha256 и revision
- YAML записан по фиксированному пути внутри run-каталога
- При EXPORT_PENDING файл не подтверждён — повторить вызов

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| NO_SUCH_BRANCH | у ветки нет головы в базе | проверить state через dom_describe |
| REVISION_OUT_OF_RANGE | revision вне 0..head | взять допустимый revision из hint или dom_resume |
| UNKNOWN_BRANCH | branch не объявлена | взять код из dom_describe.branches |
| NOT_INITIALIZED | база не создана | сначала dom_start |

### 3.31. `dom_sync_status`

Статус outbox по intent (read-only).

**Вызов:** `dom_sync_status(branch)`

**Preconditions**

- Вызван dom_describe — известны коды веток
- Ветка существует (dom_resume/dom_query)

**Postconditions**

- Получен SYNC_STATUS с intents[] (pending/ambiguous/ack)
- Видно, что ещё не доставлено в OMH/YAML-экспорт
- Вызов read-only

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| NO_SUCH_BRANCH | у ветки нет головы в базе | проверить state через dom_describe |
| UNKNOWN_BRANCH | branch не объявлена | взять код из dom_describe.branches |
| NOT_INITIALIZED | база не создана | сначала dom_start |
| CHAIN_BROKEN | цепочка событий не проходит проверку | остановить запись и сообщить оператору |

### 3.32. `dom_sync`

Доставить outbox: YAML + симулированный OMH с read-back.

**Вызов:** `dom_sync(branch)`

**Preconditions**

- У launch есть capability admit
- Ветка существует и цепочка событий цела

**Postconditions**

- Получен SYNCED (всё доставлено) или SYNC_PENDING (есть незавершённые intent)
- Сделан YAML-экспорт и симулированная доставка в OMH с read-back
- Состояние доставки записано только этим инструментом

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| NO_SUCH_BRANCH | у ветки нет головы в базе | проверить state через dom_describe |
| INDEPENDENCE_NOT_ENFORCED | нет capability admit или отключена симуляция | запустить сервер с ролью, у которой есть admit |
| CHAIN_BROKEN | цепочка событий не проходит проверку | остановить запись и сообщить оператору |
| NOT_INITIALIZED | база не создана | сначала dom_start |

### 3.33. `dom_recall`

Состояние ветки после N-го события (active/archived claims).

**Вызов:** `dom_recall(branch, at_seq)`

**Preconditions**

- Вызван dom_describe — известны коды веток
- at_seq — номер события ветки из dom_resume/trace или 0

**Postconditions**

- Получен RECALL с active_claims, archived_claims и resolved_conflicts
- Получены related_branches и baseline_range
- pending_decisions может быть null: история кандидатов не восстанавливается

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| INVALID_FIELD | at_seq не целое или отрицательное | передать целое at_seq >= 0 |
| REVISION_OUT_OF_RANGE | at_seq больше головы ветки | взять at_seq из dom_resume/trace (0..head) |
| NO_SUCH_BRANCH | у ветки нет головы в базе | проверить state через dom_describe |
| UNKNOWN_BRANCH | branch не объявлена | взять код из dom_describe.branches |

### 3.34. `dom_claim_versions`

История версий одной логической claim.

**Вызов:** `dom_claim_versions(claim_id)`

**Preconditions**

- Известен claim_id любой версии (dom_find/dom_context)

**Postconditions**

- Получены versions[] от старой к новой с is_current и supersedes
- Получены logical_claim_id и resolution
- claim_id с is_current=true пригоден для dom_update

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| NO_SUCH_CLAIM | claim_id не существует или вне scope | найти id через dom_find |
| INVALID_FIELD | claim_id пустой или не строка | передать непустой claim_id строкой |
| NOT_INITIALIZED | база не создана | сначала dom_start |

### 3.35. `dom_diff`

Сравнить текущие claims двух веток по logical id.

**Вызов:** `dom_diff(branch_a, branch_b)`

**Preconditions**

- Вызван dom_describe — обе ветки объявлены и видны
- Для сравнения вариантов известны коды базовой и variant-ветки

**Postconditions**

- Получены only_a, only_b, changed и identical по logical claim id
- Получены comparable и common_ancestor
- Вызов read-only

**Common errors**

| Код | Причина | Решение |
|---|---|---|
| UNKNOWN_BRANCH | одна из веток не объявлена | взять коды из dom_describe.branches |
| NO_SUCH_BRANCH | у одной из веток нет головы в базе | проверить state через dom_describe |
| SCOPE_DENIED | ветка вне grant этого запуска | сравнивать только свои ветки |
| NOT_INITIALIZED | база не создана | сначала dom_start |

## 4. Что делать, если help пустой

`help` не может быть пустым по контракту: `public_dto.error_help()` подставляет по цепочке `help` инструмента из `HELP_DATA` → текст статуса из `_STATUS_HELP` → `DEFAULT_HELP`. Поэтому пустой `help` — **баг**, а не нормальное состояние.

1. **Не повторять вызов вслепую.** Отказ уже произошёл; повтор без изменения аргументов/состояния ничего не изменит.
2. **Fallback:** вызвать `dom_help({"tool": "<имя инструмента>"})` — он читает тот же `HELP_DATA` напрямую и не зависит от ledger.
3. **Если и `dom_help` пуст или инструмент не найден** — зафиксировать: имя инструмента, `status`, `field`, `hint`, профиль (`lite`/`strict`) и версию плагина (`VERSION`).
4. **Сообщить разработчику** — это баг: приложить полный JSON ответа и шаги воспроизведения.
5. **Продолжить работу без `help`:** `status` + `hint` из §1–§2 достаточны для восстановления; если причина неясна — `dom_describe` и проверка preconditions из §3.

Важно: `help` обрезается до 500 символов (`HELP_LIMIT`). Обрыв текста на полуслове — это лимит, а не пустой `help`; полная версия всегда доступна через `dom_help`.

### 4.1. Error help gate выключен по умолчанию

Error help gate (`help_gate.js`) выключен по умолчанию. Причина: `execute.after` не срабатывает для MCP-инструментов на OpenCode V2. Включить после live-пробы. `HELP_GATE_ENABLED=false`.

Покрытие самого gate — 12 инструментов из 35 (`dom_describe`, `dom_claim`, `dom_update`, `dom_annotate`, `dom_find`, `dom_context`, `dom_plan_status`, `dom_plan_ingest`, `dom_plan_tree`, `dom_task_claims`, `dom_skill_select`, `dom_help`); для остальных подсказку даёт только `dom_help`.

Практическое следствие: автоматическая строка `⚠️ <tool> failed. <help>` в инструкции (`dom-help`) не появляется, и `help` из ответа инструмента остаётся единственным источником подсказки. Это не ошибка и не повод повторять вызов — действуйте по §1–§3 и, если нужно, по `dom_help`. Включать gate (`helpGate: true` в опциях плагина) стоит только после того, как live-проба подтвердит, что хост действительно вызывает `execute.after` для MCP-инструментов и передаёт server-prefixed имя (`dom-memory_dom_claim`).

## Приложение A. Коды в коде, но не в HELP_DATA

Эти коды реально возвращаются сервером (`Denied(...)`, `_STATUS_HELP`, `_HELP`, транспорт), но не описаны ни в одном `common_errors` словаря `HELP_DATA.py`. Их стоит добавить в справку соответствующих инструментов.

| Код | Где возникает | Причина | Решение |
|---|---|---|---|
| INVALID_TEXT | dom_start, dom_claim, dom_update, dom_extract, dom_extract_submit | Текст не кодируется в UTF-8 (одиночные суррогаты). | Перекодировать материал в обычный UTF-8 и повторить. |
| UNKNOWN_TOOL | все (транспорт) | Имя инструмента не объявлено в SIGNATURE или недоступно в профиле запуска. | `dom_describe` → список инструментов профиля; в strict — `tools/list`. |
| MIGRATION_REQUIRED | все | Схема БД старше кода: нет нужной таблицы или колонки (в т.ч. после обновления плагина). | Один раз `dom_start` (lite) или `dom_init` (strict) — миграция аддитивная; повторить исходный вызов. |
| UPDATE_INCOMPLETE | dom_update | Новая версия принята, но старая ещё не помечена заменённой (сбой между шагами). | Повторить тот же `dom_update`: дубликата не будет, шаг завершится. |
| OWNER_AUTHORITY_REQUIRED | dom_submit, dom_append | Событие `exclude` прислано без capability `owner`. | Такие события пишет только owner-launch. |
| IDEMPOTENCY_BINDING_MISMATCH | dom_submit, dom_admit, dom_append | `idempotency_key` уже привязан к другому кандидату или ветке. | Взять новый ключ. |
| IDEMPOTENCY_BINDING_UNKNOWN | dom_submit, dom_admit, dom_append | Legacy-строка идемпотентности блокирует этот ключ. | Взять новый ключ. |
| IDEMPOTENCY_REVIEW_MISMATCH | dom_admit | `idempotency_key` использован с другим review. | Использовать исходный `review_id` либо новый ключ. |
| DELIVERY_ENGINE_ONLY | — (внутренний compatibility-путь) | Попытка записать состояние доставки вручную, минуя движок. | Состояние пишет только `dom_sync`; читать — `dom_sync_status`. |
| PLAN_TOO_WIDE | dom_plan_ingest | План шире лимитов: 676 верхних пунктов, 35 подпунктов на пункт, 2000 всего. | Сократить план или разбить работу на этапы. |
| STALE_PROJECTION_SNAPSHOT | dom_plan_ingest | `source_revision` старше текущей проекции плана. | Переслать план с актуальным `source_revision` из `dom_plan_status`. |
| PROPOSAL_CONFLICT | dom_distillate | У текущей версии тот же нормализованный text, но другие anchors. | Отозвать версию (`withdraw`) или передать исходные anchors. |
| NO_IMPROVEMENT | dom_distillate | Кандидат совпадает с эффективной версией. | Предложить реально изменённый text или оставить эффективную версию. |
| REVIEW_FAILED | dom_distillate | Последний review этой версии провалился. | Исправить text/anchors и предложить версию заново. |
| REVIEW_STALE | dom_distillate | Review не соответствует изменившимся text/anchors. | Выполнить `validate` заново, затем `accept`. |
| DISTILLATE_LOOP | dom_distillate | Три неудачных review — цепочка остановлена, сброса нет. | Спросить пользователя; новый источник начнёт новую цепочку. |
| STORAGE_READ_ONLY | все | База доступна только на чтение (SQLITE_READONLY). | Проверить права на файл и каталог; сообщить оператору. |
| STORAGE_CONSTRAINT | все | Сработало ограничение неизменяемости или целостности (SQLITE_CONSTRAINT). | Не обходить правило SQL: записать изменение новым claim/событием. |
| STORAGE_UNAVAILABLE | все | Иная ошибка SQLite: базу не удалось использовать. | Повторить один раз; если повторяется — сообщить оператору, базу не пересоздавать. |
| INTERNAL_ERROR | все | Внутренний сбой сервера; детали наружу не отдаются (fail-closed). | Повторить один раз; при повторе — сообщить оператору (`error_type` есть в логе сервера). |
| PUBLIC_PROJECTION_INVALID | все | Сервер отказал в сериализации собственного ответа (fail-closed проекция). | Не повторять вслепую; сообщить оператору имя инструмента и ответ. |

Дополнительно сканирование `mockup/` находит служебные коды, которые **не являются отказами инструментов** и потому не входят в справочник:

- `DB_PATH_MISMATCH` — диагностический статус `health`/`db` внутри ответа `dom_describe` (один и тот же файл БД виден по разным путям), не отказ;
- `EXPORT_PENDING` — статус `dom_export`, когда файл не удалось подтвердить; нужно просто повторить вызов;
- `INVALID_JSON`, `INVALID_TYPE`, `INVALID_KEY`, `INVALID_NUMBER`, `INVALID_UNICODE`, `REQUIRED`, `INVALID_ENCODING`, `DUPLICATE_KEY` — коды внутри `errors[]` ответа `REJECTED` (restricted-JSON валидатор), а не самостоятельные статусы;
- `EVENT_CHAIN_BROKEN`, `EVENT_HASH_MISMATCH`, `CLAIM_ROW_MISMATCH` и подобные — внутренние причины проверки цепочки, наружу выходит только `CHAIN_BROKEN`.

## Приложение B. Статусы, которые не являются ошибками

При разборе ответа не путать успешные и переходные статусы с отказами:

| Группа | Статусы |
|---|---|
| Проект | `DESCRIBED`, `STARTED`, `ALREADY_STARTED`, `INITIALIZED` |
| Claims | `CLAIM_ADMITTED`, `ALREADY_RECORDED`, `CLAIM_UPDATED`, `CLAIM_FORKED`, `FOUND`, `CONTEXT` |
| Кандидаты | `RECORDED_CANDIDATE`, `DEMO_REVIEWED`, `DEMO_ADMITTED`, `EXTRACTED`, `EXTRACTION_REQUEST`, `CANDIDATE` |
| План | `PLAN_INGESTED`, `PLAN_UNCHANGED`, `PLAN_TREE`, `PLAN_STATUS`, `TASK_CLAIMS`, `TASK_GRAPH` |
| Чтение | `RESUMED`, `SNAPSHOT`, `TRACE`, `RECALL`, `CLAIM_VERSIONS`, `DIFF` |
| Доставка/экспорт | `SYNCED`, `SYNC_PENDING`, `EXPORTED`, `EXPORT_PENDING`, `SYNC_STATUS` |
| Дистиллят | `DISTILLATE_SHOWN`, `PROPOSED`, `VALIDATED`, `ACCEPTED`, `WITHDRAWN`, `DISPUTED`, `RESOLVED` |
| Прочее | `ANNOTATED`, `RECONCILED`, `SKILLS_SELECTED`, `OK`, `SUPERSEDED`, `AWAITING_REVIEW`, `REVIEWED`, `ADMITTED` |

`REVIEW_FAILED` и `REVIEW_REJECTED` — **не успех**: первый означает провал validate дистиллята, второй — отклонение кандидата review; оба описаны в §2.

## Приложение C. FAQ

### «Почему я вижу 169 скилов в промпте?»

**Короткий ответ:** OpenCode загружает весь каталог skills в каждый промпт автоматически. Для фильтрации используй `--agent dom-router` с permissions.

**Подробно.** `core/skill-guidance` — core-блок хоста, а не ответ DOM-инструмента; поэтому в ответах нет ни `status`, ни `hint`, и это не ошибка. Плагин не может ни переписать, ни удалить этот блок: `ctx.skill.transform` видит только 17 plugin-visible skills (E4), а `dom.skill-queue` лишь добавляет строку с выбранными skills, не убирая каталог. В текущей сборке каталог занимает ~70 882 chars на 169 skills.

Единственный поддерживаемый способ убрать каталог — permissions-профиль агента. В `opencode/opencode.dev.json` объявлен `agents.dom-router`:

```json
"permissions": [
  {"action": "skill", "resource": "*", "effect": "deny"},
  {"action": "skill", "resource": "sqlite-migration", "effect": "allow"},
  {"action": "skill", "resource": "dom-memory", "effect": "allow"}
]
```

Обратите внимание: `resource` — это whole-value wildcard (`*`, `?`), а не regex. Альтернация `a|b` не работает и не разрешает ни один skill, поэтому allow-list — отдельное правило на каждый id. В репозитории allow-правило сейчас записано одной строкой через `|`; фактически это «deny all» (каталог скрыт полностью, 0 skills), и до исправления конфига профиль работает именно так.

Запуск:

```powershell
powershell -File tools/run_dev_session.ps1 -Prompt "Reply with exactly: ok" --agent dom-router
```

E6 (fresh session, 2026-11-24): `deny *` + одна разрешённая запись даёт 69 276 → 575 chars (−99,2%). Live-замер `dom-router` 2026-11-28: блок `core/skill-guidance` отсутствует целиком, system 86 644 → 13 854 chars. Проверка — по свежему снапшоту `.dom-memory/observability/prompts/<session_id>/<ts>.json`: поле `agent` = `"dom-router"`, запрещённых skills в тексте нет. Если allow-правила записаны по одному id, в каталоге остаются только они и его размер — сотни символов.

Важно: `deny` запрещает и загрузку skill по id, поэтому `allow` должен покрывать skills, нужные шагам плана. Сейчас allow-list статический (правила в конфиге) и не связан с `dom.skill-queue`; динамический режим включается опцией `options.dynamicPermissions` и ещё не подтверждён E2E-замером каталога. Порядок запуска и проверки — `docs/development.md` §7, полный разбор — `docs/audit/root-cause-analysis.md`.
