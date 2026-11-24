# KANBAN — DOM Memory: 0.2.5 и линия надёжности

**Дата:** 2026-11-20
**База:** 0.2.4, commit `f60cd84`, `VERSION` 0.2.4
**Статус доски:** ACTIVE
**Назначение:** исполнимая доска для агентской фабрики: карта → проверка на отказоустойчивость → внедрение → живой прогон с поиском дыр и молчаливых отказов.

**Источники (в порядке силы):**
1. Код и тесты в репозитории.
2. Этот канбан.
3. `docs/superpowers/plans/2026-11-19-semantic-bridge-0.2.5.md`.
4. `docs/superpowers/specs/2026-11-19-semantic-bridge-0.2.5.md`.
5. `PRIMARY_IMPLEMENTATION_REVIEW_024.md`.
6. `DOM_CODE_AUDIT_0.2.4.md`, `DOM_CODE_AUDIT_0.2.4_EXPANDED.md`, `положения.md`, `ревью.md`.

**Правило конфликта:** если карточка противоречит коду или тесту, побеждает код; карточка исправляется, а не подгоняется. Если план противоречит канбану, побеждает канбан, потому что он несёт текущее решение и evidence.

---

## 0. Как работать с доской

### 0.1. Роли фабрики

Источник ролей и state machine: `E:\opencode_harness_portable\.opencode\shared\code-factory-process.md`. Контракты адаптированы под DOM: код и тесты важнее текста агента, `SKIP` не равен `PASS`.

| Роль | Что делает | Что не делает |
|---|---|---|
| `code-orchestrator` | читает карточку, диспатчит звенья, держит baseline и капсулу | не пишет код вместо воркера |
| `coder-worker` | пишет код/тест по контракту карточки | не меняет критерии приёмки |
| `code-reviewer` | атакует код и даёт фальсифицируемые замечания | не принимает код |
| `code-tester` | пишет/запускает проверки, фиксирует вывод | не «чинит» тест под результат |
| `code-auditor` | проверяет процесс и доказательства | не судит код по существу |

### 0.2. Состояния карточки

```text
BACKLOG -> READY -> RED -> GREEN -> MIRROR -> VERIFY -> REVIEW -> DONE
                                  \-> FAILED
                                  \-> BLOCKED
```

- `RED` — проверка добавлена и падает по ожидаемой причине.
- `GREEN` — код минимально достаточен, проверка проходит.
- `MIRROR` — изменение скопировано во второе дерево, SHA256 совпал.
- `VERIFY` — прогнаны все проверки карточки, включая negative/fault probe.
- `REVIEW` — ревьюер дал `APPROVE`; замечания закрыты или эскалированы.
- `DONE` — evidence приложен, статус обновлён в разделе 12.

### 0.3. Контракт карточки

Каждая карточка обязана иметь:

```text
ID, ветка, цель
зависит от
файлы
шаги
проверка (команда -> ожидание)
fault probe (негативный сценарий)
DoD
откат
evidence
```

Карточка без `fault probe` не переходит в `DONE`. Карточка без команды проверки не переходит в `GREEN`.

### 0.4. Правила фабрики

1. **LLM свидетельствует, код решает.** Статус меняется по выводу команды, не по тексту агента.
2. **Один активный кодер на файл.** Параллельные ветки не редактируют один и тот же файл.
3. **WIP limit:** не более двух кодирующих веток одновременно; в каждой — одна карточка в `GREEN`.
4. **Деревья:** правим `opencode/`, копируем в `claude/`, сверяем SHA256 до коммита.
5. **Никаких новых зависимостей** в 0.2.5. Сеть, модель и случайность в runtime запрещены.
6. **Никакого «всё зелёное»** без полного вывода проверок. `SKIP` — не `PASS`.
7. **Один коммит = одна логическая карточка или группа карточек одной ветки.**
8. **Секреты и runtime-база** не коммитятся. `.dom-memory/` уже в `.gitignore`.

### 0.5. Дисциплина доказательств

Для каждой карточки сохраняется:

- команда;
- полный вывод или его хвост (не менее 20 строк при ошибке);
- exit code;
- дерево (`claude`/`opencode`);
- версия Python;
- ссылка на файл evidence.

Evidence складывается в `docs/kanban/runs/<date>-<lane>.md`. Без evidence карточка остаётся в `VERIFY`.

---

## 1. Честный baseline

| Факт | Подтверждение | Статус |
|---|---|---|
| `VERSION` = 0.2.4 в корне и обоих деревьях | `Get-Content VERSION`, `claude/VERSION`, `opencode/VERSION` | подтверждено |
| HEAD = `f60cd84`, `main` = `origin/main` | `git rev-parse --short HEAD`, `git rev-parse --short origin/main` | подтверждено |
| Tracked-дерево чистое | `git status --short` показывает только untracked | подтверждено |
| 10 engine-файлов в архиве ревью были «modified» только из-за CRLF | `git diff --ignore-cr-at-eol --stat` пуст | подтверждено |
| Шесть self-checks 0.2.4 зелёные в обоих деревьях | прогон 0.2.4 | подтверждено в этой среде |
| Два stage-3 теста в среде ревью не завершились | `PRIMARY_IMPLEMENTATION_REVIEW_024.md` §8 | не PASS; перепроверить в A2.2/B0.1 |
| В коде 0.2.4 нет warrant и audit | `distillate.py`, `public_dto.py` | подтверждено |
| Текущая `.dom-memory/main/dom.sqlite3` — свежая OpenCode-база | `project_root.created_at = 2026-11-16T17:02`, 103 claims, все `origin_version='lite-v1'` | подтверждено |
| Клод-эпоха в текущую базу не переносилась | нет иных `origin_version`; нет distillate/warrant claims | подтверждено |
| `PROFILES = ('prod','lab','lite','strict')`, mapping `prod→lite`, `lab→strict` | `server.py` | подтверждено; решение — D-001 |

**Правило честности:** «ревью не завершило тест» — это `BLOCKED`, а не `PASS`. B0.1 обязана либо воспроизвести PASS, либо зафиксировать причину.

### 1.1. Вывод ревью, который определяет доску

- 0.2.4 — не «заготовка», а рабочее ядро: immutable claims/events, admission, plan projection, distillate, delivery boundary, read APIs. Новые модули не строить до аудита существующих.
- **P0:** baseline/reproducibility + ledger integrity (atomicity, replay, chain, projection).
- **P1:** host contract, plan recovery/concurrency, retrieval adequacy, trust boundary/injection.
- **P2:** semantic bridge 0.2.5 — логичное отдельное расширение, но не часть baseline 0.2.4.
- Ревью не подтвердило «все тесты зелёные»: два stage-3 теста не завершились. Это учтено в B0.1 и A2.2.

**Почему 0.2.5 всё равно волна 0:** это узкое расширение уже работающего 0.2.4, а не замена ядра. Релиз 0.2.5 не отменяет P0: B0.1 — обязательный gate, а полный B запускается волной 1 сразу после релиза. Если живой прогон H найдёт дыру в ledger, 0.2.5 останавливается и B переносится вперёд.

---

## 2. Карта доски

| Блок | Ветки | Карточек | Приоритет | Волна | Статус блока |
|---|---|---|---:|---|---|
| **A. POSITION** — prod/lab, baseline, provenance | A1, A2 | 9 | P0 | 0 | READY |
| **B. LEDGER** — атомарность, replay, chain, concurrency | B1–B4 | 18 | P0 | 0 (B0.1), 1 (полный) | BACKLOG |
| **C. HOST** — parity, hook reliability | C1, C2 | 8 | P1 | 1 | BACKLOG |
| **D. PLAN** — identity, recovery, concurrency | D1, D2 | 8 | P1 | 1 | BACKLOG |
| **E. RETRIEVAL** — adequacy, gaps | E1, E2 | 6 | P1 | 2 | BACKLOG |
| **F. SECURITY** — trust boundary, injection | F1, F2 | 7 | P0/P1 | 0 (F1.1), 1 (остальное) | BACKLOG |
| **G. SEMANTIC 0.2.5** — warrant, binding, audit, corpus | G1–G5 | 18 | P2 (текущий релиз) | 0 | READY |
| **H. ADVERSARIAL RUN** — живой прогон, дыры | H1–H3 | 24 | P0 для релиза | 0 | BLOCKED by G |
| **I. RELEASE 0.2.5** — version, dist, install, live | I1–I3 | 9 | P0 | 0 | BLOCKED by G/H |

**Итого:** 9 блоков, 25 рабочих подветок (+9 map-веток `X0`), 107 карточек.

### 2.1. Волны

| Волна | Что делаем | Почему |
|---|---|---|
| **0 — 0.2.5** | A1, A2.1–A2.3, B0.1, F1.1, G, H, I | выпуск warrant + audit; минимальный integrity smoke и trust boundary перед релизом |
| **1 — надёжность** | B (полный), C, D, F2 | закрыть P0/P1 ревью: ledger, host, plan, injection |
| **2 — retrieval** | E, затем решение по DCP/Capability | измерить существующий retrieval до новых модулей |

Ревью предлагает начинать с P0 ledger. Это не отменяется: B0.1 — обязательный gate волны 0, полный B — волна 1. Если H найдёт дыру в ledger, релиз 0.2.5 останавливается и B переносится в волну 0.

---

## 3. Трассировка ревью → карточки

| ID ревью | Работа | Карточки |
|---|---|---|
| R0-01 | Baseline hygiene | A2.1–A2.4 |
| R0-02 | Ledger atomicity/replay | B1.1–B2.3 |
| R0-03 | Hash-chain corruption/projection | B3.1–B3.5 |
| R1-01 | Unified host contract | C1.1–C1.3 |
| R1-02 | Plan ingest concurrency/recovery | D2.1–D2.4 |
| R1-03 | Hook latency/failure budget | C2.1–C2.4 |
| R1-04 | Retrieval adequacy | E1.1–E2.3 |
| R1-05 | Threat model/injection | F1.1–F2.3 |
| R2-01 | Semantic bridge 0.2.5 | G1.1–G5.3 |
| R2-02 | Capability Registry | вне 0.2.5; решение после E |
| R2-03 | DCP | вне 0.2.5; E2.3 решает, нужен ли adapter |

---

## 4. Блок A — POSITION (P0, волна 0)

**Цель:** зафиксировать, что такое `prod` и `lab`, чем является текущая база и где проходит граница релиза 0.2.4/0.2.5.
**Выход блока:** решение D-001 записано, baseline manifest и тег созданы, runtime-артефакты классифицированы.

### A0 — карта отказов

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| A0.1 | Карта отказов позиции и baseline: version mismatch, грязное дерево, CRLF-ложный diff, stale dist, drift install target, drift двух деревьев, старый live-процесс | — | `docs/kanban/maps/A-position.md` | файл существует; у каждой дыры есть detection и card → PASS | одна дыра без карточки → review reject | READY |

### Ветка A1 — prod/lab и provenance

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| A1.1 | Записать решение: `prod` (alias `lite`) = выпущенная рабочая линия; `lab` (alias `strict`) = подготовка следующих версий, отработка фич, лабораторный стенд; это одна продуктовая линия, не два продукта | — | `docs/position.md` | `Select-String docs/position.md -Pattern 'prod.*released','lab.*preparation'` → оба совпадения | читатель `PROFILES` думает «два продукта» → текст должен явно опровергать | READY |
| A1.2 | Записать provenance текущей базы: создана 2026-11-16T17:02, 103 claims, все `lite-v1`, 26 таблиц, нет distillate/warrant; клод-эпоха не переносилась | A1.1 | `docs/position.md` §Provenance | SQL-дамп повторяет цифры → PASS | заявить «миграция прошла» без SQL → reject | READY |
| A1.3 | Связать позицию с README/GET_STARTED/NOT_DONE: одна ссылка, без переписывания продукта | A1.1 | `README.md`, `GET_STARTED.md`, `NOT_DONE.md` | ссылки разрешаются; `VERSION` не меняется | документ противоречит `server.py` → reject | BACKLOG |

**Текст решения A1.1 (канон):**

> `prod` — выпущенная рабочая линия, на которой стоит текущая установка. `lab` — лаборатория подготовки следующих версий и отработки фич. `prod` и `lab` — две линии одной продуктовой памяти, а не два продукта. В 0.2.5 runtime-имена не переименовываются: `PROFILES` и mapping остаются как есть, решение фиксируется документально.

### Ветка A2 — baseline freeze

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| A2.1 | Классифицировать дерево: committed 0.2.4 / local config / generated / RFC 0.2.5 / runtime; таблица на каждый untracked | A0.1 | `docs/release/0.2.4-baseline.md` | таблица покрывает 100% `git status --short`; `.opencode/` и `.dom-memory/` не в commit | `.opencode/` попал в commit → fail | READY |
| A2.2 | Manifest: commit, VERSION, SHA256 `claude/`, `opencode/`, `dist/`, версии Python/Node/OS, список проверок с PASS/FAIL/BLOCKED | A2.1 | `docs/release/0.2.4-baseline.md` | хэши пересчитываются; два прогона совпадают | manifest снят с грязного дерева без пометки → reject | READY |
| A2.3 | Тег baseline: `v0.2.4-baseline` на `f60cd84`, аннотированный, push | A2.2 | git tag | `git rev-parse v0.2.4-baseline^{commit}` = `f60cd84` | тег на другом commit → снять и переставить | BACKLOG |
| A2.4 | Политика `.opencode/`: решить, local config или tracked; обновить `.gitignore` или добавить в commit | A2.1 | `.gitignore`, `docs/release/0.2.4-baseline.md` | `git check-ignore -v .opencode/opencode.json` даёт явный ответ | runtime-база случайно tracked → fail | BACKLOG |
| A2.5 | Закоммитить канбан, position, baseline manifest и карты отдельным docs-коммитом | A1.1, A2.2 | git | `git status --short` не содержит эти файлы | смешать с кодом 0.2.5 → reject | BACKLOG |

**Команды A2.1–A2.2:**

```powershell
git status --short
git diff --stat
git diff --ignore-cr-at-eol --stat
git rev-parse HEAD
Get-Content VERSION, claude/VERSION, opencode/VERSION
```

**DoD блока A:** D-001 записан; provenance подтверждён SQL; manifest воспроизводим; тег указывает на `f60cd84`; `.opencode/` имеет явную политику.

**Откат блока A:** docs-коммит откатывается `git revert`; тег снимается `git tag -d v0.2.4-baseline` и удаляется на remote только с явного решения владельца.

---

## 5. Блок B — LEDGER (P0, волна 0 smoke + волна 1)

**Цель:** доказать, что ядро не оставляет частичных записей, не дублирует replay, различает повреждения chain и не маскирует corrupt state.
**Выход блока:** `tests/check_ledger_atomicity.py`, `tests/check_ledger_replay.py`, `tests/check_ledger_corruption.py`, `tests/check_ledger_concurrency.py` — зелёные в обоих деревьях.

### B0 — карта и pre-release smoke

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| B0.1 | Pre-release integrity smoke: `PRAGMA integrity_check`, `verify_chain`, counts, чтение projections; без новых тестов | A2.1 | `docs/kanban/runs/2026-11-20-b0-smoke.md` | integrity `ok`, chain valid, projections читаются → PASS | любой corrupt state → релиз стоп | READY |
| B0.2 | Карта отказов ledger: partial write, duplicate, replay divergence, hash corruption, projection stale, lock timeout, silent rollback | — | `docs/kanban/maps/B-ledger.md` | каждая дыра имеет detection + card | дыра без detection → reject | BACKLOG |
| B0.3 | Hotfix публичной проекции `dom_plan_status`: `inbox_queued`/`inbox_failed` добавлены в `PlanStatus` DTO; регрессия идёт через `project_tool`, а не только через сервис | B0.1 | `claude/mockup/public_dto.py`, `opencode/mockup/public_dto.py`, `tests/check_inbox_status.py` | `python tests/check_inbox_status.py` → PASS в обоих деревьях; stdio `dom_plan_status` → `PLAN_STATUS` | удалить поле из DTO → `UNKNOWN_FIELD $.inbox_queued` | DONE |

### Ветка B1 — атомарность

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| B1.1 | Fault injection на submit: trigger `BEFORE INSERT ON claims` → `RAISE(ABORT)`; submit отказывает, частичных строк нет | B0.1 | `tests/check_ledger_atomicity.py` | claims/reviews/transactions/events не изменились; chain valid | после снятия trigger retry проходит | BACKLOG |
| B1.2 | Fault injection на review: trigger на `reviews`; review отказывает, candidate не «полу-принят» | B1.1 | тот же | нет review, нет branch head, нет event | candidate остался в ambiguous state → fail | BACKLOG |
| B1.3 | Fault injection на admit: trigger на `branch_heads`/`events`; admit отказывает, нет ложного accepted | B1.2 | тот же | branch head не сдвинут; projection не видит accepted | projection видит accepted → fail | BACKLOG |
| B1.4 | Crash/restart recovery: kill процесса на границе, повторный старт, нет ложного accepted | B1.3 | тот же | после restart `verify_chain` valid; повторный submit идемпотентен | повторный submit создаёт дубль → fail | BACKLOG |

**Метод:** не менять production-код ради теста. Использовать SQLite-триггеры и повторный запуск сервиса. Если нужен test-only seam, он вводится отдельным решением и не меняет поведение.

### Ветка B2 — idempotency/replay

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| B2.1 | Тот же idempotency key + тот же payload → тот же результат, одна claim, один event | B1.1 | `tests/check_ledger_replay.py` | counts +1, повтор не +1 | второй вызов создаёт вторую claim → fail | BACKLOG |
| B2.2 | Тот же key + другой payload/scope → явный отказ, не перезапись | B2.1 | тот же | код отказа зафиксирован в тесте; ledger не изменён | молчаливая перезапись → fail | BACKLOG |
| B2.3 | Replay после restart: key сохраняется, результат тот же | B2.2 | тот же | после restart ответ идентичен | key потерян → fail | BACKLOG |

### Ветка B3 — chain и corruption

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| B3.1 | Повреждение `event_hash` → отдельный код, не общий `CORRUPT` | B1.4 | `tests/check_ledger_corruption.py` | код A ≠ код B ≠ код C | все повреждения дают один код → fail | BACKLOG |
| B3.2 | Повреждение `previous_hash` → отдельный код | B3.1 | тот же | отдельный код | цепочка «чинится» сама → fail | BACKLOG |
| B3.3 | Повреждение claim digest → отдельный код | B3.2 | тот же | отдельный код | projection отдаёт corrupt claim → fail | BACKLOG |
| B3.4 | Повреждение review digest → отдельный код | B3.3 | тот же | отдельный код | accept проходит → fail | BACKLOG |
| B3.5 | Projection integrity: corrupt state не выдаётся как valid | B3.4 | тот же | `dom_context`/`find`/`show` отказывают или помечают | corrupt виден как valid → fail | BACKLOG |

**Правило:** точные имена кодов фиксируются по фактическому `admission.py`; если коды неразличимы, карточка включает минимальное исправление, а не подгонку теста.

### Ветка B4 — concurrency

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| B4.1 | Два writer обновляют одну claim: один wins, второй отказ или корректный merge | B2.3 | `tests/check_ledger_concurrency.py` | нет lost update; chain valid | оба accepted → fail | BACKLOG |
| B4.2 | Два session одновременно ingest одну plan revision | B4.1 | тот же | один ingest, второй idempotent/refusal | двойные plan_tasks → fail | BACKLOG |
| B4.3 | SQLite busy/locked: timeout даёт предсказуемый отказ, не порчу | B4.2 | тот же | код отказа, retry проходит | процесс падает без кода → fail | BACKLOG |

**DoD блока B:** все четыре теста зелёные в обоих деревьях; ни один refusal не оставляет частичных строк; коды повреждений различимы; B0.1 подтверждён перед релизом.

**Откат блока B:** тесты и минимальные исправления откатываются `git revert`; схема БД не меняется без отдельной миграции.

---

## 6. Блок C — HOST (P1, волна 1)

**Цель:** доказать одинаковую семантику двух host-деревьев и предсказуемое поведение hook при таймауте/отказе.

### C0 — карта

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| C0.1 | Карта host-отказов: drift engine, разная нормализация, timeout, orphan child, duplicate ingest, hook crash, stale plan file | — | `docs/kanban/maps/C-host.md` | каждая дыра имеет detection + card | дыра без detection → reject | BACKLOG |

### Ветка C1 — parity

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| C1.1 | Byte-equality gate для всех shared-файлов: `mockup/*.py`, `skills/dom-memory/SKILL.md`, hooks | C0.1 | `tests/check_tree_parity.py` | SHA256 совпадает для каждого файла | изменить 1 байт в `claude/` → fail | BACKLOG |
| C1.2 | Unified host contract suite: один сценарий, оба дерева, нормализованные пути/время, сравнение JSON | C1.1 | `tests/check_host_contract.py` | нормализованные результаты идентичны | разный статус → fail | BACKLOG |
| C1.3 | Единая команда прогона: `tools/check_all.ps1` или `tools/check_all.py`, nonzero при любом FAIL | C1.2 | `tools/check_all.ps1` | все checks + exit code | один FAIL → nonzero | BACKLOG |

### Ветка C2 — hook reliability

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| C2.1 | Замерить latency hook: p50/p95 на 20 вызовах, записать в evidence | C1.2 | `opencode/index.js`, `docs/kanban/runs/` | таблица p50/p95/max | нет замеров → не GREEN | BACKLOG |
| C2.2 | Timeout + child cleanup + no duplicate ingest | C2.1 | `opencode/index.js`, `tests/check_host_hook.py` | timeout kills child; один ingest | orphan process → fail | BACKLOG |
| C2.3 | Graceful fallback: hook failure не блокирует host, контекст пустой, ошибка видна | C2.2 | тот же | host продолжает, ошибка в логе | host падает → fail | BACKLOG |
| C2.4 | ADR: `spawnSync` vs async `spawn` по замерам | C2.1–C2.3 | `docs/adr/0001-hook-execution.md` | решение с числами и условием пересмотра | решение без чисел → reject | BACKLOG |

**DoD блока C:** parity-скрипт зелёный; contract suite даёт идентичный результат; latency измерена; timeout и fallback проверены; ADR зафиксирован.

---

## 7. Блок D — PLAN (P1, волна 1)

**Цель:** закрыть негативные и конкурентные сценарии plan identity/ingest, не меняя root-identity baseline.

### D0 — карта

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| D0.1 | Карта plan-отказов: stale plan, same root new meaning, partial ingest, inbox failure, context-only revision, concurrent ingest | — | `docs/kanban/maps/D-plan.md` | каждая дыра имеет detection + card | дыра без card → reject | BACKLOG |

### Ветка D1 — identity edges

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| D1.1 | Rename root → fresh plan IDs, старые claims остаются в истории | D0.1 | `tests/check_plan_identity.py` | новые IDs; старые читаются | старые claims архивированы → fail | BACKLOG |
| D1.2 | Same root, новый смысл: поведение явно определено как revision; `new_plan` не вводится в 0.2.5 | D1.1 | `docs/plan-identity.md`, тест | тест фиксирует текущее поведение + docs | поведение не определено → fail | BACKLOG |
| D1.3 | Context-only change не создаёт content revision | D1.2 | `tests/check_plan_context.py` | content revision не растёт | ложная revision → fail | BACKLOG |

### Ветка D2 — recovery/concurrency

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| D2.1 | Stale plan не перезаписывает более новую ревизию | D1.3 | `tests/check_plan_recovery.py` | stale → отказ, новая ревизия цела | stale wins → fail | BACKLOG |
| D2.2 | Два session ingest одну ревизию: один wins, второй idempotent/refusal | D2.1 | тот же | нет двойных plan_tasks | двойные tasks → fail | BACKLOG |
| D2.3 | Partial ingest не архивирует незатронутые элементы | D2.2 | тот же | незатронутые активны | массовый archive → fail | BACKLOG |
| D2.4 | Ошибка записи inbox job не теряет job, retry идемпотентен | D2.3 | тот же | job сохранён; retry один результат | job потерян → fail | BACKLOG |

**DoD блока D:** все негативные сценарии воспроизведены; stale/concurrent/partial/inbox не оставляют ложного состояния; docs обновлены.

---

## 8. Блок E — RETRIEVAL (P1, волна 2)

**Цель:** измерить, что существующие read APIs уже дают агенту, и чего не хватает; не строить новый RAG до доказательства пробела.

### E0 — карта

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| E0.1 | Карта retrieval-сценариев и возможных пробелов: missing context, irrelevant context, stale ref, trace gap | — | `docs/kanban/maps/E-retrieval.md` | 7 сценариев, у каждого expected context | сценарий без expected → reject | BACKLOG |

### Ветка E1 — corpus

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| E1.1 | Фиксированный corpus: новая задача, повторная задача, продолжение частичной, устаревший источник, конфликт claims, cross-branch dependency, смена версии инструмента | E0.1 | `tests/fixtures/retrieval/scenarios.json` | 7 сценариев, каждый с задачей и ожидаемым контекстом | сценарий без ожидаемого контекста → reject | BACKLOG |
| E1.2 | Для каждого сценария — обязательные claims, plan items, decisions, dependencies, open issues | E1.1 | тот же | таблица «required context» заполнена | «примерно» вместо списка → reject | BACKLOG |

### Ветка E2 — measurement

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| E2.1 | Прогнать `dom_context`, `dom_task_graph`, `dom_task_claims`, `dom_find`, `dom_plan_tree`, `dom_recall` на corpus | E1.2 | `tests/check_retrieval_adequacy.py` | каждый API отвечает, результаты записаны | API падает → fail | BACKLOG |
| E2.2 | Метрики: missing, irrelevant, stale refs, trace completeness | E2.1 | тот же | таблица по сценариям | метрика без определения → reject | BACKLOG |
| E2.3 | Отчёт и решение: нужен ли DCP adapter; без нового RAG | E2.2 | `docs/reports/retrieval-adequacy.md` | вывод по каждому пробелу + решение | «нужен DCP» без цифр → reject | BACKLOG |

**DoD блока E:** corpus зафиксирован; метрики воспроизводимы; решение по DCP основано на пробелах, а не на гипотезе.

---

## 9. Блок F — SECURITY (P0 statement, P1 tests)

**Цель:** явно ограничить trust boundary и доказать, что содержимое памяти не меняет policy/authority.

### F0 — карта

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| F0.1 | Карта угроз: `by=user` как auth, fixture identity, prompt injection через claim/plan/source, project leak, policy change | — | `docs/kanban/maps/F-security.md` | каждая угроза имеет test/decision | угроза без mitigation → reject | BACKLOG |

### Ветка F1 — boundary

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| F1.1 | Записать trust boundary: local single-owner, доверенная машина, доверенный project dir; нет production auth | — | `docs/security/trust-boundary.md` | документ явно перечисляет, что не является auth | `by=user` назван auth → reject | READY |
| F1.2 | `by=user` и fixture identity не меняют policy; тест на попытку | F1.1 | `tests/check_security_boundary.py` | policy log не меняется | policy изменилась → fail | BACKLOG |
| F1.3 | Hook injection помечает память как untrusted data, не как инструкции | F1.1 | `opencode/index.js`, `claude/hooks/*` | в выводе есть явная метка untrusted | текст claim исполняется как инструкция → fail | BACKLOG |

### Ветка F2 — injection/isolation

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| F2.1 | Malicious claim/plan/source не меняет policy/authority | F1.2 | `tests/check_injection.py` | policy/authority неизменны | tool call из claim → fail | BACKLOG |
| F2.2 | Project isolation: claim проекта A не виден в B | F1.2 | `tests/check_project_isolation.py` | B не видит A | утечка → fail | BACKLOG |
| F2.3 | Injection corpus: 10 вредоносных текстов, все без эффекта | F2.1 | `tests/fixtures/security/injection.jsonl` | 10/10 no-effect | один эффект → fail | BACKLOG |

**DoD блока F:** trust boundary записан; `by` не auth; injection corpus без эффекта; project isolation доказана.

---

## 10. Блок G — SEMANTIC 0.2.5 (волна 0)

**Цель:** выпустить warrant + review binding + read-only audit + gold corpus + skill v2.
**Точный код и порядок:** `docs/superpowers/plans/2026-11-19-semantic-bridge-0.2.5.md` (Tasks 1–4). Карточки ниже — исполнимая обвязка; план — источник точных сниппетов.

### G0 — карта отказов

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| G0.1 | Карта отказов warrant/audit: missing, empty, long, equal, corrupt digest, stale review, legacy, audit writes, audit re-runs gates, DTO drop, tree drift, status tuple, archived/disputed/loop, source/candidate digest tamper, no effective, withdrawn, anchor offset/group mismatch | — | `docs/kanban/maps/G-semantic.md` | каждая дыра имеет detection + card | дыра без detection → reject | READY |

### Ветка G1 — warrant contract

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| G1.1 | RED: в `check_distillate_pipeline.py` добавить `count_rows`, `WARRANT`, `WARRANT2`, отказ без warrant (None/''/'   '/'x'*301/равен тексту), проверку отсутствия записи; warrant на все 11 propose; conflict на другой warrant; warrant после lost-basis repair | G0.1 | `tests/check_distillate_pipeline.py` | `& $py -B tests/check_distillate_pipeline.py` → FAIL по warrant | отказ пишет claim/event → fail | READY |
| G1.2 | RED: `check_distillate_audit.py` — warrant в propose/review helper; archived-source propose с warrant; `check_statement_capture.py` — v1 без warrant | G1.1 | `tests/check_distillate_audit.py`, `tests/check_statement_capture.py` | оба теста FAIL по ожидаемым полям | negative anchor тест маскирует warrant → fail | READY |
| G1.3 | GREEN: `_clean_warrant`, `_warrant_value`, `_warrant_public`, `_public_version`, `_propose` (same-text + lost basis_spans), dispatcher, `DISTILLATE_ACTIONS`, `WARRANT_MAX` | G1.2 | `opencode/mockup/distillate.py` | три теста PASS в `opencode` | same-text возвращает ALREADY_RECORDED при другом warrant → fail | READY |
| G1.4 | GREEN plumbing: `Service.distillate`, `server._handle_distillate`, `LITE_SIGNATURE`, `TOOL_DOCS`, `_HELP`; mirror в `claude/`; SHA256 | G1.3 | `admission.py`, `server.py`, `claim_entry.py`, `public_dto.py`, `claude/mockup/*` | `distillate.py`, `admission.py`, `server.py`, `claim_entry.py`, `public_dto.py` — хэши совпадают | drift → fail | READY |

**Команды G1:**

```powershell
$py = 'E:\opencode_harness_portable\.venv\Scripts\python.exe'
& $py -B tests/check_distillate_pipeline.py
& $py -B tests/check_distillate_audit.py
& $py -B tests/check_statement_capture.py
```

### Ветка G2 — review binding

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| G2.1 | RED: review хранит `warrant_digest`; изменение warrant → `REVIEW_STALE`, `show` → `proposed`; legacy review без warrant проходит; corrupt `basis_spans.candidate_digest` → review stale | G1.4 | `tests/check_distillate_audit.py` | тест FAIL до GREEN | stale review остаётся `validated` → fail | READY |
| G2.2 | GREEN: `_review_pass` (candidate digest + warrant), `_state` (`passed` для accepted/validated), `_accept`, `_validate` (corrupt warrant → `PROPOSAL_INVALID(warrant)`, `warrant_digest` в review) | G2.1 | `opencode/mockup/distillate.py` | тест PASS | `_state` не вызывает `_review_pass` → fail | READY |
| G2.3 | Repair paths: same-text repair, lost-`basis_spans` repair, legacy repair; повторный validate/accept | G2.2 | `distillate.py`, `check_distillate_audit.py` | legacy repair → accepted; audit `WARRANT_MISSING` до, clean после | repair без warrant → fail | READY |

### Ветка G3 — audit

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| G3.1 | RED: clean v1 → `AUDITED/OK`; audit без target → `INVALID_FIELD`; unknown source → `STATEMENT_NOT_FOUND`; archived → `ATTENTION/SUPERSEDED`; disputed → `BLOCKED/DISPUTED`; corrupt candidate digest → `BLOCKED/CANDIDATE_DIGEST_MISMATCH + REVIEW_STALE`; counts before/after | G2.3 | `tests/check_distillate_audit.py` | тест FAIL до GREEN | audit пишет → fail | READY |
| G3.2 | GREEN: `_finding`, `_audit_actions`, `_audit_next`, `_audit`; порядок findings; health roll-up; metrics; `next_steps` | G3.1 | `opencode/mockup/distillate.py` | тест PASS | audit вызывает `run_checks`/`annotate`/`update` → fail | READY |
| G3.3 | DTO: `Finding`, optional `warrant/metrics/findings/health/allowed_actions/blocked_actions`, `AUDITED` в status tuple, help text | G3.2 | `opencode/mockup/public_dto.py`, `claim_entry.py` | `project_tool` сохраняет все поля | поле теряется → fail | READY |
| G3.4 | Read-only proof: статический grep запрещённых вызовов + динамические counts | G3.3 | `tests/check_distillate_audit.py` | grep пуст; counts равны | скрытая запись → fail | READY |

**Запрещённые вызовы в `_audit`:** `run_checks`, `_write_property`, `ce.annotate`, `ce.update`, `svc.capability('submit')`.

### Ветка G4 — gold corpus

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| G4.1 | 18 кейсов в `corpus.jsonl` по таблице плана §3.1: RU/EN clean, accepted, refusals, source digest, V4/V5/V6, disputed, archived, loop, stale, legacy, warrant long/corrupt, withdrawn, legacy repair | G3.4 | `tests/fixtures/semantic_audit/corpus.jsonl` | 18 строк, каждая с `expect` | кейс без exact expected → reject | READY |
| G4.2 | Runner: exact per-case, recall/precision как отчёт, оба дерева, temp project, timeout | G4.1 | `tests/check_semantic_audit.py` | `18/18 exact`, recall 1.000, precision 1.000 на дерево | порог вместо exact → reject | READY |
| G4.3 | Stdio probe: tools/list содержит `dom_distillate`; audit → `AUDITED`; propose без warrant → `PROPOSAL_INVALID`; с warrant → `PROPOSED` | G4.2 | `tests/check_semantic_audit.py` | probe PASS или явный SKIP при отсутствии `mcp` | SKIP выдан за PASS → fail | READY |

### Ветка G5 — skill и docs

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| G5.1 | Skill v2: когда propose, когда спросить; anchors; warrant RU/EN; progressive updates; audit; legacy; anti-patterns | G3.4 | `claude/skills/dom-memory/SKILL.md`, `opencode/skills/dom-memory/SKILL.md` | файлы byte-identical; примеры good/bad | skill обещает semantic judge → reject | READY |
| G5.2 | `claim-brief.md`: §12.5 pipeline, §12.15 warrant, §12.16 audit, §12.13 limits, §15 non-goals | G5.1 | `docs/claim-brief.md` | разделы есть; «form, not truth» явно | audit назван validator → reject | READY |
| G5.3 | `NOT_DONE.md`/`README.md`: v2 skill implemented; audit per-distillate; warrant not authenticated/judged; corpus 18 cases; версия 0.2.5 | G5.2 | `NOT_DONE.md`, `README.md` | текст совпадает с фактическим поведением | заявлено больше, чем есть → reject | READY |

**DoD блока G:** все G-карточки `DONE`; три stage-3 теста + corpus зелёные в обоих деревьях; DTO сохраняет audit; audit не пишет; skill/docs честно ограничивают гарантии.

---

## 11. Блок H — ADVERSARIAL RUN (волна 0)

**Цель:** живой прогон агентом всех возможных ситуаций с поиском дыр, молчаливых падений и ложной готовности. Это не «ещё один тест», а отдельный контур доказательств.

### H0 — карта прогона

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| H0.1 | Карта сценариев и молчаливых отказов: S1–S15, матрица 43 кейсов, stop rules | G0.1 | `docs/kanban/maps/H-live-run.md` | каждый S имеет detection; каждый кейс — expected | сценарий без expected → reject | BLOCKED by G |

### Ветка H1 — матрица сценариев

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| H1.1 | Happy paths: RU v1, EN v1, RU accepted, EN accepted, legacy repair | G4.2 | `docs/kanban/runs/2026-11-20-live-run.md` | 5/5 expected status/health/effective | ложный OK → fail | BLOCKED by G |
| H1.2 | Refusals: no/empty/space/301/equal warrant; missing/ambiguous anchor; archived; disputed; stale; loop; validate v1; accept without validate; withdraw→accept | G4.2 | тот же | каждый отказ + counts unchanged | отказ пишет → stop | BLOCKED by G |
| H1.3 | Corruption: source digest, warrant digest, candidate digest, event hash, previous hash, review digest, claim raw | G4.2 | тот же | каждый код различим; projection не врёт | corrupt как valid → stop | BLOCKED by G |
| H1.4 | Lifecycle: superseded, withdrawn, no effective, anchor offset/group, warrant too long/equal after annotate, legacy missing, repair | G4.2 | тот же | health/actions совпадают с матрицей | health OK при blocking → stop | BLOCKED by G |
| H1.5 | Host: оба дерева, stdio, full restart, live MCP, stale process, install hash, old tool surface | G4.3 | тот же | после restart audit/warrant доступны | старый процесс отвечает → stop | BLOCKED by G |

**Матрица 43 кейсов (сводка):**

| Группа | Кол-во | Что проверяет |
|---|---:|---|
| Happy | 5 | v1, accepted, legacy repair, RU/EN |
| Refusals | 14 | warrant, anchors, lifecycle, stale, loop |
| Corruption | 8 | source/warrant/candidate/event/prev/review/claim/projection |
| Lifecycle | 9 | superseded, withdrawn, no effective, anchor mismatch, legacy |
| Host | 7 | both trees, stdio, restart, live, stale, install, surface |

### Ветка H2 — охота за молчаливыми отказами

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| H2.1 | S1: отказ пишет в ledger | H1.2 | counts | counts до = после | +1 → stop | BLOCKED by G |
| H2.2 | S2: status/state/health расходятся | H1.4 | show/audit/accept | `validated` ⇒ accept не `REVIEW_STALE`; `BLOCKED` ⇒ есть blocking finding | расхождение → stop | BLOCKED by G |
| H2.3 | S3: audit пишет | H1.1 | counts + grep | counts равны; grep пуст | запись → stop | BLOCKED by G |
| H2.4 | S4: audit повторно гоняет V1–V8 | H1.1 | static grep + timing | нет `run_checks`; время не зависит от V1–V8 | скрытый вызов → stop | BLOCKED by G |
| H2.5 | S5: DTO теряет поля audit | H1.1 | `project_tool` | все ключи на месте | поле потеряно → stop | BLOCKED by G |
| H2.6 | S6: tree drift | G1.4 | hash script | все shared-файлы равны | расхождение → stop | BLOCKED by G |
| H2.7 | S7: stale live process | H1.5 | tools/list + version | после restart новая поверхность | старый процесс → stop | BLOCKED by G |
| H2.8 | S8: исключение проглатывается, ответ пустой | H1.1–H1.4 | stderr/exit code | любой сбой виден в коде/тексте | пустой `OK` → stop | BLOCKED by G |
| H2.9 | S9: повторный вызов создаёт дубль | H1.2 | counts/idempotency | один результат | дубль → stop | BLOCKED by G |
| H2.10 | S10: review не привязан к warrant | H1.4 | review digest | stale ловится | accept проходит → stop | BLOCKED by G |
| H2.11 | S11: archived source всё ещё writable | H1.4 | actions | propose/validate/accept blocked | запись → stop | BLOCKED by G |
| H2.12 | S12: project isolation leak | H1.1 | two projects | A не виден в B | утечка → stop | BLOCKED by G |
| H2.13 | S13: hook failure блокирует host | H1.5 | host log | host продолжает | падение → stop | BLOCKED by G |
| H2.14 | S14: plan ingest partial state | H1.4 | plan tree | нет полу-архива | полу-состояние → stop | BLOCKED by G |
| H2.15 | S15: ложная готовность (SKIP как PASS) | H1.5 | evidence | SKIP помечен SKIP | SKIP=PASS → stop | BLOCKED by G |

### Ветка H3 — evidence и реестр дыр

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| H3.1 | Журнал прогона: environment, команды, выводы, вердикты | H1.1–H1.5 | `docs/kanban/runs/2026-11-20-live-run.md` | каждая строка матрицы имеет evidence | строка без evidence → reject | BLOCKED by G |
| H3.2 | Реестр дыр: severity, repro, expected/actual, card на фикс | H2.1–H2.15 | `docs/kanban/runs/2026-11-20-holes.md` | каждая дыра имеет severity + card | дыра без card → reject | BLOCKED by G |
| H3.3 | Re-run после фиксов; critical/major закрыты или явно приняты владельцем | H3.2 | тот же | 0 open critical/major | открытая critical → релиз стоп | BLOCKED by G |

**Stop rules (приоритет):**
1. Любой S1–S5, S7–S8, S10–S11, S15 → немедленный стоп релиза.
2. Любая critical-дыра → стоп.
3. Major → фикс или письменное принятие владельцем.
4. Minor → запись в реестр, можно отложить.

**Формат evidence:**

```text
| case | tree | action | expected | actual | verdict | evidence |
|---|---|---|---|---|---|---|
| ru-clean-v1 | opencode | audit | AUDITED/OK | AUDITED/OK | PASS | run.md#L42 |
```

---

## 12. Блок I — RELEASE 0.2.5 (волна 0)

**Цель:** выпустить 0.2.5 только после G и H, обновить dist и install target, перезапустить live MCP и доказать поверхность.

### I0 — release gate

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| I0.1 | Release gate: A2.1–A2.3, B0.1, G, H3.3 без open critical/major | A2.3, B0.1, G, H3.3 | `docs/kanban/runs/2026-11-20-release-gate.md` | все пункты PASS | любой BLOCKED → не начинать I1 | BLOCKED |

### Ветка I1 — version и suite

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| I1.1 | Bump 0.2.5: `VERSION`, `claude/VERSION`, `opencode/VERSION`, `claude/.claude-plugin/plugin.json`, `opencode/package.json`, `pyproject.toml`, `README.md` | I0.1 | 7 файлов | `Select-String ... -Pattern '0\.2\.5'` → все | один файл 0.2.4 → fail | BLOCKED |
| I1.2 | Full suite: 9 checks, оба дерева, exit codes | I1.1 | `docs/kanban/runs/2026-11-20-full-suite.md` | все PASS; SKIP явно помечен | FAIL спрятан → стоп | BLOCKED |

**Команды I1.2:**

```powershell
$py = 'E:\opencode_harness_portable\.venv\Scripts\python.exe'
& $py -B tests/smoke_cold_start.py
& $py -B tests/check_plan_context.py
& $py -B tests/check_inbox_status.py
& $py -B tests/check_plan_identity.py
& $py -B tests/check_statement_capture.py
& $py -B tests/check_distillate_extract.py
& $py -B tests/check_distillate_pipeline.py
& $py -B tests/check_distillate_audit.py
& $py -B tests/check_semantic_audit.py
```

### Ветка I2 — artifacts

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| I2.1 | Build dist: `tools/build_zip.py`, удалить старый 0.2.4 zip, проверить содержимое | I1.2 | `dist/` | zip содержит VERSION 0.2.5 и изменённые модули | старый zip остался → fail | BLOCKED |
| I2.2 | Install target: `Copy-Item claude/*` + отдельно `claude/.claude-plugin` | I2.1 | install target | `VERSION` 0.2.5; `plugin.json` 0.2.5 | `.claude-plugin` 0.2.4 → fail | BLOCKED |
| I2.3 | Hash verification: 6 ключевых файлов repo vs install | I2.2 | install target | все строки `True` | один False → fail | BLOCKED |

**Install target:**
`E:\opencode_harness_portable\projects\omh-baseline-dom-mcp\.claude\worktrees\mcp-llm-server-054018\dist\claude\dom-memory`

### Ветка I3 — live и финал

| ID | Карточка | Зависит | Файлы | Проверка → ожидание | Fault probe | Статус |
|---|---|---|---|---|---|---|
| I3.1 | Commit + push: `release: 0.2.5 semantic bridge and audit` | I2.3 | git | `HEAD` = `origin/main` | push отклонён → не DONE | BLOCKED |
| I3.2 | Full restart live MCP + stdio probe: tools/list, audit, propose без warrant, propose с warrant | I3.1 | `C:\Temp\opencode\probe_mcp.py` | audit `AUDITED`; без warrant `PROPOSAL_INVALID`; с warrant `PROPOSED` | старый процесс → restart снова | BLOCKED |
| I3.3 | Final state + closeout: `git status`, `git log -5`, версии, evidence, обновить доску | I3.2 | `docs/kanban/runs/2026-11-20-release.md` | дерево чистое (кроме runtime); HEAD = origin/main | грязное дерево → не DONE | BLOCKED |

**Важно:** live MCP загружает install target при старте. После копирования нужен полный перезапуск процесса; «горячая перезагрузка» не считается доказательством.

---

## 13. Матрица отказоустойчивости (сводная)

| # | Класс отказа | Где | Ожидаемый сигнал | Молчаливый провал | Карточка |
|---|---|---|---|---|---|
| F1 | Нет warrant | propose | `PROPOSAL_INVALID(field=warrant)`, нет записи | negative anchor тест маскирует | G1.1 |
| F2 | Warrant пустой/пробельный | propose | `PROPOSAL_INVALID(warrant)` | `strip()` не применён | G1.1 |
| F3 | Warrant > 300 | propose | `PROPOSAL_INVALID(warrant)` | off-by-one на границе | G1.1 |
| F4 | Warrant = candidate | propose | `PROPOSAL_INVALID(warrant)` | нормализация не учла регистр/пробелы | G1.1 |
| F5 | Другой warrant на тот же текст | propose | `PROPOSAL_CONFLICT` | early same-text branch вернул `ALREADY_RECORDED` | G1.3 |
| F6 | Изменённый warrant после validate | accept/show | `REVIEW_STALE`; state `proposed` | `_state` не вызвал `_review_pass` | G2.2 |
| F7 | Corrupt warrant digest | validate/audit | `PROPOSAL_INVALID(warrant)`; `WARRANT_DIGEST_MISMATCH` | validate переиспользовал старый review | G2.2 |
| F8 | Corrupt source digest | audit | `SOURCE_DIGEST_MISMATCH`, `BLOCKED` | audit доверяет metadata | G3.2 |
| F9 | Corrupt candidate digest | audit/accept | `CANDIDATE_DIGEST_MISMATCH` + `REVIEW_STALE` | `_review_pass` игнорирует spans | G2.2 |
| F10 | Audit пишет | audit | counts не меняются | скрытый `_write_property` | G3.4 |
| F11 | Audit гоняет V1–V8 | audit | нет `run_checks` | audit вызывает `_validate` | G3.4 |
| F12 | DTO теряет audit-поля | projection | все ключи на месте | optional-поля не добавлены | G3.3 |
| F13 | Tree drift | оба дерева | hash mismatch | копирование забыто | G1.4/C1.1 |
| F14 | Stale live process | host | после restart новая поверхность | tools/list закеширован | I3.2 |
| F15 | Archived source пишется | audit/actions | `propose/validate/accept` blocked | проверка archive после warrant | G3.2 |
| F16 | Disputed source | audit | `DISPUTED`, `BLOCKED` | conflict query не видит current | G3.2 |
| F17 | Loop | audit | `DISTILLATE_LOOP`, `BLOCKED` | fail count только по current | G3.2 |
| F18 | No effective | audit | `NO_EFFECTIVE`, `BLOCKED` | effective посчитан неверно | G3.2 |
| F19 | Legacy без warrant | validate/accept | работает; audit `WARRANT_MISSING` | новый review требует warrant | G2.1 |
| F20 | Anchor offset/group mismatch | audit | blocking-код | audit только считает anchors | G3.2 |
| F21 | Частичная запись ledger | submit/review/admit | отказ, counts без изменений | transaction закоммитил часть | B1.1–B1.4 |
| F22 | Replay дубль | idempotency | один результат | второй event | B2.1 |
| F23 | Corrupt chain | verify | различимые коды | общий `CORRUPT` | B3.1–B3.5 |
| F24 | Concurrent writer | update/plan | один wins | lost update | B4.1–B4.3 |
| F25 | Hook timeout | host | child killed, host жив | orphan/duplicate | C2.2 |
| F26 | Plan partial ingest | plan | незатронутые активны | массовый archive | D2.3 |
| F27 | Injection из claim | host | policy не меняется | текст исполнен как инструкция | F2.1 |

---

## 14. Живой агентский прогон: протокол

### 14.1. Роли прогона

| Роль | Задача |
|---|---|
| `run-orchestrator` | выбирает кейс, создаёт scratch project, фиксирует expected |
| `run-agent` | выполняет действия через MCP/сервис, не подглядывая в expected-код |
| `run-observer` | записывает фактические статусы, counts, health, actions |
| `run-reviewer` | атакует вердикт: где мог быть молчаливый провал |
| `run-auditor` | проверяет полноту evidence и stop rules |

### 14.2. Порядок

1. Зафиксировать baseline hash и версию (`A2.2`).
2. Создать отдельный scratch project на каждый кейс; не использовать рабочую `.dom-memory`.
3. Выполнить кейс; после каждого действия записать `status`, `health`, `findings`, `allowed/blocked`, counts.
4. Для отказа — доказать отсутствие записи.
5. Для успеха — доказать переход состояния.
6. Для corruption — доказать различимый код и отказ projection.
7. Для host — полный restart, не hot reload.
8. Свести в таблицу; каждое расхождение — в реестр дыр.
9. Re-run после фиксов; закрыть critical/major.

### 14.3. Stop rules

- Любой молчаливый провал S1–S5, S7–S8, S10–S11, S15 — стоп.
- Любая critical-дыра — стоп.
- Major — фикс или письменное принятие владельцем.
- Minor — запись, можно отложить.

### 14.4. Evidence

- `docs/kanban/runs/2026-11-20-environment.md` — версии, commit, hashes.
- `docs/kanban/runs/2026-11-20-live-run.md` — матрица и выводы.
- `docs/kanban/runs/2026-11-20-holes.md` — реестр дыр.
- `docs/kanban/runs/2026-11-20-release.md` — финал.

---

## 15. Реестр решений

| ID | Решение | Статус | Основание |
|---|---|---|---|
| D-001 | `prod` (alias `lite`) = выпущенная рабочая линия; `lab` (alias `strict`) = подготовка следующих версий/отработка фич; одна продуктовая линия, не два продукта; runtime-имена не меняются в 0.2.5 | ПРИНЯТО | запрос владельца + `server.py` |
| D-002 | 0.2.5 = warrant + review binding + read-only audit + gold corpus + skill v2; без semantic judge | ПРИНЯТО | spec/plan 0.2.5 |
| D-003 | Warrant обязателен на `propose`: одно предложение, 1–300 символов, не равен кандидату; breaking change | ПРИНЯТО | plan §Global Constraints |
| D-004 | Audit read-only, per-distillate, без V1–V8 и без project-wide scan | ПРИНЯТО | spec §4 |
| D-005 | Gold corpus: exact per-case; recall/precision — отчёт, не порог | ПРИНЯТО | plan §3.2 |
| D-006 | Никаких новых зависимостей; runtime детерминированный, offline | ПРИНЯТО | plan §Global Constraints |
| D-007 | P0 ledger не пропускается: B0.1 — gate волны 0; полный B — волна 1; дыра в ledger останавливает релиз | ПРИНЯТО | review §4, §10 |
| D-008 | Две базы: live MCP — `omh-baseline-dom-mcp/.dom-memory` (project_root 2026-11-12; 103 `lite-v1` + 8 `stmt-extract-v1` на 2026-11-22), отдельная база плагина — `opencode-dom-memory-plugin/.dom-memory` (2026-11-16, 103 claims, все `lite-v1`); клод-эпоха не переносилась; доска и П5–П13 пишутся в live-базу | ПРИНЯТО (уточнено 2026-11-22) | SQL-дамп обеих баз |
| D-009 | Оба дерева byte-identical для shared-файлов; SHA256 перед каждым коммитом | ПРИНЯТО | plan §Review Focus |
| D-010 | `.opencode/` — local config/runtime до решения A2.4; по умолчанию не коммитится | ОТКРЫТО | review §9 |
| D-011 | Релиз требует полного restart live MCP + probe; hot reload не доказательство | ПРИНЯТО | plan §5.5 |
| D-012 | Карточка `DONE` только с evidence; SKIP ≠ PASS | ПРИНЯТО | фабричный процесс |
| D-013 | Маршрутизация скилов, агентов, моделей и инструментов — ответственность хоста, оркестратора, агентского runtime или пользователя, не DOM; Capability Registry остаётся внешней сущностью | ПРИНЯТО | запрос владельца 2026-11-22; `положения.md` §5.2 п.8, §5.3, U-013 |
| D-014 | `PlanStatus` DTO обязан содержать `inbox_queued`/`inbox_failed`; публичная проекция — обязательная часть теста, а не только сервис | ПРИНЯТО | live repro `UNKNOWN_FIELD $.inbox_queued`; B0.3; `check_inbox_status.py` |

---

## 16. Открытые вопросы ревью → карточки

| # | Вопрос | Куда |
|---|---|---|
| 1 | Какой commit/ветка — baseline: только `f60cd84` или local uncommitted? | A2.1, A2.2 |
| 2 | Есть ли maintainer venv и полный лог двух stage-3 тестов? | A2.2, B0.1 |
| 3 | Что авторитетно для Capability Registry: host MCP list, manifest, probe, provider API, ручная декларация? | после E; вне 0.2.5 |
| 4 | DCP только для plan task или для произвольной операции? | E2.3 |
| 5 | Trust boundary: local single-owner, multi-agent на одной машине, shared project? | F1.1 |
| 6 | Вводить ли `new_plan` в следующем minor? | D1.2 |

---

## 17. Журнал статусов

Обновляется после каждой карточки. Источник истины — эта таблица, не статусы в таблицах веток (они могут отставать).

| Дата | Карточка | Было | Стало | Evidence | Кто |
|---|---|---|---|---|---|
| 2026-11-20 | — | — | — | доска создана | orchestrator |
| 2026-11-22 | — | — | — | зафиксирована граница: маршрутизация скилов/агентов/моделей/инструментов — не DOM; `положения.md` §5.2 п.8, §5.3, U-013; claim `5140706e` | orchestrator |
| 2026-11-22 | B0.3 | — | DONE | RED `UNKNOWN_FIELD $.inbox_queued`; GREEN оба дерева; `check_inbox_status.py` PASS; SHA256 `public_dto.py` совпадает | orchestrator |
| 2026-11-22 | — | — | — | доска занесена в live MCP: план П5–П13 (r16, 43 пункта, 0 archived), решения D-001–D-012, root claim `55b50e87`, code claim `f48f7a88`; live `dom_plan_status` всё ещё 0.2.4 → B0.3 до restart | orchestrator |
| 2026-11-23 | — | — | — | два строгих аудита отклонили смешанный план; принят split D-020: 0.2.6 — наблюдаемость, 0.3.0 — WriteContext/claim_sessions/read_receipts/гейт | orchestrator |
| 2026-11-23 | J0.1 (new) | — | DONE | единый resolver `paths.py`, `DOM_DIRECTORY` wins; `dom_describe` отдаёт `directory`/`db_path`; `memory_preflight.py` + registry; tests PASS; probe `DESCRIBED`; evidence `docs/kanban/runs/2026-11-23-task0-memory-paths.md` | orchestrator |
| 2026-11-23 | J1.1 (new) | — | DONE | `observability.js`: per-PID JSONL, 64 KiB cap, retention, redaction, `dropped`; hooks record duration/session/injected/db_path; `node --test tests/check_observability.mjs` 3/3; evidence `docs/kanban/runs/2026-11-23-task1-observability.md` | orchestrator |
| 2026-11-23 | J2.1 (new) | — | DONE | `snapshot`: `dom.prompt-snapshot/2`, system/messages/tools/options/dom.notes, hash before truncation, redaction, 300k truncation test, registered-handler integration; `node --test tests/check_prompt_snapshot.mjs` 3/3; evidence `docs/kanban/runs/2026-11-23-task2-snapshot.md` | orchestrator |
| 2026-11-23 | J3.1/J3.2 (new) | — | DONE | monitor API: health/claims/claim/hooks/snapshots/snapshot/plan; token, Host, no CORS, traversal, corrupt DB; `check_monitor_api.py` PASS; real probe `CHAIN_OK`, 212 claims, r16 | orchestrator |
| 2026-11-23 | J4.1 (new) | — | DONE | web UI: Hooks & injections, DOM claims, Plan, Health; snapshot blocks; `textContent` only; `check_monitor_ui.py` PASS; real UI 200 | orchestrator |
| 2026-11-23 | J5.1 (new) | — | DONE | MCP plan-ingest probe: 10 runs, p50 236.591 ms, p95 342.362 ms, all `PLAN_INGESTED`; missing `origin/session/base_revision/contexts/stamp`; no production change; evidence `docs/kanban/runs/2026-11-23-mcp-plan-ingest.md` | orchestrator |
| 2026-11-23 | J6.1 (new) | — | DONE | release card `docs/releases/0.2.6.md`: checks, dist/install/restart/push gates, rollback, known gaps; VERSION не менялся | orchestrator |
| 2026-11-23 | I0.1 (0.2.6) | — | DONE | live 0.2.6: install target обновлён, backup 0.2.4, service restart, live MCP `DESCRIBED`/`PLAN_STATUS`, 13 snapshots, monitor `CHAIN_OK`/224 claims; evidence `docs/kanban/runs/2026-11-23-live-0.2.6.md` | orchestrator |
| 2026-11-23 | I1.1 (0.2.6) | — | DONE | GitHub release `v0.2.6` с assets `opencode-dom-memory-0.2.6.zip` и `dom-memory.plugin`; tags `v0.2.4`/`v0.2.6`; main `b48034c` | orchestrator |
| 2026-11-23 | — | — | — | three-line policy `docs/VERSIONS.md`: live main+v0.2.6, pre-release `feature/trusted-write-loop-0.3.0`, planned 0.3.1+ | orchestrator |
| 2026-11-24 | — | — | — | monitor: local token `start.py`, claims versions/properties, plan tree, snapshot block origins; analysis `docs/kanban/runs/2026-11-24-monitor-analysis.md`; plan r16 mixes legacy P1–P4 and current P5–P13 | orchestrator |
| 2026-11-24 | — | — | — | baseline DOM index: `plan_check_hook.py` prints plan/revision/items/open/claims when no check is due; `check_dom_index_injection.py` PASS; install target hook updated; D-021…D-025 claim-organization requirements recorded | orchestrator |
| 2026-11-24 | — | — | — | DOM brief in `dom.notes`: ROOT→LINE→CURRENT baselines, ACTIVE pin, DONE/PLANNED, RISKS, RECENT MAIN, ANCHORS, VECTOR, CHECKLIST; live plan auto-selects П1 and warns `ACTIVE: not marked`; evidence `docs/kanban/runs/2026-11-24-dom-brief.md` | orchestrator |
| 2026-11-24 | — | — | — | brief cache per session: prompt stores the brief, context re-injects it on tool continuations; `check_prompt_snapshot.mjs` asserts two consecutive snapshots carry `dom.notes`; live `index.js` updated + service restarted | orchestrator |
| 2026-11-24 | — | — | — | plan r17: added П14 «Хуки, инъекции и notes-контур» + П14.1–П14.5; root properties `main_task`, `main_task_meaning`, `current_task=П14.2`, `gits`; current header properties meaning/why/now/vector/boundaries/tools/gits; notes schema `docs/concepts/2026-11-24-notes-schema.md` | orchestrator |
| 2026-11-24 | — | — | — | notes v2: `NEXT` ordered (plan → missing graph links → conflicts → risks), `RISK CHECKS`, conflict paths, dynamic checklist, `GRAPH` roles; P14.2 linked implements+validates (4 work claims); schema/process doc updated | orchestrator |
| 2026-11-24 | — | — | — | P14.3/P14.4: `mockup/promptizer.py` (per-variable specs, mini-prompts, sentence distillation, source order property→context→claim→not recorded); brief `SOURCES` line (plan/root/property/context/claim anchors); note fits 3400 chars without truncating the checklist | orchestrator |
| 2026-11-24 | — | — | — | plan r18: П14.1–П14.4 `done` (73 items, 56 open); `current_task=П14.5` pinned; П14.5 header properties meaning/why/now/vector/boundaries/tools/gits; claims П14.3 `a12cd3cc`+`3aba41e6`, П14.4 `221a724f`+`9ccfd5e8` (implements+validates) | orchestrator |
| 2026-11-24 | — | — | — | plan r19: П14.5 and П14 `done` (node fully closed); live snapshot 03:49 notes=1 with `SOURCES`, 2939 chars; brief falls back to auto when `current_task` is done and says “pin the next block”; prompt budget measured (86% skills catalog, DOM tools 842 chars) | orchestrator |
| 2026-11-24 | — | — | — | plan r20: new П15 «0.3.0 Trusted write loop» + П15.1–П15.8 (AO…AO800) with header contexts; `current_task=П15` pinned; `NEXT` prefers open children of the current block (П15.1–П15.4 shown) | orchestrator |
| 2026-11-24 | — | — | — | plan r21: П16 «0.3.1 Panel and skill routing» + П16.1 Native Panel, П16.2 Skill router concept (AP, AP100, AP200); concept doc `docs/concepts/2026-11-24-skill-router-0.3.1.md` (Б1–Б7, four layers, KPI, risks, open decisions); claims `0bfeeeab` (doc) + `2103ac97` (decision) | orchestrator |
| 2026-11-24 | — | — | — | plan r22: П16.3 Queue channel spike (AP300); 0.3.1 channel decision — queue is a keyed `dom.skill-queue` instruction entry announced at the next step boundary, `dom.notes` stays the plan brief; registry `ctx.skill.list()`, weights `ctx.storage` + ledger snapshots, gate/queue_miss via `tool.hook` | orchestrator |
| 2026-11-24 | — | — | — | prompt map from a live snapshot: system 86 361 = 80.2% `core/skill-guidance` (69 276), `core/codemode` 8 901, dom 2 774, harness 2 000; tools 13 055, messages 9 577; layout doc `docs/concepts/2026-11-24-prompt-block-layout.md` (placement rules, sensing contract, intensity table, learning loop, experiments E1–E5) | orchestrator |
| 2026-11-24 | — | — | — | E4 measured: +6 013-char probe skill → catalog 69 276 → 75 094 (+5 818); `ctx.skill.list()` sees only 17 skills (bundled+superpowers), not the 165 config ones; removing `brainstorming` cut 324 chars (75 094 → 74 770); probe not removable; restore 68 980 after restart. E4 fails for the 69k catalog → 0.3.1 stays additive (queue entry, outcomes, weights), catalog shrink moves to 0.4 | orchestrator |
| 2026-11-24 | — | — | — | E6 measured: `permissions deny *` + `allow dom-dashboard` → catalog 69 276 → **575** chars, 1 skill; a context hook replaced `E6-MARKER` inside `core/skill-guidance` with the live queue (`E6-INJECTED`), so arbitrary-region substitution works; `deny` also blocks loading → dashboard must inject bodies or allow per agent; restore 166 | orchestrator |

### 17.1. Метрики доски

| Метрика | Значение |
|---|---|
| Всего карточек | 107 |
| DONE | 0 |
| READY | 25 |
| BACKLOG | 48 |
| BLOCKED | 33 (из них 24 — `BLOCKED by G`) |
| FAILED | 0 |
| Открытых critical | 0 |
| Открытых major | 0 |

---

## 18. Definition of Done доски

### 18.1. Волна 0 (0.2.5)

- [ ] A1.1–A1.3, A2.1–A2.5 `DONE`.
- [ ] B0.1 `DONE`: integrity smoke без corrupt state.
- [ ] F1.1 `DONE`: trust boundary записан.
- [ ] G1.1–G5.3 `DONE`: warrant, binding, audit, corpus, skill/docs.
- [ ] H1.1–H3.3 `DONE`: 43 кейса, 0 open critical/major, evidence.
- [ ] I1.1–I3.3 `DONE`: версия, dist, install, live probe, финал.
- [ ] Все shared-файлы byte-identical.
- [ ] `VERSION` 0.2.5 везде; `plugin.json` 0.2.5.
- [ ] Live MCP после полного restart отдаёт `audit` и `warrant`.

### 18.2. Волна 1 (надёжность)

- [ ] B1.1–B4.3 `DONE`.
- [ ] C1.1–C2.4 `DONE`.
- [ ] D1.1–D2.4 `DONE`.
- [ ] F2.1–F2.3 `DONE`.
- [ ] Ни один refusal не оставляет частичных строк.
- [ ] Host contract suite идентичен на обоих деревьях.

### 18.3. Волна 2 (retrieval)

- [ ] E1.1–E2.3 `DONE`.
- [ ] Отчёт retrieval-adequacy с цифрами.
- [ ] Решение по DCP/Capability Registry зафиксировано.

### 18.4. Общее правило

Доска не считается `DONE`, если:
- есть карточка `DONE` без evidence;
- есть `SKIP`, выданный за `PASS`;
- есть расхождение версии/хэшей между деревьями;
- live-процесс отвечает старой версией;
- audit пишет в ledger;
- warrant можно обойти на `propose`.
