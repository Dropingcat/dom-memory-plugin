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
 *   enabled     false turns both hooks off
 */
import { spawnSync } from "node:child_process"
import { createHash } from "node:crypto"
import { existsSync, readFileSync, readdirSync, realpathSync, statSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { createObservability, preview, sha256 } from "./observability.js"

const HERE = dirname(fileURLToPath(import.meta.url))
const PLAN_MAX_AGE_MS = 15 * 60 * 1000
const PLAN_AGENT = "plan"
const DISCUSSION_MAX_CHARS = 24000 // the plan discussion, not the whole session history
const DISCUSSION_MAX_MESSAGES = 80

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

  const run = (script, args, input, sessionID) => {
    const result = spawnSync(python, ["-B", script, ...args], {
      input,
      encoding: "utf8",
      timeout: timeoutMs,
      cwd: projectDir,
      env: sessionID ? { ...env, DOM_SESSION: sessionID } : env,
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

  const registrations = []

  /* Claude UserPromptSubmit: the duty cycle runs on every user prompt. */
  registrations.push(
    await ctx.session.hook("prompt", (event) => {
      const started = performance.now()
      const sessionID = sessionOf(event)
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
      const injectedText = notes.join("\n\n")
      if (notes.length) {
        if (!Array.isArray(event.system)) event.system = []
        event.system.push({ type: "text", text: injectedText })
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
