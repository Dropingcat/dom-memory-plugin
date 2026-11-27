import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"
import test from "node:test"

import { stepFromBrief } from "../opencode/index.js"
import { computeFingerprint } from "../opencode/gates/tau_gate.js"

const PY = process.env.DOM_PYTHON || process.env.PYTHON_BIN || "python"
const HERE = dirname(fileURLToPath(import.meta.url))
const SCRIPT = join(HERE, "..", "opencode", "scripts", "queue_producer.py")

const BRIEF = [
  "[DOM MEMORY INDEX] plan r28 · 26 items / 26 open · 497 claims · CHAIN_OK",
  "MAIN TASK: Разработка плагина DOM (root 149d33e5)",
  "LINE:",
  '  П15 "card — verified card has this skill" [ER pending] baseline: card',
  'CURRENT: П15 "card — verified card has this skill" [ER pending] anchor ?',
  "ACTIVE: pinned by current_task=П15",
  "CURRENT MEANING: card verification means the card provides the capability",
  "WHY: the selector must trust curated provides",
  "NOW: implement the card check",
  "NEXT: 1) П1 Timeout: 100 ms [pending]",
].join("\n")

test("stepFromBrief reads the CURRENT line, its context and the plan revision", () => {
  const step = stepFromBrief(BRIEF)
  assert.equal(step.step_id, "П15")
  assert.equal(step.title, "card — verified card has this skill")
  assert.equal(step.plan_version, 28)
  assert.deepEqual(step.cap, [])
  assert.match(step.context, /card verification means/)
  assert.match(step.context, /trust curated provides/)
  assert.match(step.context, /implement the card check/)
  assert.ok(step.context.length <= 600)
})

test("stepFromBrief handles the auto CURRENT form and no-plan notes", () => {
  const auto = stepFromBrief('CURRENT (auto): П1 "Timeout: 100 ms" [pending blocked] anchor ?')
  assert.equal(auto.step_id, "П1")
  assert.equal(auto.title, "Timeout: 100 ms")
  assert.equal(auto.plan_version, undefined)
  assert.equal(auto.context, undefined)

  assert.equal(stepFromBrief("[DOM MEMORY INDEX] no active plan items; call dom_plan_status/dom_plan_ingest.\n"), null)
  assert.equal(stepFromBrief("memory started for this project (branch main)"), null)
  assert.equal(stepFromBrief(""), null)
  assert.equal(stepFromBrief(undefined), null)
})

test("producer state and the JS tau-gate hash the same sigma", () => {
  const result = spawnSync(
    PY,
    ["-B", SCRIPT, "--step", JSON.stringify({ step_id: "П2", title: "L2 V5 migration", cap: ["sqlite", "migration"] }),
      "--no-cache"],
    { encoding: "utf8", timeout: 120000, windowsHide: true },
  )
  assert.equal(result.error, undefined, String(result.error))
  assert.equal(result.status, 0, result.stderr)
  const data = JSON.parse(result.stdout)
  assert.equal(data.status, "OK", JSON.stringify(data))
  assert.equal(typeof data.block_text, "string")
  assert.ok(data.block_text.length > 0 && data.block_text.length <= 800)
  assert.equal(computeFingerprint(data.state), data.fingerprint)
  assert.equal(data.state.indexDigest, data.index_digest)
  assert.equal(data.state.stepId, data.step_id)
})
