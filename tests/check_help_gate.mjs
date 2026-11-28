import assert from "node:assert/strict"
import { mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"

import { applyHelpGate, helpFor, HELP_DATA } from "../opencode/gates/help_gate.js"
import { register } from "../opencode/index.js"

const PY = process.env.DOM_PYTHON || process.env.PYTHON_BIN || "python"
const DOM_TOOLS = Object.keys(HELP_DATA).filter((key) => key.startsWith("dom_"))

/** Run `fn` with console.warn captured (the gate warns its injection). */
function captured(fn) {
  const lines = []
  const originalLog = console.log
  const originalWarn = console.warn
  console.log = (...args) => lines.push(args.join(" "))
  console.warn = (...args) => lines.push(args.join(" "))
  try {
    return { value: fn(), lines }
  } finally {
    console.log = originalLog
    console.warn = originalWarn
  }
}

test("helpFor returns the actionable text for a known DOM tool", () => {
  const help = helpFor("dom_claim")
  assert.equal(typeof help, "string")
  assert.ok(help.length > 0)
  assert.match(help, /dom_describe/)
  assert.match(help, /Тебе НУЖНО/)
})

test("helpFor falls back to the default for an unknown tool", () => {
  const help = helpFor("unknown_tool")
  assert.equal(help, helpFor("_default"))
  assert.match(help, /dom_help/)
  assert.notEqual(help, helpFor("dom_claim"))
})

test("the embedded subset is a small dom_* map with bounded help text", () => {
  assert.ok(DOM_TOOLS.length >= 8, `only ${DOM_TOOLS.length} tools embedded`)
  for (const key of DOM_TOOLS) {
    assert.ok(key.startsWith("dom_"), key)
    const help = helpFor(key)
    assert.ok(help.trim().length > 0, key)
    assert.ok(help.length <= 500, `${key}: help is ${help.length} chars`)
  }
  // The fallback must not collide with a real tool name.
  assert.equal(DOM_TOOLS.includes("_default"), false)
})

test("an error event yields {tool, help} and logs the injection once", () => {
  const { value, lines } = captured(() =>
    applyHelpGate(
      { tool: "dom_update", error: new Error("CONFLICT_READ_REQUIRED") },
      undefined,
      { enabled: true },
    ),
  )
  assert.equal(value.tool, "dom_update")
  assert.equal(value.help, helpFor("dom_update"))
  assert.equal(lines.length, 1)
  assert.match(lines[0], /\[dom-memory hooks\] error help: dom_update failed -> injected/)

  // BUG-2: the host reports the server-prefixed MCP name.
  const prefixed = captured(() =>
    applyHelpGate(
      { tool: "dom-memory_dom_claim", error: new Error("INVALID_FIELD") },
      undefined,
      { enabled: true },
    ),
  )
  assert.equal(prefixed.value.tool, "dom_claim")
  assert.equal(prefixed.value.help, helpFor("dom_claim"))
})

test("an event without an error yields nothing", () => {
  assert.equal(
    captured(() => applyHelpGate({ tool: "dom_claim", status: "ok" }, undefined, { enabled: true }))
      .value,
    undefined,
  )
  assert.equal(
    captured(() => applyHelpGate({ tool: "dom_claim" }, undefined, { enabled: true })).value,
    undefined,
  )
  // The gate itself is off by default, even for a failed DOM call.
  assert.equal(captured(() => applyHelpGate({ tool: "dom_claim", error: true })).value, undefined)
})

test("a non-DOM tool is ignored even when it fails", () => {
  assert.equal(
    captured(() => applyHelpGate({ tool: "bash", error: true }, undefined, { enabled: true }))
      .value,
    undefined,
  )
  assert.equal(
    captured(() => applyHelpGate({ tool: "dom", error: true }, undefined, { enabled: true })).value,
    undefined,
  )
})

test("error may arrive as a status or in the host's output argument", () => {
  assert.equal(
    captured(() =>
      applyHelpGate({ tool: "dom_find", status: "error" }, undefined, { enabled: true }),
    ).value.tool,
    "dom_find",
  )
  assert.equal(
    captured(() =>
      applyHelpGate({ tool: "dom_find" }, { isError: true }, { enabled: true }),
    ).value.tool,
    "dom_find",
  )
  assert.equal(
    captured(() =>
      applyHelpGate({ tool: "dom_find" }, { output: "boom" }, { enabled: true }),
    ).value,
    undefined,
  )
})

test("register wires execute.after into the dom-help instruction entry", async () => {
  const dir = mkdtempSync(join(tmpdir(), "dom-help-"))
  const handlers = {}
  const put = []
  const ctx = {
    options: {
      projectDir: dir,
      pythonPath: PY,
      planDir: join(dir, "plans"),
      timeoutMs: 5000,
      helpGate: true,
    },
    location: { directory: dir },
    session: {
      hook: async (name, fn) => {
        handlers[name] = fn
        return { dispose() {} }
      },
      instructions: { entry: { put: (key, value) => put.push([key, value]) } },
    },
    tool: {
      hook: async (name, fn) => {
        handlers[name] = fn
        return { dispose() {} }
      },
    },
  }
  const cleanup = await register(ctx)
  try {
    assert.equal(typeof handlers["execute.after"], "function")
    assert.equal(typeof handlers["execute.before"], "function")

    // BUG-2: OpenCode reports the server-prefixed MCP name on the hook event.
    await handlers["execute.after"]({ tool: "dom-memory_dom_claim", error: new Error("INVALID_FIELD") })
    assert.equal(put.length, 1)
    assert.equal(put[0][0], "dom-help")
    assert.match(put[0][1], /^⚠️ dom_claim failed\. /)
    assert.match(put[0][1], /dom_describe/)

    // Success and non-DOM calls do not touch the entry.
    await handlers["execute.after"]({ tool: "dom_claim", status: "ok" })
    await handlers["execute.after"]({ tool: "bash", error: true })
    assert.equal(put.length, 1)
  } finally {
    cleanup()
  }
})
