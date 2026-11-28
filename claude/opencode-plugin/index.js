/**
 * DOM project-memory hooks for OpenCode V2 (dependency-free Promise plugin).
 *
 * The Claude Code plugin wires two hooks (hooks/hooks.json):
 *   PostToolUse / ExitPlanMode  -> hooks/plan_hook.py --now      (approved plan into memory)
 *   UserPromptSubmit            -> hooks/plan_check_hook.py      (duty cycle: take in queued plans,
 *                                                                 ask for a plan check when due)
 *
 * OpenCode has no ExitPlanMode tool: plan mode is the `plan` agent, which may write plan files
 * under ~/.opencode/plan, and the plan is approved when the user switches to another agent.
 * The port keeps the same hook programs and the same triggers:
 *   session "prompt"  -> plan_check_hook.py; its request is queued for the model
 *   session "context" -> plan_hook.py --now for the newest fresh plan file, but only for a
 *                        non-plan agent (the moment of approval); queued notes are injected
 *                        into the model request (the OpenCode equivalent of additionalContext)
 *
 * The plan file is a distillation of the conversation, so the context hook also sends the
 * discussion that produced it (the user/assistant text of event.messages, tool calls and
 * tool results excluded). plan_hook.py distils that into a context per plan item, and the
 * header claim of an item then carries the reason it exists, not only its title.
 *
 * The Python hook programs are the single source of truth: this file only decides when to run
 * them and how their answer reaches the model. A hook never blocks the session: every failure is
 * reported to the model instead of thrown.
 *
 * Options (opencode.json -> plugins[].options):
 *   pythonPath  interpreter with the server dependencies (default DOM_PYTHON / PYTHON_BIN / python)
 *   pluginRoot  directory that holds hooks/plan_hook.py (default: this artifact root)
 *   projectDir  DOM project directory (default OPENCODE_PROJECT_DIR / plugin location)
 *   directory   DOM run directory (default <projectDir>/.dom-memory/main)
 *   planDir     where the plan agent writes plan files (default ~/.opencode/plan)
 *   principal   DOM_PRINCIPAL of this host session
 *   role        DOM_ROLE (owner | author | reviewer)
 *   checkEvery  DOM_CHECK_EVERY (plan-check period, default 10)
 *   autostart   start the project memory on the first task (default true); false = DOM_AUTOSTART=0
 *   timeoutMs   per hook-program timeout (default 60000)
 *   skillTimeoutMs  ceiling for the skill queue producer inside a model request (default 4000)
 *   enabled     false turns both hooks off
 */
import { spawnSync } from "node:child_process"
import { createHash } from "node:crypto"
import { existsSync, readFileSync, readdirSync, realpathSync, statSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { createObservability, preview, sha256 } from "./observability.js"
import { applyHelpGate, HELP_GATE_ENABLED } from "./gates/help_gate.js"
import { checkReemit, computeFingerprint } from "./gates/tau_gate.js"

const HERE = dirname(fileURLToPath(import.meta.url))
const PLAN_MAX_AGE_MS = 15 * 60 * 1000
const PLAN_AGENT = "plan"
const DISCUSSION_MAX_CHARS = 24000 // the plan discussion, not the whole session history
const DISCUSSION_MAX_MESSAGES = 80

/* L7 trusted-write gate (advisory).  dom_claim stands on its anatomy basis,
   dom_update/dom_annotate stand on the claim they change.  The hook asks the run
   database whether this same session already read those claim ids; the Python
   Service gate (admission.Service._gate_check) remains the invariant. */
const GATE_TOOLS = new Set(["dom_claim", "dom_update", "dom_annotate"])
const GATE_QUERY = `import sqlite3,sys
db,sid,ids=sys.argv[1],sys.argv[2],sys.argv[3:]
try:
    con=sqlite3.connect(db,timeout=2)
    bad=[i for i in ids if con.execute('SELECT 1 FROM read_receipts WHERE session_id=? AND claim_id=? LIMIT 1',(sid,i)).fetchone() is None]
except Exception:
    sys.exit(0)
sys.exit(1 if bad else 0)`

function gateIds(tool, args) {
  if (tool === "dom_claim") {
    return Array.isArray(args?.basis) ? args.basis.filter((id) => typeof id === "string" && id.trim()) : []
  }
  if (tool === "dom_update" || tool === "dom_annotate") {
    return typeof args?.claim_id === "string" && args.claim_id.trim() ? [args.claim_id] : []
  }
  return []
}

function findRoot(explicit) {
  const candidates = explicit ? [resolve(explicit)] : [HERE, resolve(HERE, ".."), resolve(HERE, "../..")]
  for (const dir of candidates) {
    if (existsSync(join(dir, "hooks", "plan_hook.py"))) return dir
  }
  throw new Error(`dom-memory hooks: hooks/plan_hook.py not found (looked in ${candidates.join(", ")})`)
}

function digest(text) {
  return createHash("sha256").update(text).digest("hex")
}

function sessionOf(event) {
  const id = event?.sessionID ?? event?.session?.id
  return typeof id === "string" ? id : ""
}

function newestPlanFile(planDir, now) {
  let names
  try {
    names = readdirSync(planDir)
  } catch {
    return null
  }
  let best = null
  for (const name of names) {
    if (!name.toLowerCase().endsWith(".md")) continue
    const path = join(planDir, name)
    try {
      const stat = statSync(path)
      if (!stat.isFile() || now - stat.mtimeMs > PLAN_MAX_AGE_MS) continue
      if (!best || stat.mtimeMs > best.mtime) best = { path, mtime: stat.mtimeMs }
    } catch {
      /* a plan file removed between readdir and stat is not an error */
    }
  }
  return best
}

/* Mirrors hooks/plan_hook.py stamp_path/read_stamp: the consumption sidecar.
   Returns null when absent or unreadable. */
function readStamp(planFile) {
  try {
    const data = JSON.parse(readFileSync(`${planFile}.dom.json`, "utf8"))
    return data && typeof data === "object" ? data : null
  } catch {
    return null
  }
}

/* Text of one request message. The model message is {role, content[]} with
   text/media/tool-call/tool-result parts: only text is discussion. */
function messageText(message) {
  const content = message?.content
  if (typeof content === "string") return content.trim()
  if (!Array.isArray(content)) return ""
  const parts = []
  for (const part of content) {
    if (part && part.type === "text" && typeof part.text === "string" && part.text.trim()) parts.push(part.text)
  }
  return parts.join("\n").trim()
}

/* The tail of the conversation that produced the plan, role-marked. Keeps whole
   messages while they fit the budget and always keeps the newest one (truncated
   from the head), so the plan discussion survives a long session. */
export function discussionFromMessages(messages) {
  if (!Array.isArray(messages)) return ""
  const lines = []
  for (const message of messages) {
    const role = message?.role
    if (role !== "user" && role !== "assistant") continue
    const text = messageText(message)
    if (text) lines.push(`${role === "user" ? "User" : "Assistant"}: ${text}`)
  }
  let out = ""
  let kept = 0
  for (let i = lines.length - 1; i >= 0 && kept < DISCUSSION_MAX_MESSAGES; i--) {
    let line = lines[i]
    if (!out && line.length > DISCUSSION_MAX_CHARS) {
      out = line.slice(line.length - DISCUSSION_MAX_CHARS) // the newest message is the plan itself
      break
    }
    if (out && out.length + line.length + 2 > DISCUSSION_MAX_CHARS) break
    out = out ? `${line}\n\n${out}` : line
    kept++
  }
  return out
}

/* The plan brief's CURRENT line is the active micro-step (sigma):
     CURRENT: П15 "card — verified card has this skill" [ER pending] anchor 1a2b3c4d CAP: sqlite,migration
     CURRENT (auto): П1 "Timeout: 100 ms" [pending blocked] anchor ?
   The line number is the step id (the plan path), the quoted text the title, the
   plan revision the invalidation key; a trailing CAP: list (from the header's
   declared `accept`) is the step's capability envelope.  CURRENT MEANING/WHY/NOW
   are the step's own recorded context, so they are passed along (bounded like
   README's sigma context).  Returns null when the brief carries no plan: no
   step -> no skill queue. */
export function stepFromBrief(note) {
  const text = typeof note === "string" ? note : ""
  if (!text) return null
  const line = text.split("\n").find((row) => /^\s*CURRENT(?: \(auto\))?:/i.test(row))
  if (!line) return null
  const body = line.replace(/^\s*CURRENT(?: \(auto\))?:\s*/i, "").trim()
  const match = /^(\S+)\s+"(.*)"\s*\[/.exec(body) || /^(\S+)\s+"(.*)"/.exec(body)
  if (!match) {
    // A CURRENT line that does not parse is a producer degradation, not "no plan":
    // make it visible instead of silently skipping the skill queue.
    console.warn("[dom-memory] brief parse failed:", text.slice(0, 100))
    return null
  }
  const stepId = match[1].trim()
  const title = match[2].trim()
  if (!stepId || !title) {
    console.warn("[dom-memory] brief parse failed:", text.slice(0, 100))
    return null
  }
  const step = { step_id: stepId, title, cap: [] }
  const capMatch = /\bCAP:\s*([\w,-]+)\s*$/i.exec(body)
  if (capMatch) {
    step.cap = capMatch[1]
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean)
  }
  const context = []
  for (const key of ["CURRENT MEANING", "WHY", "NOW"]) {
    const row = text.split("\n").find((item) => item.trim().startsWith(`${key}:`))
    if (!row) continue
    const value = row.slice(row.indexOf(":") + 1).trim()
    if (value && !/^not recorded$/i.test(value) && value !== "?") context.push(value)
  }
  if (context.length) step.context = context.join(" ").slice(0, 600) // MicroStep CONTEXT_LIMIT
  const version = /\[DOM MEMORY INDEX\][^\n]*?\bplan\s+r(\d+)/i.exec(text)
  if (version) step.plan_version = Number(version[1])
  return step
}

function noteOf(stdout) {
  if (!stdout) return ""
  try {
    const data = JSON.parse(stdout)
    const text = data?.hookSpecificOutput?.additionalContext
    return typeof text === "string" && text.trim() ? text.trim() : ""
  } catch {
    return stdout.trim() /* plan_check_hook prints plain text */
  }
}

/** Register both hooks on a plugin context; returns the cleanup function. */
export async function register(ctx) {
  const options = ctx.options ?? {}
  const root = findRoot(options.pluginRoot)
  const hooksDir = join(root, "hooks")
  const planHook = join(hooksDir, "plan_hook.py")
  const checkHook = join(hooksDir, "plan_check_hook.py")
  const python = options.pythonPath || process.env.DOM_PYTHON || process.env.PYTHON_BIN || "python"
  const projectDir = resolve(
    options.projectDir || process.env.OPENCODE_PROJECT_DIR || ctx.location?.directory || process.cwd(),
  )
  /* Without an explicit run directory the hook programs resolve it themselves from
     OPENCODE_PROJECT_DIR (<project>/.dom-memory/main) and pin DOM_RUNTIME_ROOT — the layout a
     packaged host plugin is built for. An explicit directory must come with its runtime root,
     otherwise mockup.admission refuses the path (RUNTIME_PATH_REQUIRED). */
  const domDir = options.directory ? resolve(options.directory) : null
  const planDir = resolve(options.planDir || join(homedir(), ".opencode", "plan"))
  const timeoutMs = Number(options.timeoutMs) > 0 ? Number(options.timeoutMs) : 60_000
  const env = { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1", OPENCODE_PROJECT_DIR: projectDir, DOM_PROJECT_DIR: projectDir }
  if (domDir) {
    env.DOM_DIRECTORY = domDir
    env.DOM_RUNTIME_ROOT = resolve(domDir, "..")
  }
  if (options.principal) env.DOM_PRINCIPAL = String(options.principal)
  if (options.role) env.DOM_ROLE = String(options.role)
  if (options.checkEvery) env.DOM_CHECK_EVERY = String(options.checkEvery)
  if (options.autostart === false) env.DOM_AUTOSTART = "0"

  if (options.enabled === false) {
    console.error("[dom-memory hooks] disabled by options.enabled=false")
    return () => {}
  }
  const runDir = domDir || join(projectDir, ".dom-memory", "main")
  const dbFile = join(runDir, "dom.sqlite3")
  let dbPath = dbFile
  try {
    dbPath = realpathSync(dbFile)
  } catch {
    /* the database is created by the first hook run */
  }
  const observability = createObservability({
    projectDir,
    pid: process.pid,
    retentionDays: Number(options.retentionDays) > 0 ? Number(options.retentionDays) : 7,
  })
  console.error(`[dom-memory hooks] root=${root} project=${projectDir} planDir=${planDir}`)

  const queueProducer = join(root, "scripts", "queue_producer.py")
  const skillQueueEnabled = existsSync(queueProducer)
  if (!skillQueueEnabled) {
    console.error("[dom-memory hooks] queue_producer.py not found — skill queue disabled")
  }
  /* The producer walks the skill index (~0.25 s warm) inside a model request, so it
     gets its own, shorter ceiling than the plan hooks; the result cache in the
     producer is what keeps the common case cheap. */
  const skillTimeoutMs = Number(options.skillTimeoutMs) > 0 ? Number(options.skillTimeoutMs) : 4000

  const pending = new Map()
  const planDigests = new Map() // sessionID -> sha256 of the last plan file this session consumed
  const briefs = new Map() // sessionID -> last DOM brief from the prompt hook, reused on tool continuations

  const queue = (sessionID, text) => {
    if (!text) return
    const key = sessionID || ""
    const list = pending.get(key) ?? []
    list.push(text)
    pending.set(key, list)
  }

  const run = (script, args, input, sessionID, limitMs = timeoutMs) => {
    const result = spawnSync(python, ["-B", script, ...args], {
      input,
      encoding: "utf8",
      timeout: limitMs,
      cwd: projectDir,
      env: sessionID
        ? { ...env, DOM_SESSION: sessionID, DOM_SESSION_SOURCE: "hook", DOM_ORIGIN: "hook" }
        : env,
      windowsHide: true,
      maxBuffer: 8 * 1024 * 1024,
    })
    return { stdout: (result.stdout ?? "").trim(), stderr: (result.stderr ?? "").trim(), error: result.error }
  }

  const ingestPlan = (text, sessionID, planFile, discussion) => {
    const payload = JSON.stringify({
      hook_event_name: "PostToolUse",
      tool_name: "ExitPlanMode",
      tool_input: { plan: text },
      tool_response: {},
      cwd: projectDir,
      session_id: sessionID || undefined,
      dom_discussion: discussion || undefined,
    })
    const args = ["--now"]
    if (domDir) args.push("--directory", domDir)
    if (planFile) args.push("--plan-file", planFile) // B+D: consumption stamp + stale-file protection
    const result = run(planHook, args, payload, sessionID)
    const note = noteOf(result.stdout)
    if (note) return note
    if (result.error) return `[DOM project memory] plan_hook failed: ${result.error.message}`
    if (result.stderr) return `[DOM project memory] plan_hook: ${result.stderr}`
    return ""
  }

  const planCheck = (promptText, sessionID) => {
    const result = run(checkHook, domDir ? [domDir] : [], promptText ?? "", sessionID)
    if (result.error) return `[DOM project memory] plan_check_hook failed: ${result.error.message}`
    const note = noteOf(result.stdout)
    if (note) return note
    if (result.stderr) return `[DOM project memory] plan_check_hook: ${result.stderr}`
    return ""
  }

  /* L7: the plugin-side gate.  Advisory: it refuses the call early with a reason,
     but the Service invariant (GATE_NO_READ) still decides.  A missing database or
     a failed probe never blocks: the hook only speaks when it can prove a miss. */
  const gateCovers = (sessionID, ids) => {
    if (!sessionID || !ids.length || !existsSync(dbFile)) return true
    const result = spawnSync(python, ["-c", GATE_QUERY, dbFile, sessionID, ...ids], {
      encoding: "utf8",
      timeout: timeoutMs,
      env,
      windowsHide: true,
    })
    if (result.error) return true
    return result.status === 0
  }

  /* tau-gate: only the *skill block* is gated.  The DOM plan notes stay ungated --
     they carry the duty-cycle request and must reach the model.  A skill block arrives
     on the prompt event (from the queue producer); an unchanged fingerprint means the
     same block would be re-injected, so it is dropped and the idle streak counted.
     No hard cap: a real change always resets the counter and is emitted (H1). */
  const skillBlockOf = (event) => {
    const block = event?.skillBlock ?? event?.step?.skillBlock
    if (!block || typeof block !== "object") return null
    const text = typeof block.text === "string" ? block.text.trim() : ""
    if (!text) return null
    const step = event?.step ?? {}
    return {
      text,
      state: {
        stepId: block.stepId ?? block.step_id ?? step.stepId ?? step.step_id ?? step.id ?? "",
        capSet: block.capSet ?? block.cap_set ?? step.cap ?? step.capSet ?? [],
        categoryHint: block.categoryHint ?? block.category_hint ?? step.categoryHint ?? step.category_hint ?? "",
        markers: block.markers ?? step.markers ?? [],
        planVersion:
          block.planVersion ?? block.plan_version ?? step.planVersion ?? step.plan_version ?? event?.planVersion ?? 0,
        indexDigest: block.indexDigest ?? block.index_digest ?? step.indexDigest ?? step.index_digest ?? "",
      },
    }
  }

  const applyTauGate = (sessionID, event) => {
    const block = skillBlockOf(event)
    if (!block) return
    const fingerprint = computeFingerprint(block.state)
    const { shouldReemit, idleCount } = checkReemit(sessionID || "", fingerprint)
    if (!shouldReemit) {
      console.warn(`[τ-gate] idle (${idleCount}), block not re-emitted`)
      return
    }
    /* Entry transport first (the host announces it at the next step boundary), the
       queue -> event.system.push fallback otherwise. */
    const entry = ctx.session?.instructions?.entry
    if (typeof entry?.put === "function") entry.put("dom.skill-queue", block.text)
    else if (typeof entry?.set === "function") entry.set("dom.skill-queue", block.text)
    else queue(sessionID, block.text)
    console.warn(`[τ-gate] emit fp=${fingerprint.slice(0, 12)}`)
  }

  /* Skill queue producer: the context hook turns the active step of the plan brief
     into a queue block by running the Python pipeline.  This is the native producer
     the tau-gate above was built for: the producer returns the normalised sigma
     (cap_set, derived category hint, markers, plan version, index digest), the gate
     hashes exactly that, and only a new fingerprint reaches the instruction entry.
     Advisory throughout: a missing index, a crash or a timeout is logged and the
     model request goes on without a block. */
  const skillQueueFor = (sessionID, event) => {
    if (!sessionID || !skillQueueEnabled) return null
    const note = briefs.get(sessionID)
    const step = stepFromBrief(note)
    if (!step) return null // no plan -> no step -> no queue
    const args = ["--step", JSON.stringify(step)]
    if (domDir) args.push("--directory", domDir)
    const result = run(queueProducer, args, "", sessionID, skillTimeoutMs)
    if (result.error) {
      console.warn("[dom-memory] skill-producer: timeout/error:", result.error.message)
      return { status: "error", error: result.error.message }
    }
    let data
    try {
      data = JSON.parse(result.stdout)
    } catch (error) {
      const detail = result.stderr ? `; stderr: ${result.stderr.trim().slice(0, 200)}` : ""
      console.warn("[dom-memory] skill-producer: timeout/error:", `${error.message}${detail}`)
      return { status: "error", error: "unparsable output" }
    }
    if (data?.status !== "OK" || typeof data.block_text !== "string" || !data.block_text.trim()) {
      const message = data?.error || "empty result"
      console.warn(`[dom-memory] skill-producer: ${message}`)
      return { status: "error", error: message }
    }
    const state = data.state && typeof data.state === "object" ? data.state : step
    const fingerprint = computeFingerprint(state)
    if (data.fingerprint && data.fingerprint !== fingerprint) {
      // Same sigma, two processes: a mismatch is a pipeline bug, not a reason to block.
      console.warn(
        `[dom-memory] skill-producer: fingerprint drift py=${String(data.fingerprint).slice(0, 12)} ` +
          `js=${fingerprint.slice(0, 12)}`,
      )
    }
    const queueList = Array.isArray(data.queue) ? data.queue.map((id) => String(id)) : []
    const top = queueList[0] || "skill_search"
    const { shouldReemit, idleCount } = checkReemit(sessionID, fingerprint)
    if (!shouldReemit) {
      console.log(`[τ-gate] skill-queue idle (${idleCount}), block not re-emitted`)
      return { status: "idle", skill_id: top, idle: idleCount, fingerprint }
    }
    const text = data.block_text.trim()
    /* F1: `entry.put` is not verifiable from inside the hook — in the live host it
       returned normally and the block never reached the prompt.  The request under
       assembly (`event.system`) is the channel whose delivery can be checked here,
       so the entry is only the first attempt and `event.system.push` is both the
       fallback and the verification.  The status follows the observed delivery, not
       the attempt: nothing is logged as `injected` until the block is seen. */
    const delivery = deliverSkillBlock(sessionID, event, text)
    if (delivery.status !== "injected") {
      console.warn(
        `[dom-memory] skill-queue ${delivery.status}: ${top} not delivered ` +
          `(${text.length} chars, fp ${fingerprint.slice(0, 12)})` +
          (delivery.error ? `: ${delivery.error}` : ""),
      )
      return {
        status: delivery.status,
        skill_id: top,
        queue: queueList,
        gaps: Array.isArray(data.gaps) ? data.gaps.map((gap) => String(gap)) : [],
        confidence_bp: Number(data.confidence_bp) || 0,
        fingerprint,
        via: delivery.via,
        error: delivery.error,
      }
    }
    console.warn(
      `[dom-memory] skill-queue injected via ${delivery.via}: ${top} (${text.length} chars, idle ${idleCount}, ` +
        `fp ${fingerprint.slice(0, 12)})`,
    )
    return {
      status: "injected",
      skill_id: top,
      queue: queueList,
      gaps: Array.isArray(data.gaps) ? data.gaps.map((gap) => String(gap)) : [],
      confidence_bp: Number(data.confidence_bp) || 0,
      fingerprint,
      via: delivery.via,
    }
  }

  /* Deliver the queue block and report what was actually observed.  Order:
       1. keyed instruction entry (announced by the host at the next step boundary),
       2. `event.system` — the request under assembly; its delivery is verifiable,
       3. the deferred queue (pending notes) as the last resort.
     An entry that neither throws nor mirrors into `event.system` cannot be proven
     here, so it is reported as `unverified` and warned about instead of `injected`. */
  const deliverSkillBlock = (sessionID, event, text) => {
    const entry = ctx.session?.instructions?.entry
    const seen = () =>
      Array.isArray(event?.system) &&
      event.system.some((block) => typeof block?.text === "string" && block.text.includes(text))
    let entryError = null
    try {
      if (typeof entry?.put === "function") entry.put("dom.skill-queue", text)
      else if (typeof entry?.set === "function") entry.set("dom.skill-queue", text)
    } catch (caught) {
      entryError = caught instanceof Error ? caught.message : String(caught)
      console.warn(`[dom-memory] skill-queue: entry.put failed: ${entryError}`)
    }
    if (seen()) return { status: "injected", via: "entry", error: entryError }
    if (Array.isArray(event?.system)) {
      event.system.push({ type: "text", text })
      if (seen()) return { status: "injected", via: "system", error: entryError }
      return { status: "error", via: "system", error: "block missing after push" }
    }
    if (!entryError && (typeof entry?.put === "function" || typeof entry?.set === "function")) {
      return { status: "unverified", via: "entry", error: "entry.put not verifiable" }
    }
    queue(sessionID, text)
    return { status: "queued", via: "queue", error: entryError }
  }

  const executeBefore = async (event, output) => {
    const tool = event?.tool ?? event?.toolID ?? event?.name ?? ""
    if (!GATE_TOOLS.has(tool)) return
    const args = event?.args ?? output?.args ?? event?.input ?? {}
    const sessionID = sessionOf(event) || (typeof event?.sessionID === "string" ? event.sessionID : "")
    const ids = gateIds(tool, args)
    const covered = gateCovers(sessionID, ids)
    console.warn(
      `[dom-memory gate] ${tool} ${ids.length ? (covered ? "ok" : "GATE_NO_READ advisory") : "no basis"} ` +
        `session=${sessionID || "unknown"}${ids.length ? ` claims=${ids.join(",")}` : ""}`,
    )
    if (ids.length && !covered) {
      throw new Error(
        `GATE_NO_READ advisory: ${tool} stands on claim(s) this session has not read (${ids.join(", ")}). ` +
          "Read them with dom_find or dom_context first. The Service gate is the invariant; this hook only warns early.",
      )
    }
  }

  const registrations = []

  /* Error help: a failed DOM call injects an actionable help line as the
     `dom-help` instruction entry.  Advisory only — the error itself is never
     masked, and a gate failure never throws into the host.
     Off unless `helpGate: true` is set: BUG-2 showed the host may never fire
     `execute.after` for MCP tools, so the gate waits for a live probe. */
  const helpGateEnabled = options.helpGate ?? HELP_GATE_ENABLED
  const executeAfter = (event, output) => {
    let result
    try {
      result = applyHelpGate(event, output, { enabled: helpGateEnabled })
    } catch (caught) {
      console.error("[dom-memory hooks] help-gate:", caught)
      return
    }
    if (!result) return
    const text = `⚠️ ${result.tool} failed. ${result.help}`
    try {
      const entry = ctx.session?.instructions?.entry
      if (typeof entry?.put === "function") entry.put("dom-help", text)
      else if (typeof entry?.set === "function") entry.set("dom-help", text)
      else console.warn(`[dom-memory hooks] help-gate: no instruction entry for ${result.tool}`)
    } catch (caught) {
      console.error("[dom-memory hooks] help-gate:", caught)
    }
  }

  /* Claude UserPromptSubmit: the duty cycle runs on every user prompt. */
  registrations.push(
    await ctx.session.hook("prompt", (event) => {
      const started = performance.now()
      const sessionID = sessionOf(event)
      try {
        applyTauGate(sessionID, event)
      } catch (caught) {
        /* advisory: a gate failure never blocks the prompt */
        console.error("[dom-memory hooks] tau-gate:", caught)
      }
      let note = ""
      let error = null
      try {
        note = planCheck(event?.prompt?.text ?? "", sessionID)
        if (note) briefs.set(sessionID || "", note)
        else briefs.delete(sessionID || "")
        queue(sessionID, note)
      } catch (caught) {
        error = caught instanceof Error ? caught.message : String(caught)
        console.error("[dom-memory hooks] prompt:", caught)
      }
      observability.record({
        hook: "prompt",
        phase: "after",
        session_id: sessionID || null,
        duration_ms: Math.round(performance.now() - started),
        source: "dom.plan_check",
        status: error ? "error" : note ? "ok" : "silent",
        injected: note ? { notes: 1, chars: note.length, sha256: sha256(note), preview: preview(note) } : null,
        error,
        db_path: dbPath,
      })
    }),
  )

  /* Claude PostToolUse(ExitPlanMode): the approved plan reaches the memory once, when a
     non-plan agent (build) makes its first model call after the plan file was written. */
  registrations.push(
    await ctx.session.hook("context", (event) => {
      const started = performance.now()
      const notes = []
      const sessionID = sessionOf(event)
      const systemBefore = Array.isArray(event.system) ? event.system.length : 0
      const queued = pending.get(sessionID)
      const queuedCount = queued?.length ?? 0
      let error = null
      if (queued?.length) {
        notes.push(...queued)
        pending.delete(sessionID)
      }
      if (!notes.length && briefs.has(sessionID || "")) {
        notes.push(briefs.get(sessionID || ""))
      }
      try {
        if (String(event?.agent ?? "") !== PLAN_AGENT) {
          const plan = newestPlanFile(planDir, Date.now())
          if (plan) {
            const text = readFileSync(plan.path, "utf8")
            const current = digest(text)
            const last = planDigests.get(sessionID)
            const stamp = readStamp(plan.path)
            const consumedByOther = !!(stamp && stamp.session && stamp.session !== sessionID)
            if (current !== last && !consumedByOther) {
              // B+D: per-session dedup, plus skip a plan already consumed by another session
              // (its plan_hook wrote the <file>.dom.json stamp; a foreign plan is never written here).
              planDigests.set(sessionID, current)
              const note = ingestPlan(text, sessionID, plan.path, discussionFromMessages(event?.messages))
              if (note) notes.push(note)
            }
          }
        }
      } catch (caught) {
        error = caught instanceof Error ? caught.message : String(caught)
        console.error("[dom-memory hooks] context:", caught)
      }
      /* Skill queue producer: run after the plan is ingested (its note joins the
         notes) and before the snapshot, so the injected block is attributable to
         this request.  Advisory: failures are recorded, never thrown. */
      if (!Array.isArray(event?.system)) {
        let assigned = false
        try {
          event.system = []
          assigned = Array.isArray(event.system)
        } catch {
          /* a frozen event keeps the entry/queue fallback */
        }
        if (!assigned) console.warn("[dom-memory] context hook: event.system unavailable")
      }
      let skillQueue = null
      try {
        skillQueue = skillQueueFor(sessionID, event)
      } catch (caught) {
        console.error("[dom-memory hooks] skill-queue:", caught)
      }
      const injectedText = notes.join("\n\n")
      if (notes.length) {
        if (Array.isArray(event?.system)) event.system.push({ type: "text", text: injectedText })
        else console.warn("[dom-memory] context hook: event.system unavailable — notes not delivered")
      }
      const snapshot = observability.snapshot(event, { source: "dom.context", notes })
      observability.record({
        hook: "context",
        phase: "after",
        session_id: sessionID || null,
        duration_ms: Math.round(performance.now() - started),
        source: "dom.context",
        status: error ? "error" : notes.length ? "injected" : "silent",
        extracted: { queued: queuedCount },
        injected: notes.length
          ? { notes: notes.length, chars: injectedText.length, sha256: sha256(injectedText), preview: preview(injectedText) }
          : null,
        skill_queue: skillQueue
          ? {
              status: skillQueue.status,
              skill_id: skillQueue.skill_id ?? null,
              idle: skillQueue.idle ?? 0,
              fingerprint: skillQueue.fingerprint ? skillQueue.fingerprint.slice(0, 16) : null,
              via: skillQueue.via ?? null,
              error: skillQueue.error ?? null,
            }
          : null,
        snapshot: snapshot ? { id: snapshot.id, sha256_full: snapshot.sha256_full, truncated: snapshot.truncated, path: snapshot.path } : null,
        context: {
          system_blocks_before: systemBefore,
          system_blocks_after: Array.isArray(event.system) ? event.system.length : systemBefore,
        },
        error,
        db_path: dbPath,
      })
    }),
  )

  /* L7: registered only when the host exposes the tool domain (the hook tests
     call register() with a session-only ctx).  Advisory: the Service gate stays
     the invariant even when this hook is absent or forged around. */
  if (ctx.tool?.hook) {
    registrations.push(await ctx.tool.hook("execute.before", executeBefore))
    registrations.push(await ctx.tool.hook("execute.after", executeAfter))
  }

  return () => {
    for (const registration of registrations) {
      Promise.resolve(registration?.dispose?.()).catch(() => {})
    }
    observability.close()
  }
}

export default {
  id: "dom-memory.hooks",
  setup(ctx) {
    return register(ctx)
  },
}
