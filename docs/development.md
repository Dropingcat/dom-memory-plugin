# Разработка DOM Memory plugin

Документ для тех, кто правит сам плагин и хочет проверять его в живой сессии
OpenCode, не ломая пользовательскую установку и не путая проектный плагин с
глобальной сборкой.

---

## 1. Быстрый live-тест

Из корня репозитория:

```powershell
powershell -File tools/run_dev_session.ps1 -Prompt "Reply with exactly: ok"
```

Скрипт:

1. считает корень репозитория от `$PSScriptRoot` (то есть от `<repo>/tools`);
2. выставляет `OPENCODE_CONFIG=<repo>/opencode/opencode.dev.json`;
3. находит `opencode-cli.exe`
   (`C:\Users\Arhys\AppData\Local\Programs\ai.opencode.desktop\resources\opencode-cli.exe`,
   иначе — `opencode` из `PATH`);
4. запускает
   `opencode-cli run --standalone --print-logs --auto --format json --model <model> <prompt>`
   с рабочей директорией `<repo>`;
5. в `finally` восстанавливает прежнее значение `OPENCODE_CONFIG`
   (или удаляет переменную, если она не была задана);
6. печатает путь активного конфига и напоминает, что активный экземпляр плагина
   нужно подтвердить по строке `[dom-memory hooks]` (§3).

Параметры:

| Параметр | По умолчанию | Назначение |
|----------|--------------|------------|
| `-Model` | `polza/deepseek/deepseek-v4.1-flash-1017` | модель в форме `provider/model` |
| `-Prompt` | — | сообщение пользователя |
| остальные аргументы | — | пробрасываются в `opencode-cli run` (например `--agent explore`) |

Первый позиционный аргумент — это `-Model`, поэтому промпт всегда передавайте
именованным `-Prompt`.

Примеры:

```powershell
# Другая модель
powershell -File tools/run_dev_session.ps1 -Model polza/deepseek/deepseek-v4.1-pro -Prompt "Reply: ok"

# Флаг прокидывается в opencode-cli run
powershell -File tools/run_dev_session.ps1 -Prompt "Reply with exactly: ok" --agent explore
```

---

## 2. Зачем нужен `OPENCODE_CONFIG`

Глобально установленный DOM-плагин и проектный плагин объявляют **один и тот же**
`id: dom-memory.hooks`. OpenCode оставляет для этого id **один** экземпляр, и это
не обязательно та сборка, которую вы правите. Без явной проверки live-тест может
молча запустить старую глобальную `dist`-сборку.

`opencode/opencode.dev.json` даёт один предсказуемый источник истины:

* в `plugins` ровно один entry — проектный плагин
  `file:///E:/opencode_harness_portable/projects/opencode-dom-memory-plugin/opencode`;
* MCP-сервер `dom-memory` указывает на `opencode/scripts/dom_server.py` этого же
  репозитория;
* в `environment` задан `OPENCODE_PROJECT_DIR=<repo>`, то есть память пишется в
  репозиторий плагина, а не в чужой проект.

Важное уточнение про OpenCode 2.3.0: конфигурация **многослойная**, и
`OPENCODE_CONFIG` добавляет слой, а не отключает остальные. Список реально
прочитанных документов показывает

```powershell
opencode debug config
```

В типичном запуске на этой машине в списке есть глобальный
`%USERPROFILE%\.config\opencode\opencode.jsonc`, конфиги родительских каталогов
(`E:\opencode_harness_portable\.opencode\opencode.json`) и конфиг проекта
(`<repo>\.opencode\opencode.json`) — плагины и MCP-серверы из них продолжают
загружаться. Сам путь из `OPENCODE_CONFIG` в этом списке не печатается, хотя
применяется (это видно по серверам/плагинам, которых нет в других слоях).
Поэтому dev-конфиг **не** «выключает» остальные плагины: он фиксирует
объявление для этого репозитория, но при совпадении `id` побеждает тот слой,
чей экземпляр загрузился первым.

Практический вывод: после запуска обязательно проверяйте, какой экземпляр хуков
победил (§3). Если в строке виден `dist`-путь, значит победила глобальная сборка,
и тест недостоверен.

Проверить сам файл без запуска модели:

```powershell
Get-Content opencode/opencode.dev.json -Raw | ConvertFrom-Json | Out-Null
```

---

## 3. Как убедиться, какой плагин активен

При инициализации плагин печатает в stderr:

```
[dom-memory hooks] root=<repo>\opencode project=<repo> planDir=<...>
```

`--print-logs` в `run_dev_session.ps1` уже включён. Ищите строку в выводе:

```powershell
powershell -File tools/run_dev_session.ps1 -Prompt "Reply with exactly: ok" 2>&1 |
    Select-String "\[dom-memory hooks\]"
```

Как читать:

| Что видно | Значение |
|-----------|----------|
| `root=<repo>\opencode` | активен проектный плагин из репозитория — тест достоверен |
| `root=...\dist\...` или путь в другом проекте | победила глобальная/чужая сборка — тест недостоверен |
| строки нет вовсе | плагин не загрузился: проверьте `OPENCODE_CONFIG`, путь в `plugins[].package`, наличие `opencode/index.js` |
| `disabled by options.enabled=false` | плагин выключен опцией конфига |

Дополнительно:

* `opencode debug config` — какие документы конфигурации реально прочитаны;
* `opencode mcp list` — какие MCP-серверы сконфигурированы и их статус;
* `mcp transport error ... exited with code 1` в конце сессии — штатный шум
  завершения: так завершаются все MCP-серверы при выходе, а не только
  `dom-memory`.

---

## 4. Куда пишется память

Резолвер один для MCP-сервера, хуков и монитора (`opencode/mockup/paths.py`).

Проект (по убыванию приоритета):

1. `DOM_PROJECT_DIR`;
2. `OPENCODE_PROJECT_DIR`;
3. `CLAUDE_PROJECT_DIR`;
4. `HERMES_PROJECT_DIR`;
5. текущая директория процесса.

Корень данных: `DOM_RUNTIME_ROOT` либо `<project>/.dom-memory`.
Директория запуска: `--directory` / `DOM_DIRECTORY` либо `<root>/main`.
База: `<run>/dom.sqlite3`, по умолчанию
`<project>/.dom-memory/main/dom.sqlite3`.

В `opencode/opencode.dev.json` задано:

```json
"environment": { "OPENCODE_PROJECT_DIR": "E:/opencode_harness_portable/projects/opencode-dom-memory-plugin" }
```

поэтому live-база ляжет в
`E:/opencode_harness_portable/projects/opencode-dom-memory-plugin/.dom-memory/main/dom.sqlite3`.
Наблюдаемость (`observability/`, `prompts/`) — рядом, в `<project>/.dom-memory/observability`.

Явный каталог запуска:

* опция плагина `"directory": "..."` — плагин сам выставляет `DOM_DIRECTORY` и
  `DOM_RUNTIME_ROOT=<dirname>`;
* переменные `DOM_DIRECTORY` + `DOM_RUNTIME_ROOT` в `environment` у MCP-сервера.

`dom_server.py` не принимает `--directory` флагом: каталог приходит только через
окружение. Если задать `DOM_DIRECTORY` без согласованного `DOM_RUNTIME_ROOT`,
`mockup.admission` откажет с `RUNTIME_PATH_REQUIRED`.

Позиционный `--directory` самого `opencode-cli` — это рабочая директория сессии;
она используется как fallback для `projectDir`, если ни опция, ни переменные не
заданы.

---

## 5. Откат live-базы после теста

Live-тест пишет в настоящий ledger, поэтому перед экспериментами сохраните базу:

```powershell
$run = "E:/opencode_harness_portable/projects/opencode-dom-memory-plugin/.dom-memory/main"
$backup = "C:\Temp\opencode\dom-live-backup-$(Get-Date -Format yyyyMMdd-HHmmss)"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item "$run\*" $backup -Recurse -Force
```

Откат (сначала закройте сессии, которые держат эту базу открытой — SQLite не
любит подмену файла под живым соединением):

```powershell
$run = "E:/opencode_harness_portable/projects/opencode-dom-memory-plugin/.dom-memory/main"
$backup = Get-ChildItem "C:\Temp\opencode" -Directory -Filter "dom-live-backup-*" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
Copy-Item "$($backup.FullName)\*" $run -Recurse -Force
```

Каталог `C:\Temp\opencode` — единственное место, где dev-процесс зависит от
`C:\Temp`, и только для ручных бэкапов; сам репозиторий от него не зависит.

Если база не нужна, её можно просто удалить: она пересоздастся первым хуком
(`.dom-memory/main/dom.sqlite3`). Ledger append-only и хеш-цепочный, поэтому
«починить» его правкой строк нельзя — только откатить файл из бэкапа.

---

## 6. Live-тест субагента

Тот же конфиг работает и для субагента — просто передайте `--agent`:

```powershell
powershell -File tools/run_dev_session.ps1 -Prompt "Reply with exactly: ok" --agent explore
```

Или напрямую, не забыв про переменную и восстановление:

```powershell
$previous = $env:OPENCODE_CONFIG
$env:OPENCODE_CONFIG = "E:/opencode_harness_portable/projects/opencode-dom-memory-plugin/opencode/opencode.dev.json"
try {
    opencode-cli run --standalone --print-logs --auto --format json --agent explore "Reply with exactly: ok"
}
finally {
    if ($null -eq $previous) { Remove-Item Env:OPENCODE_CONFIG -ErrorAction SilentlyContinue }
    else { $env:OPENCODE_CONFIG = $previous }
}
```

Проверка та же: в stderr должна быть строка
`[dom-memory hooks] root=<repo>\opencode project=<repo>`.

---

## 7. Permissions-профиль `dom-router`

Профиль нужен, когда сессия должна видеть не весь каталог skills, а только
разрешённые. В `opencode/opencode.dev.json` он объявлен так:

```json
"agents": {
  "dom-router": {
    "permissions": [
      {"action": "skill", "resource": "*", "effect": "deny"},
      {"action": "skill", "resource": "sqlite-migration|dom-memory|test-driven-development|pubchem-database", "effect": "allow"}
    ]
  }
}
```

### 7.1. Запуск

`tools/run_dev_session.ps1` прокидывает неизвестные ему аргументы в
`opencode-cli run`, поэтому `--agent` передаётся напрямую:

```powershell
powershell -File tools/run_dev_session.ps1 -Prompt "Reply with exactly: ok" --agent dom-router
```

Скрипт сам выставит `OPENCODE_CONFIG=<repo>/opencode/opencode.dev.json` и
восстановит прежнее значение после запуска (§1). Проверка активного плагина —
та же строка `[dom-memory hooks]` из §3: профиль меняет только skills, плагин и
MCP-сервер должны остаться проектными.

Без dev-конфига профиль не появится: `agents.dom-router` объявлен именно в нём,
а не в глобальном `opencode.jsonc`. Какие слои конфигурации реально прочитаны,
показывает `opencode debug config`.

### 7.2. Почему permissions — единственный способ фильтрации

Host собирает промпт сам: каталог skills (`core/skill-guidance`) — core-блок,
который плагин не может ни переписать, ни удалить. `ctx.skill.transform`
видит только 17 plugin-visible skills, а не 165 конфигурационных (E4), поэтому
убрать ими каталог нельзя; `dom.skill-queue` добавляет строку с выбранными
skills, но не удаляет уже вставленный каталог. Permissions — единственный
поддерживаемый рычаг: `deny skill *` + `allow` оставляет в промпте только
разрешённые записи.

E6 (fresh session, 2026-11-24): `deny *` + `allow dom-dashboard` даёт
69 276 → 575 chars каталога (−99,2%), 166 → 1 skill. Live-замер `dom-router`
(2026-11-28, тот же dev-конфиг): блок `core/skill-guidance` исчез целиком,
system 86 644 → 13 854 chars. Полный разбор — в
`docs/audit/root-cause-analysis.md`.

Следствие: `deny` запрещает и загрузку skill по id — модель не сможет вызвать
`skill` для запрещённого. Поэтому `allow` должен покрывать skills, которые
реально нужны шагам плана.

### 7.3. Синтаксис `resource`: wildcard, а не regex

Хост сопоставляет `resource` целиком и понимает только wildcard `*`
(ноль и более символов, включая `/`) и `?` (ровно один символ); всё остальное —
литерал. Регулярные выражения и альтернация `a|b` не поддерживаются: правило
`"resource": "sqlite-migration|dom-memory"` не разрешает ни один skill.

Поэтому allow-list — это отдельное правило на каждый id:

```json
"permissions": [
  {"action": "skill", "resource": "*", "effect": "deny"},
  {"action": "skill", "resource": "sqlite-migration", "effect": "allow"},
  {"action": "skill", "resource": "dom-memory", "effect": "allow"},
  {"action": "skill", "resource": "test-driven-development", "effect": "allow"},
  {"action": "skill", "resource": "pubchem-database", "effect": "allow"}
]
```

В текущем `opencode/opencode.dev.json` allow-правило записано одной строкой
через `|`, поэтому фактически не разрешает ничего: каталог скрыт полностью
(0 skills), а не сужен до четырёх. Live-проверка 2026-11-28 это подтвердила;
пока конфиг не исправлен, профиль работает как «deny all».

### 7.4. Как проверить, что skills отфильтрованы

1. Запустить сессию с профилем (§7.1) и `--print-logs`.
2. Взять самый свежий снапшот промпта:
   `<repo>/.dom-memory/observability/prompts/<session_id>/<ts>.json`.
   Поле `agent` должно быть `"dom-router"`; иначе профиль не применился.
3. В тексте `system` / `system_blocks` проверить:
   * блок `core/skill-guidance` отсутствует либо содержит только разрешённые
     ids; skills вне `allow` (например `brainstorming`) не встречаются;
   * при корректном allow-list разрешённые ids присутствуют, а размер блока —
     сотни символов, а не десятки тысяч (ориентир E6: ~575 chars на запись);
   * сейчас, из-за `|` в allow-правиле (§7.3), блока нет вовсе — это ожидаемый
     результат «deny all», а не доказательство работы allow-list.

Без снапшота проверка косвенная (модель «не знает» о запрещённых skills) и
недостоверна; опирайтесь на JSON снапшота.

### 7.5. Динамический allow-list из `dom.skill-queue`

Статический профиль (§7) — это база; очередь роутера дополняет его на уровне
сессии. У хоста нет `ctx.session.permissions.set`: документированный рычаг —
`ctx.session.update({ sessionID, permissions })` (Build plugins → Server →
Permissions). Правила сессии вычисляются **после** правил агента, побеждает
последнее совпадение, дочерние сессии наследуют набор на момент создания.
Поэтому плагин меняет allow-list только для этой сессии и не трогает
`agents.<id>.permissions`.

После каждого шага `skillQueueFor` берёт `queue` продюсера и вызывает
`ctx.session.update` с одним из режимов (`options.dynamicPermissions`):

| Режим | Ruleset | Смысл |
|---|---|---|
| `"allow"` (по умолчанию) | `allow <queue>` | очередь добавляется к статическому allow-list агента |
| `"strict"` | `deny *` + `allow <queue>` | очередь — точный allow-list; остальные skills скрыты и не загружаются |
| `false` / `"off"` | — | статический профиль не меняется |

`resource` — один skill id на правило (правила с `*`/`?`, без regex и `|`;
см. §7.3). Одинаковая
очередь не переотправляется (подпись ruleset на сессию); смена очереди
заменяет набор целиком. Ошибка `update` логируется и повторяется на следующем
запросе, запрос модели не блокируется. На хосте без `ctx.session.update`
плагин пишет `permissions: ctx.session.update unavailable` и работает со
статическим профилем — это graceful degradation для старых сборок.

`"allow"` — безопасный default: он только расширяет список и ничего не
запрещает, поэтому не ломает обычные сессии с полным каталогом. `"strict"`
предназначен для профиля, которому разрешено владеть skills (например
`dom-router`), потому что `deny skill *` в сессии действует и на загрузку
skill по id.

Проверка: `node --test tests/check_dynamic_permissions.mjs` — очередь →
ruleset, дедупликация, замена, strict, off, отсутствие API и retry после
reject. E2E доставки блока остаётся в `tests/check_skills_in_prompt.py`.

### 7.6. Статический профиль и динамический режим

Статический allow-list в `opencode/opencode.dev.json` — база профиля: он не
зависит от плана и `dom.skill-queue`. Динамический режим (§7.5) добавляет
skills из очереди на уровне сессии, но включается опцией
`options.dynamicPermissions` и пока не подтверждён E2E-замером каталога:
сравнивать нужно снапшоты `core/skill-guidance` до/после на одной и той же
сессии. До такого замера профиль считается статическим, а allow-список должен
быть надмножеством skills, нужных типичным шагам.

---

## 8. Ограничение

`opencode/opencode.dev.json` — инструмент **разработки плагина**. Он жёстко
привязан к путям этой машины и грузит только проектный плагин, поэтому не
предназначен для распространения.

Для пользователей штатный путь установки — `dom install --project`, который
появится в версии **0.3.2** и сам создаст корректный конфиг с нужными путями.
