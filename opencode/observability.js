/**
 * Dependency-free observability for the DOM OpenCode plugin.
 *
 * - `record`: one JSONL file per process per UTC day, so two OpenCode
 *   processes cannot interleave a partial line on Windows.
 * - `snapshot`: the assembled model input at the DOM `context` hook, before
 *   provider lowering. It is not the raw provider request body.
 *
 * `record` and `snapshot` never throw: an I/O failure increments `dropped`,
 * and the host request continues.
 */
import { appendFileSync, mkdirSync, readdirSync, statSync, unlinkSync, writeFileSync } from "node:fs"
import { createHash } from "node:crypto"
import { join } from "node:path"

const DAY_MS = 24 * 60 * 60 * 1000
const MAX_LINE_BYTES = 64 * 1024
const MAX_SYSTEM_CHARS = 200_000
const MAX_MESSAGE_CHARS = 200_000
const MAX_MESSAGES = 50
const MAX_TOOLS = 200
const MAX_OPTIONS_CHARS = 20_000
const MAX_NOTE_CHARS = 4_000
const MAX_NOTES = 50
const SECRET_KEY = /authorization|token|secret|apiKey|password/i
const SECRET_TEXT = [
  /sk-[A-Za-z0-9_-]{8,}/g,
  /ghp_[A-Za-z0-9]{20,}/g,
  /Bearer\s+[A-Za-z0-9._~+/=-]+/gi,
  /(password|api[_-]?key|token)\s*[=:]\s*\S+/gi,
  /AKIA[0-9A-Z]{16}/g,
]

export function sha256(value) {
  return createHash("sha256").update(String(value ?? "")).digest("hex")
}

export function preview(value, max = 200) {
  const text = String(value ?? "")
  return text.length > max ? `${text.slice(0, max)}…` : text
}

export function canonicalJson(value) {
  const canonical = (item) => {
    if (Array.isArray(item)) return item.map(canonical)
    if (item && typeof item === "object") {
      const out = {}
      for (const key of Object.keys(item).sort()) out[key] = canonical(item[key])
      return out
    }
    return item
  }
  return JSON.stringify(canonical(value))
}

function scrubText(value) {
  let out = String(value ?? "")
  for (const pattern of SECRET_TEXT) out = out.replace(pattern, "[REDACTED]")
  return out
}

function deepRedact(value, full = false) {
  if (Array.isArray(value)) return value.map((item) => deepRedact(item, full))
  if (value && typeof value === "object") {
    const out = {}
    for (const [key, item] of Object.entries(value)) {
      out[key] = SECRET_KEY.test(key) ? "[REDACTED]" : deepRedact(item, full)
    }
    return out
  }
  if (typeof value === "string") return full ? value : scrubText(value)
  return value
}

function sanitizeSegment(value) {
  const text = String(value || "unknown").replace(/[^A-Za-z0-9._-]/g, "_")
  return text.slice(0, 64) || "unknown"
}

function projectContent(content, full) {
  if (typeof content === "string") return [{ type: "text", text: deepRedact(content, full) }]
  if (!Array.isArray(content)) return []
  return content.map((part) => {
    if (!part || typeof part !== "object") return { type: "unknown" }
    if (part.type === "text" && typeof part.text === "string") {
      return { type: "text", text: deepRedact(part.text, full) }
    }
    const out = { type: String(part.type || "unknown") }
    for (const key of ["mimeType", "filename", "name"]) {
      if (typeof part[key] === "string") out[key] = part[key]
    }
    return out
  })
}

function projectMessages(messages, full) {
  if (!Array.isArray(messages)) return []
  return messages.map((message) => {
    const out = { role: String(message?.role || "unknown"), content: projectContent(message?.content, full) }
    if (message?.metadata && typeof message.metadata === "object") out.metadata = deepRedact(message.metadata, full)
    return out
  })
}

function projectTools(tools, full) {
  const out = {}
  const entries = Array.isArray(tools)
    ? tools.map((tool) => [tool?.name || tool?.id || "unknown", tool])
    : Object.entries(tools || {})
  for (const [name, tool] of entries.slice(0, MAX_TOOLS)) {
    if (!tool || typeof tool !== "object") {
      out[name] = { description: "" }
      continue
    }
    out[name] = {
      description: preview(deepRedact(tool.description || "", full), 500),
      input_schema: deepRedact(tool.inputSchema || tool.input || tool.parameters || null, full),
    }
  }
  return out
}

function projectNotes(notes, full) {
  const list = Array.isArray(notes) ? notes : []
  return list.slice(0, MAX_NOTES).map((note) => {
    if (typeof note === "string") return { text: deepRedact(note, full), source: "dom", claim_ids: [] }
    return {
      text: deepRedact(note?.text || "", full),
      source: String(note?.source || "dom"),
      claim_ids: Array.isArray(note?.claim_ids) ? note.claim_ids.map(String) : [],
    }
  })
}

function countChars(value) {
  try {
    return JSON.stringify(value).length
  } catch {
    return 0
  }
}

export function createObservability({
  projectDir,
  pid = process.pid,
  retentionDays = 7,
  maxPreview = 200,
} = {}) {
  const dir = join(projectDir, ".dom-memory", "observability")
  const promptDir = join(dir, "prompts")
  let dropped = 0

  const day = () => new Date().toISOString().slice(0, 10)
  const fileFor = (value) => join(dir, `hooks-${value}-${pid}.jsonl`)

  const cleanup = () => {
    const cutoff = Date.now() - retentionDays * DAY_MS
    const current = fileFor(day())
    let names
    try {
      names = readdirSync(dir)
    } catch {
      return
    }
    for (const name of names) {
      if (!/^hooks-\d{4}-\d{2}-\d{2}-\d+\.jsonl$/.test(name)) continue
      const path = join(dir, name)
      if (path === current) continue
      try {
        if (statSync(path).mtimeMs < cutoff) unlinkSync(path)
      } catch {
        /* a file removed or locked concurrently is not a reason to fail */
      }
    }
  }

  const record = (event = {}) => {
    try {
      mkdirSync(dir, { recursive: true })
      const base = { ts: new Date().toISOString(), pid, ...deepRedact(event, false) }
      let line = JSON.stringify(base)
      if (Buffer.byteLength(line) > MAX_LINE_BYTES) {
        if (base.injected && typeof base.injected === "object") {
          base.injected = { ...base.injected, preview: preview(base.injected.preview, maxPreview), truncated: true }
        }
        line = JSON.stringify(base)
      }
      if (Buffer.byteLength(line) > MAX_LINE_BYTES) {
        line = JSON.stringify({ ts: base.ts, pid, hook: base.hook, phase: base.phase, status: "LINE_TOO_LARGE", dropped: 1 })
      }
      appendFileSync(fileFor(day()), `${line}\n`, "utf8")
      cleanup()
    } catch {
      dropped += 1
    }
  }

  const truncateSnapshot = (full) => {
    let truncated = false
    const system = []
    let systemBudget = MAX_SYSTEM_CHARS
    for (const block of full.system) {
      const blockText = typeof block?.text === "string" ? block.text : ""
      if (blockText.length > systemBudget) {
        system.push({ ...block, text: `${blockText.slice(0, Math.max(0, systemBudget))}…` })
        truncated = true
        systemBudget = 0
        break
      }
      system.push(block)
      systemBudget -= blockText.length
    }

    const messages = []
    let messageBudget = MAX_MESSAGE_CHARS
    for (let index = full.messages.length - 1; index >= 0 && messages.length < MAX_MESSAGES; index -= 1) {
      const message = full.messages[index]
      const size = countChars(message)
      if (size > messageBudget) {
        if (messages.length === 0) {
          messages.unshift({ ...message, truncated: true })
          truncated = true
        }
        break
      }
      messages.unshift(message)
      messageBudget -= size
    }

    const notes = full.dom.notes.map((note) => {
      if (note.text.length > MAX_NOTE_CHARS) {
        truncated = true
        return { ...note, text: `${note.text.slice(0, MAX_NOTE_CHARS)}…`, truncated: true }
      }
      return note
    })

    const optionsText = JSON.stringify(full.options)
    const options = optionsText.length > MAX_OPTIONS_CHARS
      ? { truncated: true, preview: preview(optionsText, MAX_OPTIONS_CHARS) }
      : full.options
    if (optionsText.length > MAX_OPTIONS_CHARS) truncated = true

    const stored = {
      ...full,
      system,
      messages,
      options,
      dom: { notes },
      counts: {
        ...full.counts,
        system_blocks: system.length,
        messages: messages.length,
        chars: countChars({ system, messages, notes }),
      },
      truncated,
    }
    return stored
  }

  const cleanupPrompts = () => {
    const cutoff = Date.now() - retentionDays * DAY_MS
    const walk = (path) => {
      let names
      try {
        names = readdirSync(path, { withFileTypes: true })
      } catch {
        return
      }
      for (const entry of names) {
        const child = join(path, entry.name)
        if (entry.isDirectory()) {
          walk(child)
          continue
        }
        try {
          if (statSync(child).mtimeMs < cutoff) unlinkSync(child)
        } catch {
          /* locked or removed concurrently */
        }
      }
    }
    walk(promptDir)
  }

  const snapshot = (event = {}, meta = {}) => {
    try {
      const full = Boolean(process.env.DOM_SNAPSHOT_FULL === "1")
      const session = String(event.sessionID || event.session?.id || "unknown")
      const notes = projectNotes(meta.notes, full)
      const body = {
        schema: "dom.prompt-snapshot/2",
        ts: new Date().toISOString(),
        session_id: session,
        agent: event.agent ?? null,
        model: deepRedact(event.model ?? null, full),
        kind: event.kind ?? null,
        source: String(meta.source || "dom.context"),
        system: Array.isArray(event.system) ? event.system.map((block) => deepRedact(block, full)) : [],
        messages: projectMessages(event.messages, full),
        tools: projectTools(event.tools, full),
        options: deepRedact(event.options ?? {}, full),
        dom: { notes },
        hook_order: "unknown",
      }
      body.counts = {
        system_blocks: body.system.length,
        messages: body.messages.length,
        tools: Object.keys(body.tools).length,
        chars: countChars({ system: body.system, messages: body.messages, notes }),
      }
      const sha256Full = sha256(canonicalJson(body))
      const stored = truncateSnapshot(body)
      stored.sha256_full = sha256Full

      const stamp = body.ts.replace(/[:.]/g, "-")
      const base = `${stamp}-${sha256Full.slice(0, 8)}.json`
      const path = join(promptDir, sanitizeSegment(session), base)
      mkdirSync(join(promptDir, sanitizeSegment(session)), { recursive: true })
      writeFileSync(path, JSON.stringify(stored, null, 2), { encoding: "utf8", mode: 0o600 })
      cleanupPrompts()
      return { id: `${sanitizeSegment(session)}/${base}`, path, sha256_full: sha256Full, truncated: stored.truncated }
    } catch {
      dropped += 1
      return null
    }
  }

  const close = () => {
    cleanup()
    return { dropped }
  }

  return {
    record,
    snapshot,
    close,
    dir,
    get dropped() {
      return dropped
    },
  }
}
