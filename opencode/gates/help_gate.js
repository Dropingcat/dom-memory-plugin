/**
 * Help gate: on a DOM tool error, hand the model an actionable "what to do next".
 *
 * The hook is advisory: it observes `execute.after` and returns a help line, the
 * caller injects it into the session instructions.  A gate failure never blocks
 * the prompt and never changes the tool outcome.
 *
 * The texts below are the `help` field of the canonical `opencode/HELP_DATA.py`
 * (contract 4.1) for the tools an agent hits most often.  The full dictionary —
 * usage, preconditions, postconditions, common_errors — is served live by the
 * `dom_help` MCP tool; keeping only `help` here means the hot path stays a pure
 * in-process lookup (no subprocess on every tool error).
 *
 * The subset lives in JS, so it cannot read HELP_DATA.py at runtime; when the
 * Python help text changes, update this map too.  `tests/check_help_gate.mjs`
 * pins the shape (non-empty, ≤500 chars, dom_* keys) so drift is visible.
 *
 * BUG-2: hosted MCP calls arrive server-prefixed (`dom-memory_dom_claim`), so
 * `applyHelpGate` normalizes the name to the `dom_*` namespace before lookup.
 */

/**
 * Master switch for the advisory error-help gate.
 *
 * Off by default: enabling it assumes the host really fires `execute.after` for
 * MCP tool calls and may report the server-prefixed name.  Set the plugin
 * option `helpGate: true` (or flip this constant) only after a live probe has
 * confirmed both; until then the injected help line is dead weight.
 */
export const HELP_GATE_ENABLED = false

/** @type {Record<string, string>} */
export const HELP_DATA = {
  dom_describe:
    "Тебе НУЖНО: 1) Вызвать dom_describe({}) первым делом в новой сессии. 2) Запомнить state, profile, branches и review_level. 3) Если state=NOT_STARTED — вызвать dom_start с запросом заказчика. 4) Если state=FOREIGN_BASE — решить, твоя ли это база (adopt). 5) Брать коды веток только из branches, не придумывать свои.",
  dom_claim:
    "Тебе НУЖНО: 1) Взять branch из dom_describe (или plan_task — тогда branch вычислится сам). 2) Выбрать type и передать непустой text дословно. 3) Добавить обязательные для типа поля: decision→rationale, quote→source_ref, formula→notation+units, code→repository_ref+relative_path, data→artifact_ref+units, concept→definition. 4) Сохранить claim_id: он нужен для dom_update, basis и dom_context.",
  dom_update:
    "Тебе НУЖНО: 1) Найти claim_id через dom_find и прочитать текущую версию. 2) Передать полный новый text и непустой reason. 3) Передать expected_current=claim_id прочитанной версии — иначе при гонке придёт CONFLICT_READ_REQUIRED. 4) Если текущая версия чужая и не подходит — mode=fork. 5) Корневой request не обновляется (ROOT_IMMUTABLE).",
  dom_annotate:
    "Тебе НУЖНО: 1) Прочитать claim через dom_context/dom_find и взять claim_id. 2) Выбрать kind: comment (text), link (url+locator), property (name+value), archive, conflict/resolve (conflict_ref); для archive, conflict и resolve обязателен reason. 3) Не использовать серверные имена (body, text, branch) как property. 4) Помнить: claim не меняется, событие добавляется в историю.",
  dom_find:
    "Тебе НУЖНО: 1) Передать query (подстрока без учёта регистра) или оставить пустым — тогда вернутся последние claims. 2) Сузить поиск через branch/type/plan_task/keyword/current_only. 3) Держать limit в 1..200. 4) Взять claim_id из matches для basis, dom_update или dom_context. 5) Если total=0 — изменить query, а не повторять тот же вызов.",
  dom_context:
    "Тебе НУЖНО: 1) Передать claim_id любой версии (из dom_find/dom_claim). 2) При необходимости увеличить depth (1..32), чтобы увидеть более длинную цепочку refines. 3) Прочитать refines_chain, basis и plan — откуда claim произошёл. 4) Использовать этот вызов перед dom_update/dom_annotate, чтобы получить актуальную версию.",
  dom_plan_status:
    "Тебе НУЖНО: 1) Вызвать без аргументов (check_every по умолчанию 10). 2) Посмотреть has_plan, open_items, plan_check_due, conflicts и request. 3) Если has_plan=false или request.kind=plan_request — отправить план через dom_plan_ingest. 4) Если plan_check_due=true — проверить, изменился ли план, и переслать его. 5) Если conflicts непусты — прочитать dom_plan_tree и свести изменения.",
  dom_plan_ingest:
    "Тебе НУЖНО: 1) Взять source_revision из dom_plan_status — это base_revision. 2) Передать plan нумерованными пунктами (1., 1.1) или JSON [{\"title\",\"context\",\"children\"}]. 3) Добавить contexts: путь пункта → контекст из разговора. 4) При PLAN_CONFLICT прочитать dom_plan_tree, слить изменения и повторить с base_revision=current_revision. 5) force=true — только осознанно, когда перезапись чужих изменений действительно нужна.",
  dom_plan_tree:
    "Тебе НУЖНО: 1) Передать root — id пункта (П1), путь (П1.2) или код ветки (AA100). 2) Задать depth 1..10 при необходимости. 3) require_fresh=true, если нельзя отвечать по устаревшей проекции. 4) Взять plan_task_id и branch из tasks для dom_claim(plan_task=...) и dom_task_claims. 5) Если stale=true — переслать план через dom_plan_ingest.",
  dom_task_claims:
    "Тебе НУЖНО: 1) Передать task — id, путь (П1.2) или код ветки. 2) include_children=true, если нужны подпункты. 3) link_role отфильтрует роли (defines/justifies/implements/validates/documents/depends_on). 4) include_history=true покажет заменённые версии. 5) Проверить link_stale и orphaned_links, прежде чем опираться на связи.",
  dom_skill_select:
    "Тебе НУЖНО: 1) Передать step — объект с title и, желательно, context, cap[], markers, category_hint. 2) При необходимости queue — до 3 следующих шагов для prefetch. 3) Вставить block (<=800 символов) в промпт. 4) Учесть gaps/missing_caps: если нужного скила нет, не выдумывать его. 5) Помнить: вызов read-only, ledger не трогает.",
  dom_help:
    "Тебе НУЖНО: 1) Передать имя инструмента в tool, например dom_claim, dom_find, dom_update. 2) Прочитать usage, preconditions и common_errors — там написано, что сделать до и после вызова. 3) При ошибке любого инструмента вызвать dom_help с его именем и выполнить шаги из help. 4) Помнить: вызов read-only, ledger не меняется.",
  _default:
    'Тебе НУЖНО: 1) Вызвать dom_help({tool: "<tool_name>"}) для подробной справки. 2) Проверить аргументы. 3) Повторить вызов.',
};

/** Statuses the host may report instead of an `error` field. */
const ERROR_STATUSES = new Set(["error", "failed", "failure", "denied"])

/**
 * Help text for a tool; unknown tools get the generic default.
 * @param {string} toolName
 * @returns {string}
 */
export function helpFor(toolName) {
  return HELP_DATA[toolName] || HELP_DATA["_default"]
}

/** Does this execute.after event describe a failed call? */
function isErrorEvent(event, output) {
  if (event?.error) return true
  const status = event?.status ?? event?.outcome?.status ?? event?.result?.status
  if (typeof status === "string" && ERROR_STATUSES.has(status.toLowerCase())) return true
  const result = event?.result ?? output
  return !!(result?.isError === true || result?.error)
}

/**
 * Normalize a host tool name to the DOM namespace.
 *
 * MCP hosts prefix the name with the server id, e.g. `dom-memory_dom_claim`;
 * dropping the first `_`-separated segment recovers `dom_claim`.  A native
 * `dom_*` name is returned untouched, and anything else is returned as-is so
 * the caller can reject it.
 *
 * @param {unknown} value
 * @returns {string}
 */
export function normalizeToolName(value) {
  if (typeof value !== "string") return ""
  if (value.includes("_") && !value.startsWith("dom_")) {
    return value.split("_").slice(1).join("_")
  }
  return value
}

/**
 * Pure decision: which help, if any, belongs to this tool event.
 *
 * @param {{tool?: string, toolID?: string, name?: string, error?: unknown}} event
 * @param {{isError?: boolean, error?: unknown}} [output]  host's second hook argument
 * @param {{enabled?: boolean}} [options]  defaults to HELP_GATE_ENABLED
 * @returns {{tool: string, help: string} | undefined}
 */
export function applyHelpGate(event, output, options = {}) {
  if (!(options?.enabled ?? HELP_GATE_ENABLED)) return undefined
  if (!isErrorEvent(event, output)) return undefined
  const toolName = normalizeToolName(event?.tool ?? event?.toolID ?? event?.name)
  if (!toolName.startsWith("dom_")) return undefined
  console.warn(`[dom-memory hooks] error help: ${toolName} failed -> injected`)
  return { tool: toolName, help: helpFor(toolName) }
}

export default { applyHelpGate, helpFor, normalizeToolName, HELP_DATA, HELP_GATE_ENABLED }
