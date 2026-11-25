import assert from "node:assert/strict"
import { existsSync, mkdirSync, mkdtempSync, readFileSync, utimesSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"

import { register } from "../opencode/index.js"
import { createObservability } from "../opencode/observability.js"

const PY = process.env.DOM_PYTHON || process.env.PYTHON_BIN || "python"
const DAY = () => new Date().toISOString().slice(0, 10)

test("prompt hook writes one journal line with session, duration and db_path", async () => {
  const dir = mkdtempSync(join(tmpdir(), "dom-obs-"))
  const handlers = {}
  const ctx = {
    options: { projectDir: dir, pythonPath: PY, planDir: join(dir, "plans"), timeoutMs: 15000 },
    location: { directory: dir },
    session: {
      hook: async (name, fn) => {
        handlers[name] = fn
        return { dispose() {} }
      },
    },
  }
  const cleanup = await register(ctx)
  assert.equal(typeof handlers.prompt, "function")
  await handlers.prompt({ prompt: { text: "hello observability" }, sessionID: "ses_1" })
  const file = join(dir, ".dom-memory", "observability", `hooks-${DAY()}-${process.pid}.jsonl`)
  assert.ok(existsSync(file), `missing ${file}`)
  const lines = readFileSync(file, "utf8").trim().split("\n").map((line) => JSON.parse(line))
  const line = lines.find((item) => item.hook === "prompt")
  assert.ok(line, "no prompt line")
  assert.equal(line.session_id, "ses_1")
  assert.equal(typeof line.duration_ms, "number")
  assert.ok(line.duration_ms >= 0)
  assert.ok(String(line.db_path).endsWith("dom.sqlite3"))
  assert.ok(["ok", "silent", "error"].includes(line.status))
  cleanup()
})

test("record never throws and increments dropped on an impossible path", () => {
  const file = join(tmpdir(), `dom-obs-file-${process.pid}`)
  writeFileSync(file, "not a directory")
  const obs = createObservability({ projectDir: file })
  obs.record({ hook: "prompt", phase: "after" })
  assert.equal(obs.dropped, 1)
})

test("retention deletes old PID files but keeps the current one", () => {
  const dir = mkdtempSync(join(tmpdir(), "dom-obs-ret-"))
  const obsDir = join(dir, ".dom-memory", "observability")
  mkdirSync(obsDir, { recursive: true })
  const old = join(obsDir, "hooks-2020-01-01-99999.jsonl")
  writeFileSync(old, "{}\n")
  const past = new Date(Date.now() - 10 * 24 * 60 * 60 * 1000)
  utimesSync(old, past, past)
  const obs = createObservability({ projectDir: dir, retentionDays: 7 })
  obs.record({ hook: "prompt" })
  assert.equal(existsSync(old), false)
  assert.ok(existsSync(join(obsDir, `hooks-${DAY()}-${process.pid}.jsonl`)))
})

