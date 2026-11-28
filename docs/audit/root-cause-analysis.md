# Расследование: почему 70K токенов скилов остаются в промпте

**Дата:** 2026-11-28
**Проверка:** live-тест после F1 фикса (`9f89c44..eb69830`)

---

## 0. Что проверено

**Метод:** `opencode run --standalone --print-logs --format json` с dev-config, модель polza/deepseek.

**Результат:** в stderr видна строка:
```
[dom-memory] skill-queue injected via system: skill_search (256 chars, idle 0, fp 645ae246ee9c)
```

**Вывод:** продюсер работает, queue **доставляется** через `event.system.push`. F1 пофиксен.

---

## 1. Почему 70K токенов всё ещё в промпте

### 1.1. Текущая архитектура

```text
OpenCode host (каждый промпт):
  1. Загружает ВСЕ 169 skills из registry  ← 70 882 chars
  2. Загружает Code Mode catalog           ←  9 099 chars
  3. Загружает DOM namespace                ←    845 chars
  4. Загружает dom.notes (наша инъекция)    ←  2 778 chars
  ─────────────────────────────────────────
  Итого: ~83 000 chars
```

DOM контролирует только пункт 4 (`dom.notes`). Пункт 1 — каталог скилов — загружается host'ом **автоматически**, независимо от того, что сказал роутер.

### 1.2. Что делает роутер

| Компонент | Статус | Размер |
|-----------|--------|--------|
| `dom_skill_select({step})` → queue [3 скила] | ✅ PASS (38 тестов) | — |
| `queue_producer.py` → block + fingerprint | ✅ После F1 работает | 256 chars |
| `entry.put("dom.skill-queue", block)` | ✅ После F1 + fallback | — |
| **Host фильтрует skills по queue** | **❌ НЕ РЕАЛИЗОВАНО** | **70 882 chars → 0** |

### 1.3. Где обрыв

```
Роутер: queue = [sqlite-migration, dom-memory, test-driven-development]
    │
    ▼
DOM index.js: entry.put("dom.skill-queue", block)  ← РАБОТАЕТ (F1 fix)
    │
    ▼
OpenCode host: загружает ВСЕ 169 skills          ← НЕ СМОТРИТ НА QUEUE
    │
    ▼
Модель видит 169 скилов, а не 3
```

---

## 2. Для каких блоков актуально

| Блок | Актуальность | Статус |
|------|-------------|--------|
| **Router pipeline** (`skill_*.py`) | Выбирает 3 скила из 148 | ✅ Работает, 38 тестов |
| **Queue producer** (`queue_producer.py` + `index.js`) | Доставляет queue в host | ✅ После F1 работает |
| **τ-gate** (`tau_gate.js`) | Фильтрует повторные инъекции | ✅ Работает |
| **Permissions-профиль** (`dom-router`) | **Заставляет host фильтровать по queue** | ❌ Не реализован |
| **Skill registry** (host-side) | Загружает skill descriptions | ❌ Host не фильтрует |

---

## 3. Почему блоки сделаны так

### 3.1. Router pipeline — сделан как pure MCP tool

**Зачем:** детерминированный выбор скилов без LLM. Вызов `dom_skill_select` → queue без запроса к модели.

**Почему так:** изначальная спека (скиловая селекция) говорила «построить pipeline». Про доставку в host и фильтрацию — ни слова.

**Кем решалось:** обсуждение 2026-11-26/27, addendum A, collapse audit. Фокус был на algorithm selection, не на end-to-end.

### 3.2. Queue producer — добавлен как P1 (после аудита)

**Зачем:** чтобы queue автоматически инжектировалась в промпт без ручного вызова `dom_skill_select`.

**Почему так:** collapse audit (2026-11-27) обнаружил, что очередь не инжектируется. P1 был добавлен как «срочно».

**Нестыковка:** producer сделали, но acceptance criteria проверяли *что скрипт возвращает JSON*, а не *что блок виден модели в live-сессии*.

### 3.3. Permissions-профиль — не реализован

**Зачем:** единственный способ убрать 70K из промпта — `permissions: deny skill * + allow skill dom-memory` (E6 measurement: 69K→575 chars).

**Почему так:** Decision 10 (0.3.1 concept doc) говорит: *«the variant ships as a dedicated agent profile. The default 0.3.1 path stays the instruction-entry queue»*. То есть профиль был осознанно отложен.

**Почему не подняли:** ни план, ни аудит, ни тесты не включили E2E метрику «токены скилов в промпте до/после».

---

## 4. Где провалились этапы

| Этап | Что должно было быть | Что было | Почему |
|------|---------------------|----------|--------|
| **Планирование** | Включить в scope host-side filtering | Не включили | Фокус на pipeline, не на E2E |
| **Реализация** | producer + host-side filter | Только producer | Producer добавлен как P1, фильтр — P2 |
| **Аудит** | Проверить: «токены скилов уменьшились?» | Проверено: «38 тестов PASS» | Аудитор смотрел код, не промпты |
| **Тестирование** | E2E: live-сессия → замер токенов | 38 групп unit-тестов | Тестировали pipeline, не инжекцию |
| **Приёмка** | «queue в промпте, токены <5K» | «pipeline PASS» | Acceptance criteria не включали E2E |

---

## 5. Что нужно сделать

### Шаг 1: permissions-профиль `dom-router`

В `opencode.json` проекта (или в agent profile):
```json
{
  "agents": {
    "dom-router": {
      "permissions": [
        {"action": "skill", "resource": "*", "effect": "deny"},
        {"action": "skill", "resource": "sqlite-migration|dom-memory", "effect": "allow"}
      ]
    }
  }
}
```

**Где:** `docs/concepts/2026-11-24-skill-router-0.3.1.md` Decision 10 — уже описано, не реализовано.
**Объём:** ~20 строк конфига + тест (69K→575 chars).

### Шаг 2: Producer — очередь из cap

Продюсер сейчас не получает cap из brief (F2 пофиксен, но plan steps не имеют `accept`). Без cap — queue = `skill_search`. Нужно:
- Добавить `accept` свойства к шагам плана (E2)
- Проверить, что `CAP:` появляется в brief
- Проверить, что producer возвращает `sqlite-migration` а не `skill_search`

### Шаг 3: E2E тест

```python
before = len(prompt_skills_section)  # ~70K
queue = get_queue_from_stderr()  # [sqlite-migration, dom-memory]
after = len(prompt_skills_filtered)  # ~575 (только разрешённые)
assert after < before * 0.1           # уменьшение ≥90%
```

---

## 6. Вывод

Весь 0.3.1 был построен на неверном предположении: «роутер выберет скилы → host сам поймёт». Host не понимает. Нужен **явный механизм фильтрации** — permissions-профиль.

Это не баг кода. Это **баг планирования**: scope не включал host-side интеграцию. Спека обсуждала pipeline, но не доставку. Аудит смотрел процесс, а не результат. Тесты проверяли алгоритм, а не эффект.