# Карта развёртывания DOM Memory plugin

**Дата:** 2026-11-27
**Метод:** read-only инвентаризация (Test-Path / Select-String / ConvertFrom-Json)
**Статус:** актуально на момент проверки

---

## 1. OpenCode конфиги

| Путь | DOM MCP | DOM plugin | Статус |
|---|---|---|---|
| `C:\Users\Arhys\.config\opencode\opencode.jsonc` | нет | нет | active (глобальные npm-плагины) |
| `E:\opencode_harness_portable\.opencode\opencode.json` | да — `dom_server.py`, `OPENCODE_PROJECT_DIR=...omh-baseline-dom-mcp` | да — dist 0.2.6, projectDir=`...omh-baseline-dom-mcp` | **active (harness)** |
| `E:\opencode_harness_portable\.opencode\opencode.json.bak` | нет | нет | backup |
| `<repo>\.opencode\opencode.json` | да — `dom_server.py`, `OPENCODE_PROJECT_DIR=<repo>` | да — repo 0.3.1, projectDir=`<repo>` | **active (project)** |
| `<repo>\opencode\opencode.dev.json` | да — `dom_server.py`, `OPENCODE_PROJECT_DIR=<repo>` | да — repo 0.3.1 | **dev (isolation)** |

`<repo>` = `E:\opencode_harness_portable\projects\opencode-dom-memory-plugin`.
`<omh-baseline>` = `E:\opencode_harness_portable\projects\omh-baseline-dom-mcp`.

---

## 2. Плагины (JS hooks) с id `dom-memory.hooks`

| Путь | Версия | Статус |
|---|---|---|
| `<repo>\opencode\index.js` | **0.3.1** | active (project + dev config) |
| `<omh-baseline>\.claude\worktrees\mcp-llm-server-054018\dist\claude\dom-memory\opencode-plugin\index.js` | **0.2.6** | active (harness config) |
| `<omh-baseline>\.claude\worktrees\mcp-llm-server-054018\dist\claude\dom-memory-0.2.4-backup-2026-11-23\opencode-plugin\index.js` | 0.2.4 | legacy backup |
| `<omh-baseline>\.claude\worktrees\mcp-llm-server-054018\dist\opencode\dom-memory\opencode-plugin\index.js` | 0.2.3 | legacy |
| `<omh-baseline>\.claude\worktrees\mcp-llm-server-054018\plugin\opencode\index.js` | (legacy dev) | legacy |

**Дублирование по id:** все пять экземпляров объявляют `id: "dom-memory.hooks"`. OpenCode грузит только первый по порядку конфигов; глобальный/харнесс-конфиг выигрывает у проектного. Именно поэтому для live-теста нужен `OPENCODE_CONFIG` с изолированным dev-конфигом.

---

## 3. MCP серверы DOM

| Конфиг | Команда | Project dir | Статус |
|---|---|---|---|
| `<omh-baseline>\...` (harness) | `python ...\dom_server.py` | `<omh-baseline>` | active (harness) |
| `<repo>\.opencode\opencode.json` | `python <repo>\opencode\scripts\dom_server.py` | `<repo>` | active (project) |
| `<repo>\opencode\opencode.dev.json` | то же | `<repo>` | dev |

**Важно:** в `<repo>\.dom-memory\main\dom.sqlite3` лежит реестр `work_ref lite:c60130ee...` (36 пунктов, советские учебники), а не план разработки DOM. Требуется сброс/перенос памяти и явный `--directory`.

---

## 4. Skills

| Корень | SKILL.md | Статус |
|---|---:|---|
| `C:\Users\Arhys\.config\opencode\skills` | 134 | active |
| `C:\Users\Arhys\.claude\skills` | 14 | active |
| `<repo>\opencode\skills` | 1 (`dom-memory`) | active (plugin skill) |

Итого: **149** описаний; индексатор `skill_index.py` собирает 148 из двух основных корней (repo-скилл подключается опционально через `skill_roots(project_root)`).

---

## 5. Claude Desktop

| Путь | Версия | Статус |
|---|---|---|
| `%USERPROFILE%\.claude\` (плагины Claude) | не проверялось в этой инвентаризации | `[UNVERIFIED]` |

Плагин Claude живёт отдельным контуром (`.claude-plugin/plugin.json`, hooks.json) и не конфликтует с OpenCode-плагином по id. Проверить перед дедупликацией.

---

## 6. Выводы

1. **Два активных DOM-плагина** (0.2.6 в harness, 0.3.1 в проекте) с одинаковым id — источник дедупликации. Для постоянной работы оставить один:
   - либо удалить entry 0.2.6 из `E:\opencode_harness_portable\.opencode\opencode.json`,
   - либо оставить harness-плагин только для `<omh-baseline>` и использовать dev-конфиг для репо.
2. **MCP не дублируется** — только один сервер на конфиг.
3. **Память проекта** должна быть явно указана через `--directory` и/или `OPENCODE_PROJECT_DIR`.
4. **Legacy-копии** 0.2.3/0.2.4 можно оставить как backup, но они не должны попадать в `plugins` активных конфигов.
