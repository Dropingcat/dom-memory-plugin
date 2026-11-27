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

## 7. Ограничение

`opencode/opencode.dev.json` — инструмент **разработки плагина**. Он жёстко
привязан к путям этой машины и грузит только проектный плагин, поэтому не
предназначен для распространения.

Для пользователей штатный путь установки — `dom install --project`, который
появится в версии **0.3.2** и сам создаст корректный конфиг с нужными путями.
