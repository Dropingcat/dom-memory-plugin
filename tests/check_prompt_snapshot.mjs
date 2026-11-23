import assert from "node:assert/strict"
import { mkdtempSync, readFileSync, readdirSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"

import { register } from "../opencode/index.js"
import { canonicalJson, createObservability, sha256 } from "../opencode/observability.js"

const PY = "E:\\opencode_harness_portable\\.venv\\Scripts\\python.exe"

test("snapshot stores blocks, redacts secrets, and hashes the full semantic input", () => {
  const dir = mkdtempSync(join(tmpdir(), "dom-snap-"))
  const obs = createObservability({ projectDir: dir })
  const event = {
    sessionID: "s1",
    agent: "build",
    kind: "primary",
    model: { providerID: "p", id: "m" },
    system: [
      { type: "text", text: "base token sk-abc12345" },
      {
        type: "text",
        text: 'tools["dom-memory"].dom_find and tools["dom-memory"].dom_claim',
        metadata: { experimental: { instructions: [{ key: "core/codemode", text: "catalog" }] } },
      },
    ],
    messages: [{ role: "user", content: [{ type: "text", text: "token sk-abc12345 and task" }] }],
    tools: { read: { description: "read", inputSchema: { type: "object" }, execute: () => {} } },
    options: { temperature: 0.2, apiKey: "secret", maxTokens: 1000 },
  }
  const result = obs.snapshot(event, { source: "dom.context", notes: ["[DOM MEMORY INDEX] plan r16"] })
  assert.ok(result && result.path)
  const snap = JSON.parse(readFileSync(result.path, "utf8"))
  assert.equal(snap.schema, "dom.prompt-snapshot/2")
  assert.equal(snap.dom.notes.length, 1)
  assert.equal(snap.options.apiKey, "[REDACTED]")
  assert.equal(snap.options.maxTokens, 1000)
  assert.equal(snap.system_blocks[1].origin, "core/codemode")
  assert.equal(snap.origin_summary["core/codemode"], 1)
  assert.ok(snap.dom_tools.includes("dom_find"), snap.dom_tools)
  assert.ok(snap.dom_tools.includes("dom_claim"), snap.dom_tools)
  assert.doesNotMatch(JSON.stringify(snap.messages), /sk-abc12345/)
  assert.doesNotMatch(JSON.stringify(snap.system), /sk-abc12345/)
  assert.equal(snap.tools.read.input_schema.type, "object")
  assert.equal(snap.tools.read.execute, undefined)
  assert.equal(snap.truncated, false)
  const copy = JSON.parse(JSON.stringify(snap))
  delete copy.sha256_full
  delete copy.truncated
  assert.equal(snap.sha256_full, sha256(canonicalJson(copy)))
})

test("snapshot marks truncation and keeps the full-text hash", () => {
  const dir = mkdtempSync(join(tmpdir(), "dom-snap-big-"))
  const obs = createObservability({ projectDir: dir })
  const big = "x".repeat(300_000)
  const event = { sessionID: "s2", system: [{ type: "text", text: big }], messages: [], tools: {}, options: {} }
  const result = obs.snapshot(event, { source: "dom.context", notes: ["note"] })
  const snap = JSON.parse(readFileSync(result.path, "utf8"))
  assert.equal(snap.truncated, true)
  assert.equal(snap.sha256_full.length, 64)
  const copy = JSON.parse(JSON.stringify(snap))
  delete copy.sha256_full
  delete copy.truncated
  assert.notEqual(snap.sha256_full, sha256(canonicalJson(copy)))
})

test("context hook writes a snapshot through the registered handler", async () => {
  const dir = mkdtempSync(join(tmpdir(), "dom-snap-hook-"))
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
  await handlers.prompt({ prompt: { text: "snapshot task" }, sessionID: "ses_snap" })
  const event = {
    sessionID: "ses_snap",
    agent: "build",
    system: [{ type: "text", text: "base" }],
    messages: [{ role: "user", content: [{ type: "text", text: "snapshot task" }] }],
    tools: {},
    options: {},
  }
  await handlers.context(event)
  await handlers.context({ ...event, system: [{ type: "text", text: "base2" }] })
  const root = join(dir, ".dom-memory", "observability", "prompts", "ses_snap")
  const files = readdirSync(root).filter((name) => name.endsWith(".json")).sort()
  assert.ok(files.length >= 2, files)
  for (const name of files) {
    const snap = JSON.parse(readFileSync(join(root, name), "utf8"))
    assert.equal(snap.schema, "dom.prompt-snapshot/2")
    assert.equal(snap.session_id, "ses_snap")
    assert.ok(snap.counts.system_blocks >= 1)
    assert.ok(snap.dom.notes.length >= 1, `expected the DOM brief in ${name}`)
  }
  cleanup()
})
