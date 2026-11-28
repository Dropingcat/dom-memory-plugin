# AUDIT A2 — хуки и хелпы v0.3.1

**Аудитор:** A2 (хуки + help-слой), Code Factory
**Объект:** `E:\opencode_harness_portable\projects\opencode-dom-memory-plugin` @ `b7d0729` (worktree грязный)
**Режим:** READ-ONLY исходники; записаны только отчёт и probe-скрипты в `C:\Temp\opencode\`
**Контракт:** `docs/master-plan/AUDITOR_CONTRACT_FULL_CYCLE_TEMPLATE.md` (ЧЛ-A2, ЧЛ-A3, §3 Фактичность)

---

## AUDIT_VERDICT: REQUEST_CHANGES

**Сводка:** серверный help-слой сделан добротно (35/35 инструментов, русский префикс «Тебе НУЖНО:», help ≤ 500 и непустой на всех живых отказах), но релиз в текущем виде выпускать нельзя: **закоммиченное дерево (HEAD) валит 2 теста**, τ-гейт и help-гейт пишут решения в **stdout вместо stderr**, транспортный help `dom_claim` **обрезан на 500 символах посреди перечисления**, help-гейт **выключен и покрывает 12/35 инструментов**, а три обязательных артефакта ЧЛ-A3 (`t1-full-report.md`, `t2-trace-chain.md`, `o1-orchestrator-prompt.md`) **отсутствуют**.

---

## 0. Метод (что реально запускалось)

| # | Команда / проба | Результат |
|---|---|---|
| 1 | `python -B tools/run_all_checks.py` | `PASS: 38 checks, 38 passed, 0 failed (91.1 s)`, exit 0 |
| 2 | `python -B tools/run_tests.py` | `PASS: 40 groups, 0 failed`, exit 0 |
| 3 | `git archive HEAD` → `C:\Temp\opencode\a2-head.zip` → `python -B tests/check_dom_help.py`, `tests/check_skill_mcp_registration.py` | **оба падают** (AssertionError), exit 1 |
| 4 | `C:\Temp\opencode\a2_probe.py` — реальный `server.dispatch` + `public_dto.project_tool` по 17 «плохим» вызовам, включая 3 инструмента с необязательной схемой | все отказы с help, префикс есть; найден обрез 500 у `dom_claim` |
| 5 | `C:\Temp\opencode\a2_probe2.py` — границы `limit/depth/check_every`, `dom_claim` без `branch`, пустой `step` | найден обрез help; найден обязательный `branch` при `branch?` в usage |
| 6 | `C:\Temp\opencode\a2_hookrun.mjs` — настоящий `register()` из `index.js` с подставными `plan_check_hook.py`/`queue_producer.py`: 3 хода (тот же шаг → смена CAP) | idle=1 на 2-м ходе, emit с новым fp на смене CAP; **все решения в stdout** |
| 7 | `queue_producer.py` с 5 невалидными вызовами (`--step not-json`, `[1,2]`, без `--step`, `--bogus`, `--help`) | везде **exit 0** + `{"status":"ERROR"}` |
| 8 | Monitor `http://127.0.0.1:8772/api/health` и `/api/hooks?limit=2000` (токен `…/monitor.token`) | CHAIN_OK, 645 events; 186 hook-событий; 8 tool-отказов — у всех help с префиксом; 58 `skill_queue=idle`, 19 `injected`; **0 событий help-gate/tau-gate** |
| 9 | статический разбор: usage HELP_DATA vs `SIGNATURE`; 210 `Denied(...)`; 94 status-литерала; `argument_help` длины; префиксы `_STATUS_HELP` | 35/35 tools, 0 сирот, 4 status без `_HELP`, 17 без `next_action`, `dom_claim` usage 711→500 |

---

## 1. Найденные недочёты

### CRITICAL

**C1. Закоммиченное дерево валит 2 группы тестов; «0 failed» — только в грязном worktree**
`tests/check_dom_help.py:137` (HEAD) требует `schema['required'] == ['tool']`, а `opencode/mockup/claim_entry.py:1799-1806` уже отдаёт `required: []` (BUG-1 фикс закоммичен). `tests/check_skill_mcp_registration.py:147-159` (HEAD) требует `required == ["step"]` / `required == {"step": dict}` — код тоже уже `[]`/`{}`.
HEAD-прогон: оба теста падают с AssertionError. Рабочее дерево проходит только потому, что правки тестов **не закоммичены** (`git status`: ` M tests/check_dom_help.py`, ` M tests/check_skill_mcp_registration.py`).
→ Утверждение «0 failed» истинно лишь для незакоммиченного состояния; `git checkout HEAD` даёт 2 FAIL. Нарушен A3.4 (границы процесса) и «фактичность».

**C2. Решения τ-гейта и help-гейта уходят в stdout, а не в stderr; префикс `[dom-memory]` у τ-строк отсутствует**
`opencode/index.js:382` — `console.log("[τ-gate] idle …")`, `:391` — `console.log("[τ-gate] emit …")`, `:439` — `console.log("[τ-gate] skill-queue idle …")`, `:451` — `console.log("[dom-memory] skill-queue injected …")`.
`opencode/gates/help_gate.js:115` — `console.log("[dom-memory hooks] error help: …")`.
Тесты это **закрепляют**: `tests/check_tau_gate.mjs:213-228` перехватывает именно `console.log`; `tests/check_help_gate.mjs:14-23` — то же.
Мой harness (`a2_hookrun.mjs`): stdout = `["[dom-memory] skill-queue injected …", "[τ-gate] skill-queue idle (1) …", "[dom-memory] skill-queue injected …"]`, stderr = **только** стартовая строка `[dom-memory hooks] root=…`.
Независимое подтверждение: `C:\Temp\opencode\hard-test-report.md:210-219` (BUG-7) — `[τ-gate]` в stderr `run` = **0** вхождений.
→ ЧЛ-A2.4/A2.5 в формулировке «лог в stderr» **не выполняются**; операторская диагностика не видна. Дополнительно: MCP-сервер логирует `[dom-mockup]` (`server.py:596`), хуки — `[dom-memory hooks]`, τ — `[τ-gate]` — три разных префикса.

**C3. Обязательные артефакты ЧЛ-A3 отсутствуют (A3.5, A3.6 — FAIL)**
Проверено `Test-Path` в `C:\Temp\opencode\`: `t1-full-report.md` — **нет**, `t2-trace-chain.md` — **нет**, `o1-orchestrator-prompt.md` — **нет**. Есть только `hard-test-report.md`, `hard-test-2-report.md`, `dom-v0.3.1-report.md`.
→ A3.5 («оркестратор проверил свой промпт») недоказуем, A3.6 («все отчёты в C:\Temp\opencode») провален по 3 из 3 имён.

### MAJOR

**M1. Транспортный help `dom_claim` обрезан на 500 символах посреди перечисления**
`opencode/mockup/server.py:272-281` строит `argument_help('dom_claim')` длиной **711** симв.; `server.py:615` передаёт его в `public_dto.invalid_arguments`; `opencode/mockup/public_dto.py:704-713` режет `text[:HELP_LIMIT]` (500).
Факт (проба): `dom_claim({"type":"text","text":"abc","bogus":1})` → `INVALID_ARGUMENTS`, `len(help)=500`, хвост `"…commit_ref, "`, обязательный шаг «Повторить вызов» **потерян**. Функционально help неполный и обрывается на середине.
Тесты не ловят: `tests/check_error_help.py:177-191` проверяет длину только у `error_help` по status, а не у `argument_help`.

**M2. help-гейт выключен по умолчанию, не логирует своё состояние и покрывает 12 из 35 инструментов**
`opencode/gates/help_gate.js:30` — `HELP_GATE_ENABLED = false`; `index.js:490` — `options.helpGate ?? HELP_GATE_ENABLED`. При выключенном гейте `applyHelpGate` (`help_gate.js:111`) молча возвращает `undefined`; в стартовом логе (`index.js:267`) и в monitor нет ни одного признака, что гейт off.
`help_gate.js:33-60` — 12 ключей `dom_*` + `_default`; без адресного help остаются **23** инструмента: `dom_start, dom_resume, dom_submit, dom_review, dom_candidate, dom_admit, dom_append, dom_query, dom_snapshot, dom_trace, dom_export, dom_sync, dom_sync_status, dom_recall, dom_diff, dom_extract, dom_extract_submit, dom_distillate, dom_accept, dom_reconcile, dom_task_graph, dom_claim_versions, dom_init` → для них инжектится общий `_default`.
Monitor: `/api/hooks` 186 событий — hook-имена только `prompt/context/tool`, **0** событий help-gate. Подтверждает `hard-test-report.md:170-177` (BUG-2, гейт инертен).
Т.е. заявленный «help-слой на ошибках» в живой сессии фактически = только серверный payload; клиентские schema-отказы не аннотируются ничем.

**M3. `HELP_DATA.usage` противоречит схеме и поведению (3 инструмента + `dom_claim`)**
- `HELP_DATA.py:618` — `dom_help(tool)` (обязательный), а `server.py:224` / `claim_entry.input_schema` → `required: []` (BUG-1 сделал `tool` необязательным). Usage не обновлён.
- `HELP_DATA.py:596` — `dom_skill_select(step, …)`, а `server.py:220` / схема → `step` необязателен (`required: []`).
- `HELP_DATA.py:50` — `dom_start(request, …)`, а `server.py:199` → `request` необязателен.
- `HELP_DATA.py:90` — `dom_claim(branch?, …)` (branch необязателен), `server.py:200` тоже без branch в required — **но** `claim_entry.py:473` кидает `INVALID_FIELD field=branch`, если branch не передан и нет `plan_task` (проверено: `dom_claim({"type":"text","text":"abc"})` → `INVALID_FIELD`, help про ветку).
→ Агент, читающий `dom_help`, получает неверную сигнатуру: «branch?» на деле обязателен, а «tool/step/request» помечены как обязательные, хотя схема их не требует.

**M4. Постусловие `dom_describe` в help и docs не соответствует коду после перехода на индекс**
`HELP_DATA.py:28` — «Получен список tools **с аргументами и примерами**»; `docs/help/dom-errors.md:223` повторяет. Фактически `claim_entry.py:1913` (`tools = tool_index()`) и `:1765-1766` отдают только `{name, summary≤120}`, а аргументы/примеры ушли в `dom_help` (`claim_entry.py:1955`, `tool_docs`). Проверено: `tools[0] = {"name":"dom_describe","summary":"…one-line…"}`, полей `arguments`/`example` нет.
→ Документация и help обещают то, чего describe больше не возвращает (побочный эффект hotfix бюджета 15000).

**M5. «Каждый отказ содержит hint и help» — ложь для части статусов; у `STALE_PROJECTION_SNAPSHOT` нет ни hint, ни next_action**
`docs/help/dom-errors.md:25` — «Доменный отказ всегда содержит `status`, `hint` и `help`». `server.py:50-51` (INSTRUCTIONS_LITE, попадает в системный промпт) — «Every refusal carries a hint and next_action».
Факты:
- `opencode/mockup/admission.py:886`: `raise Denied('STALE_PROJECTION_SNAPSHOT', field='source_revision')` — **без hint и help**. `STALE_PROJECTION_SNAPSHOT` отсутствует в `_HELP` (есть только в `_STATUS_HELP`). Проекция: `{'field','help','status'}` — `hint=None`, `next_action=None` (проверено `project_tool`).
- 17 из 71 статусов `_HELP` не имеют `next_action` в lite (`EMPTY_TEXT, TEXT_TOO_LARGE, NO_CHANGE, STORAGE_*, INTERNAL_ERROR, …`), а 4 статуса (`GATE_NO_READ, PLAN_TOO_WIDE, REVIEWER_SESSION_REQUIRED, STALE_PROJECTION_SNAPSHOT`) вообще отсутствуют в `_HELP` → ни hint, ни next_action из таблицы.
→ Контракт промпта и справочника не выполняется; help есть, но обязательные поля — нет.

### MINOR

**m1. Тихие пути деградации (A2.8) — 4 штуки**
1. `opencode/hooks/plan_check_hook.py:89-90`: `if status.get('status') != 'PLAN_STATUS': return 0` — отказ (`MIGRATION_REQUIRED`, `SCOPE_DENIED`, `STORAGE_*`) глотается без stderr и без сообщения модели; `index.js` запишет `status="silent"` и всё.
2. `index.js:339-349` + `:471-474`: `gateCovers` при `!sessionID || !ids.length || !existsSync(dbFile)` возвращает `true`, и лог печатает **`ok`**, хотя гейт не выполнялся (диагностика врёт).
3. `opencode/mockup/server.py:28-34`: `except ImportError: HELP_DATA = {}` — при отсутствии/поломке `HELP_DATA.py` `dom_help` молча отвечает «unknown tool» на любой инструмент (нет ни лога, ни флага); то же в `public_dto.py:39-55` (`_load_tool_help` → `{}`).
4. `opencode/scripts/queue_producer.py:224-232`: битый `--queue` (`json.JSONDecodeError`) и не-list молча превращаются в `[]` — prefetch тихо теряется, хотя контракт fail-open требует `{"status":"ERROR"}`.

**m2. Счётчики тестов в релизных заметках устарели**
`docs/releases/0.3.1-notes.md:58` — «33 groups, 0 failed»; `:70` — «`tools/run_all_checks.py` — 31/31 checks pass». Факт: `run_all_checks.py` = **38/38**, `run_tests.py` = **40 groups/0 failed**. Три разных числа в трёх артефактах.

**m3. `CACHE_SCHEMA = 2` не соответствует коду (проверка §3 контракта — FAIL)**
`opencode/mockup/skill_index.py:42` — `CACHE_SCHEMA = 1`; `queue_producer.py:59` — `SCHEMA = 1`. Заявленного «2» в коде нет нигде.

**m4. Бюджет describe держится на `assert`**
`opencode/mockup/claim_entry.py:1979-1980`: `assert size < DESCRIBE_MAX_CHARS`. Под `python -O` проверка исчезает; при превышении — `AssertionError` → публичный `INTERNAL_ERROR` с общим help вместо усечения/честного ответа. (Сейчас запас есть: lite 10 826–10 831 симв., strict 3 368.)

**m5. В strict-профиле `dom_describe.tools = []`**
`claim_entry.py:1917` (`tools = []`) при том, что help-карточка `dom_describe` обещает список инструментов; `tool_docs` при этом указывает на `dom_help` (доступен и в strict). Мелкое противоречие «карточка vs профиль».

**m6. Тест help-гейта не защищает от дрейфа текстов**
`tests/check_help_gate.mjs:41` требует лишь `DOM_TOOLS.length >= 8` и `len ≤ 500`; соответствие `HELP_DATA.py` (35 записей, точный текст) не проверяется. Файл сам признаёт ручную синхронизацию (`help_gate.js:14-16`). Итог: 12/35 при пороге 8 — дрейф пройдёт незамеченным.

### Процесс (ЧЛ-A3)

| # | Проверка | Вердикт | Доказательство |
|---|---|---|---|
| A3.1 | Тестер ≥3 отказа (ЧЛ-1) | **НЕ ПРОВЕРЯЕМО по контракту** | `t1-full-report.md` отсутствует; альтернативный `hard-test-report.md` содержит 9 дефектов (BUG-1…BUG-9) → порог ≥3 выполнен, но не в требуемом файле |
| A3.2 | Тестер ≥3 отказа (ЧЛ-2) | **НЕ ПРОВЕРЯЕМО** | `t2-trace-chain.md` отсутствует |
| A3.3 | Тестер ≥3 отказа (ЧЛ-3) | **НЕ ПРОВЕРЯЕМО / вероятно FAIL** | `hard-test-2-report.md` = «4 PASS / 2 FAIL» (<3 отказов) |
| A3.4 | Тестер не редактировал код | **FAIL по состоянию дерева** | `git status`: ` M tests/check_dom_help.py`, ` M tests/check_skill_mcp_registration.py`, ` D NOT_DONE.md`, untracked `opencode-dom-memory-plugin.zip` (5.8 MB), `tools/launch_monitor*.py`, `tools/start_monitor.py`, `.opencode/`; правки тестов не закоммичены |
| A3.5 | Проверен промпт оркестратора | **FAIL** | `o1-orchestrator-prompt.md` отсутствует |
| A3.6 | Отчёты в `C:\Temp\opencode\` | **FAIL** | 3 из 3 обязательных имён отсутствуют |

---

## 2. Фактичность (§3 контракта)

| Утверждение | Проверка | Итог |
|---|---|---|
| «38 groups, 0 failed» | `python -B tools/run_all_checks.py` | **PASS**: 38 checks / 38 passed / 0 failed, exit 0. (Но `tools/run_tests.py` даёт 40 groups, а релизные заметки — 33/31.) |
| «dom_help has 35 tools» | `python -B tests/check_help_data.py` + интроспекция | **PASS**: `HELP_DATA` = 35, `SIGNATURE` = 35, симметричная разница пуста; все 35 `help` начинаются с «Тебе НУЖНО:», max 424 ≤ 500; все 35 имеют ≥1 `common_errors` (50 кодов) |
| «describe ≤ 15000 chars» | `Service(lite).describe()`, `json.dumps(..., ensure_ascii=False)` | **PASS**: 10 826 / 10 831 симв. (strict 3 368); бюджет `claim_entry.py:1754` = 15000 |
| «τ-gate off by default» | `gates/help_gate.js:30` | **PASS с оговоркой**: `HELP_GATE_ENABLED = false`. Строка контракта называет это «τ-gate», хотя речь о **help**-гейте; у τ-гейта выключателя нет — он всегда включён (`index.js:48,376-392`) |
| «Producer timeout 4000ms» | `index.js:277` | **PASS**: `skillTimeoutMs = 4000` (холодный прогон продюсера 987 мс) |
| «CACHE_SCHEMA = 2» | `mockup/skill_index.py:42` | **FAIL**: `CACHE_SCHEMA = 1` (и `queue_producer.py:59` `SCHEMA = 1`) |
| A2.1/A2.2 «каждый отказ с непустым help» | 17 probe-отказов + 8 живых tool-отказов из monitor | **PASS на сервере**: help непустой везде, префикс есть; **исключение по качеству** — обрез 500 у `dom_claim` (M1) |
| A2.3 «help на русском» | 35 HELP_DATA + 69 `_STATUS_HELP` + `DEFAULT_HELP` + `argument_help` | **PASS**: 100% префикс «Тебе НУЖНО:» |
| A2.5 «idle counter на втором вызове» | `a2_hookrun.mjs`: 2-й ход | **PASS**: `[τ-gate] skill-queue idle (1)`, 3-й — `idle (2)`; в monitor 58 `skill_queue.status = idle` |
| A2.6 «emit при смене cap» | `a2_hookrun.mjs`: `CAP: sqlite → testing` | **PASS**: новый fp `8a4f0c81e30c`, блок переинжектирован (idle reset 0); в monitor 19 `injected` |
| A2.7 «producer fail-open» | 5 невалидных вызовов + 1 валидный | **PASS**: все невалидные → exit 0 + `{"status":"ERROR"}`; валидный → exit 0 + `{"status":"OK"}` |

---

## 3. Подтверждённое (что не вызывает сомнений)

- `HELP_DATA`: 35/35 инструментов, 0 сирот относительно `SIGNATURE/HANDLERS/TOOL_DOCS`; 35 карточек в `docs/help/dom-errors.md`, 0 расхождений по именам; 50 кодов в `common_errors` = заявленным 50.
- Серверный help всегда непустой и ≤500: `error_help` (`public_dto.py:689-713`) с каскадом «explicit → HELP_DATA → статус → DEFAULT_HELP», `guarded` (`admission.py:66-86`) добивает `help` и на возвращаемых отказах, `Broken`/`StorageError` тоже с help.
- Живой монитор: 8 tool-отказов (`dom_skill_select` ×6, `dom_help` ×2) — у каждого `help` с префиксом; CHAIN_OK, 645 events, 186 hook-событий, 0 `dropped_events`.
- τ-гейт по существу работает: idle-счётчик растёт без cap (тест на 1000 итераций), смена fingerprint сбрасывает счётчик и переинжектирует блок; JS/Python fingerprint совпадают (pinned digest `75e3043b…`).
- Producer fail-open: `argparse.SystemExit`, битый JSON, не-объект, любое исключение → один JSON на stdout и `return 0` (`queue_producer.py:207-248`).
- Help-гейт как чистая функция: `normalizeToolName` восстанавливает `dom-memory_dom_claim → dom_claim` (BUG-2), `_default` не коллизирует с реальными именами, help ≤500 в JS-подмножестве.

---

## 4. Замечания к самому контракту (не к коду)

1. §3, строка «τ-gate off by default» проверяет `help_gate.js: HELP_GATE_ENABLED` — это **help**-гейт, а не τ. Критерий «Отказ если false ≠ true» сформулирован наоборот: при заявленном «off» правильное значение — `false`.
2. A2.4 «В stderr есть `[dom-memory]` log» смешивает три разных префикса (`[dom-memory hooks]`, `[dom-mockup]`, `[τ-gate]`); критерий нужно привязать к конкретному слою.
3. A3.1-A3.3 ссылаются на `t1/t2`, которых нет в артефактах; без них проверка невыполнима буквально.

---

## 5. Минимальный набор исправлений до повторного аудита

1. `index.js:382,391,439,451` и `help_gate.js:115` → `console.error`; префикс `[dom-memory]` на всех диагностических строках; тесты `check_tau_gate.mjs`/`check_help_gate.mjs` перевести на перехват `console.error`.
2. Закоммитить правки `tests/check_dom_help.py` и `tests/check_skill_mcp_registration.py` (или откатить фикс схемы) — HEAD обязан проходить `run_tests.py`.
3. `public_dto.error_help` / `server.argument_help`: либо сократить `argument_help('dom_claim')` до ≤500, либо резать по границе пункта с явным «… см. dom_help».
4. Включить `HELP_GATE_ENABLED` (после живой проверки `execute.after` — она уже подтверждена BUG-2 фиксом) либо писать в стартовый лог «help gate OFF by design»; добавить help-событие в observability.
5. Синхронизировать `HELP_DATA.usage` (`dom_help`, `dom_skill_select`, `dom_start`, `dom_claim.branch`) и постусловие `dom_describe` с фактической схемой/ответом.
6. `admission.py:886` — добавить `hint`/`help`; убрать из промпта (`server.py:50-51`) утверждение «every refusal carries hint and next_action» либо выполнить его.
7. Убрать тихие пути: `plan_check_hook.py:89-90` (stderr-лог отказа), `index.js` (не писать `ok`, когда гейт не выполнялся), `queue_producer.py:224-232` (ERROR вместо `[]`), `server.py:28-34` (лог о пустом HELP_DATA).
8. Положить `t1-full-report.md`, `t2-trace-chain.md`, `o1-orchestrator-prompt.md` в `C:\Temp\opencode\` (или изменить контракт).

---

AUDIT_DONE
