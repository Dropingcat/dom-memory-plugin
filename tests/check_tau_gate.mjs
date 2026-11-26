import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"

import { register } from "../opencode/index.js"
import {
  checkReemit,
  computeFingerprint,
  FingerprintState,
  forgetSession,
  peekState,
  resetIdle,
} from "../opencode/gates/tau_gate.js"

const PY = process.env.DOM_PYTHON || process.env.PYTHON_BIN || "python"

const STATE = {
  stepId: "pt-2",
  capSet: ["testing", "sqlite"],
  categoryHint: "databases",
  markers: ["modify"],
  planVersion: 3,
  indexDigest: "idx-a",
}

let sequence = 0
const freshSession = () => `tau-test-${process.pid}-${sequence++}`

test("computeFingerprint is deterministic and order-independent", () => {
  const left = computeFingerprint(STATE)
  const right = computeFingerprint({
    stepId: "pt-2",
    capSet: ["sqlite", "testing", "sqlite"], // permuted + duplicated
    categoryHint: "databases",
    markers: ["modify"],
    planVersion: 3,
    indexDigest: "idx-a",
  })
  assert.equal(left, right)
  assert.equal(left, "75e3043b31cb45481a9563aee6104de61c0dd47aa4194e026a5dbf4d38007dc2")
  assert.match(left, /^[a-f0-9]{64}$/)

  // snake_case keys (the Python payload shape) produce the same digest.
  assert.equal(
    computeFingerprint({
      step_id: "pt-2",
      cap_set: ["testing", "sqlite"],
      category_hint: "databases",
      markers: ["modify"],
      plan_version: 3,
      index_digest: "idx-a",
    }),
    left,
  )

  // The canonical payload is pinned, so JS and Python cannot drift apart silently.
  const canonical = JSON.stringify({
    cap_set: ["sqlite", "testing"],
    category_hint: "databases",
    index_digest: "idx-a",
    markers: ["modify"],
    plan_version: 3,
    step_id: "pt-2",
  })
  assert.equal(left, createHash("sha256").update(canonical, "utf8").digest("hex"))

  // Any material field changes the fingerprint.
  for (const patch of [
    { stepId: "pt-3" },
    { capSet: ["testing"] },
    { categoryHint: "coding" },
    { markers: ["verify"] },
    { planVersion: 4 },
    { indexDigest: "idx-b" },
  ]) {
    assert.notEqual(computeFingerprint({ ...STATE, ...patch }), left, JSON.stringify(patch))
  }
})

test("idle re-issue increments the counter and returns false", () => {
  const session = freshSession()
  const fingerprint = computeFingerprint(STATE)

  const first = checkReemit(session, fingerprint)
  assert.deepEqual(first, { shouldReemit: true, isIdle: false, idleCount: 0 })

  const second = checkReemit(session, fingerprint)
  assert.deepEqual(second, { shouldReemit: false, isIdle: true, idleCount: 1 })

  const third = checkReemit(session, fingerprint)
  assert.deepEqual(third, { shouldReemit: false, isIdle: true, idleCount: 2 })
  assert.deepEqual(peekState(session), { previous: fingerprint, idleCount: 2 })
  forgetSession(session)
})

test("changed fingerprint resets the counter and re-emits", () => {
  const session = freshSession()
  const before = computeFingerprint(STATE)
  const after = computeFingerprint({ ...STATE, planVersion: 4 })

  checkReemit(session, before)
  checkReemit(session, before)
  checkReemit(session, before)
  assert.equal(peekState(session).idleCount, 2)

  const changed = checkReemit(session, after)
  assert.deepEqual(changed, { shouldReemit: true, isIdle: false, idleCount: 0 })
  // The next unchanged turn counts from 1 again, and is still recognized as idle.
  assert.deepEqual(checkReemit(session, after), { shouldReemit: false, isIdle: true, idleCount: 1 })
  forgetSession(session)
})

test("no hard cap on idle streak and never a false negative on change", () => {
  const session = freshSession()
  const fingerprint = computeFingerprint(STATE)
  checkReemit(session, fingerprint)
  for (let i = 0; i < 1000; i++) {
    const decision = checkReemit(session, fingerprint)
    assert.equal(decision.shouldReemit, false)
  }
  assert.equal(peekState(session).idleCount, 1000)

  // A real change still gets through after the long idle streak (H1 fix).
  const changed = computeFingerprint({ ...STATE, indexDigest: "idx-b" })
  assert.equal(checkReemit(session, changed).shouldReemit, true)
  assert.equal(peekState(session).idleCount, 0)
  forgetSession(session)
})

test("resetIdle clears the counter without faking a first emission", () => {
  const session = freshSession()
  const fingerprint = computeFingerprint(STATE)
  checkReemit(session, fingerprint)
  checkReemit(session, fingerprint)
  checkReemit(session, fingerprint)
  assert.equal(peekState(session).idleCount, 2)
  resetIdle(session)
  assert.equal(peekState(session).idleCount, 0)
  // Still the same block: idle again (count 1), not "first emission".
  assert.deepEqual(checkReemit(session, fingerprint), {
    shouldReemit: false, isIdle: true, idleCount: 1,
  })
  forgetSession(session)
})

test("sessions keep independent state", () => {
  const a = freshSession()
  const b = freshSession()
  const fingerprint = computeFingerprint(STATE)
  checkReemit(a, fingerprint)
  assert.equal(checkReemit(b, fingerprint).shouldReemit, true)
  assert.equal(checkReemit(a, fingerprint).shouldReemit, false)
  assert.equal(peekState(b).idleCount, 0)
  forgetSession(a)
  forgetSession(b)
})

test("FingerprintState carries the documented shape", () => {
  const state = new FingerprintState()
  assert.equal(state.previous, null)
  assert.equal(state.idleCount, 0)
})

test("gate latency stays under 1ms per turn", () => {
  const session = freshSession()
  const rounds = 2000
  const fingerprint = computeFingerprint(STATE)
  const started = performance.now()
  for (let i = 0; i < rounds; i++) {
    checkReemit(session, computeFingerprint(STATE))
  }
  const perTurn = (performance.now() - started) / rounds
  assert.ok(perTurn < 1, `per-turn gate cost ${perTurn.toFixed(4)}ms`)
  // The digest itself is the expensive part; report it through a second probe.
  const digestStart = performance.now()
  for (let i = 0; i < rounds; i++) computeFingerprint(STATE)
  assert.ok((performance.now() - digestStart) / rounds < 1)
  assert.equal(peekState(session).previous, fingerprint)
  forgetSession(session)
})

test("prompt hook emits once, then logs the idle count instead of re-emitting", async () => {
  const dir = mkdtempSync(join(tmpdir(), "dom-tau-"))
  const handlers = {}
  const put = []
  const ctx = {
    options: { projectDir: dir, pythonPath: PY, planDir: join(dir, "plans"), timeoutMs: 30000 },
    location: { directory: dir },
    session: {
      hook: async (name, fn) => {
        handlers[name] = fn
        return { dispose() {} }
      },
      instructions: { entry: { put: (key, value) => put.push([key, value]) } },
    },
  }
  const cleanup = await register(ctx)
  const sessionID = "ses_tau_gate"
  const block = {
    text: "use: test-driven-development@e5f6a7b8 (testing)",
    stepId: "pt-2",
    capSet: ["testing", "sqlite"],
    categoryHint: "databases",
    markers: ["modify"],
    planVersion: 3,
    indexDigest: "idx-a",
  }

  const logged = []
  const original = console.log
  console.log = (...args) => logged.push(args.join(" "))
  try {
    await handlers.prompt({ sessionID, prompt: { text: "go" }, skillBlock: block })
    assert.equal(put.length, 1)
    assert.deepEqual(put[0], ["dom.skill-queue", block.text])

    // Same block again: not re-emitted, idle count logged to console.
    await handlers.prompt({ sessionID, prompt: { text: "continue" }, skillBlock: { ...block } })
    assert.equal(put.length, 1)
    assert.ok(logged.some((line) => /\[τ-gate\] idle \(1\), block not re-emitted/.test(line)), logged.join("\n"))

    // Third identical turn: the idle counter grows.
    await handlers.prompt({ sessionID, prompt: { text: "again" }, skillBlock: { ...block } })
    assert.equal(put.length, 1)
    assert.ok(logged.some((line) => /\[τ-gate\] idle \(2\)/.test(line)), logged.join("\n"))

    // A changed block is emitted again and resets the counter.
    await handlers.prompt({
      sessionID,
      prompt: { text: "next step" },
      skillBlock: { ...block, text: "use: writing-plans@90ab12cd (planning)", planVersion: 4 },
    })
    assert.equal(put.length, 2)
    assert.equal(put[1][1], "use: writing-plans@90ab12cd (planning)")
  } finally {
    console.log = original
    cleanup()
    forgetSession(sessionID)
  }
})
