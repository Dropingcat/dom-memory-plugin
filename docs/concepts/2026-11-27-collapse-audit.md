# Collapse Pattern Audit — Skill Selector Architecture

**Дата:** 2026-11-27  
**Предмет:** `docs/concepts/2026-11-26-skill-selector-architecture.md` (proposal, 7-stage) + `docs/concepts/2026-11-27-skill-selector-addendum.md` (addendum, π/σ/τ)  
**Запрос:** найти все решения, которые могут привести к краху сессии, деградации инструментов или закладке агентом заведомо негативных рекомендаций

---

## 0. Категории collapse-паттернов

| Категория | Описание | Пример |
|-----------|----------|--------|
| **H** — Hard limit | Жёсткий счётчик, после которого система перестаёт работать | ≤8 переизданий |
| **S** — Self-grading | Селектор оценивает собственные рекомендации | outcome → weight → selector → подтверждение |
| **C** — Circular dependency | A зависит от B, B зависит от A | unverified cards ↔ нет outcomes ↔ нет authoritative (R3) |
| **G** — Gaming surface | Агент может манипулировать системой | изменить текст шага → изменить очередь |
| **D** — Denegerate state | Состояние, из которого система не выходит | phase=unknown + empty provides → gap всегда |
| **M** — Measurement contamination | Метрика, которой можно управлять | hit rate по read receipts вместо tool calls |

---

## 1. H — Hard limits (могут убить сессию)

### H1 ≤8 переизданий за сессию (Addendum §3.2 — τ-гейт)

**Где:** «счётчик переизданий ≤ K на сессию (предлагаю K=8)»

**В чём проблема:** в сессии может быть 60–80 микро-шагов. Если каждый легитимно меняет `(cap, phase)` — а при правильной работе селектора так и должно быть — счётчик вылетает за 8 на 9-м шаге. Дальше блок не переиздаётся, инструменты не обновляются, шаги идут с устаревшей или пустой очередью.

**Исправление:** считать не «сколько раз переиздали», а «сколько раз переиздали вхолостую» (fingerprint совпал, а блок переиздали). Холостое переиздание — это баг, не штатная работа. Штатное переиздание (сменился `(cap,phase)`) — не баг и не должно считаться.

**Collapse:** **высокий** — на 9-м шаге сессия перестаёт получать инструменты.

---

### H2 Max queue depth = 3 (proposal S5, existing skill_router k=3)

**Где:** `select(step, skills, k=DEFAULT_K=3)` — ограничение на top-k

**В чём проблема:** при 3 инструментах на шаг и 60 шагах селектор выбирает 180 инструментов. Если правильных инструментов всего 15, каждый будет предложен ~12 раз. Это не collapse, но размывает приоритеты.

**Collapse:** **низкий** — не убивает сессию, просто снижает качество.

---

### H3 Prompt budget 800 символов (Addendum §5.1)

**Где:** блок инжекции ≤800 символов

**В чём проблема:** если шагов в prefetch больше 5 (напр. 8 микро-шагов впереди), бюджет может быть превышен. Предложено 5 шагов, что разумно.

**Collapse:** **средний** — при превышении шаги молча обрезаются. Нужен тест на границу.

---

### H4 Max spawn latency (Addendum §4 — spawnSync 150-400ms не рекомендован)

**Где:** spawn Python на Windows

**В чём проблема:** MCP-tool (вариант B) решает, но если сервер падает и селектор падает на spawn — каждый шаг добавляет 150-400ms × 60 = 9-24 секунды простоя.

**Collapse:** **средний** — UX деградирует, но не убивает. Нужен fallback на τ-гейт: при недоступности MCP-tool блок не переиздаётся.

---

## 2. S — Self-grading (селектор оценивает себя)

### S1 Weights из outcomes (proposal S7, addendum §9.2)

**Где:** outcome (hit/miss/failure/error) → weights → следующий select

**Цикл:**
```
selector → queue → agent выбирает skill → outcome → weight update → selector видит новый weight → тот же skill получает выше score
```

**В чём проблема:** если outcome ошибочен (метка hit, хотя skill не подходил), weight укрепляет ошибку. После 3-4 циклов weight фиксирует неверный выбор. Это positive feedback loop.

**Addendum §9.2 предлагает:** ручной аудит первых 30 меток. Сдерживает loop, но не устраняет его.

**Collapse:** **высокий** — может закрепить неверную ветку выбора на всю сессию.

**Исправление:** weight не может превышать `priors`. `max_weight = priors[task_type]`. Агент не может «переубедить» селектор выше врождённого приоритета.

---

### S2 Card promotion (addendum §9.2 — unverified → shadow → verified)

**Где:** карточка переходит из unverified в verified на основе outcomes + owner accept

**Цикл:**
```
selector предложил skill → outcome hit → карточка получает promoted → selector видит verified → выше score → чаще предлагает → больше hit
```

**Collapse:** **средний** — promotion только через owner accept. Но если owner автоматом подтверждает, цикл замыкается.

**Исправление:** promotion только через независимый review (`DOM_REVIEW=independent`), не auto.

---

### S3 Fallback `skill_search` не оценивается

**Где:** при gap → `skill_search` (fallback)

**В чём проблема:** `skill_search` не имеет outcome-трека. Если fallback постоянно срабатывает — это симптом (gap), но селектор его не видит и не сообщает владельцу.

**Collapse:** **средний** — не убивает, но скрывает отсутствие провайдера за тихим fallback. «Гэп не виден → никто не курирует provides → гэп вечен».

---

## 3. C — Circular dependencies (дедлоки бутстрапа)

### C1 R3 — Дедлок бутстрапа (addendum §7, R3)

**Где:** unverified cards ↔ нет outcomes ↔ нет authoritative queue

```text
Чтобы queue была authoritative → нужны verified cards
Чтобы cards стали verified → нужны outcomes
Чтобы outcomes были → нужно запустить селектор
Чтобы селектор запустить → нужно authorised queue
```

**Addendum фиксит:** owner верифицирует 10–20 карточек вручную, разрывая цикл.

**Collapse:** **критический** для автоматического запуска. Ручной бутстрап — правильное решение.

---

### C2 Read receipts + skill-load (addendum §7 R4)

**Где:** claim read receipt использован как прокси для skill hit/miss

**Смешение:** receipt → claim прочитан → «значит skill использован» — неверно. Skill может быть загружен, но не вызван.

**Addendum предлагает:** раздельные треки: receipt → claim-трек (L2), tool-call → skill-трек.

**Collapse:** **высокий** — если receipt считать hit, то каждый `dom_find` = hit для любого skill'а в очереди. Очередь никогда не проигрывает.

---

### C3 mentions → provides (proposal §3-old, addendum §7)

**Где:** `_extract_caps` из полного текста SKILL.md

**Цикл:** skill упоминает sqlite → селектор считает что skill provides sqlite → skill предлагается как решение → agent использует → outcome hit → weight растёт → skill чаще предлагается

**Addendum исправляет:** переименовать в `mentions`, запретить покрытие need из mentions.

**Collapse:** **критический** — даёт ложные способности. Исправление принято.

---

## 4. G — Gaming surface (агент может манипулировать)

### G1 Изменение текста шага → изменение очереди

**Агент может:** незначительно изменить формулировку шага («прочитать схему» → «изучить структуру таблицы claims») → меняется `phase` (inspect → другой?) → меняется очередь.

**Если phase-классификатор срабатывает на синонимы — gaming возможен.**

**Collapse:** **средний** — агент может подобрать формулировку, которая даёт желаемый инструмент. Не убивает сессию, но подрывает авторитет очереди.

**Исправление:** phase-классификатор должен быть консервативным — срабатывать на точные глаголы, не на синонимы. `unknown` при любой неопределённости.

---

### G2 forced/banned hints (skill_router.py:145-147)

**Где:** plan hint может force или ban skill

**Агент может:** написать `hints_skills: ["мой-скилл"]` в свойства шага → skill гарантированно в очереди.

**Collapse:** **низкий** — hints — это явное решение владельца плана. Но если агент пишет hints сам себе — gaming возможен.

**Addendum D2:** таблица приоритетов `bans > forces > verified > cover`. Это защищает от forced-манипуляции.

---

### G3 `provider=client` для proposes (addendum §8.2)

**Где:** `dom_extract(provider=client)` → агент предлагает provides для карточки

**Агент может:** предложить `provides: ["sqlite","migration","security"]` для своего скилла → получить больше вызовов → больше outcomes → больше веса.

**Collapse:** **средний** — mitigated через review/admit. Но при `DOM_REVIEW=auto` — gaming возможен.

**Исправление:** `provides` proposal никогда не auto; требует `DOM_REVIEW=manual` или `independent`.

---

## 5. D — Degenerate states

### D1 `phase=unknown` + `provides=[]` → всегда gap

Если σ имеет `phase=unknown` и π имеет пустые `cap`, то селектор всегда возвращает gap. Это корректное поведение, но сессия с 60 такими шагами никогда не получает инструментов.

**Collapse:** **средний** — не убивает, но сессия работает без очереди. Необходима visibility в панель.

---

### D2 Empty `accept` → всегда `awaiting_definition`

**Где:** ни один шаг не имеет `accept` (аудит C5, live ledger: 0 свойств `accept`)

**Проблема:** пока steps не имеют accept, селектор не может построить authoritative queue. Dual mode (inferred → advisory) — mitigation, но advisory не инжектится.

**Collapse:** **средний** — функционально select() не запускается для authoritative mode.

---

### D3 Смерть MCP-сервера → селектор недоступен

**Где:** MCP-tool (вариант B из §4) — единственный transport для σ-каскада

**Проблема:** сервер упал — селектор не работает — блок не переиздаётся. τ-гейт может молчать, но блок устарел.

**Collapse:** **высокий** — необходима рестарт-политика или fallback на spawn.

---

## 6. Матрица коллапса

| ID | Паттерн | P×I | Collapse | Mitigation | Приоритет |
|----|---------|-----|----------|------------|-----------|
| **H1** | ≤8 переизданий | выс×крит | **да** | считать холостые, не штатные | 🔴 немедленно |
| **S1** | Weights из outcomes | выс×выс | да | max_weight ≤ priors | 🔴 немедленно |
| **C2** | Receipt = skill hit | выс×выс | да | раздельные треки | 🔴 немедленно |
| **C3** | mentions = provides | выс×крит | да | `mentions` ≠ `provides` (уже исправлено addendum) | 🔴 принято |
| **C1** | R3 бутстрап-дедлок | выс×крит | да | ручной бутстрап (addendum) | 🟡 принято |
| **G3** | provider=client proposals | сред×выс | условно | manual/independent review только | 🟡 E3 |
| **D3** | Смерть MCP-сервера | сред×выс | условно | fallback spawn или τ-гейт | 🟡 E7 |
| **H4** | spawn latency | сред×сред | условно | MCP-tool (вариант B) | 🟡 E7 |
| **G1** | Изменение текста σ → смена очереди | сред×сред | нет | консервативный phase-классификатор | 🟡 E4 |
| **S2** | Card promotion loop | сред×сред | условно | independent review | 🟡 E10 |
| **S3** | fallback skill_search не трекается | сред×сред | нет | gap-detection метрика в панель | 🟡 E11 |
| **H3** | Prompt budget 800 символов | сред×сред | нет | тест границы | 🟡 E6 |
| **G2** | forced/banned hints | низ×сред | нет | приоритет bans > forces (addendum D2) | 🟢 E5 |
| **H2** | k=3 max queue depth | низ×низ | нет | — | 🟢 accept |
| **D1** | phase=unknown + empty provides | сред×низ | нет | visibility в панель | 🟢 E11 |
| **D2** | empty accept = awaiting_definition | сред×низ | нет | dual mode (addendum C5 fix) | 🟢 E0 |

---

## 7. Выводы и рекомендации

### Что убивает сессию (должно быть исправлено ДО кода):

1. **H1 — счётчик переизданий** — считать холостые переиздания (fingerprint совпал, но блок переиздали), не штатные. Штатная смена `(cap,phase)` — норма, не баг.

2. **S1 — weights из outcomes** — ввести `max_weight ≤ priors`. Агент не может «переубедить» селектор выше врождённого приоритета.

3. **C2 — receipt = skill hit** — раздельные треки: receipt → claim-трек (L2), tool-call → skill-трек.

### Что может убить (должно быть спроектировано с защитой):

4. **D3 — смерть MCP-сервера** — нужен health-check + fallback на spawn-режим (пусть медленный, но живой).

5. **G3 — provider=client proposals** — только manual/independent review, никогда auto.

### Анти-паттерн проекта:

Выявленный addendum'ом анти-паттерн **«наблюдение доступности ≠ участие»** проявляется и в collapse-контексте:

| Модуль | Наблюдение | Участие | Collapse |
|--------|-----------|---------|----------|
| WU | context_only | implements | обязательство без основания |
| Outcomes | read receipt | skill call | hit без использования |
| Pipeline | mentions | provides | способность без возможности |

**Единое исправление:** три разных трека:
- **read_track** (receipt — claim прочитан)
- **call_track** (tool call — skill вызван)
- **accept_track** (outcome — задача решена)

Ни один не заменяет другой. Смешение запрещено на уровне инварианта с одним тестом на все три модуля.