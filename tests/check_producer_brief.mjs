/* Contract 1.3: the CAP envelope travels plan -> brief -> producer.
   1. stepFromBrief reads `CAP: a,b` off the CURRENT line into step.cap.
   2. A CURRENT line without CAP keeps cap=[] (the producer reports not-declared).
   3. The parsed step, fed to queue_producer.py, selects the sqlite-migration card. */
import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"
import test from "node:test"

import { stepFromBrief } from "../opencode/index.js"

const PY = process.env.DOM_PYTHON || process.env.PYTHON_BIN || "python"
const HERE = dirname(fileURLToPath(import.meta.url))
const SCRIPT = join(HERE, "..", "opencode", "scripts", "queue_producer.py")

test("stepFromBrief reads CAP from the CURRENT line", () => {
  const step = stepFromBrief('CURRENT: П15.2 "L2 V5 migration" [AO200 pending] CAP: sqlite,migration')
  assert.equal(step.step_id, "П15.2")
  assert.equal(step.title, "L2 V5 migration")
  assert.deepEqual(step.cap, ["sqlite", "migration"])
})

test("stepFromBrief keeps cap empty when the brief declares no CAP", () => {
  const step = stepFromBrief('CURRENT: П15 "card — verified card has this skill" [ER pending] anchor 1a2b3c4d')
  assert.deepEqual(step.cap, [])
})

test("the parsed CAP reaches the producer and selects the matching skill", () => {
  const step = stepFromBrief('CURRENT: П15.2 "L2 V5 migration" [AO200 pending] CAP: sqlite,migration')
  const result = spawnSync(PY, ["-B", SCRIPT, "--step", JSON.stringify(step), "--no-cache"], {
    encoding: "utf8",
    timeout: 120000,
    windowsHide: true,
  })
  assert.equal(result.error, undefined, String(result.error))
  assert.equal(result.status, 0, result.stderr)
  const data = JSON.parse(result.stdout)
  assert.equal(data.status, "OK", JSON.stringify(data))
  assert.deepEqual(data.state.capSet, ["migration", "sqlite"])
  assert.ok(data.queue.includes("sqlite-migration"), `queue: ${JSON.stringify(data.queue)}`)
})
