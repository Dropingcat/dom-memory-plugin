/**
 * Dependency-free observability for the DOM OpenCode plugin.
 *
 * One JSONL file per process per UTC day, so two OpenCode processes cannot
 * interleave a partial line on Windows.  `record` never throws: an I/O failure
 * increments `dropped`, and the host request continues.
 */
import { appendFileSync, mkdirSync, readdirSync, statSync, unlinkSync } from "node:fs"
import { createHash } from "node:crypto"
import { join } from "node:path"

const DAY_MS = 24 * 60 * 60 * 1000
const MAX_LINE_BYTES = 64 * 1024
const SECRET_KEY = /authorization|token|secret|apiKey|password/i

export function sha256(value) {
  return createHash("sha256").update(String(value ?? "")).digest("hex")
}

export function preview(value, max = 200) {
  const text = String(value ?? "")
  return text.length > max ? `${text.slice(0, max)}…` : text
}

function redact(value) {
  if (Array.isArray(value)) return value.map(redact)
  if (value && typeof value === "object") {
    const out = {}
    for (const [key, item] of Object.entries(value)) {
      out[key] = SECRET_KEY.test(key) ? "[REDACTED]" : redact(item)
    }
    return out
  }
  return value
}

export function createObservability({
  projectDir,
  pid = process.pid,
  retentionDays = 7,
  maxPreview = 200,
} = {}) {
  const dir = join(projectDir, ".dom-memory", "observability")
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
      const base = { ts: new Date().toISOString(), pid, ...redact(event) }
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

  const close = () => {
    cleanup()
    return { dropped }
  }

  return {
    record,
    close,
    dir,
    get dropped() {
      return dropped
    },
  }
}
