/**
 * τ-gate: fingerprint-based skill-block gating for the OpenCode plugin.
 *
 * Prevents redundant re-emission when the skill block has not changed.
 *
 * Fingerprint = sha256(step_id + cap_set + category_hint + markers + plan_version + index_digest)
 *
 * The core is deliberately identical to the Python side
 * (``mockup/step_registry.BlockFingerprint.digest``): the same sigma yields the same
 * hex digest in both processes, so the JS hook and the Python pipeline cannot disagree
 * about what "the block changed" means.
 *
 * Idle re-issue counter: counts ONLY when the fingerprint matches the previous one
 * (block unchanged but would have been re-emitted).  A legitimate change (fingerprint
 * changed) resets the counter.  There is no hard cap on total re-emissions: an idle
 * streak never suppresses a real change (H1 fix), it only reports how long the block
 * has been stable.
 *
 * No I/O, no spawning, no host state: this module is pure apart from the per-session
 * counter map, so it adds microseconds to a turn.
 */
import { createHash } from "node:crypto"

/** Per-session tau state.  ``previous`` is the last emitted fingerprint (hex or null). */
export class FingerprintState {
  constructor() {
    this.previous = null
    this.idleCount = 0
  }
}

const states = new Map() // sessionID -> FingerprintState

/** Coerce a cap/marker input (array, Set, comma-separated string, scalar) to strings. */
function asList(value) {
  if (value === null || value === undefined) return []
  if (typeof value === "string") {
    return value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean)
  }
  if (value instanceof Set) return [...value].map((item) => String(item).trim()).filter(Boolean)
  if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean)
  return [String(value).trim()].filter(Boolean)
}

/** Sorted, de-duplicated list: the digest must not depend on input order. */
function canonicalList(value) {
  return [...new Set(asList(value))].sort()
}

function text(value) {
  return value === null || value === undefined ? "" : String(value)
}

function version(value) {
  const number = Number(value)
  return Number.isFinite(number) ? Math.trunc(number) : 0
}

function keyOf(sessionID) {
  return text(sessionID)
}

/**
 * Compute the fingerprint from the active step + queue state.
 *
 * Accepts camelCase (JS plugin) and snake_case (Python payload) field names.  The
 * canonical payload keeps the Python key order and sorted lists, so
 * ``computeFingerprint(state) === BlockFingerprint(...).digest()`` for the same values.
 *
 * @param {object} state  { stepId, capSet, categoryHint, markers, planVersion, indexDigest }
 * @returns {string} 64-char sha256 hex fingerprint
 */
export function computeFingerprint(state) {
  const source = state ?? {}
  // Keys in the same order Python uses with json.dumps(sort_keys=True):
  // 'cap_set' < 'category_hint' (a hyphen-free '_' sorts before 't').
  const payload = {
    cap_set: canonicalList(source.capSet ?? source.cap_set),
    category_hint: text(source.categoryHint ?? source.category_hint),
    index_digest: text(source.indexDigest ?? source.index_digest),
    markers: canonicalList(source.markers),
    plan_version: version(source.planVersion ?? source.plan_version),
    step_id: text(source.stepId ?? source.step_id),
  }
  return createHash("sha256").update(JSON.stringify(payload), "utf8").digest("hex")
}

/**
 * Check if the block should be re-emitted for this session.
 *
 * First emission and every material change return ``shouldReemit: true`` and reset the
 * idle counter.  An unchanged fingerprint returns ``shouldReemit: false``, sets
 * ``isIdle: true`` and increments the counter (no cap).
 *
 * @param {string} sessionID
 * @param {string} fingerprint  hex digest from {@link computeFingerprint}
 * @returns {{shouldReemit: boolean, isIdle: boolean, idleCount: number}}
 */
export function checkReemit(sessionID, fingerprint) {
  const key = keyOf(sessionID)
  let state = states.get(key)
  if (!state) {
    state = new FingerprintState()
    states.set(key, state)
  }
  const current = text(fingerprint)
  if (state.previous === current) {
    state.idleCount += 1
    return { shouldReemit: false, isIdle: true, idleCount: state.idleCount }
  }
  state.previous = current
  state.idleCount = 0
  return { shouldReemit: true, isIdle: false, idleCount: 0 }
}

/**
 * Reset the idle counter for a session (on a legitimate change).
 *
 * Keeps the last fingerprint, so a following unchanged turn is still recognized as
 * idle (count starts again at 1) instead of being misread as a first emission.
 */
export function resetIdle(sessionID) {
  const key = keyOf(sessionID)
  const state = states.get(key)
  if (state) state.idleCount = 0
  return state ?? null
}

/** Read-only view (tests, diagnostics); never mutates. */
export function peekState(sessionID) {
  const state = states.get(keyOf(sessionID))
  return state ? { previous: state.previous, idleCount: state.idleCount } : null
}

/** Drop one session's state (session end); also used by tests to isolate runs. */
export function forgetSession(sessionID) {
  return states.delete(keyOf(sessionID))
}

export default { FingerprintState, checkReemit, computeFingerprint, resetIdle }
