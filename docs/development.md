# Разработка DOM Memory plugin

Документ для тех, кто правит сам плагин и хочет проверять его в живой сессии OpenCode,
не ломая пользовательскую установку и не задевая глобальный DOM-плагин.

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
   (или удаляет переменную, если она не была задана).

Параметры:

| Параметр | По умолчанию | Назначение |
|----------|--------------|------------|
| `-Model` | `polza/deepseek/deepseek-v4.1-flash-1017` | модель в форме `provider/model` |
| `-Prompt` | — | сообщение пользователя |
| `$args` (позиционные) | — | пробрасываются в `opencode-cli run` (например `--agent explore`) |

Примеры:

```powershell
# Другая модель
powershell -File tools/run_dev_session.ps1 -Model polza/deepseek/deepseek-v4.1-pro -Prompt "Reply: ok"

# Позиционный промпт и проброс флага
powershell -File tools/run_dev_session.ps1 "Reply: ok" --agent explore
```

Скрипт печатает путь активного конфига и предупреждение, что глобальный DOM-плагин обойдён.
Вывод `opencode-cli` (stdout — JSON-события, stderr — логи) ретранслируется после
завершения процесса, в исходной кодировке UTF-8; интерактивного стриминга нет —
это осознанно, чтобы длинные marker-строки не рвались переносом.

---

## 2. Зачем нужен `OPENCODE_CONFIG`

Глобально установленный DOM-плагин и проектный плагин объявляют **один и тот же**
`id: dom-memory.hooks`. При дедупликации плагинов OpenCode оставляет один экземпляр —
и это не обязательно тот, который вы правите. В результате live-тест молча запускает
старую глобальную сборку.

`opencode/opencode.dev.json` решает это так:

* в `plugins` ровно один entry — проектный плагин
  `file:///E:/opencode_harness_portable/projects/opencode-dom-memory-plugin/opencode`;
* глобальные плагины (harness, paper-review и пр.) в конфиг не попадают;
* MCP-сервер `dom-memory` указывает на `opencode/scripts/dom_server.py` этого же репозитория.

`OPENCODE_CONFIG` применяется к запуску как приоритетный слой: entry из dev-конфига
разбирается после конфигурации хоста, поэтому при совпадении `id` проектный плагин
выигрывает дедупликацию, а не глобальный `dist`-бандл.

Оговорка: OpenCode всё равно читает источники хоста — глобальный каталог
`OPENCODE_CONFIG_DIR` и проектные `.opencode`-конфиги. Поэтому в логах могут
появиться и другие плагины, а глобальный DOM-плагин может быть загружен и затем
отброшен при дедупликации. Единственный надёжный индикатор того, кто реально
активен, — marker-строка `[dom-memory hooks] root=... project=...` (раздел 3).

Проверить конфиг можно без запуска модели:

```powershell
Get-Content opencode/opencode.dev.json | ConvertFrom-Json | Out-Null
```

---

## 3. Как убедиться, какой плагин активен

Плагин печатает в stderr строку при инициализации:

```
[dom-memory hooks] root=<repo>/opencode project=<repo> planDir=...
```

`--print-logs` в `run_dev_session.ps1` уже включён. Ищите строку в выводе:

```powershell
powershell -File tools/run_dev_session.ps1 -Prompt "Reply: ok" 2>&1 |
    Select-String "\[dom-memory hooks\]"
```

* `root` должен указывать на `<repo>/opencode` — значит загружен проектный плагин,
  а не глобальный `dist`-бандл;
* `project` должен указывать на корень репозитория плагина;
* если строки нет вовсе — плагин не загрузился (проверьте `OPENCODE_CONFIG`,
  путь в `plugins[0].package` и наличие `opencode/index.js`).

---

## 4. Куда пишется память

Приоритет источников (см. `opencode/mockup/paths.py`):

1. явный `--directory` / `DOM_DIRECTORY` (run-директория);
2. `DOM_RUNTIME_ROOT` (корень рантайма, обычно родитель run-директории);
3. `OPENCODE_PROJECT_DIR` — корень проекта;
4. значение по умолчанию — `<project>/.dom-memory/main`.

В `opencode/opencode.dev.json` задано:

```json
"environment": { "OPENCODE_PROJECT_DIR": "E:/opencode_harness_portable/projects/opencode-dom-memory-plugin" }
```

то есть live-база ляжет в
`E:/opencode_harness_portable/projects/opencode-dom-memory-plugin/.dom-memory/main/dom.sqlite3`.

Чтобы направить конкретный запуск в другую директорию, передайте MCP-серверу `--directory`
(правка `command` в конфиге) либо задайте `DOM_DIRECTORY` в окружении.
Явная директория должна сопровождаться корректным `DOM_RUNTIME_ROOT`, иначе
`mockup.admission` откажет с `RUNTIME_PATH_REQUIRED`.

---

## 5. Откат live-базы после теста

Перед экспериментами сохраните текущую базу:

```powershell
$run = "E:/opencode_harness_portable/projects/opencode-dom-memory-plugin/.dom-memory/main"
$backup = "C:\Temp\opencode\dom-live-backup-$(Get-Date -Format yyyyMMdd-HHmmss)"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item "$run\*" $backup -Recurse -Force
```

Откат:

```powershell
$run = "E:/opencode_harness_portable/projects/opencode-dom-memory-plugin/.dom-memory/main"
$backup = Get-ChildItem "C:\Temp\opencode" -Directory -Filter "dom-live-backup-*" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
Copy-Item "$($backup.FullName)\*" $run -Recurse -Force
```

Каталог `C:\Temp\opencode` — единственное место, где dev-процесс зависит от `C:\Temp`,
и только для ручных бэкапов. Репозиторий от него не зависит.

Если база не нужна, её можно просто удалить: она пересоздастся первым хуком
(`.dom-memory/main/dom.sqlite3`). Ledger append-only и хеш-цепочный, поэтому
«починить» его правкой строк нельзя — только откатить файл из бэкапа.

---

## 6. Live-тест субагента

Тот же конфиг работает и для субагента — просто передайте `--agent`:

```powershell
powershell -File tools/run_dev_session.ps1 -Prompt "Reply with exactly: ok" --agent explore
```

Или напрямую, не забыв про переменную:

```powershell
$env:OPENCODE_CONFIG = "E:/opencode_harness_portable/projects/opencode-dom-memory-plugin/opencode/opencode.dev.json"
opencode-cli run --standalone --print-logs --auto --format json --agent explore "Reply with exactly: ok"
Remove-Item Env:OPENCODE_CONFIG
```

Проверка та же: в stderr должна быть строка `[dom-memory hooks] root=<repo>/opencode project=<repo>`.

---

## 7. Ограничение

`opencode/opencode.dev.json` — инструмент **разработки плагина**. Он жёстко привязан к
путям этой машины и грузит только проектный плагин, поэтому не предназначен для
распространения.

Для пользователей штатный путь установки — `dom install --project`, который появится
в версии **0.3.2** и сам создаст корректный конфиг с нужными путями.
