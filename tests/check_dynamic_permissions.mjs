/* Dynamic skill permissions from dom.skill-queue.

   The host has no `ctx.session.permissions.set`; the documented lever is
   `ctx.session.update({ sessionID, permissions })`, whose rules are evaluated
   after the agent's and where the last match wins.  This test drives the real
   plugin hooks and asserts:

   1. the queue becomes a session allow rule (mode "allow", the default);
   2. an unchanged queue does not repeat the update;
   3. a new queue replaces the ruleset;
   4. mode "strict" prepends `deny skill *`, so the queue is the exact list;
   5. mode "off" leaves the static profile untouched;
   6. a host without `session.update` only warns (static profile still works);
   7. a rejected update is reported and retried on the next request.
*/
import assert from "node:assert/strict"
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import test from "node:test"

import { register } from "../opencode/index.js"

const PY = process.env.DOM_PYTHON || process.env.PYTHON_BIN || "python"
const HERE = dirname(fileURLToPath(import.meta.url))

const PRODUCER = `import json, sys
args = sys.argv
step = json.loads(args[args.index("--step") + 1])
cap = [str(c).lower() for c in (step.get("cap") or [])]
if "sqlite" in cap or "migration" in cap:
    queue = ["sqlite-migration"]
elif "testing" in cap:
    queue = ["test-driven-development"]
else:
    queue = []
state = {"stepId": step.get("step_id", ""), "capSet": step.get("cap") or [], "categoryHint": "",
         "markers": [], "planVersion": int(step.get("plan_version") or 0), "indexDigest": "idx-dyn"}
print(json.dumps({"status": "OK", "step_id": step.get("step_id", ""), "plan_version": state["planVersion"],
                  "index_digest": "idx-dyn", "state": state,
                  "block_text": "step: %s\\nuse: %s" % (step.get("step_id", ""), ", ".join(queue)),
                  "queue": queue, "gaps": [], "confidence_bp": 6500}))
`

const briefFor = (cap) =>
  [
    "[DOM MEMORY INDEX] plan r7 · 1 items / 1 open · 3 claims · CHAIN_OK",
    `CURRENT: П2 "L2 V5 migration" [pending] anchor abcdef12 CAP: ${cap}`,
  ].join("\n")

function makeRoot(base) {
  const root = join(base, "plugin")
  const project = join(base, "project")
  const plans = join(project, "plans")
  for (const dir of [join(root, "hooks"), join(root, "scripts"), plans]) mkdirSync(dir, { recursive: true })
  writeFileSync(join(root, "hooks", "plan_hook.py"), "import sys\nsys.exit(0)\n", "utf8")
  // The brief is read at call time so one registration can move through steps.
  writeFileSync(
    join(root, "hooks", "plan_check_hook.py"),
    [
      "import sys",
      "from pathlib import Path",
      `sys.stdout.write(Path(r"${join(root, "hooks", "brief.txt")}").read_text(encoding="utf-8"))`,
      "",
    ].join("\n"),
    "utf8",
  )
  writeFileSync(join(root, "scripts", "queue_producer.py"), PRODUCER, "utf8")
  return { root, project, plans, brief: join(root, "hooks", "brief.txt") }
}

function makeCtx(root, project, plans, overrides = {}) {
  const handlers = {}
  const updates = []
  const ctx = {
    options: {
      pluginRoot: root,
      projectDir: project,
      planDir: plans,
      pythonPath: PY,
      timeoutMs: 30000,
      skillTimeoutMs: 30000,
    },
    location: { directory: project },
    session: {
      hook: async (name, fn) => {
        handlers[name] = fn
        return { dispose() {} }
      },
      instructions: { entry: { put: () => {} } },
      update: (input) => {
        updates.push(JSON.parse(JSON.stringify(input)))
        return Promise.resolve()
      },
    },
  }
  Object.assign(ctx.options, overrides.options || {})
  Object.assign(ctx.session, overrides.session || {})
  return { ctx, handlers, updates }
}

async function step(harness, briefPath, sessionID, cap) {
  writeFileSync(briefPath, briefFor(cap), "utf8")
  await harness.handlers.prompt({ sessionID, prompt: { text: "go" } })
  await harness.handlers.context({ sessionID, system: [] })
}

test("the skill queue becomes session permissions", async () => {
  const base = mkdtempSync(join(tmpdir(), "dom-dynamic-perm-"))
  try {
    const { root, project, plans, brief } = makeRoot(base)
    const harness = makeCtx(root, project, plans)
    const cleanup = await register(harness.ctx)
    const warnings = []
    const originalWarn = console.warn
    console.warn = (...args) => warnings.push(args.join(" "))
    try {
      const sessionID = `dynamic-perm-${process.pid}-${Date.now()}`

      // 1. Default mode: the queue is appended to the agent's allow-list.
      await step(harness, brief, sessionID, "sqlite,migration")
      assert.equal(harness.updates.length, 1, JSON.stringify(harness.updates))
      assert.equal(harness.updates[0].sessionID, sessionID)
      assert.deepEqual(harness.updates[0].permissions, [
        { action: "skill", resource: "sqlite-migration", effect: "allow" },
      ])
      assert.ok(
        warnings.some((line) => /permissions: session allow-list = sqlite-migration/.test(line)),
        warnings.join("\n"),
      )

      // 2. The same queue is not re-applied on every request.
      await step(harness, brief, sessionID, "sqlite,migration")
      assert.equal(harness.updates.length, 1, "unchanged queue must not repeat the update")

      // 3. A new queue replaces the session ruleset (the host replaces, not merges).
      await step(harness, brief, sessionID, "testing")
      assert.equal(harness.updates.length, 2, JSON.stringify(harness.updates))
      assert.deepEqual(harness.updates[1].permissions, [
        { action: "skill", resource: "test-driven-development", effect: "allow" },
      ])

      // 4. Strict mode makes the queue the exact allow-list.
      harness.ctx.options.dynamicPermissions = "strict"
      await step(harness, brief, sessionID, "testing")
      assert.equal(harness.updates.length, 3, JSON.stringify(harness.updates))
      assert.deepEqual(harness.updates[2].permissions, [
        { action: "skill", resource: "*", effect: "deny" },
        { action: "skill", resource: "test-driven-development", effect: "allow" },
      ])

      // 5. Off leaves the static agent profile untouched.
      harness.ctx.options.dynamicPermissions = "off"
      await step(harness, brief, sessionID, "sqlite,migration")
      assert.equal(harness.updates.length, 3, "off must not touch permissions")
    } finally {
      console.warn = originalWarn
      cleanup()
    }
  } finally {
    rmSync(base, { recursive: true, force: true })
  }
})

test("a host without session.update only warns", async () => {
  const base = mkdtempSync(join(tmpdir(), "dom-dynamic-perm-legacy-"))
  try {
    const { root, project, plans, brief } = makeRoot(base)
    const harness = makeCtx(root, project, plans, { session: { update: undefined } })
    const cleanup = await register(harness.ctx)
    const warnings = []
    const originalWarn = console.warn
    console.warn = (...args) => warnings.push(args.join(" "))
    try {
      const sessionID = `dynamic-perm-legacy-${process.pid}-${Date.now()}`
      await step(harness, brief, sessionID, "sqlite,migration")
      assert.equal(harness.updates.length, 0)
      assert.ok(
        warnings.some((line) => /ctx\.session\.update unavailable — static allow-list only/.test(line)),
        warnings.join("\n"),
      )
    } finally {
      console.warn = originalWarn
      cleanup()
    }
  } finally {
    rmSync(base, { recursive: true, force: true })
  }
})

test("a rejected update is reported and retried", async () => {
  const base = mkdtempSync(join(tmpdir(), "dom-dynamic-perm-reject-"))
  try {
    const { root, project, plans, brief } = makeRoot(base)
    const harness = makeCtx(root, project, plans)
    const cleanup = await register(harness.ctx)
    const warnings = []
    const originalWarn = console.warn
    console.warn = (...args) => warnings.push(args.join(" "))
    try {
      const sessionID = `dynamic-perm-reject-${process.pid}-${Date.now()}`
      let calls = 0
      harness.ctx.session.update = () => {
        calls += 1
        return calls === 1 ? Promise.reject(new Error("boom")) : Promise.resolve()
      }
      await step(harness, brief, sessionID, "sqlite,migration")
      await new Promise((resolve) => setImmediate(resolve))
      assert.ok(
        warnings.some((line) => /permissions: update failed: boom/.test(line)),
        warnings.join("\n"),
      )
      // The failed signature is dropped, so the next request attempts it again.
      await step(harness, brief, sessionID, "sqlite,migration")
      assert.equal(calls, 2, "a rejected update must be retried")
    } finally {
      console.warn = originalWarn
      cleanup()
    }
  } finally {
    rmSync(base, { recursive: true, force: true })
  }
})
