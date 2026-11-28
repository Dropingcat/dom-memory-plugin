# Аудит планирования DOM: проблемы и приоритет решений

**Дата:** 2026-11-28  
**Ревизия плана:** r33  
**Автор:** Agent (lite-owner)

---

## Сводка

Проведён аудит системы планирования DOM (база `omh-baseline-dom-mcp/.dom-memory/main/dom.sqlite3`). Выявлено **4 проблемы**, из которых 2 критические. Документ содержит аналитику по каждой проблеме, порядок исправления и приоритеты.

---

## Проблема 1 (КРИТИЧЕСКАЯ): `current_task = "П15"` — мёртвая ссылка

### Суть
На root claim `149d33e5` (`"Гони здесь"`) установлено свойство `current_task = "П15"`. Однако все 4 версии plan task П15 архивированы (`archived=1`):
- `"0.3.0 Trusted write loop"` rev='r23'
- `"Найти все MCP-серверные конфиги..."` rev='r26'
- `"Автоперехода нет..."` rev='r28'
- `"`card` — verified card has this skill"` rev='r29'

### Следствие
`project_brief()` в `mockup/brief.py` (строки 136–151) не находит активный П15:
```python
if explicit:  # explicit = "П15"
    found = planning.resolve_task(c, explicit)
    if found is not None:
        found = dict(found)
    if found and not found.get('archived') ...  # found.archived=True → пропуск
```
Флаг `explicit_active` остаётся `False`, и `_choose_current()` выбирает первый pending task (П1). LINE строится только из П1. П2–П7 не показываются в LINE, хотя видны в NEXT.

### Данные
```
Root claim 149d33e5, свойство current_task:
  seq=723: current_task = "П14.5"
  seq=780: current_task = "П15"
Plan task П15: 0 версий с archived=0
```

### Воспроизведение
Любой `project_brief()` текущей ревизии r33 даёт LINE из одного элемента:
```
LINE:
  П1 "0. Что есть в наличии" [GF pending]
```

### Приоритет исправления: **P0 — немедленно**

### Варианты решения

| # | Действие | Риск | Сложность |
|---|----------|------|-----------|
| 1 | `dom_annotate(claim_id=149d33e5, kind="property", name="current_task", value="П1")` | Может сбить существующий фокус сессии | 5 мин |
| 2 | Создать новый П15 с актуальной задачей | Нужно догнать план | 30 мин |
| 3 | Очистить `current_task` совсем (пустая строка) — автовыбор будет всегда | Пропадёт ручной пиннинг | 5 мин |

**Рекомендация:** Вариант 1 — простейшее исправление.

---

## Проблема 2 (КРИТИЧЕСКАЯ): `dom_plan_ingest` плодит мусорные `plan_task_id`

### Суть
Каждый вызов `dom_plan_ingest` создаёт **новую запись в `plan_tasks`** (`plan_task_id`) для того же самого пути плана, вместо того чтобы создавать новую версию в рамках существующего `plan_task_id`.

### Данные
```
Путь П1: 12 разных plan_task_id (r1..r33):
  pt-11e18d927bcb772c  branch=AA  v1-v7   rev=r1…r23
  pt-b2969f26f790571c  branch=AQ  v1-v2   rev=r23…r24
  pt-f16d4c5a363601bf  branch=AW  v1-v2   rev=r24…r25
  ...
  pt-8763ebaba1a8b513  branch=FZ  v1-v2   rev=r32…r33
  pt-6764ee4c4b7822da  branch=GF  v1      rev=r33

plan_task_versions: 811 записей на 375 plan_tasks
Active tasks (archived=0): 440 (при 29 уникальных путях)
```

### Следствие
- Раздувание БД: 440 активных записей при 29 реальных задачах
- `current_plan_tasks` view корректно фильтрует через `MAX(version)`, но:
  - Мёртвые `plan_task_id` занимают место в индексах
  - backup/restore весит больше, чем нужно
  - При повторном инжекте ломается связь старых claim → новая версия того же item

### Коренная причина
Механизм версионирования в `plan_ingest`: при изменении плана он создаёт новый `plan_task` вместо нового `plan_task_version` в существующем `plan_task`. Нужно проверить логику в `server.py` / `planning.py` — раздел маппинга path → plan_task_id.

### Приоритет исправления: **P1 — после P0**

### Варианты решения

| # | Действие | Риск | Сложность |
|---|----------|------|-----------|
| 1 | В `plan_ingest`: искать существующий `plan_task_id` по path, а не создавать новый | Ломает контракт — старые ingest не смогут обновить | 1 день |
| 2 | GC-скрипт: удалить мёртвые `plan_task_id` у которых все версии archived=1 | Нарушает append-only, нужен backup | 2 дня |
| 3 | Оставить как есть (view фильтрует) | База будет расти бесконтрольно | 0 |

**Рекомендация:** Вариант 1 — исправить `plan_ingest`.

---

## Проблема 3 (СРЕДНЯЯ): Двойной П1 на ревизии r33

### Суть
Одна ревизия `r33` имеет два разных `plan_task_id` для пути 'П1':
- `pt-8763ebaba1a8b513` → branch `FZ`, title `"В index.js → skillQueueFor():"`, `archived=1`
- `pt-6764ee4c4b7822da` → branch `GF`, title `"0. Что есть в наличии"`, `archived=0`

### Причина
Вероятно, был выполнен `partial` или `force` ingest на r33 без корректного `base_revision`, что привело к созданию второго `plan_task_id` для того же пути. Второй (FZ) был сразу заархивирован.

### Следствие
- Не влияет на отображение (FZ версия `archived=1`)
- Создаёт путаницу: почему на одной ревизии два П1?
- Указывает на отсутствие защиты от дублирования пути внутри revision

### Приоритет исправления: **P2 — в рамках P1**

### Варианты решения
- В `plan_ingest` добавить проверку: если для пути уже есть `plan_task_id` на этой ревизии — не создавать второй
- Игнорировать (второй уже archived)

---

## Проблема 4 (НИЗКАЯ): Плоская иерархия плана

### Суть
П1–П7 — все `parent_id = NULL` (top-level). Нет plan task `"Разработка плагина DOM"` как корневого элемента. GRANDLINE существует только как свойство `main_task` на root claim `149d33e5`, не как план-задача.

### Следствие
- Монитор показывает 7 корневых элементов вместо одного
- LINE в brief показывает только П1 (автовыбранный current)
- Нет единой точки привязки для всей иерархии плана

### Данные
```
П1  0. Что есть в наличии      parent_id=NULL
П2  1. Какие блоки извлекать   parent_id=NULL
П3  2. Скрипт сбора           parent_id=NULL
П4  3. Сортировка и анализ    parent_id=NULL
П5  4. Критерии соответствия  parent_id=NULL
П6  5. Выходные артефакты     parent_id=NULL
П7  6. Риски                  parent_id=NULL
```

### Коренная причина
План изначально создавался как список top-level задач. При каждой итерации (r23→r33) задачи сдвигались, но родительская связь не создавалась. `main_task` на root claim — единственное место, где хранится "имя проекта", но оно не связано с plan_tasks.

### Приоритет исправления: **P3 — после P1, низкий приоритет**

### Варианты решения

| # | Действие | Риск | Сложность |
|---|----------|------|-----------|
| 1 | Создать план-задачу П0 "Разработка плагина DOM" → П1-П7 её дети | Нужен full reingest плана, сдвинет branch коды | 1 час |
| 2 | Оставить как есть — GRANDLINE показывается через brief | Косметический костыль | 0 |
| 3 | Переименовать П1 в "0. Разработка плагина: что есть" | Не решает иерархию | 15 мин |

**Рекомендация:** Вариант 1 — при следующем крупном перепланировании.

---

## Порядок исправления

```
P0 ───────── Проблема 1: current_task=П15 ──────── обновить свойство
 |                                                    5 мин
P1 ───────── Проблема 2: plan_ingest плодит id ──── исправить код сервера
 |                                                    1 день
 |  └──────── Проблема 3: двойной П1 на r33 ──── в рамках того же кода
 |                                                    30 мин
P3 ───────── Проблема 4: плоская иерархия ────────── при перепланировании
                                                      1 час
```

---

## Диагностические команды

```sql
-- П1: все plan_task_id
SELECT DISTINCT t.plan_task_id, v.path, b.branch, v.source_revision, v.archived
FROM plan_tasks t
JOIN plan_task_versions v ON v.plan_task_id=t.plan_task_id
LEFT JOIN plan_branches b ON b.plan_task_id=t.plan_task_id
WHERE v.path='П1' ORDER BY v.source_revision;

-- Количество active vs total
SELECT 'active' as type, count(*) FROM plan_task_versions WHERE archived=0
UNION ALL
SELECT 'total', count(*) FROM plan_task_versions;

-- Проверка current_task
SELECT payload FROM events
WHERE claim_ref='149d33e5-7814-4eae-910e-1653fba9b9ca'
AND kind='property' AND payload LIKE '%current_task%'
ORDER BY seq DESC LIMIT 5;

-- Все archived plan_task для П15
SELECT v.path, v.title, v.archived, v.source_revision
FROM plan_task_versions v
JOIN plan_tasks t ON t.plan_task_id=v.plan_task_id
WHERE v.path='П15' ORDER BY v.source_revision;
```

---

## Статус исправлений

| P | Проблема | Статус | Описание |
|---|----------|--------|----------|
| **P0** | `current_task=П15` | ✅ **done** | `dom_annotate` → `current_task="П1"` |
| **P1** | `plan_ingest` плодит `plan_task_id` | ✅ **done** | В `ingest()`: при наличии `base_revision` инжектится старый root id в `roots[0]['id']`, что заставляет `flatten()` считать это revision |
| **P2** | Двойной путь на одной ревизии | ✅ **done** | В `flatten().walk()`: `seen_paths` — проверка уникальности `path` перед добавлением |
| **P3** | Плоская иерархия | ⏳ **plan** | При перепланировании: создать П0 "Разработка плагина DOM" как корень |

### Изменённые файлы

| Файл | Что сделано |
|------|-------------|
| `opencode/mockup/planning.py` | P1: inject root id в ingest() перед flatten(); P2: dedup guard по path в walk() |
| `claude/mockup/planning.py` | Те же изменения (mirror) |
| `docs/plan-identity.md` | Добавлен §12 с описанием P1 fix |
| Root claim `149d33e5` | `current_task` обновлён с П15 на П1 |

### Тесты

`tests/check_plan_identity.py` → 6/6 PASS (3 сценария × 2 дерева):
- New plan (без base_revision) — все ID свежие, старые задачи архивированы
- Revision (тот же root title) — ID сохранены, обновлённый заголовок — новая версия
- Partial — новый пункт создан, существующие не тронуты