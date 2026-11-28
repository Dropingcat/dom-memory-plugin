# T1 — Полный цикл тестирования v0.3.1 (отчёт тестера)

**Репозиторий:** `E:\opencode_harness_portable\projects\opencode-dom-memory-plugin`
**Коммит:** `b7d0729` (тэг `v0.3.1`)
**Код НЕ менялся.** Записи только в `C:\Temp\opencode\`.
**Модель сессий:** `polza/deepseek/deepseek-v4.1-flash-1017`
**Конфиг:** `opencode/opencode.dev.json` (`OPENCODE_CONFIG`, `--standalone`)
**CLI:** `...\resources\opencode-cli.exe`
**Монитор:** `127.0.0.1:8772`, токен из `.dom-memory/observability/monitor.token`

Методы:

| Метод | Что покрывает | Файлы |
|---|---|---|
| Прямой `dispatch` + `public_dto.project_tool` (тот же путь, что `call_tool` в `server.py`) | ЧЛ-1, ЧЛ-2, ЧЛ-3 | `C:\Temp\opencode\t1_direct.py`, `t1_probe2.py`, `t1_bounds.py`, `t1_help_cross.py`, `t1_final.py`, `t1_policy.py` |
| Реальный MCP stdio (`mcp.ClientSession` → `dom_server.py`), с перехватом stderr | ЧЛ-2/3/5 на транспорте, 5.4 | `C:\Temp\opencode\t1_mcp_stdio.py` |
| Реальный `queue_producer.py` в отдельных процессах + `stepFromBrief` (node) | ЧЛ-4 | `C:\Temp\opencode\t1_producer.mjs`, `t1_cap_parse.mjs`, `t1_det_empty.py` |
| 7 живых сессий CLI + снапшоты промпта + `hooks-*.jsonl` + монитор | ЧЛ-5, вопросы оркестратора | `C:\Temp\opencode\s1..s7*.json`, `t1_verify_snap.py`, `t1_new_hooks.py` |

Живые сессии (все `exit 0`):

| # | что делала | sessionID |
|---|---|---|
| S1 | `dom_describe` | `ses_ddeb32f1bffezWzy4UUwDnsx5J` |
| S2 | `dom_skill_select` + `dom_help` + `dom_claim` (маркер `T1-MARKER-7f3c9a`) | `ses_ddeb0fa38ffeVeowZQPZ2C6bG9` |
| S3 | `dom_describe` + `dom_skill_select` + `dom_help` + `dom_find` (кросс-сессия) | `ses_ddeb05277ffeZhqxD4wstPchIn` |
| S4 | самоотчёт модели о системном промпте | `ses_ddeafd3d7ffe4095F6Dnh9ZR2Z` |
| S5, S6 | «Reply with exactly: ok» | `ses_ddeaf7016ffeXjIKF6LJU2kee2`, `ses_ddeaf1f30ffevO8x4ox2zp8MBM` |
| S7 | `--agent explore`, подсчёт .py | `ses_ddeaecd7bffeuM6h6AxH6RjcFP` |

---

## 1. Сводка

| ЧЛ | Тестов ядровых | Доп. тестов | Отказов (ядро) | Отказов (всего) | Минимум по контракту | Вердикт |
|---|---|---|---|---|---|---|
| ЧЛ-1 Skill router | 10 | 6 | 4 (1.1, 1.6, 1.7, 1.10) | 4 | 3 | **PASS критерия** |
| ЧЛ-2 DOM tools | 10 | 7 | 5 (2.2, 2.4, 2.5, 2.6, 2.7) | 6 | 3 | **PASS критерия** |
| ЧЛ-3 Help | 7 | 3 | **0** | 3 (3.8, 3.10, 3.11) | 3 | **PASS только с доп. тестами** — ядро 3.1–3.7 зелёное |
| ЧЛ-4 τ-гейт + producer | 8 | 3 | 2 (4.2, 4.6) | 4 | 2 (§4) / 3 (постановка) | **PASS критерия §4** |
| ЧЛ-5 Интеграция | 6 | 1 | 1 (5.4) | 2 (5.4, 5.7) | 1 (§4) / 3 (постановка) | **PASS критерия §4** |
| **Итого** | **41** | **20** | **12** | **19** | — | — |

Честная оговорка: постановка требовала «минимум 3 отказа в каждом чек-листе». ЧЛ-3 и ЧЛ-5 в ядре дают меньше; я не стал выдумывать отказы, а расширил чек-листы доп. тестами,
которые проверяют **содержание** (help против фактического поведения, доставку блока в промпт), и нашёл там реальные дефекты. Там, где доп. тест тоже зелёный — так и написано.

---

## 2. ЧЛ-1 — Skill router pipeline (`dom_skill_select`)

| # | Вход | Ожидание | Факт | Вердикт |
|---|---|---|---|---|
| 1.1 | `step={}` | `INVALID_ARGUMENTS` + help | `SKILLS_SELECTED`, help нет, блок «no-signal» | **FAIL** |
| 1.2 | `title` 501 | `INVALID_FIELD(step.title)` + help | `INVALID_FIELD`, help 101 симв., field=`step.title` | PASS |
| 1.3 | `title` 500 | `SKILLS_SELECTED` | `SKILLS_SELECTED`, блок 263 симв. | PASS |
| 1.4 | `context` 2001 | `INVALID_FIELD(step.context)` + help | `INVALID_FIELD`, help 118 симв. | PASS |
| 1.5 | `cap:"string"` | `INVALID_ARGUMENTS` либо приведение | приведение к `("string",)`, `SKILLS_SELECTED` (контрактом разрешено) | PASS |
| 1.6 | `cap:[]` | `gaps: not-declared` в блоке | блок: `gaps: not-declared` ✔, **но JSON-поле `gaps` = дамп индекса (21 запись, «… and 135 more»)** | **FAIL** (см. F10) |
| 1.7 | `queue:"bad"` | `INVALID_ARGUMENTS` + help | `INVALID_ARGUMENTS` + help ✔; **`queue:[1,2,3]` → `INTERNAL_ERROR` (TypeError)** | **FAIL** (см. F6) |
| 1.8 | 100 идентичных вызовов | все одинаковы | 1 уникальный результат из 100 | PASS |
| 1.9 | `index_digest:"bad"` | принят | `SKILLS_SELECTED`, digest использован как есть | PASS |
| 1.10 | `<script>alert('xss')</script>` | принят, без инъекции | принят; **title дословно уходит в системный блок** | **FAIL (minor)** (см. F13) |

Доп. (ЧЛ-1): `step` отсутствует → `INVALID_ARGUMENTS`+help ✔; `step:null` → transport `INVALID_ARGUMENTS` ✔;
`title:null` / `title:0` / `title:[]` → `SKILLS_SELECTED` (тот же дефект, что 1.1); `plan_version:-5` и `queue` из 200 шагов → тихо приняты.

---

## 3. ЧЛ-2 — DOM tools (`dom_claim`)

| # | Вход | Ожидание | Факт | Вердикт |
|---|---|---|---|---|
| 2.1 | `branch:""` / без branch | `INVALID_FIELD` + help | `INVALID_FIELD`, field=`branch`, help 116 | PASS |
| 2.2 | `branch:"NONEXISTENT"` | `INVALID_FIELD` + help | **`UNKNOWN_BRANCH`, `field` отсутствует** | **FAIL** (см. F11, F12) |
| 2.3 | `text` 2 000 000 симв. | `TEXT_TOO_LARGE` + help | `TEXT_TOO_LARGE`, help 130; граница 1 MiB точная (1 MiB — принят, +1 байт — отказ) | PASS |
| 2.4 | `type:"invalid"` | `INVALID_FIELD` | **`UNKNOWN_TYPE`** | **FAIL** (см. F11) |
| 2.5 | `type:""` | `INVALID_FIELD` | **`UNKNOWN_TYPE`** | **FAIL** (см. F11) |
| 2.6 | `basis:["<несуществующий id>"]` (эквивалент self-basis: id выдаёт сервер) | `INVALID_FIELD` + help | **`CLAIM_ADMITTED`**, ссылка сохранена и в `dom_context` указывает в пустоту | **FAIL** (см. F4) |
| 2.7 | `plan_task:"BAD-PLAN-TASK"` | `INVALID_FIELD` + help | **`TASK_NOT_FOUND`** | **FAIL** (см. F11) |
| 2.8 | `idempotency_key:""` | принят | `CLAIM_ADMITTED` | PASS |
| 2.9 | двойной idempotency | второй = `ALREADY_RECORDED` | `ALREADY_RECORDED`, тот же `claim_id` | PASS |
| 2.10 | `dom_help(tool:"")` | `INVALID_ARGUMENTS` + help | `INVALID_ARGUMENTS`, field=`tool`, help 152 | PASS |

Доп. (ЧЛ-2): `basis` из 64 несуществующих id → `CLAIM_ADMITTED`; `basis` из 65 → `INVALID_FIELD` ✔;
`idempotency_key` 5000 симв. → принят без границы; неизвестный ключ → `INVALID_ARGUMENTS`+help ✔;
`dom_find(limit=0/-1/1e9)` → `INVALID_FIELD` ✔, `limit="10"`/`true` → `INVALID_ARGUMENTS` ✔.
**2.11 (доп.):** `dom_update` без `expected_current` в режиме `auto` → `CLAIM_UPDATED` (слепая перезапись), см. F9.

---

## 4. ЧЛ-3 — Help система

| # | Тест | Ожидание | Факт | Вердикт |
|---|---|---|---|---|
| 3.1 | `dom_help` для всех 35 | `OK` + usage/pre/post/help | 35/35 `OK` | PASS (по букве) |
| 3.2 | `dom_help("BAD")` | `INVALID_FIELD` + help | `INVALID_FIELD`, help 179 | PASS |
| 3.3 | `dom_help({})` | `INVALID_ARGUMENTS` + help (BUG-1) | `INVALID_ARGUMENTS`, help 152 | PASS |
| 3.4 | каждый help ≤ 500 | — | max 424 симв. | PASS |
| 3.5 | каждый help содержит «Тебе НУЖНО:» | — | 35/35 | PASS |
| 3.6 | 50 кодов из `common_errors` в коде | найдены | 50/50 найдены в `mockup/*.py` | PASS |
| 3.7 | `describe()` < 15000 | — | 10 841…11 734 симв. локально; 11 509 при 64 ветках, 13 329 при 64 ветках по 32 симв. → запас ≥11 % | PASS |
| 3.8 (доп.) | help только для доступных инструментов | — | **4 «фантомных» инструмента** (`dom_init`, `dom_snapshot`, `dom_append`, `dom_submit`) имеют полный help, но транспорт отвечает `UNKNOWN_TOOL` | **FAIL** (см. F7) |
| 3.10 (доп.) | postconditions help выполнимы | — | `dom_update(mode=fork)` обещает `CLAIM_FORKED`, фактически всегда `FORK_NOT_ALLOWED` | **FAIL** (см. F8) |
| 3.11 (доп.) | preconditions help = реальные гарантии | — | «read-before-write» в help, но без `expected_current` запись проходит молча | **FAIL** (см. F9) |

Дополнительно проверено и **зелёное**: `usage` в HELP_DATA совпадает с `SIGNATURE` по всем 35 инструментам (0 расхождений),
каждый код имеет `cause`+`fix`, `dom_help` не зависит от профиля, `describe` отдаёт `read_gate`.

---

## 5. ЧЛ-4 — τ-гейт + queue producer

| # | Вход | Ожидание | Факт | Вердикт |
|---|---|---|---|---|
| 4.1 | первый вызов producer | `fingerprint` не null | `OK`, fp `f4841b0cef61…`, 974 ms cold | PASS |
| 4.2 | второй вызов того же шага | `[τ-gate] idle (1)` в stderr | **в producer гейта нет**: stderr пуст, блок пересчитан и выдан байт-в-байт | **FAIL** (см. F3) |
| 4.3 | `cap:["sqlite"]` → `["testing"]` | fingerprint изменился | `f4841b0cef61` → `838a715ea1a4` | PASS |
| 4.4 | `--step "not json"` | `status: ERROR` | `{"status":"ERROR","error":"invalid step JSON"}`, exit 0 | PASS |
| 4.5 | `--timeout 0.001` | `ERROR` или timeout | флага `--timeout` не существует; argparse → `{"status":"ERROR","error":"invalid arguments"}`, exit 0 | PASS (с оговоркой: опции нет) |
| 4.6 | `CAP: sqlite,migration` в brief | `cap=["sqlite","migration"]` | базовая форма ✔; **любая другая форма молча даёт `cap=[]`** | **FAIL** (см. F2) |
| 4.7 | brief без CAP | `cap=[]` | `cap=[]` | PASS |
| 4.8 | 3 прогона одного шага | одинаковы | 3/3 идентичны (в т.ч. `cap=[]`) | PASS |
| 4.9 (доп.) | `gap_skills_count` | число несelected скиллов | **21 при фактических 155+** (`len()` обрезанного списка) | **FAIL** (см. F14) |
| 4.10 (доп.) | CAP реально эмитится хуками | — | **строка `CAP:` не встречается ни в одном `opencode/hooks/*.py`** → в продакшене `cap` всегда `[]` | **FAIL** (см. F2) |

Факты по CAP-парсеру (`index.js:191`, regex `/\bCAP:\s*([\w,-]+)\s*$/i`):

```
CAP: sqlite,migration                  -> ["sqlite","migration"]   ok
CAP: sqlite, migration                 -> []                       (пробел после запятой)
CAP: sqlite, migration,                -> []                       (висячая запятая)
CAP: sqlite,migration.                 -> []                       (точка)
CAP: sqlite,migration [pending]        -> []                       (CAP не в конце строки)
(CAP: sqlite,migration)                -> []                       (скобки)
CAP: миграция                          -> []                       (кириллица: \w — ASCII)
CAP: SQLite,Migration                  -> ["SQLite","Migration"]   (регистр не нормализуется)
```

Кэш: холодный 974 ms → тёплый 277 ms, `--no-cache` 974 ms; кэш не влияет на байты (проверено).

---

## 6. ЧЛ-5 — Интеграция (7 живых сессий, все exit 0)

| # | Тест | Факт | Вердикт |
|---|---|---|---|
| 5.1 | describe + skill_select + help в одной сессии | S3: `DESCRIBED`, `SKILLS_SELECTED`, `OK`, exit 0 | PASS |
| 5.2 | 5 последовательных сессий | 7/7 exit 0; `integrity: CHAIN_OK`; 207 claims | PASS |
| 5.3 | монитор после сессий | `GET /api/hooks` → 200, 189 items, 0 ошибок, 21 запись за сегодня из 7 сессий | PASS (монитор пришлось запустить вручную — см. «Наблюдения») |
| 5.4 | каждый отказ имеет help + log | все отказы инструментов: help непустой и русский; stderr `[dom-mockup] {…}` есть всегда. **Но:** skill-queue логируется как `injected`, а в промпте его нет; `INTERNAL_ERROR` для `queue:[1,2,3]` советует «повторить вызов» | **FAIL** (см. F1, F6) |
| 5.5 | cross-session: `dom_find` находит claim из другой сессии | S2 создала `T1-MARKER-7f3c9a` → S3 нашла `claim_id 741cd945-7eb1-49e1-8493-4f55d94f6bc4` | PASS |
| 5.6 | `--agent explore` | exit 0; `PYFILES=34` (33 плоских + `fixtures/__init__.py`) | PASS |
| 5.7 (доп.) | инструменты DOM находимы через `search` | `search({query:"dom"})` → `{"items":[],"remaining":0}`; модель дважды промахнулась (`tools.dom_describe`, `tools["dom-memory.dom_describe"]`) → «Unknown tool» без help | **FAIL (minor)** (см. F15) |

---

## 7. Отказы — детально

### F1 (CRITICAL). `dom.skill-queue` не попадает в промпт, хотя лог пишет `injected`
**Где:** `opencode/index.js:439-451` (ветка `ctx.session?.instructions?.entry.put`) и `index.js:596-599`.
**Вход:** любая живая сессия (7/7 сегодня, 124 записи `context` за историю; дельта блоков = 1 в 123 из них).
**Факт:**

```jsonc
// hooks-2026-11-28-*.jsonl, контекст-хук
{"skill_queue":{"status":"injected","skill_id":"skill_search","idle":0,"fingerprint":"645ae246ee9cee8f"},
 "context":{"system_blocks_before":5,"system_blocks_after":6},
 "injected":{"notes":1,"chars":2785}}
```
* `system_blocks_after - before = 1` **всегда** (123 из 124 записей `context` за всю историю, 7/7 сегодня;
  единственная запись с дельтой 0 — хук без заметок) — добавлен только блок плана.
* `skillQueueFor` вызывается **до** push плана, значит при `event.system.push` дельта была бы 2.
  Следовательно выбран `entry.put(...)` — и этот канал до промпта не доходит.
* Снапшот `2026-11-28T08-37-05-300Z-a15b16c3.json`: 6 блоков `[1471, 119, 79981, 238, 2000, 2785]`,
  `has 'use: skill_' = False`, `has 'gaps:' = False`, `has 'dom.skill-queue' = False`.
* Самоотчёт модели в S4: `{"dom_index_block_present":true,"lines_starting_with_step":0,"lines_starting_with_gap":0,"has_use_skill":false,"has_dom_skill_queue":false}`.
* Во всех 13 «injected» записях `skill_id = skill_search` — ни один реальный скилл ни разу не был «внедрён».

**Ожидание:** блок скиллов в системном промпте (или честный `status:"error"/"skipped"`, но не `injected`).
**Почему это критично:** это единственная новая фича 0.3.1; она не работает, а наблюдаемость рапортует успех.
**Как чинить:** после `entry.put` проверять факт доставки (или писать в `event.system` и удалять дубль), а статус ставить по факту.

### F2 (CRITICAL). CAP никогда не эмитится планом, а парсер теряет валидные формы
**Где:** `opencode/index.js:191`; `grep "CAP:" opencode/hooks/*.py` → 0 совпадений.
**Факт:** ни один из планировочных хуков не пишет `CAP:` в строку `CURRENT`, поэтому `stepFromBrief`
всегда возвращает `cap: []` → producer всегда отдаёт `gaps: not-declared` и `use: skill_search`.
Это подтверждается 13/13 `skill_id=skill_search`. Плюс парсер принимает **только** `CAP: a,b` без пробелов/точек/кириллицы (таблица выше).
**Ожидание (4.6):** `cap=["sqlite","migration"]` для любой разумной записи; (4.10) хотя бы один хук реально выводит CAP.
**Следствие:** весь «skill router» в проде деградирует в `skill_search`.

### F3 (MAJOR). В `queue_producer.py` нет τ-гейта; idle-лог невидим
**Где:** `opencode/scripts/queue_producer.py` (слово `idle` — 0 раз), `opencode/index.js:439` (`console.log`).
**Факт:** producer идемпотентно возвращает тот же блок при каждом вызове (это ок), но **гейт живёт только в JS**,
а его сообщение идёт через `console.log`, который в stderr сессии не попадает (`grep "τ-gate" *.err` → пусто,
при этом в `hooks-*.jsonl` `idle` растёт 1→2→3→4). Контрактный тест 4.2 в текущей архитектуре невыполним.
**Ожидание:** `[τ-gate] idle (1)` в stderr (или явное переописание контракта).

### F4 (MAJOR). `basis` не проверяется на существование — «висячие» ссылки
**Где:** `opencode/mockup/claim_entry.py:531` (`anatomy_errors` проверяет только форму) — сравните с `:579` (`refines` проверяется!).
**Вход:** `dom_claim({branch:"AA", type:"text", text:"B", basis:["9c101a49-6d74-4f54-8137-4e6c97de8b83"]})`.
**Факт:** `CLAIM_ADMITTED`; `dom_context(claim_id)` → `status: CONTEXT`, `basis` содержит несуществующий id,
`upstream/chain` пусты; `dom_trace` → `TRACE` без предупреждений. 64 таких id — тоже принято.
**Почему важно:** неизменяемая запись с неразрешимой родословной; в manual-режиме это маскируется `GATE_NO_READ`.
**Ожидание (2.6):** `INVALID_FIELD(field="basis")` + help.

### F5 (MAJOR). `dom_skill_select({step:{}})` возвращает успех
**Где:** `server.py:388-414` (нет проверки пустого title), `skill_select.py:157-196` (`title or ''`).
**Факт:** `SKILLS_SELECTED`, блок `step: [no-cap | no-category] / use: skill_search …`; то же для `title:null|0|[]`.
**Ожидание (1.1):** `INVALID_ARGUMENTS` + help. Сейчас модель получает «успех» на пустом шаге и не узнаёт, что шаг не распознан.

### F6 (MAJOR). `queue` с нестроковыми элементами → `INTERNAL_ERROR`
**Где:** `server.py:284` (`strict_arguments` проверяет только тип контейнера), `skill_select.py:172` (`raise TypeError`), `server.py:630` (fail-closed).
**Вход:** `dom_skill_select({step:{title:"t"}, queue:[1,2,3]})` (через реальный MCP stdio).
**Факт:** `isError:true`, `status:"INTERNAL_ERROR"`, hint «Retry once, then tell the operator», в логе `error_type: TypeError`.
**Ожидание (1.7):** `INVALID_ARGUMENTS` + help с указанием элемента.
**Почему важно:** детерминированная ошибка советует повтор — повтор никогда не поможет; причина скрыта от модели.

### F7 (MAJOR). Help документирует 4 инструмента, которых нет
**Где:** `HELP_DATA` = 35 записей; `tools_for("lite")` = 31 инструмент.
**Фантомы:** `dom_init`, `dom_snapshot`, `dom_append`, `dom_submit`.
**Факт:**
```
dom_help({tool:"dom_snapshot"}) -> status OK, usage "dom_snapshot(branch, revision?)",
                                   help "…dom_snapshot — синоним dom_query с той же схемой…"
dom_snapshot({branch:"main"})   -> isError:true, status UNKNOWN_TOOL,
                                   hint "Unknown tool in this profile",
                                   help  — ТА ЖЕ фраза про «синоним dom_query»
```
**Ожидание (3.8):** help либо скрыт в профиле, либо честно говорит «недоступен в lite»; отказ `UNKNOWN_TOOL`
не должен переиспользовать help несуществующего инструмента.

### F8 (MINOR). Help обещает `CLAIM_FORKED`, которого нельзя получить
**Где:** `HELP_DATA["dom_update"]["postconditions"][2]`, `claim_entry.py:949`.
**Факт:** `dom_update(..., mode:"fork", expected_current:<свой id>)` → `FORK_NOT_ALLOWED`
(«The current version is your own»). В единственном продуктовом профиле (`lite`, один владелец) все версии «свои»,
значит ветка `CLAIM_FORKED` недостижима, но описана как обычный исход.

### F9 (MAJOR). Read-before-write в `auto` — опция, а не гарантия
**Где:** `claim_entry.py:911-913`: `expected_current is None and mode == 'auto'` → `_plain_update` (без проверки версии).
**Факт:** `dom_update({claim_id, text, reason})` → `CLAIM_UPDATED` (слепая перезапись).
При этом `HELP_DATA["dom_update"]["preconditions"]` говорит «известен claim_id прочитанной версии (expected_current)»,
а `postconditions` — «При расхождении вернулся CONFLICT_READ_REQUIRED». В `manual/independent` защищает `_gate_write` (GATE_NO_READ), в `auto` — ничто.
**Ожидание:** либо `CONFLICT_READ_REQUIRED` при отсутствии `expected_current`, либо честный текст help.
Это классическая «тихая деградация»: документация обещает инвариант, которого в дефолтном режиме нет.

### F10 (MAJOR). Поле `gaps` в ответе `dom_skill_select` — дамп всего индекса
**Где:** `skill_select.py:324` (`'gaps': gaps` — обрезанный список ВСЕХ несelected скиллов) vs `:306` (`block_gaps`).
**Факт при `cap:[]`:**
```
block: … gaps: not-declared (step has no capability declaration)
gaps: [20 названий скиллов, "... and 135 more"]
```
То есть блок говорит «требований нет», а JSON-поле отдаёт 21 запись, читающуюся как «не хватает 155 скиллов».
Плюс у producer поле `gaps` означает совсем другое (`missing_caps`, `queue_producer.py:196`). Одно имя — две семантики.

### F11 (MINOR, но контрактное). Класс статуса не тот, что в спеке
`branch:"NONEXISTENT"` → `UNKNOWN_BRANCH`; `type:"invalid"|""` → `UNKNOWN_TYPE`; `plan_task:"BAD…"` → `TASK_NOT_FOUND`.
Контракт 2.2/2.4/2.5/2.7 ждёт `INVALID_FIELD`. Внутри `HELP_DATA` эти коды описаны, поэтому это расхождение
**контракта и реализации**, а не «недокументированный» код; help присутствует во всех случаях (116/157/152 симв.).
Решение за архитектором: либо привести коды к спеке, либо переписать чек-лист.

### F12 (MINOR). `UNKNOWN_BRANCH` без `field`
**Где:** `admission.py:336` — `raise Denied('UNKNOWN_BRANCH', branches=sorted(branches))` (нет `field='branch'`).
**Факт:** в ответе `field: null`, хотя все соседние отказы называют поле. Модели труднее себя исправить.

### F13 (MINOR). Title без экранирования уходит в системный блок
**Где:** `skill_render.render_current` → `_title` (только схлопывание пробелов).
**Факт:** `step.title = "<script>alert('xss')</script>"` → первая строка блока `step: <script>alert('xss')</script> [no-cap…]`,
то есть произвольный текст шага попадает в системный промпт. XSS в промпте невозможен, но это канал
prompt-injection (заголовок приходит из плана/брифа). Минимум — маркировать блок как данные.

### F14 (MINOR). `gap_skills_count` — длина обрезанного списка
**Где:** `queue_producer.py:193` (`len(result["gaps"])`), docstring `:27` («count of indexed skills that were not selected»).
**Факт:** `21`, тогда как в самом блоке `"... and 135 more"` → реально ≥155.

### F15 (MINOR, интеграция). DOM-инструменты не находятся через `search`
**Факт:** `await search({query:"dom"})` → `{"items":[],"remaining":0}`; модель делает 1–2 промаха
(«Unknown tool 'dom_describe'», «Unknown tool 'dom-memory.dom_describe'») до `tools["dom-memory"].dom_describe`.
Отказ Code Mode не содержит help и не упоминает `tools["dom-memory"]`. Затрагивает 2 из 7 сессий.

### F16 (MINOR). Крупный `--step` не запускает producer и не оставляет диагностики
**Факт:** `spawnSync` c `--step` из 500 000 символов → `error.code = ENAMETOOLONG`, `status = null`, stdout пуст.
`index.js` в этом случае делает `console.warn` (невидим в stderr), очередь просто выключается.
Практический предел из брифа (~1.2 КБ) не достигает лимита, но API не защищён.

---

## 8. Ответы на вопросы оркестратора

### Q1. Присутствует ли `dom.skill-queue` в промпте? — **НЕТ**
Три независимых доказательства:
1. **Наблюдаемость:** `skill_queue.status = "injected"`, но `system_blocks_after - before = 1` в 123 из 124 записей (сегодня 7/7).
2. **Снапшот промпта** (то, что реально ушло в модель): 6 блоков, `use: skill_` / `gaps:` / `dom.skill-queue` — отсутствуют.
3. **Самоотчёт модели в сессии S4:** `lines_starting_with_step: 0`, `has_use_skill: false`, `has_dom_skill_queue: false`.

Причина — не «очередь пустая», а **мёртвый канал**: `entry.put("dom.skill-queue", …)` (`index.js:446`) срабатывает,
`event.system.push` (fallback) не выполняется, и запись в `entry` до промпта не доходит. Дополнительно: даже если бы дошла,
это был бы `skill_search` (fallback), потому что CAP не эмитится (F2).

### Q2. Показывает ли `dom_describe` поле `read_gate`? — **ДА**
Поле есть в ответе и корректно отражает политику (проверено и в живом MCP, и напрямую):

| режим запуска | `read_gate` | `review_policy.mode` |
|---|---|---|
| `auto` | `auto` | `auto` |
| `manual` | `manual` | `manual` |
| `independent` | `independent` | `independent` |

Живая сессия S1/S3: `"...\"read_gate\":\"auto\"..."` в выводе `dom_describe`. Плюс в `limits` есть расшифровка.

### Q3. Корректно ли producer внедряет очередь для шага с `cap`? — **ДА, но только при ручном вызове**
```
--step '{"step_id":"П15.2","title":"L2 V5 migration","cap":["sqlite","migration"]}'
→ queue: ["sqlite-migration","dom-memory"]
  block: step: L2 V5 migration [migration,sqlite | databases]
         use: sqlite-migration — matched: bm25,cap,card,group; verified for cap: migration,sqlite
         gaps: none
  state.capSet: ["migration","sqlite"]
```
Скилл подобран верно, блок корректен, fingerprint стабилен (3/3 процесса идентичны).
**Но** в живом контуре этот путь недостижим: хук не формирует `CAP:` (F2), поэтому `cap` всегда `[]`,
`queue` пуст, `use: skill_search`. Итог: механизм рабочий, триггер — мёртвый.

---

## 9. Токен-статистика (снапшоты промпта, S1)

```
prompt_total_chars:  177064      (counts.chars)
system_total_chars:   86594
system_blocks:            6
dom_notes_chars:       2785      ([DOM MEMORY INDEX] plan r7 · 44 items / 44 open · 206 claims · CHAIN_OK)
skill_queue_chars:        0      (не доставлен — см. F1)
tool_result_dom_describe: 11734  (в живой сессии; 13017 при ensure_ascii=True)
token_estimate:       ~44000     (177064 / 4)
```

Блоки: `[1471, 119, 79981, 238, 2000, 2785]`. `origin_summary`: `core/* = 5`, `plugin/harness-superpowers-planning = 1`, `dom = 1`.
`dom_tools: 0` — имена DOM-инструментов в системном тексте не упоминаются (они приходят только через MCP `tools/list`),
что и объясняет промахи из F15.

---

## 10. Топ-3 отказа

1. **F1 — `dom.skill-queue` не доставляется в промпт, лог рапортует `injected`** (`index.js:446`).
   Ядро версии не работает; наблюдаемость создаёт ложную уверенность. Подтверждено снапшотом, логом и самоотчётом модели.
2. **F2 — `CAP:` не эмитится ни одним хуком; парсер принимает только одну форму записи** (`index.js:191`, `hooks/*.py`).
   Даже при починке доставки очередь останется `skill_search`, а «почти правильные» CAP-строки будут молча теряться.
3. **F9 — read-before-write в `auto` выключен по умолчанию, хотя help обещает обратное** (`claim_entry.py:911`).
   Слепая перезапись claim'а без `expected_current`; в immutable-ledger это тихая потеря версии.

Далее по важности: F4 (висячие `basis` — нарушение целостности), F6 (`INTERNAL_ERROR` вместо `INVALID_ARGUMENTS`),
F7 (help для 4 несуществующих инструментов).

---

## 11. Что проверено и зелёное (чтобы не переделывать)

* Границы `title`/`context`: 500/501 и 2000/2001 — точные, help русский, ≤500 символов.
* `TEXT_TOO_LARGE`: 1 MiB байт точно; многобайтовый текст считается в UTF-8.
* Детерминизм: 100 вызовов `dom_skill_select` → 1 результат; 3 процесса producer → идентичные байты (в т.ч. `cap=[]`).
* `dom_help` 35/35: usage совпадает с `SIGNATURE`, pre/post/common_errors на месте, help ≤ 424, все с «Тебе НУЖНО:».
* 50 error-кодов из HELP_DATA присутствуют в `mockup/*.py`.
* `describe` укладывается в бюджет с запасом ≥11 % даже на максимуме (64 ветки по 32 символа → 13 329).
* `dom_start`: границы веток (1..64, код ≤32) и `adopt` работают.
* `dom_find(limit)`: 0/-1/1e9 → `INVALID_FIELD`, `"10"`/`true` → `INVALID_ARGUMENTS`.
* `dom_claim`: 2.8, 2.9 (idempotency), пустой/пробельный текст (`EMPTY_TEXT`), неизвестный ключ.
* Транспорт: 31 инструмент в `tools/list`, каждый отказ логируется в stderr `[dom-mockup]`, help непустой и русский.
* 7 живых сессий exit 0, `integrity: CHAIN_OK`, cross-session `dom_find` работает, `--agent explore` работает.
* Монитор: 189 записей, 0 ошибок, свежие сессии видны.

## 12. Наблюдения (не отказы чек-листа)

* **Монитор на 8772 не был запущен** (connection refused). Запущен вручную:
  `python opencode/monitor/server.py --project <repo> --port 8772 --token <из monitor.token>`.
  Автозапуска нет; для теста 5.3 это ручной шаг.
* Плагин загружается **дважды** за сессию (наш `opencode/` и копия из `omh-baseline-dom-mcp/.claude/worktrees/...`).
  Двойной инъекции нет (в снапшоте один `dom`-блок), но дубликат `id` — риск, о котором уже предупреждает `docs/development.md`.
* Блок `[DOM MEMORY INDEX]` (2785 симв.) инжектится в системный промпт на каждом вызове модели и меняется
  (206→207 claims), инвалидируя кэш системного префикса.
* `briefs` в `index.js` не очищается по завершении сессии (утечка в долгоживущем процессе).

## Приложение. Воспроизведение

```
python -X utf8 C:\Temp\opencode\t1_direct.py        # ЧЛ-1/2/3 через dispatch
python -X utf8 C:\Temp\opencode\t1_probe2.py        # help-vs-behaviour, висячие basis, fork
python -X utf8 C:\Temp\opencode\t1_mcp_stdio.py     # реальный MCP stdio + stderr
node            C:\Temp\opencode\t1_producer.mjs    # ЧЛ-4 целиком
node            C:\Temp\opencode\t1_cap_parse.mjs   # формы CAP
powershell -File C:\Temp\opencode\t1_run_session.ps1 -PromptFile <p.txt> -Out <out.json> [ -Agent explore ]
python -X utf8 C:\Temp\opencode\t1_verify_snap.py   # что реально ушло в модель
```

Резервная копия ledger перед живыми сессиями: `C:\Temp\opencode\dom-live-backup-20261128-113610\dom.sqlite3`.

TESTER_DONE
